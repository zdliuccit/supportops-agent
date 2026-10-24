"""专业子任务共享父账本的预留元数据；不是物理派发或执行许可。"""

from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from supportops_core.model_routing_accounting_contracts import RoutingPriceSnapshot, routing_usage_cost
from supportops_core.specialist_task_contracts import (
    Count, Digest, SpecialistBudgetBinding, SpecialistContract, SpecialistVersion, contract_digest,
)

SPECIALIST_BUDGET_POLICY = "specialist_knowledge_budget_v1"


class SpecialistBudgetAuthorization(SpecialistContract):
    """仅独立可信启动/输入计量提供者返回，不提供HTTP上传入口。"""

    schema_version: Literal["specialist_budget_authorization_v1"] = "specialist_budget_authorization_v1"
    call_id: UUID
    task_input_digest: Digest
    schedule_digest: Digest
    context_digest: Digest
    budget_digest: Digest
    model_input_digest: Digest
    governance_decision_digest: Digest
    credential_revision: int = Field(ge=1, le=2**31 - 1)
    reserved_input_tokens: Count = Field(ge=1)
    reserved_output_tokens: Count = Field(ge=1)
    token_strategy: Literal["utf8_upper_bound_v1"] = "utf8_upper_bound_v1"
    physical_call_limit: Literal[1] = 1
    tool_call_limit: Literal[0] = 0
    implicit_retries: Literal[0] = 0
    checked_at: AwareDatetime
    expires_at: AwareDatetime

    @model_validator(mode="after")
    def fixed_window(self) -> "SpecialistBudgetAuthorization":
        if not self.checked_at < self.expires_at:
            raise ValueError("SPECIALIST_BUDGET_AUTHORIZATION_INVALID")
        return self


class SpecialistBudgetReservation(SpecialistContract):
    schema_version: Literal["specialist_budget_reservation_v1"] = "specialist_budget_reservation_v1"
    call_id: UUID
    tenant_id: UUID
    conversation_id: UUID
    run_id: UUID
    attempt_id: UUID
    generation: int = Field(ge=1)
    task_id: UUID
    task_input_digest: Digest
    schedule_digest: Digest
    context_digest: Digest
    authorization_scope_digest: Digest
    authorization_digest: Digest
    governance_decision_digest: Digest
    model_input_digest: Digest
    parent_version: SpecialistVersion
    child_version: SpecialistVersion
    credential_revision: int = Field(ge=1, le=2**31 - 1)
    budget: SpecialistBudgetBinding
    price_snapshot: RoutingPriceSnapshot
    call_number: int = Field(ge=1)
    reserved_input_tokens: Count = Field(ge=1)
    reserved_output_tokens: Count = Field(ge=1)
    reserved_cost_microusd: int = Field(ge=0, le=2**63 - 1)
    recorded_at: AwareDatetime
    expires_at: AwareDatetime
    physical_call_limit: Literal[1] = 1
    tool_call_limit: Literal[0] = 0
    implicit_retries: Literal[0] = 0
    execution_permitted: Literal[False] = False

    @model_validator(mode="after")
    def exact_price_and_scope(self) -> "SpecialistBudgetReservation":
        price = self.price_snapshot
        if (self.budget.parent_run_id != self.run_id
            or not self.recorded_at < self.expires_at <= self.budget.deadline
            or price.call_id != self.call_id or price.tenant_id != self.tenant_id
            or price.actual_model_version_id != self.child_version.model_version_id
            or price.candidate_digest != contract_digest(self.child_version)
            or self.reserved_input_tokens + self.reserved_output_tokens > self.budget.tokens_limit
            or self.reserved_cost_microusd > self.budget.cost_microusd_limit
            or routing_usage_cost(price, input_tokens=self.reserved_input_tokens,
                output_tokens=self.reserved_output_tokens) != self.reserved_cost_microusd):
            raise ValueError("SPECIALIST_BUDGET_RESERVATION_INVALID")
        return self
