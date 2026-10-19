"""应用侧已授权调用到独立执行器的显式桥接；不把网络或取密能力装入模型进程。"""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from supportops_core.enums import AuditAction
from supportops_core.mcp_http_authorization import HttpMcpAuthorizationError, _active
from supportops_core.mcp_http_broker import (
    DurableHttpMcpCredentialBroker, _active_binding, _row, _state, _transaction, authorize_http_mcp_dispatch,
)
from supportops_core.mcp_http_client import HttpMcpToolObservation, _freeze, _validate
from supportops_core.mcp_http_executor_contracts import MAX_INPUT_BYTES, HttpMcpExecutorRequest
from supportops_core.mcp_http_executor_ipc import dispatch_http_executor
from supportops_core.models import AgentAuditEvent


async def dispatch_authorized_http_mcp_tool(factory: async_sessionmaker[AsyncSession], *,
    request: HttpMcpExecutorRequest, broker: DurableHttpMcpCredentialBroker,
) -> HttpMcpToolObservation:
    """只接受网关已预留ToolInvocation；库函数不新增许可/配额/Agent注册或自动重试。"""
    try:
        if not isinstance(broker, DurableHttpMcpCredentialBroker) or broker.session_factory is not factory:
            raise ValueError()
        # 首个await前冻结完整范围/描述/参数；无SecretStr或任意执行命令。
        fixed = HttpMcpExecutorRequest.model_validate(_freeze(request.model_dump(mode="json"), max_bytes=MAX_INPUT_BYTES))
        await authorize_http_mcp_dispatch(factory, fixed.request, authority=broker.upstream)
        await broker.assert_current_permit(fixed.request)
        async with _transaction(factory) as session:
            row = await _row(session, fixed.request)
            if row.status != "authorized":
                raise HttpMcpAuthorizationError("MCP_HTTP_DISPATCH_ALREADY_CLAIMED")
        started_at = datetime.now(UTC)
        # 一次socket派发；超时/取消/回执丢失保留原claim/bound，不能推断未执行。
        report = await dispatch_http_executor(fixed)
        if report.status != "observed" or report.observation is None:
            raise HttpMcpAuthorizationError(report.error_code or "MCP_HTTP_EXECUTOR_UNCONFIRMED")
        observation = report.observation
        if (observation.source_record_id != fixed.request.policy.source_record_id
            or observation.descriptor_digest != fixed.request.binding.descriptor_digest
            or observation.received_at < started_at or observation.received_at > datetime.now(UTC)):
            raise ValueError()
        # 应用侧也按固定输出Schema校验；未安装已有可选jsonschema时明确拒绝，不跳过校验。
        _validate(fixed.descriptor.output_schema, _freeze(observation.data, max_bytes=65_536))
        await broker.assert_current_permit(fixed.request)
        async with _transaction(factory) as session:
            row = await _row(session, fixed.request)
            state = _state(row)
            if state.revoked_at is not None or state.expires_at <= datetime.now(UTC):
                raise ValueError()
            await _active_binding(session, fixed.request)
            audit = await session.scalar(select(AgentAuditEvent.id).where(
                AgentAuditEvent.tenant_id == fixed.request.binding.tenant_id,
                AgentAuditEvent.actor_user_id == fixed.request.binding.actor_user_id,
                AgentAuditEvent.action == AuditAction.MCP_AUTHORIZATION_EVENT,
                AgentAuditEvent.correlation_id == "mcp:" + str(fixed.request.binding.dispatch_id),
                AgentAuditEvent.metadata_payload["request_digest"].as_string() == fixed.request_digest,
                AgentAuditEvent.metadata_payload["lease_id"].as_string() == str(state.lease_id),
                AgentAuditEvent.metadata_payload["event"].as_string() == "call_confirmed",
                AgentAuditEvent.metadata_payload["code"].as_string() == "MCP_HTTP_CALL_CONFIRMED",
            ).limit(1))
            if audit is None:
                raise HttpMcpAuthorizationError("MCP_HTTP_AUDIT_UNAVAILABLE")
        current = await broker.current(fixed.request, state.lease_id)
        _active(fixed.request, current)
        if current != state:
            raise HttpMcpAuthorizationError("MCP_HTTP_AUTHORIZATION_CHANGED")
        await broker.assert_current_permit(fixed.request)
        # 仅返回不可信观察；未来网关仍须统一过滤/账本/来源证明，不能当业务verified。
        return observation
    except HttpMcpAuthorizationError:
        raise
    except Exception:
        raise HttpMcpAuthorizationError("MCP_HTTP_EXECUTOR_UNCONFIRMED") from None
