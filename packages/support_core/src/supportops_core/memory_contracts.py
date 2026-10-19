"""W25.2 长期记忆设计契约；不提取、保存、确认或召回实际记忆。"""

import hashlib
import json
from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

MemoryDigest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
MemoryType = Literal["response_preference", "user_environment", "stable_support_context"]


class MemoryContract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, hide_input_in_errors=True)

    @field_validator("*", mode="after")
    @classmethod
    def utc_time(cls, value: object) -> object:
        if isinstance(value, datetime):
            return value.astimezone(UTC)
        return value


def memory_contract_digest(value: MemoryContract) -> str:
    """规范摘要只证明数据绑定，不证明同意、授权或政策已获批准。"""
    body = json.dumps(value.model_dump(mode="json"), ensure_ascii=False,
        sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(body).hexdigest()


class MemoryPolicy(MemoryContract):
    """不可变候选政策；空集合表示无允许范围，空期限不表示永久保留。"""

    schema_version: Literal["memory_policy_design_v1"] = "memory_policy_design_v1"
    policy_id: UUID
    tenant_id: UUID
    version: int = Field(ge=1)
    previous_policy_digest: MemoryDigest | None = None
    created_by_user_id: UUID
    created_at: AwareDatetime
    allowed_types: tuple[MemoryType, ...] = Field(default=(), max_length=3)
    allowed_agent_ids: tuple[UUID, ...] = Field(default=(), max_length=32)
    candidate_ttl_seconds: int | None = Field(default=None, ge=1)
    confirmed_ttl_seconds: int | None = Field(default=None, ge=1)
    max_content_bytes: int = Field(default=4096, ge=1, le=8192)
    retention_policy_id: UUID | None = None
    retention_policy_digest: MemoryDigest | None = None
    valid_until: AwareDatetime | None = None
    subject_scope: Literal["self"] = "self"
    confirmation_required: Literal[True] = True
    legal_hold_status: Literal["unknown"] = "unknown"
    status: Literal["draft"] = "draft"
    approval_granted: Literal[False] = False
    execution_enabled: Literal[False] = False

    @model_validator(mode="after")
    def policy_consistency(self) -> "MemoryPolicy":
        if (self.version == 1) != (self.previous_policy_digest is None):
            raise ValueError("MEMORY_POLICY_VERSION_INVALID")
        if (len(set(self.allowed_types)) != len(self.allowed_types)
            or len(set(self.allowed_agent_ids)) != len(self.allowed_agent_ids)):
            raise ValueError("MEMORY_POLICY_SCOPE_DUPLICATED")
        if (self.retention_policy_id is None) != (self.retention_policy_digest is None):
            raise ValueError("MEMORY_RETENTION_BINDING_INVALID")
        if self.valid_until is not None and self.valid_until <= self.created_at:
            raise ValueError("MEMORY_POLICY_WINDOW_INVALID")
        return self


class MemorySource(MemoryContract):
    """只引用本人原用户消息；不复制正文，也不把工具状态作为稳定记忆事实。"""

    tenant_id: UUID
    subject_user_id: UUID
    conversation_id: UUID
    message_id: UUID
    message_input_sequence: int = Field(ge=1)
    message_digest: MemoryDigest
    captured_at: AwareDatetime
    source_kind: Literal["user_message"] = "user_message"


class MemoryRecordScope(MemoryContract):
    """一个不可变记忆修订；确认和撤销均绑定本范围摘要。"""

    schema_version: Literal["memory_record_scope_v1"] = "memory_record_scope_v1"
    tenant_id: UUID
    subject_user_id: UUID
    agent_id: UUID
    origin_agent_version_id: UUID
    record_id: UUID
    revision_id: UUID
    revision: int = Field(ge=1)
    previous_revision_id: UUID | None = None
    previous_revision_digest: MemoryDigest | None = None
    policy_id: UUID
    policy_version: int = Field(ge=1)
    policy_digest: MemoryDigest
    memory_type: MemoryType
    lifetime_class: Literal["candidate", "confirmed"] = "candidate"
    memory_key: str = Field(pattern=r"^[a-z][a-z0-9_.]{0,63}$")
    content_digest: MemoryDigest
    sources: tuple[MemorySource, ...] = Field(min_length=1, max_length=8, repr=False)
    created_at: AwareDatetime
    valid_from: AwareDatetime
    valid_until: AwareDatetime
    visibility: Literal["subject_and_agent"] = "subject_and_agent"

    @model_validator(mode="after")
    def scope_consistency(self) -> "MemoryRecordScope":
        previous = (self.previous_revision_id, self.previous_revision_digest)
        if ((self.revision == 1 and any(item is not None for item in previous))
            or (self.revision > 1 and any(item is None for item in previous))
            or self.previous_revision_id == self.revision_id):
            raise ValueError("MEMORY_RECORD_VERSION_INVALID")
        if not self.created_at <= self.valid_from < self.valid_until:
            raise ValueError("MEMORY_RECORD_WINDOW_INVALID")
        if len({item.message_id for item in self.sources}) != len(self.sources):
            raise ValueError("MEMORY_SOURCE_DUPLICATED")
        if any(item.tenant_id != self.tenant_id or item.subject_user_id != self.subject_user_id
            or item.captured_at > self.created_at for item in self.sources):
            raise ValueError("MEMORY_SOURCE_SCOPE_INVALID")
        return self


class MemoryConfirmation(MemoryContract):
    """未来服务记录的确认事实；客户端或模型不能凭此结构自行授予确认。"""

    confirmation_id: UUID
    scope_digest: MemoryDigest
    subject_user_id: UUID
    method: Literal["subject_explicit", "approved_rule"]
    confirmed_at: AwareDatetime
    decision_reference_digest: MemoryDigest
    confirmed_by_user_id: UUID | None = None
    rule_version_id: UUID | None = None

    @model_validator(mode="after")
    def confirmer_consistency(self) -> "MemoryConfirmation":
        if self.method == "subject_explicit":
            if self.confirmed_by_user_id != self.subject_user_id or self.rule_version_id is not None:
                raise ValueError("MEMORY_CONFIRMATION_ACTOR_INVALID")
        elif self.confirmed_by_user_id is not None or self.rule_version_id is None:
            raise ValueError("MEMORY_CONFIRMATION_RULE_INVALID")
        return self


class MemoryRevocation(MemoryContract):
    event_id: UUID
    scope_digest: MemoryDigest
    revoked_at: AwareDatetime
    reason: Literal["rejected", "withdrawn", "superseded", "policy_revoked", "source_revoked", "erasure_requested", "tombstoned"]
    actor_user_id: UUID | None = None
    replacement_revision_id: UUID | None = None
    tombstone_id: UUID | None = None

    @model_validator(mode="after")
    def revocation_consistency(self) -> "MemoryRevocation":
        if ((self.reason == "superseded") != (self.replacement_revision_id is not None)
            or (self.reason == "tombstoned") != (self.tombstone_id is not None)):
            raise ValueError("MEMORY_REVOCATION_BINDING_INVALID")
        return self


class MemoryRecord(MemoryContract):
    """生命周期投影；tombstoned只可呈现无正文投影，不表示执行过物理擦除。"""

    schema_version: Literal["memory_record_design_v1"] = "memory_record_design_v1"
    scope: MemoryRecordScope
    content: str | None = Field(default=None, min_length=1, max_length=8192, repr=False)
    status: Literal["candidate", "confirmed", "rejected", "withdrawn", "superseded", "tombstoned"]
    confirmation: MemoryConfirmation | None = None
    revocation: MemoryRevocation | None = None
    recall_enabled: Literal[False] = False
    execution_enabled: Literal[False] = False

    @model_validator(mode="after")
    def record_consistency(self) -> "MemoryRecord":
        scope_digest = memory_contract_digest(self.scope)
        if self.status == "tombstoned":
            if self.content is not None:
                raise ValueError("MEMORY_TOMBSTONE_CONTENT_FORBIDDEN")
        elif self.content is None or not self.content.strip() or (
            len(self.content.encode("utf-8")) > 8192
            or hashlib.sha256(self.content.encode("utf-8")).hexdigest() != self.scope.content_digest
        ):
            raise ValueError("MEMORY_CONTENT_DIGEST_INVALID")
        if self.confirmation is not None and (
            self.confirmation.scope_digest != scope_digest
            or self.confirmation.subject_user_id != self.scope.subject_user_id
            or not self.scope.created_at <= self.confirmation.confirmed_at < self.scope.valid_until
        ):
            raise ValueError("MEMORY_CONFIRMATION_SCOPE_INVALID")
        if (self.status == "confirmed" or self.scope.lifetime_class == "confirmed") and self.confirmation is None:
            raise ValueError("MEMORY_CONFIRMATION_REQUIRED")
        if self.status in {"candidate", "rejected"} and self.confirmation is not None:
            raise ValueError("MEMORY_CONFIRMATION_STATUS_INVALID")
        if self.status in {"candidate", "confirmed"}:
            if self.revocation is not None:
                raise ValueError("MEMORY_REVOCATION_STATUS_INVALID")
        else:
            revocation = self.revocation
            if (revocation is None or revocation.scope_digest != scope_digest
                or revocation.revoked_at < self.scope.created_at
                or (self.confirmation is not None and revocation.revoked_at < self.confirmation.confirmed_at)
                or revocation.replacement_revision_id == self.scope.revision_id):
                raise ValueError("MEMORY_REVOCATION_SCOPE_INVALID")
            expected = {"withdrawn", "policy_revoked", "source_revoked", "erasure_requested"} if self.status == "withdrawn" else {self.status}
            if revocation.reason not in expected:
                raise ValueError("MEMORY_REVOCATION_STATUS_INVALID")
            if revocation.reason in {"rejected", "withdrawn", "erasure_requested"} and revocation.actor_user_id != self.scope.subject_user_id:
                raise ValueError("MEMORY_REVOCATION_ACTOR_INVALID")
        return self


def validate_memory_policy_binding(policy: MemoryPolicy, record: MemoryRecord) -> None:
    """仅核对设计对象是否相容；即使通过，草稿也没有保存、确认或召回许可。"""
    scope = record.scope
    if (scope.tenant_id != policy.tenant_id or scope.policy_id != policy.policy_id
        or scope.policy_version != policy.version or scope.policy_digest != memory_contract_digest(policy)
        or scope.memory_type not in policy.allowed_types or scope.agent_id not in policy.allowed_agent_ids
        or scope.created_at < policy.created_at):
        raise ValueError("MEMORY_POLICY_BINDING_INVALID")
    ttl = policy.candidate_ttl_seconds if scope.lifetime_class == "candidate" else policy.confirmed_ttl_seconds
    if ttl is None or policy.valid_until is None:
        raise ValueError("MEMORY_POLICY_WINDOW_UNCONFIGURED")
    if (scope.valid_until > policy.valid_until
        or (scope.valid_until - scope.created_at).total_seconds() > ttl):
        raise ValueError("MEMORY_POLICY_WINDOW_INVALID")
    if record.content is not None and len(record.content.encode("utf-8")) > policy.max_content_bytes:
        raise ValueError("MEMORY_POLICY_CONTENT_LIMIT_EXCEEDED")
