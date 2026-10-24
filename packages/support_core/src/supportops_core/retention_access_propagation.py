"""登录会话和固定凭据修订的只读传播观察；不撤销、删除、解封或解除恢复屏障。"""

import asyncio
import hashlib
import re
from datetime import UTC, datetime
from typing import Literal, Protocol
from uuid import UUID

from pydantic import AwareDatetime, Field
from sqlalchemy import text
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from supportops_core.reference_history_schema import verify_reference_history_guards
from supportops_core.restore_credential_contracts import RestoredCredentialLocator
from supportops_core.restore_session_contracts import RestoredSessionLocator
from supportops_core.restore_session_history import require_restore_session_history_guard
from supportops_core.retention_batch_contracts import Digest, Identifier, RetentionBatchContract, batch_digest
from supportops_core.retention_propagation_contracts import PropagationObservation, PropagationRequest, PropagationTarget
from supportops_core.retention_tombstone_snapshot import _canonical
from supportops_core.schema_structure import MIGRATION_LOCK_KEY, require_public_read_only_schema

_SESSION_COLUMNS = frozenset({"id", "tenant_id", "user_id", "created_at", "revoked_at", "revoked_by", "revocation_code"})
_CREDENTIAL_COLUMNS = frozenset({"id", "tenant_id", "credential_id", "revision", "provider", "created_at", "deleted_at"})
_REVOCATION_CODE = re.compile(r"^[A-Z][A-Z0-9_]{0,31}$")
_State = Literal["quarantined", "present", "unknown"]


class AccessPropagationDatabase(RetentionBatchContract):
    expected_database: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_-]{0,62}$", repr=False)
    expected_maintenance_role: str = Field(pattern=r"^[a-z_][a-z0-9_]{0,62}$", repr=False)


class _AccessBinding(RetentionBatchContract):
    record_created_at: AwareDatetime
    database_scope_digest: Digest
    target_id: Identifier


class SessionPropagationBinding(_AccessBinding):
    schema_version: Literal["auth_session_propagation_binding_v1"] = "auth_session_propagation_binding_v1"
    locator: RestoredSessionLocator = Field(repr=False)
    verifier_id: Literal["auth_session_quarantine_v1"] = "auth_session_quarantine_v1"
    projection: Literal["auth_session_persistent_revocation_v1"] = "auth_session_persistent_revocation_v1"


class CredentialPropagationBinding(_AccessBinding):
    schema_version: Literal["credential_revision_propagation_binding_v1"] = "credential_revision_propagation_binding_v1"
    locator: RestoredCredentialLocator = Field(repr=False)
    credential_id: UUID = Field(repr=False)
    credential_revision: int = Field(ge=1, le=2**31 - 1)
    provider: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_.:-]{0,63}$")
    verifier_id: Literal["credential_revision_quarantine_v1"] = "credential_revision_quarantine_v1"
    projection: Literal["credential_revision_deleted_read_quarantine_v1"] = "credential_revision_deleted_read_quarantine_v1"


AccessPropagationBinding = SessionPropagationBinding | CredentialPropagationBinding


class AccessPropagationAuthority(Protocol):
    authority_id: str

    async def assert_current(self, request: PropagationRequest) -> None:
        """独立核对原事实成员/当前账本代次、当前读取许可及完整副本拓扑。"""
        ...

    async def assert_access_scope(self, request: PropagationRequest, binding: AccessPropagationBinding) -> None:
        """核对原本人/资源/时间与实际目标，并确认投影对应当前部署的鉴权或解封路径。

        凭据修订是共享资源，个人关联不授予共享范围的读取或维护权；需独立批准。
        撤销/软删除观察不证明令牌物理销毁、provider秘密撤销或其他副本/在途进程已隔离。
        """
        ...


class AccessPropagationUnavailable(RuntimeError):
    code = "RETENTION_ACCESS_PROPAGATION_UNCONFIRMED"


class _AccessMetadataReader:
    """仅复用列权限、历史保护及状态投影；调用方必须自行提供当前授权围栏。"""

    def __init__(self, database: AccessPropagationDatabase, binding: AccessPropagationBinding) -> None:
        if (not isinstance(database, AccessPropagationDatabase)
            or type(binding) not in (SessionPropagationBinding, CredentialPropagationBinding)
            or binding.database_scope_digest != batch_digest(database)):
            raise AccessPropagationUnavailable()
        self._database_scope = AccessPropagationDatabase.model_validate_json(database.model_dump_json())
        self._binding = type(binding).model_validate_json(binding.model_dump_json())

    async def _database(self, connection: AsyncConnection) -> None:
        await connection.execute(text("SET TRANSACTION ISOLATION LEVEL READ COMMITTED, READ ONLY"))
        await connection.execute(text("SELECT pg_catalog.set_config('statement_timeout','100ms',true), "
            "pg_catalog.set_config('lock_timeout','50ms',true)"))
        await connection.run_sync(require_public_read_only_schema)
        if await connection.scalar(text("SELECT pg_catalog.pg_try_advisory_xact_lock_shared(:key)"),
            {"key": MIGRATION_LOCK_KEY}) is not True:
            raise ValueError()
        is_session = isinstance(self._binding, SessionPropagationBinding)
        table = "auth_sessions" if is_session else "model_credential_revisions"
        allowed = _SESSION_COLUMNS if is_session else _CREDENTIAL_COLUMNS
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
            WHERE n.nspname='public' AND c.relname=:table ORDER BY a.attnum LIMIT 33
        """), {"table": table})).mappings().all()
        if not rows or len(rows) > 32 or not allowed <= {row["attname"] for row in rows}:
            raise ValueError()
        for row in rows:
            if (row["db"] != self._database_scope.expected_database
                or row["role"] != self._database_scope.expected_maintenance_role
                or row["original_role"] != self._database_scope.expected_maintenance_role or row["relkind"] != "r"
                or row["can_read"] is not (row["attname"] in allowed)
                or any(row[name] for name in ("rolsuper", "rolbypassrls", "rolcreaterole", "rolcreatedb",
                    "owns_table", "owns_schema", "relrowsecurity", "relforcerowsecurity", "can_ddl", "can_write"))):
                raise ValueError()
        excess = await connection.scalar(text("""
            SELECT EXISTS(SELECT 1 FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
                WHERE n.nspname NOT IN ('pg_catalog','information_schema') AND n.nspname !~ '^pg_'
                AND c.relkind IN ('r','p','v','m','f') AND NOT (n.nspname='public' AND c.relname=:table)
                AND (has_table_privilege(current_user,c.oid,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
                    OR has_any_column_privilege(current_user,c.oid,'SELECT,INSERT,UPDATE,REFERENCES')))
                OR EXISTS(SELECT 1 FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
                    WHERE n.nspname NOT IN ('pg_catalog','information_schema') AND n.nspname !~ '^pg_'
                    AND p.proowner=(SELECT oid FROM pg_catalog.pg_roles WHERE rolname=current_user))
        """), {"table": table})
        if excess is not False:
            raise ValueError()
        if is_session:
            await require_restore_session_history_guard(connection)
        else:
            await connection.run_sync(verify_reference_history_guards)

    async def _row(self, connection: AsyncConnection) -> RowMapping | None:
        # 两个封闭SQL分支，不拼接表名或接受外部查询；ONLY不将继承子表当作原目标。
        query = text("""
            SELECT id,tenant_id,user_id,created_at,revoked_at,revoked_by,revocation_code
            FROM ONLY public.auth_sessions WHERE id=:id LIMIT 2
        """) if isinstance(self._binding, SessionPropagationBinding) else text("""
            SELECT id,tenant_id,credential_id,revision,provider,created_at,deleted_at
            FROM ONLY public.model_credential_revisions WHERE id=:id LIMIT 2
        """)
        return (await connection.execute(query, {"id": self._binding.locator.resource_id})).mappings().one_or_none()

    def _state(self, row: RowMapping | None, observed_at: datetime) -> tuple[_State, datetime | None]:
        if row is None:
            return "unknown", None
        binding, locator = self._binding, self._binding.locator
        if (row["id"] != locator.resource_id or row["tenant_id"] != locator.tenant_id
            or not isinstance(row["created_at"], datetime) or row["created_at"].tzinfo is None
            or row["created_at"] != binding.record_created_at):
            raise ValueError()
        if isinstance(binding, SessionPropagationBinding):
            if row["user_id"] != locator.subject_user_id:
                raise ValueError()
            marker, actor, code = row["revoked_at"], row["revoked_by"], row["revocation_code"]
            if marker is None:
                if actor is not None or code is not None:
                    raise ValueError()
            elif (actor is not None and not isinstance(actor, UUID)
                or not isinstance(code, str) or _REVOCATION_CODE.fullmatch(code) is None):
                raise ValueError()
        else:
            if (row["credential_id"] != binding.credential_id or type(row["revision"]) is not int
                or row["revision"] != binding.credential_revision or row["provider"] != binding.provider):
                raise ValueError()
            marker = row["deleted_at"]
        if marker is None:
            return "present", None
        if (not isinstance(marker, datetime) or marker.tzinfo is None
            or not binding.record_created_at <= marker <= observed_at):
            raise ValueError()
        return "quarantined", marker.astimezone(UTC)


class PostgresAccessPropagationVerifier(_AccessMetadataReader):
    """每实例仅一种固定资源投影和独立只读身份，显式加入原传播注册表后才可观察。"""

    def __init__(self, engine: AsyncEngine, database: AccessPropagationDatabase,
        binding: AccessPropagationBinding, *, authority: AccessPropagationAuthority,
    ) -> None:
        if (not isinstance(engine, AsyncEngine) or engine.dialect.name != "postgresql"
            or not isinstance(database, AccessPropagationDatabase)
            or type(binding) not in (SessionPropagationBinding, CredentialPropagationBinding) or authority is None
            or binding.database_scope_digest != batch_digest(database)):
            raise AccessPropagationUnavailable()
        # 元数据独立严格复制，不在构造时连接DB、读取批准或获取任何秘密。
        super().__init__(database, binding)
        self._engine, self._authority = engine, authority

    @property
    def verifier_id(self) -> str:
        return self._binding.verifier_id

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
            await self._authority.assert_access_scope(request, self._binding)
        self._bound(request, target)

    async def observe(self, request: PropagationRequest, target: PropagationTarget) -> PropagationObservation:
        try:
            deadline = asyncio.get_running_loop().time() + 0.9
            async with asyncio.timeout(0.9):
                await self._current(request, target)
                async with self._engine.connect() as connection, connection.begin():
                    await self._database(connection)
                    await self._current(request, target)
                    row = await self._row(connection)
                    observed_at = datetime.now(UTC)
                    state, marker = self._state(row, observed_at)
                await self._current(request, target)
                proof = {"schema_version": "access_quarantine_propagation_evidence_v1",
                    "request_digest": batch_digest(request), "binding_digest": batch_digest(self._binding),
                    "fact_digest": request.fact.record_digest, "state": state,
                    "quarantine_marker_at": marker.isoformat() if marker else None,
                    "observed_at": observed_at.isoformat(), "erasure_verified": False,
                    "provider_secret_revocation_verified": False}
                observation = PropagationObservation(request_digest=batch_digest(request), target_id=target.target_id,
                    target_scope_digest=target.target_scope_digest, verifier_id=self.verifier_id,
                    resource_key_digest=request.fact.resource_key_digest, fact_digest=request.fact.record_digest,
                    ledger_generation=request.ledger_generation, state=state,
                    evidence_digest=hashlib.sha256(_canonical(proof)).hexdigest() if state != "unknown" else None,
                    observed_at=observed_at)
            if asyncio.get_running_loop().time() > deadline:
                raise ValueError()
            return observation
        except Exception:
            raise AccessPropagationUnavailable() from None
