"""原一次派发交接下的知识快照/关键词代次隔离；不删除正文或分块。"""

import asyncio
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from supportops_core.knowledge_quarantine_policy import require_knowledge_quarantine_write_role
from supportops_core.retention_batch_contracts import RetentionBatchLease, RetentionBatchPlan
from supportops_core.retention_batch_events import RetentionBatchEvent, require_live_event
from supportops_core.retention_batch_store import PostgresRetentionBatchStore
from supportops_core.retention_dispatch import RetentionBatchDispatch
from supportops_core.retention_knowledge_batch import KnowledgeRetentionBinding, _prepare_knowledge_scope
from supportops_core.retention_knowledge_propagation import MAX_CHUNKS, MAX_GENERATIONS, KnowledgePropagationDatabase

EXECUTOR_ID = "knowledge_quarantine_executor_v1"


class KnowledgeRetentionExecutionAuthority(Protocol):
    async def assert_current_execution(self, event: RetentionBatchEvent, write_database: KnowledgePropagationDatabase,
        verification_database: KnowledgePropagationDatabase, bindings: tuple[KnowledgeRetentionBinding, ...],
    ) -> None:
        """独立确认原共享快照整体隔离批准、政策/法律保留及全部固定引用/在途依赖。

        须从可信部署配置确认原ledger/写/复核确在同一实际数据库实例，三份维护身份与
        最小权限独立，原完整盘点及当前keyword_v1读取路径成立；库名相同不足为证。
        个人关联、管理角色或影响预览不授权停用共享知识。目标隔离只允许首次填写原
        快照deleted_at并将其当前关键词代次失效，不授予正文/分块/版本/文档写入、物理
        擦除、未来索引调度或副本处理权；并发索引构建及外部读取须按真实维护方案协调。
        """
        ...


class KnowledgeRetentionExecutionUnconfirmed(RuntimeError):
    code = "RETENTION_KNOWLEDGE_EXECUTION_UNCONFIRMED"


class PostgresKnowledgeRetentionExecutor:
    """只接收本次已提交交接；历史DTO或无交接的旧执行协议不能启动目标写。"""

    executor_id = EXECUTOR_ID

    def __init__(self, engine: AsyncEngine, database: KnowledgePropagationDatabase, *,
        verification_database: KnowledgePropagationDatabase, plan: RetentionBatchPlan,
        bindings: tuple[KnowledgeRetentionBinding, ...], store: PostgresRetentionBatchStore | None = None,
        authority: KnowledgeRetentionExecutionAuthority | None = None,
    ) -> None:
        if (not isinstance(engine, AsyncEngine) or engine.dialect.name != "postgresql"
            or not isinstance(database, KnowledgePropagationDatabase)
            or not isinstance(verification_database, KnowledgePropagationDatabase)
            or not isinstance(plan, RetentionBatchPlan) or plan.executor_id != self.executor_id
            or not isinstance(store, PostgresRetentionBatchStore) or authority is None
            or database.expected_database != verification_database.expected_database
            or database.expected_database != store.expected_database
            or database.expected_maintenance_role == verification_database.expected_maintenance_role):
            raise KnowledgeRetentionExecutionUnconfirmed()
        try:
            self._plan, self._verification_database, self._bindings, _ = _prepare_knowledge_scope(
                plan, verification_database, bindings)
            self._database = KnowledgePropagationDatabase.model_validate_json(database.model_dump_json())
        except Exception:
            raise KnowledgeRetentionExecutionUnconfirmed() from None
        self._engine, self._store, self._authority = engine, store, authority
        self._entered = False

    async def execute_once(self, plan: RetentionBatchPlan, lease: RetentionBatchLease) -> None:
        raise KnowledgeRetentionExecutionUnconfirmed()

    async def _current(self, event: RetentionBatchEvent) -> None:
        require_live_event(event, event.lease)
        async with asyncio.timeout(0.7):
            if await self._store.read_current_dispatch(self._plan, event.lease) != event:
                raise ValueError()
            await self._authority.assert_current_execution(event, self._database,
                self._verification_database, self._bindings)
        require_live_event(event, event.lease)

    async def _database_policy(self, connection: AsyncConnection) -> None:
        await require_knowledge_quarantine_write_role(connection, self._database,
            statement_timeout="100ms", lock_timeout="50ms")
        # 新写入口只容许这两张原普通表的明确列能力；拒绝其他schema和继承带来的扩大。
        excess = await connection.scalar(text("""
            SELECT EXISTS(SELECT 1 FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
                WHERE n.nspname NOT IN ('pg_catalog','information_schema') AND n.nspname !~ '^pg_'
                AND c.relkind IN ('r','p','v','m','f')
                AND NOT (n.nspname='public' AND c.relname IN ('knowledge_snapshots','knowledge_index_generations'))
                AND (has_table_privilege(current_user,c.oid,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
                    OR has_any_column_privilege(current_user,c.oid,'SELECT,INSERT,UPDATE,REFERENCES')))
                OR EXISTS(SELECT 1 FROM pg_catalog.pg_namespace n
                    WHERE n.nspname NOT IN ('pg_catalog','information_schema') AND n.nspname !~ '^pg_'
                    AND (n.nspowner=(SELECT oid FROM pg_catalog.pg_roles WHERE rolname=current_user)
                        OR has_schema_privilege(current_user,n.oid,'CREATE')))
                OR EXISTS(SELECT 1 FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
                    WHERE n.nspname NOT IN ('pg_catalog','information_schema') AND n.nspname !~ '^pg_'
                    AND p.proowner=(SELECT oid FROM pg_catalog.pg_roles WHERE rolname=current_user))
                OR EXISTS(SELECT 1 FROM pg_catalog.pg_inherits i
                    JOIN pg_catalog.pg_class c ON c.oid IN (i.inhrelid,i.inhparent)
                    JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
                    WHERE n.nspname='public' AND c.relname IN
                        ('knowledge_snapshots','knowledge_index_generations','knowledge_chunks'))
        """))
        if excess is not False:
            raise ValueError()

    async def _apply(self, connection: AsyncConnection, event: RetentionBatchEvent) -> None:
        expected = {binding.propagation.locator.resource_id: binding for binding in self._bindings}
        snapshot_ids = sorted(expected)
        rows = (await connection.execute(text("""
            SELECT id,tenant_id,document_id,version_id,content_digest,created_at,deleted_at
            FROM ONLY public.knowledge_snapshots WHERE id IN :ids ORDER BY id LIMIT :limit FOR UPDATE NOWAIT
        """).bindparams(bindparam("ids", expanding=True)), {
            "ids": snapshot_ids, "limit": len(snapshot_ids) + 1,
        })).mappings().all()
        by_id = {row["id"]: row for row in rows}
        if len(rows) != len(expected) or len(by_id) != len(rows) or set(by_id) != set(expected):
            raise ValueError()
        now = datetime.now(UTC)
        pending_snapshots = []
        for row in rows:
            binding = expected[row["id"]]
            original = binding.propagation
            created_at, deleted_at = row["created_at"], row["deleted_at"]
            if (row["tenant_id"] != self._plan.tenant_id or row["document_id"] != original.document_id
                or row["version_id"] != original.version_id or row["content_digest"] != original.content_digest
                or not isinstance(created_at, datetime) or created_at.tzinfo is None
                or created_at != binding.snapshot_created_at or created_at > self._plan.cutoff_at
                or deleted_at is not None and (not isinstance(deleted_at, datetime) or deleted_at.tzinfo is None
                    or not created_at <= deleted_at <= now)):
                raise ValueError()
            if deleted_at is None:
                pending_snapshots.append(row["id"])
        generations = (await connection.execute(text("""
            SELECT id,tenant_id,document_id,snapshot_id,generation_number,algorithm_version,content_digest,status,chunk_count
            FROM ONLY public.knowledge_index_generations WHERE snapshot_id IN :ids
            ORDER BY id LIMIT :limit FOR UPDATE NOWAIT
        """).bindparams(bindparam("ids", expanding=True)), {
            "ids": snapshot_ids, "limit": MAX_GENERATIONS + 1,
        })).mappings().all()
        if len(generations) > MAX_GENERATIONS or len({row["id"] for row in generations}) != len(generations):
            raise ValueError()
        chunks = 0
        pending_generations = []
        for row in generations:
            if row["snapshot_id"] not in expected:
                raise ValueError()
            original = expected[row["snapshot_id"]].propagation
            if (row["tenant_id"] != self._plan.tenant_id or row["document_id"] != original.document_id
                or row["content_digest"] != original.content_digest or row["algorithm_version"] != "keyword_v1"
                or row["status"] not in {"pending", "ready", "invalidated", "failed"}
                or type(row["generation_number"]) is not int or row["generation_number"] < 1
                or type(row["chunk_count"]) is not int or not 0 <= row["chunk_count"] <= MAX_CHUNKS):
                raise ValueError()
            chunks += row["chunk_count"]
            if chunks > MAX_CHUNKS:
                raise ValueError()
            if row["status"] != "invalidated":
                pending_generations.append(row["id"])
        # 全集锁定及元数据复核完成后才写入；原有效隔离时间和已失效代次均保留。
        await self._current(event)
        if pending_snapshots:
            written = (await connection.execute(text("""
                UPDATE ONLY public.knowledge_snapshots SET deleted_at=:now
                WHERE id IN :ids AND tenant_id=:tenant_id AND deleted_at IS NULL RETURNING id
            """).bindparams(bindparam("ids", expanding=True)), {
                "ids": pending_snapshots, "tenant_id": self._plan.tenant_id, "now": datetime.now(UTC),
            })).scalars().all()
            if len(written) != len(pending_snapshots) or set(written) != set(pending_snapshots):
                raise ValueError()
        if pending_generations:
            await self._current(event)
            written = (await connection.execute(text("""
                UPDATE ONLY public.knowledge_index_generations SET status='invalidated'
                WHERE id IN :ids AND tenant_id=:tenant_id AND snapshot_id IN :snapshot_ids
                    AND algorithm_version='keyword_v1' AND status IN ('pending','ready','failed') RETURNING id
            """).bindparams(bindparam("ids", expanding=True), bindparam("snapshot_ids", expanding=True)), {
                "ids": pending_generations, "tenant_id": self._plan.tenant_id, "snapshot_ids": snapshot_ids,
            })).scalars().all()
            if len(written) != len(pending_generations) or set(written) != set(pending_generations):
                raise ValueError()

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
                    if await connection.scalar(text("SELECT pg_catalog.pg_try_advisory_xact_lock_shared(:key)"),
                        {"key": self._store.subject_fence_key}) is not True:
                        raise ValueError()
                    await self._current(event)
                    await self._apply(connection, event)
                    await self._current(event)
                    current_window()
                current_window()  # 提交/连接退出后越界仅未知，不假称已回滚或重新派发。
        except Exception:
            raise KnowledgeRetentionExecutionUnconfirmed() from None
