"""隔离Mock补偿仅冻结关闭工单意向，不复用原建单批准或执行许可。"""
from datetime import datetime
from typing import Literal
from uuid import UUID
from pydantic import Field
from supportops_core.agent_config import StrictModel


class CompensationCreate(StrictModel):
    context_run_id: UUID
    expected_scope_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class CompensationScope(StrictModel):
    schema_version: Literal["mock_ticket_compensation_v1"] = "mock_ticket_compensation_v1"
    tenant_id: UUID
    subject_user_id: UUID
    action_id: UUID
    action_generation: int = Field(ge=1)
    action_status: Literal["succeeded", "needs_attention"]
    original_parameters_digest: str
    logical_key: str
    source_receipt_id: UUID
    source_receipt_digest: str
    source_receipt_status: Literal["verified", "conflict"]
    target_ticket_id: UUID
    target_external_id: str
    target_version: int = Field(ge=1)
    target_status: Literal["open", "in_progress", "escalated"]
    target_parameters_digest: str
    context_run_id: UUID
    agent_version_id: UUID
    config_digest: str
    model_version_id: UUID
    credential_revision: int = Field(ge=1)
    original_tool_implementation_version: int = Field(ge=1)
    context_tool_implementation_version: int = Field(ge=1)
    current_catalog_version: int = Field(ge=1)
    intent: Literal["close_mock_ticket"] = "close_mock_ticket"
    reason_code: Literal["verification_conflict", "user_requested_closure"]
    minimum_risk: Literal["R3", "R4"]
    approval_required: Literal[True] = True
    execution_enabled: Literal[False] = False


class CompensationReadiness(StrictModel):
    scope: CompensationScope | None
    scope_digest: str | None
    proposal_supported: bool
    blockers: list[str]
    approval_granted: Literal[False] = False
    execution_enabled: Literal[False] = False


class CompensationSummary(StrictModel):
    id: UUID
    action_id: UUID
    source_receipt_id: UUID
    scope_digest: str
    minimum_risk: Literal["R3", "R4"]
    reason_code: Literal["verification_conflict", "user_requested_closure"]
    intent: Literal["close_mock_ticket"] = "close_mock_ticket"
    status: Literal["proposed"] = "proposed"
    created_at: datetime
    approval_required: Literal[True] = True
    approval_granted: Literal[False] = False
    execution_enabled: Literal[False] = False


class CompensationDetail(StrictModel):
    proposal: CompensationSummary
    scope: CompensationScope | None
    integrity_status: Literal["recorded", "integrity_failed"]
    applicability: Literal["current", "changed", "unavailable", "integrity_failed"]
    blockers: list[str]
    approval_granted: Literal[False] = False
    execution_enabled: Literal[False] = False
