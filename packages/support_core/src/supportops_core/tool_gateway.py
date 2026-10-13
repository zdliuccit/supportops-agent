"""本地工具统一许可/执行入口；远程适配也必须复用这一治理边界。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import structlog

from langchain.agents.middleware import ToolCallRequest
from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool
from langgraph.types import Command
from pydantic import BaseModel, ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from supportops_core.agent_config import SECRET_PATTERNS
from supportops_core.agent_runtime import AgentRuntimeSnapshot, filter_runtime_tools
from supportops_core.auth import IdentityContext
from supportops_core.config import Settings
from supportops_core.context_services import ContextEvidence, ContextSnapshot
from supportops_core.diagnostic_mock_services import DiagnosticQuery, MockDiagnosticResponse, MOCK_DIAGNOSTIC_TOOLS, query_mock_diagnostic
from supportops_core.diagnostic_contracts import DiagnosticSource
from supportops_core.knowledge_retrieval import lock_evidence_documents, read_knowledge_evidence, search_keyword_knowledge
from supportops_core.models import RunAttempt, Tenant, ToolInvocation
from supportops_core.model_budget_services import ModelBudgetExceeded, assert_current_cost_budget
from supportops_core.quota_services import quota_day_start
from supportops_core.resource_policy import TrustedResource, authorize_resource
from supportops_core.run_attempt_services import (
    RunAttemptIdentity, RunCancellationRequested, RunLeaseLostError, RuntimeAuthorizationDenied,
    current_run_identity, owned_run,
)
from supportops_core.services import append_event
from supportops_core.tool_policy import ResolvedToolPolicy, ToolPolicyError, resolve_tool_policy
from supportops_core.tool_observation_services import record_tool_observation
from supportops_core.action_proposal_services import propose_mock_ticket
from supportops_core.models import BusinessAction

logger = structlog.get_logger()


@dataclass(frozen=True, slots=True)
class ToolPermit:
    invocation_id: UUID
    identity: IdentityContext | None
    arguments: dict[str, object]
    policy: ResolvedToolPolicy | None
    error: ToolPolicyError | None


@dataclass(frozen=True, slots=True)
class ControlledToolCall:
    """工作流内部调用仍走相同许可，不构造模型/SDK 的调用历史。"""
    tool_call: dict[str, Any]
    tool: BaseTool


def diagnostic_result_digest(response: MockDiagnosticResponse) -> str:
    payload = json.dumps(response.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(payload.encode()).hexdigest()


class ToolExecutionGateway:
    """Schema/实时权限/额度/审批/输出过滤/审计的单一调用入口。"""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession], *, settings: Settings,
                 snapshot: AgentRuntimeSnapshot, identity: RunAttemptIdentity) -> None:
        self.session_factory = session_factory
        self.settings = settings
        self.snapshot = snapshot
        self.identity = identity
        self.parallel_permits = asyncio.Semaphore(snapshot.config.runtime.max_parallel_tools)

    def _arguments(self, request: ToolCallRequest | ControlledToolCall) -> dict[str, object]:
        raw = request.tool_call.get("args")
        if not isinstance(raw, dict) or request.tool is None:
            raise ToolPolicyError("TOOL_INPUT_INVALID", "工具参数或服务端实现无效")
        try:
            encoded = json.dumps(raw, ensure_ascii=False, sort_keys=True, allow_nan=False)
            if len(encoded.encode("utf-8")) > self.settings.tool_argument_max_bytes:
                raise ToolPolicyError("TOOL_INPUT_TOO_LARGE", "工具参数超过大小限制")
            schema = request.tool.tool_call_schema
            if not isinstance(schema, type) or not issubclass(schema, BaseModel):
                raise ToolPolicyError("TOOL_SCHEMA_UNSUPPORTED", "工具输入 Schema 尚未通过受控适配")
            if set(raw) - set(schema.model_fields):
                raise ToolPolicyError("TOOL_INPUT_INVALID", "工具包含未获准参数，不能覆盖可信上下文")
            return schema.model_validate(raw).model_dump(mode="json")
        except (TypeError, ValueError, ValidationError) as exc:
            raise ToolPolicyError("TOOL_INPUT_INVALID", "工具参数不符合已注册 Schema") from exc

    async def _reserve(self, request: ToolCallRequest | ControlledToolCall) -> ToolPermit:
        name = request.tool_call.get("name")
        call_id = request.tool_call.get("id")
        valid_name = isinstance(name, str) and 0 < len(name) <= 100
        valid_id = isinstance(call_id, str) and 0 < len(call_id) <= 200
        tool_id = name if valid_name else "__invalid_tool__"
        recorded_call_id = call_id if valid_id else f"invalid:{uuid4()}"
        arguments: dict[str, object] = {}
        policy = None
        identity_context = None
        error = None
        async with self.session_factory() as session, session.begin():
            owned = await owned_run(session, self.identity)
            duplicate = await session.scalar(select(ToolInvocation).where(
                ToolInvocation.tenant_id == self.identity.tenant_id,
                ToolInvocation.run_id == self.identity.run_id,
                ToolInvocation.tool_call_id == recorded_call_id,
            ))
            if duplicate is not None:
                await append_event(session, run_id=self.identity.run_id, event_type="tool.denied",
                    attempt_id=self.identity.attempt_id,
                    data={"code": "TOOL_CALL_DUPLICATE", "invocation_id": str(duplicate.id)})
                return ToolPermit(duplicate.id, None, {}, None, ToolPolicyError("TOOL_CALL_DUPLICATE", "同一工具调用标识不能重复执行"))
            try:
                if not valid_name or not valid_id:
                    raise ToolPolicyError("TOOL_INPUT_INVALID", "工具标识无效")
                if tool_id in MOCK_DIAGNOSTIC_TOOLS and (not self.settings.diagnostic_mock_enabled or self.settings.environment == "production"):
                    raise ToolPolicyError("MOCK_DIAGNOSTICS_DISABLED", "隔离业务 Mock 未启用或当前为 production，不能查询模拟业务数据")
                if tool_id == "create_mock_ticket" and (not self.settings.business_action_mock_enabled or self.settings.environment == "production"):
                    raise ToolPolicyError("ACTION_CONNECTOR_UNAVAILABLE", "隔离动作 Mock 未启用，不能提出建单动作")
                identity_context = await current_run_identity(session, run=owned.run, conversation=owned.conversation)
                first_start = await session.scalar(select(func.min(RunAttempt.started_at)).where(
                    RunAttempt.run_id == self.identity.run_id, RunAttempt.tenant_id == self.identity.tenant_id))
                if first_start is not None:
                    if first_start.tzinfo is None:
                        first_start = first_start.replace(tzinfo=UTC)
                    if (datetime.now(UTC) - first_start).total_seconds() >= self.snapshot.config.runtime.run_timeout_seconds:
                        raise ToolPolicyError("AGENT_RUN_TIME_BUDGET_EXCEEDED", "整个 Run 的累计执行时长已耗尽")
                arguments = self._arguments(request)
                policy = await resolve_tool_policy(session, identity=identity_context, config=self.snapshot.config,
                    allowed_tool_ids=self.snapshot.agent_version.resolved_tool_ids, tool_id=tool_id, arguments=arguments,
                    implementation_versions=self.snapshot.agent_version.resolved_tool_versions)
                if policy.approval_required and not policy.action_proposal_only:
                    raise ToolPolicyError("TOOL_APPROVAL_REQUIRED", "此工具需要有效的持久化审批，当前不能执行")
                if policy.risk_number >= 2 and not policy.action_proposal_only:
                    raise ToolPolicyError("TOOL_ACTION_WORKFLOW_UNAVAILABLE", "写动作必须接入动作幂等与复核流程")
                # 当前身份工具只读取本人的获准字段；工单占位工具不执行资源查询。
                if tool_id == "current_identity_summary":
                    authorize_resource(identity_context, TrustedResource(kind="identity",
                        tenant_id=self.identity.tenant_id, resource_id=str(identity_context.user.id),
                        owner_user_id=identity_context.user.id, visible_fields=frozenset({"user_id", "tenant_id", "roles"})),
                        action="read", fields=frozenset({"user_id", "tenant_id", "roles"}))
                tenant = await session.scalar(select(Tenant).where(Tenant.id == self.identity.tenant_id).with_for_update(key_share=True))
                if tenant is None or tenant.status != "active":
                    raise ToolPolicyError("RUNTIME_AUTHORIZATION_DENIED", "当前租户不允许执行")
                await assert_current_cost_budget(session, tenant_id=self.identity.tenant_id, run_id=self.identity.run_id,
                    run_limit_usd=self.snapshot.config.runtime.max_cost_usd,
                    tenant_limit_usd=self.settings.tenant_model_cost_usd_per_day)
                consumed = [ToolInvocation.tenant_id == self.identity.tenant_id,
                            ToolInvocation.run_id == self.identity.run_id, ToolInvocation.status != "denied"]
                total = int(await session.scalar(select(func.count()).select_from(ToolInvocation).where(*consumed)) or 0)
                per_tool = int(await session.scalar(select(func.count()).select_from(ToolInvocation)
                    .where(*consumed, ToolInvocation.tool_id == tool_id)) or 0)
                if total >= self.snapshot.config.runtime.tool_call_limit or per_tool >= policy.max_calls_per_run:
                    raise ToolPolicyError("TOOL_CALL_QUOTA_EXCEEDED", "工具调用已达到本轮总量或单工具限额")
                daily = int(await session.scalar(select(func.count()).select_from(ToolInvocation).where(
                    ToolInvocation.tenant_id == self.identity.tenant_id, ToolInvocation.status != "denied",
                    ToolInvocation.created_at >= quota_day_start())) or 0)
                if daily >= self.settings.tenant_tool_limit_per_day:
                    raise ToolPolicyError("TENANT_TOOL_DAILY_QUOTA_EXCEEDED", "租户每日工具额度已用尽")
                parallel = int(await session.scalar(select(func.count()).select_from(ToolInvocation)
                    .join(RunAttempt, ToolInvocation.attempt_id == RunAttempt.id)
                    .where(*consumed, ToolInvocation.status == "running", RunAttempt.status == "running",
                           RunAttempt.lease_expires_at > datetime.now(UTC))) or 0)
                if parallel >= self.snapshot.config.runtime.max_parallel_tools:
                    raise ToolPolicyError("TOOL_PARALLEL_LIMIT_EXCEEDED", "工具并行许可已用尽")
            except RuntimeAuthorizationDenied as exc:
                error = ToolPolicyError(exc.code, "当前主体、Agent 授权或模型资源不允许执行")
            except ToolPolicyError as exc:
                error = exc
            except ModelBudgetExceeded as exc:
                error = ToolPolicyError(exc.code, str(exc))
            digest = hashlib.sha256(json.dumps(arguments, ensure_ascii=False, sort_keys=True,
                                               separators=(",", ":"), allow_nan=False).encode()).hexdigest()
            invocation = ToolInvocation(tenant_id=self.identity.tenant_id, run_id=self.identity.run_id,
                attempt_id=self.identity.attempt_id, tool_call_id=recorded_call_id, tool_id=tool_id,
                implementation_version=policy.implementation_version if policy else 0,
                catalog_version=policy.catalog_version if policy else 0,
                risk_level=f"R{policy.risk_number if policy else 4}", arguments_digest=digest,
                status="denied" if error else "running", error_code=error.code if error else None,
                finished_at=datetime.now(UTC) if error else None)
            session.add(invocation)
            await session.flush()
            await append_event(session, run_id=self.identity.run_id, event_type="tool.denied" if error else "tool.authorized",
                attempt_id=self.identity.attempt_id,
                data={"invocation_id": str(invocation.id), "tool_id": tool_id, "risk_level": invocation.risk_level,
                      "catalog_version": invocation.catalog_version, "code": error.code if error else None})
        return ToolPermit(invocation.id, identity_context, arguments, policy, error)

    def _sanitize(self, result: ToolMessage) -> ToolMessage:
        content = result.content
        if not isinstance(content, str):
            content = "\n".join(block if isinstance(block, str) else block["text"] for block in content
                if isinstance(block, str) or (isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str)))
        for pattern in SECRET_PATTERNS:
            content = pattern.sub("[REDACTED]", content)
        content = re.sub(r'(?i)("?(?:password|secret|access_token|refresh_token|cookie|private_key)"?\s*[:=]\s*)("[^"\n]*"|[^\s,}]+)',
                         r'\1"[REDACTED]"', content)
        content = content.encode("utf-8")[:self.settings.tool_result_max_bytes].decode("utf-8", errors="ignore")
        return result.model_copy(update={"content": content, "artifact": None, "additional_kwargs": {}, "response_metadata": {}})

    async def _observe(self, invocation_id: UUID) -> None:
        try:
            async with asyncio.timeout(2):
                await record_tool_observation(self.session_factory, invocation_id=invocation_id, tenant_id=self.identity.tenant_id)
        except Exception as exc:
            # 工具许可/结果已提交，不用观测故障回滚业务事实，也不打印原始工具数据。
            logger.warning("tool_observation_unavailable", invocation_id=str(invocation_id), error_type=type(exc).__name__)

    async def _finish(self, permit: ToolPermit, *, status: str, code: str | None = None, result: ToolMessage | None = None) -> None:
        async with self.session_factory() as session, session.begin():
            owned = await owned_run(session, self.identity, allow_cancelling=True)
            invocation = await session.scalar(select(ToolInvocation).where(
                ToolInvocation.id == permit.invocation_id, ToolInvocation.attempt_id == self.identity.attempt_id,
                ToolInvocation.tenant_id == self.identity.tenant_id,
            ).with_for_update())
            if invocation is None or invocation.status != "running":
                raise ToolPolicyError("TOOL_INVOCATION_STATE_INVALID", "工具许可状态已变化")
            if result is not None:
                if owned.run.status.value != "running" or owned.conversation.deleted_at is not None:
                    raise RunCancellationRequested("工具返回前 Run 已请求取消")
                context = await current_run_identity(session, run=owned.run, conversation=owned.conversation)
                current_policy = await resolve_tool_policy(session, identity=context, config=self.snapshot.config,
                    allowed_tool_ids=self.snapshot.agent_version.resolved_tool_ids,
                    tool_id=invocation.tool_id, arguments=permit.arguments,
                    implementation_versions=self.snapshot.agent_version.resolved_tool_versions)
                if ((not current_policy.action_proposal_only and (current_policy.approval_required or current_policy.risk_number >= 2))
                    or current_policy.catalog_version != invocation.catalog_version):
                    raise ToolPolicyError("TOOL_POLICY_CHANGED", "工具返回前策略已变化，结果不再进入模型")
                invocation.result_digest = hashlib.sha256(str(result.content).encode()).hexdigest()
                if invocation.tool_id == "create_mock_ticket":
                    payload = json.loads(str(result.content))
                    action = await session.scalar(select(BusinessAction).where(BusinessAction.id == UUID(payload["action_id"]),
                        BusinessAction.tenant_id == self.identity.tenant_id, BusinessAction.proposed_by == context.user.id))
                    if action is None:
                        raise ToolPolicyError("ACTION_STATE_CONFLICT", "动作提案缺少当前主体的冻结事实")
                    invocation.business_action_id = action.id
                if invocation.tool_id in MOCK_DIAGNOSTIC_TOOLS:
                    diagnostic = MockDiagnosticResponse.model_validate_json(str(result.content))
                    if diagnostic.tool_id != invocation.tool_id:
                        raise ToolPolicyError("DIAGNOSTIC_RESULT_INVALID", "诊断来源与调用不一致")
                    invocation.diagnostic_result = diagnostic.model_dump(mode="json")
                    invocation.result_digest = diagnostic_result_digest(diagnostic)
                if invocation.tool_id == "search_knowledge":
                    # 事务内先更新成功状态，后续证据授权失败会整体回滚。
                    if result.status != "error":
                        invocation.status = "succeeded"
                    payload = json.loads(str(result.content))
                    await lock_evidence_documents(session, evidence_ids=[UUID(item["evidence_id"]) for item in payload["evidence"]], identity=context)
                    # 网关内部生成的封闭结果契约；返回前再复查知识权限和有效性。
                    for item in sorted(payload["evidence"], key=lambda value: value["snapshot_id"]):
                        await read_knowledge_evidence(session, evidence_id=UUID(item["evidence_id"]),
                            identity=context, run_id=self.identity.run_id, attempt_id=self.identity.attempt_id)
                    if owned.attempt.context_snapshot is not None:
                        snapshot = ContextSnapshot.model_validate(owned.attempt.context_snapshot)
                        evidence = [ContextEvidence.model_validate({key: item[key] for key in (
                            "evidence_id", "snapshot_id", "version_id", "section_id", "content_digest", "retrieved_at",
                        )}) for item in payload["evidence"]]
                        owned.attempt.context_snapshot = snapshot.model_copy(update={
                            "evidence": [*snapshot.evidence, *evidence],
                            "unresolved_issues": [*snapshot.unresolved_issues, "KNOWLEDGE_SEARCH_TIMEOUT"]
                                if result.status == "error" else snapshot.unresolved_issues,
                        }).model_dump(mode="json")
            invocation.status, invocation.error_code = status, code
            invocation.finished_at = datetime.now(UTC)
            await append_event(session, run_id=self.identity.run_id, event_type="tool.completed" if status == "succeeded" else "tool.failed",
                attempt_id=self.identity.attempt_id,
                data={"invocation_id": str(invocation.id), "tool_id": invocation.tool_id, "status": status, "code": code})
        await self._observe(permit.invocation_id)

    async def execute(self, request: ToolCallRequest,
                      handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]]) -> ToolMessage | Command[Any]:
        # 同尝试的超额并行请求等待许可；数据库计数继续防跨进程绕过。
        async with self.parallel_permits:
            return await self._execute(request, handler)

    async def _execute(self, request: ToolCallRequest | ControlledToolCall,
                       handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]] | None) -> ToolMessage | Command[Any]:
        permit = await self._reserve(request)
        if permit.error is not None:
            await self._observe(permit.invocation_id)
            raise permit.error
        assert permit.identity is not None
        try:
            if request.tool_call["name"] == "search_knowledge":
                search_error = False
                try:
                    async with asyncio.timeout(self.settings.knowledge_search_timeout_seconds):
                        async with self.session_factory() as session, session.begin():
                            owned = await owned_run(session, self.identity)
                            context = await current_run_identity(session, run=owned.run, conversation=owned.conversation)
                            payload = await search_keyword_knowledge(session, identity=context, attempt=self.identity,
                                query=str(permit.arguments["query"]), invocation_id=permit.invocation_id,
                                max_results=int(permit.arguments.get("max_results", 5)),
                                output_budget_bytes=self.settings.tool_result_max_bytes)
                            search_error = payload.get("status") == "unavailable"
                except TimeoutError:
                    search_error = True
                    payload = {"schema_version": "1", "strategy": "keyword_v1", "status": "unavailable",
                        "evidence": [], "data_trust": "untrusted_knowledge_data", "error_code": "KNOWLEDGE_SEARCH_TIMEOUT",
                        "guidance": "知识检索超时，证据未取得。可按单工具/Run 剩余额度缩小范围重试；无法取得证据时追问或建议人工复核。"}
                result = ToolMessage(content=json.dumps(payload, ensure_ascii=False),
                    name="search_knowledge", tool_call_id=request.tool_call["id"], status="error" if search_error else "success")
                # 先处理每块正文再序列化，不能截断 JSON 导致证据契约不可解析。
                for item in payload["evidence"]:
                    for key in ("content", "title", "section_title"):
                        item[key] = str(self._sanitize(ToolMessage(content=item[key], tool_call_id=request.tool_call["id"])).content)
                result = result.model_copy(update={"content": json.dumps(payload, ensure_ascii=False)})
                if len(str(result.content).encode()) > self.settings.tool_result_max_bytes:
                    raise ToolPolicyError("TOOL_OUTPUT_TOO_LARGE", "知识证据超过输出预算，请减少检索数量")
            elif request.tool_call["name"] == "create_mock_ticket":
                async with self.session_factory() as session, session.begin():
                    owned = await owned_run(session, self.identity)
                    context = await current_run_identity(session, run=owned.run, conversation=owned.conversation)
                    action = await propose_mock_ticket(session, run_id=owned.run.id, identity=context,
                        settings=self.settings, correlation_id=owned.run.correlation_id)
                    payload = {"schema_version": "1", "data_environment": "mock", "action_id": str(action.id),
                        "status": action.status, "risk_level": action.risk_level,
                        "guidance": "这里只返回已冻结的提案事实；proposed/queued 不是执行成功。真实工单系统未连接，执行必须通过独立受控动作流程与复核回执。"}
                result = ToolMessage(content=json.dumps(payload, ensure_ascii=False), name="create_mock_ticket", tool_call_id=request.tool_call["id"])
            elif request.tool_call["name"] in MOCK_DIAGNOSTIC_TOOLS:
                async with self.session_factory() as session, session.begin():
                    owned = await owned_run(session, self.identity)
                    context = await current_run_identity(session, run=owned.run, conversation=owned.conversation)
                    response = await query_mock_diagnostic(session, identity=context, run=owned.run,
                        tool_id=request.tool_call["name"], query=DiagnosticQuery.model_validate(permit.arguments),
                        history_turns=self.settings.agent_context_history_turns)
                result = ToolMessage(content=response.model_dump_json(), name=request.tool_call["name"],
                    tool_call_id=request.tool_call["id"], status="error" if response.status in {"unavailable", "access_denied"} else "success")
            else:
                if handler is None or not isinstance(request, ToolCallRequest):
                    raise ToolPolicyError("TOOL_WORKFLOW_CALL_INVALID", "内部工作流只能调用受控查询实现")
                current_context = replace(self.snapshot.context, roles=permit.identity.principal.roles)
                trusted_request = replace(request, runtime=replace(request.runtime, context=current_context),
                    tool_call={**request.tool_call, "args": permit.arguments})
                result = await handler(trusted_request)
            if not isinstance(result, ToolMessage):
                raise ToolPolicyError("TOOL_RESULT_INVALID", "工具不能直接修改运行图状态")
            safe_result = result if request.tool_call["name"] == "search_knowledge" else self._sanitize(result)
            is_error = safe_result.status == "error"
            error_code = None
            if is_error:
                error_code = "TOOL_RETURNED_ERROR"
                if request.tool_call["name"] == "search_knowledge":
                    error_code = json.loads(str(safe_result.content)).get("error_code") or "KNOWLEDGE_SEARCH_UNAVAILABLE"
                elif request.tool_call["name"] in MOCK_DIAGNOSTIC_TOOLS:
                    error_code = MockDiagnosticResponse.model_validate_json(str(safe_result.content)).error_code or "DIAGNOSTIC_DEPENDENCY_UNAVAILABLE"
            await self._finish(permit, status="failed" if is_error else "succeeded",
                               code=error_code, result=safe_result)
            return safe_result
        except asyncio.CancelledError:
            try:
                await self._finish(permit, status="cancelled", code="TOOL_CANCELLED")
            except (RunLeaseLostError, RuntimeAuthorizationDenied, RunCancellationRequested, ToolPolicyError):
                pass
            raise
        except Exception as exc:
            code = (exc.code if isinstance(exc, ToolPolicyError) else
                    exc.code if isinstance(exc, RuntimeAuthorizationDenied) else "TOOL_EXECUTION_FAILED")
            try:
                await self._finish(permit, status="failed", code=code)
            except (RunLeaseLostError, RuntimeAuthorizationDenied, RunCancellationRequested, ToolPolicyError):
                pass
            if isinstance(exc, (RunLeaseLostError, RuntimeAuthorizationDenied, RunCancellationRequested, ToolPolicyError)):
                raise
            raise ToolPolicyError(code, "工具执行失败，未返回获准结果") from exc

    async def controlled_query(self, *, tool_id: str, arguments: dict[str, object]) -> tuple[ToolMessage, DiagnosticSource | None]:
        """固定工作流每节点最多一次；仅复用同 Run、同参数、五分钟内的成功只读事实。"""
        if tool_id not in {*MOCK_DIAGNOSTIC_TOOLS, "search_knowledge"}:
            raise ToolPolicyError("TOOL_WORKFLOW_CALL_INVALID", "内部诊断不能执行其他工具")
        tool = next((item for item in filter_runtime_tools(self.snapshot) if item.name == tool_id), None)
        if tool is None:
            raise ToolPolicyError("TOOL_NOT_BOUND", "固定 Agent 版本未绑定诊断所需查询")
        request = ControlledToolCall(tool_call={"id": f"diagnostic:{self.identity.attempt_id}:{tool_id}", "name": tool_id, "args": arguments}, tool=tool)
        validated = self._arguments(request)
        digest = hashlib.sha256(json.dumps(validated, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
        if tool_id in MOCK_DIAGNOSTIC_TOOLS:
            async with self.session_factory() as session, session.begin():
                owned = await owned_run(session, self.identity)
                context = await current_run_identity(session, run=owned.run, conversation=owned.conversation)
                if not self.settings.diagnostic_mock_enabled or self.settings.environment == "production":
                    raise ToolPolicyError("MOCK_DIAGNOSTICS_DISABLED", "当前不允许模拟诊断")
                policy = await resolve_tool_policy(session, identity=context, config=self.snapshot.config,
                    allowed_tool_ids=self.snapshot.agent_version.resolved_tool_ids, tool_id=tool_id, arguments=validated,
                    implementation_versions=self.snapshot.agent_version.resolved_tool_versions)
                if policy.approval_required or policy.risk_number >= 2:
                    raise ToolPolicyError("TOOL_POLICY_CHANGED", "只读诊断策略已变化")
                previous = await session.scalar(select(ToolInvocation).where(
                    ToolInvocation.tenant_id == self.identity.tenant_id, ToolInvocation.run_id == self.identity.run_id,
                    ToolInvocation.tool_id == tool_id, ToolInvocation.status == "succeeded",
                    ToolInvocation.arguments_digest == digest, ToolInvocation.implementation_version == policy.implementation_version,
                    ToolInvocation.catalog_version == policy.catalog_version, ToolInvocation.risk_level.in_(("R0", "R1")),
                    ToolInvocation.finished_at >= datetime.now(UTC) - timedelta(minutes=5),
                    ToolInvocation.diagnostic_result.is_not(None),
                ).order_by(ToolInvocation.created_at.desc(), ToolInvocation.id.desc()).limit(1))
                if previous is not None:
                    response = MockDiagnosticResponse.model_validate(previous.diagnostic_result)
                    if response.status == "ok" and response.tool_id == tool_id and diagnostic_result_digest(response) == previous.result_digest:
                        source = DiagnosticSource(invocation_id=previous.id, tool_id=tool_id,
                            implementation_version=previous.implementation_version, catalog_version=previous.catalog_version,
                            result_digest=previous.result_digest, observed_at=response.observed_at, reused=True)
                        await append_event(session, run_id=self.identity.run_id, attempt_id=self.identity.attempt_id,
                            event_type="diagnostic.query_reused", data={"tool_id": tool_id, "invocation_id": str(previous.id)})
                        return ToolMessage(content=response.model_dump_json(), tool_call_id=request.tool_call["id"], name=tool_id), source
        async with self.parallel_permits:
            result = await self._execute(request, None)
        if not isinstance(result, ToolMessage):
            raise ToolPolicyError("TOOL_RESULT_INVALID", "工作流查询缺少安全结果")
        if tool_id == "search_knowledge":
            return result, None
        async with self.session_factory() as session:
            invocation = await session.scalar(select(ToolInvocation).where(
                ToolInvocation.tenant_id == self.identity.tenant_id, ToolInvocation.run_id == self.identity.run_id,
                ToolInvocation.attempt_id == self.identity.attempt_id, ToolInvocation.tool_call_id == request.tool_call["id"]))
            if invocation is None or invocation.result_digest is None:
                raise ToolPolicyError("DIAGNOSTIC_RESULT_INVALID", "查询结果缺少持久化来源")
            response = MockDiagnosticResponse.model_validate_json(str(result.content))
            return result, DiagnosticSource(invocation_id=invocation.id, tool_id=tool_id,
                implementation_version=invocation.implementation_version, catalog_version=invocation.catalog_version,
                result_digest=invocation.result_digest, observed_at=response.observed_at)
