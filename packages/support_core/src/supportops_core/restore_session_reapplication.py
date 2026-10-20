"""独立维护许可下仅重施登录会话隔离；不删除数据、不生成tombstone或解除恢复屏障。"""

import asyncio
import hashlib
import json
from datetime import UTC, datetime
from typing import Literal, Protocol
from uuid import uuid4

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from supportops_core.models import AuthSession
from supportops_core.restore_session_contracts import (
    RestoreSessionAudit, RestoreSessionReceipt, RestoreSessionRequest, restore_session_digest,
)
from supportops_core.restore_session_history import require_restore_session_history_guard
from supportops_core.restore_startup_barrier import (
    RestoreStartupBlocked, _read_context, assert_service_start_allowed,
)


class RestoreSessionAuthority(Protocol):
    """未来独立维护服务必须复检原账本真实性/最新代次、事实成员、目标范围与当前许可。"""

    authority_id: str

    async def assert_current(self, request: RestoreSessionRequest, *, purpose: Literal["apply", "verify"]) -> None: ...


class RestoreSessionAuditSink(Protocol):
    """必须独立持久化事件后返回；缺失/失败不继续，未知审计不自动重放。"""

    async def record(self, event: RestoreSessionAudit) -> None: ...


class RestoreSessionUnconfirmed(RuntimeError):
    code = "RESTORE_SESSION_REAPPLICATION_UNCONFIRMED"


def _context(request: RestoreSessionRequest) -> None:
    now = datetime.now(UTC)
    if not request.created_at <= now < request.expires_at:
        raise ValueError()
    try:
        assert_service_start_allowed(mode="quarantined", expected_restore_id=request.restore_id,
            expected_context_digest=request.context_digest)
    except RestoreStartupBlocked as exc:
        if exc.code != "RESTORE_QUARANTINE_ACTIVE":
            raise ValueError() from None
    else:
        raise ValueError()
    # 复用受保护读取，不改变已有屏障；再次绑定字节以防两次观察间标记变化。
    raw = _read_context()
    if hashlib.sha256(raw).hexdigest() != request.context_digest:
        raise ValueError()
    marker = json.loads(raw.decode("utf-8"))
    if (marker["restore_id"] != str(request.restore_id) or marker["backup_id"] != request.backup_id
        or marker["target_alias"] != request.target_alias or marker["ledger_id"] != str(request.ledger_id)
        or marker["ledger_generation"] != request.ledger_generation
        or marker["ledger_snapshot_sha256"] != request.ledger_snapshot_digest):
        raise ValueError()


async def _authorize(request: RestoreSessionRequest, authority: RestoreSessionAuthority,
    purpose: Literal["apply", "verify"],
) -> None:
    _context(request)
    if authority.authority_id != request.authority_id:
        raise ValueError()
    async with asyncio.timeout(0.5):
        await authority.assert_current(request, purpose=purpose)
    _context(request)


async def _audit(request: RestoreSessionRequest, sink: RestoreSessionAuditSink,
    event: Literal["quarantine_requested", "commit_observed", "quarantine_verified", "target_not_observed"],
) -> None:
    message = RestoreSessionAudit(event_id=uuid4(), restore_id=request.restore_id, request_id=request.request_id,
        request_digest=restore_session_digest(request), fact_digest=request.fact.record_digest,
        resource_key_digest=request.fact.resource_key_digest, event=event, observed_at=datetime.now(UTC))
    async with asyncio.timeout(0.5):
        await sink.record(message)


async def _database(connection: AsyncConnection, request: RestoreSessionRequest, *, readonly: bool) -> None:
    # 仅固定SQL与参数化预期，不插值库名、角色、表名或目标ID，不改变角色/复制模式/触发器。
    await connection.execute(text("SET TRANSACTION ISOLATION LEVEL READ COMMITTED, READ ONLY"
        if readonly else "SET TRANSACTION ISOLATION LEVEL READ COMMITTED, READ WRITE"))
    await connection.execute(text("SELECT set_config('statement_timeout', '1000ms', true), "
        "set_config('lock_timeout', '250ms', true)"))
    state = (await connection.execute(text("""
        SELECT current_database() AS database_name, current_user AS role_name, session_user AS session_role,
            current_schemas(true) AS schemas, current_setting('session_replication_role') AS replication_role,
            current_setting('transaction_isolation') AS isolation,
            current_setting('transaction_read_only') AS read_only,
            r.rolsuper, r.rolbypassrls, r.rolcreaterole, r.rolcreatedb,
            c.relrowsecurity, c.relforcerowsecurity, c.relkind, c.relowner=r.oid AS is_owner,
            has_table_privilege(current_user,c.oid,'SELECT') AS can_select,
            (has_table_privilege(current_user,c.oid,'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
                OR has_any_column_privilege(current_user,c.oid,'INSERT,REFERENCES')) AS broad_write,
            has_column_privilege(current_user,c.oid,'revoked_at','UPDATE') AS can_revoke_time,
            has_column_privilege(current_user,c.oid,'revocation_code','UPDATE') AS can_revoke_code,
            EXISTS(SELECT 1 FROM pg_catalog.pg_attribute a WHERE a.attrelid=c.oid
                AND a.attnum>0 AND NOT a.attisdropped AND a.attname NOT IN ('revoked_at','revocation_code')
                AND has_column_privilege(current_user,c.oid,a.attnum,'UPDATE')) AS other_column_write
        FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
        JOIN pg_catalog.pg_roles r ON r.rolname=current_user
        WHERE n.nspname='public' AND c.relname='auth_sessions'
    """))).mappings().one()
    if (state["database_name"] != request.expected_database or state["role_name"] != request.expected_maintenance_role
        or state["session_role"] != request.expected_maintenance_role
        or list(state["schemas"]) != ["pg_catalog", "public"] or state["replication_role"] != "origin"
        or state["isolation"] != "read committed" or state["read_only"] != ("on" if readonly else "off")
        or state["relkind"] != "r" or not state["can_select"]
        or not state["can_revoke_time"] or not state["can_revoke_code"]
        or any(state[name] for name in ("rolsuper", "rolbypassrls", "rolcreaterole", "rolcreatedb", "is_owner",
            "relrowsecurity", "relforcerowsecurity", "broad_write", "other_column_write"))):
        raise ValueError()
    await require_restore_session_history_guard(connection)


async def _target(connection: AsyncConnection, request: RestoreSessionRequest, *, lock: bool) -> dict[str, object] | None:
    table = AuthSession.__table__
    query = select(table.c.id, table.c.tenant_id, table.c.user_id, table.c.created_at,
        table.c.revoked_at, table.c.revoked_by, table.c.revocation_code).where(table.c.id == request.locator.resource_id)
    if lock:
        query = query.with_for_update()
    row = (await connection.execute(query)).mappings().one_or_none()
    if row is None:
        return None
    if (row["tenant_id"] != request.locator.tenant_id or row["user_id"] != request.locator.subject_user_id
        or not isinstance(row["created_at"], datetime) or row["created_at"].tzinfo is None
        or row["created_at"] > request.fact.recorded_at):
        raise ValueError()
    revoked_at = row["revoked_at"]
    if revoked_at is None:
        if row["revoked_by"] is not None or row["revocation_code"] is not None:
            raise ValueError()
    elif not isinstance(revoked_at, datetime) or revoked_at.tzinfo is None or not row["created_at"] <= revoked_at <= datetime.now(UTC):
        raise ValueError()
    return dict(row)


async def _verify(engine: AsyncEngine, request: RestoreSessionRequest, authority: RestoreSessionAuthority,
    sink: RestoreSessionAuditSink, *, already: bool,
) -> RestoreSessionReceipt:
    await _authorize(request, authority, "verify")
    # 写事务结束后另开只读事务，不能把UPDATE行数或旧实体状态当作复核结果。
    async with engine.connect() as connection, connection.begin():
        await _database(connection, request, readonly=True)
        row = await _target(connection, request, lock=False)
    observed_at = datetime.now(UTC)
    await _authorize(request, authority, "verify")
    if row is None:
        await _audit(request, sink, "target_not_observed")
        await _authorize(request, authority, "verify")
        return RestoreSessionReceipt(request_id=request.request_id, request_digest=restore_session_digest(request),
            status="not_observed", revoked_at=None, observed_at=observed_at, access_revocation_confirmed=False)
    revoked_at = row["revoked_at"]
    if not isinstance(revoked_at, datetime):
        raise ValueError()
    await _audit(request, sink, "quarantine_verified")
    await _authorize(request, authority, "verify")
    return RestoreSessionReceipt(request_id=request.request_id, request_digest=restore_session_digest(request),
        status="already_quarantined" if already else "quarantined", revoked_at=revoked_at,
        observed_at=observed_at, access_revocation_confirmed=True)


async def reapply_restored_session_quarantine(engine: AsyncEngine, request: RestoreSessionRequest, *,
    authority: RestoreSessionAuthority | None = None, audit_sink: RestoreSessionAuditSink | None = None,
) -> RestoreSessionReceipt:
    """只对精确原会话首次撤销；没有独立当前许可/持久审计则不读目标、不写数据库。"""
    try:
        if (not isinstance(engine, AsyncEngine) or engine.dialect.name != "postgresql"
            or not isinstance(request, RestoreSessionRequest) or authority is None or audit_sink is None):
            raise ValueError()
        async with asyncio.timeout(8):
            await _authorize(request, authority, "apply")
            await _audit(request, audit_sink, "quarantine_requested")
            await _authorize(request, authority, "apply")
            async with engine.connect() as connection, connection.begin():
                await _database(connection, request, readonly=False)
                row = await _target(connection, request, lock=True)
                already = row is not None and row["revoked_at"] is not None
                if row is not None and not already:
                    await _authorize(request, authority, "apply")
                    table = AuthSession.__table__
                    result = await connection.execute(update(table).where(table.c.id == request.locator.resource_id,
                        table.c.tenant_id == request.locator.tenant_id, table.c.user_id == request.locator.subject_user_id,
                        table.c.revoked_at.is_(None), table.c.revoked_by.is_(None), table.c.revocation_code.is_(None))
                        .values(revoked_at=datetime.now(UTC), revocation_code="RESTORE_TOMBSTONE_QUARANTINE"))
                    if result.rowcount != 1:
                        raise ValueError()
                await _authorize(request, authority, "apply")
            # 提交未知或审计失败不重试，原标记始终保留；不能推断没有撤销。
            await _audit(request, audit_sink, "commit_observed")
            return await _verify(engine, request, authority, audit_sink, already=already)
    except Exception:
        raise RestoreSessionUnconfirmed() from None


async def verify_restored_session_quarantine(engine: AsyncEngine, request: RestoreSessionRequest, *,
    authority: RestoreSessionAuthority | None = None, audit_sink: RestoreSessionAuditSink | None = None,
) -> RestoreSessionReceipt:
    """未知结果只作显式只读核对；仍需当前独立许可、原有效窗口和原固定请求。"""
    try:
        if (not isinstance(engine, AsyncEngine) or engine.dialect.name != "postgresql"
            or not isinstance(request, RestoreSessionRequest) or authority is None or audit_sink is None):
            raise ValueError()
        async with asyncio.timeout(5):
            return await _verify(engine, request, authority, audit_sink, already=True)
    except Exception:
        raise RestoreSessionUnconfirmed() from None
