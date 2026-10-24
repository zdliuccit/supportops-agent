"""模型路由候选过滤契约；通过过滤仍不构成模型调用或路由启动许可。"""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import ConfigDict, Field, model_validator

from supportops_core.agent_config import StrictModel

Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$", max_length=64)]
BoundaryLabel = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$", max_length=64)]


class RoutingFilterModel(StrictModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, frozen=True)


class RoutingCandidateReference(RoutingFilterModel):
    candidate_id: UUID
    endpoint_id: UUID
    endpoint_model_id: UUID
    model_version_id: UUID
    credential_revision: int = Field(ge=1, le=2**31 - 1, strict=True)
    model_request_digest: Digest
    capability_digest: Digest
    pricing_digest: Digest
    data_boundary_digest: Digest
    priority: int = Field(ge=0, le=7, strict=True)


class RoutingCallRequirements(RoutingFilterModel):
    tenant_id: UUID
    agent_version_id: UUID
    policy_version_id: UUID
    policy_digest: Digest
    reserved_input_tokens: int = Field(ge=1, le=2_000_000, strict=True)
    reserved_output_tokens: int = Field(ge=1, le=128_000, strict=True)
    response_strategy: Literal["tool", "provider"]
    uses_tools: bool = Field(strict=True)
    streaming: bool = Field(strict=True)
    parallel_tool_calls: bool = Field(default=False, strict=True)
    vision: bool = Field(default=False, strict=True)
    allowed_regions: tuple[BoundaryLabel, ...] = Field(min_length=1, max_length=16)
    data_classification: BoundaryLabel
    max_call_cost_microusd: int = Field(ge=0, le=2**63 - 1, strict=True)

    @model_validator(mode="after")
    def valid_requirements(self) -> "RoutingCallRequirements":
        if len(set(self.allowed_regions)) != len(self.allowed_regions):
            raise ValueError("允许地域不能重复")
        if self.parallel_tool_calls and not self.uses_tools:
            raise ValueError("并行工具能力要求必须伴随工具使用")
        return self


class _RoutingDataBoundaryFields(RoutingFilterModel):
    tenant_id: UUID
    policy_version_id: UUID
    endpoint_id: UUID
    endpoint_model_id: UUID
    model_version_id: UUID
    model_request_digest: Digest
    approval_reference_digest: Digest
    processing_regions: tuple[BoundaryLabel, ...] = Field(min_length=1, max_length=16)
    allowed_data_classifications: tuple[BoundaryLabel, ...] = Field(min_length=1, max_length=16)

    @model_validator(mode="after")
    def unique_scope(self) -> "_RoutingDataBoundaryFields":
        if (len(set(self.processing_regions)) != len(self.processing_regions)
            or len(set(self.allowed_data_classifications)) != len(self.allowed_data_classifications)):
            raise ValueError("数据边界地域或数据分类不能重复")
        return self


class RoutingDataBoundaryScope(_RoutingDataBoundaryFields):
    """预先固定的数据范围；不包含当前策略摘要或批准事实，避免摘要循环。"""

    schema_version: Literal["routing_data_boundary_scope_v1"] = "routing_data_boundary_scope_v1"


class RoutingDataBoundary(_RoutingDataBoundaryFields):
    """可信治理读取器提供当前批准；范围摘要本身不是批准或执行许可。"""

    schema_version: Literal["routing_data_boundary_v1"] = "routing_data_boundary_v1"
    policy_digest: Digest
    approved_at: datetime
    expires_at: datetime
    revoked_at: datetime | None = None

    @model_validator(mode="after")
    def valid_boundary(self) -> "RoutingDataBoundary":
        if (self.approved_at.tzinfo is None or self.expires_at.tzinfo is None
            or self.approved_at >= self.expires_at
            or self.revoked_at is not None and self.revoked_at.tzinfo is None):
            raise ValueError("数据边界批准与有效期须有时区且顺序有效")
        return self


RoutingExclusionReason = Literal[
    "REFERENCE_UNAVAILABLE", "ENDPOINT_DISABLED", "MODEL_ARCHIVED", "CREDENTIAL_UNAVAILABLE",
    "CREDENTIAL_REVISION_CHANGED", "CREDENTIAL_REVOKED", "CREDENTIAL_PROVIDER_UNAVAILABLE",
    "REQUEST_DIGEST_MISMATCH", "CAPABILITY_DIGEST_MISMATCH", "PRICING_DIGEST_MISMATCH",
    "LATEST_PROBE_UNVERIFIED", "CAPABILITY_UNSUPPORTED", "CAPABILITY_UNCONFIRMED",
    "CONTEXT_LIMIT_UNKNOWN", "CONTEXT_LIMIT_EXCEEDED", "OUTPUT_LIMIT_UNKNOWN", "OUTPUT_LIMIT_EXCEEDED",
    "DATA_BOUNDARY_UNCONFIRMED", "DATA_BOUNDARY_MISMATCH", "DATA_BOUNDARY_EXPIRED", "DATA_BOUNDARY_REVOKED",
    "DATA_REGION_NOT_ALLOWED", "DATA_CLASSIFICATION_NOT_ALLOWED", "USD_PRICING_UNKNOWN", "CALL_COST_EXCEEDED",
]


class RoutingCandidateAssessment(RoutingFilterModel):
    candidate_id: UUID
    model_version_id: UUID
    priority: int = Field(ge=0, le=7, strict=True)
    status: Literal["eligible", "excluded"]
    reasons: tuple[RoutingExclusionReason, ...] = Field(max_length=25)
    estimated_cost_microusd: int | None = Field(default=None, ge=0, le=2**63 - 1, strict=True)
    latest_probe_id: UUID | None = None
    execution_permitted: Literal[False] = False

    @model_validator(mode="after")
    def valid_status(self) -> "RoutingCandidateAssessment":
        if (self.status == "eligible") != (not self.reasons):
            raise ValueError("候选状态必须与排除原因一致")
        if len(set(self.reasons)) != len(self.reasons):
            raise ValueError("候选排除原因不能重复")
        return self


class RoutingCandidateFilterReport(RoutingFilterModel):
    schema_version: Literal["routing_candidate_filter_v1"] = "routing_candidate_filter_v1"
    tenant_id: UUID
    agent_version_id: UUID
    policy_version_id: UUID
    policy_digest: Digest
    requirements_digest: Digest
    candidate_set_digest: Digest
    observed_at: datetime
    candidates: tuple[RoutingCandidateAssessment, ...] = Field(min_length=1, max_length=8)
    routing_enabled: Literal[False] = False
    budget_reserved: Literal[False] = False
    execution_permitted: Literal[False] = False
