"""持久熔断操作与短期存储批准；均不授予模型派发许可。"""

from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from supportops_core.model_routing_contracts import Digest, RoutingFilterModel
from supportops_core.model_routing_failover_contracts import (
    CircuitOutcome, RoutingCircuitEvent, RoutingCircuitScope, RoutingCircuitSnapshot, RoutingFailoverControls,
)
from supportops_core.model_routing_reservation_contracts import RoutingCallPlan
from supportops_core.release_manifest_services import manifest_digest


class RoutingCircuitRequest(RoutingFilterModel):
    action: Literal["read", "observe", "claim_recovery"]
    scope: RoutingCircuitScope
    controls: RoutingFailoverControls
    event_id: UUID | None = None
    run_id: UUID | None = None
    call_id: UUID | None = None
    expected_sequence: int = Field(default=0, ge=0, le=256, strict=True)
    expected_event_digest: Digest | None = None
    outcome: CircuitOutcome | None = None
    plan: RoutingCallPlan | None = None

    @model_validator(mode="after")
    def exact_operation(self) -> "RoutingCircuitRequest":
        if self.scope.controls_digest != manifest_digest(self.controls.model_dump(mode="json")):
            raise ValueError("ROUTING_CIRCUIT_CONTROLS_MISMATCH")
        if self.action == "read":
            if (any(value is not None for value in (self.event_id, self.run_id, self.call_id,
                self.expected_event_digest, self.outcome, self.plan)) or self.expected_sequence):
                raise ValueError("ROUTING_CIRCUIT_READ_SCOPE_INVALID")
        elif (self.event_id is None or self.run_id is None or self.call_id is None
            or (self.expected_sequence == 0) != (self.expected_event_digest is None)):
            raise ValueError("ROUTING_CIRCUIT_WRITE_SCOPE_INVALID")
        elif self.action == "observe":
            if self.outcome is None or self.plan is not None:
                raise ValueError("ROUTING_CIRCUIT_OBSERVATION_INVALID")
        elif (self.outcome is not None or self.plan is None or self.plan.call_id != self.call_id
            or self.plan.run_id != self.run_id or self.plan.requirements.tenant_id != self.scope.tenant_id
            or self.plan.requirements.policy_version_id != self.scope.policy_version_id
            or self.plan.requirements.policy_digest != self.scope.policy_digest
            or self.scope.candidate not in self.plan.candidates):
            raise ValueError("ROUTING_CIRCUIT_RECOVERY_SCOPE_INVALID")
        return self


class RoutingCircuitPermit(RoutingFilterModel):
    request_digest: Digest
    actor_user_id: UUID
    governance_reference_digest: Digest
    checked_at: AwareDatetime
    expires_at: AwareDatetime

    @model_validator(mode="after")
    def short_window(self) -> "RoutingCircuitPermit":
        if not 0 < (self.expires_at - self.checked_at).total_seconds() <= 1:
            raise ValueError("ROUTING_CIRCUIT_APPROVAL_WINDOW_INVALID")
        return self


class RoutingCircuitStoredEvent(RoutingFilterModel):
    schema_version: Literal["routing_circuit_stored_event_v1"] = "routing_circuit_stored_event_v1"
    scope: RoutingCircuitScope
    controls: RoutingFailoverControls
    event: RoutingCircuitEvent
    run_id: UUID
    request: RoutingCircuitRequest
    request_digest: Digest
    permit: RoutingCircuitPermit
    source_accounting_digest: Digest | None = None

    @model_validator(mode="after")
    def original_proof(self) -> "RoutingCircuitStoredEvent":
        if (self.scope.controls_digest != manifest_digest(self.controls.model_dump(mode="json"))
            or self.event.scope_digest != manifest_digest(self.scope.model_dump(mode="json"))
            or self.permit.request_digest != self.request_digest
            or self.request_digest != manifest_digest(self.request.model_dump(mode="json"))
            or self.request.scope != self.scope or self.request.controls != self.controls
            or self.request.event_id != self.event.event_id or self.request.call_id != self.event.call_id
            or self.request.run_id != self.run_id or self.request.expected_sequence + 1 != self.event.sequence
            or self.request.expected_event_digest != self.event.previous_event_digest
            or self.request.outcome != self.event.outcome
            or self.event.kind != ("observation" if self.request.action == "observe" else "recovery_claimed")
            or not self.permit.checked_at <= self.event.recorded_at < self.permit.expires_at
            or (self.event.kind == "observation") != (self.source_accounting_digest is not None)):
            raise ValueError("ROUTING_CIRCUIT_STORED_SCOPE_INVALID")
        return self


class RoutingCircuitView(RoutingFilterModel):
    snapshot: RoutingCircuitSnapshot
    event: RoutingCircuitStoredEvent | None = None
    record_digest: Digest | None = None
    recovery_claimed: bool = Field(default=False, strict=True)
    execution_permitted: Literal[False] = False

    @model_validator(mode="after")
    def persisted_observation(self) -> "RoutingCircuitView":
        if ((self.event is None) != (self.record_digest is None)
            or self.event is not None and manifest_digest(self.event.model_dump(mode="json")) != self.record_digest
            or self.recovery_claimed and (self.event is None or self.event.event.kind != "recovery_claimed"
                or self.snapshot.phase != "half_open"
                or self.snapshot.recovery_call_id != self.event.event.call_id)):
            raise ValueError("ROUTING_CIRCUIT_VIEW_INVALID")
        return self
