"""一次路由SDK的最小持久观察；不保存响应正文、错误文案或秘密。"""

from typing import Literal
from uuid import UUID, uuid5

from pydantic import AwareDatetime, Field, SerializerFunctionWrapHandler, model_serializer, model_validator

from supportops_core.model_routing_accounting_contracts import RoutingExecutionUsageReceipt
from supportops_core.model_routing_contracts import Digest, RoutingFilterModel

RoutingObservedOutcome = Literal["completed", "transient_failure", "rate_limited", "contract_failure", "unknown"]


class RoutingSDKObservationRecord(RoutingFilterModel):
    schema_version: Literal["routing_sdk_observation_v1", "routing_sdk_observation_v2"] = "routing_sdk_observation_v2"
    observation_id: UUID
    tenant_id: UUID
    run_id: UUID
    attempt_id: UUID
    generation: int = Field(ge=1, le=2**31 - 1, strict=True)
    call_id: UUID
    root_call_id: UUID
    root_plan_digest: Digest
    reservation_digest: Digest
    consumption_digest: Digest
    prepared_request_digest: Digest
    receipt: RoutingExecutionUsageReceipt | None
    receipt_digest: Digest | None
    response_digest: Digest | None
    provider_failure_code: Literal["server_error", "rate_limit_exceeded", "unclassified", "none"]
    classification: RoutingObservedOutcome
    answer_contract: Literal["accepted", "rejected", "not_available"]
    # v1没有回答绑定，只可查证历史，不补造摘要用于新结果准备。
    answer_digest: Digest | None = None
    io_stop_confirmed: bool = Field(strict=True)
    observed_at: AwareDatetime

    @model_serializer(mode="wrap")
    def preserve_legacy_observation(self, handler: SerializerFunctionWrapHandler) -> dict[str, object]:
        values: dict[str, object] = handler(self)
        if self.schema_version == "routing_sdk_observation_v1":
            values.pop("answer_digest", None)
        return values

    @model_validator(mode="after")
    def original_observation(self) -> "RoutingSDKObservationRecord":
        from supportops_core.release_manifest_services import manifest_digest

        receipt = self.receipt
        if (self.observation_id != uuid5(self.call_id, "routing-sdk-observation-v1")
            or self.schema_version == "routing_sdk_observation_v1" and self.answer_digest is not None
            or self.schema_version == "routing_sdk_observation_v2"
                and (self.answer_digest is not None) != (self.classification == "completed")
            or (receipt is None) != (self.receipt_digest is None)
            or (self.answer_contract == "accepted") != (self.classification == "completed")
            or self.answer_contract == "rejected" and self.classification != "contract_failure"
            or self.provider_failure_code == "unclassified" and self.classification != "contract_failure"
            or self.classification in {"completed", "unknown"} and self.provider_failure_code != "none"
            or (self.provider_failure_code == "server_error") != (self.classification == "transient_failure")
            or (self.provider_failure_code == "rate_limit_exceeded") != (self.classification == "rate_limited")):
            raise ValueError("ROUTING_EXECUTION_OBSERVATION_INVALID")
        if receipt is None:
            if self.classification != "unknown" or self.response_digest is not None:
                raise ValueError("ROUTING_EXECUTION_OBSERVATION_INVALID")
        elif (self.receipt_digest != manifest_digest(receipt.model_dump(mode="json"))
            or receipt.tenant_id != self.tenant_id or receipt.run_id != self.run_id or receipt.attempt_id != self.attempt_id
            or receipt.generation != self.generation or receipt.call_id != self.call_id
            or receipt.observation_id != self.observation_id or receipt.reservation_digest != self.reservation_digest
            or receipt.dispatch_evidence_digest != self.consumption_digest or receipt.observed_at != self.observed_at
            or (receipt.usage is None) != (self.classification == "unknown")
            or self.classification != "unknown" and (self.response_digest is None or receipt.finished_at is None)
            or self.classification == "completed" and receipt.outcome != "completed"
            or self.classification in {"transient_failure", "rate_limited"} and receipt.outcome != "failed"
            or self.classification == "contract_failure" and receipt.outcome not in {"completed", "failed"}
            or self.classification == "unknown" and receipt.outcome != "unknown"):
            raise ValueError("ROUTING_EXECUTION_OBSERVATION_INVALID")
        return self
