"""单批次维护协调器；无默认账本/维护许可/执行器，不扫描、不调度、不自动重放。"""

import asyncio
from datetime import UTC, datetime
from typing import Literal, Protocol
from uuid import UUID, uuid4

from supportops_core.retention_batch_contracts import (
    RetentionBatchLease, RetentionBatchPlan, RetentionBatchReceipt, RetentionBatchRecoveryPermit,
    RetentionBatchVerification, require_batch_lease, require_batch_receipt, require_batch_verification,
    require_live_recovery_permit,
)
from supportops_core.retention_batch_events import RetentionBatchInspection, RetentionJobStatusPage

Phase = Literal["claimed", "dispatched", "verifying", "recorded"]


class RetentionBatchAuthority(Protocol):
    """独立许可源须复检原批准/法律保留/完整依赖/固定范围；reconcile permit每次新签且不可复用。"""

    authority_id: str

    async def assert_current(self, plan: RetentionBatchPlan, *,
        purpose: Literal["execute", "verify", "read", "reconcile"],
    ) -> None: ...

    async def issue_recovery_permit(self, plan: RetentionBatchPlan, lease: RetentionBatchLease,
    ) -> RetentionBatchRecoveryPermit: ...


class RetentionBatchStore(Protocol):
    """可信持久账本，不得以进程内字典、缓存或应用事务冒充提交。

    claim须以最多250ms锁预算CAS固定plan，提交owner/generation/短租约和领取次数；
    同主体作业只能有一个有效批次，序号必须连续，前批次未completed不得推进。最多三次领取，
    只允许对从未派发且已证明无副作用的旧领取显式恢复；已经派发不能再领取或执行。
    所有写入比较当前owner/generation/租约及计划摘要，审计同事务追加，禁止改写历史。
    租约过期的已派发记录进入待复核，不能像只读Run一样重新执行。
    """

    async def claim(self, plan: RetentionBatchPlan, *, owner_id: UUID) -> RetentionBatchLease: ...

    async def assert_current(self, lease: RetentionBatchLease, *, phase: Phase) -> None: ...

    async def mark_dispatched(self, lease: RetentionBatchLease) -> None:
        """仅claimed到dispatched的一次CAS，先持久提交，重复调用必须拒绝。"""
        ...

    async def begin_verification(self, lease: RetentionBatchLease, *, verification_id: UUID,
        max_attempts: int,
    ) -> None:
        """从派发/待复核状态提交本次验证ID与累计次数，最多三次；从不重置派发事实。"""
        ...

    async def finalize(self, plan: RetentionBatchPlan, lease: RetentionBatchLease,
        verification: RetentionBatchVerification,
    ) -> RetentionBatchReceipt:
        """核对最新验证ID，原子持久化逐项事实/原验证/终态/审计后返回匹配回执。

        每项verified必须保存与原操作、scope和evidence绑定的真实tombstone；
        部分成功也保留已确认事实，其余needs_attention，不把批次完成当作整个job完成。
        """
        ...

    async def record_unconfirmed(self, lease: RetentionBatchLease) -> None:
        """仅追加最小未知事件；原围栏下未终结记录标需处理，已知终态不可降级或覆盖。

        不释放/回滚派发，不续租或重排；未知事件写入失败仍保留原派发/验证事实。
        """
        ...

    async def read_job_status(self, plan: RetentionBatchPlan, *, after_sequence: int = 0,
        page_size: int = 20,
    ) -> RetentionJobStatusPage:
        """在当前独立读取许可下返回有界作业投影；不得续租、派发或推断job完成。"""
        ...

    async def read_status(self, plan: RetentionBatchPlan) -> RetentionBatchInspection | None:
        """读取单批只读投影；不得续租、调用目标或改写旧状态。"""
        ...

    async def begin_reconciliation(self, plan: RetentionBatchPlan, lease: RetentionBatchLease, *,
        permit: RetentionBatchRecoveryPermit, verification_id: UUID,
    ) -> None:
        """持久消费唯一permit并先追加恢复开始事实；绝不重新派发executor。"""
        ...

    async def finalize_reconciliation(self, plan: RetentionBatchPlan, lease: RetentionBatchLease, *,
        permit: RetentionBatchRecoveryPermit, verification: RetentionBatchVerification,
    ) -> RetentionBatchReceipt: ...


class RetentionBatchExecutor(Protocol):
    executor_id: str

    async def execute_once(self, plan: RetentionBatchPlan, lease: RetentionBatchLease) -> None:
        """可信固定类别适配器；目标写边界再次验当前许可/围栏/依赖，禁止内部隐式重试。"""
        ...


class RetentionBatchVerifier(Protocol):
    verifier_id: str

    async def observe(self, plan: RetentionBatchPlan, lease: RetentionBatchLease, *,
        verification_id: UUID,
    ) -> RetentionBatchVerification:
        """与执行器分离的只读目标观察；不以提交回执或缺一行推断全部副本已擦除。"""
        ...


class RetentionBatchUnavailable(RuntimeError):
    code = "RETENTION_BATCH_DEPENDENCIES_UNAVAILABLE"


class RetentionBatchUnconfirmed(RuntimeError):
    code = "RETENTION_BATCH_OUTCOME_UNCONFIRMED"


def _window(plan: RetentionBatchPlan, lease: RetentionBatchLease | None = None) -> None:
    now = datetime.now(UTC)
    if not plan.created_at <= now < plan.expires_at:
        raise ValueError()
    if lease is not None and not lease.claimed_at <= now < lease.expires_at:
        raise ValueError()


def _remaining(plan: RetentionBatchPlan, lease: RetentionBatchLease | None, limit: float) -> float:
    _window(plan, lease)
    deadline = min(plan.expires_at, lease.expires_at) if lease is not None else plan.expires_at
    remaining = min(limit, (deadline - datetime.now(UTC)).total_seconds())
    if remaining <= 0:
        raise ValueError()
    return remaining


async def _current(plan: RetentionBatchPlan, lease: RetentionBatchLease, store: RetentionBatchStore,
    authority: RetentionBatchAuthority, *, phase: Phase, purpose: Literal["execute", "verify"],
) -> None:
    _window(plan, lease)
    async with asyncio.timeout(_remaining(plan, lease, 1)):
        await store.assert_current(lease, phase=phase)
        await authority.assert_current(plan, purpose=purpose)
    _window(plan, lease)


async def _unknown(store: RetentionBatchStore, lease: RetentionBatchLease | None) -> None:
    if lease is None:
        return
    try:
        async with asyncio.timeout(0.5):
            await store.record_unconfirmed(lease)
    except Exception:
        # 审计未知不能掩盖原未确认，也不清除已持久化的一次派发事实。
        return


async def _verify(plan: RetentionBatchPlan, lease: RetentionBatchLease, store: RetentionBatchStore,
    authority: RetentionBatchAuthority, verifier: RetentionBatchVerifier,
) -> RetentionBatchReceipt:
    _window(plan, lease)
    async with asyncio.timeout(_remaining(plan, lease, 1)):
        await authority.assert_current(plan, purpose="verify")
    _window(plan, lease)
    verification_id = uuid4()
    async with asyncio.timeout(_remaining(plan, lease, 1)):
        await store.begin_verification(lease, verification_id=verification_id,
            max_attempts=plan.max_verification_attempts)
    await _current(plan, lease, store, authority, phase="verifying", purpose="verify")
    started_at = datetime.now(UTC)
    async with asyncio.timeout(_remaining(plan, lease, 2)):
        verification = await verifier.observe(plan, lease, verification_id=verification_id)
    if not isinstance(verification, RetentionBatchVerification):
        raise ValueError()
    require_batch_verification(plan, lease, verification, verification_id=verification_id, started_at=started_at)
    await _current(plan, lease, store, authority, phase="verifying", purpose="verify")
    async with asyncio.timeout(_remaining(plan, lease, 1)):
        receipt = await store.finalize(plan, lease, verification)
    if not isinstance(receipt, RetentionBatchReceipt):
        raise ValueError()
    require_batch_receipt(plan, lease, verification, receipt)
    await _current(plan, lease, store, authority, phase="recorded", purpose="verify")
    return receipt


async def execute_retention_batch(plan: RetentionBatchPlan, *, owner_id: UUID,
    store: RetentionBatchStore | None = None, authority: RetentionBatchAuthority | None = None,
    executor: RetentionBatchExecutor | None = None, verifier: RetentionBatchVerifier | None = None,
) -> RetentionBatchReceipt:
    """最多一次派发与一次独立复核；不循环领取下一批、不提供默认物理删除实现。"""
    if store is None or authority is None or executor is None or verifier is None:
        raise RetentionBatchUnavailable()
    lease: RetentionBatchLease | None = None
    try:
        if (not isinstance(plan, RetentionBatchPlan) or not isinstance(owner_id, UUID)
            or executor.executor_id != plan.executor_id or verifier.verifier_id != plan.verifier_id):
            raise ValueError()
        async with asyncio.timeout(10):
            _window(plan)
            async with asyncio.timeout(_remaining(plan, None, 1)):
                await authority.assert_current(plan, purpose="execute")
            _window(plan)
            async with asyncio.timeout(_remaining(plan, None, 1)):
                candidate = await store.claim(plan, owner_id=owner_id)
            if not isinstance(candidate, RetentionBatchLease):
                raise ValueError()
            require_batch_lease(plan, candidate, owner_id=owner_id)
            lease = candidate
            await _current(plan, lease, store, authority, phase="claimed", purpose="execute")
            async with asyncio.timeout(_remaining(plan, lease, 1)):
                await store.mark_dispatched(lease)
            # 已提交派发事实后才进入适配器；后续任何未知都没有重新调用许可。
            await _current(plan, lease, store, authority, phase="dispatched", purpose="execute")
            async with asyncio.timeout(_remaining(plan, lease, 2)):
                await executor.execute_once(plan, lease)
            return await _verify(plan, lease, store, authority, verifier)
    except Exception:
        await _unknown(store, lease)
        raise RetentionBatchUnconfirmed() from None


async def verify_retention_batch(plan: RetentionBatchPlan, lease: RetentionBatchLease, *,
    owner_id: UUID, store: RetentionBatchStore | None = None,
    authority: RetentionBatchAuthority | None = None, verifier: RetentionBatchVerifier | None = None,
) -> RetentionBatchReceipt:
    """原有效租约内显式只读复核；过期后需未来独立恢复许可，不重取租约或调用执行器。"""
    if store is None or authority is None or verifier is None:
        raise RetentionBatchUnavailable()
    try:
        if (not isinstance(plan, RetentionBatchPlan) or not isinstance(lease, RetentionBatchLease)
            or not isinstance(owner_id, UUID) or verifier.verifier_id != plan.verifier_id):
            raise ValueError()
        require_batch_lease(plan, lease, owner_id=owner_id)
        async with asyncio.timeout(8):
            return await _verify(plan, lease, store, authority, verifier)
    except Exception:
        # 只允许经固定绑定验证的原租约写最小未知事件。
        try:
            require_batch_lease(plan, lease, owner_id=owner_id)
        except Exception:
            pass
        else:
            await _unknown(store, lease)
        raise RetentionBatchUnconfirmed() from None


async def reconcile_expired_retention_batch(plan: RetentionBatchPlan, lease: RetentionBatchLease, *,
    store: RetentionBatchStore | None = None,
    authority: RetentionBatchAuthority | None = None, verifier: RetentionBatchVerifier | None = None,
) -> RetentionBatchReceipt:
    """过期后仅凭新独立许可执行一次只读观察；不领取新租约、不调用执行器。"""
    if store is None or authority is None or verifier is None:
        raise RetentionBatchUnavailable()
    reconciliation_started = False
    try:
        if (not isinstance(plan, RetentionBatchPlan) or not isinstance(lease, RetentionBatchLease)
            or verifier.verifier_id != plan.verifier_id):
            raise ValueError()
        require_batch_lease(plan, lease, owner_id=lease.owner_id)
        now = datetime.now(UTC)
        if now < lease.expires_at:
            raise ValueError()
        async with asyncio.timeout(8):
            inspection = await store.read_status(plan)
            if (inspection is None or inspection.status.batch_id != lease.batch_id
                or inspection.status.plan_digest != lease.plan_digest
                or inspection.status.dispatched_at is None or not inspection.lease_expired
                or not inspection.reconciliation_required):
                raise ValueError()
            await authority.assert_current(plan, purpose="reconcile")
            permit = await authority.issue_recovery_permit(plan, lease)
            if (not isinstance(permit, RetentionBatchRecoveryPermit)
                or permit.plan_digest != lease.plan_digest or permit.lease_id != lease.lease_id
                or permit.generation != lease.generation or permit.authority_id != authority.authority_id
                or permit.approver_id == str(plan.subject_user_id)
                or permit.verifier_id != verifier.verifier_id or permit.issued_at < lease.expires_at):
                raise ValueError()
            require_live_recovery_permit(permit)
            verification_id = uuid4()
            await authority.assert_current(plan, purpose="reconcile")
            await store.begin_reconciliation(plan, lease, permit=permit, verification_id=verification_id)
            reconciliation_started = True
            require_live_recovery_permit(permit)
            await authority.assert_current(plan, purpose="reconcile")
            started_at = datetime.now(UTC)
            verification = await verifier.observe(plan, lease, verification_id=verification_id)
            if not isinstance(verification, RetentionBatchVerification):
                raise ValueError()
            require_batch_verification(plan, lease, verification, verification_id=verification_id,
                started_at=started_at)
            require_live_recovery_permit(permit)
            await authority.assert_current(plan, purpose="reconcile")
            receipt = await store.finalize_reconciliation(plan, lease, permit=permit, verification=verification)
            require_batch_receipt(plan, lease, verification, receipt)
            return receipt
    except Exception:
        if reconciliation_started:
            await _unknown(store, lease)
        raise RetentionBatchUnconfirmed() from None
