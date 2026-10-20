"""批次账本的封闭追加事件及逐步历史核对，不生成目标执行许可。"""

from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from supportops_core.retention_batch_contracts import (
    Digest, RetentionBatchContract, RetentionBatchLease, RetentionBatchPlan, RetentionBatchReceipt,
    RetentionBatchRecoveryPermit, RetentionBatchStatus, RetentionBatchVerification, batch_digest, require_batch_lease,
    require_batch_receipt, require_batch_verification,
)

MAX_EVENTS = 32
MAX_EVENT_BYTES = 131_072


class RetentionBatchEvent(RetentionBatchContract):
    schema_version: Literal["retention_batch_event_v1"] = "retention_batch_event_v1"
    id: UUID
    revision: int = Field(ge=1, le=MAX_EVENTS)
    previous_digest: Digest | None
    event_type: Literal["claimed", "dispatched", "verification_started", "recorded", "unconfirmed",
        "reconciliation_started", "reconciled"]
    plan: RetentionBatchPlan = Field(repr=False)
    lease: RetentionBatchLease = Field(repr=False)
    status: RetentionBatchStatus
    verification_id: UUID | None = None
    verification_started_at: AwareDatetime | None = None
    verification: RetentionBatchVerification | None = Field(default=None, repr=False)
    receipt: RetentionBatchReceipt | None = Field(default=None, repr=False)
    recovery_permit: RetentionBatchRecoveryPermit | None = Field(default=None, repr=False)

    @model_validator(mode="after")
    def fixed_event(self) -> "RetentionBatchEvent":
        require_batch_lease(self.plan, self.lease, owner_id=self.lease.owner_id)
        if (self.status.job_id != self.plan.job_id or self.status.batch_id != self.plan.batch_id
            or self.status.plan_digest != self.lease.plan_digest or self.status.claim_attempts != self.lease.claim_attempt
            or self.status.verification_attempts > self.plan.max_verification_attempts
            or self.status.recorded_at < self.lease.claimed_at
            or self.event_type not in {"unconfirmed", "reconciliation_started", "reconciled"}
                and self.status.recorded_at >= self.lease.expires_at
            or (self.recovery_permit is None) != (self.event_type not in {"reconciliation_started", "reconciled"})
            or (self.verification_id is None) != (self.verification_started_at is None)
            or (self.verification_id is None) != (self.status.verification_attempts == 0)
            or (self.verification is None) != (self.receipt is None)):
            raise ValueError("RETENTION_BATCH_EVENT_INVALID")
        if self.recovery_permit is not None and (
            self.recovery_permit.plan_digest != self.lease.plan_digest
            or self.recovery_permit.lease_id != self.lease.lease_id
            or self.recovery_permit.generation != self.lease.generation
            or self.recovery_permit.verifier_id != self.plan.verifier_id
            or self.recovery_permit.issued_at < self.lease.expires_at
            or self.recovery_permit.issued_at > self.status.recorded_at
            or self.status.recorded_at > self.recovery_permit.expires_at
        ):
            raise ValueError("RETENTION_BATCH_RECOVERY_PERMIT_BINDING_INVALID")
        if self.verification_started_at is not None and (self.verification_started_at.tzinfo is None
            or not self.lease.claimed_at <= self.verification_started_at <= self.status.recorded_at):
            raise ValueError("RETENTION_BATCH_VERIFICATION_TIME_INVALID")
        if self.receipt is not None and self.verification is not None:
            if self.verification_id is None or self.verification_started_at is None:
                raise ValueError("RETENTION_BATCH_VERIFICATION_MISSING")
            require_batch_verification(self.plan, self.lease, self.verification,
                verification_id=self.verification_id, started_at=self.verification_started_at)
            require_batch_receipt(self.plan, self.lease, self.verification, self.receipt)
            if self.status.state != self.receipt.state or self.status.recorded_at < self.receipt.recorded_at:
                raise ValueError("RETENTION_BATCH_RECORDED_STATE_INVALID")
        return self


def require_event_transition(previous: RetentionBatchEvent | None, event: RetentionBatchEvent) -> None:
    if previous is None:
        if (event.revision != 1 or event.previous_digest is not None or event.event_type != "claimed"
            or event.lease.generation != 1 or event.lease.claim_attempt != 1):
            raise ValueError("RETENTION_BATCH_INITIAL_EVENT_INVALID")
    elif (event.revision != previous.revision + 1 or event.previous_digest != batch_digest(previous)
        or event.plan != previous.plan or event.status.recorded_at < previous.status.recorded_at):
        raise ValueError("RETENTION_BATCH_EVENT_CHAIN_INVALID")
    if event.event_type == "claimed":
        if (event.status.state != "claimed" or event.status.dispatched_at is not None
            or event.status.verification_attempts != 0 or event.verification_id is not None or event.receipt is not None):
            raise ValueError("RETENTION_BATCH_CLAIM_INVALID")
        if previous is not None and (previous.status.dispatched_at is not None
            or previous.receipt is not None or event.lease.claim_attempt != previous.lease.claim_attempt + 1
            or event.lease.generation != previous.lease.generation + 1
            or event.lease.claimed_at < previous.lease.expires_at):
            raise ValueError("RETENTION_BATCH_RECLAIM_FORBIDDEN")
        return
    if previous is None or event.lease != previous.lease:
        raise ValueError("RETENTION_BATCH_LEASE_CHANGED")
    if event.event_type == "dispatched":
        if (previous.status.state != "claimed" or previous.status.dispatched_at is not None
            or event.status.state != "dispatched" or event.status.dispatched_at != event.status.recorded_at
            or event.status.verification_attempts != 0 or event.verification_id is not None or event.receipt is not None):
            raise ValueError("RETENTION_BATCH_DISPATCH_REPLAY_FORBIDDEN")
    elif event.event_type == "verification_started":
        if (previous.status.dispatched_at is None or previous.receipt is not None
            or event.status.state != "verifying" or event.status.dispatched_at != previous.status.dispatched_at
            or event.status.verification_attempts != previous.status.verification_attempts + 1
            or event.verification_id is None or event.verification_id == previous.verification_id
            or event.verification_started_at != event.status.recorded_at or event.receipt is not None):
            raise ValueError("RETENTION_BATCH_VERIFICATION_START_INVALID")
    elif event.event_type == "recorded":
        if (previous.status.state != "verifying" or previous.receipt is not None or event.receipt is None
            or event.status.dispatched_at != previous.status.dispatched_at
            or event.status.verification_attempts != previous.status.verification_attempts
            or event.verification_id != previous.verification_id
            or event.verification_started_at != previous.verification_started_at):
            raise ValueError("RETENTION_BATCH_RECORDING_INVALID")
    elif event.event_type == "reconciliation_started":
        if (previous.status.state not in {"dispatched", "verifying", "needs_attention"}
            or previous.status.dispatched_at is None
            or previous.receipt is not None or event.status.state != "verifying"
            or event.status.dispatched_at != previous.status.dispatched_at
            or event.status.verification_attempts != previous.status.verification_attempts + 1
            or event.verification_id is None or event.verification_id == previous.verification_id
            or event.verification_started_at != event.status.recorded_at or event.receipt is not None
            or event.recovery_permit is None or event.status.recorded_at < event.lease.expires_at):
            raise ValueError("RETENTION_BATCH_RECONCILIATION_START_INVALID")
    elif event.event_type == "reconciled":
        if (previous.event_type != "reconciliation_started" or previous.receipt is not None
            or event.receipt is None or event.status.dispatched_at != previous.status.dispatched_at
            or event.status.verification_attempts != previous.status.verification_attempts
            or event.verification_id != previous.verification_id
            or event.verification_started_at != previous.verification_started_at
            or event.recovery_permit != previous.recovery_permit):
            raise ValueError("RETENTION_BATCH_RECONCILIATION_INVALID")
    elif event.event_type == "unconfirmed":
        expected = previous.status.state if previous.receipt is not None else "needs_attention"
        if (event.status.state != expected or event.status.dispatched_at != previous.status.dispatched_at
            or event.status.verification_attempts != previous.status.verification_attempts
            or event.verification_id != previous.verification_id
            or event.verification_started_at != previous.verification_started_at
            or event.verification != previous.verification or event.receipt != previous.receipt):
            raise ValueError("RETENTION_BATCH_UNKNOWN_EVENT_INVALID")


def require_live_event(event: RetentionBatchEvent, lease: RetentionBatchLease) -> None:
    now = datetime.now(UTC)
    if (event.lease != lease or not lease.claimed_at <= now < lease.expires_at
        or not event.plan.created_at <= now < event.plan.expires_at):
        raise ValueError("RETENTION_BATCH_CURRENT_LEASE_INVALID")


class RetentionBatchInspection(RetentionBatchContract):
    """历史状态与当前到期观察分开，不为查询补写派发或终态时间。"""

    schema_version: Literal["retention_batch_inspection_v1"] = "retention_batch_inspection_v1"
    sequence: int = Field(ge=1, le=1_000_000)
    status: RetentionBatchStatus
    observed_at: AwareDatetime
    lease_expires_at: AwareDatetime
    lease_expired: bool
    reconciliation_required: bool
    execution_permit: Literal[False] = False
    automatic_replay_allowed: Literal[False] = False

    @model_validator(mode="after")
    def current_observation(self) -> "RetentionBatchInspection":
        expired = self.observed_at >= self.lease_expires_at
        review = self.status.state == "needs_attention" or (expired and self.status.dispatched_at is not None
            and self.status.state not in {"completed", "not_executed"})
        if (self.observed_at < self.status.recorded_at or self.lease_expired != expired
            or self.reconciliation_required != review):
            raise ValueError("RETENTION_BATCH_INSPECTION_INVALID")
        return self


class RetentionJobStatusPage(RetentionBatchContract):
    """有界作业状态页；只投影每批最新事件，不构成执行或完成许可。"""

    schema_version: Literal["retention_job_status_page_v1"] = "retention_job_status_page_v1"
    job_id: UUID
    after_sequence: int = Field(ge=0, le=1_000_000)
    items: tuple[RetentionBatchInspection, ...] = Field(max_length=100)
    has_more: bool
    next_after_sequence: int | None = Field(default=None, ge=1, le=1_000_000)
    job_complete: Literal[False] = False
    execution_permit: Literal[False] = False
    automatic_replay_allowed: Literal[False] = False

    @model_validator(mode="after")
    def ordered_page(self) -> "RetentionJobStatusPage":
        sequences = tuple(item.sequence for item in self.items)
        if (any(item.status.job_id != self.job_id for item in self.items)
            or tuple(sorted(set(sequences))) != sequences
            or any(sequence <= self.after_sequence for sequence in sequences)
            or (self.has_more and not self.items)
            or (self.next_after_sequence != sequences[-1] if self.has_more else self.next_after_sequence is not None)):
            raise ValueError("RETENTION_JOB_STATUS_PAGE_INVALID")
        return self
