"""Agent Worker 的固定快照加载、执行、落库和稳定失败映射。"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from uuid import UUID, uuid4

import structlog
import httpx
from openai import APIConnectionError, APIStatusError
from pydantic import ValidationError
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from supportops_core.agent_config import SupportAnswer, SupportContext, parse_agent_config
from supportops_core.agent_runtime import (
    AgentCapabilityError,
    AgentCheckpointError,
    AgentExecutionResult,
    AgentObservationResult,
    AgentReferenceError,
    AgentRuntimeConfigurationError,
    AgentRuntimeSnapshot,
    AgentSecretResolutionError,
    AgentStructuredOutputError,
    invoke_agent,
    validate_runtime_capabilities,
)
from supportops_core.config import Settings
from supportops_core.context_services import ContextBudgetExceeded, ContextSnapshot, question_digest
from supportops_core.runtime_errors import runtime_error_message
from supportops_core.tool_observation_services import count_tool_invocations
from supportops_core.model_budget_services import ModelBudgetExceeded, ModelBudgetGateway, ModelBudgetSummary, model_budget_summary
from supportops_core.knowledge_retrieval import KnowledgeEvidenceError, assert_attempt_knowledge_authorized
from supportops_core.support_result_services import build_support_result
from supportops_core.history_context_services import assert_history_context_authorized, history_excerpt, history_source, readable_history_result
from supportops_core.tool_context_services import assert_tool_context_authorized
from supportops_core.enums import MessageRole, RunStatus
from supportops_core.models import (
    AgentErrorEvent,
    AgentRun,
    AgentRunObservation,
    AgentVersion,
    Conversation,
    Message,
    ModelCredential,
    ModelCredentialRevision,
    ModelEndpointVersion,
    User,
    RunAttempt,
    ToolInvocation,
)
from supportops_core.run_attempt_services import (
    RunClaim,
    RunAttemptIdentity, RunCancellationRequested, RunLeaseLostError, RuntimeAuthorizationDenied,
    check_and_renew_attempt, claim_run_attempt, current_run_identity, owned_run, release_conversation,
    reset_run_notification, close_unsettled_tools,
)
from supportops_core.secrets import create_secret_provider
from supportops_core.credential_services import resolve_credential_secret, CredentialAccessDenied, record_credential_denial
from supportops_core.services import ResourceNotFoundError, append_event, content_digest, transition_run
from supportops_core.tool_gateway import ToolExecutionGateway
from supportops_core.diagnostic_workflow import is_mock_diagnostic, run_mock_diagnostic
from supportops_core.tool_policy import ToolPolicyError

logger = structlog.get_logger()

_OBSERVATION_METADATA_KEYS = frozenset(
    {
        "failure_stage",
        "agent_version_id",
        "model_endpoint_version_id",
        "model_call_count",
        "tool_call_count",
        "retry_count",
        "finish_reason",
        "provider_request_id",
        "first_output_source",
        "first_output_boundary",
    }
)


def _sanitize_observation_metadata(payload: Mapping[str, object] | None) -> dict[str, object]:
    """只保留 Dashboard 需要的结构化字段，禁止把 prompt/response/secret 写入观测。"""
    if not payload:
        return {}
    return {
        key: value
        for key, value in payload.items()
        if key in _OBSERVATION_METADATA_KEYS
        and value is not None
        and isinstance(value, (str, int, float, bool))
    }


def _apply_model_budget(run: AgentRun, budget: ModelBudgetSummary) -> None:
    """成功、失败与取消用同一账本口径，不遗漏已发生的调用。"""
    run.model_call_count, run.retry_count = budget["model_call_count"], budget["retry_count"]
    run.input_tokens, run.output_tokens = budget["input_tokens"], budget["output_tokens"]
    run.cached_input_tokens, run.reasoning_tokens = budget["cached_input_tokens"], budget["reasoning_tokens"]
    run.total_cost_microusd, run.cost_source = budget["total_cost_microusd"], budget["cost_source"]


def _observation_from_result(
    run: AgentRun,
    observation: AgentObservationResult,
    *,
    default_cost_microusd: int | None = None,
    attempt_id: UUID | None = None,
) -> AgentRunObservation:
    """将运行时摘要映射为持久化观测，并使用统一 trace/span 关系。"""
    return AgentRunObservation(
        tenant_id=run.tenant_id,
        run_id=run.id,
        attempt_id=attempt_id,
        trace_id=run.correlation_id,
        span_id=f"{observation.kind}:{attempt_id or run.id}:{observation.name}",
        kind=observation.kind,
        name=observation.name,
        status=observation.status,
        started_at=observation.started_at,
        first_output_at=observation.first_output_at,
        finished_at=observation.finished_at,
        duration_ms=observation.duration_ms,
        provider=observation.provider,
        model_version_id=observation.model_version_id,
        tool_id=observation.tool_id,
        input_tokens=observation.input_tokens,
        output_tokens=observation.output_tokens,
        total_cost_microusd=observation.total_cost_microusd
        if observation.total_cost_microusd is not None
        else default_cost_microusd,
        error_code=observation.error_code,
        metadata_payload=_sanitize_observation_metadata(observation.metadata),
    )


async def _persist_run_observations(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    run_id: UUID,
    execution: AgentExecutionResult | None = None,
    observations: tuple[AgentObservationResult, ...] = (),
    status: str,
    error_code: str | None = None,
    attempt_identity: RunAttemptIdentity | None = None,
) -> None:
    """在独立事务写入观测，观测故障不得回滚已提交的产品状态。"""
    async with session_factory() as session, session.begin():
        run = await session.scalar(select(AgentRun).where(AgentRun.id == run_id).with_for_update())
        if run is None:
            return
        attempt = None
        if attempt_identity is not None:
            attempt = await session.scalar(select(RunAttempt).where(
                RunAttempt.id == attempt_identity.attempt_id, RunAttempt.run_id == run_id,
                RunAttempt.tenant_id == attempt_identity.tenant_id,
                RunAttempt.owner_id == attempt_identity.owner_id,
                RunAttempt.generation == attempt_identity.generation,
            ))
            if attempt is None:
                return
        finished_at = (attempt.finished_at if attempt else run.finished_at) or datetime.now(UTC)
        started_at = attempt.started_at if attempt else (run.started_at or run.created_at)
        attempt_budget = await model_budget_summary(session, tenant_id=run.tenant_id, run_id=run.id,
            attempt_id=attempt.id) if attempt is not None else None
        parent = AgentRunObservation(
            tenant_id=run.tenant_id,
            run_id=run.id,
            attempt_id=attempt.id if attempt else None,
            trace_id=run.correlation_id,
            span_id=f"agent:{attempt.id if attempt else run.id}",
            kind="agent",
            name="agent.execute",
            status=status,
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=_duration_ms(started_at, finished_at),
            input_tokens=attempt_budget["input_tokens"] if attempt_budget is not None else (execution.input_tokens if execution else run.input_tokens),
            output_tokens=attempt_budget["output_tokens"] if attempt_budget is not None else (execution.output_tokens if execution else run.output_tokens),
            total_cost_microusd=attempt_budget["total_cost_microusd"] if attempt_budget is not None else run.total_cost_microusd,
            error_code=error_code,
            metadata_payload=_sanitize_observation_metadata(
                {
                    "failure_stage": "worker" if error_code else None,
                    "agent_version_id": str(run.agent_version_id),
                    "model_endpoint_version_id": str(run.model_endpoint_version_id),
                }
            ),
        )
        session.add(parent)
        await session.flush()
        if attempt is not None:
            await session.execute(update(AgentRunObservation).where(
                AgentRunObservation.tenant_id == run.tenant_id, AgentRunObservation.run_id == run.id,
                AgentRunObservation.attempt_id == attempt.id,
                AgentRunObservation.kind.in_(("tool", "retrieval", "client", "api")), AgentRunObservation.parent_observation_id.is_(None),
            ).values(parent_observation_id=parent.id))
        child_observations = execution.observations if execution is not None else observations
        if not child_observations:
            return
        for observation in child_observations:
            if attempt is not None and observation.kind == "tool":
                # 正式工具由网关账本记追踪，不重复叠加 SDK callback/兜底摘要。
                continue
            child = _observation_from_result(
                run,
                observation,
                default_cost_microusd=None,
                attempt_id=attempt.id if attempt else None,
            )
            child.parent_observation_id = parent.id
            session.add(child)


def _duration_ms(start: datetime | None, end: datetime | None) -> int | None:
    """计算耗时并兼容 SQLite 测试返回的 naive datetime。"""
    if start is None or end is None:
        return None
    if (start.tzinfo is None) != (end.tzinfo is None):
        if start.tzinfo is None:
            end = end.replace(tzinfo=None)
        else:
            start = start.replace(tzinfo=None)
    return max(0, int((end - start).total_seconds() * 1000))


async def _load_snapshot(
    session: AsyncSession,
    *,
    run_id: UUID,
    settings: Settings,
    attempt_identity: RunAttemptIdentity,
    audit_session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> AgentRuntimeSnapshot:
    """按 Run 固定引用加载并交叉校验租户、版本、消息和凭据。"""
    run = await session.get(AgentRun, run_id)
    if run is None:
        raise AgentReferenceError("Run 不存在")
    conversation = await session.scalar(
        select(Conversation).where(
            Conversation.id == run.conversation_id,
            Conversation.tenant_id == run.tenant_id,
            Conversation.agent_id == run.agent_id,
            Conversation.agent_version_id == run.agent_version_id,
        )
    )
    version = await session.scalar(
        select(AgentVersion).where(
            AgentVersion.id == run.agent_version_id,
            AgentVersion.agent_id == run.agent_id,
            AgentVersion.tenant_id == run.tenant_id,
            AgentVersion.model_endpoint_version_id == run.model_endpoint_version_id,
            AgentVersion.config_digest == run.config_digest,
        )
    )
    model_version = await session.scalar(
        select(ModelEndpointVersion).where(
            ModelEndpointVersion.id == run.model_endpoint_version_id,
            ModelEndpointVersion.tenant_id == run.tenant_id,
        )
    )
    input_message = await session.scalar(
        select(Message).where(
            Message.id == run.input_message_id,
            Message.tenant_id == run.tenant_id,
            Message.conversation_id == run.conversation_id,
            Message.role == MessageRole.USER,
        )
    )
    if conversation is None or version is None or model_version is None or input_message is None:
        raise AgentReferenceError("Run 固定版本引用不一致")
    if input_message.user_id != conversation.user_id:
        raise AgentReferenceError("Run 输入消息不属于当前会话主体")
    credential = await session.scalar(
        select(ModelCredential).where(
            ModelCredential.id == model_version.credential_id,
            ModelCredential.tenant_id == run.tenant_id,
        )
    )
    credential_revision = await session.scalar(
        select(ModelCredentialRevision).where(
            ModelCredentialRevision.credential_id == model_version.credential_id,
            ModelCredentialRevision.tenant_id == run.tenant_id,
            ModelCredentialRevision.revision == run.credential_revision,
        )
    )
    user = await session.scalar(
        select(User).where(
            User.id == conversation.user_id,
            User.tenant_id == run.tenant_id,
        )
    )
    if credential is None or credential_revision is None:
        raise AgentSecretResolutionError("模型凭据 revision 已变化或不可用")
    if user is None or run.input_sequence is None or input_message.turn_sequence != run.input_sequence:
        raise AgentReferenceError("Run 用户或固定输入边界无效")
    identity = await current_run_identity(session, run=run, conversation=conversation)
    history_filters = [Message.tenant_id == run.tenant_id, Message.conversation_id == run.conversation_id,
                       Message.role.in_((MessageRole.USER, MessageRole.ASSISTANT)), Message.turn_sequence <= run.input_sequence]
    total_history = int(await session.scalar(select(func.count()).select_from(Message).where(*history_filters)) or 0)
    lower_sequence = max(1, run.input_sequence - settings.agent_context_history_turns)
    history = list((await session.scalars(select(Message).where(*history_filters,
        Message.turn_sequence >= lower_sequence)
        .order_by(Message.turn_sequence, Message.role.desc(), Message.created_at, Message.id)
        .limit(settings.agent_context_history_turns * 2 + 1))).all())
    if lower_sequence > 1:
        anchor = (await session.scalars(select(Message).where(*history_filters, Message.turn_sequence == 1)
            .order_by(Message.role.desc(), Message.created_at, Message.id).limit(2))).all()
        history = [*anchor, *history]
    selected_messages = []
    excerpts = []
    for message in history:
        allowed, projected = await readable_history_result(session, message=message, identity=identity, conversation_id=conversation.id)
        if not allowed:
            # 结构损坏、撤权及失效诊断/动作也不能退回旧正文或摘要。
            continue
        selected_messages.append(message)
        if message.id != input_message.id:
            excerpts.append(history_excerpt(message, projected))
    if not any(message.id == input_message.id for message in selected_messages):
        raise AgentReferenceError("有界历史缺少当前输入，不能生成脱离当前问题的回答")
    excerpts = list({entry.source.message_id: entry for entry in [*excerpts[:2], *excerpts[-10:]]}.values())
    product_messages = tuple((message.role.value, message.content) for message in selected_messages)
    context_snapshot = ContextSnapshot(current_input_id=input_message.id, question_digest=question_digest(input_message.content),
        through_input_sequence=run.input_sequence, selected_message_ids=[message.id for message in selected_messages],
        omitted_message_count=max(0, total_history - len(selected_messages)),
        history_sources=[history_source(message) for message in selected_messages], history_excerpts=excerpts)
    try:
        config = parse_agent_config(version.config)
    except ValidationError as exc:
        raise AgentRuntimeConfigurationError("已发布 Agent 配置无效") from exc
    secret_provider = create_secret_provider(settings)
    try:
        api_key = await resolve_credential_secret(session, tenant_id=run.tenant_id,
            credential_id=model_version.credential_id, revision=run.credential_revision, provider=secret_provider,
            actor_user_id=user.id, endpoint_id=model_version.endpoint_id, purpose="model_run",
            run_id=run.id, correlation_id=run.correlation_id, audit_session_factory=audit_session_factory)
    except CredentialAccessDenied:
        raise
    except Exception as exc:
        raise AgentSecretResolutionError("模型凭据无法解析") from exc
    snapshot = AgentRuntimeSnapshot(
        agent_version=version,
        model_version=model_version,
        config=config,
        api_key=api_key,
        context=SupportContext(
            tenant_id=run.tenant_id,
            user_id=user.id,
            roles=identity.principal.roles,
            conversation_id=conversation.id,
            run_id=run.id,
            correlation_id=run.correlation_id,
            attempt_id=attempt_identity.attempt_id,
            execution_generation=attempt_identity.generation,
            owner_id=attempt_identity.owner_id,
        ),
        input_text=input_message.content,
        product_messages=product_messages,
        context_snapshot=context_snapshot,
        product_message_ids=tuple(message.id for message in selected_messages),
    )
    validate_runtime_capabilities(snapshot)
    return snapshot


async def _fail_run(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    run_id: UUID,
    error_code: str,
    observations: tuple[AgentObservationResult, ...] = (),
    attempt_identity: RunAttemptIdentity,
    retryable: bool = False,
    settings: Settings,
) -> None:
    """在独立事务中安全地把运行中 Run 转为失败终态。"""
    event_payload: dict[str, object] | None = None
    async with session_factory() as session, session.begin():
        try:
            owned = await owned_run(session, attempt_identity, allow_cancelling=True)
        except (RunLeaseLostError, ResourceNotFoundError):
            return
        run, attempt = owned.run, owned.attempt
        now = datetime.now(UTC)
        if error_code.startswith("MODEL_CREDENTIAL_"):
            model_version = await session.get(ModelEndpointVersion, run.model_endpoint_version_id)
            if model_version is not None:
                record_credential_denial(session, tenant_id=run.tenant_id, actor_user_id=owned.conversation.user_id,
                    endpoint_id=model_version.endpoint_id, revision=run.credential_revision, purpose="model_run",
                    correlation_id=run.correlation_id, code=error_code, run_id=run.id)
        attempt.finished_at = now
        attempt.error_code = error_code
        attempt.status = "quarantined" if error_code == "AGENT_PRODUCT_STATE_COMMIT_FAILED" else "failed"
        attempt.metrics = {
            "model_call_count": sum(item.kind == "llm" for item in observations) or None,
            "tool_call_count": sum(item.kind == "tool" for item in observations) or None,
            "usage_source": "unknown",
        }
        await close_unsettled_tools(session, attempt, code=error_code)
        attempt.metrics = dict(await model_budget_summary(session, tenant_id=run.tenant_id, run_id=run.id, attempt_id=attempt.id))
        attempt.metrics["tool_call_count"] = await count_tool_invocations(session, tenant_id=run.tenant_id, run_id=run.id, attempt_id=attempt.id)
        run.tool_call_count = await count_tool_invocations(session, tenant_id=run.tenant_id, run_id=run.id)
        _apply_model_budget(run, await model_budget_summary(session, tenant_id=run.tenant_id, run_id=run.id))
        cancelled = run.status == RunStatus.CANCELLING or owned.conversation.deleted_at is not None
        if attempt.side_effect_state != "none":
            cancelled = False
            attempt.status = "quarantined"
            run.recovery_reason = "ACTION_RECONCILIATION_REQUIRED"
            await append_event(session, run_id=run.id, event_type="run.recovery_blocked", attempt_id=attempt.id,
                               data={"code": run.recovery_reason})
        elif cancelled:
            if run.status == RunStatus.RUNNING:
                transition_run(run, RunStatus.CANCELLING, now=now)
            transition_run(run, RunStatus.CANCELLED, now=now)
            attempt.status = "cancelled"
            await append_event(session, run_id=run.id, event_type="run.cancelled", attempt_id=attempt.id,
                               data={"status": "cancelled", "reason": error_code})
            release_conversation(owned)
        elif (retryable and attempt.replay_policy == "read_only" and attempt.side_effect_state == "none"
              and run.recovery_count < settings.run_attempt_max_recoveries):
            transition_run(run, RunStatus.QUEUED, now=now)
            run.current_attempt_id = None
            run.recovery_count += 1
            run.recovery_reason = error_code
            run.error_code = None
            release_conversation(owned)
            await append_event(session, run_id=run.id, event_type="text.replace", attempt_id=attempt.id, data={"content": ""})
            await append_event(session, run_id=run.id, event_type="run.recovering", attempt_id=attempt.id,
                               data={"status": "queued", "reason": error_code, "recovery_count": run.recovery_count})
            await reset_run_notification(session, run, now=now)
        else:
            transition_run(run, RunStatus.FAILED)
            release_conversation(owned)
        if run.status == RunStatus.FAILED:
            run.error_code = error_code
        finished_at = run.finished_at or datetime.now(UTC)
        run.queue_latency_ms = _duration_ms(run.created_at, run.started_at)
        run.execution_latency_ms = _duration_ms(run.started_at, finished_at)
        run.end_to_end_latency_ms = _duration_ms(run.created_at, finished_at)
        if run.status == RunStatus.FAILED:
            if error_code in {"KNOWLEDGE_EVIDENCE_INVALID", "RUNTIME_AUTHORIZATION_DENIED"}:
                await append_event(session, run_id=run.id, event_type="text.replace", attempt_id=attempt.id, data={"content": ""})
            await append_event(session, run_id=run.id, event_type="run.failed", attempt_id=attempt.id,
                               data={"status": "failed", "code": error_code, "message": runtime_error_message(error_code)})
            from supportops_core.escalation_services import BUDGET_ESCALATION_ERRORS
            if error_code in BUDGET_ESCALATION_ERRORS and settings.business_action_mock_enabled and settings.environment != "production":
                # 升级只保存提案/来源，失败 Run 不获得写动作许可，不再次调用模型。
                try:
                    async with session.begin_nested():
                        from supportops_core.action_proposal_services import propose_mock_ticket
                        actor = await current_run_identity(session, run=run, conversation=owned.conversation)
                        await propose_mock_ticket(session, run_id=run_id, identity=actor,
                            settings=settings, correlation_id=run.correlation_id)
                except Exception as exc:
                    await session.refresh(run)
                    logger.warning("budget_escalation_unavailable", run_id=str(run_id), error_type=type(exc).__name__)
                    await append_event(session, run_id=run_id, event_type="escalation.unavailable",
                        data={"code": getattr(exc, "code", "BUDGET_ESCALATION_UNAVAILABLE"), "data_environment": "mock"})
        event_payload = {
            "tenant_id": run.tenant_id,
            "run_id": run.id,
            "conversation_id": run.conversation_id,
            "agent_id": run.agent_id,
            "agent_version_id": run.agent_version_id,
            "model_endpoint_version_id": run.model_endpoint_version_id,
            "user_id": None,
            "occurred_at": finished_at,
            "severity": "error",
            "error_code": error_code,
            "reason": runtime_error_message(error_code),
            "stage": "agent.execute",
            "resolution_status": "unresolved",
            "retry_count": run.retry_count,
            "latency_ms": run.end_to_end_latency_ms,
            "metadata_payload": {"failure_stage": "worker"},
        }
        event_payload["user_id"] = await session.scalar(
            select(Conversation.user_id).where(
                Conversation.id == run.conversation_id,
                Conversation.tenant_id == run.tenant_id,
            )
        )
        if cancelled:
            event_payload = None
    try:
        await _persist_run_observations(
            session_factory,
            run_id=run_id,
            status="cancelled" if cancelled else "failed",
            error_code=error_code,
            observations=observations,
            attempt_identity=attempt_identity,
        )
    except Exception:
        logger.exception("agent_run_observation_persist_failed", run_id=str(run_id))
    if event_payload and event_payload["user_id"] is not None:
        try:
            async with session_factory() as event_session, event_session.begin():
                event_session.add(AgentErrorEvent(**event_payload))
        except Exception:
            logger.exception(
                "agent_error_event_persist_failed", run_id=str(run_id), error_code=error_code
            )


class _AnswerEventWriter:
    """按短时间窗口合并 token，独立提交可续传正文；数据库锁保护取消边界。"""

    def __init__(
        self, session_factory: async_sessionmaker[AsyncSession], run_id: UUID,
        attempt_identity: RunAttemptIdentity,
        settings: Settings,
    ) -> None:
        self.session_factory = session_factory
        self.run_id = run_id
        self.attempt_identity = attempt_identity
        self.settings = settings
        self.latest = ""
        self.persisted = ""
        self.changed = asyncio.Event()
        self.stopping = False
        self.task = asyncio.create_task(self._write())

    async def update(self, answer: str) -> None:
        if self.task.done():
            self.task.result()
        self.latest = answer
        self.changed.set()

    async def finish(self, answer: str) -> None:
        await self.update(answer)
        self.stopping = True
        self.changed.set()
        await self.task

    async def close(self) -> None:
        self.task.cancel()
        try:
            await self.task
        except asyncio.CancelledError:
            pass

    async def _write(self) -> None:
        while True:
            await self.changed.wait()
            if not self.stopping and self.persisted:
                await asyncio.sleep(0.05)
            self.changed.clear()
            answer = self.latest
            if answer != self.persisted:
                async with self.session_factory() as session, session.begin():
                    owned = await owned_run(session, self.attempt_identity)
                    current_identity = await current_run_identity(session, run=owned.run, conversation=owned.conversation)
                    history_snapshot = ContextSnapshot.model_validate(owned.attempt.context_snapshot) if owned.attempt.context_snapshot else None
                    await assert_history_context_authorized(session, snapshot=history_snapshot, identity=current_identity,
                        conversation_id=owned.conversation.id, run=owned.run)
                    await assert_attempt_knowledge_authorized(session, identity=current_identity, attempt=self.attempt_identity)
                    await assert_tool_context_authorized(session, snapshot=history_snapshot, identity=current_identity,
                        run=owned.run, settings=self.settings)
                    proposed_action = await session.scalar(select(ToolInvocation.id).where(ToolInvocation.tenant_id == self.attempt_identity.tenant_id,
                        ToolInvocation.run_id == self.run_id, ToolInvocation.attempt_id == self.attempt_identity.attempt_id,
                        ToolInvocation.status == "succeeded", ToolInvocation.business_action_id.is_not(None)).limit(1))
                    if proposed_action is not None:
                        answer = "已记录隔离模拟建单提案；提案或排队状态不表示工单已经创建，实际状态以动作详情和独立复核回执为准。"
                    extending = answer.startswith(self.persisted)
                    await append_event(
                        session,
                        run_id=self.run_id,
                        attempt_id=self.attempt_identity.attempt_id,
                        event_type="text.delta" if extending else "text.replace",
                        data={"delta": answer[len(self.persisted):]}
                        if extending else {"content": answer},
                    )
                self.persisted = answer
            if self.stopping:
                return


async def _invoke_with_lease(
    snapshot: AgentRuntimeSnapshot, *, session_factory: async_sessionmaker[AsyncSession],
    settings: Settings, identity: RunAttemptIdentity,
    on_observations: Callable[[tuple[AgentObservationResult, ...]], None],
) -> AgentExecutionResult:
    """续租/取消监督与推理独立等待；监督失败优先停止推理。"""
    async def check() -> None:
        await check_and_renew_attempt(session_factory, identity, lease_seconds=settings.run_attempt_lease_seconds)

    async def monitor() -> None:
        while True:
            await asyncio.sleep(settings.run_attempt_heartbeat_seconds)
            await check()

    writer = _AnswerEventWriter(session_factory, identity.run_id, identity, settings)
    gateway = ToolExecutionGateway(session_factory, settings=settings, snapshot=snapshot, identity=identity)

    async def execute() -> AgentExecutionResult:
        await check()
        if is_mock_diagnostic(gateway):
            answer = await run_mock_diagnostic(gateway)
            # 固定事实工作流没有发起模型调用，也不制造 checkpoint 或模型 usage。
            return AgentExecutionResult(answer=answer, model_call_count=0, finish_reason="diagnostic_workflow")
        return await invoke_agent(snapshot, settings=settings, on_observations=on_observations,
            on_answer=writer.update, boundary_check=check, tool_executor=gateway.execute,
            model_executor=ModelBudgetGateway(session_factory, settings=settings, snapshot=snapshot, identity=identity).execute)

    model_task = asyncio.create_task(execute(), name=f"agent:{identity.attempt_id}")
    monitor_task = asyncio.create_task(monitor(), name=f"lease:{identity.attempt_id}")
    try:
        async with asyncio.timeout(snapshot.config.runtime.run_timeout_seconds):
            done, _ = await asyncio.wait({model_task, monitor_task}, return_when=asyncio.FIRST_COMPLETED)
            if monitor_task in done:
                await monitor_task
                raise RunLeaseLostError("执行租约监督意外退出")
            execution = await model_task
            if isinstance(execution, SupportAnswer):
                execution = AgentExecutionResult(answer=execution)
            await writer.finish(execution.answer.answer)
            return execution
    finally:
        for task in (model_task, monitor_task):
            task.cancel()
        await asyncio.gather(model_task, monitor_task, return_exceptions=True)
        await writer.close()


async def _cancel_owned_attempt(
    session_factory: async_sessionmaker[AsyncSession], identity: RunAttemptIdentity, *, reason: str,
) -> bool:
    """仅当前有效尝试能取消产品 Run；隔离 checkpoint 不清除其他尝试。"""
    async with session_factory() as session, session.begin():
        try:
            owned = await owned_run(session, identity, allow_cancelling=True)
        except (RunLeaseLostError, ResourceNotFoundError):
            return False
        if owned.attempt.side_effect_state != "none":
            if owned.run.status == RunStatus.RUNNING:
                transition_run(owned.run, RunStatus.CANCELLING)
            owned.attempt.status = "quarantined"
            owned.attempt.error_code = "ACTION_RECONCILIATION_REQUIRED"
            owned.run.recovery_reason = "ACTION_RECONCILIATION_REQUIRED"
            await append_event(session, run_id=identity.run_id, event_type="run.recovery_blocked",
                               attempt_id=identity.attempt_id, data={"code": owned.run.recovery_reason})
            return False
        if owned.run.status == RunStatus.RUNNING:
            transition_run(owned.run, RunStatus.CANCELLING)
        transition_run(owned.run, RunStatus.CANCELLED)
        owned.attempt.status = "cancelled"
        await close_unsettled_tools(session, owned.attempt, code="RUN_CANCELLED")
        _apply_model_budget(owned.run, await model_budget_summary(session, tenant_id=owned.run.tenant_id, run_id=owned.run.id))
        owned.run.tool_call_count = await count_tool_invocations(session, tenant_id=owned.run.tenant_id, run_id=owned.run.id)
        owned.attempt.metrics = {**await model_budget_summary(session, tenant_id=owned.run.tenant_id,
            run_id=owned.run.id, attempt_id=owned.attempt.id), "tool_call_count": await count_tool_invocations(
                session, tenant_id=owned.run.tenant_id, run_id=owned.run.id, attempt_id=owned.attempt.id)}
        owned.attempt.finished_at = owned.run.finished_at
        release_conversation(owned)
        await append_event(session, run_id=identity.run_id, event_type="run.cancelled",
                           attempt_id=identity.attempt_id, data={"status": "cancelled", "reason": reason})
    return True


def _execution_error(exc: Exception) -> tuple[str, bool]:
    """仅明确临时依赖错误和安全产品重建可恢复，不重试权限/配置错误。"""
    if isinstance(exc, (ToolPolicyError, ModelBudgetExceeded, ContextBudgetExceeded, KnowledgeEvidenceError)):
        return exc.code, False
    if isinstance(exc, CredentialAccessDenied):
        return exc.code, False
    if isinstance(exc, (APIConnectionError, httpx.NetworkError, httpx.TimeoutException)):
        return "MODEL_DEPENDENCY_TEMPORARILY_UNAVAILABLE", True
    if isinstance(exc, APIStatusError) and (exc.status_code in {408, 409, 429} or exc.status_code >= 500):
        return "MODEL_DEPENDENCY_TEMPORARILY_UNAVAILABLE", True
    if isinstance(exc, TimeoutError):
        return "AGENT_RUN_TIMEOUT", False
    if isinstance(exc, RuntimeAuthorizationDenied):
        return exc.code, False
    if isinstance(exc, AgentReferenceError):
        return "AGENT_VERSION_REFERENCE_INVALID", False
    if isinstance(exc, AgentSecretResolutionError):
        return "MODEL_CREDENTIAL_UNAVAILABLE", False
    if isinstance(exc, AgentCapabilityError):
        return "MODEL_CAPABILITY_INCOMPATIBLE", False
    if isinstance(exc, AgentStructuredOutputError):
        return "AGENT_STRUCTURED_OUTPUT_INVALID", False
    if isinstance(exc, AgentCheckpointError):
        return "AGENT_CHECKPOINT_FAILED", True
    if isinstance(exc, AgentRuntimeConfigurationError):
        return "AGENT_RUNTIME_CONFIG_INVALID", False
    if isinstance(exc, ValueError):
        return "MODEL_ENDPOINT_BLOCKED", False
    return "AGENT_RUNTIME_ERROR", False


async def execute_agent_run(
    session_factory: async_sessionmaker[AsyncSession], run_id: UUID, *,
    settings: Settings, on_claim: Callable[[], None] | None = None, owner_id: str | None = None,
    preclaimed: RunClaim | None = None,
) -> bool:
    """领取独立尝试，所有产品写入受当前 owner/generation/租约约束。"""
    async with session_factory() as session, session.begin():
        claim = preclaimed
        if claim is None:
            claim = await claim_run_attempt(session, run_id, owner_id=owner_id or f"direct:{uuid4()}",
                lease_seconds=settings.run_attempt_lease_seconds, max_tenant_active_runs=settings.tenant_max_active_runs)
        else:
            if claim.identity.run_id != run_id or claim.identity.owner_id != owner_id:
                raise RunLeaseLostError("预领取的 Run/owner 不属于本执行者")
            await owned_run(session, claim.identity)
        if claim is None:
            logger.info("run_skipped", run_id=str(run_id), reason="not_executable")
            return False
    identity = claim.identity
    correlation_id = claim.run.correlation_id
    if on_claim is not None:
        on_claim()
    failed_observations: tuple[AgentObservationResult, ...] = ()
    product_commit_started = False
    product_committed = False

    def capture_observations(value: tuple[AgentObservationResult, ...]) -> None:
        nonlocal failed_observations
        failed_observations = value

    try:
        async with session_factory() as session, session.begin():
            snapshot = await _load_snapshot(session, run_id=run_id, settings=settings, attempt_identity=identity,
                audit_session_factory=session_factory)
        async with session_factory() as session, session.begin():
            owned = await owned_run(session, identity)
            await current_run_identity(session, run=owned.run, conversation=owned.conversation)
            owned.attempt.context_snapshot = snapshot.context_snapshot.model_dump(mode="json") if snapshot.context_snapshot else None
            await append_event(session, run_id=run_id, event_type="stage.started", attempt_id=identity.attempt_id,
                               data={"stage": "agent.execute", "attempt_id": str(identity.attempt_id),
                                     "generation": identity.generation})
        execution = await _invoke_with_lease(snapshot, session_factory=session_factory, settings=settings,
                                             identity=identity, on_observations=capture_observations)
        if execution.checkpoint_thread_id is not None and execution.checkpoint_thread_id != claim.attempt.checkpoint_thread_id:
            raise AgentCheckpointError("checkpoint thread 不属于当前尝试")
        product_commit_started = True
        async with session_factory() as session, session.begin():
            owned = await owned_run(session, identity)
            run, attempt, conversation = owned.run, owned.attempt, owned.conversation
            current_identity = await current_run_identity(session, run=run, conversation=conversation)
            context_snapshot = ContextSnapshot.model_validate(attempt.context_snapshot) if attempt.context_snapshot else None
            await assert_history_context_authorized(session, snapshot=context_snapshot, identity=current_identity,
                conversation_id=conversation.id, run=run, extra_evidence_ids=[
                    *(citation.evidence_id for citation in execution.answer.citations if citation.evidence_id is not None),
                    *(evidence_id for conflict in execution.answer.evidence_conflicts for evidence_id in conflict.evidence_ids)])
            support_result = await build_support_result(session, answer=execution.answer, identity=current_identity, attempt=identity)
            await assert_tool_context_authorized(session, snapshot=context_snapshot, identity=current_identity,
                run=run, settings=settings)
            if attempt.context_snapshot is not None and support_result.evidence_conflicts:
                context_snapshot = ContextSnapshot.model_validate(attempt.context_snapshot)
                attempt.context_snapshot = context_snapshot.model_copy(update={
                    "conflicts": list(dict.fromkeys([*context_snapshot.conflicts, "KNOWLEDGE_EVIDENCE_CONFLICT"])),
                    "unresolved_issues": [*context_snapshot.unresolved_issues, "KNOWLEDGE_CONCLUSION_UNCONFIRMED"],
                }).model_dump(mode="json")
            input_message = await session.get(Message, run.input_message_id)
            if input_message is None or input_message.turn_sequence != run.input_sequence:
                raise AgentReferenceError("Run 输入消息边界无效")
            message = Message(
                tenant_id=run.tenant_id, conversation_id=run.conversation_id, user_id=input_message.user_id,
                role=MessageRole.ASSISTANT, turn_sequence=run.input_sequence,
                content=support_result.answer, content_hash=content_digest(support_result.answer),
                idempotency_key=f"run:{run.id}:assistant", correlation_id=run.correlation_id,
                support_result=support_result.model_dump(mode="json"),
            )
            session.add(message)
            await session.flush()
            if support_result.answer != execution.answer.answer:
                await append_event(session, run_id=run.id, event_type="text.replace", attempt_id=attempt.id,
                                   data={"content": support_result.answer})
            await append_event(session, run_id=run.id, event_type="result.ready", attempt_id=attempt.id,
                               data={"message_id": str(message.id), "schema_version": "1"})
            await append_event(session, run_id=run.id, event_type="stage.completed", attempt_id=attempt.id,
                               data={"stage": "agent.execute"})
            from supportops_core.approval_services import prepare_runtime_approvals, queue_runtime_mock_tickets
            waiting_approval = await prepare_runtime_approvals(session, run=run, identity=current_identity, settings=settings)
            transition_run(run, RunStatus.WAITING_APPROVAL if waiting_approval else RunStatus.COMPLETED)
            await queue_runtime_mock_tickets(session, run=run, identity=current_identity, settings=settings)
            finished_at = run.finished_at or datetime.now(UTC)
            run.input_tokens, run.output_tokens = execution.input_tokens, execution.output_tokens
            run.cached_input_tokens, run.reasoning_tokens = execution.cached_input_tokens, execution.reasoning_tokens
            run.model_call_count, run.tool_call_count = execution.model_call_count, execution.tool_call_count
            run.retry_count, run.finish_reason = execution.retry_count, execution.finish_reason
            run.provider_request_id = execution.provider_request_id
            first_output_at = next((item.first_output_at for item in execution.observations if item.first_output_at is not None), None)
            run.time_to_first_token_ms = _duration_ms(attempt.started_at, first_output_at)
            run.queue_latency_ms = _duration_ms(run.created_at, run.started_at)
            run.execution_latency_ms = _duration_ms(attempt.started_at, finished_at)
            run.end_to_end_latency_ms = None if waiting_approval else _duration_ms(run.created_at, finished_at)
            input_price = snapshot.model_version.pricing.get("input_per_million_tokens")
            output_price = snapshot.model_version.pricing.get("output_per_million_tokens")
            if (execution.input_tokens is not None and execution.output_tokens is not None
                and isinstance(input_price, int | float) and isinstance(output_price, int | float)):
                run.total_cost_microusd = int(round(execution.input_tokens * float(input_price) + execution.output_tokens * float(output_price)))
                run.cost_source = "calculated"
            else:
                run.cost_source = "unknown"
            budget = await model_budget_summary(session, tenant_id=run.tenant_id, run_id=run.id)
            # 生产由独立账本计量，历史 checkpoint 和 SDK 重试不重复/漏计。
            _apply_model_budget(run, budget)
            run.tool_call_count = await count_tool_invocations(session, tenant_id=run.tenant_id, run_id=run.id)
            attempt.status = "completed"
            attempt.finished_at = finished_at
            attempt.checkpoint_id = execution.checkpoint_id
            attempt.metrics = {**await model_budget_summary(session, tenant_id=run.tenant_id, run_id=run.id, attempt_id=attempt.id),
                               "tool_call_count": await count_tool_invocations(session, tenant_id=run.tenant_id, run_id=run.id, attempt_id=attempt.id)}
            conversation.checkpoint_thread_id = execution.checkpoint_thread_id
            conversation.checkpoint_id = execution.checkpoint_id
            conversation.checkpoint_input_sequence = run.input_sequence
            release_conversation(owned)
            await append_event(session, run_id=run.id, event_type="run.waiting_approval" if waiting_approval else "run.completed", attempt_id=attempt.id,
                               data={"status": run.status.value, "answer_ready": True, "attempt_id": str(attempt.id), "generation": attempt.generation,
                                     "agent_id": str(run.agent_id), "agent_version_id": str(run.agent_version_id),
                                     "model_endpoint_version_id": str(run.model_endpoint_version_id)})
        product_committed = True
        try:
            await _persist_run_observations(session_factory, run_id=run_id, execution=execution,
                                            status="completed", attempt_identity=identity)
        except Exception:
            logger.exception("agent_run_observation_persist_failed", run_id=str(run_id), attempt_id=str(identity.attempt_id))
        logger.info("run_waiting_approval" if waiting_approval else "run_completed", run_id=str(run_id), attempt_id=str(identity.attempt_id), correlation_id=correlation_id)
        return True
    except asyncio.CancelledError:
        if not product_committed:
            await _cancel_owned_attempt(session_factory, identity, reason="worker_shutdown")
        raise
    except RunCancellationRequested:
        cancelled = await _cancel_owned_attempt(session_factory, identity, reason="user_cancelled")
        if cancelled:
            try:
                await _persist_run_observations(session_factory, run_id=run_id, status="cancelled",
                                                observations=failed_observations, attempt_identity=identity)
            except Exception:
                logger.exception("agent_run_observation_persist_failed", run_id=str(run_id))
        return False
    except RunLeaseLostError:
        # 恢复者已接管或租约过期，旧执行者不再更改 Run 或会话指针。
        logger.warning("run_attempt_lease_lost", run_id=str(run_id), attempt_id=str(identity.attempt_id), generation=identity.generation)
        return False
    except Exception as exc:
        error_code, retryable = _execution_error(exc)
        if isinstance(exc, KnowledgeEvidenceError):
            error_code, retryable = exc.code, False
        elif product_commit_started and not isinstance(exc, (RuntimeAuthorizationDenied, ToolPolicyError, ModelBudgetExceeded, ContextBudgetExceeded)):
            error_code, retryable = "AGENT_PRODUCT_STATE_COMMIT_FAILED", True
        logger.error("run_attempt_failed", run_id=str(run_id), attempt_id=str(identity.attempt_id),
                     correlation_id=correlation_id, error_code=error_code, error_type=type(exc).__name__)
        await _fail_run(session_factory, run_id=run_id, error_code=error_code, observations=failed_observations,
                        attempt_identity=identity, retryable=retryable, settings=settings)
        return False


execute_placeholder_run = execute_agent_run
