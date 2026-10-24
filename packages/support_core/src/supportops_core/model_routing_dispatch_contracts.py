"""路由调用的一次派发入口；领取/消费记录均不是可重用执行令牌。"""

from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from supportops_core.model_routing_contracts import Digest, RoutingFilterModel
from supportops_core.model_routing_failover_contracts import RoutingFailoverControls
from supportops_core.model_routing_reservation_contracts import RoutingCallPlan, RoutingReservationRecord
from supportops_core.release_manifest_services import manifest_digest

ROUTING_DISPATCH_CONSUMED_ERROR = "ROUTING_DISPATCH_CONSUMED_USAGE_UNKNOWN"


class RoutingDispatchRequest(RoutingFilterModel):
    plan: RoutingCallPlan
    root_plan: RoutingCallPlan
    controls: RoutingFailoverControls
    reservation_digest: Digest
    failover_link_digest: Digest | None = None
    recovery_event_id: UUID | None = None

    @model_validator(mode="after")
    def same_original_request(self) -> "RoutingDispatchRequest":
        if (self.plan != self.root_plan.model_copy(update={"call_id": self.plan.call_id})
            or (self.plan.call_id == self.root_plan.call_id) != (self.failover_link_digest is None)):
            raise ValueError("ROUTING_DISPATCH_ROOT_SCOPE_INVALID")
        return self


class RoutingDispatchPermit(RoutingFilterModel):
    request_digest: Digest
    reservation_digest: Digest
    actor_user_id: UUID
    approval_reference_digest: Digest
    prepared_request_digest: Digest
    model_input_digest: Digest
    checked_at: AwareDatetime
    expires_at: AwareDatetime

    @model_validator(mode="after")
    def bounded_window(self) -> "RoutingDispatchPermit":
        if not 0 < (self.expires_at - self.checked_at).total_seconds() <= 30:
            raise ValueError("ROUTING_DISPATCH_APPROVAL_WINDOW_INVALID")
        return self


class RoutingDispatchRecord(RoutingFilterModel):
    schema_version: Literal["routing_dispatch_record_v1"] = "routing_dispatch_record_v1"
    dispatch_id: UUID
    reservation: RoutingReservationRecord
    request_digest: Digest
    owner_digest: Digest
    root_call_id: UUID
    root_plan_digest: Digest
    controls_digest: Digest
    failover_link_digest: Digest | None = None
    recovery_record_digest: Digest | None = None
    circuit_scope_digest: Digest
    circuit_head_digest: Digest | None = None
    prepared_request_digest: Digest
    claimed_at: AwareDatetime
    expires_at: AwareDatetime
    execution_permitted: Literal[False] = False

    @model_validator(mode="after")
    def fixed_claim(self) -> "RoutingDispatchRecord":
        if (not self.reservation.recorded_at <= self.claimed_at < self.expires_at
            or (self.expires_at - self.claimed_at).total_seconds() > 30
            or (self.root_call_id == self.reservation.call_id) != (self.failover_link_digest is None)):
            raise ValueError("ROUTING_DISPATCH_CLAIM_WINDOW_INVALID")
        return self


class RoutingDispatchConsumption(RoutingFilterModel):
    schema_version: Literal["routing_dispatch_consumption_v1"] = "routing_dispatch_consumption_v1"
    record: RoutingDispatchRecord
    record_digest: Digest
    confirmation_circuit_digest: Digest | None = None
    consumed_at: AwareDatetime
    expires_at: AwareDatetime
    physical_call_limit: Literal[1] = 1
    automatic_retry_allowed: Literal[False] = False
    request_sent_confirmed: Literal[False] = False
    execution_permitted: Literal[False] = False

    @model_validator(mode="after")
    def one_original_entry(self) -> "RoutingDispatchConsumption":
        if (manifest_digest(self.record.model_dump(mode="json")) != self.record_digest
            or not self.record.claimed_at <= self.consumed_at < self.expires_at <= self.record.expires_at):
            raise ValueError("ROUTING_DISPATCH_CONSUMPTION_INVALID")
        return self


class RoutingDispatchView(RoutingFilterModel):
    record: RoutingDispatchRecord | None = None
    consumption: RoutingDispatchConsumption | None = None
    execution_permitted: Literal[False] = False

    @model_validator(mode="after")
    def original_record(self) -> "RoutingDispatchView":
        if self.consumption is not None and self.consumption.record != self.record:
            raise ValueError("ROUTING_DISPATCH_HISTORY_INVALID")
        return self
