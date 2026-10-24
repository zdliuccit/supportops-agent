"""原一次派发交接下的会话/凭据隔离写适配；无默认批准、注册或物理删除。"""

import asyncio
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from supportops_core.reference_history_schema import verify_reference_history_guards
from supportops_core.restore_session_history import require_restore_session_history_guard
from supportops_core.retention_access_batch import _prepare_access_scope
from supportops_core.retention_access_propagation import (
    AccessPropagationBinding, AccessPropagationDatabase, SessionPropagationBinding,
)
from supportops_core.retention_batch_contracts import RetentionBatchLease, RetentionBatchPlan
from supportops_core.retention_batch_events import RetentionBatchEvent, require_live_event
from supportops_core.retention_batch_store import PostgresRetentionBatchStore
from supportops_core.retention_dispatch import RetentionBatchDispatch
from supportops_core.schema_structure import MIGRATION_LOCK_KEY, require_public_schema_context

EXECUTOR_ID = "access_quarantine_executor_v1"
_READ_SESSION = frozenset({"id", "tenant_id", "user_id", "created_at", "revoked_at", "revoked_by", "revocation_code"})
_READ_CREDENTIAL = frozenset({"id", "tenant_id", "credential_id", "revision", "provider", "created_at", "deleted_at"})


class AccessRetentionExecutionAuthority(Protocol):
    async def assert_current_execution(self, event: RetentionBatchEvent, write_database: AccessPropagationDatabase,
        verification_database: AccessPropagationDatabase, bindings: tuple[AccessPropagationBinding, ...],
    ) -> None:
        """独立复核当前隔离写批准/政策/法律保留/完整依赖、本人和共享资源范围。

        必须从真实部署配置确认账本、写引擎与复核引擎是同一个实际数据库实例，不能只
        比较库名；确认原maintenance身份、最小列权限、实际读取路径和副本盘点。主体共享
        advisory围栏只有同库才与原Store排他锁互斥，绕过该Store的写入/DDL另需受控。
        本批准仅允许首次永久撤销原会话或软删除原凭据修订；不授予物理擦除、provider
        秘密撤销、任意SQL/其他资源写入，也不能以管理员身份或影响预览自行批准。
        """
        ...


class AccessRetentionExecutionUnconfirmed(RuntimeError):
    code = "RETENTION_ACCESS_EXECUTION_UNCONFIRMED"


class PostgresAccessRetentionExecutor:
    executor_id = EXECUTOR_ID

    def __init__(self, engine: AsyncEngine, database: AccessPropagationDatabase, *,
        verification_database: AccessPropagationDatabase, plan: RetentionBatchPlan,
        bindings: tuple[AccessPropagationBinding, ...], store: PostgresRetentionBatchStore | None = None,
        authority: AccessRetentionExecutionAuthority | None = None,
    ) -> None:
        if (not isinstance(engine, AsyncEngine) or engine.dialect.name != "postgresql"
            or not isinstance(database, AccessPropagationDatabase)
            or not isinstance(verification_database, AccessPropagationDatabase)
            or not isinstance(plan, RetentionBatchPlan) or plan.executor_id != self.executor_id
            or not isinstance(store, PostgresRetentionBatchStore) or authority is None
            or database.expected_database != verification_database.expected_database
            or database.expected_database != store.expected_database
            or database.expected_maintenance_role == verification_database.expected_maintenance_role):
            raise AccessRetentionExecutionUnconfirmed()
        self._plan, self._verification_database, self._bindings, self._readers = _prepare_access_scope(
            plan, verification_database, bindings)
        self._database = AccessPropagationDatabase.model_validate_json(database.model_dump_json())
        self._engine, self._store, self._authority = engine, store, authority
        self._entered = False

    async def execute_once(self, plan: RetentionBatchPlan, lease: RetentionBatchLease) -> None:
        # 原协议兼容入口不能拿历史plan/lease进入写事务，必须经新协调分支传本次交接。
        raise AccessRetentionExecutionUnconfirmed()

    async def _current(self, event: RetentionBatchEvent) -> None:
        require_live_event(event, event.lease)
        async with asyncio.timeout(0.7):
            if await self._store.read_current_dispatch(self._plan, event.lease) != event:
                raise ValueError()
            await self._authority.assert_current_execution(event, self._database,
                self._verification_database, self._bindings)
        require_live_event(event, event.lease)

    async def _database_policy(self, connection: AsyncConnection) -> None:
        await connection.execute(text("SET TRANSACTION ISOLATION LEVEL READ COMMITTED, READ WRITE"))
        await connection.execute(text("SELECT pg_catalog.set_config('statement_timeout','100ms',true), "
            "pg_catalog.set_config('lock_timeout','50ms',true)"))
        await connection.run_sync(require_public_schema_context)
        if await connection.scalar(text("SELECT pg_catalog.pg_try_advisory_xact_lock_shared(:key)"),
            {"key": MIGRATION_LOCK_KEY}) is not True:
            raise ValueError()
        is_session = isinstance(self._bindings[0], SessionPropagationBinding)
        table = "auth_sessions" if is_session else "model_credential_revisions"
        readable = _READ_SESSION if is_session else _READ_CREDENTIAL
        writable = {"revoked_at", "revocation_code"} if is_session else {"deleted_at"}
        rows = (await connection.execute(text("""
            SELECT current_database() AS db,current_user AS role,session_user AS original_role,
                current_setting('transaction_read_only') AS readonly,
                r.rolsuper,r.rolbypassrls,r.rolcreaterole,r.rolcreatedb,
                c.relkind,c.relrowsecurity,c.relforcerowsecurity,c.relowner=r.oid AS owns_table,
                n.nspowner=r.oid AS owns_schema,has_schema_privilege(current_user,n.oid,'CREATE') AS can_ddl,
                (has_table_privilege(current_user,c.oid,'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
                    OR has_any_column_privilege(current_user,c.oid,'INSERT,REFERENCES')) AS broad_write,
                a.attname,has_column_privilege(current_user,c.oid,a.attnum,'SELECT') AS can_read,
                has_column_privilege(current_user,c.oid,a.attnum,'UPDATE') AS can_update
            FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
            JOIN pg_catalog.pg_roles r ON r.rolname=current_user
            JOIN pg_catalog.pg_attribute a ON a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped
            WHERE n.nspname='public' AND c.relname=:table ORDER BY a.attnum LIMIT 33
        """), {"table": table})).mappings().all()
        if not rows or len(rows) > 32 or not (readable | writable) <= {row["attname"] for row in rows}:
            raise ValueError()
        for row in rows:
            if (row["db"] != self._database.expected_database or row["role"] != self._database.expected_maintenance_role
                or row["original_role"] != self._database.expected_maintenance_role or row["readonly"] != "off"
                or row["relkind"] != "r" or row["can_read"] is not (row["attname"] in readable)
                or row["can_update"] is not (row["attname"] in writable)
                or any(row[name] for name in ("rolsuper", "rolbypassrls", "rolcreaterole", "rolcreatedb",
                    "owns_table", "owns_schema", "relrowsecurity", "relforcerowsecurity", "can_ddl", "broad_write"))):
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
            await connection.run_sync(lambda sync: verify_reference_history_guards(sync, require_read_only=False))

    async def execute_dispatched(self, plan: RetentionBatchPlan, lease: RetentionBatchLease,
        dispatch: RetentionBatchDispatch,
    ) -> None:
        try:
            if (self._entered or plan != self._plan or not isinstance(lease, RetentionBatchLease)
                or type(dispatch) is not RetentionBatchDispatch):
                raise ValueError()
            self._entered = True
            event = dispatch.consume(self._store, self._plan, lease)
            loop = asyncio.get_running_loop()
            remaining = (min(plan.expires_at, lease.expires_at) - datetime.now(UTC)).total_seconds()
            deadline = loop.time() + min(1.9, remaining)

            def current_window() -> None:
                require_live_event(event, lease)
                if loop.time() >= deadline:
                    raise ValueError()

            async with asyncio.timeout_at(deadline):
                await self._current(event)
                async with self._engine.connect() as connection, connection.begin():
                    await self._database_policy(connection)
                    # 同实际数据库内与原Store的主体排他写锁互斥，保持至目标提交/回滚。
                    if await connection.scalar(text("SELECT pg_catalog.pg_try_advisory_xact_lock_shared(:key)"),
                        {"key": self._store.subject_fence_key}) is not True:
                        raise ValueError()
                    await self._current(event)
                    is_session = plan.category == "auth_sessions"
                    query = text("""
                        SELECT id,tenant_id,user_id,created_at,revoked_at,revoked_by,revocation_code
                        FROM ONLY public.auth_sessions WHERE id IN :ids ORDER BY id LIMIT :limit FOR UPDATE NOWAIT
                    """) if is_session else text("""
                        SELECT id,tenant_id,credential_id,revision,provider,created_at,deleted_at
                        FROM ONLY public.model_credential_revisions WHERE id IN :ids ORDER BY id LIMIT :limit FOR UPDATE NOWAIT
                    """)
                    rows = (await connection.execute(query.bindparams(bindparam("ids", expanding=True)), {
                        "ids": [binding.locator.resource_id for binding in self._bindings],
                        "limit": len(self._bindings) + 1,
                    })).mappings().all()
                    by_id = {row["id"]: row for row in rows}
                    expected_ids = {binding.locator.resource_id for binding in self._bindings}
                    if len(rows) != len(expected_ids) or len(by_id) != len(rows) or set(by_id) != expected_ids:
                        raise ValueError()
                    observed_at = datetime.now(UTC)
                    pending: list[UUID] = []
                    for binding, reader in zip(self._bindings, self._readers, strict=True):
                        state, _ = reader._state(by_id[binding.locator.resource_id], observed_at)
                        if state == "present":
                            pending.append(binding.locator.resource_id)
                        elif state != "quarantined":
                            raise ValueError()
                    await self._current(event)
                    current_window()
                    if pending:
                        update = text("""
                            UPDATE ONLY public.auth_sessions SET revoked_at=:now,revocation_code='RETENTION_QUARANTINE'
                            WHERE id IN :ids AND tenant_id=:tenant_id AND user_id=:subject_id
                                AND revoked_at IS NULL AND revoked_by IS NULL AND revocation_code IS NULL RETURNING id
                        """) if is_session else text("""
                            UPDATE ONLY public.model_credential_revisions SET deleted_at=:now
                            WHERE id IN :ids AND tenant_id=:tenant_id AND deleted_at IS NULL RETURNING id
                        """)
                        parameters = {"ids": pending, "now": datetime.now(UTC), "tenant_id": plan.tenant_id}
                        if is_session:
                            parameters["subject_id"] = plan.subject_user_id
                        written = (await connection.execute(update.bindparams(bindparam("ids", expanding=True)), parameters)).scalars().all()
                        if len(written) != len(pending) or set(written) != set(pending):
                            raise ValueError()
                    await self._current(event)
                    current_window()  # 提交前过期回滚；退出后过期只能报告未知，不能假称回滚。
                current_window()
        except Exception:
            raise AccessRetentionExecutionUnconfirmed() from None
