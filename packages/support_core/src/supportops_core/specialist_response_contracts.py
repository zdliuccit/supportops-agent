"""原Provider回答观察及确定转换；正文只在可信进程内，不是执行回执或授权。"""

import hashlib
from typing import ClassVar, Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from supportops_core.specialist_request_contracts import SpecialistPreparedRequest
from supportops_core.specialist_task_contracts import (
    Digest, SpecialistContract, SpecialistTaskInput, SpecialistTaskOutput, contract_digest,
)

SPECIALIST_RESPONSE_BYTES_MAX = 131072


class SpecialistProviderAnswerObservation(SpecialistContract):
    """仅可信执行记录读取器构造；不得来自模型/HTTP报告上传。"""

    schema_version: Literal["specialist_provider_answer_observation_v1"] = "specialist_provider_answer_observation_v1"
    observation_id: UUID
    call_id: UUID
    reservation_digest: Digest
    consumption_digest: Digest
    prepared_request_digest: Digest
    model_input_digest: Digest
    answer_json: str = Field(min_length=1, max_length=SPECIALIST_RESPONSE_BYTES_MAX, repr=False)
    answer_bytes_digest: Digest
    finished_at: AwareDatetime
    observed_at: AwareDatetime

    @model_validator(mode="after")
    def original_bytes(self) -> "SpecialistProviderAnswerObservation":
        try:
            body = self.answer_json.encode("utf-8")
        except UnicodeError:
            raise ValueError("SPECIALIST_RESPONSE_ENCODING_INVALID") from None
        if (len(body) > SPECIALIST_RESPONSE_BYTES_MAX or hashlib.sha256(body).hexdigest() != self.answer_bytes_digest
            or self.finished_at > self.observed_at):
            raise ValueError("SPECIALIST_RESPONSE_OBSERVATION_INVALID")
        return self


class SpecialistResultBinding(SpecialistContract):
    _allowed_specialists: ClassVar[frozenset[str]] = frozenset({"knowledge", "diagnostic", "ticket_analysis"})
    call_id: UUID
    task: SpecialistTaskInput = Field(repr=False)
    prepared: SpecialistPreparedRequest = Field(repr=False)

    @model_validator(mode="after")
    def original_task(self) -> "SpecialistResultBinding":
        if (self.task.specialist not in self._allowed_specialists or self.prepared.task_id != self.task.task_id
            or self.prepared.task_input_digest != contract_digest(self.task)
            or self.prepared.child_version != self.task.child_version):
            raise ValueError("SPECIALIST_RESPONSE_TASK_MISMATCH")
        return self


class SpecialistKnowledgeResultBinding(SpecialistResultBinding):
    _allowed_specialists = frozenset({"knowledge"})


class SpecialistResultConversion(SpecialistContract):
    schema_version: Literal["specialist_result_conversion_v1", "specialist_knowledge_result_conversion_v1"] = "specialist_result_conversion_v1"
    call_id: UUID
    task_input_digest: Digest
    provider_observation_digest: Digest
    prepared_request_digest: Digest
    accounting_digest: Digest
    citation_evidence_ids: tuple[UUID, ...] = Field(max_length=50)
    analysis_is_redacted: bool
    result: SpecialistTaskOutput = Field(repr=False)
    result_digest: Digest
    execution_permitted: Literal[False] = False
    business_status_confirmed: Literal[False] = False

    @model_validator(mode="after")
    def original_result(self) -> "SpecialistResultConversion":
        if (self.result.input_digest != self.task_input_digest or self.result_digest != contract_digest(self.result)
            or len(set(self.citation_evidence_ids)) != len(self.citation_evidence_ids)):
            raise ValueError("SPECIALIST_RESPONSE_CONVERSION_INVALID")
        return self


class SpecialistKnowledgeResultConversion(SpecialistResultConversion):
    schema_version: Literal["specialist_knowledge_result_conversion_v1"] = "specialist_knowledge_result_conversion_v1"
