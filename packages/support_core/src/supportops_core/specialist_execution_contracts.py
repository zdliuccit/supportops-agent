"""一次完整物理回执的受控持久记录；正文密封，元数据不授予运行许可。"""

from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from supportops_core.model_routing_accounting_contracts import RoutingExecutionUsageReceipt
from supportops_core.release_manifest_services import manifest_digest
from supportops_core.specialist_task_contracts import Digest, SpecialistContract, contract_digest

StoragePurpose = Literal["capture", "read_usage", "read_answer", "read_status"]


class SpecialistExecutionStoragePermit(SpecialistContract):
    purpose: StoragePurpose
    reservation_digest: Digest
    consumption_digest: Digest
    receipt_digest: Digest
    policy_digest: Digest
    checked_at: AwareDatetime
    expires_at: AwareDatetime
    # 费用读取不需要正文；有明确正文许可仍不得超过原消费期限。
    answer_until: AwareDatetime | None = None

    @model_validator(mode="after")
    def short_permit(self) -> "SpecialistExecutionStoragePermit":
        if (not 0 < (self.expires_at - self.checked_at).total_seconds() <= 5
            or self.answer_until is not None and (self.purpose not in {"capture", "read_answer"}
                or self.answer_until <= self.checked_at)):
            raise ValueError("SPECIALIST_STORAGE_PERMIT_INVALID")
        return self


class SpecialistExecutionRecord(SpecialistContract):
    schema_version: Literal["specialist_execution_record_v1"] = "specialist_execution_record_v1"
    call_id: UUID
    tenant_id: UUID
    conversation_id: UUID
    subject_user_id: UUID
    run_id: UUID
    attempt_id: UUID
    generation: int = Field(ge=1)
    reservation_digest: Digest
    consumption_digest: Digest
    prepared_request_digest: Digest
    storage_policy_digest: Digest
    capture_permit_digest: Digest
    receipt: RoutingExecutionUsageReceipt
    receipt_digest: Digest
    answer_status: Literal["not_supplied", "not_permitted", "invalid", "unavailable", "sealed_redacted"] = "not_supplied"
    # all-null表示费用元数据单独落账；不从null推断SDK没有返回正文。
    original_answer_digest: Digest | None = None
    answer_digest: Digest | None = None
    answer_was_redacted: bool | None = None
    answer_until: AwareDatetime | None = None
    key_id: UUID | None = None
    ciphertext_digest: Digest | None = None
    recorded_at: AwareDatetime
    execution_permitted: Literal[False] = False

    @model_validator(mode="after")
    def full_receipt(self) -> "SpecialistExecutionRecord":
        receipt = self.receipt
        body = (self.original_answer_digest, self.answer_digest, self.answer_was_redacted,
            self.answer_until, self.key_id, self.ciphertext_digest)
        if (receipt.call_id != self.call_id or receipt.tenant_id != self.tenant_id
            or receipt.run_id != self.run_id or receipt.attempt_id != self.attempt_id or receipt.generation != self.generation
            or receipt.reservation_digest != self.reservation_digest or self.receipt_digest != manifest_digest(receipt.model_dump(mode="json"))
            or receipt.usage is None or receipt.finished_at is None or receipt.outcome == "unknown"
            or receipt.observed_at > self.recorded_at or any(item is not None for item in body) != all(item is not None for item in body)
            or (self.answer_status == "sealed_redacted") != all(item is not None for item in body)):
            raise ValueError("SPECIALIST_EXECUTION_RECORD_INVALID")
        if self.answer_until is not None and (receipt.outcome != "completed" or self.answer_until <= self.recorded_at):
            raise ValueError("SPECIALIST_EXECUTION_ANSWER_WINDOW_INVALID")
        return self


class SpecialistExecutionStatus(SpecialistContract):
    record: SpecialistExecutionRecord
    record_digest: Digest
    execution_permitted: Literal[False] = False

    @model_validator(mode="after")
    def original_record(self) -> "SpecialistExecutionStatus":
        if self.record_digest != contract_digest(self.record):
            raise ValueError("SPECIALIST_EXECUTION_HISTORY_INVALID")
        return self
