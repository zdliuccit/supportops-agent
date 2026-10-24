"""知识类别固定完整计划集的显式逐批推进；不混用访问类别作业或默认注册。"""

from typing import ClassVar, Literal, Protocol
from uuid import UUID

from pydantic import Field, model_validator
from sqlalchemy.ext.asyncio import AsyncEngine

from supportops_core.retention_batch_contracts import RetentionBatchContract, RetentionBatchPlan, batch_digest
from supportops_core.retention_batch_runtime import RetentionBatchAuthority
from supportops_core.retention_batch_store import PostgresRetentionBatchStore, RetentionBatchStoreScope
from supportops_core.retention_job import (
    RetentionJobObservation, _JobBatchAuthority, _PostgresRetentionJob, require_fixed_job_sequence,
)
from supportops_core.retention_knowledge_batch import (
    MAX_SNAPSHOTS, VERIFIER_ID, KnowledgeRetentionAuthority, KnowledgeRetentionBinding,
)
from supportops_core.retention_knowledge_executor import EXECUTOR_ID, KnowledgeRetentionExecutionAuthority
from supportops_core.retention_knowledge_propagation import KnowledgePropagationDatabase
from supportops_core.retention_knowledge_runtime import PostgresKnowledgeRetentionBatch


class KnowledgeRetentionJobPlan(RetentionBatchContract):
    """可信盘点冻结的知识完整集合；摘要本身不批准隔离共享知识。"""

    schema_version: Literal["knowledge_retention_job_plan_v1"] = "knowledge_retention_job_plan_v1"
    batches: tuple[RetentionBatchPlan, ...] = Field(min_length=1, max_length=20, repr=False)

    @model_validator(mode="after")
    def fixed_sequence(self) -> "KnowledgeRetentionJobPlan":
        require_fixed_job_sequence(self.batches, categories=frozenset({"knowledge"}),
            error_code="RETENTION_KNOWLEDGE_JOB_PLAN_INVALID")
        if any(len(batch.resource_key_digests) > MAX_SNAPSHOTS or batch.executor_id != EXECUTOR_ID
            or batch.verifier_id != VERIFIER_ID for batch in self.batches):
            raise ValueError("RETENTION_KNOWLEDGE_JOB_PLAN_INVALID")
        return self


class KnowledgeRetentionJobAuthority(Protocol):
    async def assert_current_job(self, plan: KnowledgeRetentionJobPlan, *, purpose: Literal["read", "advance"]) -> None:
        """独立确认原job完整集合/顺序/摘要、真实当前批准、政策/法律保留及全部共享依赖。

        将原主体/申请/maintenance身份、实际部署及当前共享知识维护范围绑定到此完整
        集合，拒绝子集、增补、换序或以新摘要分拆批准。个人关联不能批准整体隔离。
        read不授予推进权，advance不代替各批独立ledger/目标写/目标复核批准；无默认放行。
        """
        ...


class KnowledgeRetentionJobStatus(RetentionJobObservation):
    _invalid_code: ClassVar[str] = "RETENTION_KNOWLEDGE_JOB_STATUS_INVALID"
    schema_version: Literal["knowledge_retention_job_status_v1"] = "knowledge_retention_job_status_v1"


class KnowledgeRetentionJobUnavailable(RuntimeError):
    code = "RETENTION_KNOWLEDGE_JOB_DEPENDENCIES_UNAVAILABLE"


class KnowledgeRetentionJobUnconfirmed(RuntimeError):
    code = "RETENTION_KNOWLEDGE_JOB_UNCONFIRMED"


class PostgresKnowledgeRetentionJob(_PostgresRetentionJob[KnowledgeRetentionJobStatus]):
    """固定1–20个知识批次，单次最多推进一个新批；失败/取消关闭本实例推进。"""

    def __init__(self, ledger_engine: AsyncEngine, ledger_scope: RetentionBatchStoreScope, *,
        write_engine: AsyncEngine, write_database: KnowledgePropagationDatabase,
        verification_engine: AsyncEngine, verification_database: KnowledgePropagationDatabase,
        plan: KnowledgeRetentionJobPlan, bindings: tuple[tuple[KnowledgeRetentionBinding, ...], ...],
        job_authority: KnowledgeRetentionJobAuthority | None = None,
        ledger_authority: RetentionBatchAuthority | None = None,
        execution_authority: KnowledgeRetentionExecutionAuthority | None = None,
        verification_authority: KnowledgeRetentionAuthority | None = None,
    ) -> None:
        if (not isinstance(plan, KnowledgeRetentionJobPlan) or type(bindings) is not tuple
            or len(bindings) != len(plan.batches) or job_authority is None or ledger_authority is None
            or execution_authority is None or verification_authority is None):
            raise KnowledgeRetentionJobUnavailable()
        try:
            self._plan = KnowledgeRetentionJobPlan.model_validate_json(plan.model_dump_json())
            self._authority = _JobBatchAuthority(self._plan, self._plan.batches, job_authority, ledger_authority,
                unconfirmed_error=KnowledgeRetentionJobUnconfirmed)
            batches = []
            resource_ids: set[UUID] = set()
            for batch, group in zip(self._plan.batches, bindings, strict=True):
                if type(group) is not tuple:
                    raise ValueError()
                runtime = PostgresKnowledgeRetentionBatch(ledger_engine, ledger_scope,
                    write_engine=write_engine, write_database=write_database,
                    verification_engine=verification_engine, verification_database=verification_database,
                    plan=batch, bindings=group, ledger_authority=self._authority,
                    execution_authority=execution_authority, verification_authority=verification_authority)
                ids = {binding.propagation.locator.resource_id for binding in group}
                if resource_ids.intersection(ids):
                    raise ValueError()
                resource_ids.update(ids)
                batches.append(runtime)
            store = PostgresRetentionBatchStore(ledger_engine, ledger_scope, authority=self._authority)
        except Exception:
            raise KnowledgeRetentionJobUnavailable() from None
        super().__init__(plans=self._plan.batches, batches=tuple(batches), store=store,
            manifest_digest=batch_digest(self._plan), status_type=KnowledgeRetentionJobStatus,
            unconfirmed_error=KnowledgeRetentionJobUnconfirmed)
