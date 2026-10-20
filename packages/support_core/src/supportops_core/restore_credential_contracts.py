"""恢复后固定模型凭据修订的隔离契约；不读取秘密或声明物理擦除。"""

import hashlib
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from supportops_core.retention_batch_contracts import Digest, Identifier, RetentionBatchContract
from supportops_core.retention_propagation_contracts import PropagationDeletionFact
from supportops_core.retention_tombstone_snapshot import _canonical

RestoreCredentialEvent = Literal["quarantine_requested", "commit_observed", "quarantine_verified",
    "target_not_observed", "quarantine_not_confirmed"]


class RestoredCredentialLocator(RetentionBatchContract):
    schema_version: Literal["supportops_restore_resource_locator_v1"] = "supportops_restore_resource_locator_v1"
    tenant_id: UUID
    subject_user_id: UUID
    category: Literal["credentials"] = "credentials"
    resource_kind: Literal["model_credential_revision"] = "model_credential_revision"
    resource_id: UUID


class RestoreCredentialDatabase(RetentionBatchContract):
    expected_database: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_-]{0,62}$", repr=False)
    expected_maintenance_role: str = Field(pattern=r"^[a-z_][a-z0-9_]{0,62}$", repr=False)


class RestoreCredentialRequest(RetentionBatchContract):
    schema_version: Literal["restore_credential_request_v1"] = "restore_credential_request_v1"
    request_id: UUID
    restore_id: UUID
    context_digest: Digest
    backup_id: str = Field(pattern=r"^supportops-backup\.[A-Za-z0-9]{12}$")
    target_alias: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    ledger_id: UUID
    ledger_generation: int = Field(ge=1, le=2**63 - 1)
    ledger_snapshot_digest: Digest
    authority_id: Identifier
    fact: PropagationDeletionFact = Field(repr=False)
    locator: RestoredCredentialLocator = Field(repr=False)
    credential_id: UUID = Field(repr=False)
    credential_revision: int = Field(ge=1, le=2**31 - 1)
    provider: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_.:-]{0,63}$")
    write_database: RestoreCredentialDatabase = Field(repr=False)
    verification_database: RestoreCredentialDatabase = Field(repr=False)
    created_at: AwareDatetime
    expires_at: AwareDatetime
    operation: Literal["quarantine_model_credential_revision"] = "quarantine_model_credential_revision"

    @model_validator(mode="after")
    def fixed_scope(self) -> "RestoreCredentialRequest":
        fact, locator = self.fact, self.locator
        if (fact.category != "credentials" or fact.tenant_id != locator.tenant_id
            or fact.subject_user_id != locator.subject_user_id
            or fact.resource_key_digest != hashlib.sha256(_canonical(locator.model_dump(mode="json"))).hexdigest()
            or not fact.recorded_at <= self.created_at < self.expires_at
            or (self.expires_at - self.created_at).total_seconds() > 60
            or self.write_database.expected_database != self.verification_database.expected_database
            or self.write_database.expected_maintenance_role == self.verification_database.expected_maintenance_role):
            raise ValueError("RESTORE_CREDENTIAL_SCOPE_INVALID")
        return self


class RestoreCredentialObservation(RetentionBatchContract):
    schema_version: Literal["restore_credential_observation_v1"] = "restore_credential_observation_v1"
    request_digest: Digest
    fact_digest: Digest
    resource_key_digest: Digest
    state: Literal["quarantined", "present", "unknown"]
    evidence_digest: Digest | None
    observed_at: AwareDatetime

    @model_validator(mode="after")
    def evidence_shape(self) -> "RestoreCredentialObservation":
        if (self.state == "unknown") != (self.evidence_digest is None):
            raise ValueError("RESTORE_CREDENTIAL_OBSERVATION_INVALID")
        return self


class RestoreCredentialAudit(RetentionBatchContract):
    schema_version: Literal["restore_credential_audit_v1"] = "restore_credential_audit_v1"
    event_id: UUID
    restore_id: UUID
    request_id: UUID
    request_digest: Digest
    fact_digest: Digest
    resource_key_digest: Digest
    event: RestoreCredentialEvent
    observation_digest: Digest | None
    evidence_digest: Digest | None
    observed_at: AwareDatetime

    @model_validator(mode="after")
    def evidence_shape(self) -> "RestoreCredentialAudit":
        observed = self.event in {"quarantine_verified", "target_not_observed", "quarantine_not_confirmed"}
        if (observed != (self.observation_digest is not None)
            or (self.event in {"quarantine_verified", "quarantine_not_confirmed"}) != (self.evidence_digest is not None)):
            raise ValueError("RESTORE_CREDENTIAL_AUDIT_INVALID")
        return self


class RestoreCredentialReceipt(RetentionBatchContract):
    schema_version: Literal["restore_credential_receipt_v1"] = "restore_credential_receipt_v1"
    request_id: UUID
    request_digest: Digest
    status: Literal["quarantined", "not_observed", "needs_attention"]
    observation_digest: Digest
    evidence_digest: Digest | None
    observed_at: AwareDatetime
    fixed_revision_read_quarantine_confirmed: bool
    original_erasure_reapplied: Literal[False] = False
    provider_secret_revocation_confirmed: Literal[False] = False
    restore_access_allowed: Literal[False] = False
    automatic_retries_enabled: Literal[False] = False

    @model_validator(mode="after")
    def consistent_receipt(self) -> "RestoreCredentialReceipt":
        if (self.fixed_revision_read_quarantine_confirmed != (self.status == "quarantined")
            or (self.status == "not_observed") != (self.evidence_digest is None)):
            raise ValueError("RESTORE_CREDENTIAL_RECEIPT_INVALID")
        return self
