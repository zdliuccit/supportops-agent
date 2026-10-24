"""路由物理调用的冻结价格及可信执行用量；不承载正文、秘密或执行许可。"""

from decimal import Decimal
from fractions import Fraction
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from supportops_core.model_routing_contracts import Digest, RoutingFilterModel
from supportops_core.model_routing_reservation_contracts import RoutingReservationRecord

Price = Annotated[str, Field(pattern=r"^(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$", max_length=400)]
TokenCount = Annotated[int, Field(ge=0, le=2**31 - 1, strict=True)]
AccountingStatus = Literal["unknown", "settled"]
AccountingErrorCode = Literal["ROUTING_MODEL_USAGE_UNKNOWN", "ROUTING_USAGE_BOUND_EXCEEDED", "ROUTING_USAGE_COST_OUT_OF_RANGE"]


class RoutingPriceSnapshot(RoutingFilterModel):
    schema_version: Literal["routing_price_snapshot_v1"] = "routing_price_snapshot_v1"
    call_id: UUID
    tenant_id: UUID
    actual_model_version_id: UUID
    candidate_digest: Digest
    source_pricing_digest: Digest
    source_pricing: dict[str, str | int | float | None] = Field(min_length=3, max_length=3)
    currency: Literal["USD"] = "USD"
    input_per_million_tokens: Price
    output_per_million_tokens: Price

    @model_validator(mode="after")
    def original_prices(self) -> "RoutingPriceSnapshot":
        keys = {"currency", "input_per_million_tokens", "output_per_million_tokens"}
        if set(self.source_pricing) != keys or self.source_pricing["currency"] != "USD":
            raise ValueError("只冻结原封闭USD价格配置")
        for key in keys - {"currency"}:
            value = self.source_pricing[key]
            if type(value) not in (int, float):
                raise ValueError("原价格必须是有限非负数")
            amount = Decimal(str(value))
            if (not amount.is_finite() or not 0 <= amount <= 2**63 - 1
                or amount != Decimal(getattr(self, key))):
                raise ValueError("精确单价必须等于原价格配置")
        return self


def routing_usage_cost(snapshot: RoutingPriceSnapshot, *, input_tokens: int, output_tokens: int) -> int:
    # USD/百万Token等于微美元/Token；缓存和推理已包含在总量，不再叠加收费。
    amount = (input_tokens * Fraction(Decimal(snapshot.input_per_million_tokens))
        + output_tokens * Fraction(Decimal(snapshot.output_per_million_tokens)))
    cost = -(-amount.numerator // amount.denominator)
    if not 0 <= cost <= 2**63 - 1:
        raise ValueError("费用超过账本范围")
    return cost


class RoutingUsageTotals(RoutingFilterModel):
    input_tokens: TokenCount
    output_tokens: TokenCount
    cached_input_tokens: TokenCount | None = None
    reasoning_tokens: TokenCount | None = None

    @model_validator(mode="after")
    def included_subtotals(self) -> "RoutingUsageTotals":
        if (self.cached_input_tokens is not None and self.cached_input_tokens > self.input_tokens
            or self.reasoning_tokens is not None and self.reasoning_tokens > self.output_tokens):
            raise ValueError("缓存和推理用量必须包含在原输入/输出总量内")
        return self


class RoutingExecutionUsageReceipt(RoutingFilterModel):
    schema_version: Literal["routing_execution_usage_v1"] = "routing_execution_usage_v1"
    observation_id: UUID
    tenant_id: UUID
    call_id: UUID
    run_id: UUID
    attempt_id: UUID
    generation: int = Field(ge=1, le=2**31 - 1, strict=True)
    reservation_digest: Digest
    actual_model_version_id: UUID
    credential_revision: int = Field(ge=1, le=2**31 - 1, strict=True)
    model_input_digest: Digest
    dispatch_id: UUID
    dispatch_evidence_digest: Digest
    outcome: Literal["completed", "failed", "cancelled", "unknown"]
    usage: RoutingUsageTotals | None = None
    started_at: AwareDatetime
    finished_at: AwareDatetime | None = None
    observed_at: AwareDatetime

    @model_validator(mode="after")
    def observed_window(self) -> "RoutingExecutionUsageReceipt":
        if (self.started_at > self.observed_at
            or self.finished_at is not None and not self.started_at <= self.finished_at <= self.observed_at
            or self.usage is not None and (self.finished_at is None or self.outcome == "unknown")):
            raise ValueError("真实完整用量须来自已结束的原物理调用")
        return self


class RoutingAccountingRecord(RoutingFilterModel):
    schema_version: Literal["routing_accounting_record_v1"] = "routing_accounting_record_v1"
    reservation_digest: Digest
    pricing_snapshot_digest: Digest
    previous_record_digest: Digest | None = None
    receipt: RoutingExecutionUsageReceipt
    receipt_digest: Digest
    status: AccountingStatus
    settled_cost_microusd: int | None = Field(default=None, ge=0, le=2**63 - 1, strict=True)
    reservation_exceeded: bool = Field(strict=True)
    error_code: AccountingErrorCode | None = None
    recorded_at: AwareDatetime
    execution_permitted: Literal[False] = False

    @model_validator(mode="after")
    def accounting_state(self) -> "RoutingAccountingRecord":
        if self.status == "settled":
            if (self.receipt.usage is None or self.settled_cost_microusd is None
                or self.error_code != ("ROUTING_USAGE_BOUND_EXCEEDED" if self.reservation_exceeded else None)):
                raise ValueError("结算必须有真实完整用量和对应费用")
        elif self.receipt.usage is not None:
            if (self.settled_cost_microusd is not None or not self.reservation_exceeded
                or self.error_code != "ROUTING_USAGE_COST_OUT_OF_RANGE"):
                raise ValueError("超出账本范围须保留真实用量并阻断后续调用，不能截断费用")
        elif (self.settled_cost_microusd is not None or self.reservation_exceeded
            or self.error_code != "ROUTING_MODEL_USAGE_UNKNOWN"):
            raise ValueError("未知用量保留原预留，不填零费用")
        if self.receipt.observed_at > self.recorded_at:
            raise ValueError("回执观察时间不得晚于账本记录")
        return self


class RoutingAccountingView(RoutingFilterModel):
    reservation: RoutingReservationRecord
    pricing: RoutingPriceSnapshot
    accounting: RoutingAccountingRecord
    accounting_digest: Digest
