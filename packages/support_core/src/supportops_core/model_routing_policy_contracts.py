"""路由策略候选版本与永久撤销契约；保存元数据不构成批准。"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, field_validator, model_validator

from supportops_core.model_routing_contracts import (
    BoundaryLabel, Digest, RoutingCandidateReference, RoutingFilterModel,
)


class RoutingPolicyDefinition(RoutingFilterModel):
    """治理引用为预先登记的不透明引用摘要，不是最终批准/评测正文的内容摘要。

    最终批准须由独立可信来源绑定保存后的确切策略摘要并查证；不进入策略字节，
    避免策略与批准/评测报告互相摘要。提供引用不表示已有批准或评测结果。
    """

    schema_version: Literal["model_routing_policy_v1"] = "model_routing_policy_v1"
    selection_rule: Literal["first_eligible_priority_v1"] = "first_eligible_priority_v1"
    go_no_go_reference_digest: Digest
    data_boundary_approval_digest: Digest
    evaluation_reference_digest: Digest
    fallback_policy_digest: Digest
    budget_policy_digest: Digest
    allowed_purposes: tuple[BoundaryLabel, ...] = Field(min_length=1, max_length=16)
    allowed_regions: tuple[BoundaryLabel, ...] = Field(min_length=1, max_length=16)
    allowed_data_classifications: tuple[BoundaryLabel, ...] = Field(min_length=1, max_length=16)
    candidates: tuple[RoutingCandidateReference, ...] = Field(min_length=1, max_length=8)
    primary_candidate_id: UUID
    valid_until: AwareDatetime

    @field_validator("valid_until", mode="before")
    @classmethod
    def explicit_timestamp(cls, value: object) -> object:
        if not isinstance(value, (str, datetime)):
            raise ValueError("策略期限须为带时区的时间，不接受数字时间戳")
        if isinstance(value, str):
            try:
                if datetime.fromisoformat(value).tzinfo is None:
                    raise ValueError()
            except ValueError:
                raise ValueError("策略期限须为带时区的ISO时间") from None
        return value

    @model_validator(mode="after")
    def fixed_sets(self) -> "RoutingPolicyDefinition":
        for values in (
            self.allowed_purposes, self.allowed_regions, self.allowed_data_classifications,
            tuple(row.candidate_id for row in self.candidates),
            tuple(row.model_version_id for row in self.candidates),
            tuple(row.priority for row in self.candidates),
        ):
            if len(values) != len(set(values)):
                raise ValueError("策略范围、候选和优先级不能重复")
        first = min(self.candidates, key=lambda row: row.priority)
        if first.candidate_id != self.primary_candidate_id:
            raise ValueError("主候选须在集合内且具有首个优先级")
        return self


class RoutingPolicySave(RoutingFilterModel):
    version_id: UUID
    expected_revision: int = Field(ge=0, le=2**31 - 2, strict=True)
    expected_policy_digest: Digest | None = None
    definition: RoutingPolicyDefinition

    @model_validator(mode="after")
    def predecessor(self) -> "RoutingPolicySave":
        if (self.expected_revision == 0) != (self.expected_policy_digest is None):
            raise ValueError("首次保存不含前版摘要，后续保存必须固定前版摘要")
        return self


class RoutingPolicyVersionRecord(RoutingFilterModel):
    schema_version: Literal["routing_policy_version_record_v1"] = "routing_policy_version_record_v1"
    version_id: UUID
    tenant_id: UUID
    policy_id: UUID
    revision: int = Field(ge=1, le=2**31 - 1, strict=True)
    previous_version_id: UUID | None
    previous_policy_digest: Digest | None
    created_by: UUID
    created_at: AwareDatetime
    definition: RoutingPolicyDefinition

    @model_validator(mode="after")
    def predecessor(self) -> "RoutingPolicyVersionRecord":
        if ((self.revision == 1) != (self.previous_version_id is None)
            or (self.previous_version_id is None) != (self.previous_policy_digest is None)
            or self.previous_version_id == self.version_id
            or self.definition.valid_until <= self.created_at):
            raise ValueError("策略前版或初始有效期不符合不可变版本契约")
        return self


RoutingRevocationReason = Literal[
    "operator_disabled", "policy_replaced", "go_no_go_withdrawn",
    "data_boundary_withdrawn", "evaluation_withdrawn",
]


class RoutingPolicyRevoke(RoutingFilterModel):
    expected_policy_digest: Digest
    reason: RoutingRevocationReason


class RoutingPolicyRevocationRecord(RoutingFilterModel):
    schema_version: Literal["routing_policy_revocation_v1"] = "routing_policy_revocation_v1"
    revocation_id: UUID
    tenant_id: UUID
    policy_id: UUID
    version_id: UUID
    policy_digest: Digest
    revoked_by: UUID
    revoked_at: AwareDatetime
    reason: RoutingRevocationReason


class RoutingPolicyVersionView(RoutingFilterModel):
    record: RoutingPolicyVersionRecord
    policy_digest: Digest
    status: Literal["candidate", "expired", "revoked"]
    revocation: RoutingPolicyRevocationRecord | None = None
    revocation_digest: Digest | None = None
    candidate_validation_status: Literal["not_assessed"] = "not_assessed"
    approvals_verified: Literal[False] = False
    routing_enabled: Literal[False] = False
    execution_permitted: Literal[False] = False
