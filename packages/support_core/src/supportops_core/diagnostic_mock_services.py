"""S01–S06 隔离业务数据，不连接真实日志/配额/路由或计费系统。"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import Field, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from supportops_core.agent_config import StrictModel
from supportops_core.auth import IdentityContext
from supportops_core.enums import MessageRole
from supportops_core.models import AgentRun, Message
from supportops_core.resource_policy import TrustedResource, authorize_resource

MOCK_DIAGNOSTIC_TOOLS = frozenset({"get_request_logs", "get_current_user_profile", "get_model_route_status", "list_active_incidents"})
MOCK_FIXTURE_REQUESTS = {
    "mock-s01-rpm": "S01", "mock-s02-tpm": "S02", "mock-s03-concurrency": "S03",
    "mock-s04-account": "S04", "mock-s05-upstream": "S05", "mock-s06-unknown": "S06",
}
DIAGNOSTIC_FACT_FIELDS = {
    "get_request_logs": frozenset({"rpm_count", "rpm_limit", "input_tokens", "max_output_reserved_tokens", "tpm_limit", "concurrency", "concurrency_limit", "http_status", "request_at", "model_name", "region", "error_class"}),
    "get_current_user_profile": frozenset({"rpm_count", "rpm_limit", "input_tokens", "max_output_reserved_tokens", "tpm_limit", "concurrency", "concurrency_limit", "project_id", "subject_user_id", "account_status", "cash_balance_usd", "gift_balance_usd", "frozen_balance_usd", "plan_remaining_tokens", "quota_updated_at"}),
    "get_model_route_status": frozenset({"model_name", "region", "route_status", "incident_id"}),
    "list_active_incidents": frozenset({"incident_count", "incident_id", "visibility", "model_name", "region", "incident_status"}),
}
_MODE = re.compile(r"\[mock:(empty|timeout|denied|conflict)\]", re.IGNORECASE)


class DiagnosticQuery(StrictModel):
    request_id: str = Field(min_length=3, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
    subject_user_id: UUID | None = None
    project_id: UUID | None = None
    model_name: str | None = Field(default=None, min_length=1, max_length=128)
    region: str | None = Field(default=None, min_length=1, max_length=64)
    window_start: datetime | None = None
    window_end: datetime | None = None

    @model_validator(mode="after")
    def limited_window(self) -> "DiagnosticQuery":
        if (self.window_start is None) != (self.window_end is None):
            raise ValueError("时间范围必须同时提供起点和终点")
        if self.window_start is not None and self.window_end is not None:
            if self.window_start.tzinfo is None or self.window_end.tzinfo is None:
                raise ValueError("查询时间必须包含时区")
            if not timedelta(0) < self.window_end - self.window_start <= timedelta(minutes=15):
                raise ValueError("查询范围必须大于零且不超过 15 分钟")
            if self.window_end > datetime.now(UTC) + timedelta(minutes=5):
                raise ValueError("查询时间不能超出当前时间")
        return self


class MockDiagnosticResponse(StrictModel):
    schema_version: Literal["1"] = "1"
    fixture_version: Literal["mock_429_v1"] = "mock_429_v1"
    data_environment: Literal["mock"] = "mock"
    data_trust: Literal["untrusted_business_data"] = "untrusted_business_data"
    tool_id: str
    scenario: str | None = None
    request_id: str
    status: Literal["ok", "not_found", "unavailable", "access_denied", "conflict"]
    observed_at: datetime
    facts: dict[str, str | int | float | bool | None] = Field(default_factory=dict)
    error_code: str | None = None
    guidance: str = "这些是隔离模拟数据，不证明真实生产状态；不足或冲突时追问/人工复核，不能执行配额或资金变更。"

    @model_validator(mode="after")
    def whitelist_facts(self) -> "MockDiagnosticResponse":
        if set(self.facts) - DIAGNOSTIC_FACT_FIELDS.get(self.tool_id, frozenset()):
            raise ValueError("诊断结果包含未获准字段")
        if any(isinstance(value, str) and len(value) > 256 for value in self.facts.values()):
            raise ValueError("诊断字段超过大小限制")
        if self.status in {"unavailable", "access_denied", "not_found"} and self.facts:
            raise ValueError("未取得的查询结果不能携带已确认事实")
        return self


async def query_mock_diagnostic(
    session: AsyncSession, *, identity: IdentityContext, run: AgentRun,
    tool_id: str, query: DiagnosticQuery, history_turns: int,
) -> MockDiagnosticResponse:
    """只查询用户真实提供的标识和本人模拟资源，模型不能编造请求范围。"""
    now = datetime.now(UTC)
    response = MockDiagnosticResponse(tool_id=tool_id,
        request_id=query.request_id.lower() if query.request_id.lower() in MOCK_FIXTURE_REQUESTS else "unconfigured_request",
        status="unavailable", observed_at=now)
    if tool_id not in MOCK_DIAGNOSTIC_TOOLS or identity.principal.tenant_id != run.tenant_id:
        return response.model_copy(update={"status": "access_denied", "error_code": "DIAGNOSTIC_SCOPE_DENIED"})
    if run.input_sequence is None:
        return response.model_copy(update={"error_code": "DIAGNOSTIC_INPUT_BOUNDARY_INVALID"})
    project_id = uuid5(NAMESPACE_URL, f"supportops:mock-project:{run.tenant_id}:{identity.user.id}")
    if (query.subject_user_id is not None and query.subject_user_id != identity.user.id
        or query.project_id is not None and query.project_id != project_id):
        return response.model_copy(update={"status": "access_denied", "error_code": "DIAGNOSTIC_SCOPE_DENIED"})
    field_scope = frozenset({"request", "usage", "limits", "account_summary", "public_route", "visible_incidents"})
    authorize_resource(identity, TrustedResource(kind="project", tenant_id=run.tenant_id, resource_id=str(project_id),
        owner_user_id=identity.user.id, visible_fields=field_scope), action="read", fields=field_scope)
    # 只读当前主体的有限用户轮次，不采纳模型历史中自造的 request_id。
    user_inputs = (await session.scalars(select(Message.content).where(
        Message.tenant_id == run.tenant_id, Message.conversation_id == run.conversation_id,
        Message.user_id == identity.user.id, Message.role == MessageRole.USER,
        Message.turn_sequence <= run.input_sequence,
        Message.turn_sequence >= max(1, run.input_sequence - history_turns),
    ).order_by(Message.turn_sequence, Message.id))).all()
    marker = re.compile(rf"(?<![A-Za-z0-9._:-]){re.escape(query.request_id)}(?![A-Za-z0-9._:-])", re.IGNORECASE)
    if not any(marker.search(text) for text in user_inputs):
        return response.model_copy(update={"error_code": "DIAGNOSTIC_REQUEST_CONTEXT_REQUIRED",
            "guidance": "请先由用户补充请求 ID、发生时间、模型和区域；不能自行构造请求 ID，也不需要完整密钥或 Prompt。"})
    scenario = MOCK_FIXTURE_REQUESTS.get(query.request_id.lower())
    if scenario is None:
        return response.model_copy(update={"error_code": "BUSINESS_CONNECTOR_UNCONFIGURED"})
    response = response.model_copy(update={"scenario": scenario})
    input_message = await session.scalar(select(Message).where(
        Message.id == run.input_message_id, Message.tenant_id == run.tenant_id,
        Message.conversation_id == run.conversation_id, Message.user_id == identity.user.id,
        Message.role == MessageRole.USER, Message.turn_sequence == run.input_sequence,
    ))
    if input_message is None:
        return response.model_copy(update={"error_code": "DIAGNOSTIC_REQUEST_CONTEXT_REQUIRED"})
    request_at = input_message.created_at
    if request_at.tzinfo is None:
        request_at = request_at.replace(tzinfo=UTC)
    if (query.window_start is not None and query.window_end is not None and not query.window_start <= request_at <= query.window_end
        or query.model_name not in {None, "mock-model"} or query.region not in {None, "mock-cn"}):
        return response.model_copy(update={"status": "not_found", "error_code": None})
    mode_match = _MODE.search(input_message.content)
    mode = mode_match.group(1).lower() if mode_match else "normal"
    if mode == "empty":
        return response.model_copy(update={"status": "not_found", "error_code": None})
    if mode == "timeout":
        return response.model_copy(update={"status": "unavailable", "error_code": "MOCK_DEPENDENCY_TIMEOUT"})
    if mode == "denied":
        return response.model_copy(update={"status": "access_denied", "error_code": "MOCK_DOWNSTREAM_ACCESS_DENIED"})
    conflict = mode == "conflict"
    counts = {"rpm_count": 75 if scenario == "S01" else 10, "rpm_limit": 60,
        "input_tokens": 12000 if scenario == "S02" else 1000,
        "max_output_reserved_tokens": 36000 if scenario == "S02" else 2000,
        "tpm_limit": 40000, "concurrency": 5 if scenario == "S03" else 1, "concurrency_limit": 4}
    facts: dict[str, str | int | float | bool | None]
    if tool_id == "get_request_logs":
        facts = {**counts, "http_status": 429, "request_at": request_at.isoformat(),
            "model_name": "mock-model", "region": "mock-cn", "error_class": {
                "S01": "rpm_exceeded", "S02": "tpm_reservation_exceeded", "S03": "project_concurrency_exceeded",
                "S04": "plan_quota_exhausted", "S05": "upstream_rate_limited", "S06": "unknown",
            }[scenario]}
    elif tool_id == "get_current_user_profile":
        facts = {**counts, "project_id": str(project_id), "subject_user_id": str(identity.user.id),
            "account_status": "active", "cash_balance_usd": 100.0, "gift_balance_usd": 0.0,
            "frozen_balance_usd": 0.0, "plan_remaining_tokens": 0 if scenario == "S04" else 100000,
            "quota_updated_at": (request_at - timedelta(hours=1)).isoformat()}
        if conflict:
            facts.update(rpm_count=10, rpm_limit=120)
    elif tool_id == "get_model_route_status":
        facts = {"model_name": "mock-model", "region": "mock-cn",
            "route_status": "rate_limited" if scenario == "S05" else "healthy",
            "incident_id": "mock-upstream-429-v1" if scenario == "S05" else None}
    else:
        facts = {"incident_count": 1 if scenario == "S05" else 0,
            "incident_id": "mock-upstream-429-v1" if scenario == "S05" else None,
            "visibility": "tenant", "model_name": "mock-model", "region": "mock-cn",
            "incident_status": "investigating" if scenario == "S05" else None}
    return response.model_copy(update={"status": "conflict" if conflict else "ok", "facts": facts})
