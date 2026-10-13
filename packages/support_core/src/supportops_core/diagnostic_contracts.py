"""有界 429 诊断状态；查询事实与根因确认分开。"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field

from supportops_core.agent_config import StrictModel

DiagnosticStageName = Literal["classify", "collect_information", "query_logs", "query_profile", "query_route", "query_incidents", "retrieve_rules", "evaluate", "escalate", "complete"]


class DiagnosticStage(StrictModel):
    name: DiagnosticStageName
    status: Literal["running", "completed", "skipped", "failed"]
    started_at: datetime
    finished_at: datetime | None = None
    error_code: str | None = None


class DiagnosticSource(StrictModel):
    invocation_id: UUID
    tool_id: str
    implementation_version: int
    catalog_version: int
    result_digest: str
    observed_at: datetime
    reused: bool = False


class DiagnosticFact(StrictModel):
    tool_id: str
    field: str
    value: str | int | float | bool | None
    invocation_id: UUID
    observed_at: datetime
    confirmation: Literal["source_observed"] = "source_observed"


class DiagnosticCandidate(StrictModel):
    code: Literal["rpm_exceeded", "tpm_reservation_exceeded", "project_concurrency_exceeded", "plan_quota_exhausted", "upstream_rate_limited"]
    description: str
    confirmation: Literal["confirmed_mock", "unconfirmed"] = "unconfirmed"
    source_ids: list[UUID]
    evidence_ids: list[UUID] = Field(default_factory=list)


class DiagnosticEscalation(StrictModel):
    """接管包只包含已授权的事实引用；不代表已经建立工单。"""
    status: Literal["recommended"] = "recommended"
    reason_codes: list[str]
    confirmed_fact_count: int
    source_ids: list[UUID]
    evidence_ids: list[UUID]
    completed_steps: list[DiagnosticStageName]
    unresolved_items: list[str]


class DiagnosticReport(StrictModel):
    schema_version: Literal["1"] = "1"
    workflow_version: Literal["mock_429_workflow_v1"] = "mock_429_workflow_v1"
    data_environment: Literal["mock"] = "mock"
    run_id: UUID
    attempt_id: UUID
    status: Literal["running", "needs_information", "confirmed_mock", "unconfirmed", "conflict", "unavailable"] = "running"
    request_id: str | None = None
    max_steps: Literal[10] = 10
    max_queries: Literal[5] = 5
    stages: list[DiagnosticStage] = Field(default_factory=list, max_length=10)
    sources: list[DiagnosticSource] = Field(default_factory=list, max_length=4)
    facts: list[DiagnosticFact] = Field(default_factory=list, max_length=80)
    candidates: list[DiagnosticCandidate] = Field(default_factory=list, max_length=5)
    evidence_ids: list[UUID] = Field(default_factory=list, max_length=6)
    unresolved_items: list[str] = Field(default_factory=list, max_length=20)
    reason_codes: list[str] = Field(default_factory=list, max_length=20)
    escalation: DiagnosticEscalation | None = None
    finished_at: datetime | None = None
