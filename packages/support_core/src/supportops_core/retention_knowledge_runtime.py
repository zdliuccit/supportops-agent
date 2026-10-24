"""知识批次的显式维护组合；独立Review不持有目标写引擎或执行器。"""

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncEngine

from supportops_core.retention_batch_contracts import RetentionBatchPlan, RetentionBatchReceipt
from supportops_core.retention_batch_events import RetentionBatchInspection
from supportops_core.retention_batch_runtime import (
    RetentionBatchAuthority, execute_retention_batch, reconcile_expired_retention_batch, verify_retention_batch,
)
from supportops_core.retention_batch_store import PostgresRetentionBatchStore, RetentionBatchStoreScope
from supportops_core.retention_knowledge_batch import (
    KnowledgeRetentionAuthority, KnowledgeRetentionBinding, PostgresKnowledgeRetentionVerifier, _prepare_knowledge_scope,
)
from supportops_core.retention_knowledge_executor import (
    EXECUTOR_ID, KnowledgeRetentionExecutionAuthority, PostgresKnowledgeRetentionExecutor,
)
from supportops_core.retention_knowledge_propagation import KnowledgePropagationDatabase


class KnowledgeRetentionRuntimeUnavailable(RuntimeError):
    code = "RETENTION_KNOWLEDGE_RUNTIME_DEPENDENCIES_UNAVAILABLE"


class KnowledgeRetentionRuntimeUnconfirmed(RuntimeError):
    code = "RETENTION_KNOWLEDGE_RUNTIME_UNCONFIRMED"


class PostgresKnowledgeRetentionReview:
    """固定原批次的状态、活租约复核及过期恢复；不需要目标写依赖。"""

    def __init__(self, ledger_engine: AsyncEngine, ledger_scope: RetentionBatchStoreScope, *,
        verification_engine: AsyncEngine, verification_database: KnowledgePropagationDatabase,
        plan: RetentionBatchPlan, bindings: tuple[KnowledgeRetentionBinding, ...],
        ledger_authority: RetentionBatchAuthority | None = None,
        verification_authority: KnowledgeRetentionAuthority | None = None,
    ) -> None:
        if (not isinstance(ledger_engine, AsyncEngine) or ledger_engine.dialect.name != "postgresql"
            or not isinstance(verification_engine, AsyncEngine) or verification_engine.dialect.name != "postgresql"
            or ledger_engine is verification_engine or not isinstance(ledger_scope, RetentionBatchStoreScope)
            or not isinstance(verification_database, KnowledgePropagationDatabase) or not isinstance(plan, RetentionBatchPlan)
            or ledger_authority is None or verification_authority is None
            or ledger_scope.expected_database != verification_database.expected_database
            or ledger_scope.expected_maintenance_role == verification_database.expected_maintenance_role
            or ledger_scope.tenant_id != plan.tenant_id or ledger_scope.subject_user_id != plan.subject_user_id
            or ledger_scope.ledger_id != plan.ledger_id or ledger_scope.maintenance_identity != plan.maintenance_identity):
            raise KnowledgeRetentionRuntimeUnavailable()
        try:
            self._plan, self._verification_database, self._bindings, _ = _prepare_knowledge_scope(
                plan, verification_database, bindings)
            scope = RetentionBatchStoreScope.model_validate_json(ledger_scope.model_dump_json())
            self._store = PostgresRetentionBatchStore(ledger_engine, scope, authority=ledger_authority)
            self._verifier = PostgresKnowledgeRetentionVerifier(verification_engine, self._verification_database,
                plan=self._plan, bindings=self._bindings, store=self._store, authority=verification_authority)
        except Exception:
            raise KnowledgeRetentionRuntimeUnavailable() from None
        self._ledger_authority = ledger_authority
        self._busy = False

    @contextmanager
    def _operation(self) -> Iterator[None]:
        if self._busy:
            raise KnowledgeRetentionRuntimeUnconfirmed()
        self._busy = True
        try:
            yield
        finally:
            self._busy = False

    def _live_plan(self) -> None:
        if not self._plan.created_at <= datetime.now(UTC) < self._plan.expires_at:
            raise KnowledgeRetentionRuntimeUnconfirmed()

    async def read_status(self) -> RetentionBatchInspection | None:
        """仅原当前read许可下查历史，计划到期不自动授予或撤销历史读取权限。"""
        try:
            return await self._store.read_status(self._plan)
        except Exception:
            raise KnowledgeRetentionRuntimeUnconfirmed() from None

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
                        return await reconcile_expired_retention_batch(self._plan, lease, store=self._store,
                            authority=self._ledger_authority, verifier=self._verifier)
                    return await verify_retention_batch(self._plan, lease, owner_id=lease.owner_id,
                        store=self._store, authority=self._ledger_authority, verifier=self._verifier)
        except Exception:
            raise KnowledgeRetentionRuntimeUnconfirmed() from None

    async def verify(self) -> RetentionBatchReceipt:
        """从原账本取得真实活租约，不接受调用方上传owner/代次或重建派发交接。"""
        return await self._review(expired=False)

    async def reconcile(self) -> RetentionBatchReceipt:
        """原过期派发仅凭新独立恢复许可复核，不重新进入目标写适配。"""
        return await self._review(expired=True)


class PostgresKnowledgeRetentionBatch(PostgresKnowledgeRetentionReview):
    """显式固定一个原共享知识批次；构造不连接DB、查询批准或启动任务。"""

    def __init__(self, ledger_engine: AsyncEngine, ledger_scope: RetentionBatchStoreScope, *,
        write_engine: AsyncEngine, write_database: KnowledgePropagationDatabase,
        verification_engine: AsyncEngine, verification_database: KnowledgePropagationDatabase,
        plan: RetentionBatchPlan, bindings: tuple[KnowledgeRetentionBinding, ...],
        ledger_authority: RetentionBatchAuthority | None = None,
        execution_authority: KnowledgeRetentionExecutionAuthority | None = None,
        verification_authority: KnowledgeRetentionAuthority | None = None,
    ) -> None:
        if (not isinstance(write_engine, AsyncEngine) or write_engine.dialect.name != "postgresql"
            or write_engine is ledger_engine or write_engine is verification_engine
            or not isinstance(write_database, KnowledgePropagationDatabase)
            or not isinstance(ledger_scope, RetentionBatchStoreScope)
            or not isinstance(verification_database, KnowledgePropagationDatabase)
            or not isinstance(plan, RetentionBatchPlan) or plan.executor_id != EXECUTOR_ID
            or execution_authority is None or write_database.expected_database != ledger_scope.expected_database
            or write_database.expected_maintenance_role in {
                ledger_scope.expected_maintenance_role, verification_database.expected_maintenance_role}):
            raise KnowledgeRetentionRuntimeUnavailable()
        super().__init__(ledger_engine, ledger_scope, verification_engine=verification_engine,
            verification_database=verification_database, plan=plan, bindings=bindings,
            ledger_authority=ledger_authority, verification_authority=verification_authority)
        try:
            self._executor = PostgresKnowledgeRetentionExecutor(write_engine, write_database,
                verification_database=self._verification_database, plan=self._plan, bindings=self._bindings,
                store=self._store, authority=execution_authority)
        except Exception:
            raise KnowledgeRetentionRuntimeUnavailable() from None
        self._execution_entered = False

    async def execute(self, *, owner_id: UUID) -> RetentionBatchReceipt:
        """本实例一次进入；取消/失败/未知不重置，只查询原状态或另行显式复核。"""
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
            raise KnowledgeRetentionRuntimeUnconfirmed() from None
