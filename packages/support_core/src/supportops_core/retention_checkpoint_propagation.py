"""指定执行尝试checkpoint的只读残留观察；不读取载荷、不删除或推断擦除完成。"""

import asyncio
import hashlib
from datetime import UTC, datetime
from typing import Literal, Protocol
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator
from sqlalchemy import Column, MetaData, Table, Text, select, text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from supportops_core.retention_batch_contracts import Digest, Identifier, RetentionBatchContract, batch_digest
from supportops_core.retention_propagation_contracts import PropagationObservation, PropagationRequest, PropagationTarget
from supportops_core.retention_tombstone_snapshot import _canonical
from supportops_core.schema_structure import MIGRATION_LOCK_KEY, require_public_read_only_schema, verify_checkpoint_schema

VERIFIER_ID = "checkpoint_attempt_presence_v1"
_READ_COLUMNS = {
    "checkpoint_migrations": {"v"},
    "checkpoints": {"thread_id"},
    "checkpoint_blobs": {"thread_id"},
    "checkpoint_writes": {"thread_id"},
}


class CheckpointPropagationLocator(RetentionBatchContract):
    schema_version: Literal["supportops_restore_resource_locator_v1"] = "supportops_restore_resource_locator_v1"
    tenant_id: UUID
    subject_user_id: UUID
    category: Literal["checkpoints"] = "checkpoints"
    resource_kind: Literal["checkpoint_attempt_thread"] = "checkpoint_attempt_thread"
    resource_id: UUID


class CheckpointPropagationDatabase(RetentionBatchContract):
    expected_database: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_-]{0,62}$", repr=False)
    expected_maintenance_role: str = Field(pattern=r"^[a-z_][a-z0-9_]{0,62}$", repr=False)
    checkpoint_schema: str = Field(pattern=r"^[a-z][a-z0-9_]{0,62}$", repr=False)

    @model_validator(mode="after")
    def dedicated_schema(self) -> "CheckpointPropagationDatabase":
        if self.checkpoint_schema in {"public", "information_schema"} or self.checkpoint_schema.startswith("pg_"):
            raise ValueError("RETENTION_CHECKPOINT_SCHEMA_INVALID")
        return self


class CheckpointPropagationBinding(RetentionBatchContract):
    schema_version: Literal["checkpoint_attempt_propagation_binding_v1"] = "checkpoint_attempt_propagation_binding_v1"
    locator: CheckpointPropagationLocator = Field(repr=False)
    conversation_id: UUID = Field(repr=False)
    run_id: UUID = Field(repr=False)
    attempt_generation: int = Field(ge=1, le=2**31 - 1)
    attempt_started_at: AwareDatetime
    database_scope_digest: Digest
    target_id: Identifier
    verifier_id: Literal["checkpoint_attempt_presence_v1"] = VERIFIER_ID
    projection: Literal["langgraph_pg_attempt_thread_presence_v1"] = "langgraph_pg_attempt_thread_presence_v1"

    @property
    def thread_id(self) -> str:
        # 与run_attempt_services当前持久命名一致；不接任意字符串、前缀或namespace过滤。
        return (f"tenant:{self.locator.tenant_id}:conversation:{self.conversation_id}"
            f":run:{self.run_id}:attempt:{self.locator.resource_id}")


class CheckpointPropagationAuthority(Protocol):
    authority_id: str

    async def assert_current(self, request: PropagationRequest) -> None:
        """复检真实账本成员/代次、当前读取许可、批准及完整副本拓扑。"""
        ...

    async def assert_checkpoint_scope(self, request: PropagationRequest, binding: CheckpointPropagationBinding) -> None:
        """从独立可信盘点核对原主体/会话/Run/attempt/代次/开始时间及实际库/schema。

        thread命名和摘要相符不证明归属或目标实例；不能用用户提供的UUID替代盘点。
        本许可仅用于元数据观察，不授予删除、重放或恢复开放。
        """
        ...


class CheckpointPropagationUnavailable(RuntimeError):
    code = "RETENTION_CHECKPOINT_PROPAGATION_UNCONFIRMED"


class PostgresCheckpointPropagationVerifier:
    """只发现固定thread是否残留；全部无行仍unknown，没有独立隔离/擦除证明。"""

    verifier_id = VERIFIER_ID

    def __init__(self, engine: AsyncEngine, database: CheckpointPropagationDatabase,
        binding: CheckpointPropagationBinding, *, authority: CheckpointPropagationAuthority,
    ) -> None:
        if (not isinstance(engine, AsyncEngine) or engine.dialect.name != "postgresql"
            or not isinstance(database, CheckpointPropagationDatabase)
            or not isinstance(binding, CheckpointPropagationBinding)
            or binding.database_scope_digest != batch_digest(database) or authority is None):
            raise CheckpointPropagationUnavailable()
        self._engine, self._database_scope, self._binding, self._authority = engine, database, binding, authority

    def _bound(self, request: PropagationRequest, target: PropagationTarget) -> None:
        binding, locator = self._binding, self._binding.locator
        if (not isinstance(request, PropagationRequest) or not isinstance(target, PropagationTarget)
            or self._authority.authority_id != request.authority_id or target not in request.targets
            or target.kind != "external_copy" or target.target_id != binding.target_id
            or target.verifier_id != self.verifier_id or target.target_scope_digest != batch_digest(binding)
            or request.fact.category != "checkpoints" or request.fact.tenant_id != locator.tenant_id
            or request.fact.subject_user_id != locator.subject_user_id
            or request.fact.resource_key_digest != hashlib.sha256(_canonical(locator.model_dump(mode="json"))).hexdigest()
            or binding.attempt_started_at > request.fact.recorded_at
            or not request.created_at <= datetime.now(UTC) < request.expires_at):
            raise ValueError()

    async def _current(self, request: PropagationRequest, target: PropagationTarget) -> None:
        self._bound(request, target)
        async with asyncio.timeout(0.5):
            await self._authority.assert_current(request)
            await self._authority.assert_checkpoint_scope(request, self._binding)
        self._bound(request, target)

    async def _database(self, connection: AsyncConnection) -> None:
        await connection.execute(text("SET TRANSACTION ISOLATION LEVEL READ COMMITTED, READ ONLY"))
        await connection.execute(text("SELECT pg_catalog.set_config('statement_timeout','100ms',true), "
            "pg_catalog.set_config('lock_timeout','50ms',true)"))
        await connection.run_sync(require_public_read_only_schema)
        if await connection.scalar(text("SELECT pg_catalog.pg_try_advisory_xact_lock_shared(:key)"),
            {"key": MIGRATION_LOCK_KEY}) is not True:
            raise ValueError()
        scope = self._database_scope
        rows = (await connection.execute(text("""
            SELECT current_database() AS db,current_user AS role,session_user AS original_role,
                r.rolsuper,r.rolbypassrls,r.rolcreaterole,r.rolcreatedb,
                c.relname,c.relkind,c.relrowsecurity,c.relforcerowsecurity,c.relowner=r.oid AS owns_table,
                n.nspowner=r.oid AS owns_schema,has_schema_privilege(current_user,n.oid,'CREATE') AS can_ddl,
                has_schema_privilege(current_user,'public','CREATE') AS public_ddl,
                (has_table_privilege(current_user,c.oid,'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
                    OR has_any_column_privilege(current_user,c.oid,'INSERT,UPDATE,REFERENCES')) AS can_write,
                a.attname,has_column_privilege(current_user,c.oid,a.attnum,'SELECT') AS can_read
            FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
            JOIN pg_catalog.pg_roles r ON r.rolname=current_user
            JOIN pg_catalog.pg_attribute a ON a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped
            WHERE n.nspname=:schema AND c.relname IN
                ('checkpoint_migrations','checkpoints','checkpoint_blobs','checkpoint_writes')
            ORDER BY c.relname,a.attnum LIMIT 65
        """), {"schema": scope.checkpoint_schema})).mappings().all()
        if len(rows) > 64 or {row["relname"] for row in rows} != set(_READ_COLUMNS):
            raise ValueError()
        actual: dict[str, set[str]] = {name: set() for name in _READ_COLUMNS}
        for row in rows:
            actual[row["relname"]].add(row["attname"])
            if (row["db"] != scope.expected_database or row["role"] != scope.expected_maintenance_role
                or row["original_role"] != scope.expected_maintenance_role or row["relkind"] != "r"
                or row["can_read"] is not (row["attname"] in _READ_COLUMNS[row["relname"]])
                or any(row[name] for name in ("rolsuper", "rolbypassrls", "rolcreaterole", "rolcreatedb", "owns_table",
                    "owns_schema", "relrowsecurity", "relforcerowsecurity", "can_ddl", "public_ddl", "can_write"))):
                raise ValueError()
        if any(not _READ_COLUMNS[name] <= columns for name, columns in actual.items()):
            raise ValueError()
        excess = await connection.scalar(text("""
            SELECT EXISTS(SELECT 1 FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
                WHERE n.nspname NOT IN ('pg_catalog','information_schema') AND n.nspname !~ '^pg_'
                AND c.relkind IN ('r','p','v','m','f')
                AND NOT (n.nspname=:schema AND c.relname IN
                    ('checkpoint_migrations','checkpoints','checkpoint_blobs','checkpoint_writes'))
                AND (has_table_privilege(current_user,c.oid,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
                    OR has_any_column_privilege(current_user,c.oid,'SELECT,INSERT,UPDATE,REFERENCES')))
        """), {"schema": scope.checkpoint_schema})
        if excess is not False:
            raise ValueError()
        # 复用锁定3.1.2/迁移0–9的结构核对，不加载Saver、不调用setup或读取JSON/blob。
        await connection.run_sync(lambda sync: verify_checkpoint_schema(sync, schema=scope.checkpoint_schema))

    async def observe(self, request: PropagationRequest, target: PropagationTarget) -> PropagationObservation:
        try:
            deadline = asyncio.get_running_loop().time() + 0.9
            async with asyncio.timeout(0.9):
                await self._current(request, target)
                async with self._engine.connect() as connection, connection.begin():
                    await self._database(connection)
                    await self._current(request, target)
                    metadata = MetaData()
                    tables = [Table(name, metadata, Column("thread_id", Text), schema=self._database_scope.checkpoint_schema)
                        for name in ("checkpoints", "checkpoint_blobs", "checkpoint_writes")]
                    # 同一SQL快照覆盖三表全部namespace，EXISTS只需命中一行，不扫描或返回载荷。
                    flags = (await connection.execute(select(*[
                        select(table.c.thread_id).where(table.c.thread_id == self._binding.thread_id).exists().label(table.name)
                        for table in tables]))).mappings().one()
                    observed_at = datetime.now(UTC)
                await self._current(request, target)
                if any(type(value) is not bool for value in flags.values()):
                    raise ValueError()
                state: Literal["present", "unknown"] = "present" if any(flags.values()) else "unknown"
                proof = {"schema_version": "checkpoint_attempt_presence_evidence_v1",
                    "request_digest": batch_digest(request), "binding_digest": batch_digest(self._binding),
                    "fact_digest": request.fact.record_digest, "state": state, "present_in_tables": dict(flags),
                    "observed_at": observed_at.isoformat(), "erasure_verified": False, "quarantine_verified": False}
                observation = PropagationObservation(request_digest=batch_digest(request), target_id=target.target_id,
                    target_scope_digest=target.target_scope_digest, verifier_id=self.verifier_id,
                    resource_key_digest=request.fact.resource_key_digest, fact_digest=request.fact.record_digest,
                    ledger_generation=request.ledger_generation, state=state,
                    evidence_digest=hashlib.sha256(_canonical(proof)).hexdigest() if state == "present" else None,
                    observed_at=observed_at)
            if asyncio.get_running_loop().time() > deadline:
                raise ValueError()
            return observation
        except Exception:
            raise CheckpointPropagationUnavailable() from None
