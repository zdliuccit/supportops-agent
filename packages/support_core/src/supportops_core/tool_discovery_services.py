"""工具发现治理控制面：批准、不可变快照与失效围栏；不调用或注册远端工具。"""

import re
from typing import Literal, cast
from uuid import UUID

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from supportops_core.agent_services import require_platform_admin
from supportops_core.auth import AuthenticationError, IdentityContext, ensure_identity
from supportops_core.enums import AuditAction, AuditResourceType
from supportops_core.models import AgentAuditEvent, AuthSession, Tenant, ToolDiscoveryRevision, User, utc_now
from supportops_core.redaction import redact_sensitive_text
from supportops_core.services import ResourceNotFoundError
from supportops_core.tool_discovery_contracts import (
    MAX_CATALOG_BYTES, DiscoveredTool, DiscoveryCatalogCommand, DiscoveryInvalidationCommand,
    DiscoveryPolicyCommand, DiscoverySnapshot, DiscoverySnapshotTool, DiscoveryToolBinding,
    DiscoveryView, ToolSourcePolicy, canonical_json, discovery_digest, parse_discovery_catalog, policy_payload,
)


class ToolDiscoveryConflict(ValueError):
    """只携带固定说明，不透出远端目录、Schema或验证异常。"""


class ToolDiscoverySessionInvalid(AuthenticationError):
    """事务边界复检会话失败，供API返回固定401。"""


async def _administrator(session: AsyncSession, identity: IdentityContext, *, lock: bool) -> IdentityContext:
    principal = identity.principal
    # 所有来源写操作先串行同租户，再锁身份；来源IO由未来适配器在事务外完成。
    tenant_query = select(Tenant).where(Tenant.id == principal.tenant_id).execution_options(populate_existing=True)
    if lock:
        tenant_query = tenant_query.with_for_update()
    if await session.scalar(tenant_query) is None:
        raise ToolDiscoverySessionInvalid("登录会话已失效，请重新登录")
    user_query = select(User).where(User.id == principal.user_id, User.tenant_id == principal.tenant_id).execution_options(populate_existing=True)
    if lock:
        user_query = user_query.with_for_update()
    if await session.scalar(user_query) is None:
        raise ToolDiscoverySessionInvalid("登录会话已失效，请重新登录")
    if principal.session_id is not None:
        auth_query = select(AuthSession).where(AuthSession.id == principal.session_id).execution_options(populate_existing=True)
        if lock:
            auth_query = auth_query.with_for_update()
        if await session.scalar(auth_query) is None:
            raise ToolDiscoverySessionInvalid("登录会话已失效，请重新登录")
    try:
        current = await ensure_identity(session, principal, require_session=True)
    except AuthenticationError:
        raise ToolDiscoverySessionInvalid("登录会话已失效，请重新登录") from None
    require_platform_admin(current)
    return current


async def _latest(session: AsyncSession, tenant_id: UUID, source_key: str) -> ToolDiscoveryRevision | None:
    if not isinstance(source_key, str) or re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", source_key) is None:
        raise ToolDiscoveryConflict("工具来源标识无效")
    return await session.scalar(select(ToolDiscoveryRevision).where(
        ToolDiscoveryRevision.tenant_id == tenant_id, ToolDiscoveryRevision.source_key == source_key,
    ).order_by(ToolDiscoveryRevision.revision.desc(), ToolDiscoveryRevision.id.desc()).limit(1)
        .execution_options(populate_existing=True))


def _policy_digest(tenant_id: UUID, source_key: str, payload: dict[str, object]) -> str:
    return discovery_digest({"tenant_id": str(tenant_id), "source_key": source_key, "policy": payload})


def _policy(row: ToolDiscoveryRevision) -> ToolSourcePolicy:
    try:
        policy = ToolSourcePolicy.model_validate(row.policy)
        payload = policy_payload(policy)
        if payload != row.policy or _policy_digest(row.tenant_id, row.source_key, payload) != row.policy_digest:
            raise ValueError()
        if row.revision != row.parent_revision + 1 or row.parent_revision < 0:
            raise ValueError()
        expected_status = {
            "list_changed": "pending", "manual_refresh": "pending",
            "catalog_recorded": "ready", "catalog_rejected": "rejected",
        }
        if row.event_kind == "source_configured":
            if row.status != ("pending" if policy.enabled else "disabled"):
                raise ValueError()
        elif expected_status.get(row.event_kind) != row.status:
            raise ValueError()
        if row.status != "disabled" and not policy.enabled:
            raise ValueError()
        if row.status != "ready" and (row.catalog is not None or row.catalog_digest is not None):
            raise ValueError()
        if not 0 <= row.observed_count <= 100 or (row.status == "rejected") != (row.failure_code is not None):
            raise ValueError()
        if row.failure_code is not None and row.failure_code != "TOOL_DISCOVERY_CATALOG_REJECTED":
            raise ValueError()
        return policy
    except Exception:
        raise ToolDiscoveryConflict("工具发现记录完整性不可确认") from None


def _usable(policy: ToolSourcePolicy) -> None:
    if not policy.enabled or policy.valid_until <= utc_now():
        raise ToolDiscoveryConflict("工具来源未批准、已停用或已到期")


def _binding(tenant_id: UUID, source_key: str, tool: DiscoveredTool) -> DiscoverySnapshotTool:
    descriptor = tool.model_dump(mode="json")
    return DiscoverySnapshotTool(
        tool_id="mcp_" + discovery_digest([str(tenant_id), source_key, tool.name]),
        remote_name=tool.name, descriptor_digest=discovery_digest(descriptor),
        input_schema_digest=discovery_digest(tool.input_schema), output_schema_digest=discovery_digest(tool.output_schema),
        descriptor=tool,
    )


def _snapshot(row: ToolDiscoveryRevision, policy: ToolSourcePolicy) -> DiscoverySnapshot:
    try:
        snapshot = DiscoverySnapshot.model_validate(row.catalog)
        if (snapshot.tenant_id != row.tenant_id or snapshot.source_key != row.source_key
            or snapshot.policy_digest != row.policy_digest or snapshot.base_revision != row.parent_revision
            or discovery_digest(snapshot.model_dump(mode="json")) != row.catalog_digest):
            raise ValueError()
        allowed = {tool.name: tool.descriptor_digest for tool in policy.allowed_tools}
        expected = [_binding(row.tenant_id, row.source_key, tool.descriptor) for tool in snapshot.tools]
        if (len(expected) != len(allowed) or len({tool.remote_name for tool in expected}) != len(expected)
            or any(tool.descriptor_digest != allowed.get(tool.remote_name) for tool in expected)
            or [tool.model_dump(mode="json") for tool in expected] != [tool.model_dump(mode="json") for tool in snapshot.tools]):
            raise ValueError()
        return snapshot
    except Exception:
        raise ToolDiscoveryConflict("固定工具目录完整性不可确认") from None


def _view(row: ToolDiscoveryRevision) -> DiscoveryView:
    policy = _policy(row)
    snapshot = _snapshot(row, policy) if row.status == "ready" else None
    expired = policy.enabled and policy.valid_until <= utc_now()
    # _policy已核对持久化状态；expired只作本次读取投影，不改写历史。
    status = cast(Literal["pending", "ready", "disabled", "rejected", "expired"], "expired" if expired else row.status)
    tools = [DiscoveryToolBinding.model_validate(tool.model_dump(exclude={"descriptor"}))
        for tool in snapshot.tools] if snapshot is not None and not expired else []
    return DiscoveryView(record_id=row.id, source_key=row.source_key, revision=row.revision, status=status,
        policy_digest=row.policy_digest, policy=policy, source_fingerprint=policy.source_fingerprint,
        valid_until=policy.valid_until, catalog_digest=row.catalog_digest if not expired else None,
        tools=tools, failure_code=row.failure_code, observed_count=row.observed_count, created_at=row.created_at)


async def _append(session: AsyncSession, identity: IdentityContext, *, source_key: str,
    previous: ToolDiscoveryRevision | None, policy: dict[str, object], event: str, status: str,
    correlation_id: str, catalog: dict[str, object] | None = None, failure_code: str | None = None,
    observed_count: int = 0,
) -> ToolDiscoveryRevision:
    parent = previous.revision if previous is not None else 0
    if parent >= 2_147_483_646:
        raise ToolDiscoveryConflict("工具来源修订已耗尽")
    row = ToolDiscoveryRevision(tenant_id=identity.principal.tenant_id, source_key=source_key,
        revision=parent + 1, parent_revision=parent, event_kind=event, status=status,
        policy=policy, policy_digest=_policy_digest(identity.principal.tenant_id, source_key, policy),
        catalog=catalog, catalog_digest=discovery_digest(catalog) if catalog is not None else None,
        failure_code=failure_code, observed_count=observed_count, actor_user_id=identity.user.id)
    session.add(row)
    await session.flush()
    await _administrator(session, identity, lock=True)
    if status == "ready":
        _usable(_policy(row))
    session.add(AgentAuditEvent(tenant_id=row.tenant_id, actor_user_id=identity.user.id,
        resource_type=AuditResourceType.TOOL, action=AuditAction.TOOL_UPDATED, correlation_id=correlation_id,
        metadata_payload={"discovery_record_id": str(row.id), "source_key_digest": discovery_digest(source_key),
            "discovery_revision": row.revision, "discovery_event": event, "status": status}))
    await session.flush()
    return row


async def configure_tool_source(session: AsyncSession, identity: IdentityContext, *, source_key: str,
    command: DiscoveryPolicyCommand, correlation_id: str,
) -> DiscoveryView:
    payload = policy_payload(command.policy)
    await _administrator(session, identity, lock=True)
    current = await _latest(session, identity.principal.tenant_id, source_key)
    if (current.revision if current else 0) != command.expected_revision:
        raise ToolDiscoveryConflict("来源修订已变化，请重新读取后批准")
    if current is not None:
        _policy(current)
    if command.policy.enabled:
        _usable(ToolSourcePolicy.model_validate(payload))
    row = await _append(session, identity, source_key=source_key, previous=current, policy=payload,
        event="source_configured", status="pending" if command.policy.enabled else "disabled", correlation_id=correlation_id)
    return _view(row)


async def read_tool_source(session: AsyncSession, identity: IdentityContext, *, source_key: str) -> DiscoveryView:
    await _administrator(session, identity, lock=False)
    row = await _latest(session, identity.principal.tenant_id, source_key)
    if row is None:
        raise ResourceNotFoundError("工具来源不存在")
    return _view(row)


async def current_source_for_admin(session: AsyncSession, identity: IdentityContext, *, source_key: str,
    lock: bool = False,
) -> tuple[ToolDiscoveryRevision, ToolSourcePolicy]:
    """供连接控制面复用同一身份/租户锁与批准完整性检查。"""
    await _administrator(session, identity, lock=lock)
    row = await _latest(session, identity.principal.tenant_id, source_key)
    if row is None:
        raise ResourceNotFoundError("工具来源不存在")
    return row, _policy(row)


def require_source_snapshot(source: ToolDiscoveryRevision) -> DiscoverySnapshot:
    policy = _policy(source)
    _usable(policy)
    return _snapshot(source, policy)


async def list_tool_sources(session: AsyncSession, identity: IdentityContext, *,
    page_size: int, offset: int,
) -> tuple[list[DiscoveryView], int]:
    """先限定租户并按来源选最新修订，再SQL计数/分页，不读取全部历史。"""
    await _administrator(session, identity, lock=False)
    latest = select(
        ToolDiscoveryRevision.source_key,
        func.max(ToolDiscoveryRevision.revision).label("revision"),
    ).where(ToolDiscoveryRevision.tenant_id == identity.principal.tenant_id).group_by(
        ToolDiscoveryRevision.source_key,
    ).subquery()
    total = int(await session.scalar(select(func.count()).select_from(latest)) or 0)
    rows = await session.scalars(select(ToolDiscoveryRevision).join(latest, and_(
        ToolDiscoveryRevision.source_key == latest.c.source_key,
        ToolDiscoveryRevision.revision == latest.c.revision,
    )).where(ToolDiscoveryRevision.tenant_id == identity.principal.tenant_id)
        .order_by(ToolDiscoveryRevision.source_key.asc(), ToolDiscoveryRevision.id.asc())
        .offset(offset).limit(page_size).execution_options(populate_existing=True))
    return [_view(row) for row in rows], total


async def invalidate_tool_source(session: AsyncSession, identity: IdentityContext, *, source_key: str,
    command: DiscoveryInvalidationCommand, correlation_id: str,
) -> DiscoveryView:
    await _administrator(session, identity, lock=True)
    current = await _latest(session, identity.principal.tenant_id, source_key)
    if current is None:
        raise ResourceNotFoundError("工具来源不存在")
    policy = _policy(current)
    _usable(policy)
    if current.policy_digest != command.expected_policy_digest or policy.source_fingerprint != command.source_fingerprint:
        raise ToolDiscoveryConflict("来源连接或批准策略已变化")
    row = await _append(session, identity, source_key=source_key, previous=current, policy=policy_payload(policy),
        event=command.reason, status="pending", correlation_id=correlation_id)
    return _view(row)


async def record_tool_catalog(session: AsyncSession, identity: IdentityContext, *, source_key: str,
    command: DiscoveryCatalogCommand, correlation_id: str,
) -> DiscoveryView:
    await _administrator(session, identity, lock=True)
    current = await _latest(session, identity.principal.tenant_id, source_key)
    if current is None:
        raise ResourceNotFoundError("工具来源不存在")
    policy = _policy(current)
    _usable(policy)
    if (current.revision != command.expected_revision or current.status != "pending"
        or current.policy_digest != command.expected_policy_digest or policy.source_fingerprint != command.source_fingerprint):
        raise ToolDiscoveryConflict("发现围栏或来源批准已变化，须重新读取；不能沿用旧目录")
    failure, payload, observed_count = None, None, 0
    try:
        catalog = parse_discovery_catalog(command.catalog_json)
        observed_count = len(catalog.tools)
        allowed = {tool.name: tool.descriptor_digest for tool in policy.allowed_tools}
        bindings = [_binding(current.tenant_id, source_key, tool) for tool in sorted(catalog.tools, key=lambda tool: tool.name)
            if tool.name in allowed]
        if len(bindings) != len(allowed) or any(tool.descriptor_digest != allowed[tool.remote_name] for tool in bindings):
            raise ValueError()
        snapshot = DiscoverySnapshot(tenant_id=current.tenant_id, source_key=source_key,
            policy_digest=current.policy_digest, base_revision=current.revision, tools=bindings)
        payload = snapshot.model_dump(mode="json")
        encoded = canonical_json(payload)
        if len(encoded.encode("utf-8")) > MAX_CATALOG_BYTES or redact_sensitive_text(encoded) != encoded:
            raise ValueError()
    except Exception:
        failure, payload = "TOOL_DISCOVERY_CATALOG_REJECTED", None
    row = await _append(session, identity, source_key=source_key, previous=current, policy=policy_payload(policy),
        event="catalog_rejected" if failure else "catalog_recorded", status="rejected" if failure else "ready",
        correlation_id=correlation_id, catalog=payload, failure_code=failure, observed_count=observed_count)
    return _view(row)


async def resolve_discovered_tool(session: AsyncSession, identity: IdentityContext, *, source_key: str,
    record_id: UUID, catalog_digest: str, tool_id: str,
) -> DiscoveredTool:
    """控制面解析固定描述；仍不授予网关、目标系统或Agent执行许可。"""
    await _administrator(session, identity, lock=False)
    row = await _latest(session, identity.principal.tenant_id, source_key)
    if row is None or row.id != record_id or row.status != "ready" or row.catalog_digest != catalog_digest:
        raise ToolDiscoveryConflict("固定工具目录已失效，必须重新验证")
    policy = _policy(row)
    _usable(policy)
    from supportops_core.tool_connection_state import latest_connection
    connection = await latest_connection(session, row)
    if connection is not None and connection.circuit_state != "closed":
        raise ToolDiscoveryConflict("来源已熔断或正在恢复探测，固定定义暂不可解析")
    snapshot = _snapshot(row, policy)
    tool = next((tool for tool in snapshot.tools if tool.tool_id == tool_id), None)
    if tool is None:
        raise ResourceNotFoundError("获批工具不存在")
    return tool.descriptor
