"""按明确任务类型加载当前原材料和完整请求；不接受注入的自由组装回调。"""

from sqlalchemy.ext.asyncio import AsyncSession

from supportops_core.config import Settings
from supportops_core.run_attempt_services import RunAttemptIdentity
from supportops_core.specialist_business_request_services import prepare_specialist_business_request
from supportops_core.specialist_context_contracts import SpecialistKnowledgeScope
from supportops_core.specialist_context_services import load_specialist_knowledge_context
from supportops_core.specialist_material_contracts import (
    SpecialistBusinessMaterial, SpecialistMaterialContext, SpecialistMaterialScope, material_matches_task,
)
from supportops_core.specialist_request_contracts import SpecialistPreparedRequest
from supportops_core.specialist_request_services import SpecialistRequestDenied, prepare_specialist_knowledge_request
from supportops_core.specialist_task_contracts import SpecialistTaskInput


async def prepare_specialist_material(session: AsyncSession, *, attempt: RunAttemptIdentity,
    task: SpecialistTaskInput, scope: SpecialistMaterialScope, settings: Settings, redaction_secrets: tuple[str, ...],
) -> tuple[SpecialistMaterialContext, SpecialistPreparedRequest]:
    if not material_matches_task(task, scope):
        raise SpecialistRequestDenied("SPECIALIST_MATERIAL_TYPE_MISMATCH")
    if isinstance(scope, SpecialistKnowledgeScope):
        context = await load_specialist_knowledge_context(session, attempt=attempt, task=task, scope=scope,
            settings=settings, redaction_secrets=redaction_secrets)
        prepared = await prepare_specialist_knowledge_request(session, task=task, context=context,
            settings=settings, redaction_secrets=redaction_secrets)
        return context, prepared
    if isinstance(scope, SpecialistBusinessMaterial):
        return await prepare_specialist_business_request(session, attempt=attempt, task=task, scope=scope.scope,
            observations=scope.observations, settings=settings, redaction_secrets=redaction_secrets)
    raise SpecialistRequestDenied("SPECIALIST_MATERIAL_TYPE_MISMATCH")
