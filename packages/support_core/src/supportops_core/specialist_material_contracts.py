"""固定只读子任务的材料分支；业务原信封仅在内存中，不进入预算/派发事件。"""

import hashlib
from typing import Literal

from pydantic import Field, model_validator

from supportops_core.specialist_business_context_contracts import (
    SpecialistBusinessContext, SpecialistBusinessObservation, SpecialistBusinessScope, specialist_business_context_digest,
)
from supportops_core.specialist_context_contracts import (
    SpecialistKnowledgeContext, SpecialistKnowledgeScope, specialist_context_digest,
)
from supportops_core.specialist_task_contracts import SpecialistContract, SpecialistTaskInput


class SpecialistBusinessMaterial(SpecialistContract):
    schema_version: Literal["specialist_business_material_binding_v1"] = "specialist_business_material_binding_v1"
    scope: SpecialistBusinessScope
    observations: tuple[SpecialistBusinessObservation, ...] = Field(min_length=1, max_length=32, repr=False)

    @model_validator(mode="after")
    def original_messages(self) -> "SpecialistBusinessMaterial":
        indexed = {row.source.invocation_id: row for row in self.observations}
        if len(indexed) != len(self.observations) or set(indexed) != {row.invocation_id for row in self.scope.sources}:
            raise ValueError("SPECIALIST_BUSINESS_MATERIAL_BINDING_INVALID")
        for source in self.scope.sources:
            message = indexed[source.invocation_id]
            if (message.tool_id != source.tool_id or message.tool_call_id != source.tool_call_id
                or message.source.implementation_version != source.implementation_version
                or message.source.catalog_version != source.catalog_version
                or hashlib.sha256(message.envelope_json.encode("utf-8")).hexdigest() != source.message_digest
                or hashlib.sha256(message.payload_json.encode("utf-8")).hexdigest() != source.result_digest):
                raise ValueError("SPECIALIST_BUSINESS_MATERIAL_BINDING_INVALID")
        return self


SpecialistMaterialScope = SpecialistKnowledgeScope | SpecialistBusinessMaterial
SpecialistMaterialContext = SpecialistKnowledgeContext | SpecialistBusinessContext


def material_scope(scope: SpecialistMaterialScope) -> SpecialistKnowledgeScope | SpecialistBusinessScope:
    return scope.scope if isinstance(scope, SpecialistBusinessMaterial) else scope


def material_matches_task(task: SpecialistTaskInput, scope: SpecialistMaterialScope) -> bool:
    if isinstance(scope, SpecialistKnowledgeScope):
        return task.specialist == "knowledge"
    return isinstance(scope, SpecialistBusinessMaterial) and task.specialist == scope.scope.specialist


def material_context_digest(context: SpecialistMaterialContext) -> str:
    if isinstance(context, SpecialistKnowledgeContext):
        return specialist_context_digest(context)
    return specialist_business_context_digest(context)
