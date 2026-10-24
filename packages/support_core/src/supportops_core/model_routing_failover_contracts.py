"""有限回退与熔断的封闭输入；观察/建议不授予预算、探测或派发许可。"""

from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from supportops_core.model_routing_accounting_contracts import RoutingAccountingView, RoutingExecutionUsageReceipt
from supportops_core.model_routing_contracts import Digest, RoutingCandidateReference, RoutingFilterModel
from supportops_core.release_manifest_services import manifest_digest


class RoutingFailoverControls(RoutingFilterModel):
    """独立于策略版本摘要，供原fallback_policy_digest固定，避免互相摘要。"""

    schema_version: Literal["routing_failover_controls_v1"] = "routing_failover_controls_v1"
    max_physical_calls: int = Field(default=2, ge=1, le=3, strict=True)
    failure_threshold: int = Field(default=3, ge=1, le=10, strict=True)
    cooldown_seconds: int = Field(default=30, ge=1, le=3600, strict=True)
    recovery_window_seconds: int = Field(default=10, ge=1, le=30, strict=True)
    observation_max_age_seconds: Literal[1] = 1
    implicit_sdk_retries: Literal[0] = 0
    repeat_candidate_allowed: Literal[False] = False
    failover_after_tool_allowed: Literal[False] = False
    failover_after_public_text_allowed: Literal[False] = False


class RoutingCircuitScope(RoutingFilterModel):
    tenant_id: UUID
    policy_version_id: UUID
    policy_digest: Digest
    candidate: RoutingCandidateReference
    controls_digest: Digest


CircuitOutcome = Literal["completed", "transient_failure", "rate_limited", "authentication_failure",
    "contract_failure", "cancelled", "unknown"]


class RoutingCircuitEvent(RoutingFilterModel):
    schema_version: Literal["routing_circuit_event_v1"] = "routing_circuit_event_v1"
    event_id: UUID
    scope_digest: Digest
    sequence: int = Field(ge=1, le=256, strict=True)
    previous_event_digest: Digest | None = None
    kind: Literal["observation", "recovery_claimed"]
    call_id: UUID
    outcome: CircuitOutcome | None = None
    receipt: RoutingExecutionUsageReceipt | None = None
    accounting_digest: Digest | None = None
    accounting_within_bounds: bool | None = Field(default=None, strict=True)
    recorded_at: AwareDatetime

    @model_validator(mode="after")
    def fixed_event(self) -> "RoutingCircuitEvent":
        if (self.sequence == 1) != (self.previous_event_digest is None):
            raise ValueError("ROUTING_CIRCUIT_EVENT_CHAIN_INVALID")
        if self.kind == "recovery_claimed":
            if (self.outcome is not None or self.receipt is not None or self.accounting_digest is not None
                or self.accounting_within_bounds is not None):
                raise ValueError("ROUTING_CIRCUIT_RECOVERY_CLAIM_INVALID")
        else:
            receipt = self.receipt
            expected = ({"completed", "failed"} if self.outcome == "contract_failure" else
                {self.outcome} if self.outcome in {"completed", "cancelled", "unknown"} else {"failed"})
            if (self.outcome is None or receipt is None or receipt.call_id != self.call_id
                or receipt.outcome not in expected or receipt.observed_at > self.recorded_at
                or (receipt.usage is None) != (self.accounting_digest is None)
                or (receipt.usage is None) != (self.accounting_within_bounds is None)):
                raise ValueError("ROUTING_CIRCUIT_OBSERVATION_INVALID")
        return self


CircuitReason = Literal["failure_threshold", "recovery_failed", "recovery_unconfirmed", "unsafe_outcome", "history_full"]
CircuitPhase = Literal["closed", "open", "half_open", "quarantined"]
FailoverStatus = Literal["proposed", "blocked", "completed"]


class RoutingCircuitSnapshot(RoutingFilterModel):
    schema_version: Literal["routing_circuit_snapshot_v1"] = "routing_circuit_snapshot_v1"
    scope: RoutingCircuitScope
    scope_digest: Digest
    phase: CircuitPhase
    reason: CircuitReason | None = None
    consecutive_failures: int = Field(ge=0, le=10, strict=True)
    event_count: int = Field(ge=0, le=256, strict=True)
    last_event_digest: Digest | None = None
    open_until: AwareDatetime | None = None
    recovery_call_id: UUID | None = None
    recovery_until: AwareDatetime | None = None
    observed_at: AwareDatetime
    execution_permitted: Literal[False] = False

    @model_validator(mode="after")
    def coherent_state(self) -> "RoutingCircuitSnapshot":
        if (manifest_digest(self.scope.model_dump(mode="json")) != self.scope_digest
            or (self.event_count == 0) != (self.last_event_digest is None)
            or (self.phase == "closed") != (self.reason is None)
            or (self.phase == "open") != (self.open_until is not None)
            or (self.phase == "half_open") != (self.recovery_call_id is not None and self.recovery_until is not None)
            or (self.recovery_call_id is None) != (self.recovery_until is None)):
            raise ValueError("ROUTING_CIRCUIT_SNAPSHOT_INVALID")
        return self


class RoutingFailoverAttempt(RoutingFilterModel):
    """可信持久源读到的原逻辑请求物理尝试；不接受模型/HTTP上传该观察。"""

    root_call_id: UUID
    ordinal: int = Field(ge=1, le=3, strict=True)
    accounting: RoutingAccountingView
    outcome: CircuitOutcome
    public_text_emitted: bool = Field(strict=True)
    business_tool_calls_started: int = Field(ge=0, le=1000, strict=True)
    tool_outcome: Literal["none", "observed", "unknown"]


FailoverReason = Literal["policy_expired", "scope_mismatch", "observations_stale", "history_invalid", "call_limit",
    "unsafe_outcome", "usage_unknown", "public_text_started", "tool_started", "no_candidate"]
CandidateFailoverReason = Literal["ineligible", "already_attempted", "circuit_missing", "circuit_open",
    "recovery_inflight", "circuit_quarantined", "observation_stale", "scope_mismatch"]


class RoutingFailoverCandidate(RoutingFilterModel):
    candidate_id: UUID
    model_version_id: UUID
    reason: CandidateFailoverReason | None


class RoutingFailoverDecision(RoutingFilterModel):
    schema_version: Literal["routing_failover_decision_v1"] = "routing_failover_decision_v1"
    plan_digest: Digest
    controls_digest: Digest
    status: FailoverStatus
    reason: FailoverReason | None
    attempts_consumed: int = Field(ge=0, le=3, strict=True)
    candidate_id: UUID | None = None
    recovery_trial_required: bool = Field(default=False, strict=True)
    candidates: tuple[RoutingFailoverCandidate, ...] = Field(max_length=8)
    observed_at: AwareDatetime
    budget_reserved: Literal[False] = False
    recovery_claimed: Literal[False] = False
    execution_permitted: Literal[False] = False

    @model_validator(mode="after")
    def suggestion_only(self) -> "RoutingFailoverDecision":
        if ((self.status == "proposed") != (self.candidate_id is not None)
            or (self.status == "blocked") != (self.reason is not None)
            or self.recovery_trial_required and self.candidate_id is None
            or len({row.candidate_id for row in self.candidates}) != len(self.candidates)
            or self.candidate_id is not None and not any(row.candidate_id == self.candidate_id and row.reason is None
                for row in self.candidates)):
            raise ValueError("ROUTING_FAILOVER_DECISION_INVALID")
        return self
