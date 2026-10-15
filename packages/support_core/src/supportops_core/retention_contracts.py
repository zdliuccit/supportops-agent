"""保留与擦除的预览契约；策略草稿/影响计数均不构成批准或执行许可。"""
from datetime import datetime
from typing import Literal
from uuid import UUID
from pydantic import Field, model_validator
from supportops_core.agent_config import StrictModel

DataCategory = Literal["identity", "auth_sessions", "conversations", "messages", "runs", "events",
    "observations", "knowledge", "credentials", "actions", "approvals", "checkpoints", "memory", "backups", "external_copies", "governance_audit"]
DATA_CATEGORIES: tuple[DataCategory, ...] = ("identity", "auth_sessions", "conversations", "messages", "runs", "events",
    "observations", "knowledge", "credentials", "actions", "approvals", "checkpoints", "memory", "backups", "external_copies", "governance_audit")


class RetentionRule(StrictModel):
    category: DataCategory
    retain_days: int | None = Field(default=None, ge=1, le=36500, strict=True)
    legal_hold_required: bool | None = Field(default=None, strict=True)
    backup_handling: Literal["unresolved", "isolate_on_restore"] = "unresolved"


class RetentionPolicyCreate(StrictModel):
    name: str = Field(min_length=1, max_length=100)
    rules: list[RetentionRule] = Field(min_length=1, max_length=16)
    reviewer_user_ids: list[UUID] = Field(default_factory=list, max_length=20)
    schema_version: Literal["retention_policy_draft_v1"] = "retention_policy_draft_v1"
    reviewer_strategy: Literal["explicit_subject_scope_non_self"] = "explicit_subject_scope_non_self"
    automatic_cleanup_enabled: Literal[False] = False

    @model_validator(mode="after")
    def unique_scope(self) -> "RetentionPolicyCreate":
        if len({rule.category for rule in self.rules}) != len(self.rules) or len(set(self.reviewer_user_ids)) != len(self.reviewer_user_ids):
            raise ValueError("策略类别与候选复核人员不能重复")
        return self


class RetentionPolicySummary(StrictModel):
    id: UUID
    name: str
    policy_digest: str
    status: Literal["draft"] = "draft"
    created_by: UUID
    created_at: datetime
    approval_granted: Literal[False] = False


class RetentionPolicyDetail(StrictModel):
    policy: RetentionPolicySummary
    definition: RetentionPolicyCreate | None
    integrity_status: Literal["recorded", "integrity_failed"]


class ErasureRequestCreate(StrictModel):
    categories: list[DataCategory] = Field(min_length=1, max_length=16)
    reason_code: Literal["privacy_request", "account_closure", "data_correction"]

    @model_validator(mode="after")
    def unique_categories(self) -> "ErasureRequestCreate":
        if len(set(self.categories)) != len(self.categories):
            raise ValueError("申请类别不能重复")
        return self


class ErasurePreviewCommand(StrictModel):
    expected_revision: int = Field(ge=1, strict=True)


class ErasureRequestSummary(StrictModel):
    id: UUID
    categories: list[DataCategory]
    reason_code: str
    status: Literal["requested", "previewed", "withdrawn"]
    revision: int
    policy_id: UUID | None
    policy_digest: str | None
    scope_digest: str
    cutoff_at: datetime
    created_at: datetime
    previewed_at: datetime | None
    withdrawn_at: datetime | None
    execution_enabled: Literal[False] = False


class ErasureTableImpact(StrictModel):
    table: str
    row_count: int | None = Field(default=None, ge=0)
    relation: Literal["subject_owned", "subject_participation", "shared_reference", "unresolved"]
    reason_code: str | None = None


class ErasureCategoryImpact(StrictModel):
    category: DataCategory
    known_related_rows: int = Field(ge=0)
    unknown_table_count: int = Field(ge=0)
    tables: list[ErasureTableImpact] = Field(max_length=16)
    blockers: list[str] = Field(max_length=16)
    complete_inventory: Literal[False] = False
    eligible_for_erasure: Literal[False] = False


class ErasurePreviewReport(StrictModel):
    schema_version: Literal["erasure_preview_v1"] = "erasure_preview_v1"
    request_id: UUID
    scope_digest: str
    policy_digest: str | None
    cutoff_at: datetime
    observed_at: datetime
    finished_at: datetime
    impacts: list[ErasureCategoryImpact] = Field(max_length=16)
    active_run_count: int = Field(ge=0)
    unresolved_action_count: int = Field(ge=0)
    blockers: list[str] = Field(max_length=16)
    complete_inventory: Literal[False] = False
    snapshot_is_execution_permit: Literal[False] = False


class ErasureReviewScope(StrictModel):
    scope_digest: str
    preview_digest: str
    approval_scope_digest: str
    reviewer_strategy: Literal["explicit_subject_scope_non_self"] = "explicit_subject_scope_non_self"
    separate_maintenance_identity_required: Literal[True] = True
    fresh_inventory_required: Literal[True] = True
    approval_granted: Literal[False] = False
    execution_enabled: Literal[False] = False


class ErasureRequestDetail(StrictModel):
    request: ErasureRequestSummary
    report: ErasurePreviewReport | None
    report_digest: str | None
    report_status: Literal["not_recorded", "recorded", "integrity_failed"]
    review_scope: ErasureReviewScope | None
