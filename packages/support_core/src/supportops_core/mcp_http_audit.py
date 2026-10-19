"""应用侧独立审计写入器；不在隔离连接器中注入数据库连接或权限。"""

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import and_, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from supportops_core.enums import AuditAction, AuditResourceType
from supportops_core.mcp_http_authorization import HttpMcpAuthorizationError
from supportops_core.mcp_http_authorization_contracts import HttpMcpAuthorizationAudit, HttpMcpCredentialRequest
from supportops_core.models import AgentAuditEvent, AgentRun, Conversation, RunAttempt, ToolDiscoveryRevision, ToolInvocation, User


@dataclass(frozen=True, slots=True)
class DatabaseHttpMcpAuditSink:
    """可信网关/审计broker侧使用，未来隔离执行器应经认证IPC调用，不能持有此工厂。"""

    session_factory: async_sessionmaker[AsyncSession] = field(repr=False)
    request: HttpMcpCredentialRequest = field(repr=False)

    async def record(self, event: HttpMcpAuthorizationAudit) -> None:
        deadline = asyncio.get_running_loop().time()+2
        try:
            binding, policy = self.request.binding, self.request.policy
            if (event.request_digest != self.request.request_digest or event.dispatch_id != binding.dispatch_id
                or event.observed_at > datetime.now(UTC)):
                raise ValueError()
            async with asyncio.timeout(2), self.session_factory() as session, session.begin():
                if session.get_bind().dialect.name == "postgresql":
                    await session.execute(text("SET LOCAL statement_timeout='1s'"))
                    await session.execute(text("SET LOCAL lock_timeout='250ms'"))
                # 只核对原引用元数据；取消/过期/软删除后仍可记录失败，不据此授予当前执行权限。
                row = (await session.execute(select(AgentRun.agent_id, AgentRun.agent_version_id)
                    .select_from(ToolInvocation)
                    .join(RunAttempt, and_(RunAttempt.id == ToolInvocation.attempt_id, RunAttempt.run_id == ToolInvocation.run_id))
                    .join(AgentRun, AgentRun.id == ToolInvocation.run_id)
                    .join(Conversation, Conversation.id == AgentRun.conversation_id)
                    .join(User, and_(User.id == Conversation.user_id, User.tenant_id == AgentRun.tenant_id))
                    .join(ToolDiscoveryRevision, and_(ToolDiscoveryRevision.id == policy.source_record_id,
                        ToolDiscoveryRevision.tenant_id == AgentRun.tenant_id))
                    .where(ToolInvocation.id == binding.invocation_id, ToolInvocation.tenant_id == binding.tenant_id,
                        ToolInvocation.run_id == binding.run_id, ToolInvocation.attempt_id == binding.attempt_id,
                        ToolInvocation.tool_id == binding.tool_id, ToolInvocation.implementation_version == binding.implementation_version,
                        ToolInvocation.arguments_digest == binding.arguments_digest,
                        RunAttempt.tenant_id == binding.tenant_id, RunAttempt.generation == binding.generation,
                        AgentRun.tenant_id == binding.tenant_id, Conversation.tenant_id == binding.tenant_id,
                        Conversation.user_id == binding.actor_user_id, ToolDiscoveryRevision.source_key == policy.source_key,
                        ToolDiscoveryRevision.policy_digest == policy.source_policy_digest)
                    .execution_options(include_deleted=True))).one_or_none()
                if row is None:
                    raise ValueError()
                session.add(AgentAuditEvent(tenant_id=binding.tenant_id, actor_user_id=binding.actor_user_id,
                    agent_id=row.agent_id, version_id=row.agent_version_id, resource_type=AuditResourceType.TOOL,
                    action=AuditAction.MCP_AUTHORIZATION_EVENT, correlation_id="mcp:"+str(binding.dispatch_id),
                    metadata_payload={"schema_version": event.schema_version, "request_digest": event.request_digest,
                        "dispatch_id": str(event.dispatch_id), "lease_id": str(event.lease_id) if event.lease_id else None,
                        "event": event.event, "code": event.code, "observed_at": event.observed_at.isoformat(),
                        "run_id": str(binding.run_id), "attempt_id": str(binding.attempt_id), "generation": binding.generation,
                        "invocation_id": str(binding.invocation_id), "source_record_id": str(policy.source_record_id),
                        "credential_id": str(policy.credential_id), "credential_revision": policy.credential_revision}))
            if asyncio.get_running_loop().time() >= deadline:
                raise ValueError()
        except Exception:
            # 未知提交不当作未写入，不重试或暴露SQL/参数；外部取消仍传播。
            raise HttpMcpAuthorizationError("MCP_HTTP_AUDIT_UNAVAILABLE") from None
