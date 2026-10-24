"""专业子调用记录的独立只读残留观察；不读取回执载荷或密封回答。"""

import asyncio
import hashlib
from datetime import UTC, datetime
from typing import Literal, Protocol
from uuid import UUID

from pydantic import AwareDatetime, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from supportops_core.retention_batch_contracts import Digest, Identifier, RetentionBatchContract, batch_digest
from supportops_core.retention_propagation_contracts import PropagationObservation, PropagationRequest, PropagationTarget
from supportops_core.retention_tombstone_snapshot import _canonical
from supportops_core.schema_structure import MIGRATION_LOCK_KEY, require_public_read_only_schema

VERIFIER_ID = "specialist_execution_presence_v1"
_READ_COLUMNS = frozenset({"call_id", "tenant_id", "run_id", "attempt_id", "generation", "record_digest", "created_at"})
_COLUMNS = _READ_COLUMNS | {"payload", "sealed_answer"}


class SpecialistPropagationLocator(RetentionBatchContract):
    schema_version: Literal["supportops_restore_resource_locator_v1"] = "supportops_restore_resource_locator_v1"
    tenant_id: UUID
    subject_user_id: UUID
    category: Literal["runs"] = "runs"
    resource_kind: Literal["specialist_execution_observation"] = "specialist_execution_observation"
    resource_id: UUID


class SpecialistPropagationDatabase(RetentionBatchContract):
    expected_database: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_-]{0,62}$", repr=False)
    expected_maintenance_role: str = Field(pattern=r"^[a-z_][a-z0-9_]{0,62}$", repr=False)


class SpecialistPropagationBinding(RetentionBatchContract):
    schema_version: Literal["specialist_execution_propagation_binding_v1"] = "specialist_execution_propagation_binding_v1"
    locator: SpecialistPropagationLocator = Field(repr=False)
    run_id: UUID = Field(repr=False)
    attempt_id: UUID = Field(repr=False)
    generation: int = Field(ge=1, le=2**31 - 1)
    record_digest: Digest
    record_created_at: AwareDatetime
    database_scope_digest: Digest
    target_id: Identifier
    verifier_id: Literal["specialist_execution_presence_v1"] = VERIFIER_ID
    projection: Literal["specialist_execution_metadata_presence_v1"] = "specialist_execution_metadata_presence_v1"


class SpecialistPropagationAuthority(Protocol):
    authority_id: str

    async def assert_current(self, request: PropagationRequest) -> None:
        """独立核对原删除事实真实成员、当前代次/读取许可和完整副本拓扑。"""
        ...

    async def assert_specialist_scope(self, request: PropagationRequest, binding: SpecialistPropagationBinding) -> None:
        """独立盘点核对本人/原call/Run/attempt/代次/记录摘要与时间及实际目标实例。

        普通管理员身份、原call UUID或摘要不能代替归属和当前读取批准。
        本接口只批准元数据观察，不授予载荷/密文读取、解封或删除许可。
        """
        ...


class SpecialistPropagationUnavailable(RuntimeError):
    code = "RETENTION_SPECIALIST_PROPAGATION_UNCONFIRMED"


class PostgresSpecialistPropagationVerifier:
    """精确原call仍有记录即present；无行仅unknown，不推断隔离或物理擦除。"""

    verifier_id = VERIFIER_ID

    def __init__(self, engine: AsyncEngine, database: SpecialistPropagationDatabase,
        binding: SpecialistPropagationBinding, *, authority: SpecialistPropagationAuthority,
    ) -> None:
        if (not isinstance(engine, AsyncEngine) or engine.dialect.name != "postgresql"
            or not isinstance(database, SpecialistPropagationDatabase)
            or not isinstance(binding, SpecialistPropagationBinding) or authority is None
            or binding.database_scope_digest != batch_digest(database)):
            raise SpecialistPropagationUnavailable()
        self._engine, self._database_scope, self._binding, self._authority = engine, database, binding, authority

    def _bound(self, request: PropagationRequest, target: PropagationTarget) -> None:
        binding, locator = self._binding, self._binding.locator
        if (not isinstance(request, PropagationRequest) or not isinstance(target, PropagationTarget)
            or self._authority.authority_id != request.authority_id or target not in request.targets
            or target.kind != "external_copy" or target.target_id != binding.target_id
            or target.verifier_id != self.verifier_id or target.target_scope_digest != batch_digest(binding)
            or request.fact.category != locator.category or request.fact.tenant_id != locator.tenant_id
            or request.fact.subject_user_id != locator.subject_user_id
            or request.fact.resource_key_digest != hashlib.sha256(_canonical(locator.model_dump(mode="json"))).hexdigest()
            or binding.record_created_at > request.fact.recorded_at
            or not request.created_at <= datetime.now(UTC) < request.expires_at):
            raise ValueError()

    async def _current(self, request: PropagationRequest, target: PropagationTarget) -> None:
        self._bound(request, target)
        async with asyncio.timeout(0.5):
            await self._authority.assert_current(request)
            await self._authority.assert_specialist_scope(request, self._binding)
        self._bound(request, target)

    async def _database(self, connection: AsyncConnection) -> None:
        await connection.execute(text("SET TRANSACTION ISOLATION LEVEL READ COMMITTED, READ ONLY"))
        await connection.execute(text("SELECT pg_catalog.set_config('statement_timeout','100ms',true), "
            "pg_catalog.set_config('lock_timeout','50ms',true)"))
        await connection.run_sync(require_public_read_only_schema)
        if await connection.scalar(text("SELECT pg_catalog.pg_try_advisory_xact_lock_shared(:key)"),
            {"key": MIGRATION_LOCK_KEY}) is not True:
            raise ValueError()
        rows = (await connection.execute(text("""
            SELECT current_database() AS db,current_user AS role,session_user AS original_role,
                r.rolsuper,r.rolbypassrls,r.rolcreaterole,r.rolcreatedb,
                c.relkind,c.relrowsecurity,c.relforcerowsecurity,c.relowner=r.oid AS owns_table,
                n.nspowner=r.oid AS owns_schema,has_schema_privilege(current_user,n.oid,'CREATE') AS can_ddl,
                (has_table_privilege(current_user,c.oid,'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
                    OR has_any_column_privilege(current_user,c.oid,'INSERT,UPDATE,REFERENCES')) AS can_write,
                a.attname,has_column_privilege(current_user,c.oid,a.attnum,'SELECT') AS can_read
            FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
            JOIN pg_catalog.pg_roles r ON r.rolname=current_user
            JOIN pg_catalog.pg_attribute a ON a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped
            WHERE n.nspname='public' AND c.relname='specialist_execution_observations'
            ORDER BY a.attnum LIMIT 10
        """))).mappings().all()
        if len(rows) != len(_COLUMNS) or {row["attname"] for row in rows} != _COLUMNS:
            raise ValueError()
        for row in rows:
            if (row["db"] != self._database_scope.expected_database
                or row["role"] != self._database_scope.expected_maintenance_role
                or row["original_role"] != self._database_scope.expected_maintenance_role or row["relkind"] != "r"
                or row["can_read"] is not (row["attname"] in _READ_COLUMNS)
                or any(row[name] for name in ("rolsuper", "rolbypassrls", "rolcreaterole", "rolcreatedb",
                    "owns_table", "owns_schema", "relrowsecurity", "relforcerowsecurity", "can_ddl", "can_write"))):
                raise ValueError()
        excess = await connection.scalar(text("""
            SELECT EXISTS(SELECT 1 FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
                WHERE n.nspname NOT IN ('pg_catalog','information_schema') AND n.nspname !~ '^pg_'
                AND c.relkind IN ('r','p','v','m','f')
                AND NOT (n.nspname='public' AND c.relname='specialist_execution_observations')
                AND (has_table_privilege(current_user,c.oid,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
                    OR has_any_column_privilege(current_user,c.oid,'SELECT,INSERT,UPDATE,REFERENCES')))
        """))
        if excess is not False:
            raise ValueError()

    async def observe(self, request: PropagationRequest, target: PropagationTarget) -> PropagationObservation:
        try:
            deadline = asyncio.get_running_loop().time() + 0.9
            async with asyncio.timeout(0.9):
                await self._current(request, target)
                async with self._engine.connect() as connection, connection.begin():
                    await self._database(connection)
                    await self._current(request, target)
                    # 按唯一call定位，错误租户/归属不能预过滤成无行；只取七个白名单元数据列。
                    row = (await connection.execute(text("""
                        SELECT call_id,tenant_id,run_id,attempt_id,generation,record_digest,created_at
                        FROM ONLY public.specialist_execution_observations WHERE call_id=:call_id LIMIT 2
                    """), {"call_id": self._binding.locator.resource_id})).mappings().one_or_none()
                    observed_at = datetime.now(UTC)
                    binding = self._binding
                    if row is not None and (row["call_id"] != binding.locator.resource_id
                        or row["tenant_id"] != binding.locator.tenant_id or row["run_id"] != binding.run_id
                        or row["attempt_id"] != binding.attempt_id or type(row["generation"]) is not int
                        or row["generation"] != binding.generation or row["record_digest"] != binding.record_digest
                        or not isinstance(row["created_at"], datetime) or row["created_at"].tzinfo is None
                        or row["created_at"] != binding.record_created_at):
                        raise ValueError()
                await self._current(request, target)
                state: Literal["present", "unknown"] = "present" if row is not None else "unknown"
                proof = {"schema_version": "specialist_execution_presence_evidence_v1",
                    "request_digest": batch_digest(request), "binding_digest": batch_digest(binding),
                    "fact_digest": request.fact.record_digest, "state": state, "observed_at": observed_at.isoformat(),
                    "payload_read": False, "sealed_answer_read": False, "erasure_verified": False}
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
            raise SpecialistPropagationUnavailable() from None
