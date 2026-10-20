"""恢复知识快照隔离的固定请求与最小回执；源事实、备份及读取投影分别绑定。"""

import hashlib
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from supportops_core.retention_batch_contracts import Digest, RetentionBatchContract, batch_digest
from supportops_core.retention_knowledge_propagation import KnowledgePropagationBinding, KnowledgePropagationDatabase
from supportops_core.retention_propagation_contracts import PropagationRequest
from supportops_core.retention_tombstone_snapshot import _canonical


class RestoreKnowledgeRequest(RetentionBatchContract):
    schema_version: Literal["restore_knowledge_request_v1"] = "restore_knowledge_request_v1"
    request_id: UUID
    restore_id: UUID
    context_digest: Digest
    backup_id: str = Field(pattern=r"^supportops-backup\.[A-Za-z0-9]{12}$")
    target_alias: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    ledger_snapshot_digest: Digest
    propagation: PropagationRequest = Field(repr=False)
    binding: KnowledgePropagationBinding = Field(repr=False)
    write_database: KnowledgePropagationDatabase = Field(repr=False)
    verification_database: KnowledgePropagationDatabase = Field(repr=False)
    created_at: AwareDatetime
    expires_at: AwareDatetime
    operation: Literal["quarantine_knowledge_snapshot"] = "quarantine_knowledge_snapshot"

    @model_validator(mode="after")
    def fixed_scope(self) -> "RestoreKnowledgeRequest":
        source, binding = self.propagation, self.binding
        fact, locator = source.fact, binding.locator
        if (source.request_id != self.request_id or source.created_at != self.created_at
            or source.expires_at != self.expires_at or len(source.targets) != 1
            or fact.category != "knowledge" or fact.tenant_id != locator.tenant_id
            or fact.subject_user_id != locator.subject_user_id
            or fact.resource_key_digest != hashlib.sha256(_canonical(locator.model_dump(mode="json"))).hexdigest()
            or binding.database_scope_digest != batch_digest(self.verification_database)
            or self.write_database.expected_database != self.verification_database.expected_database
            or self.write_database.expected_maintenance_role == self.verification_database.expected_maintenance_role):
            raise ValueError("RESTORE_KNOWLEDGE_SCOPE_INVALID")
        target = source.targets[0]
        if (target.kind != "index" or target.target_id != binding.target_id
            or target.verifier_id != binding.verifier_id or target.target_scope_digest != batch_digest(binding)):
            raise ValueError("RESTORE_KNOWLEDGE_TARGET_INVALID")
        return self


class RestoreKnowledgeAudit(RetentionBatchContract):
    schema_version: Literal["restore_knowledge_audit_v1"] = "restore_knowledge_audit_v1"
    event_id: UUID
    restore_id: UUID
    request_id: UUID
    request_digest: Digest
    fact_digest: Digest
    resource_key_digest: Digest
    event: Literal["quarantine_requested", "commit_observed", "quarantine_verified",
        "target_not_observed", "quarantine_not_confirmed"]
    observation_digest: Digest | None
    evidence_digest: Digest | None
    observed_at: AwareDatetime

    @model_validator(mode="after")
    def evidence_shape(self) -> "RestoreKnowledgeAudit":
        observed = self.event in {"quarantine_verified", "target_not_observed", "quarantine_not_confirmed"}
        if (observed != (self.observation_digest is not None)
            or (self.event in {"quarantine_verified", "quarantine_not_confirmed"}) != (self.evidence_digest is not None)):
            raise ValueError("RESTORE_KNOWLEDGE_AUDIT_INVALID")
        return self


class RestoreKnowledgeReceipt(RetentionBatchContract):
    schema_version: Literal["restore_knowledge_receipt_v1"] = "restore_knowledge_receipt_v1"
    request_id: UUID
    request_digest: Digest
    status: Literal["quarantined", "not_observed", "needs_attention"]
    observation_digest: Digest
    evidence_digest: Digest | None
    observed_at: AwareDatetime
    snapshot_read_quarantine_confirmed: bool
    original_erasure_reapplied: Literal[False] = False
    restore_access_allowed: Literal[False] = False
    automatic_retries_enabled: Literal[False] = False

    @model_validator(mode="after")
    def receipt_consistency(self) -> "RestoreKnowledgeReceipt":
        if (self.snapshot_read_quarantine_confirmed != (self.status == "quarantined")
            or (self.status == "not_observed") != (self.evidence_digest is None)):
            raise ValueError("RESTORE_KNOWLEDGE_RECEIPT_INVALID")
        return self
