"""应用侧持久化一次派发broker；取密在事务外，未知取密永久保留已领取。"""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from collections.abc import AsyncIterator, Awaitable, Callable
from uuid import UUID, uuid4

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from supportops_core.agent_config import agent_config_digest, parse_agent_config
from supportops_core.enums import ApprovalPolicy, RunStatus
from supportops_core.mcp_http_authorization import (
    HttpMcpAuthorizationError, HttpMcpCredentialBroker, HttpMcpTokenGrant, _bounded, _scope_matches, _token,
)
from supportops_core.mcp_http_authorization_contracts import HttpMcpAuthorizationAudit, HttpMcpCredentialRequest, HttpMcpTokenState
from supportops_core.mcp_http_jwt import HttpMcpJwtVerificationPolicy, VerifiedJwtHttpMcpCredentialBroker
from supportops_core.models import (
    AgentRun, AgentVersion, Conversation, HttpMcpDispatch, RunAttempt, ToolCatalogEntry,
    ToolDiscoveryRevision, ToolInvocation,
)
from supportops_core.run_attempt_services import current_run_identity
from supportops_core.tool_connection_state import aware, latest_connection
from supportops_core.tool_discovery_contracts import canonical_json, discovery_digest
from supportops_core.tool_discovery_services import require_source_snapshot

EXECUTOR_UID = 10004


@asynccontextmanager
async def _transaction(factory: async_sessionmaker[AsyncSession]) -> AsyncIterator[AsyncSession]:
    async with asyncio.timeout(2), factory() as session, session.begin():
        if session.get_bind().dialect.name != "postgresql":
            raise HttpMcpAuthorizationError("MCP_HTTP_DISPATCH_UNAVAILABLE")
        await session.execute(text("SET LOCAL statement_timeout='1s'"))
        await session.execute(text("SET LOCAL lock_timeout='250ms'"))
        if await session.scalar(text("SHOW transaction_isolation")) != "read committed":
            raise HttpMcpAuthorizationError("MCP_HTTP_DISPATCH_UNAVAILABLE")
        yield session


async def _active_binding(session: AsyncSession, request: HttpMcpCredentialRequest) -> None:
    """重查平台事实；业务目标字段/同意及凭据当前批准仍由上游独立校验。"""
    binding, policy = request.binding, request.policy
    now = datetime.now(UTC)
    if min(policy.valid_until, binding.expires_at) <= now:
        raise HttpMcpAuthorizationError("MCP_HTTP_AUTHORIZATION_EXPIRED")
    run = await session.scalar(select(AgentRun).where(AgentRun.id == binding.run_id, AgentRun.tenant_id == binding.tenant_id)
        .execution_options(populate_existing=True))
    if run is None:
        raise ValueError()
    conversation = await session.scalar(select(Conversation).where(Conversation.id == run.conversation_id,
        Conversation.tenant_id == binding.tenant_id, Conversation.user_id == binding.actor_user_id).execution_options(populate_existing=True))
    attempt = await session.scalar(select(RunAttempt).where(RunAttempt.id == binding.attempt_id,
        RunAttempt.run_id == binding.run_id, RunAttempt.tenant_id == binding.tenant_id).execution_options(populate_existing=True))
    invocation = await session.scalar(select(ToolInvocation).where(ToolInvocation.id == binding.invocation_id,
        ToolInvocation.tenant_id == binding.tenant_id, ToolInvocation.run_id == binding.run_id,
        ToolInvocation.attempt_id == binding.attempt_id).execution_options(populate_existing=True))
    if (conversation is None or attempt is None or invocation is None or run.status != RunStatus.RUNNING
        or conversation.agent_id != run.agent_id or conversation.agent_version_id != run.agent_version_id
        or run.current_attempt_id != attempt.id or run.execution_generation != binding.generation
        or conversation.active_run_id != run.id or conversation.active_attempt_id != attempt.id
        or attempt.status != "running" or attempt.generation != binding.generation or aware(attempt.lease_expires_at) <= now
        or binding.expires_at > aware(attempt.lease_expires_at) or invocation.status != "running"
        or invocation.tool_id != binding.tool_id or invocation.implementation_version != binding.implementation_version
        or invocation.arguments_digest != binding.arguments_digest or invocation.risk_level not in {"R0", "R1"}
        or invocation.business_action_id is not None):
        raise ValueError()
    identity = await current_run_identity(session, run=run, conversation=conversation)
    version = await session.scalar(select(AgentVersion).where(AgentVersion.id == run.agent_version_id,
        AgentVersion.tenant_id == binding.tenant_id, AgentVersion.agent_id == run.agent_id).execution_options(populate_existing=True))
    if (version is None or version.withdrawn_at is not None or binding.tool_id not in version.resolved_tool_ids
        or version.model_endpoint_version_id != run.model_endpoint_version_id):
        raise ValueError()
    if version.resolved_tool_versions.get(binding.tool_id) != binding.implementation_version:
        raise ValueError()
    config = parse_agent_config(version.config)
    tool = next((item for item in config.tools if item.tool_id == binding.tool_id and item.enabled), None)
    if (tool is None or tool.approval_policy == ApprovalPolicy.REQUIRED or run.config_digest != agent_config_digest(config)
        or version.config_digest != run.config_digest):
        raise ValueError()
    entry = await session.scalar(select(ToolCatalogEntry).where(ToolCatalogEntry.tenant_id == binding.tenant_id,
        ToolCatalogEntry.tool_id == binding.tool_id).execution_options(populate_existing=True))
    if (entry is None or not entry.is_enabled or entry.is_revoked or entry.version != invocation.catalog_version
        or entry.risk_level != "low" or entry.required_roles and not set(identity.principal.roles).intersection(entry.required_roles)):
        raise ValueError()
    source = await session.scalar(select(ToolDiscoveryRevision).where(ToolDiscoveryRevision.tenant_id == binding.tenant_id,
        ToolDiscoveryRevision.source_key == policy.source_key).order_by(ToolDiscoveryRevision.revision.desc()).limit(1)
        .execution_options(populate_existing=True))
    if source is None or source.id != policy.source_record_id or source.policy_digest != policy.source_policy_digest:
        raise ValueError()
    snapshot = require_source_snapshot(source)
    selected = [item for item in snapshot.tools if item.tool_id == binding.tool_id and item.descriptor_digest == binding.descriptor_digest]
    if len(selected) != 1:
        raise ValueError()
    event = await latest_connection(session, source)
    if event is not None and event.circuit_state != "closed":
        raise ValueError()


def _request(row: HttpMcpDispatch) -> HttpMcpCredentialRequest:
    request = HttpMcpCredentialRequest.model_validate(row.request_payload)
    binding, policy = request.binding, request.policy
    if (request.request_digest != row.request_digest or binding.dispatch_id != row.id
        or binding.tenant_id != row.tenant_id or binding.actor_user_id != row.actor_user_id
        or binding.run_id != row.run_id or binding.attempt_id != row.attempt_id or binding.invocation_id != row.invocation_id
        or binding.generation != row.generation or policy.source_record_id != row.source_record_id
        or binding.expires_at != aware(row.expires_at) or row.executor_uid != EXECUTOR_UID):
        raise ValueError()
    return request


async def _row(session: AsyncSession, request: HttpMcpCredentialRequest, *, lock: bool = False) -> HttpMcpDispatch:
    query = select(HttpMcpDispatch).where(HttpMcpDispatch.id == request.binding.dispatch_id,
        HttpMcpDispatch.tenant_id == request.binding.tenant_id).execution_options(populate_existing=True)
    row = await session.scalar(query.with_for_update() if lock else query)
    if row is None or _request(row) != request:
        raise HttpMcpAuthorizationError("MCP_HTTP_DISPATCH_SCOPE_REJECTED")
    return row


def _state(row: HttpMcpDispatch) -> HttpMcpTokenState:
    if row.status not in {"bound", "revoked"} or row.token_state is None:
        raise HttpMcpAuthorizationError("MCP_HTTP_DISPATCH_UNAVAILABLE")
    if discovery_digest(row.token_state) != row.token_state_digest:
        raise ValueError()
    state = HttpMcpTokenState.model_validate(row.token_state)
    if state.revoked_at is not None or state.lease_id != row.lease_id:
        raise ValueError()
    _scope_matches(_request(row), state)
    if row.status == "revoked":
        if row.revoked_at is None:
            raise ValueError()
        state = state.model_copy(update={"revoked_at": aware(row.revoked_at)})
    return state


async def authorize_http_mcp_dispatch(factory: async_sessionmaker[AsyncSession], request: HttpMcpCredentialRequest, *,
    authority: HttpMcpCredentialBroker | None = None,
) -> None:
    """可信网关保存许可；无API/模型入口，缺少独立当前策略/资源授权者直接拒绝。"""
    if authority is None or authority.provider_id != request.policy.provider_id:
        raise HttpMcpAuthorizationError("MCP_HTTP_PROVIDER_NOT_CONFIGURED")
    try:
        async with asyncio.timeout(5):
            await _bounded(authority.assert_current_permit(request))
            async with _transaction(factory) as session:
                # 仅此调用scope加锁，遵循会话→Run→尝试→工具顺序，不持锁取密或联网。
                run_scope = (await session.execute(select(AgentRun.conversation_id).where(AgentRun.id == request.binding.run_id,
                    AgentRun.tenant_id == request.binding.tenant_id))).one_or_none()
                if run_scope is None:
                    raise ValueError()
                await session.scalar(select(Conversation).where(Conversation.id == run_scope.conversation_id,
                    Conversation.tenant_id == request.binding.tenant_id).with_for_update(key_share=True))
                await session.scalar(select(AgentRun).where(AgentRun.id == request.binding.run_id,
                    AgentRun.tenant_id == request.binding.tenant_id).with_for_update())
                await session.scalar(select(RunAttempt).where(RunAttempt.id == request.binding.attempt_id,
                    RunAttempt.tenant_id == request.binding.tenant_id).with_for_update())
                await session.scalar(select(ToolInvocation).where(ToolInvocation.id == request.binding.invocation_id,
                    ToolInvocation.tenant_id == request.binding.tenant_id).with_for_update())
                await _active_binding(session, request)
                existing = await session.scalar(select(HttpMcpDispatch).where(HttpMcpDispatch.invocation_id == request.binding.invocation_id))
                if existing is not None:
                    if _request(existing) != request:
                        raise ValueError()
                    return  # 查询原事实，不重置领取或重新发令牌。
                payload = request.model_dump(mode="json")
                now = datetime.now(UTC)
                if len(canonical_json(payload).encode("utf-8")) > 16_384 or request.binding.expires_at > now+timedelta(seconds=300):
                    raise ValueError()
                session.add(HttpMcpDispatch(id=request.binding.dispatch_id, tenant_id=request.binding.tenant_id,
                    actor_user_id=request.binding.actor_user_id, run_id=request.binding.run_id, attempt_id=request.binding.attempt_id,
                    invocation_id=request.binding.invocation_id, source_record_id=request.policy.source_record_id,
                    generation=request.binding.generation, executor_uid=EXECUTOR_UID, request_digest=request.request_digest,
                    request_payload=payload, status="authorized", revision=1, created_at=now,
                    expires_at=request.binding.expires_at, updated_at=now))
    except HttpMcpAuthorizationError:
        raise
    except Exception:
        raise HttpMcpAuthorizationError("MCP_HTTP_DISPATCH_UNAVAILABLE") from None


@dataclass(frozen=True, slots=True)
class DurableHttpMcpCredentialBroker:
    """应用/broker身份使用；上游必须是真实受控凭据服务，不能递归包装自身。"""

    session_factory: async_sessionmaker[AsyncSession] = field(repr=False)
    upstream: HttpMcpCredentialBroker = field(repr=False)
    platform_issuer: str = field(repr=False)
    platform_audience: str = field(repr=False)

    @property
    def provider_id(self) -> str:
        return self.upstream.provider_id

    async def resolve_dispatch(self, dispatch_id: UUID, digest: str, *, peer_uid: int) -> HttpMcpCredentialRequest:
        try:
            async with _transaction(self.session_factory) as session:
                row = await session.get(HttpMcpDispatch, dispatch_id)
                if row is None or row.executor_uid != peer_uid or row.request_digest != digest:
                    raise ValueError()
                return _request(row)
        except Exception:
            raise HttpMcpAuthorizationError("MCP_HTTP_DISPATCH_SCOPE_REJECTED") from None

    async def assert_current_permit(self, request: HttpMcpCredentialRequest) -> None:
        try:
            if not self.platform_issuer or not self.platform_audience or self.provider_id != request.policy.provider_id:
                raise ValueError()
            if request.policy.issuer == self.platform_issuer or request.policy.resource == self.platform_audience:
                raise HttpMcpAuthorizationError("MCP_HTTP_PLATFORM_TOKEN_REJECTED")
            async with _transaction(self.session_factory) as session:
                row = await _row(session, request)
                if row.status not in {"authorized", "claimed", "bound"}:
                    raise ValueError()
                await _active_binding(session, request)
            await _bounded(self.upstream.assert_current_permit(request))
        except HttpMcpAuthorizationError:
            raise
        except Exception:
            raise HttpMcpAuthorizationError("MCP_HTTP_AUTHORIZATION_CHANGED") from None

    async def validate_audit_binding(self, request: HttpMcpCredentialRequest, event: HttpMcpAuthorizationAudit) -> None:
        """只核对已持久化原lease，不以审计读取授予当前执行许可。"""
        try:
            if event.dispatch_id != request.binding.dispatch_id or event.request_digest != request.request_digest:
                raise ValueError()
            async with _transaction(self.session_factory) as session:
                row = await _row(session, request)
                if event.lease_id is not None:
                    state = _state(row)
                    if event.lease_id != state.lease_id:
                        raise ValueError()
                    if event.event == "revocation_confirmed" and state.revoked_at is None:
                        raise ValueError()
                    if event.event in {"credential_acquired", "call_confirmed"} and state.revoked_at is not None:
                        raise ValueError()
        except Exception:
            raise HttpMcpAuthorizationError("MCP_HTTP_AUDIT_UNAVAILABLE") from None

    async def claim(self, request: HttpMcpCredentialRequest) -> HttpMcpTokenGrant:
        try:
            await self.assert_current_permit(request)
            claim_id = uuid4()
            async with _transaction(self.session_factory) as session:
                row = await _row(session, request, lock=True)
                if row.status != "authorized":
                    raise HttpMcpAuthorizationError("MCP_HTTP_DISPATCH_ALREADY_CLAIMED")
                await _active_binding(session, request)
                row.status, row.claim_id, row.claimed_at = "claimed", claim_id, datetime.now(UTC)
                row.revision += 1
                row.updated_at = row.claimed_at
            # 提交不明或取消则不进入取密；取密之后失败不清理claim、不重取/缓存明文。
            grant = await _bounded(self.upstream.claim(request), 3)
            _token(request, grant, platform_issuer=self.platform_issuer, platform_audience=self.platform_audience)
            await _bounded(self.upstream.assert_current_permit(request))
            async with _transaction(self.session_factory) as session:
                row = await _row(session, request, lock=True)
                if row.status != "claimed" or row.claim_id != claim_id:
                    raise ValueError()
                await _active_binding(session, request)
                state = grant.state.model_dump(mode="json")
                if len(canonical_json(state).encode("utf-8")) > 8192:
                    raise ValueError()
                row.status, row.token_state, row.token_state_digest = "bound", state, discovery_digest(state)
                row.lease_id = grant.state.lease_id
                row.revision += 1
                row.updated_at = datetime.now(UTC)
            return grant
        except HttpMcpAuthorizationError:
            raise
        except Exception:
            raise HttpMcpAuthorizationError("MCP_HTTP_DISPATCH_UNAVAILABLE") from None

    async def current(self, request: HttpMcpCredentialRequest, lease_id: UUID) -> HttpMcpTokenState:
        try:
            async with _transaction(self.session_factory) as session:
                expected = _state(await _row(session, request))
            if expected.lease_id != lease_id:
                raise ValueError()
            current = await _bounded(self.upstream.current(request, lease_id))
            if not isinstance(current, HttpMcpTokenState):
                raise ValueError()
            _scope_matches(request, current)
            if current.model_copy(update={"revoked_at": expected.revoked_at}) != expected:
                raise ValueError()
            if expected.revoked_at is not None and current.revoked_at != expected.revoked_at:
                raise ValueError()
            if current.revoked_at is not None:
                if current.revoked_at > datetime.now(UTC):
                    raise ValueError()
                async with _transaction(self.session_factory) as session:
                    row = await _row(session, request, lock=True)
                    persisted = _state(row)
                    if persisted.revoked_at is None:
                        row.status, row.revoked_at = "revoked", current.revoked_at
                        row.revision += 1
                        row.updated_at = datetime.now(UTC)
                    elif persisted.revoked_at != current.revoked_at:
                        raise ValueError()
            return current
        except HttpMcpAuthorizationError:
            raise
        except Exception:
            raise HttpMcpAuthorizationError("MCP_HTTP_CREDENTIAL_REJECTED") from None

    async def revoke(self, request: HttpMcpCredentialRequest, lease_id: UUID) -> None:
        # 撤销不要求原执行仍活跃；上游须独立鉴定当前撤销权限。
        state = await self.current(request, lease_id)
        if state.revoked_at is None:
            await _bounded(self.upstream.revoke(request, lease_id), 3)
            state = await self.current(request, lease_id)
        if state.revoked_at is None:
            raise HttpMcpAuthorizationError("MCP_HTTP_REVOCATION_UNCONFIRMED")


def create_jwt_verified_http_mcp_broker(factory: async_sessionmaker[AsyncSession], *,
    upstream: HttpMcpCredentialBroker, policy: HttpMcpJwtVerificationPolicy,
    current_policy: Callable[[HttpMcpCredentialRequest], Awaitable[HttpMcpJwtVerificationPolicy]],
    platform_issuer: str, platform_audience: str,
) -> DurableHttpMcpCredentialBroker:
    """显式构造持久化→验签→真实上游链；不读取配置/取密/连库或提供默认提供者。"""
    if not platform_issuer or not platform_audience or not callable(current_policy) or not upstream.provider_id:
        raise HttpMcpAuthorizationError("MCP_HTTP_PROVIDER_NOT_CONFIGURED")
    verified = VerifiedJwtHttpMcpCredentialBroker(upstream=upstream, policy=policy, current_policy=current_policy,
        platform_issuer=platform_issuer, platform_audience=platform_audience)
    return DurableHttpMcpCredentialBroker(session_factory=factory, upstream=verified,
        platform_issuer=platform_issuer, platform_audience=platform_audience)
