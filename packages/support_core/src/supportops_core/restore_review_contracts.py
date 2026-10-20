"""恢复资源复核的封闭汇总契约；列明隔离观察不等于擦除或开放许可。"""

import hashlib
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from supportops_core.restore_credential_contracts import RestoreCredentialReceipt, RestoreCredentialRequest
from supportops_core.restore_knowledge_contracts import RestoreKnowledgeReceipt, RestoreKnowledgeRequest
from supportops_core.restore_session_contracts import RestoreSessionReceipt, RestoreSessionRequest
from supportops_core.retention_batch_contracts import Digest, Identifier, RetentionBatchContract
from supportops_core.retention_contracts import DataCategory
from supportops_core.retention_propagation_contracts import PropagationDeletionFact
from supportops_core.retention_tombstone_snapshot import MAX_FACTS, _canonical

ReviewCategory = Literal["auth_sessions", "knowledge", "credentials"]
ReviewStatus = Literal["isolation_observed", "needs_attention", "not_observed", "missing_evidence", "unconfirmed", "unsupported"]


def _fact_refs_digest(references: list[dict[str, str]]) -> str:
    return hashlib.sha256(_canonical({"schema_version": "restore_review_fact_set_v1", "facts": references})).hexdigest()


def review_fact_set_digest(facts: tuple[PropagationDeletionFact, ...]) -> str:
    """只绑定原事实ID/原摘要；这是汇总范围摘要，不替代原快照字节摘要或重签事实。"""
    return _fact_refs_digest([{"id": str(fact.id), "record_digest": fact.record_digest} for fact in facts])


class RestoreReviewReaderBinding(RetentionBatchContract):
    category: ReviewCategory
    reader_id: Identifier


class RestoreReviewRequest(RetentionBatchContract):
    schema_version: Literal["restore_review_request_v1"] = "restore_review_request_v1"
    request_id: UUID
    restore_id: UUID
    context_digest: Digest
    backup_id: str = Field(pattern=r"^supportops-backup\.[A-Za-z0-9]{12}$")
    target_alias: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    ledger_id: UUID
    ledger_generation: int = Field(ge=1, le=2**63 - 1)
    ledger_snapshot_digest: Digest
    fact_set_digest: Digest
    inventory_digest: Digest
    authority_id: Identifier
    facts: tuple[PropagationDeletionFact, ...] = Field(max_length=MAX_FACTS, repr=False)
    readers: tuple[RestoreReviewReaderBinding, ...] = Field(max_length=3)
    max_observation_age_seconds: int = Field(ge=1, le=60)
    created_at: AwareDatetime
    expires_at: AwareDatetime

    @model_validator(mode="after")
    def fixed_scope(self) -> "RestoreReviewRequest":
        ids = tuple(str(fact.id) for fact in self.facts)
        categories = tuple(reader.category for reader in self.readers)
        if (ids != tuple(sorted(set(ids))) or categories != tuple(sorted(set(categories)))
            or self.fact_set_digest != review_fact_set_digest(self.facts)
            or any(fact.recorded_at > self.created_at for fact in self.facts)
            or not self.created_at < self.expires_at or (self.expires_at - self.created_at).total_seconds() > 60):
            raise ValueError("RESTORE_REVIEW_SCOPE_INVALID")
        return self


class SessionRestoreReviewEvidence(RetentionBatchContract):
    schema_version: Literal["restore_review_session_evidence_v1"] = "restore_review_session_evidence_v1"
    reader_id: Identifier
    audit_record_digest: Digest
    request: RestoreSessionRequest = Field(repr=False)
    receipt: RestoreSessionReceipt = Field(repr=False)


class KnowledgeRestoreReviewEvidence(RetentionBatchContract):
    schema_version: Literal["restore_review_knowledge_evidence_v1"] = "restore_review_knowledge_evidence_v1"
    reader_id: Identifier
    audit_record_digest: Digest
    request: RestoreKnowledgeRequest = Field(repr=False)
    receipt: RestoreKnowledgeReceipt = Field(repr=False)


class CredentialRestoreReviewEvidence(RetentionBatchContract):
    schema_version: Literal["restore_review_credential_evidence_v1"] = "restore_review_credential_evidence_v1"
    reader_id: Identifier
    audit_record_digest: Digest
    request: RestoreCredentialRequest = Field(repr=False)
    receipt: RestoreCredentialReceipt = Field(repr=False)


RestoreReviewEvidence = SessionRestoreReviewEvidence | KnowledgeRestoreReviewEvidence | CredentialRestoreReviewEvidence
ReviewReason = Literal["ISOLATION_OBSERVED", "ORIGINAL_ERASURE_UNCONFIRMED", "ISOLATION_UNCONFIRMED",
    "TARGET_NOT_OBSERVED", "EVIDENCE_MISSING", "EVIDENCE_UNCONFIRMED", "READER_UNAVAILABLE", "ADAPTER_NOT_IMPLEMENTED"]


class RestoreReviewItem(RetentionBatchContract):
    fact_id: UUID
    fact_digest: Digest
    category: DataCategory
    original_action: Literal["quarantined", "erased"]
    status: ReviewStatus
    reason: ReviewReason
    isolation_observed: bool
    source_request_digest: Digest | None
    source_receipt_digest: Digest | None
    source_audit_record_digest: Digest | None
    observed_at: AwareDatetime | None

    @model_validator(mode="after")
    def consistent_result(self) -> "RestoreReviewItem":
        references = (self.source_request_digest, self.source_receipt_digest, self.source_audit_record_digest, self.observed_at)
        known = self.status in {"isolation_observed", "needs_attention", "not_observed"}
        reasons = {"isolation_observed": {"ISOLATION_OBSERVED"},
            "needs_attention": {"ORIGINAL_ERASURE_UNCONFIRMED", "ISOLATION_UNCONFIRMED"},
            "not_observed": {"TARGET_NOT_OBSERVED"}, "missing_evidence": {"EVIDENCE_MISSING", "READER_UNAVAILABLE"},
            "unconfirmed": {"EVIDENCE_UNCONFIRMED"}, "unsupported": {"ADAPTER_NOT_IMPLEMENTED"}}
        if (self.reason not in reasons[self.status]
            or any((value is not None) != known for value in references)
            or self.isolation_observed != (self.status == "isolation_observed" or self.reason == "ORIGINAL_ERASURE_UNCONFIRMED")
            or self.status == "isolation_observed" and self.original_action != "quarantined"
            or self.reason == "ORIGINAL_ERASURE_UNCONFIRMED" and self.original_action != "erased"):
            raise ValueError("RESTORE_REVIEW_ITEM_INVALID")
        return self


class RestoreReviewReport(RetentionBatchContract):
    schema_version: Literal["restore_review_report_v1"] = "restore_review_report_v1"
    request_id: UUID
    request_digest: Digest
    restore_id: UUID
    fact_set_digest: Digest
    inventory_digest: Digest
    status: Literal["listed_isolations_observed", "needs_attention"]
    fact_count: int = Field(ge=0, le=MAX_FACTS)
    isolation_observed_count: int = Field(ge=0, le=MAX_FACTS)
    erasure_required_count: int = Field(ge=0, le=MAX_FACTS)
    items: tuple[RestoreReviewItem, ...] = Field(max_length=MAX_FACTS)
    reviewed_at: AwareDatetime
    original_erasure_reapplied: Literal[False] = False
    provider_secret_revocation_confirmed: Literal[False] = False
    complete_replica_verification: Literal[False] = False
    restore_access_allowed: Literal[False] = False
    execution_allowed: Literal[False] = False

    @model_validator(mode="after")
    def consistent_report(self) -> "RestoreReviewReport":
        ids = tuple(str(item.fact_id) for item in self.items)
        all_observed = bool(self.items) and all(item.status == "isolation_observed" for item in self.items)
        if (ids != tuple(sorted(set(ids))) or self.fact_count != len(self.items)
            or self.fact_set_digest != _fact_refs_digest([
                {"id": str(item.fact_id), "record_digest": item.fact_digest} for item in self.items])
            or self.isolation_observed_count != sum(item.isolation_observed for item in self.items)
            or self.erasure_required_count != sum(item.original_action == "erased" for item in self.items)
            or (self.status == "listed_isolations_observed") != all_observed
            or any(item.observed_at is not None and item.observed_at > self.reviewed_at for item in self.items)):
            raise ValueError("RESTORE_REVIEW_REPORT_INVALID")
        return self
