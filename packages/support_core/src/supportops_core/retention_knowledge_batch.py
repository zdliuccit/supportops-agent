"""知识快照与关键词索引的独立批次复核；不隔离目标或生成删除事实。"""

import asyncio
import hashlib
from datetime import UTC, datetime
from typing import Literal, Protocol
from uuid import UUID

from pydantic import AwareDatetime, Field
from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncEngine

from supportops_core.retention_batch_contracts import (
    RetentionBatchContract, RetentionBatchLease, RetentionBatchPlan, RetentionBatchVerification,
    RetentionResourceVerification, batch_digest, require_live_recovery_permit,
)
from supportops_core.retention_batch_events import RetentionBatchEvent, require_live_event
from supportops_core.retention_batch_store import PostgresRetentionBatchStore
from supportops_core.retention_knowledge_propagation import (
    MAX_CHUNKS, MAX_GENERATIONS, KnowledgePropagationBinding, KnowledgePropagationDatabase,
    _KNOWLEDGE_METADATA_QUERY, _KnowledgeMetadataReader,
)
from supportops_core.retention_tombstone_snapshot import _canonical

VERIFIER_ID = "knowledge_quarantine_batch_v1"
MAX_SNAPSHOTS = 10


class KnowledgeRetentionBinding(RetentionBatchContract):
    """固定原快照创建时间与部署读取投影；主体关联不构成共享知识维护许可。"""

    schema_version: Literal["knowledge_retention_binding_v1"] = "knowledge_retention_binding_v1"
    propagation: KnowledgePropagationBinding = Field(repr=False)
    snapshot_created_at: AwareDatetime


class KnowledgeRetentionAuthority(Protocol):
    async def assert_current_scope(self, event: RetentionBatchEvent, database: KnowledgePropagationDatabase,
        bindings: tuple[KnowledgeRetentionBinding, ...],
    ) -> None:
        """独立核对原完整知识批次、政策/批准/法律保留/固定引用与全部共享依赖。

        须确认原主体/申请/maintenance身份、快照原归属、创建截止、固定文档/版本/摘要、
        实际目标实例及当前部署的keyword_v1读取路径；个人关联或管理员角色不能批准
        整体隔离共享快照。普通或原恢复验证必须与当前原账本入口匹配，不授予目标写权。
        """
        ...


class KnowledgeRetentionUnconfirmed(RuntimeError):
    code = "RETENTION_KNOWLEDGE_BATCH_VERIFICATION_UNCONFIRMED"


def _prepare_knowledge_scope(plan: RetentionBatchPlan, database: KnowledgePropagationDatabase,
    bindings: tuple[KnowledgeRetentionBinding, ...],
) -> tuple[RetentionBatchPlan, KnowledgePropagationDatabase, tuple[KnowledgeRetentionBinding, ...], tuple[_KnowledgeMetadataReader, ...]]:
    if (not isinstance(plan, RetentionBatchPlan) or not isinstance(database, KnowledgePropagationDatabase)
        or plan.category != "knowledge" or plan.operation != "quarantined" or plan.verifier_id != VERIFIER_ID
        or type(bindings) is not tuple or not 1 <= len(bindings) <= MAX_SNAPSHOTS
        or any(type(binding) is not KnowledgeRetentionBinding for binding in bindings)):
        raise KnowledgeRetentionUnconfirmed()
    copied_plan = RetentionBatchPlan.model_validate_json(plan.model_dump_json())
    copied_database = KnowledgePropagationDatabase.model_validate_json(database.model_dump_json())
    copied = tuple(KnowledgeRetentionBinding.model_validate_json(binding.model_dump_json()) for binding in bindings)
    indexed: dict[str, KnowledgeRetentionBinding] = {}
    ids: set[UUID] = set()
    for binding in copied:
        locator = binding.propagation.locator
        digest = hashlib.sha256(_canonical(locator.model_dump(mode="json"))).hexdigest()
        if (digest in indexed or locator.resource_id in ids or locator.tenant_id != plan.tenant_id
            or locator.subject_user_id != plan.subject_user_id or binding.snapshot_created_at > plan.cutoff_at):
            raise KnowledgeRetentionUnconfirmed()
        ids.add(locator.resource_id)
        indexed[digest] = binding
    if tuple(sorted(indexed)) != plan.resource_key_digests:
        raise KnowledgeRetentionUnconfirmed()
    ordered = tuple(indexed[digest] for digest in plan.resource_key_digests)
    readers = tuple(_KnowledgeMetadataReader(copied_database, binding.propagation) for binding in ordered)
    return copied_plan, copied_database, ordered, readers


class PostgresKnowledgeRetentionVerifier:
    """仅消费原持久验证入口；只读目标元数据，缺少独立当前批准默认拒绝。"""

    verifier_id = VERIFIER_ID

    def __init__(self, engine: AsyncEngine, database: KnowledgePropagationDatabase, *,
        plan: RetentionBatchPlan, bindings: tuple[KnowledgeRetentionBinding, ...],
        store: PostgresRetentionBatchStore | None = None, authority: KnowledgeRetentionAuthority | None = None,
    ) -> None:
        if (not isinstance(engine, AsyncEngine) or engine.dialect.name != "postgresql"
            or not isinstance(database, KnowledgePropagationDatabase) or not isinstance(store, PostgresRetentionBatchStore)
            or store.expected_database != database.expected_database or authority is None):
            raise KnowledgeRetentionUnconfirmed()
        self._plan, self._database, self._bindings, self._readers = _prepare_knowledge_scope(plan, database, bindings)
        self._engine, self._store, self._authority = engine, store, authority
        self._entered: set[UUID] = set()

    @staticmethod
    def _window(event: RetentionBatchEvent) -> None:
        if not event.plan.created_at <= datetime.now(UTC) < event.plan.expires_at:
            raise ValueError()
        if event.recovery_permit is None:
            require_live_event(event, event.lease)
        else:
            require_live_recovery_permit(event.recovery_permit)

    async def _current(self, lease: RetentionBatchLease, verification_id: UUID) -> RetentionBatchEvent:
        async with asyncio.timeout(0.8):
            event = await self._store.read_current_verification(self._plan, lease, verification_id=verification_id)
            self._window(event)
            await self._authority.assert_current_scope(event, self._database, self._bindings)
            self._window(event)
        return event

    async def observe(self, plan: RetentionBatchPlan, lease: RetentionBatchLease, *,
        verification_id: UUID,
    ) -> RetentionBatchVerification:
        try:
            if (not isinstance(plan, RetentionBatchPlan) or plan != self._plan
                or not isinstance(lease, RetentionBatchLease) or not isinstance(verification_id, UUID)
                or verification_id in self._entered or len(self._entered) >= plan.max_verification_attempts):
                raise ValueError()
            self._entered.add(verification_id)  # 失败/取消同样消耗本实例的验证进入机会。
            deadline = asyncio.get_running_loop().time() + 1.9
            async with asyncio.timeout(1.9):
                event = await self._current(lease, verification_id)
                async with self._engine.connect() as connection, connection.begin():
                    await self._readers[0]._database(connection)
                    async with asyncio.timeout(0.5):
                        await self._authority.assert_current_scope(event, self._database, self._bindings)
                    self._window(event)
                    # 全部快照/各代次/分块计数取自同一条SQL快照；不按预期租户预过滤错归属。
                    query = text(_KNOWLEDGE_METADATA_QUERY +
                        " WHERE s.id IN :snapshot_ids ORDER BY s.id LIMIT :snapshot_limit").bindparams(
                            bindparam("snapshot_ids", expanding=True))
                    rows = (await connection.execute(query, {
                        "snapshot_ids": [binding.propagation.locator.resource_id for binding in self._bindings],
                        "snapshot_limit": len(self._bindings) + 1, "generation_limit": MAX_GENERATIONS + 1,
                    })).mappings().all()
                    observed_at = datetime.now(UTC)
                    expected_ids = {binding.propagation.locator.resource_id for binding in self._bindings}
                    by_id = {row["id"]: row for row in rows}
                    if len(rows) > len(self._bindings) or len(by_id) != len(rows) or not set(by_id) <= expected_ids:
                        raise ValueError()
                    resources: list[RetentionResourceVerification] = []
                    generations = chunks = 0
                    for binding, reader, digest in zip(self._bindings, self._readers, plan.resource_key_digests, strict=True):
                        row = by_id.get(binding.propagation.locator.resource_id)
                        if row is not None and row["created_at"] != binding.snapshot_created_at:
                            raise ValueError()
                        state, projection = reader._project(row, observed_at, plan.cutoff_at)
                        if projection is not None:
                            generation_count, chunk_count = projection["generation_count"], projection["chunk_count"]
                            if type(generation_count) is not int or type(chunk_count) is not int:
                                raise ValueError()
                            generations += generation_count
                            chunks += chunk_count
                        # 整批总量额外有界；超限整批未确认，不截断代次或挑选已隔离部分。
                        if generations > MAX_GENERATIONS or chunks > MAX_CHUNKS:
                            raise ValueError()
                        proof = {"schema_version": "knowledge_retention_verification_evidence_v1",
                            "plan_digest": batch_digest(plan), "lease_digest": batch_digest(lease),
                            "verification_id": str(verification_id), "source_event_digest": batch_digest(event),
                            "binding_digest": batch_digest(binding), "database_digest": batch_digest(self._database),
                            "resource_key_digest": digest, "projection": projection,
                            "observed_at": observed_at.isoformat(), "erasure_verified": False}
                        resources.append(RetentionResourceVerification(resource_key_digest=digest,
                            status="verified" if state == "quarantined" else "unknown",
                            evidence_digest=hashlib.sha256(_canonical(proof)).hexdigest() if state == "quarantined" else None))
                if await self._current(lease, verification_id) != event:
                    raise ValueError()
                result = RetentionBatchVerification(plan_digest=batch_digest(plan), lease_id=lease.lease_id,
                    generation=lease.generation, verification_id=verification_id, verifier_id=self.verifier_id,
                    observed_at=observed_at, resources=tuple(resources))
            if asyncio.get_running_loop().time() > deadline:
                raise ValueError()
            self._window(event)
            return result
        except Exception:
            raise KnowledgeRetentionUnconfirmed() from None
