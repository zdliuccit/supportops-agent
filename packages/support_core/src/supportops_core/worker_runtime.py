"""Agent Worker 的固定快照加载、执行、落库和稳定失败映射。"""

from __future__ import annotations

import asyncio
from uuid import UUID

import structlog
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from supportops_core.agent_config import SupportContext, parse_agent_config
from supportops_core.agent_runtime import (
    AgentCapabilityError,
    AgentCheckpointError,
    AgentReferenceError,
    AgentRuntimeConfigurationError,
    AgentRuntimeSnapshot,
    AgentSecretResolutionError,
    AgentStructuredOutputError,
    invoke_agent,
    validate_runtime_capabilities,
)
from supportops_core.config import Settings
from supportops_core.enums import MessageRole, RunStatus
from supportops_core.models import (
    AgentRun,
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
    )
    validate_runtime_capabilities(snapshot)
    return snapshot


async def _fail_run(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    run_id: UUID,
    error_code: str,
) -> None:
    """在独立事务中安全地把运行中 Run 转为失败终态。"""
    async with session_factory() as session, session.begin():
        run = await session.scalar(select(AgentRun).where(AgentRun.id == run_id).with_for_update())
        if run is None or RunStatus(run.status) not in {
            RunStatus.RUNNING,
            RunStatus.CANCELLING,
        }:
            return
        transition_run(run, RunStatus.FAILED)
        run.error_code = error_code
        await append_event(
            session,
            run_id=run.id,
            event_type="run.failed",
            data={"status": RunStatus.FAILED.value, "code": error_code},
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

    try:
        async with session_factory() as session:
            snapshot = await _load_snapshot(session, run_id=run_id, settings=settings)
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
            answer = await invoke_agent(snapshot, settings=settings)
        async with session_factory() as session, session.begin():
            run = await session.scalar(
                select(AgentRun).where(AgentRun.id == run_id).with_for_update()
            )
            if run is None:
                return False
            if RunStatus(run.status) == RunStatus.CANCELLING:
                transition_run(run, RunStatus.CANCELLED)
                await append_event(
                    session,
                    run_id=run.id,
                    event_type="run.cancelled",
                    data={"status": RunStatus.CANCELLED.value},
                )
                return True
            input_message = await session.get(Message, run.input_message_id)
            if input_message is None:
                raise AgentReferenceError("Run 输入消息不存在")
            await append_event(
                session,
                run_id=run.id,
                event_type="text.delta",
                data={"delta": answer.answer},
            )
            session.add(
                Message(
                    tenant_id=run.tenant_id,
                    conversation_id=run.conversation_id,
                    user_id=input_message.user_id,
                    role=MessageRole.ASSISTANT,
                    content=answer.answer,
                    content_hash=content_digest(answer.answer),
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
    logger.error(
        "run_failed",
        run_id=str(run_id),
        correlation_id=correlation_id,
        error_code=error_code,
        error_type=error_type,
    )
    await _fail_run(session_factory, run_id=run_id, error_code=error_code)
    return False


execute_placeholder_run = execute_agent_run
