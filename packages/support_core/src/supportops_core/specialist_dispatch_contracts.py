"""知识子请求的持久领取与一次入口消费；历史记录不授予模型/业务执行许可。"""

from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from supportops_core.specialist_scheduler_contracts import SpecialistDispatchClaim
from supportops_core.specialist_task_contracts import Digest, SpecialistContract, contract_digest

DISPATCH_CONSUMED_ERROR = "SPECIALIST_DISPATCH_CONSUMED_USAGE_UNKNOWN"


class SpecialistDispatchRecord(SpecialistContract):
    schema_version: Literal["specialist_dispatch_record_v1"] = "specialist_dispatch_record_v1"
    tenant_id: UUID
    conversation_id: UUID
    run_id: UUID
    attempt_id: UUID
    generation: int = Field(ge=1)
    call_id: UUID
    owner_digest: Digest
    prepared_request_digest: Digest
    model_input_digest: Digest
    claim: SpecialistDispatchClaim
    execution_permitted: Literal[False] = False


class SpecialistDispatchConsumption(SpecialistContract):
    schema_version: Literal["specialist_dispatch_consumption_v1"] = "specialist_dispatch_consumption_v1"
    record: SpecialistDispatchRecord
    record_digest: Digest
    consumed_at: AwareDatetime
    expires_at: AwareDatetime
    physical_call_limit: Literal[1] = 1
    automatic_retry_allowed: Literal[False] = False
    request_sent_confirmed: Literal[False] = False
    execution_permitted: Literal[False] = False

    @model_validator(mode="after")
    def one_original_window(self) -> "SpecialistDispatchConsumption":
        if (self.record_digest != contract_digest(self.record)
            or not self.record.claim.claimed_at <= self.consumed_at < self.expires_at <= self.record.claim.expires_at):
            raise ValueError("SPECIALIST_DISPATCH_CONSUMPTION_INVALID")
        return self


class SpecialistDispatchView(SpecialistContract):
    record: SpecialistDispatchRecord | None = None
    consumption: SpecialistDispatchConsumption | None = None
    execution_permitted: Literal[False] = False

    @model_validator(mode="after")
    def same_record(self) -> "SpecialistDispatchView":
        if self.consumption is not None and self.consumption.record != self.record:
            raise ValueError("SPECIALIST_DISPATCH_HISTORY_INVALID")
        return self
