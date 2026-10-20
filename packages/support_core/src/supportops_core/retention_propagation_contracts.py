"""删除传播的固定副本清单与只读核对回执；不产生删除或恢复开放许可。"""

from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from supportops_core.retention_batch_contracts import Digest, Identifier, RetentionBatchContract
from supportops_core.retention_contracts import DataCategory
from supportops_core.retention_tombstone_snapshot import _fact

ReplicaKind = Literal["index", "cache", "external_copy", "memory", "backup"]
ReplicaState = Literal["quarantined", "erased", "present", "unknown"]


class PropagationDeletionFact(RetentionBatchContract):
    schema_version: Literal["supportops_deletion_fact_v1"] = "supportops_deletion_fact_v1"
    id: UUID
    tenant_id: UUID
    subject_user_id: UUID
    erasure_request_id: UUID
    category: DataCategory
    resource_key_digest: Digest
    scope_digest: Digest
    action: Literal["quarantined", "erased"]
    evidence_digest: Digest
    maintenance_identity: Identifier
    record_digest: Digest
    recorded_at: AwareDatetime

    @model_validator(mode="after")
    def original_fact(self) -> "PropagationDeletionFact":
        # 复用既有fact_v1的UTC +00:00字节，不重签原事实或使用新计划摘要替换它。
        _fact(self.model_dump(mode="python"), datetime.now(UTC))
        return self


class PropagationTarget(RetentionBatchContract):
    target_id: Identifier
    kind: ReplicaKind
    target_scope_digest: Digest
    verifier_id: Identifier


class PropagationRequest(RetentionBatchContract):
    schema_version: Literal["retention_propagation_request_v1"] = "retention_propagation_request_v1"
    request_id: UUID
    ledger_id: UUID
    ledger_generation: int = Field(ge=1, le=2**63 - 1)
    authority_id: Identifier
    inventory_digest: Digest
    fact: PropagationDeletionFact = Field(repr=False)
    targets: tuple[PropagationTarget, ...] = Field(min_length=1, max_length=16, repr=False)
    created_at: AwareDatetime
    expires_at: AwareDatetime

    @model_validator(mode="after")
    def bounded_scope(self) -> "PropagationRequest":
        keys = tuple(target.target_id for target in self.targets)
        if keys != tuple(sorted(set(keys))):
            raise ValueError("RETENTION_PROPAGATION_TARGETS_INVALID")
        # 同一副本不能换别名重复计数；多个实际副本由独立盘点给出不同范围摘要。
        if len({target.target_scope_digest for target in self.targets}) != len(self.targets):
            raise ValueError("RETENTION_PROPAGATION_SCOPE_DUPLICATED")
        if (not self.fact.recorded_at <= self.created_at < self.expires_at
            or (self.expires_at - self.created_at).total_seconds() > 60):
            raise ValueError("RETENTION_PROPAGATION_WINDOW_INVALID")
        return self


class PropagationObservation(RetentionBatchContract):
    schema_version: Literal["retention_propagation_observation_v1"] = "retention_propagation_observation_v1"
    request_digest: Digest
    target_id: Identifier
    target_scope_digest: Digest
    verifier_id: Identifier
    resource_key_digest: Digest
    fact_digest: Digest
    ledger_generation: int = Field(ge=1, le=2**63 - 1)
    state: ReplicaState
    evidence_digest: Digest | None
    observed_at: AwareDatetime

    @model_validator(mode="after")
    def evidence_required(self) -> "PropagationObservation":
        if (self.state == "unknown") != (self.evidence_digest is None):
            raise ValueError("RETENTION_PROPAGATION_EVIDENCE_INVALID")
        return self


class PropagationTargetResult(RetentionBatchContract):
    target_id: Identifier
    kind: ReplicaKind
    state: ReplicaState
    reason: Literal["confirmed", "target_present", "action_mismatch", "verifier_unavailable", "unconfirmed"]
    observation_digest: Digest | None
    evidence_digest: Digest | None
    observed_at: AwareDatetime

    @model_validator(mode="after")
    def consistent_result(self) -> "PropagationTargetResult":
        if ((self.reason in {"verifier_unavailable", "unconfirmed"}) != (self.state == "unknown")
            or (self.reason == "verifier_unavailable" and self.observation_digest is not None)
            or (self.state == "unknown") != (self.evidence_digest is None)
            or (self.state != "unknown" and self.observation_digest is None)
            or (self.reason == "target_present") != (self.state == "present")
            or (self.reason in {"confirmed", "action_mismatch"}
                and self.state not in {"quarantined", "erased"})):
            raise ValueError("RETENTION_PROPAGATION_RESULT_INVALID")
        return self


class PropagationAudit(RetentionBatchContract):
    schema_version: Literal["retention_propagation_audit_v1"] = "retention_propagation_audit_v1"
    event_id: UUID
    request_id: UUID
    request_digest: Digest
    fact_digest: Digest
    inventory_digest: Digest
    event: Literal["inspection_started", "target_observed", "inspection_finished"]
    result: PropagationTargetResult | None
    observed_at: AwareDatetime

    @model_validator(mode="after")
    def event_shape(self) -> "PropagationAudit":
        if (self.event == "target_observed") != (self.result is not None):
            raise ValueError("RETENTION_PROPAGATION_AUDIT_INVALID")
        return self


class PropagationReceipt(RetentionBatchContract):
    schema_version: Literal["retention_propagation_receipt_v1"] = "retention_propagation_receipt_v1"
    request_id: UUID
    request_digest: Digest
    fact_digest: Digest
    inventory_digest: Digest
    results: tuple[PropagationTargetResult, ...] = Field(min_length=1, max_length=16)
    status: Literal["listed_targets_confirmed", "needs_attention"]
    finished_at: AwareDatetime
    erasure_complete: Literal[False] = False
    restore_access_allowed: Literal[False] = False
    execution_allowed: Literal[False] = False
    automatic_retries_enabled: Literal[False] = False

    @model_validator(mode="after")
    def listed_results(self) -> "PropagationReceipt":
        keys = tuple(result.target_id for result in self.results)
        if (keys != tuple(sorted(set(keys)))
            or any(result.observed_at > self.finished_at for result in self.results)
            or (self.status == "listed_targets_confirmed")
                != all(result.reason == "confirmed" for result in self.results)):
            raise ValueError("RETENTION_PROPAGATION_RECEIPT_INVALID")
        return self
