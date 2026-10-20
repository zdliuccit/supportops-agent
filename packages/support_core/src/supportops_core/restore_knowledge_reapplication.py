"""在恢复屏障内重施原知识快照隔离；仅两列受控更新，提交后用独立只读身份复核。"""

import asyncio
import hashlib
import json
from datetime import UTC, datetime
from typing import Literal, Protocol
from uuid import uuid4

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from supportops_core.models import KnowledgeIndexGeneration, KnowledgeSnapshot
from supportops_core.reference_history_schema import verify_reference_history_guards
from supportops_core.restore_knowledge_contracts import RestoreKnowledgeAudit, RestoreKnowledgeReceipt, RestoreKnowledgeRequest
from supportops_core.restore_startup_barrier import RestoreStartupBlocked, _read_context, assert_service_start_allowed
from supportops_core.retention_batch_contracts import batch_digest
from supportops_core.retention_knowledge_propagation import MAX_CHUNKS, MAX_GENERATIONS, PostgresKnowledgePropagationVerifier, _READ_COLUMNS
from supportops_core.retention_propagation_contracts import PropagationObservation, PropagationRequest
from supportops_core.schema_structure import MIGRATION_LOCK_KEY, require_public_schema_context


class RestoreKnowledgeAuthority(Protocol):
    """独立确认原账本/当前代次/事实成员、批准/法律保留/共享范围和恢复目标。

    apply须确认旧执行者已停止、外部访问隔离且共享快照整体隔离已独立获批；
    个人申请或主体关联本身不能授权关停共享知识。verify仅允许读，不能继承写许可。
    两个引擎须由可信集成绑定到同一获批恢复实例，库名相同本身不证明实例相同。
    """

    authority_id: str

    async def assert_current(self, request: RestoreKnowledgeRequest, *, purpose: Literal["apply", "verify"]) -> None: ...


class RestoreKnowledgeAuditSink(Protocol):
    async def record(self, event: RestoreKnowledgeAudit) -> None:
        """独立持久提交；首个quarantine_requested须按request_id唯一领取且冻结request_digest。

        已有首事件必须拒绝重新领取，不因提交未知、超时或失败清除意图；后续事件仅追加。
        不得以临时内存冒充持久审计，不自动重放未知提交。
        """
        ...


class RestoreKnowledgeUnconfirmed(RuntimeError):
    code = "RESTORE_KNOWLEDGE_REAPPLICATION_UNCONFIRMED"


def _context(request: RestoreKnowledgeRequest) -> None:
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
        or marker["target_alias"] != request.target_alias or marker["ledger_id"] != str(request.propagation.ledger_id)
        or marker["ledger_generation"] != request.propagation.ledger_generation
        or marker["ledger_snapshot_sha256"] != request.ledger_snapshot_digest):
        raise ValueError()


async def _authorize(request: RestoreKnowledgeRequest, authority: RestoreKnowledgeAuthority,
    purpose: Literal["apply", "verify"],
) -> None:
    _context(request)
    if authority.authority_id != request.propagation.authority_id:
        raise ValueError()
    async with asyncio.timeout(0.5):
        await authority.assert_current(request, purpose=purpose)
    _context(request)


async def _audit(request: RestoreKnowledgeRequest, sink: RestoreKnowledgeAuditSink,
    event: Literal["quarantine_requested", "commit_observed", "quarantine_verified",
        "target_not_observed", "quarantine_not_confirmed"],
    observation: PropagationObservation | None = None,
) -> None:
    _context(request)
    message = RestoreKnowledgeAudit(event_id=uuid4(), restore_id=request.restore_id, request_id=request.request_id,
        request_digest=batch_digest(request), fact_digest=request.propagation.fact.record_digest,
        resource_key_digest=request.propagation.fact.resource_key_digest, event=event,
        observation_digest=batch_digest(observation) if observation is not None else None,
        evidence_digest=observation.evidence_digest if observation is not None else None, observed_at=datetime.now(UTC))
    async with asyncio.timeout(0.5):
        await sink.record(message)
    _context(request)


async def _write_database(connection: AsyncConnection, request: RestoreKnowledgeRequest) -> None:
    await connection.execute(text("SET TRANSACTION ISOLATION LEVEL READ COMMITTED, READ WRITE"))
    await connection.run_sync(require_public_schema_context)
    await connection.execute(text("SELECT set_config('statement_timeout','1000ms',true), "
        "set_config('lock_timeout','250ms',true)"))
    if await connection.scalar(text("SELECT pg_catalog.pg_try_advisory_xact_lock_shared(:key)"),
        {"key": MIGRATION_LOCK_KEY}) is not True:
        raise ValueError()
    rows = (await connection.execute(text("""
        SELECT current_database() AS db,current_user AS role,session_user AS original_role,
            current_schemas(true) AS schemas,current_setting('session_replication_role') AS replication,
            current_setting('transaction_read_only') AS readonly,
            r.rolsuper,r.rolbypassrls,r.rolcreaterole,r.rolcreatedb,
            c.relname,c.relkind,c.relrowsecurity,c.relforcerowsecurity,c.relowner=r.oid AS owns_table,
            has_schema_privilege(current_user,n.oid,'CREATE') AS can_ddl,
            has_table_privilege(current_user,c.oid,'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER') AS broad_write,
            has_any_column_privilege(current_user,c.oid,'INSERT,REFERENCES') AS other_write,
            a.attname,has_column_privilege(current_user,c.oid,a.attnum,'SELECT') AS can_read,
            has_column_privilege(current_user,c.oid,a.attnum,'UPDATE') AS can_update
        FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
        JOIN pg_catalog.pg_roles r ON r.rolname=current_user
        JOIN pg_catalog.pg_attribute a ON a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped
        WHERE n.nspname='public' AND c.relname IN ('knowledge_snapshots','knowledge_index_generations')
        ORDER BY c.relname,a.attnum LIMIT 65
    """))).mappings().all()
    tables = {"knowledge_snapshots": "deleted_at", "knowledge_index_generations": "status"}
    if len(rows) > 64 or {row["relname"] for row in rows} != set(tables):
        raise ValueError()
    actual: dict[str, set[str]] = {name: set() for name in tables}
    for row in rows:
        actual[row["relname"]].add(row["attname"])
        if (row["db"] != request.write_database.expected_database
            or row["role"] != request.write_database.expected_maintenance_role
            or row["original_role"] != request.write_database.expected_maintenance_role
            or list(row["schemas"]) != ["pg_catalog", "public"] or row["replication"] != "origin"
            or row["readonly"] != "off" or row["relkind"] != "r"
            or row["can_read"] is not (row["attname"] in _READ_COLUMNS[row["relname"]])
            or row["can_update"] is not (row["attname"] == tables[row["relname"]])
            or any(row[name] for name in ("rolsuper", "rolbypassrls", "rolcreaterole", "rolcreatedb", "owns_table",
                "relrowsecurity", "relforcerowsecurity", "can_ddl", "broad_write", "other_write"))):
            raise ValueError()
    if any(not (_READ_COLUMNS[name] | {tables[name]}) <= columns for name, columns in actual.items()):
        raise ValueError()
    excess = await connection.scalar(text("""
        SELECT EXISTS(SELECT 1 FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname='public' AND c.relkind IN ('r','p','v','m','f')
            AND c.relname NOT IN ('knowledge_snapshots','knowledge_index_generations')
            AND (has_table_privilege(current_user,c.oid,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
                OR has_any_column_privilege(current_user,c.oid,'SELECT,INSERT,UPDATE,REFERENCES')))
            OR EXISTS(SELECT 1 FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
                WHERE n.nspname='public' AND p.proname IN ('supportops_guard_reference_history',
                    'supportops_guard_knowledge_reference_chain','supportops_reject_reference_history_truncate')
                AND p.proowner=(SELECT oid FROM pg_catalog.pg_roles WHERE rolname=current_user))
    """))
    if excess is not False:
        raise ValueError()
    await connection.run_sync(lambda sync: verify_reference_history_guards(sync, require_read_only=False))


async def _apply(connection: AsyncConnection, request: RestoreKnowledgeRequest, authority: RestoreKnowledgeAuthority) -> None:
    binding, fact = request.binding, request.propagation.fact
    snapshot, generation = KnowledgeSnapshot.__table__, KnowledgeIndexGeneration.__table__
    row = (await connection.execute(select(*[snapshot.c[name] for name in sorted(_READ_COLUMNS[snapshot.name])])
        .where(snapshot.c.id == binding.locator.resource_id).with_for_update())).mappings().one_or_none()
    if row is None:
        return
    if (row["tenant_id"] != binding.locator.tenant_id or row["document_id"] != binding.document_id
        or row["version_id"] != binding.version_id or row["content_digest"] != binding.content_digest
        or not isinstance(row["created_at"], datetime) or row["created_at"].tzinfo is None
        or row["created_at"] > fact.recorded_at
        or row["deleted_at"] is not None and (not isinstance(row["deleted_at"], datetime)
            or row["deleted_at"].tzinfo is None or not row["created_at"] <= row["deleted_at"] <= datetime.now(UTC))):
        raise ValueError()
    generations = (await connection.execute(select(*[generation.c[name]
        for name in sorted(_READ_COLUMNS[generation.name])]).where(generation.c.snapshot_id == binding.locator.resource_id)
        .order_by(generation.c.id).limit(MAX_GENERATIONS + 1).with_for_update())).mappings().all()
    if len(generations) > MAX_GENERATIONS:
        raise ValueError()
    for item in generations:
        if (item["tenant_id"] != binding.locator.tenant_id or item["document_id"] != binding.document_id
            or item["content_digest"] != binding.content_digest or item["algorithm_version"] != "keyword_v1"
            or item["status"] not in {"pending", "ready", "invalidated", "failed"}
            or type(item["generation_number"]) is not int or item["generation_number"] < 1
            or type(item["chunk_count"]) is not int or not 0 <= item["chunk_count"] <= MAX_CHUNKS):
            raise ValueError()
    if sum(item["chunk_count"] for item in generations) > MAX_CHUNKS:
        raise ValueError()
    await _authorize(request, authority, "apply")
    if row["deleted_at"] is None:
        result = await connection.execute(update(snapshot).where(snapshot.c.id == binding.locator.resource_id,
            snapshot.c.tenant_id == binding.locator.tenant_id, snapshot.c.document_id == binding.document_id,
            snapshot.c.version_id == binding.version_id, snapshot.c.content_digest == binding.content_digest,
            snapshot.c.deleted_at.is_(None)).values(deleted_at=datetime.now(UTC)))
        if result.rowcount != 1:
            raise ValueError()
    ids = [item["id"] for item in generations if item["status"] != "invalidated"]
    if ids:
        await _authorize(request, authority, "apply")
        result = await connection.execute(update(generation).where(generation.c.id.in_(ids),
            generation.c.tenant_id == binding.locator.tenant_id, generation.c.document_id == binding.document_id,
            generation.c.snapshot_id == binding.locator.resource_id, generation.c.content_digest == binding.content_digest,
            generation.c.algorithm_version == "keyword_v1", generation.c.status.in_(["pending", "ready", "failed"]))
            .values(status="invalidated"))
        if result.rowcount != len(ids):
            raise ValueError()


class _ReadAuthority:
    def __init__(self, request: RestoreKnowledgeRequest, authority: RestoreKnowledgeAuthority) -> None:
        self._request, self._authority = request, authority
        self.authority_id = request.propagation.authority_id

    async def assert_current(self, request: PropagationRequest) -> None:
        if batch_digest(request) != batch_digest(self._request.propagation):
            raise ValueError()
        await _authorize(self._request, self._authority, "verify")


async def _verify(engine: AsyncEngine, request: RestoreKnowledgeRequest, authority: RestoreKnowledgeAuthority,
    sink: RestoreKnowledgeAuditSink,
) -> RestoreKnowledgeReceipt:
    await _authorize(request, authority, "verify")
    verifier = PostgresKnowledgePropagationVerifier(engine, request.verification_database, request.binding,
        authority=_ReadAuthority(request, authority))
    observation = await verifier.observe(request.propagation, request.propagation.targets[0])
    await _authorize(request, authority, "verify")
    status: Literal["quarantined", "not_observed", "needs_attention"]
    if observation.state == "quarantined":
        await _audit(request, sink, "quarantine_verified", observation)
        status = "quarantined"
    elif observation.state == "unknown":
        await _audit(request, sink, "target_not_observed", observation)
        status = "not_observed"
    else:
        await _audit(request, sink, "quarantine_not_confirmed", observation)
        status = "needs_attention"
    await _authorize(request, authority, "verify")
    return RestoreKnowledgeReceipt(request_id=request.request_id, request_digest=batch_digest(request), status=status,
        observation_digest=batch_digest(observation), evidence_digest=observation.evidence_digest,
        observed_at=observation.observed_at, snapshot_read_quarantine_confirmed=status == "quarantined")


async def reapply_restored_knowledge_quarantine(engine: AsyncEngine, verification_engine: AsyncEngine,
    request: RestoreKnowledgeRequest, *, authority: RestoreKnowledgeAuthority | None = None,
    audit_sink: RestoreKnowledgeAuditSink | None = None,
) -> RestoreKnowledgeReceipt:
    """当前独立许可和唯一持久意图后只隔离原快照/代次；未知仅可显式只读核对。"""
    try:
        if (not isinstance(engine, AsyncEngine) or engine.dialect.name != "postgresql"
            or not isinstance(verification_engine, AsyncEngine) or verification_engine.dialect.name != "postgresql"
            or engine is verification_engine or not isinstance(request, RestoreKnowledgeRequest)
            or authority is None or audit_sink is None):
            raise ValueError()
        deadline = asyncio.get_running_loop().time() + 8
        async with asyncio.timeout(8):
            await _authorize(request, authority, "apply")
            await _audit(request, audit_sink, "quarantine_requested")
            await _authorize(request, authority, "apply")
            async with engine.connect() as connection, connection.begin():
                await _write_database(connection, request)
                await _apply(connection, request, authority)
                await _authorize(request, authority, "apply")
            await _audit(request, audit_sink, "commit_observed")
            receipt = await _verify(verification_engine, request, authority, audit_sink)
        if asyncio.get_running_loop().time() > deadline:
            raise ValueError()
        return receipt
    except Exception:
        raise RestoreKnowledgeUnconfirmed() from None


async def verify_restored_knowledge_quarantine(verification_engine: AsyncEngine, request: RestoreKnowledgeRequest, *,
    authority: RestoreKnowledgeAuthority | None = None, audit_sink: RestoreKnowledgeAuditSink | None = None,
) -> RestoreKnowledgeReceipt:
    """没有写引擎的独立只读复核入口；原有效请求及新的当前verify许可仍必需。"""
    try:
        if (not isinstance(verification_engine, AsyncEngine) or verification_engine.dialect.name != "postgresql"
            or not isinstance(request, RestoreKnowledgeRequest) or authority is None or audit_sink is None):
            raise ValueError()
        deadline = asyncio.get_running_loop().time() + 5
        async with asyncio.timeout(5):
            receipt = await _verify(verification_engine, request, authority, audit_sink)
        if asyncio.get_running_loop().time() > deadline:
            raise ValueError()
        return receipt
    except Exception:
        raise RestoreKnowledgeUnconfirmed() from None
