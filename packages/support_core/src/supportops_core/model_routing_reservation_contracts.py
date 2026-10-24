"""逐物理路由调用的选型与预算记录；记录不授予出站执行许可。"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from supportops_core.model_routing_contracts import (
    BoundaryLabel, Digest, RoutingCallRequirements, RoutingCandidateReference, RoutingFilterModel,
)


class RoutingCallPlan(RoutingFilterModel):
    schema_version: Literal["routing_call_plan_v1"] = "routing_call_plan_v1"
    call_id: UUID
    run_id: UUID
    conversation_id: UUID
    attempt_id: UUID
    generation: int = Field(ge=1, le=2**31 - 1, strict=True)
    owner_id: str = Field(min_length=1, max_length=100)
    purpose: BoundaryLabel = "support_response"
    model_input_digest: Digest
    context_snapshot_digest: Digest
    requirements: RoutingCallRequirements
    candidates: tuple[RoutingCandidateReference, ...] = Field(min_length=1, max_length=8)
    omitted_message_count: int = Field(default=0, ge=0, le=1_000_000, strict=True)
    created_at: datetime
    expires_at: datetime

    @model_validator(mode="after")
    def valid_plan(self) -> "RoutingCallPlan":
        if (self.created_at.tzinfo is None or self.expires_at.tzinfo is None
            or not 0 < (self.expires_at - self.created_at).total_seconds() <= 30):
            raise ValueError("调用预留计划必须是带时区的最长30秒窗口")
        for values in (
            [row.candidate_id for row in self.candidates],
            [row.model_version_id for row in self.candidates],
            [row.priority for row in self.candidates],
        ):
            if len(set(values)) != len(values):
                raise ValueError("候选ID、模型版本或优先级不能重复")
        return self


class RoutingBudgetAuthorization(RoutingFilterModel):
    """可信政策存储复查后的单次范围；不能由客户端或模型自填。"""

    plan_digest: Digest
    actor_user_id: UUID
    approval_reference_digest: Digest
    run_model_call_limit: int = Field(ge=1, le=100, strict=True)
    run_cost_limit_microusd: int = Field(ge=0, le=2**63 - 1, strict=True)
    run_timeout_seconds: int = Field(ge=1, le=3600, strict=True)
    checked_at: datetime
    expires_at: datetime

    @model_validator(mode="after")
    def valid_window(self) -> "RoutingBudgetAuthorization":
        if (self.checked_at.tzinfo is None or self.expires_at.tzinfo is None
            or not 0 < (self.expires_at - self.checked_at).total_seconds() <= 30):
            raise ValueError("预算授权必须是带时区的最长30秒窗口")
        return self


class RoutingReservationRecord(RoutingFilterModel):
    schema_version: Literal["routing_reservation_record_v1"] = "routing_reservation_record_v1"
    call_id: UUID
    tenant_id: UUID
    run_id: UUID
    conversation_id: UUID
    attempt_id: UUID
    generation: int = Field(ge=1, strict=True)
    plan_digest: Digest
    model_input_digest: Digest
    context_snapshot_digest: Digest
    agent_version_id: UUID
    run_original_model_version_id: UUID
    policy_version_id: UUID
    policy_digest: Digest
    approval_reference_digest: Digest
    candidate_id: UUID
    candidate_digest: Digest
    actual_model_version_id: UUID
    credential_revision: int = Field(ge=1, strict=True)
    selection_reason: Literal["first_eligible_priority_v1", "first_eligible_failover_v1"] = "first_eligible_priority_v1"
    filter_report_digest: Digest
    requirements_digest: Digest
    candidate_set_digest: Digest
    call_number: int = Field(ge=1, strict=True)
    reserved_input_tokens: int = Field(ge=1, strict=True)
    reserved_output_tokens: int = Field(ge=1, strict=True)
    reserved_cost_microusd: int = Field(ge=0, le=2**63 - 1, strict=True)
    recorded_at: datetime
    execution_permitted: Literal[False] = False
