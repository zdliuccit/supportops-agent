"""恢复屏障内隔离指定凭据修订；仅首次写deleted_at，再由独立只读身份复核。"""

import asyncio
import hashlib
import json
from datetime import UTC, datetime
from typing import Literal, Protocol
from uuid import uuid4

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from supportops_core.models import ModelCredentialRevision
from supportops_core.reference_history_schema import verify_reference_history_guards
from supportops_core.restore_credential_contracts import (
    RestoreCredentialAudit, RestoreCredentialEvent, RestoreCredentialObservation, RestoreCredentialReceipt, RestoreCredentialRequest,
)
from supportops_core.restore_startup_barrier import RestoreStartupBlocked, _read_context, assert_service_start_allowed
from supportops_core.retention_batch_contracts import batch_digest
from supportops_core.retention_tombstone_snapshot import _canonical
from supportops_core.schema_structure import MIGRATION_LOCK_KEY, require_public_schema_context

_READ_COLUMNS = frozenset({"id", "tenant_id", "credential_id", "revision", "provider", "created_at", "deleted_at"})
_EVENTS: dict[Literal["quarantined", "unknown", "present"], RestoreCredentialEvent] = {
    "quarantined": "quarantine_verified", "unknown": "target_not_observed", "present": "quarantine_not_confirmed",
}


class RestoreCredentialAuthority(Protocol):
    """独立确认原事实实际账本成员/当前代次、批准/法律保留及共享修订全部引用范围。

    apply须确认旧API/Worker/探测者停止、网络隔离；个人关联不授予共享凭据写权。
    可信集成须确认读写引擎指向同一批准恢复实例及实际解封路径，不能只凭库名。
    verify仅授予读取；provider端秘密和其他副本/进程内密钥须另行隔离和核对。
    """

    authority_id: str

    async def assert_current(self, request: RestoreCredentialRequest, *, purpose: Literal["apply", "verify"]) -> None: ...


class RestoreCredentialAuditSink(Protocol):
    async def record(self, event: RestoreCredentialAudit) -> None:
        """独立持久追加；按request_id唯一领取意图并固定原request_digest。

        已有任何历史后拒绝重新领取写意图，提交未知/失败不清除、不重放。
        后续只追加提交观察及当前只读结果；临时内存和其他资源审计不能代替。
        """
        ...


class RestoreCredentialUnconfirmed(RuntimeError):
    code = "RESTORE_CREDENTIAL_REAPPLICATION_UNCONFIRMED"


def _context(request: RestoreCredentialRequest) -> None:
    if not request.created_at <= datetime.now(UTC) < request.expires_at:
        raise ValueError()
    try:
        assert_service_start_allowed(mode="quarantined", expected_restore_id=request.restore_id,
            expected_context_digest=request.context_digest)
    except RestoreStartupBlocked as error:
        if error.code != "RESTORE_QUARANTINE_ACTIVE":
            raise ValueError() from None
    else:
        raise ValueError()
    raw = _read_context()
    if hashlib.sha256(raw).hexdigest() != request.context_digest:
        raise ValueError()
    marker = json.loads(raw.decode("utf-8"))
    if (marker["restore_id"] != str(request.restore_id) or marker["backup_id"] != request.backup_id
        or marker["target_alias"] != request.target_alias or marker["ledger_id"] != str(request.ledger_id)
        or marker["ledger_generation"] != request.ledger_generation
        or marker["ledger_snapshot_sha256"] != request.ledger_snapshot_digest):
        raise ValueError()


async def _authorize(request: RestoreCredentialRequest, authority: RestoreCredentialAuthority,
    purpose: Literal["apply", "verify"],
) -> None:
    _context(request)
    if authority.authority_id != request.authority_id:
        raise ValueError()
    async with asyncio.timeout(0.5):
        await authority.assert_current(request, purpose=purpose)
    _context(request)


async def _audit(request: RestoreCredentialRequest, sink: RestoreCredentialAuditSink,
    event: RestoreCredentialEvent, observation: RestoreCredentialObservation | None = None,
) -> None:
    _context(request)
    message = RestoreCredentialAudit(event_id=uuid4(), restore_id=request.restore_id, request_id=request.request_id,
        request_digest=batch_digest(request), fact_digest=request.fact.record_digest,
        resource_key_digest=request.fact.resource_key_digest, event=event,
        observation_digest=batch_digest(observation) if observation is not None else None,
        evidence_digest=observation.evidence_digest if observation is not None else None, observed_at=datetime.now(UTC))
    async with asyncio.timeout(0.5):
        await sink.record(message)
    _context(request)


async def _database(connection: AsyncConnection, request: RestoreCredentialRequest, *, write: bool) -> None:
    await connection.execute(text("SET TRANSACTION ISOLATION LEVEL READ COMMITTED, READ WRITE" if write
        else "SET TRANSACTION ISOLATION LEVEL READ COMMITTED, READ ONLY"))
    await connection.run_sync(require_public_schema_context)
    await connection.execute(text("SELECT pg_catalog.set_config('statement_timeout',:statement,true), "
        "pg_catalog.set_config('lock_timeout',:lock,true)"),
        {"statement": "1000ms" if write else "100ms", "lock": "250ms" if write else "50ms"})
    if await connection.scalar(text("SELECT pg_catalog.pg_try_advisory_xact_lock_shared(:key)"),
        {"key": MIGRATION_LOCK_KEY}) is not True:
        raise ValueError()
    scope = request.write_database if write else request.verification_database
    rows = (await connection.execute(text("""
        SELECT current_database() AS db,current_user AS role,session_user AS original_role,
            current_setting('transaction_read_only') AS readonly,
            r.rolsuper,r.rolbypassrls,r.rolcreaterole,r.rolcreatedb,
            c.relkind,c.relrowsecurity,c.relforcerowsecurity,c.relowner=r.oid AS owns_table,
            has_schema_privilege(current_user,n.oid,'CREATE') AS can_ddl,
            has_table_privilege(current_user,c.oid,'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER') AS broad_write,
            has_any_column_privilege(current_user,c.oid,'INSERT,REFERENCES') AS other_write,
            a.attname,has_column_privilege(current_user,c.oid,a.attnum,'SELECT') AS can_read,
            has_column_privilege(current_user,c.oid,a.attnum,'UPDATE') AS can_update
        FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
        JOIN pg_catalog.pg_roles r ON r.rolname=current_user
        JOIN pg_catalog.pg_attribute a ON a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped
        WHERE n.nspname='public' AND c.relname='model_credential_revisions'
        ORDER BY a.attnum LIMIT 65
    """))).mappings().all()
    if not rows or len(rows) > 64 or not _READ_COLUMNS <= {row["attname"] for row in rows}:
        raise ValueError()
    for row in rows:
        if (row["db"] != scope.expected_database or row["role"] != scope.expected_maintenance_role
            or row["original_role"] != scope.expected_maintenance_role
            or row["readonly"] != ("off" if write else "on") or row["relkind"] != "r"
            or row["can_read"] is not (row["attname"] in _READ_COLUMNS)
            or row["can_update"] is not (write and row["attname"] == "deleted_at")
            or any(row[name] for name in ("rolsuper", "rolbypassrls", "rolcreaterole", "rolcreatedb", "owns_table",
                "relrowsecurity", "relforcerowsecurity", "can_ddl", "broad_write", "other_write"))):
            raise ValueError()
    excess = await connection.scalar(text("""
        SELECT EXISTS(SELECT 1 FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname='public' AND c.relkind IN ('r','p','v','m','f')
            AND c.relname<>'model_credential_revisions'
            AND (has_table_privilege(current_user,c.oid,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
                OR has_any_column_privilege(current_user,c.oid,'SELECT,INSERT,UPDATE,REFERENCES')))
            OR EXISTS(SELECT 1 FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
                WHERE n.nspname='public' AND p.proname IN ('supportops_guard_reference_history',
                    'supportops_guard_knowledge_reference_chain','supportops_reject_reference_history_truncate')
                AND p.proowner=(SELECT oid FROM pg_catalog.pg_roles WHERE rolname=current_user))
    """))
    if excess is not False:
        raise ValueError()
    await connection.run_sync(lambda sync: verify_reference_history_guards(sync, require_read_only=not write))


async def _target(connection: AsyncConnection, request: RestoreCredentialRequest, *, lock: bool,
) -> tuple[datetime | None, datetime] | None:
    table = ModelCredentialRevision.__table__
    query = select(*[table.c[name] for name in sorted(_READ_COLUMNS)]).where(
        table.c.id == request.locator.resource_id, table.c.tenant_id == request.locator.tenant_id)
    if lock:
        query = query.with_for_update()
    row = (await connection.execute(query)).mappings().one_or_none()
    observed_at = datetime.now(UTC)
    if row is None:
        return None
    created_at, deleted_at = row["created_at"], row["deleted_at"]
    if (row["tenant_id"] != request.locator.tenant_id or row["credential_id"] != request.credential_id
        or row["revision"] != request.credential_revision or row["provider"] != request.provider
        or not isinstance(created_at, datetime) or created_at.tzinfo is None or created_at > request.fact.recorded_at
        or deleted_at is not None and (not isinstance(deleted_at, datetime) or deleted_at.tzinfo is None
            or not created_at <= deleted_at <= observed_at)):
        raise ValueError()
    return deleted_at, observed_at


async def _apply(connection: AsyncConnection, request: RestoreCredentialRequest, authority: RestoreCredentialAuthority) -> None:
    target = await _target(connection, request, lock=True)
    await _authorize(request, authority, "apply")
    if target is None or target[0] is not None:
        return
    table = ModelCredentialRevision.__table__
    result = await connection.execute(update(table).where(table.c.id == request.locator.resource_id,
        table.c.tenant_id == request.locator.tenant_id, table.c.credential_id == request.credential_id,
        table.c.revision == request.credential_revision, table.c.provider == request.provider,
        table.c.deleted_at.is_(None)).values(deleted_at=datetime.now(UTC)))
    if result.rowcount != 1:
        raise ValueError()


async def _verify(engine: AsyncEngine, request: RestoreCredentialRequest, authority: RestoreCredentialAuthority,
    sink: RestoreCredentialAuditSink,
) -> RestoreCredentialReceipt:
    await _authorize(request, authority, "verify")
    async with engine.connect() as connection, connection.begin():
        await _database(connection, request, write=False)
        await _authorize(request, authority, "verify")
        target = await _target(connection, request, lock=False)
        observed_at = target[1] if target is not None else datetime.now(UTC)
    await _authorize(request, authority, "verify")
    state: Literal["quarantined", "present", "unknown"] = ("unknown" if target is None
        else "quarantined" if target[0] is not None else "present")
    proof = {"schema_version": "restore_credential_evidence_v1", "request_digest": batch_digest(request),
        "fact_digest": request.fact.record_digest, "resource_key_digest": request.fact.resource_key_digest,
        "state": state, "deleted_at": target[0].astimezone(UTC).isoformat() if target and target[0] else None,
        "observed_at": observed_at.isoformat(), "provider_secret_revocation_confirmed": False,
        "physical_erasure_verified": False}
    observation = RestoreCredentialObservation(request_digest=batch_digest(request), fact_digest=request.fact.record_digest,
        resource_key_digest=request.fact.resource_key_digest, state=state,
        evidence_digest=hashlib.sha256(_canonical(proof)).hexdigest() if target is not None else None,
        observed_at=observed_at)
    await _audit(request, sink, _EVENTS[state], observation)
    await _authorize(request, authority, "verify")
    status: Literal["quarantined", "not_observed", "needs_attention"] = ("quarantined" if state == "quarantined"
        else "not_observed" if state == "unknown" else "needs_attention")
    return RestoreCredentialReceipt(request_id=request.request_id, request_digest=batch_digest(request), status=status,
        observation_digest=batch_digest(observation), evidence_digest=observation.evidence_digest,
        observed_at=observation.observed_at, fixed_revision_read_quarantine_confirmed=state == "quarantined")


async def reapply_restored_credential_quarantine(engine: AsyncEngine, verification_engine: AsyncEngine,
    request: RestoreCredentialRequest, *, authority: RestoreCredentialAuthority | None = None,
    audit_sink: RestoreCredentialAuditSink | None = None,
) -> RestoreCredentialReceipt:
    """只隔离指定修订，不改父凭据/其他revision/秘密或provider；结果未知不重放。"""
    try:
        if (not isinstance(engine, AsyncEngine) or engine.dialect.name != "postgresql"
            or not isinstance(verification_engine, AsyncEngine) or verification_engine.dialect.name != "postgresql"
            or engine is verification_engine or not isinstance(request, RestoreCredentialRequest)
            or authority is None or audit_sink is None):
            raise ValueError()
        deadline = asyncio.get_running_loop().time() + 8
        async with asyncio.timeout(8):
            await _authorize(request, authority, "apply")
            await _audit(request, audit_sink, "quarantine_requested")
            await _authorize(request, authority, "apply")
            async with engine.connect() as connection, connection.begin():
                await _database(connection, request, write=True)
                await _apply(connection, request, authority)
                await _authorize(request, authority, "apply")
            await _authorize(request, authority, "apply")
            await _audit(request, audit_sink, "commit_observed")
            receipt = await _verify(verification_engine, request, authority, audit_sink)
        if asyncio.get_running_loop().time() > deadline:
            raise ValueError()
        return receipt
    except Exception:
        raise RestoreCredentialUnconfirmed() from None


async def verify_restored_credential_quarantine(verification_engine: AsyncEngine, request: RestoreCredentialRequest, *,
    authority: RestoreCredentialAuthority | None = None, audit_sink: RestoreCredentialAuditSink | None = None,
) -> RestoreCredentialReceipt:
    """原有效请求与当前verify许可下单独只读复核；不接受写引擎，不重放写意图。"""
    try:
        if (not isinstance(verification_engine, AsyncEngine) or verification_engine.dialect.name != "postgresql"
            or not isinstance(request, RestoreCredentialRequest) or authority is None or audit_sink is None):
            raise ValueError()
        deadline = asyncio.get_running_loop().time() + 5
        async with asyncio.timeout(5):
            receipt = await _verify(verification_engine, request, authority, audit_sink)
        if asyncio.get_running_loop().time() > deadline:
            raise ValueError()
        return receipt
    except Exception:
        raise RestoreCredentialUnconfirmed() from None
