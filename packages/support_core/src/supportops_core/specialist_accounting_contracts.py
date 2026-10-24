"""原子任务物理调用用量观察与结算；不从记账结果恢复执行许可。"""

from typing import Literal

from pydantic import AwareDatetime, Field, model_validator

from supportops_core.model_routing_accounting_contracts import RoutingExecutionUsageReceipt
from supportops_core.specialist_budget_contracts import SpecialistBudgetReservation
from supportops_core.specialist_task_contracts import Digest, SpecialistContract, contract_digest

SpecialistAccountingError = Literal[
    "SPECIALIST_MODEL_USAGE_UNKNOWN", "SPECIALIST_USAGE_BOUND_EXCEEDED", "SPECIALIST_USAGE_COST_OUT_OF_RANGE",
]


class SpecialistAccountingRecord(SpecialistContract):
    schema_version: Literal["specialist_accounting_record_v1"] = "specialist_accounting_record_v1"
    reservation_digest: Digest
    previous_record_digest: Digest | None = None
    receipt: RoutingExecutionUsageReceipt
    receipt_digest: Digest
    status: Literal["unknown", "settled"]
    settled_cost_microusd: int | None = Field(default=None, ge=0, le=2**63 - 1)
    reservation_exceeded: bool
    error_code: SpecialistAccountingError | None = None
    recorded_at: AwareDatetime
    execution_permitted: Literal[False] = False

    @model_validator(mode="after")
    def accounting_state(self) -> "SpecialistAccountingRecord":
        if self.status == "settled":
            if (self.receipt.usage is None or self.settled_cost_microusd is None
                or self.error_code != ("SPECIALIST_USAGE_BOUND_EXCEEDED" if self.reservation_exceeded else None)):
                raise ValueError("SPECIALIST_ACCOUNTING_INVALID")
        elif self.receipt.usage is not None:
            if (self.settled_cost_microusd is not None or not self.reservation_exceeded
                or self.error_code != "SPECIALIST_USAGE_COST_OUT_OF_RANGE"):
                raise ValueError("SPECIALIST_ACCOUNTING_INVALID")
        elif (self.settled_cost_microusd is not None
            or self.error_code != ("SPECIALIST_USAGE_BOUND_EXCEEDED" if self.reservation_exceeded else "SPECIALIST_MODEL_USAGE_UNKNOWN")):
            raise ValueError("SPECIALIST_ACCOUNTING_INVALID")
        if self.receipt.observed_at > self.recorded_at:
            raise ValueError("SPECIALIST_ACCOUNTING_INVALID")
        return self


class SpecialistAccountingView(SpecialistContract):
    reservation: SpecialistBudgetReservation
    accounting: SpecialistAccountingRecord | None = None
    accounting_digest: Digest | None = None
    execution_permitted: Literal[False] = False

    @model_validator(mode="after")
    def present_accounting(self) -> "SpecialistAccountingView":
        if (self.accounting is None) != (self.accounting_digest is None):
            raise ValueError("SPECIALIST_ACCOUNTING_INVALID")
        if self.accounting is not None and (
            self.accounting_digest != contract_digest(self.accounting)
            or self.accounting.reservation_digest != contract_digest(self.reservation)):
            raise ValueError("SPECIALIST_ACCOUNTING_INVALID")
        return self
