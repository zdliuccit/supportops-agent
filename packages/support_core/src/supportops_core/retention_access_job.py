"""固定计划集合的显式逐批推进；不扫描、不循环派发、不宣称整个擦除作业完成。"""

from typing import ClassVar, Literal, Protocol
from uuid import UUID

from pydantic import Field, model_validator
from sqlalchemy.ext.asyncio import AsyncEngine

from supportops_core.retention_access_batch import AccessRetentionAuthority
from supportops_core.retention_access_executor import AccessRetentionExecutionAuthority
from supportops_core.retention_access_propagation import AccessPropagationBinding, AccessPropagationDatabase
from supportops_core.retention_access_runtime import PostgresAccessRetentionBatch
from supportops_core.retention_batch_contracts import (
    RetentionBatchContract, RetentionBatchPlan, batch_digest,
)
from supportops_core.retention_batch_runtime import RetentionBatchAuthority
from supportops_core.retention_batch_store import PostgresRetentionBatchStore, RetentionBatchStoreScope
from supportops_core.retention_job import (
    RetentionJobObservation, _JobBatchAuthority, _PostgresRetentionJob, require_fixed_job_sequence,
)


class AccessRetentionJobPlan(RetentionBatchContract):
    """可信盘点冻结的完整计划集合；完整性和真实批准仍由独立authority确认。"""

    schema_version: Literal["access_retention_job_plan_v1"] = "access_retention_job_plan_v1"
    batches: tuple[RetentionBatchPlan, ...] = Field(min_length=1, max_length=20, repr=False)

    @model_validator(mode="after")
    def fixed_sequence(self) -> "AccessRetentionJobPlan":
        require_fixed_job_sequence(self.batches, categories=frozenset({"auth_sessions", "credentials"}),
            error_code="RETENTION_ACCESS_JOB_PLAN_INVALID")
        return self


class AccessRetentionJobAuthority(Protocol):
    async def assert_current_job(self, plan: AccessRetentionJobPlan, *, purpose: Literal["read", "advance"]) -> None:
        """独立确认原job固定完整集合/顺序/摘要，拒绝子集、增补、换序或分拆批准。

        须将真实当前批准、原主体/申请/维护身份、政策/法律保留、完整共享依赖及实际
        目标部署与本集合绑定；摘要或构造本身不是许可。read不授予推进或目标写权。
        不提供默认放行，不替代逐批ledger/执行/复核authority的独立检查。
        """
        ...


class AccessRetentionJobStatus(RetentionJobObservation):
    _invalid_code: ClassVar[str] = "RETENTION_ACCESS_JOB_STATUS_INVALID"
    schema_version: Literal["access_retention_job_status_v1"] = "access_retention_job_status_v1"


class AccessRetentionJobUnavailable(RuntimeError):
    code = "RETENTION_ACCESS_JOB_DEPENDENCIES_UNAVAILABLE"


class AccessRetentionJobUnconfirmed(RuntimeError):
    code = "RETENTION_ACCESS_JOB_UNCONFIRMED"


class PostgresAccessRetentionJob(_PostgresRetentionJob[AccessRetentionJobStatus]):
    """显式固定1–20批；每次advance最多调用一个全新批次，不创建后台任务。"""

    def __init__(self, ledger_engine: AsyncEngine, ledger_scope: RetentionBatchStoreScope, *,
        write_engine: AsyncEngine, write_database: AccessPropagationDatabase,
        verification_engine: AsyncEngine, verification_database: AccessPropagationDatabase,
        plan: AccessRetentionJobPlan, bindings: tuple[tuple[AccessPropagationBinding, ...], ...],
        job_authority: AccessRetentionJobAuthority | None = None,
        ledger_authority: RetentionBatchAuthority | None = None,
        execution_authority: AccessRetentionExecutionAuthority | None = None,
        verification_authority: AccessRetentionAuthority | None = None,
    ) -> None:
        if (not isinstance(plan, AccessRetentionJobPlan) or type(bindings) is not tuple
            or len(bindings) != len(plan.batches) or job_authority is None or ledger_authority is None
            or execution_authority is None or verification_authority is None):
            raise AccessRetentionJobUnavailable()
        try:
            self._plan = AccessRetentionJobPlan.model_validate_json(plan.model_dump_json())
            self._authority = _JobBatchAuthority(self._plan, self._plan.batches, job_authority, ledger_authority,
                unconfirmed_error=AccessRetentionJobUnconfirmed)
            batches = []
            resource_ids: set[UUID] = set()
            for batch, group in zip(self._plan.batches, bindings, strict=True):
                if type(group) is not tuple:
                    raise ValueError()
                runtime = PostgresAccessRetentionBatch(ledger_engine, ledger_scope,
                    write_engine=write_engine, write_database=write_database,
                    verification_engine=verification_engine, verification_database=verification_database,
                    plan=batch, bindings=group, ledger_authority=self._authority,
                    execution_authority=execution_authority, verification_authority=verification_authority)
                ids = {binding.locator.resource_id for binding in group}
                if resource_ids.intersection(ids):
                    raise ValueError()
                resource_ids.update(ids)
                batches.append(runtime)
            self._batches = tuple(batches)
            self._store = PostgresRetentionBatchStore(ledger_engine, ledger_scope, authority=self._authority)
        except Exception:
            raise AccessRetentionJobUnavailable() from None
        super().__init__(plans=self._plan.batches, batches=self._batches, store=self._store,
            manifest_digest=batch_digest(self._plan), status_type=AccessRetentionJobStatus,
            unconfirmed_error=AccessRetentionJobUnconfirmed)
