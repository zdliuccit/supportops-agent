"""显式独立维护接线：固定单批次执行、只读复核与原租约恢复；不默认注册。"""

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncEngine

from supportops_core.retention_access_batch import (
    AccessRetentionAuthority, PostgresAccessRetentionVerifier, _prepare_access_scope,
)
from supportops_core.retention_access_executor import (
    EXECUTOR_ID, AccessRetentionExecutionAuthority, PostgresAccessRetentionExecutor,
)
from supportops_core.retention_access_propagation import AccessPropagationBinding, AccessPropagationDatabase
from supportops_core.retention_batch_contracts import RetentionBatchPlan, RetentionBatchReceipt
from supportops_core.retention_batch_events import RetentionBatchInspection
from supportops_core.retention_batch_runtime import (
    RetentionBatchAuthority, execute_retention_batch, reconcile_expired_retention_batch, verify_retention_batch,
)
from supportops_core.retention_batch_store import PostgresRetentionBatchStore, RetentionBatchStoreScope


class AccessRetentionRuntimeUnavailable(RuntimeError):
    code = "RETENTION_ACCESS_RUNTIME_DEPENDENCIES_UNAVAILABLE"


class AccessRetentionRuntimeUnconfirmed(RuntimeError):
    code = "RETENTION_ACCESS_RUNTIME_UNCONFIRMED"


class PostgresAccessRetentionReview:
    """仅原批次状态/复核/恢复组合；不需要或持有目标写引擎、写批准及执行器。"""

    def __init__(self, ledger_engine: AsyncEngine, ledger_scope: RetentionBatchStoreScope, *,
        verification_engine: AsyncEngine, verification_database: AccessPropagationDatabase,
        plan: RetentionBatchPlan, bindings: tuple[AccessPropagationBinding, ...],
        ledger_authority: RetentionBatchAuthority | None = None,
        verification_authority: AccessRetentionAuthority | None = None,
    ) -> None:
        if (not isinstance(ledger_engine, AsyncEngine) or ledger_engine.dialect.name != "postgresql"
            or not isinstance(verification_engine, AsyncEngine) or verification_engine.dialect.name != "postgresql"
            or ledger_engine is verification_engine or not isinstance(ledger_scope, RetentionBatchStoreScope)
            or not isinstance(verification_database, AccessPropagationDatabase) or not isinstance(plan, RetentionBatchPlan)
            or ledger_authority is None or verification_authority is None
            or ledger_scope.expected_database != verification_database.expected_database
            or ledger_scope.expected_maintenance_role == verification_database.expected_maintenance_role
            or ledger_scope.tenant_id != plan.tenant_id or ledger_scope.subject_user_id != plan.subject_user_id
            or ledger_scope.ledger_id != plan.ledger_id or ledger_scope.maintenance_identity != plan.maintenance_identity):
            raise AccessRetentionRuntimeUnavailable()
        # 构造只有固定元数据/依赖检查，不连接数据库、查询批准、签发恢复许可或启动任务。
        self._plan, self._verification_database, self._bindings, _ = _prepare_access_scope(
            plan, verification_database, bindings)
        self._ledger_scope = RetentionBatchStoreScope.model_validate_json(ledger_scope.model_dump_json())
        self._ledger_engine, self._verification_engine = ledger_engine, verification_engine
        self._ledger_authority = ledger_authority
        self._store = PostgresRetentionBatchStore(ledger_engine, self._ledger_scope, authority=ledger_authority)
        self._verifier = PostgresAccessRetentionVerifier(verification_engine, self._verification_database,
            plan=self._plan, bindings=self._bindings, store=self._store, authority=verification_authority)
        self._busy = False

    @contextmanager
    def _operation(self) -> Iterator[None]:
        if self._busy:
            raise AccessRetentionRuntimeUnconfirmed()
        self._busy = True
        try:
            yield
        finally:
            self._busy = False

    def _live_plan(self) -> None:
        if not self._plan.created_at <= datetime.now(UTC) < self._plan.expires_at:
            raise AccessRetentionRuntimeUnconfirmed()

    async def read_status(self) -> RetentionBatchInspection | None:
        """独立当前read批准下读取原历史；原计划到期不自动撤销或授予读取权限。"""
        try:
            return await self._store.read_status(self._plan)
        except Exception:
            raise AccessRetentionRuntimeUnconfirmed() from None

    async def _review(self, *, expired: bool) -> RetentionBatchReceipt:
        try:
            with self._operation():
                async with asyncio.timeout(10):
                    self._live_plan()
                    lease = await self._store.read_pending_lease(self._plan)
                    if (datetime.now(UTC) >= lease.expires_at) != expired:
                        raise ValueError()
                    self._live_plan()
                    if expired:
                        # 原协调器独立签发并持久消费新恢复permit，只读复核，不传执行器。
                        return await reconcile_expired_retention_batch(self._plan, lease, store=self._store,
                            authority=self._ledger_authority, verifier=self._verifier)
                    return await verify_retention_batch(self._plan, lease, owner_id=lease.owner_id,
                        store=self._store, authority=self._ledger_authority, verifier=self._verifier)
        except Exception:
            raise AccessRetentionRuntimeUnconfirmed() from None

    async def verify(self) -> RetentionBatchReceipt:
        """仅原有效租约内复核；租约来自原账本，不接受调用者上传的owner/代次。"""
        return await self._review(expired=False)

    async def reconcile(self) -> RetentionBatchReceipt:
        """仅原已派发且过期租约的受控只读恢复，不重新调用目标操作。"""
        return await self._review(expired=True)


class PostgresAccessRetentionBatch(PostgresAccessRetentionReview):
    """可信维护集成显式构造的一次执行组合；恢复可另建不持目标写能力的Review。"""

    def __init__(self, ledger_engine: AsyncEngine, ledger_scope: RetentionBatchStoreScope, *,
        write_engine: AsyncEngine, write_database: AccessPropagationDatabase,
        verification_engine: AsyncEngine, verification_database: AccessPropagationDatabase,
        plan: RetentionBatchPlan, bindings: tuple[AccessPropagationBinding, ...],
        ledger_authority: RetentionBatchAuthority | None = None,
        execution_authority: AccessRetentionExecutionAuthority | None = None,
        verification_authority: AccessRetentionAuthority | None = None,
    ) -> None:
        if (not isinstance(write_engine, AsyncEngine) or write_engine.dialect.name != "postgresql"
            or write_engine is ledger_engine or write_engine is verification_engine
            or not isinstance(write_database, AccessPropagationDatabase)
            or not isinstance(ledger_scope, RetentionBatchStoreScope)
            or not isinstance(verification_database, AccessPropagationDatabase)
            or not isinstance(plan, RetentionBatchPlan) or plan.executor_id != EXECUTOR_ID
            or execution_authority is None
            or write_database.expected_database != ledger_scope.expected_database
            or write_database.expected_maintenance_role in {
                ledger_scope.expected_maintenance_role, verification_database.expected_maintenance_role}):
            raise AccessRetentionRuntimeUnavailable()
        super().__init__(ledger_engine, ledger_scope, verification_engine=verification_engine,
            verification_database=verification_database, plan=plan, bindings=bindings,
            ledger_authority=ledger_authority, verification_authority=verification_authority)
        self._executor = PostgresAccessRetentionExecutor(write_engine, write_database,
            verification_database=self._verification_database, plan=self._plan, bindings=self._bindings,
            store=self._store, authority=execution_authority)
        self._execution_entered = False

    async def execute(self, *, owner_id: UUID) -> RetentionBatchReceipt:
        """每实例最多一次执行尝试；失败/取消/未知只读原状态或显式复核，不自动重试。"""
        try:
            if not isinstance(owner_id, UUID):
                raise ValueError()
            with self._operation():
                if self._execution_entered:
                    raise ValueError()
                self._execution_entered = True
                self._live_plan()
                return await execute_retention_batch(self._plan, owner_id=owner_id, store=self._store,
                    authority=self._ledger_authority, executor=self._executor, verifier=self._verifier)
        except Exception:
            raise AccessRetentionRuntimeUnconfirmed() from None
