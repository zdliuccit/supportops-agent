"""知识关键词索引的独立只读传播观察；不清理分块、不改写快照或审批事实。"""

import asyncio
import hashlib
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from pydantic import Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from supportops_core.reference_history_schema import verify_reference_history_guards
from supportops_core.retention_batch_contracts import Digest, Identifier, RetentionBatchContract, batch_digest
from supportops_core.retention_propagation import PropagationAuthority
from supportops_core.retention_propagation_contracts import PropagationObservation, PropagationRequest, PropagationTarget
from supportops_core.retention_tombstone_snapshot import _canonical
from supportops_core.schema_structure import MIGRATION_LOCK_KEY

VERIFIER_ID = "knowledge_keyword_index_v1"
MAX_GENERATIONS = 128
MAX_CHUNKS = 10_000
_READ_COLUMNS = {
    "knowledge_snapshots": {"id", "tenant_id", "document_id", "version_id", "content_digest", "created_at", "deleted_at"},
    "knowledge_index_generations": {"id", "tenant_id", "document_id", "snapshot_id", "generation_number",
        "algorithm_version", "content_digest", "status", "chunk_count"},
    "knowledge_chunks": {"id", "tenant_id", "generation_id"},
}


class KnowledgePropagationLocator(RetentionBatchContract):
    schema_version: Literal["supportops_restore_resource_locator_v1"] = "supportops_restore_resource_locator_v1"
    tenant_id: UUID
    subject_user_id: UUID
    category: Literal["knowledge"] = "knowledge"
    resource_kind: Literal["knowledge_snapshot"] = "knowledge_snapshot"
    resource_id: UUID


class KnowledgePropagationBinding(RetentionBatchContract):
    schema_version: Literal["knowledge_index_propagation_binding_v1"] = "knowledge_index_propagation_binding_v1"
    locator: KnowledgePropagationLocator = Field(repr=False)
    document_id: UUID = Field(repr=False)
    version_id: UUID = Field(repr=False)
    content_digest: Digest
    database_scope_digest: Digest
    target_id: Identifier
    verifier_id: Literal["knowledge_keyword_index_v1"] = VERIFIER_ID
    # 只观察固定快照在当前keyword_v1读取路径中的隔离，不推断其他版本/副本或物理擦除。
    projection: Literal["keyword_v1_snapshot_read_quarantine_v1"] = "keyword_v1_snapshot_read_quarantine_v1"


class KnowledgePropagationDatabase(RetentionBatchContract):
    expected_database: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_-]{0,62}$", repr=False)
    expected_maintenance_role: str = Field(pattern=r"^[a-z_][a-z0-9_]{0,62}$", repr=False)


class KnowledgePropagationUnavailable(RuntimeError):
    code = "RETENTION_KNOWLEDGE_PROPAGATION_UNCONFIRMED"


class PostgresKnowledgePropagationVerifier:
    """固定知识快照的元数据观察；authority须独立确认实际部署读取路径与完整副本范围。"""

    verifier_id = VERIFIER_ID

    def __init__(self, engine: AsyncEngine, database: KnowledgePropagationDatabase,
        binding: KnowledgePropagationBinding, *, authority: PropagationAuthority,
    ) -> None:
        if (not isinstance(engine, AsyncEngine) or engine.dialect.name != "postgresql"
            or not isinstance(database, KnowledgePropagationDatabase)
            or not isinstance(binding, KnowledgePropagationBinding)
            or binding.database_scope_digest != batch_digest(database) or authority is None):
            raise KnowledgePropagationUnavailable()
        self._engine, self._database_scope, self._binding, self._authority = engine, database, binding, authority

    def _bound(self, request: PropagationRequest, target: PropagationTarget) -> None:
        binding, locator = self._binding, self._binding.locator
        if (not isinstance(request, PropagationRequest) or not isinstance(target, PropagationTarget)
            or self._authority.authority_id != request.authority_id or target not in request.targets
            or target.kind != "index" or target.target_id != binding.target_id
            or target.verifier_id != self.verifier_id or target.target_scope_digest != batch_digest(binding)
            or request.fact.category != "knowledge" or request.fact.tenant_id != locator.tenant_id
            or request.fact.subject_user_id != locator.subject_user_id
            or request.fact.resource_key_digest != hashlib.sha256(_canonical(locator.model_dump(mode="json"))).hexdigest()
            or not request.created_at <= datetime.now(UTC) < request.expires_at):
            raise ValueError()

    async def _current(self, request: PropagationRequest, target: PropagationTarget) -> None:
        self._bound(request, target)
        await self._authority.assert_current(request)
        self._bound(request, target)

    async def _database(self, connection: AsyncConnection) -> None:
        # 先取得共享维护锁再读取新的RC快照，防止等待前的RR快照跨过一次迁移。
        # 快照/代次/分块计数在下面同一条SQL中读取，使用同一个数据快照。
        await connection.execute(text("SET TRANSACTION ISOLATION LEVEL READ COMMITTED, READ ONLY"))
        await connection.execute(text("SELECT set_config('statement_timeout','100ms',true), "
            "set_config('lock_timeout','50ms',true)"))
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
                (has_table_privilege(current_user,c.oid,'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
                    OR has_any_column_privilege(current_user,c.oid,'INSERT,UPDATE,REFERENCES')) AS can_write,
                a.attname,has_column_privilege(current_user,c.oid,a.attnum,'SELECT') AS can_read
            FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
            JOIN pg_catalog.pg_roles r ON r.rolname=current_user
            JOIN pg_catalog.pg_attribute a ON a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped
            WHERE n.nspname='public' AND c.relname IN
                ('knowledge_snapshots','knowledge_index_generations','knowledge_chunks')
            ORDER BY c.relname,a.attnum LIMIT 65
        """))).mappings().all()
        if len(rows) > 64 or {row["relname"] for row in rows} != set(_READ_COLUMNS):
            raise ValueError()
        actual: dict[str, set[str]] = {name: set() for name in _READ_COLUMNS}
        for row in rows:
            allowed = _READ_COLUMNS[row["relname"]]
            actual[row["relname"]].add(row["attname"])
            if (row["db"] != self._database_scope.expected_database
                or row["role"] != self._database_scope.expected_maintenance_role
                or row["original_role"] != self._database_scope.expected_maintenance_role
                or list(row["schemas"]) != ["pg_catalog", "public"] or row["replication"] != "origin"
                or row["readonly"] != "on" or row["relkind"] != "r"
                or row["can_read"] is not (row["attname"] in allowed)
                or any(row[name] for name in ("rolsuper", "rolbypassrls", "rolcreaterole", "rolcreatedb", "owns_table",
                    "relrowsecurity", "relforcerowsecurity", "can_ddl", "can_write"))):
                raise ValueError()
        if any(not _READ_COLUMNS[name] <= columns for name, columns in actual.items()):
            raise ValueError()
        excess = await connection.scalar(text("""
            SELECT EXISTS(SELECT 1 FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
                WHERE n.nspname='public' AND c.relkind IN ('r','p','v','m','f')
                AND c.relname NOT IN ('knowledge_snapshots','knowledge_index_generations','knowledge_chunks')
                AND (has_table_privilege(current_user,c.oid,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
                    OR has_any_column_privilege(current_user,c.oid,'SELECT,INSERT,UPDATE,REFERENCES')))
                OR EXISTS(SELECT 1 FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
                    WHERE n.nspname='public' AND p.proname IN ('supportops_guard_reference_history',
                        'supportops_guard_knowledge_reference_chain','supportops_reject_reference_history_truncate')
                    AND p.proowner=(SELECT oid FROM pg_catalog.pg_roles WHERE rolname=current_user))
        """))
        if excess is not False:
            raise ValueError()
        await connection.run_sync(verify_reference_history_guards)

    async def _observe(self, connection: AsyncConnection, request: PropagationRequest,
    ) -> tuple[Literal["quarantined", "present", "unknown"], str | None, datetime]:
        binding, locator = self._binding, self._binding.locator
        row = (await connection.execute(text("""
            SELECT s.id,s.tenant_id,s.document_id,s.version_id,s.content_digest,s.created_at,s.deleted_at,
                COALESCE((SELECT jsonb_agg(to_jsonb(gv) ORDER BY gv.id) FROM (
                    SELECT g.id::text AS id,g.tenant_id::text AS tenant_id,g.document_id::text AS document_id,
                        g.snapshot_id::text AS snapshot_id,g.generation_number,g.algorithm_version,
                        g.content_digest,g.status,g.chunk_count,
                        (SELECT count(c.id) FROM public.knowledge_chunks c WHERE c.generation_id=g.id) AS actual_chunks,
                        EXISTS(SELECT 1 FROM public.knowledge_chunks c WHERE c.generation_id=g.id
                            AND c.tenant_id<>s.tenant_id) AS wrong_tenant
                    FROM public.knowledge_index_generations g WHERE g.snapshot_id=s.id
                    ORDER BY g.id LIMIT :generation_limit
                ) gv),'[]'::jsonb) AS generations
            FROM public.knowledge_snapshots s WHERE s.id=:snapshot_id
        """), {"snapshot_id": locator.resource_id, "generation_limit": MAX_GENERATIONS + 1})).mappings().one_or_none()
        observed_at = datetime.now(UTC)
        if row is None:
            # 缺一行不能证明完整索引已擦除，也不猜测旧ID的归属。
            return "unknown", None, observed_at
        now = observed_at
        created_at, deleted_at = row["created_at"], row["deleted_at"]
        if (row["tenant_id"] != locator.tenant_id or row["document_id"] != binding.document_id
            or row["version_id"] != binding.version_id or row["content_digest"] != binding.content_digest
            or not isinstance(created_at, datetime) or created_at.tzinfo is None
            or created_at > request.fact.recorded_at
            or deleted_at is not None and (not isinstance(deleted_at, datetime) or deleted_at.tzinfo is None
                or not created_at <= deleted_at <= now)):
            raise ValueError()
        generations = row["generations"]
        if not isinstance(generations, list) or len(generations) > MAX_GENERATIONS:
            raise ValueError()
        generation_ids: set[UUID] = set()
        chunk_count = 0
        fields = _READ_COLUMNS["knowledge_index_generations"] | {"actual_chunks", "wrong_tenant"}
        for item in generations:
            if not isinstance(item, dict) or set(item) != fields or not isinstance(item["id"], str):
                raise ValueError()
            generation_id = UUID(item["id"])
            if (generation_id in generation_ids or item["tenant_id"] != str(locator.tenant_id)
                or item["document_id"] != str(binding.document_id) or item["snapshot_id"] != str(locator.resource_id)
                or item["content_digest"] != binding.content_digest or item["algorithm_version"] != "keyword_v1"
                or item["status"] not in {"pending", "ready", "invalidated", "failed"}
                or type(item["generation_number"]) is not int or item["generation_number"] < 1
                or type(item["chunk_count"]) is not int or not 0 <= item["chunk_count"] <= MAX_CHUNKS
                or type(item["actual_chunks"]) is not int or item["actual_chunks"] != item["chunk_count"]
                or item["wrong_tenant"] is not False):
                raise ValueError()
            generation_ids.add(generation_id)
            chunk_count += item["actual_chunks"]
        if chunk_count > MAX_CHUNKS:
            raise ValueError()
        state: Literal["quarantined", "present"] = "quarantined" if deleted_at is not None and all(
            item["status"] == "invalidated" for item in generations) else "present"
        # 只对精确快照的当前读取隔离作证明；保留的分块和标题不能被描述为物理擦除。
        metadata = [{"id": str(item["id"]), "generation": item["generation_number"], "status": item["status"],
            "chunk_count": item["actual_chunks"]} for item in generations]
        proof = {"schema_version": "knowledge_index_propagation_evidence_v1", "request_digest": batch_digest(request),
            "binding_digest": batch_digest(binding), "fact_digest": request.fact.record_digest,
            "state": state, "snapshot_deleted_at": deleted_at.astimezone(UTC).isoformat() if deleted_at else None,
            "generation_count": len(generations), "chunk_count": chunk_count,
            "generations_digest": hashlib.sha256(_canonical(metadata)).hexdigest(),
            "observed_at": observed_at.isoformat(), "physical_erasure_verified": False}
        return state, hashlib.sha256(_canonical(proof)).hexdigest(), observed_at

    async def observe(self, request: PropagationRequest, target: PropagationTarget) -> PropagationObservation:
        try:
            deadline = asyncio.get_running_loop().time() + 0.9
            async with asyncio.timeout(0.9):
                await self._current(request, target)
                async with self._engine.connect() as connection, connection.begin():
                    await self._database(connection)
                    await self._current(request, target)
                    state, evidence, observed_at = await self._observe(connection, request)
                await self._current(request, target)
                observation = PropagationObservation(request_digest=batch_digest(request), target_id=target.target_id,
                    target_scope_digest=target.target_scope_digest, verifier_id=self.verifier_id,
                    resource_key_digest=request.fact.resource_key_digest, fact_digest=request.fact.record_digest,
                    ledger_generation=request.ledger_generation, state=state, evidence_digest=evidence,
                    observed_at=observed_at)
            if asyncio.get_running_loop().time() > deadline:
                raise ValueError()
            return observation
        except Exception:
            raise KnowledgePropagationUnavailable() from None
