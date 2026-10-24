"""只读调度准入与结果来源证明；业务信封只在内存，不授予执行许可。"""

from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from supportops_core.specialist_context_contracts import SpecialistKnowledgeScope
from supportops_core.specialist_material_contracts import SpecialistMaterialScope
from supportops_core.specialist_task_contracts import Digest, SpecialistContract, contract_digest


class SpecialistAdmissionBinding(SpecialistContract):
    """由可信父调用者预先固定；不可用新call重新领取同一任务。"""

    task_id: UUID
    task_input_digest: Digest
    call_id: UUID
    scope: SpecialistMaterialScope


class SpecialistKnowledgeAdmissionBinding(SpecialistAdmissionBinding):
    """兼容旧知识调用者；类型与运行入口都不隐式接受业务材料。"""

    scope: SpecialistKnowledgeScope


class SpecialistResultObservation(SpecialistContract):
    """独立可信reader核对原Provider响应和确定转换后的输出，仅返回摘要。"""

    schema_version: Literal["specialist_result_observation_v1"] = "specialist_result_observation_v1"
    observation_id: UUID
    call_id: UUID
    consumption_digest: Digest
    accounting_receipt_digest: Digest
    result_digest: Digest
    observed_at: AwareDatetime


class SpecialistResultAcceptance(SpecialistContract):
    schema_version: Literal["specialist_result_acceptance_v1"] = "specialist_result_acceptance_v1"
    tenant_id: UUID
    run_id: UUID
    attempt_id: UUID
    generation: int = Field(ge=1)
    call_id: UUID
    task_input_digest: Digest
    schedule_digest: Digest
    reservation_digest: Digest
    dispatch_record_digest: Digest
    consumption_digest: Digest
    accounting_digest: Digest
    result_digest: Digest
    result_finished_at: AwareDatetime
    observation: SpecialistResultObservation
    observation_digest: Digest
    accepted_at: AwareDatetime
    expires_at: AwareDatetime
    execution_permitted: Literal[False] = False
    business_status_confirmed: Literal[False] = False

    @model_validator(mode="after")
    def original_observation(self) -> "SpecialistResultAcceptance":
        observation = self.observation
        if (observation.call_id != self.call_id
            or observation.consumption_digest != self.consumption_digest
            or observation.result_digest != self.result_digest
            or self.observation_digest != contract_digest(observation)
            or not self.result_finished_at <= observation.observed_at <= self.accepted_at < self.expires_at):
            raise ValueError("SPECIALIST_RESULT_ACCEPTANCE_INVALID")
        return self
