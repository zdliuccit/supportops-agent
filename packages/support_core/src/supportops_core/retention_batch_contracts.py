"""独立维护批次的封闭契约；不把保留草稿或影响预览转换为擦除许可。"""

import hashlib
import json
from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

from supportops_core.retention_contracts import DataCategory

Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
Identifier = Annotated[str, Field(pattern=r"^[A-Za-z][A-Za-z0-9_.:-]{0,99}$")]


class RetentionBatchContract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, hide_input_in_errors=True)

    @field_validator("*", mode="after")
    @classmethod
    def utc_time(cls, value: object) -> object:
        return value.astimezone(UTC) if isinstance(value, datetime) else value


def batch_digest(value: RetentionBatchContract) -> str:
    raw = json.dumps(value.model_dump(mode="json"), ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


class RetentionBatchPlan(RetentionBatchContract):
    """精确有界集合由可信盘点产生；摘要本身不证明批准、无保留或无在途引用。"""

    schema_version: Literal["retention_batch_plan_v1"] = "retention_batch_plan_v1"
    job_id: UUID
    batch_id: UUID
    sequence: int = Field(ge=1, le=1_000_000)
    tenant_id: UUID
    subject_user_id: UUID
    erasure_request_id: UUID
    category: DataCategory
    operation: Literal["quarantined", "erased"]
    resource_key_digests: tuple[Digest, ...] = Field(min_length=1, max_length=100, repr=False)
    scope_digest: Digest
    policy_digest: Digest
    approval_digest: Digest
    inventory_digest: Digest
    ledger_id: UUID
    maintenance_identity: Identifier
    executor_id: Identifier
    verifier_id: Identifier
    cutoff_at: AwareDatetime
    created_at: AwareDatetime
    expires_at: AwareDatetime
    max_claim_attempts: int = Field(default=3, ge=1, le=3)
    max_verification_attempts: int = Field(default=3, ge=1, le=3)

    @model_validator(mode="after")
    def fixed_batch(self) -> "RetentionBatchPlan":
        if tuple(sorted(set(self.resource_key_digests))) != self.resource_key_digests:
            raise ValueError("RETENTION_BATCH_RESOURCES_INVALID")
        if self.executor_id == self.verifier_id:
            raise ValueError("RETENTION_BATCH_INDEPENDENT_VERIFIER_REQUIRED")
        if (not self.cutoff_at <= self.created_at < self.expires_at
            or (self.expires_at - self.created_at).total_seconds() > 86_400):
            raise ValueError("RETENTION_BATCH_WINDOW_INVALID")
        return self


class RetentionBatchLease(RetentionBatchContract):
    schema_version: Literal["retention_batch_lease_v1"] = "retention_batch_lease_v1"
    batch_id: UUID
    plan_digest: Digest
    lease_id: UUID
    owner_id: UUID
    generation: int = Field(ge=1, le=2**63 - 1)
    claim_attempt: int = Field(ge=1, le=3)
    claimed_at: AwareDatetime
    expires_at: AwareDatetime

    @model_validator(mode="after")
    def short_lease(self) -> "RetentionBatchLease":
        if not 0 < (self.expires_at - self.claimed_at).total_seconds() <= 30:
            raise ValueError("RETENTION_BATCH_LEASE_INVALID")
        return self


class RetentionBatchRecoveryPermit(RetentionBatchContract):
    """过期派发的单次只读复核授权；不能授予目标重放。"""

    schema_version: Literal["retention_batch_recovery_permit_v1"] = "retention_batch_recovery_permit_v1"
    permit_id: UUID
    plan_digest: Digest
    lease_id: UUID
    generation: int = Field(ge=1, le=2**63 - 1)
    authority_id: Identifier
    approver_id: Identifier
    verifier_id: Identifier
    reason_digest: Digest
    issued_at: AwareDatetime
    expires_at: AwareDatetime

    @model_validator(mode="after")
    def bounded_window(self) -> "RetentionBatchRecoveryPermit":
        if not 0 < (self.expires_at - self.issued_at).total_seconds() <= 60:
            raise ValueError("RETENTION_BATCH_RECOVERY_PERMIT_INVALID")
        return self


def require_live_recovery_permit(permit: RetentionBatchRecoveryPermit, *, now: datetime | None = None) -> None:
    observed_at = now or datetime.now(UTC)
    if not permit.issued_at <= observed_at < permit.expires_at:
        raise ValueError("RETENTION_BATCH_RECOVERY_PERMIT_EXPIRED")


class RetentionResourceVerification(RetentionBatchContract):
    resource_key_digest: Digest
    status: Literal["verified", "not_executed", "unknown"]
    evidence_digest: Digest | None

    @model_validator(mode="after")
    def evidence_required(self) -> "RetentionResourceVerification":
        if (self.status != "unknown") != (self.evidence_digest is not None):
            raise ValueError("RETENTION_BATCH_EVIDENCE_INVALID")
        return self


class RetentionBatchVerification(RetentionBatchContract):
    schema_version: Literal["retention_batch_verification_v1"] = "retention_batch_verification_v1"
    plan_digest: Digest
    lease_id: UUID
    generation: int = Field(ge=1, le=2**63 - 1)
    verification_id: UUID
    verifier_id: Identifier
    observed_at: AwareDatetime
    resources: tuple[RetentionResourceVerification, ...] = Field(min_length=1, max_length=100, repr=False)


class RetentionFactReference(RetentionBatchContract):
    """账本适配器实际持久化的删除事实引用，不由协调器生成事实内容。"""

    resource_key_digest: Digest
    action: Literal["quarantined", "erased"]
    evidence_digest: Digest
    record_digest: Digest


class RetentionBatchReceipt(RetentionBatchContract):
    schema_version: Literal["retention_batch_receipt_v1"] = "retention_batch_receipt_v1"
    batch_id: UUID
    plan_digest: Digest
    lease_id: UUID
    generation: int = Field(ge=1, le=2**63 - 1)
    verification_id: UUID
    verification_digest: Digest
    ledger_id: UUID
    state: Literal["completed", "not_executed", "needs_attention"]
    facts: tuple[RetentionFactReference, ...] = Field(max_length=100, repr=False)
    recorded_at: AwareDatetime
    job_complete: Literal[False] = False
    automatic_replay_allowed: Literal[False] = False


class RetentionBatchStatus(RetentionBatchContract):
    """未来持久账本的最小状态投影，不含资源定位、正文或维护凭据。"""

    schema_version: Literal["retention_batch_status_v1"] = "retention_batch_status_v1"
    job_id: UUID
    batch_id: UUID
    plan_digest: Digest
    state: Literal["pending", "claimed", "dispatched", "verifying", "completed", "not_executed", "needs_attention"]
    claim_attempts: int = Field(ge=0, le=3)
    verification_attempts: int = Field(ge=0, le=3)
    dispatched_at: AwareDatetime | None
    recorded_at: AwareDatetime
    job_complete: Literal[False] = False
    automatic_replay_allowed: Literal[False] = False

    @model_validator(mode="after")
    def progress_consistency(self) -> "RetentionBatchStatus":
        if (self.state == "pending" and (self.claim_attempts != 0 or self.dispatched_at is not None)
            or self.state != "pending" and self.claim_attempts == 0
            or self.state in {"pending", "claimed"} and self.dispatched_at is not None
            or self.state in {"dispatched", "verifying", "completed", "not_executed"} and self.dispatched_at is None
            or self.state in {"pending", "claimed", "dispatched"} and self.verification_attempts != 0
            or self.state in {"verifying", "completed", "not_executed"} and self.verification_attempts == 0
            or self.verification_attempts > 0 and self.dispatched_at is None
            or self.dispatched_at is not None and self.dispatched_at > self.recorded_at):
            raise ValueError("RETENTION_BATCH_STATUS_INVALID")
        return self


def require_batch_lease(plan: RetentionBatchPlan, lease: RetentionBatchLease, *, owner_id: UUID) -> None:
    if (lease.batch_id != plan.batch_id or lease.plan_digest != batch_digest(plan) or lease.owner_id != owner_id
        or lease.claim_attempt > plan.max_claim_attempts or lease.claimed_at < plan.created_at
        or lease.expires_at > plan.expires_at):
        raise ValueError("RETENTION_BATCH_LEASE_BINDING_INVALID")


def require_batch_verification(plan: RetentionBatchPlan, lease: RetentionBatchLease,
    verification: RetentionBatchVerification, *, verification_id: UUID, started_at: datetime,
) -> None:
    if (verification.plan_digest != lease.plan_digest or verification.lease_id != lease.lease_id
        or verification.generation != lease.generation or verification.verification_id != verification_id
        or verification.verifier_id != plan.verifier_id
        or tuple(item.resource_key_digest for item in verification.resources) != plan.resource_key_digests
        or not started_at <= verification.observed_at <= datetime.now(UTC)):
        raise ValueError("RETENTION_BATCH_VERIFICATION_BINDING_INVALID")


def require_batch_receipt(plan: RetentionBatchPlan, lease: RetentionBatchLease,
    verification: RetentionBatchVerification, receipt: RetentionBatchReceipt,
) -> None:
    statuses = {item.status for item in verification.resources}
    state = "completed" if statuses == {"verified"} else "not_executed" if statuses == {"not_executed"} else "needs_attention"
    verified = tuple(item.resource_key_digest for item in verification.resources if item.status == "verified")
    evidence = tuple(item.evidence_digest for item in verification.resources if item.status == "verified")
    if (receipt.batch_id != plan.batch_id or receipt.plan_digest != lease.plan_digest
        or receipt.lease_id != lease.lease_id or receipt.generation != lease.generation
        or receipt.verification_id != verification.verification_id
        or receipt.verification_digest != batch_digest(verification) or receipt.ledger_id != plan.ledger_id
        or receipt.state != state or tuple(item.resource_key_digest for item in receipt.facts) != verified
        or tuple(item.evidence_digest for item in receipt.facts) != evidence
        or any(item.action != plan.operation for item in receipt.facts)
        or len({item.record_digest for item in receipt.facts}) != len(receipt.facts)
        or not verification.observed_at <= receipt.recorded_at <= datetime.now(UTC)):
        raise ValueError("RETENTION_BATCH_RECEIPT_BINDING_INVALID")
