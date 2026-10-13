"""固定十个节点的 429 Mock 工作流，结论由事实规则产生，不让模型猜测根因。"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from supportops_core.agent_config import AgentConfigV2, SupportAnswer, SupportCitation
from supportops_core.auth import IdentityContext
from supportops_core.context_services import ContextSnapshot
from supportops_core.diagnostic_contracts import (
    DiagnosticCandidate, DiagnosticEscalation, DiagnosticFact, DiagnosticReport, DiagnosticStage, DiagnosticStageName,
)
from supportops_core.diagnostic_mock_services import DiagnosticQuery, MOCK_FIXTURE_REQUESTS, MockDiagnosticResponse
from supportops_core.knowledge_retrieval import lock_evidence_documents, read_knowledge_evidence
from supportops_core.models import AgentRun, ToolInvocation
from supportops_core.run_attempt_services import current_run_identity, owned_run
from supportops_core.services import append_event
from supportops_core.tool_gateway import ToolExecutionGateway, diagnostic_result_digest
from supportops_core.tool_policy import ToolPolicyError, resolve_tool_policy

_QUESTION = re.compile(r"(?<!\d)429(?!\d)|限流|mock-s0[1-6]-", re.IGNORECASE)
_REQUEST = re.compile(r"(?<![A-Za-z0-9._:-])mock-s0[1-6]-[A-Za-z0-9_-]+(?![A-Za-z0-9._:-])|(?:request[_ -]?id|请求\s*ID)\s*[:：=]\s*([A-Za-z0-9][A-Za-z0-9._:-]{2,127})", re.IGNORECASE)
_STEPS: tuple[tuple[DiagnosticStageName, str], ...] = (
    ("query_logs", "get_request_logs"), ("query_profile", "get_current_user_profile"),
    ("query_route", "get_model_route_status"), ("query_incidents", "list_active_incidents"),
)
_DESCRIPTIONS = {
    "rpm_exceeded": "请求频率超过当前项目 RPM 限额",
    "tpm_reservation_exceeded": "输入 Token 与最大输出预占超过当前 TPM 限额",
    "project_concurrency_exceeded": "当前项目并发数超过并发限额",
    "plan_quota_exhausted": "当前套餐可用 Token 配额已耗尽",
    "upstream_rate_limited": "相同模型与区域的上游限流事件与请求日志一致",
}


def is_mock_diagnostic(gateway: ToolExecutionGateway) -> bool:
    return gateway.settings.diagnostic_mock_enabled and gateway.settings.environment != "production" and bool(_QUESTION.search(gateway.snapshot.input_text))


def _number(facts: dict[str, str | int | float | bool | None], key: str) -> float | None:
    value = facts.get(key)
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def evaluate_facts(report: DiagnosticReport, responses: dict[str, MockDiagnosticResponse]) -> None:
    """只确认白名单查询事实；相互矛盾、账户状态异常或多个候选时不择一归因。"""
    logs = responses.get("get_request_logs")
    profile = responses.get("get_current_user_profile")
    route = responses.get("get_model_route_status")
    incidents = responses.get("list_active_incidents")
    if any(response.status == "conflict" for response in responses.values()):
        report.status = "conflict"
        report.reason_codes.append("DIAGNOSTIC_SOURCE_CONFLICT")
        report.unresolved_items.append("模拟来源显式报告冲突，需要人工复核。")
        return
    if any(response is None or response.status != "ok" for response in (logs, profile, route, incidents)):
        report.status = "unavailable"
        report.reason_codes.append("DIAGNOSTIC_REQUIRED_SOURCE_UNAVAILABLE")
        report.unresolved_items.append("至少一个必要查询没有取得可用结果，不能确认唯一根因。")
        return
    assert logs is not None and profile is not None and route is not None and incidents is not None
    shared = ("rpm_count", "rpm_limit", "input_tokens", "max_output_reserved_tokens", "tpm_limit", "concurrency", "concurrency_limit")
    if (any(logs.facts.get(key) != profile.facts.get(key) for key in shared)
        or any(logs.facts.get(key) != response.facts.get(key) for key in ("model_name", "region") for response in (route, incidents))
        or profile.facts.get("account_status") != "active"):
        report.status = "conflict"
        report.reason_codes.append("DIAGNOSTIC_SOURCE_CONFLICT")
        report.unresolved_items.append("请求与账户/模型/区域事实不一致，未选择单一根因。")
        return
    rpm, rpm_limit = _number(logs.facts, "rpm_count"), _number(profile.facts, "rpm_limit")
    input_tokens, output_reserved = _number(logs.facts, "input_tokens"), _number(logs.facts, "max_output_reserved_tokens")
    tpm_limit = _number(profile.facts, "tpm_limit")
    concurrency, concurrency_limit = _number(logs.facts, "concurrency"), _number(profile.facts, "concurrency_limit")
    candidates = {
        "rpm_exceeded": rpm is not None and rpm_limit is not None and rpm > rpm_limit,
        "tpm_reservation_exceeded": input_tokens is not None and output_reserved is not None and tpm_limit is not None and input_tokens + output_reserved > tpm_limit,
        "project_concurrency_exceeded": concurrency is not None and concurrency_limit is not None and concurrency > concurrency_limit,
        "plan_quota_exhausted": _number(profile.facts, "plan_remaining_tokens") == 0,
        "upstream_rate_limited": route.facts.get("route_status") == "rate_limited"
            and _number(incidents.facts, "incident_count") is not None and _number(incidents.facts, "incident_count") > 0
            and route.facts.get("incident_id") is not None and route.facts.get("incident_id") == incidents.facts.get("incident_id"),
    }
    active = [code for code, matched in candidates.items() if matched]
    if logs.facts.get("http_status") != 429 or len(active) != 1 or logs.facts.get("error_class") != active[0]:
        report.status = "conflict" if len(active) > 1 else "unconfirmed"
        report.reason_codes.append("DIAGNOSTIC_ROOT_CAUSE_NOT_UNIQUE")
        report.unresolved_items.append("查询事实未能唯一证明请求的 429 分类。")
        return
    code = active[0]
    report.candidates = [DiagnosticCandidate(code=code, description=_DESCRIPTIONS[code], source_ids=[source.invocation_id for source in report.sources])]
    report.status = "unconfirmed"


async def authorize_report_sources(session: AsyncSession, *, report: DiagnosticReport, run: AgentRun,
    identity: IdentityContext, config: AgentConfigV2, tool_ids: list[str], implementation_versions: dict[str, int],
) -> None:
    """提交/详情读取重新检查身份、调用事实、实现和实时策略，不复用旧角色授权。"""
    if report.run_id != run.id or report.attempt_id != run.current_attempt_id:
        raise ToolPolicyError("DIAGNOSTIC_RESULT_INVALID", "诊断报告不属于当前执行尝试")
    for source in report.sources:
        invocation = await session.scalar(select(ToolInvocation).where(
            ToolInvocation.id == source.invocation_id, ToolInvocation.tenant_id == run.tenant_id,
            ToolInvocation.run_id == run.id, ToolInvocation.tool_id == source.tool_id,
            ToolInvocation.status.in_(("succeeded", "failed")), ToolInvocation.diagnostic_result.is_not(None)))
        if invocation is None or (not source.reused and invocation.attempt_id != report.attempt_id):
            raise ToolPolicyError("DIAGNOSTIC_RESULT_INVALID", "诊断事实缺少获准调用来源")
        response = MockDiagnosticResponse.model_validate(invocation.diagnostic_result)
        policy = await resolve_tool_policy(session, identity=identity, config=config, allowed_tool_ids=tool_ids,
            tool_id=source.tool_id, arguments={}, implementation_versions=implementation_versions)
        if (policy.approval_required or policy.risk_number >= 2 or policy.catalog_version != source.catalog_version
            or policy.implementation_version != source.implementation_version or invocation.catalog_version != source.catalog_version
            or invocation.result_digest != source.result_digest or diagnostic_result_digest(response) != source.result_digest):
            raise ToolPolicyError("TOOL_POLICY_CHANGED", "诊断来源的权限、版本或策略已变化")
        for fact in (item for item in report.facts if item.invocation_id == source.invocation_id):
            if (response.status != "ok" or fact.tool_id != source.tool_id or fact.field not in response.facts
                or response.facts[fact.field] != fact.value or response.observed_at != fact.observed_at):
                raise ToolPolicyError("DIAGNOSTIC_RESULT_INVALID", "诊断事实与调用回执不一致")
    if {fact.invocation_id for fact in report.facts} - {source.invocation_id for source in report.sources}:
        raise ToolPolicyError("DIAGNOSTIC_RESULT_INVALID", "诊断事实包含无来源条目")


def answer_from_report(report: DiagnosticReport) -> SupportAnswer:
    if report.status == "needs_information":
        text = "请补充一个请求 ID、发生时间（含时区）、模型和区域，以便定位 429。无需提供完整 API Key 或 Prompt。"
    elif report.status == "confirmed_mock":
        text = "隔离模拟证据与获准知识规则一致，已确认模拟原因：" + report.candidates[0].description + "。\n请结合下方查询时间和知识引用查看适用范围。"
    else:
        text = "目前尚不能确认该 429 的唯一原因。\n" + "\n".join(report.unresolved_items)
        if report.candidates:
            text += "\n查询事实支持的候选：" + report.candidates[0].description + "；尚未满足知识规则确认条件。"
        text += "\n已保存排查步骤及受限事实，可以交由人工继续处理。"
    return SupportAnswer(answer=text, summary="429 模拟诊断：" + report.status,
        citations=[SupportCitation(title="获准的 429 诊断规则", evidence_id=evidence_id) for evidence_id in report.evidence_ids],
        escalation_recommended=report.escalation is not None)


async def run_mock_diagnostic(gateway: ToolExecutionGateway) -> SupportAnswer:
    """每次最多四个业务查询和一个关键词查询，无模型循环/隐藏重试。"""
    report = DiagnosticReport(run_id=gateway.identity.run_id, attempt_id=gateway.identity.attempt_id)

    async def stage(name: DiagnosticStageName, status: Literal["running", "completed", "skipped", "failed"], *, code: str | None = None) -> None:
        now = datetime.now(UTC)
        existing = next((item for item in report.stages if item.name == name), None)
        if existing is None:
            report.stages.append(DiagnosticStage(name=name, status=status, started_at=now,
                finished_at=None if status == "running" else now, error_code=code))
        else:
            existing.status, existing.finished_at, existing.error_code = status, now, code
        async with gateway.session_factory() as session, session.begin():
            owned = await owned_run(session, gateway.identity)
            await current_run_identity(session, run=owned.run, conversation=owned.conversation)
            if owned.attempt.context_snapshot is None:
                raise ToolPolicyError("DIAGNOSTIC_CONTEXT_INVALID", "诊断缺少可信输入边界")
            context = ContextSnapshot.model_validate(owned.attempt.context_snapshot)
            owned.attempt.context_snapshot = context.model_copy(update={"diagnostic": report}).model_dump(mode="json")
            await append_event(session, run_id=report.run_id, attempt_id=report.attempt_id,
                event_type="stage.started" if status == "running" else "stage.completed",
                data={"stage": f"diagnostic.{name}", "status": status, "code": code, "workflow_version": report.workflow_version})

    await stage("classify", "completed")
    await stage("collect_information", "running")
    request_ids = {match.group(1) or match.group(0) for match in _REQUEST.finditer(gateway.snapshot.input_text)}
    if len(request_ids) != 1:
        report.status = "needs_information"
        report.reason_codes.append("DIAGNOSTIC_REQUEST_ID_REQUIRED")
        report.unresolved_items.append("需要一个明确的请求 ID；多个标识不能自动选择。")
        await stage("collect_information", "completed")
        report.finished_at = datetime.now(UTC)
        await stage("complete", "completed")
        return answer_from_report(report)
    request_id = next(iter(request_ids)).lower()
    report.request_id = request_id if request_id in MOCK_FIXTURE_REQUESTS else "unconfigured_request"
    query = DiagnosticQuery(request_id=request_id)
    await stage("collect_information", "completed")
    responses: dict[str, MockDiagnosticResponse] = {}
    for name, tool_id in _STEPS:
        await stage(name, "running")
        try:
            result, source = await gateway.controlled_query(tool_id=tool_id, arguments=query.model_dump(mode="json"))
            response = MockDiagnosticResponse.model_validate_json(str(result.content))
            responses[tool_id] = response
            if source is not None:
                report.sources.append(source)
                if response.status == "ok":
                    report.facts.extend(DiagnosticFact(tool_id=tool_id, field=key, value=value,
                        invocation_id=source.invocation_id, observed_at=response.observed_at) for key, value in response.facts.items())
            if response.status != "ok":
                report.reason_codes.append(response.error_code or "DIAGNOSTIC_QUERY_" + response.status.upper())
            await stage(name, "completed" if response.status in {"ok", "conflict", "not_found"} else "failed", code=response.error_code)
        except ToolPolicyError as exc:
            if exc.code in {"RUNTIME_AUTHORIZATION_DENIED", "MODEL_RESOURCE_UNAVAILABLE"}:
                raise
            report.reason_codes.append(exc.code)
            await stage(name, "failed", code=exc.code)
            if "QUOTA" in exc.code or "BUDGET" in exc.code:
                report.unresolved_items.append("查询预算已耗尽，后续查询已停止。")
                break
    await stage("evaluate", "running")
    evaluate_facts(report, responses)
    await stage("retrieve_rules", "running")
    if len(report.candidates) == 1:
        try:
            result, _ = await gateway.controlled_query(tool_id="search_knowledge",
                arguments={"query": "429 " + report.candidates[0].code, "max_results": 3})
            payload = json.loads(str(result.content))
            evidence_ids = [UUID(item["evidence_id"]) for item in payload["evidence"]]
            async with gateway.session_factory() as session, session.begin():
                owned = await owned_run(session, gateway.identity)
                identity = await current_run_identity(session, run=owned.run, conversation=owned.conversation)
                await lock_evidence_documents(session, evidence_ids=evidence_ids, identity=identity)
                for evidence_id in evidence_ids:
                    evidence = await read_knowledge_evidence(session, evidence_id=evidence_id, identity=identity,
                        run_id=report.run_id, attempt_id=report.attempt_id)
                    # 声明式规则仅关联预定义判断，不执行知识正文中的代码或扩权指令。
                    marker = "supportops_429_rule_v1:" + report.candidates[0].code
                    if re.search(r"(?m)^\s*" + re.escape(marker) + r"\s*$", str(evidence["content"])):
                        report.evidence_ids.append(evidence_id)
            if report.evidence_ids:
                report.candidates[0].confirmation = "confirmed_mock"
                report.candidates[0].evidence_ids = list(report.evidence_ids)
                report.status = "confirmed_mock"
            else:
                report.reason_codes.append("DIAGNOSTIC_KNOWLEDGE_RULE_UNCONFIRMED")
                report.unresolved_items.append("未取得包含对应版本化规则声明的获准知识，不能确认根因。")
            await stage("retrieve_rules", "completed" if result.status != "error" else "failed", code=payload.get("error_code"))
        except ToolPolicyError as exc:
            if exc.code in {"RUNTIME_AUTHORIZATION_DENIED", "MODEL_RESOURCE_UNAVAILABLE"}:
                raise
            report.reason_codes.append(exc.code)
            report.unresolved_items.append("知识查询未完成，根因仍未确认。")
            await stage("retrieve_rules", "failed", code=exc.code)
    else:
        await stage("retrieve_rules", "skipped")
    await stage("evaluate", "completed")
    report.reason_codes = list(dict.fromkeys(report.reason_codes))
    if report.status != "confirmed_mock":
        report.escalation = DiagnosticEscalation(reason_codes=report.reason_codes, confirmed_fact_count=len(report.facts),
            source_ids=[source.invocation_id for source in report.sources], evidence_ids=report.evidence_ids,
            completed_steps=[item.name for item in report.stages if item.status == "completed"], unresolved_items=report.unresolved_items)
        await stage("escalate", "completed")
    else:
        await stage("escalate", "skipped")
    report.finished_at = datetime.now(UTC)
    await stage("complete", "completed")
    return answer_from_report(report)
