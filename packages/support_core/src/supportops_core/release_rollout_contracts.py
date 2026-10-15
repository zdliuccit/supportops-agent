"""灰度策略及只读观测契约；没有默认批准策略或人工填写通过状态。"""
from datetime import datetime
from typing import Literal
from uuid import UUID
from pydantic import Field
from supportops_core.agent_config import StrictModel
from supportops_core.release_gate_services import ReleaseReadiness

RolloutStatus = Literal["draft", "active", "paused", "stopped"]


class ReleaseRolloutPolicy(StrictModel):
    schema_version: Literal["release_rollout_policy_v1"] = "release_rollout_policy_v1"
    observation_seconds: int = Field(ge=300, le=86400, strict=True)
    minimum_runs_per_version: int = Field(ge=3, le=5000, strict=True)
    max_failure_rate: float = Field(ge=0, le=0.1, strict=True, allow_inf_nan=False)
    max_average_cost_usd: float = Field(gt=0, le=1_000_000, strict=True, allow_inf_nan=False)
    max_p95_api_first_text_ms: int = Field(ge=1, le=3000, strict=True)
    max_p95_complete_ms: int = Field(ge=1, le=10000, strict=True)
    max_cost_increase_ratio: float = Field(ge=1, le=2, strict=True, allow_inf_nan=False)
    max_latency_increase_ratio: float = Field(ge=1, le=2, strict=True, allow_inf_nan=False)
    # API观测为提交可见上界；真实客户端延迟仍由独立离线发布门禁证明。
    first_text_source: Literal["api_database_visibility_v1"] = "api_database_visibility_v1"
    max_metadata_rows_per_version: Literal[5000] = 5000
    max_candidate_bps: Literal[9000] = 9000
    max_step_bps: Literal[1000] = 1000
    automatic_expansion: Literal[False] = False
    authorization_denial_tolerance: Literal[0] = 0


class ReleaseRolloutCreate(StrictModel):
    candidate_version_id: UUID
    initial_candidate_bps: int = Field(ge=1, le=1000, strict=True)
    subject_ids: list[UUID] = Field(default_factory=list, max_length=200)
    policy: ReleaseRolloutPolicy


class ReleaseRolloutCommand(StrictModel):
    expected_revision: int = Field(ge=1, strict=True)
    operation: Literal["start", "pause", "resume", "expand", "stop"]
    candidate_bps: int | None = Field(default=None, ge=1, le=9000, strict=True)


class ReleaseRolloutSummary(StrictModel):
    id: UUID
    agent_id: UUID
    baseline_version_id: UUID
    candidate_version_id: UUID
    status: RolloutStatus
    candidate_bps: int
    revision: int
    epoch: int
    policy_digest: str
    definition_digest: str
    epoch_started_at: datetime | None
    last_checked_at: datetime | None
    reason_code: str | None
    created_at: datetime
    updated_at: datetime


class RolloutCohortMetrics(StrictModel):
    selection: Literal["baseline", "candidate"]
    version_id: UUID
    run_count: int
    completed: int
    failed: int
    pending: int
    cancelled: int
    failure_rate: float | None
    average_cost_usd: float | None
    p95_api_first_text_ms: int | None
    p95_complete_ms: int | None
    missing_cost_count: int
    missing_first_text_count: int
    missing_complete_count: int
    missing_result_count: int
    authorization_denial_count: int
    truncated: bool


class RolloutObservationCheck(StrictModel):
    code: str
    status: Literal["passed", "failed", "missing", "waiting"]
    message: str


class ReleaseRolloutObservation(StrictModel):
    schema_version: Literal["release_rollout_observation_v1"] = "release_rollout_observation_v1"
    observed_at: datetime
    epoch: int
    started_at: datetime | None
    earliest_expansion_at: datetime | None
    baseline: RolloutCohortMetrics
    candidate: RolloutCohortMetrics
    checks: list[RolloutObservationCheck]
    can_expand: bool
    pause_recommended: bool
    business_success_inferred: Literal[False] = False
    client_latency_proven: Literal[False] = False


class ReleaseRolloutDetail(StrictModel):
    rollout: ReleaseRolloutSummary
    subject_ids: list[UUID]
    policy: ReleaseRolloutPolicy
    policy_approved: bool
    supervisor_fresh: bool
    baseline_readiness: ReleaseReadiness
    candidate_readiness: ReleaseReadiness
    observation: ReleaseRolloutObservation
    start_allowed: bool
    resume_allowed: bool
    expand_allowed: bool
    disabled_reasons: list[str]


class ReleaseRolloutEventSummary(StrictModel):
    id: UUID
    revision: int
    actor_kind: Literal["user", "system"]
    actor_user_id: UUID | None
    event_type: str
    status: RolloutStatus
    candidate_bps: int
    epoch: int
    reason_code: str | None
    policy_digest: str
    created_at: datetime
