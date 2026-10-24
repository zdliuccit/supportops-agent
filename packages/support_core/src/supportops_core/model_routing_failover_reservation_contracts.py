"""有限回退预留的原逻辑请求、可信先前观察与最小追加链。"""

from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from supportops_core.model_routing_contracts import Digest, RoutingFilterModel
from supportops_core.model_routing_failover_contracts import RoutingFailoverAttempt, RoutingFailoverControls
from supportops_core.model_routing_reservation_contracts import RoutingCallPlan


class RoutingFailoverReservationRequest(RoutingFilterModel):
    root_plan: RoutingCallPlan
    controls: RoutingFailoverControls
    call_id: UUID
    expected_attempt_count: int = Field(ge=1, le=2, strict=True)
    expected_link_digest: Digest | None = None

    @model_validator(mode="after")
    def fixed_chain(self) -> "RoutingFailoverReservationRequest":
        if (self.call_id == self.root_plan.call_id
            or (self.expected_attempt_count == 1) != (self.expected_link_digest is None)
            or self.expected_attempt_count >= self.controls.max_physical_calls):
            raise ValueError("ROUTING_FAILOVER_REQUEST_CHAIN_INVALID")
        return self


class RoutingFailoverObservation(RoutingFilterModel):
    """仅由显式可信执行历史reader返回，摘要和时间本身不认证来源。"""

    root_plan_digest: Digest
    source_reference_digest: Digest
    attempts: tuple[RoutingFailoverAttempt, ...] = Field(min_length=1, max_length=2)
    checked_at: AwareDatetime
    expires_at: AwareDatetime

    @model_validator(mode="after")
    def short_observation(self) -> "RoutingFailoverObservation":
        if not 0 < (self.expires_at - self.checked_at).total_seconds() <= 1:
            raise ValueError("ROUTING_FAILOVER_OBSERVATION_WINDOW_INVALID")
        return self


class RoutingFailoverReservationLink(RoutingFilterModel):
    schema_version: Literal["routing_failover_reservation_link_v1"] = "routing_failover_reservation_link_v1"
    root_call_id: UUID
    root_plan_digest: Digest
    call_id: UUID
    ordinal: int = Field(ge=2, le=3, strict=True)
    previous_call_id: UUID
    previous_link_digest: Digest | None = None
    reservation_digest: Digest
    controls_digest: Digest
    source_reference_digest: Digest
    observation_digest: Digest
    accounting_digests: tuple[Digest, ...] = Field(min_length=1, max_length=2)
    selection_report_digest: Digest
    confirmation_report_digest: Digest
    decision_digest: Digest
    circuit_heads_digest: Digest
    candidate_id: UUID
    recovery_required: bool = Field(strict=True)
    recorded_at: AwareDatetime
    execution_permitted: Literal[False] = False

    @model_validator(mode="after")
    def exact_predecessor(self) -> "RoutingFailoverReservationLink":
        if (self.call_id in {self.root_call_id, self.previous_call_id}
            or (self.ordinal == 2) != (self.previous_link_digest is None)
            or (self.ordinal == 2) != (self.previous_call_id == self.root_call_id)
            or len(self.accounting_digests) != self.ordinal - 1):
            raise ValueError("ROUTING_FAILOVER_LINK_INVALID")
        return self
