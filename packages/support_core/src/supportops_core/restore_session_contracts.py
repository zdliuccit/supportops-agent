"""恢复后登录会话隔离的固定来源、资源定位、请求与最小回执。"""

import hashlib
import json
from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
Identifier = Annotated[str, Field(pattern=r"^[A-Za-z][A-Za-z0-9_.:-]{0,99}$")]


class RestoreSessionContract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, hide_input_in_errors=True)

    @field_validator("*", mode="after")
    @classmethod
    def utc_time(cls, value: object) -> object:
        return value.astimezone(UTC) if isinstance(value, datetime) else value


def _json_value(value: object) -> str:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime) and value.tzinfo is not None:
        return value.astimezone(UTC).isoformat()
    raise TypeError()


def restore_session_digest(value: RestoreSessionContract, *, exclude: set[str] | None = None) -> str:
    # 与既有事实快照使用同一+00:00时间字节，避免JSON序列化的Z写法改变行摘要。
    body = json.dumps(value.model_dump(mode="python", exclude=exclude), ensure_ascii=False,
        sort_keys=True, separators=(",", ":"), allow_nan=False, default=_json_value).encode("utf-8")
    return hashlib.sha256(body).hexdigest()


class RestoredSessionLocator(RestoreSessionContract):
    """仅显式UUID定位，不扫描、猜测或从摘要反查其他主体会话。"""

    schema_version: Literal["supportops_restore_resource_locator_v1"] = "supportops_restore_resource_locator_v1"
    tenant_id: UUID
    subject_user_id: UUID
    category: Literal["auth_sessions"] = "auth_sessions"
    resource_kind: Literal["auth_session"] = "auth_session"
    resource_id: UUID


class SessionDeletionFact(RestoreSessionContract):
    """字段与删除事实快照v1相同；这里只接受登录会话类别。"""

    schema_version: Literal["supportops_deletion_fact_v1"] = "supportops_deletion_fact_v1"
    id: UUID
    tenant_id: UUID
    subject_user_id: UUID
    erasure_request_id: UUID
    category: Literal["auth_sessions"] = "auth_sessions"
    resource_key_digest: Digest
    scope_digest: Digest
    action: Literal["quarantined", "erased"]
    evidence_digest: Digest
    maintenance_identity: Identifier
    record_digest: Digest
    recorded_at: AwareDatetime

    @model_validator(mode="after")
    def fixed_fact(self) -> "SessionDeletionFact":
        if restore_session_digest(self, exclude={"record_digest"}) != self.record_digest:
            raise ValueError("RESTORE_SESSION_FACT_INTEGRITY_FAILED")
        return self


class RestoreSessionRequest(RestoreSessionContract):
    schema_version: Literal["supportops_restore_session_request_v1"] = "supportops_restore_session_request_v1"
    request_id: UUID
    restore_id: UUID
    context_digest: Digest
    backup_id: str = Field(pattern=r"^supportops-backup\.[A-Za-z0-9]{12}$")
    target_alias: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    ledger_id: UUID
    ledger_generation: int = Field(ge=1, le=2**63 - 1)
    ledger_snapshot_digest: Digest
    authority_id: Identifier
    expected_database: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_-]{0,62}$", repr=False)
    expected_maintenance_role: str = Field(pattern=r"^[a-z_][a-z0-9_]{0,62}$", repr=False)
    locator: RestoredSessionLocator = Field(repr=False)
    fact: SessionDeletionFact = Field(repr=False)
    created_at: AwareDatetime
    expires_at: AwareDatetime
    operation: Literal["quarantine_auth_session"] = "quarantine_auth_session"

    @model_validator(mode="after")
    def fixed_scope(self) -> "RestoreSessionRequest":
        if (self.locator.tenant_id != self.fact.tenant_id
            or self.locator.subject_user_id != self.fact.subject_user_id
            or restore_session_digest(self.locator) != self.fact.resource_key_digest):
            raise ValueError("RESTORE_SESSION_RESOURCE_BINDING_INVALID")
        if (not self.fact.recorded_at <= self.created_at < self.expires_at
            or (self.expires_at - self.created_at).total_seconds() > 60):
            raise ValueError("RESTORE_SESSION_REQUEST_WINDOW_INVALID")
        return self


class RestoreSessionAudit(RestoreSessionContract):
    schema_version: Literal["supportops_restore_session_audit_v1"] = "supportops_restore_session_audit_v1"
    event_id: UUID
    restore_id: UUID
    request_id: UUID
    request_digest: Digest
    fact_digest: Digest
    resource_key_digest: Digest
    event: Literal["quarantine_requested", "commit_observed", "quarantine_verified", "target_not_observed"]
    observed_at: AwareDatetime


class RestoreSessionReceipt(RestoreSessionContract):
    schema_version: Literal["supportops_restore_session_receipt_v1"] = "supportops_restore_session_receipt_v1"
    request_id: UUID
    request_digest: Digest
    status: Literal["quarantined", "already_quarantined", "not_observed"]
    revoked_at: AwareDatetime | None
    observed_at: AwareDatetime
    access_revocation_confirmed: bool
    original_erasure_reapplied: Literal[False] = False
    restore_access_allowed: Literal[False] = False
    automatic_retries_enabled: Literal[False] = False

    @model_validator(mode="after")
    def receipt_consistency(self) -> "RestoreSessionReceipt":
        confirmed = self.status != "not_observed"
        if self.access_revocation_confirmed != confirmed or (self.revoked_at is not None) != confirmed:
            raise ValueError("RESTORE_SESSION_RECEIPT_INVALID")
        if self.revoked_at is not None and self.revoked_at > self.observed_at:
            raise ValueError("RESTORE_SESSION_RECEIPT_TIME_INVALID")
        return self
