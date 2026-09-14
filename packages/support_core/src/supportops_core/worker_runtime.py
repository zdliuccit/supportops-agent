"""Agent Worker 的固定快照加载、执行、落库和稳定失败映射。"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import UTC, datetime
from uuid import UUID

import structlog
from pydantic import ValidationError
from sqlalchemy import select
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
    discard_checkpoint_thread,
    invoke_agent,
    validate_runtime_capabilities,
)
from supportops_core.config import Settings
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
)
from supportops_core.secrets import LocalEnvelopeSecretProvider
from supportops_core.services import append_event, claim_run, content_digest, transition_run

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


def _observation_from_result(
    run: AgentRun,
    observation: AgentObservationResult,
    *,
    default_cost_microusd: int | None = None,
) -> AgentRunObservation:
    """将运行时摘要映射为持久化观测，并使用统一 trace/span 关系。"""
    return AgentRunObservation(
        tenant_id=run.tenant_id,
        run_id=run.id,
        trace_id=run.correlation_id,
        span_id=f"{observation.kind}:{run.id}:{observation.name}",
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
) -> None:
    """在独立事务写入观测，观测故障不得回滚已提交的产品状态。"""
    async with session_factory() as session, session.begin():
        run = await session.scalar(select(AgentRun).where(AgentRun.id == run_id).with_for_update())
        if run is None:
            return
        finished_at = run.finished_at or datetime.now(UTC)
        parent = AgentRunObservation(
            tenant_id=run.tenant_id,
            run_id=run.id,
            trace_id=run.correlation_id,
            span_id=f"agent:{run.id}",
            kind="agent",
            name="agent.execute",
            status=status,
            started_at=run.started_at or run.created_at,
            finished_at=finished_at,
            duration_ms=run.execution_latency_ms,
            input_tokens=execution.input_tokens if execution else run.input_tokens,
            output_tokens=execution.output_tokens if execution else run.output_tokens,
            total_cost_microusd=run.total_cost_microusd,
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
        child_observations = execution.observations if execution is not None else observations
        if not child_observations:
            return
        for observation in child_observations:
            child = _observation_from_result(
                run,
                observation,
                default_cost_microusd=(
                    run.total_cost_microusd if observation.kind == "llm" else None
                ),
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
    if user is None:
        raise AgentReferenceError("Run 用户不存在")
    product_messages = tuple(
        (message.role.value, message.content)
        for message in (
            await session.scalars(
                select(Message)
                .where(
                    Message.tenant_id == run.tenant_id,
                    Message.conversation_id == run.conversation_id,
                    Message.role.in_((MessageRole.USER, MessageRole.ASSISTANT)),
                )
                .order_by(Message.created_at, Message.id)
            )
        ).all()
    )
    try:
        config = parse_agent_config(version.config)
    except ValidationError as exc:
        raise AgentRuntimeConfigurationError("已发布 Agent 配置无效") from exc
    secret_provider = LocalEnvelopeSecretProvider(settings.secret_encryption_key)
    try:
        api_key = secret_provider.open(credential_revision.encrypted_secret)
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
            roles=tuple(run.identity_roles),
            conversation_id=conversation.id,
            run_id=run.id,
            correlation_id=run.correlation_id,
        ),
        input_text=input_message.content,
        product_messages=product_messages,
    )
    validate_runtime_capabilities(snapshot)
    return snapshot


async def _fail_run(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    run_id: UUID,
    error_code: str,
    observations: tuple[AgentObservationResult, ...] = (),
) -> None:
    """在独立事务中安全地把运行中 Run 转为失败终态。"""
    event_payload: dict[str, object] | None = None
    async with session_factory() as session, session.begin():
        run = await session.scalar(select(AgentRun).where(AgentRun.id == run_id).with_for_update())
        if run is None or RunStatus(run.status) not in {
            RunStatus.RUNNING,
            RunStatus.CANCELLING,
        }:
            return
        transition_run(run, RunStatus.FAILED)
        run.error_code = error_code
        finished_at = run.finished_at or datetime.now(UTC)
        run.queue_latency_ms = _duration_ms(run.created_at, run.started_at)
        run.execution_latency_ms = _duration_ms(run.started_at, finished_at)
        run.end_to_end_latency_ms = _duration_ms(run.created_at, finished_at)
        await append_event(
            session,
            run_id=run.id,
            event_type="run.failed",
            data={"status": RunStatus.FAILED.value, "code": error_code},
        )
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
            "reason": error_code.replace("_", " ").lower(),
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
    try:
        await _persist_run_observations(
            session_factory,
            run_id=run_id,
            status="failed",
            error_code=error_code,
            observations=observations,
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


async def execute_agent_run(
    session_factory: async_sessionmaker[AsyncSession],
    run_id: UUID,
    *,
    settings: Settings,
) -> bool:
    """幂等领取并执行 Run；仅在回答消息事务提交后标记 completed。"""
    async with session_factory() as session, session.begin():
        run = await claim_run(session, run_id)
        if run is None:
            logger.info("run_skipped", run_id=str(run_id), reason="not_queued")
            return False
        correlation_id = run.correlation_id

    checkpoint_committed = False
    checkpoint_context: SupportContext | None = None
    product_state_commit_started = False
    cancelled_after_checkpoint = False
    failed_observations: tuple[AgentObservationResult, ...] = ()

    def failed_observations_set(value: tuple[AgentObservationResult, ...]) -> None:
        nonlocal failed_observations
        failed_observations = value

    try:
        async with session_factory() as session:
            snapshot = await _load_snapshot(session, run_id=run_id, settings=settings)
        checkpoint_context = snapshot.context
        async with session_factory() as session, session.begin():
            await append_event(
                session,
                run_id=run_id,
                event_type="stage.started",
                data={
                    "stage": "agent.execute",
                    "agent_version_id": str(snapshot.agent_version.id),
                    "model_endpoint_version_id": str(snapshot.model_version.id),
                },
            )
        async with asyncio.timeout(snapshot.config.runtime.run_timeout_seconds):
            execution = await invoke_agent(
                snapshot,
                settings=settings,
                on_observations=lambda value: failed_observations_set(value),
            )
            # 兼容现有扩展点和测试替身：旧实现只返回 SupportAnswer。
            if isinstance(execution, SupportAnswer):
                execution = AgentExecutionResult(answer=execution)
        checkpoint_committed = True
        product_state_commit_started = True
        async with session_factory() as session, session.begin():
            run = await session.scalar(
                select(AgentRun).where(AgentRun.id == run_id).with_for_update()
            )
            if run is None:
                raise AgentReferenceError("Run 在产品状态提交前不存在")
            if RunStatus(run.status) == RunStatus.CANCELLING:
                transition_run(run, RunStatus.CANCELLED)
                await append_event(
                    session,
                    run_id=run.id,
                    event_type="run.cancelled",
                    data={"status": RunStatus.CANCELLED.value},
                )
                cancelled_after_checkpoint = True
            else:
                input_message = await session.get(Message, run.input_message_id)
                if input_message is None:
                    raise AgentReferenceError("Run 输入消息不存在")
                await append_event(
                    session,
                    run_id=run.id,
                    event_type="text.delta",
                    data={"delta": execution.answer.answer},
                )
                session.add(
                    Message(
                        tenant_id=run.tenant_id,
                        conversation_id=run.conversation_id,
                        user_id=input_message.user_id,
                        role=MessageRole.ASSISTANT,
                        content=execution.answer.answer,
                        content_hash=content_digest(execution.answer.answer),
                        idempotency_key=f"run:{run.id}:assistant",
                        correlation_id=run.correlation_id,
                    )
                )
                await append_event(
                    session,
                    run_id=run.id,
                    event_type="stage.completed",
                    data={"stage": "agent.execute"},
                )
                transition_run(run, RunStatus.COMPLETED)
                finished_at = run.finished_at or datetime.now(UTC)
                run.input_tokens = execution.input_tokens
                run.output_tokens = execution.output_tokens
                run.cached_input_tokens = execution.cached_input_tokens
                run.reasoning_tokens = execution.reasoning_tokens
                run.model_call_count = execution.model_call_count
                run.tool_call_count = execution.tool_call_count
                run.retry_count = execution.retry_count
                run.finish_reason = execution.finish_reason
                run.provider_request_id = execution.provider_request_id
                first_output_at = next(
                    (
                        item.first_output_at
                        for item in execution.observations
                        if item.first_output_at is not None
                    ),
                    None,
                )
                run.time_to_first_token_ms = _duration_ms(run.started_at, first_output_at)
                run.queue_latency_ms = _duration_ms(run.created_at, run.started_at)
                run.execution_latency_ms = _duration_ms(run.started_at, finished_at)
                run.end_to_end_latency_ms = _duration_ms(run.created_at, finished_at)
                input_price = snapshot.model_version.pricing.get("input_per_million_tokens")
                output_price = snapshot.model_version.pricing.get("output_per_million_tokens")
                if (
                    execution.input_tokens is not None
                    and execution.output_tokens is not None
                    and isinstance(input_price, int | float)
                    and isinstance(output_price, int | float)
                ):
                    run.total_cost_microusd = int(
                        round(
                            execution.input_tokens * float(input_price)
                            + execution.output_tokens * float(output_price)
                        )
                    )
                    run.cost_source = "calculated"
                else:
                    run.cost_source = "unknown"
                await append_event(
                    session,
                    run_id=run.id,
                    event_type="run.completed",
                    data={
                        "status": RunStatus.COMPLETED.value,
                        "agent_id": str(run.agent_id),
                        "agent_version_id": str(run.agent_version_id),
                        "model_endpoint_version_id": str(run.model_endpoint_version_id),
                    },
                )
        if not cancelled_after_checkpoint:
            try:
                await _persist_run_observations(
                    session_factory,
                    run_id=run_id,
                    execution=execution,
                    status="completed",
                )
            except Exception:
                logger.exception("agent_run_observation_persist_failed", run_id=str(run_id))
        if cancelled_after_checkpoint:
            assert checkpoint_context is not None
            await discard_checkpoint_thread(settings, context=checkpoint_context)
            return True
        logger.info("run_completed", run_id=str(run_id), correlation_id=correlation_id)
        return True
    except Exception as exc:
        error_type = type(exc).__name__
        if isinstance(exc, TimeoutError):
            error_code = "AGENT_RUN_TIMEOUT"
        elif isinstance(exc, AgentReferenceError):
            error_code = "AGENT_VERSION_REFERENCE_INVALID"
        elif isinstance(exc, AgentSecretResolutionError):
            error_code = "MODEL_CREDENTIAL_UNAVAILABLE"
        elif isinstance(exc, AgentCapabilityError):
            error_code = "MODEL_CAPABILITY_INCOMPATIBLE"
        elif isinstance(exc, AgentStructuredOutputError):
            error_code = "AGENT_STRUCTURED_OUTPUT_INVALID"
        elif isinstance(exc, AgentCheckpointError):
            error_code = "AGENT_CHECKPOINT_FAILED"
        elif isinstance(exc, AgentRuntimeConfigurationError):
            error_code = "AGENT_RUNTIME_CONFIG_INVALID"
        elif isinstance(exc, ValueError):
            error_code = "MODEL_ENDPOINT_BLOCKED"
        else:
            error_code = "AGENT_RUNTIME_ERROR"
        if product_state_commit_started and checkpoint_committed:
            error_code = "AGENT_PRODUCT_STATE_COMMIT_FAILED"
        if checkpoint_context is not None:
            try:
                await discard_checkpoint_thread(settings, context=checkpoint_context)
            except Exception:
                logger.exception(
                    "checkpoint_consistency_cleanup_failed",
                    run_id=str(run_id),
                    correlation_id=correlation_id,
                )
    logger.error(
        "run_failed",
        run_id=str(run_id),
        correlation_id=correlation_id,
        error_code=error_code,
        error_type=error_type,
    )
    await _fail_run(
        session_factory,
        run_id=run_id,
        error_code=error_code,
        observations=failed_observations,
    )
    return False


execute_placeholder_run = execute_agent_run
