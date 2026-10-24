"""固定计划集逐批推进的内部共用逻辑；无默认目标、批准或后台调度。"""

import asyncio
from datetime import UTC, datetime
from typing import ClassVar, Generic, Literal, Protocol, TypeVar
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from supportops_core.retention_batch_contracts import (
    Digest, RetentionBatchContract, RetentionBatchLease, RetentionBatchPlan,
    RetentionBatchReceipt, RetentionBatchRecoveryPermit, batch_digest,
)
from supportops_core.retention_batch_events import RetentionBatchInspection
from supportops_core.retention_batch_runtime import RetentionBatchAuthority
from supportops_core.retention_batch_store import PostgresRetentionBatchStore


def require_fixed_job_sequence(plans: tuple[RetentionBatchPlan, ...], *, categories: frozenset[str],
    error_code: str,
) -> None:
    """仅核对固定完整序号/上下文/跨批摘要；不生成计划或维护批准。"""
    excluded = {"batch_id", "sequence", "resource_key_digests"}
    context = plans[0].model_dump(exclude=excluded)
    resources: set[str] = set()
    batch_ids: set[UUID] = set()
    for sequence, plan in enumerate(plans, 1):
        if (plan.sequence != sequence or plan.batch_id in batch_ids
            or plan.category not in categories or plan.operation != "quarantined"
            or plan.model_dump(exclude=excluded) != context
            or resources.intersection(plan.resource_key_digests)):
            raise ValueError(error_code)
        resources.update(plan.resource_key_digests)
        batch_ids.add(plan.batch_id)


class RetentionJobObservation(RetentionBatchContract):
    """原完整计划集合的有界观察，不输出资源定位或目标写许可。"""

    _invalid_code: ClassVar[str] = "RETENTION_JOB_STATUS_INVALID"

    job_id: UUID
    manifest_digest: Digest
    planned_batches: int = Field(ge=1, le=20)
    items: tuple[RetentionBatchInspection, ...] = Field(max_length=20)
    state: Literal["ready", "needs_attention", "plan_set_observed"]
    next_sequence: int | None = Field(default=None, ge=1, le=20)
    observed_at: AwareDatetime
    job_complete: Literal[False] = False
    execution_permit: Literal[False] = False
    automatic_replay_allowed: Literal[False] = False

    @model_validator(mode="after")
    def fixed_observation(self) -> "RetentionJobObservation":
        count = len(self.items)
        blocked = any(item.status.state != "completed" for item in self.items)
        expected = "needs_attention" if blocked else "plan_set_observed" if count == self.planned_batches else "ready"
        next_sequence = count + 1 if expected == "ready" else None
        if (count > self.planned_batches or self.state != expected or self.next_sequence != next_sequence
            or any(item.sequence != sequence or item.status.job_id != self.job_id
                or item.observed_at > self.observed_at for sequence, item in enumerate(self.items, 1))):
            raise ValueError(self._invalid_code)
        return self


ManifestT = TypeVar("ManifestT", bound=RetentionBatchContract)
ManifestContraT = TypeVar("ManifestContraT", bound=RetentionBatchContract, contravariant=True)


class _JobAuthority(Protocol[ManifestContraT]):
    async def assert_current_job(self, plan: ManifestContraT, *, purpose: Literal["read", "advance"]) -> None: ...


class _JobBatchAuthority(Generic[ManifestT]):
    """在原每次批次许可边界复检原完整集合及原ledger许可，不缓存批准。"""

    def __init__(self, plan: ManifestT, plans: tuple[RetentionBatchPlan, ...], job: _JobAuthority[ManifestT],
        ledger: RetentionBatchAuthority, *, unconfirmed_error: type[RuntimeError],
    ) -> None:
        self._plan, self._plans, self._job, self._ledger = plan, plans, job, ledger
        self._unconfirmed_error = unconfirmed_error
        self.authority_id = ledger.authority_id

    async def assert_current(self, plan: RetentionBatchPlan, *,
        purpose: Literal["execute", "verify", "read", "reconcile"],
    ) -> None:
        if not 1 <= plan.sequence <= len(self._plans) or self._plans[plan.sequence - 1] != plan:
            raise self._unconfirmed_error()
        await self._job.assert_current_job(self._plan, purpose="read" if purpose == "read" else "advance")
        await self._ledger.assert_current(plan, purpose=purpose)

    async def issue_recovery_permit(self, plan: RetentionBatchPlan, lease: RetentionBatchLease,
    ) -> RetentionBatchRecoveryPermit:
        # 逐批推进不提供恢复入口；维护者需另建原独立Review并申请实际恢复许可。
        raise self._unconfirmed_error()


class _JobBatch(Protocol):
    async def execute(self, *, owner_id: UUID) -> RetentionBatchReceipt: ...


ObservationT = TypeVar("ObservationT", bound=RetentionJobObservation)


class _PostgresRetentionJob(Generic[ObservationT]):
    """仅供具体类别完成本地固定范围/依赖校验后接入，原PG围栏仍负责竞争。"""

    def __init__(self, *, plans: tuple[RetentionBatchPlan, ...], batches: tuple[_JobBatch, ...],
        store: PostgresRetentionBatchStore, manifest_digest: str, status_type: type[ObservationT],
        unconfirmed_error: type[RuntimeError],
    ) -> None:
        self._plans, self._batches, self._store = plans, batches, store
        self._manifest_digest, self._status_type = manifest_digest, status_type
        self._unconfirmed_error = unconfirmed_error
        self._busy = False
        self._halted = False

    async def _status(self) -> ObservationT:
        # 一页覆盖本集合全部历史，原Store在同一REPEATABLE READ快照中有界校验完整事件链。
        page = await self._store.read_job_status(self._plans[0], page_size=20)
        if page.has_more or len(page.items) > len(self._plans):
            raise ValueError()
        for sequence, item in enumerate(page.items, 1):
            batch = self._plans[sequence - 1]
            if (item.sequence != sequence or item.status.batch_id != batch.batch_id
                or item.status.plan_digest != batch_digest(batch)
                or sequence < len(page.items) and item.status.state != "completed"):
                raise ValueError()
        blocked = any(item.status.state != "completed" for item in page.items)
        state = "needs_attention" if blocked else "plan_set_observed" if len(page.items) == len(self._batches) else "ready"
        return self._status_type(job_id=page.job_id, manifest_digest=self._manifest_digest,
            planned_batches=len(self._batches), items=page.items, state=state,
            next_sequence=len(page.items) + 1 if state == "ready" else None, observed_at=datetime.now(UTC))

    async def read_status(self) -> ObservationT:
        """只读原账本，不访问目标；计划到期仍须当前完整集合read许可。"""
        try:
            async with asyncio.timeout(3):
                return await self._status()
        except Exception:
            raise self._unconfirmed_error() from None

    async def advance(self, *, owner_id: UUID) -> ObservationT:
        """最多推进一个尚无历史的新批次；任何未知/取消永久关闭本实例后续推进。"""
        if not isinstance(owner_id, UUID) or self._busy or self._halted:
            raise self._unconfirmed_error()
        self._busy = True
        try:
            async with asyncio.timeout(16):
                current = await self._status()
                if current.next_sequence is None:
                    return current
                sequence = current.next_sequence
                batch = self._plans[sequence - 1]
                if not batch.created_at <= datetime.now(UTC) < batch.expires_at:
                    raise ValueError()
                receipt = await self._batches[sequence - 1].execute(owner_id=owner_id)
                # 完成只采纳随后读到的原持久回执状态，不能根据调用返回推算前进。
                observed = await self._status()
                if len(observed.items) < sequence:
                    raise ValueError()
                item = observed.items[sequence - 1]
                if (item.status.state != receipt.state or item.status.recorded_at < receipt.recorded_at):
                    raise ValueError()
                return observed
        except BaseException as error:
            self._halted = True
            if not isinstance(error, Exception):
                raise
            raise self._unconfirmed_error() from None
        finally:
            self._busy = False
