"""固定support_answer_v1的有界解析/分析转换与结果reader；不从自由文本编造事实。"""

from __future__ import annotations

import asyncio
import json
import math
from datetime import UTC, datetime
from typing import ClassVar, Protocol
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from supportops_core.agent_config import SupportAnswer
from supportops_core.config import Settings
from supportops_core.redaction import redact_sensitive_text
from supportops_core.specialist_accounting_contracts import SpecialistAccountingRecord
from supportops_core.specialist_admission_contracts import SpecialistResultObservation
from supportops_core.specialist_budget_contracts import SpecialistBudgetReservation
from supportops_core.specialist_context_contracts import SpecialistKnowledgeExcerpt
from supportops_core.specialist_business_context_contracts import SpecialistBusinessExcerpt
from supportops_core.specialist_dispatch_contracts import SpecialistDispatchConsumption
from supportops_core.specialist_response_contracts import (
    SPECIALIST_RESPONSE_BYTES_MAX, SpecialistKnowledgeResultBinding, SpecialistKnowledgeResultConversion,
    SpecialistProviderAnswerObservation, SpecialistResultBinding, SpecialistResultConversion,
)
from supportops_core.specialist_task_contracts import (
    SpecialistTaskOutput, SpecialistUsage, contract_digest, validate_specialist_result,
)


class SpecialistResponseDenied(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__("只读子任务原响应当前不可转换或采纳")
        self.code = code


def _pairs(items: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in items:
        if key in result:
            raise SpecialistResponseDenied("SPECIALIST_RESPONSE_DUPLICATE_KEY")
        result[key] = value
    return result


def _constant(_: str) -> None:
    raise SpecialistResponseDenied("SPECIALIST_RESPONSE_NONFINITE_NUMBER")


def _json_object(value: str, *, bytes_limit: int) -> dict[str, object]:
    """先拒绝重复键/非有限数/复杂度，不让标准解析或Schema默默折叠原输入。"""
    try:
        if len(value.encode("utf-8")) > bytes_limit:
            raise SpecialistResponseDenied("SPECIALIST_RESPONSE_TOO_LARGE")
        decoded = json.loads(value, object_pairs_hook=_pairs, parse_constant=_constant)
        if not isinstance(decoded, dict):
            raise SpecialistResponseDenied("SPECIALIST_RESPONSE_NOT_OBJECT")
        stack: list[tuple[object, int]] = [(decoded, 0)]
        nodes = 0
        while stack:
            item, depth = stack.pop()
            nodes += 1
            if depth > 16 or nodes > 4096:
                raise SpecialistResponseDenied("SPECIALIST_RESPONSE_TOO_COMPLEX")
            if isinstance(item, float) and not math.isfinite(item):
                raise SpecialistResponseDenied("SPECIALIST_RESPONSE_NONFINITE_NUMBER")
            if isinstance(item, dict):
                stack.extend((child, depth + 1) for child in item.values())
            elif isinstance(item, list):
                stack.extend((child, depth + 1) for child in item)
        return decoded
    except SpecialistResponseDenied:
        raise
    except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
        raise SpecialistResponseDenied("SPECIALIST_RESPONSE_JSON_INVALID") from None


def _excerpts(binding: SpecialistResultBinding) -> dict[UUID, SpecialistKnowledgeExcerpt | SpecialistBusinessExcerpt]:
    """只认可原完整请求实际携带的来源；不重新检索或从回答生成来源。"""
    try:
        payload = json.loads(binding.prepared.payload_json)
        material = _json_object(payload["messages"][0]["content"], bytes_limit=131072)
        knowledge = binding.task.specialist == "knowledge"
        fields = {"schema_version", "data_is_untrusted", "question", "question_is_redacted", "excerpts", "expected_fact_keys"}
        if not knowledge:
            fields.add("specialist")
        if (set(material) != fields
            or material["schema_version"] != ("specialist_knowledge_material_v1" if knowledge else "specialist_business_material_v1")
            or not knowledge and material["specialist"] != binding.task.specialist
            or material["data_is_untrusted"] is not True
            or material["expected_fact_keys"] != list(binding.task.expected_fact_keys)
            or not isinstance(material["excerpts"], list) or not 1 <= len(material["excerpts"]) <= 64):
            raise ValueError()
        excerpt_type = SpecialistKnowledgeExcerpt if knowledge else SpecialistBusinessExcerpt
        if not knowledge and len(material["excerpts"]) > 32:
            raise ValueError()
        excerpts = tuple(excerpt_type.model_validate_json(json.dumps(item,
            ensure_ascii=False, sort_keys=True, allow_nan=False)) for item in material["excerpts"])
        indexed = {item.evidence_id: item for item in excerpts}
        sources = {item.evidence_id: item for item in binding.task.evidence}
        if len(indexed) != len(excerpts) or set(indexed) != set(sources):
            raise ValueError()
        for evidence_id, excerpt in indexed.items():
            original_digest = (excerpt.original_content_digest if isinstance(excerpt, SpecialistKnowledgeExcerpt)
                else excerpt.original_result_digest)
            if (excerpt.resource != sources[evidence_id].resource
                or original_digest != sources[evidence_id].content_digest):
                raise ValueError()
            if isinstance(excerpt, SpecialistBusinessExcerpt):
                expected = "diagnostic_request" if binding.task.specialist == "diagnostic" else "support_ticket"
                if (excerpt.resource.resource_kind != expected
                    or (excerpt.tool_id == "support_ticket_lookup") != (binding.task.specialist == "ticket_analysis")):
                    raise ValueError()
        return indexed
    except SpecialistResponseDenied:
        raise
    except (KeyError, IndexError, TypeError, ValueError, RecursionError):
        raise SpecialistResponseDenied("SPECIALIST_RESPONSE_SOURCE_MISMATCH") from None


def convert_specialist_knowledge_answer(*, binding: SpecialistKnowledgeResultBinding,
    reservation: SpecialistBudgetReservation, consumption: SpecialistDispatchConsumption,
    accounting: SpecialistAccountingRecord, observation: SpecialistProviderAnswerObservation,
    redaction_secrets: tuple[str, ...] = (),
) -> SpecialistKnowledgeResultConversion:
    """旧知识转换保留原类型和schema，不能通过此入口解释业务请求。"""
    if binding.task.specialist != "knowledge":
        raise SpecialistResponseDenied("SPECIALIST_RESPONSE_TASK_MISMATCH")
    converted = convert_specialist_answer(binding=binding, reservation=reservation, consumption=consumption,
        accounting=accounting, observation=observation, redaction_secrets=redaction_secrets)
    return SpecialistKnowledgeResultConversion.model_validate_json(json.dumps({**converted.model_dump(mode="json"),
        "schema_version": "specialist_knowledge_result_conversion_v1"}, ensure_ascii=False, allow_nan=False))


def convert_specialist_answer(*, binding: SpecialistResultBinding,
    reservation: SpecialistBudgetReservation, consumption: SpecialistDispatchConsumption,
    accounting: SpecialistAccountingRecord, observation: SpecialistProviderAnswerObservation,
    redaction_secrets: tuple[str, ...] = (),
) -> SpecialistResultConversion:
    """仅纯确定转换；调用者仍须证明原观察可信，采纳层另查当前授权/期限。

    SupportAnswer没有类型化事实字段：保留answer为不可信analysis，facts恒空，所需事实键
    全部missing。无事实要求的分析任务只有原合法引用存在且无冲突/升级才可完成。
    这不验证自然语言语义正确性，不改变原输出Schema或确认业务状态。
    """
    task, prepared, receipt = binding.task, binding.prepared, accounting.receipt
    usage = receipt.usage
    if (reservation.call_id != binding.call_id or observation.call_id != binding.call_id
        or reservation.task_input_digest != contract_digest(task) or reservation.task_id != task.task_id
        or reservation.parent_version != task.parent.version or reservation.child_version != task.child_version
        or reservation.tenant_id != task.parent.tenant_id or reservation.run_id != task.parent.run_id
        or reservation.attempt_id != task.parent.attempt_id or reservation.generation != task.parent.attempt_generation
        or reservation.conversation_id != task.parent.conversation_id or reservation.budget != task.budget
        or reservation.context_digest != prepared.context_digest
        or reservation.model_input_digest != prepared.model_input_digest
        or reservation.credential_revision != prepared.credential_revision
        or consumption.record.call_id != binding.call_id
        or consumption.record.tenant_id != reservation.tenant_id
        or consumption.record.conversation_id != reservation.conversation_id
        or consumption.record.run_id != reservation.run_id or consumption.record.attempt_id != reservation.attempt_id
        or consumption.record.generation != reservation.generation
        or consumption.record.model_input_digest != prepared.model_input_digest
        or consumption.record.claim.task_input_digest != contract_digest(task)
        or consumption.record.claim.task_id != task.task_id
        or consumption.record.claim.authorization_scope_digest != task.parent.authorization_scope_digest
        or consumption.record.claim.governance_decision_digest != reservation.governance_decision_digest
        or consumption.record.claim.budget_scope_digest != contract_digest(task.budget)
        or consumption.expires_at > reservation.expires_at
        or consumption.record.prepared_request_digest != contract_digest(prepared)
        or consumption.record.claim.budget_reservation_digest != contract_digest(reservation)
        or observation.reservation_digest != contract_digest(reservation)
        or observation.consumption_digest != contract_digest(consumption)
        or observation.prepared_request_digest != contract_digest(prepared)
        or observation.model_input_digest != prepared.model_input_digest
        or accounting.reservation_digest != contract_digest(reservation)
        or accounting.receipt_digest != contract_digest(receipt)
        or receipt.call_id != binding.call_id or receipt.tenant_id != reservation.tenant_id
        or receipt.run_id != reservation.run_id or receipt.attempt_id != reservation.attempt_id
        or receipt.generation != reservation.generation or receipt.reservation_digest != contract_digest(reservation)
        or receipt.actual_model_version_id != task.child_version.model_version_id
        or receipt.credential_revision != prepared.credential_revision or receipt.model_input_digest != prepared.model_input_digest
        or receipt.dispatch_id != consumption.record.claim.dispatch_id
        or receipt.dispatch_evidence_digest != contract_digest(consumption)
        or accounting.status != "settled" or accounting.reservation_exceeded or accounting.error_code is not None
        or usage is None or receipt.outcome != "completed" or receipt.finished_at is None
        or observation.finished_at != receipt.finished_at
        or not consumption.consumed_at <= receipt.started_at <= receipt.finished_at < min(task.deadline, consumption.expires_at)
        or observation.observed_at > datetime.now(UTC)):
        raise SpecialistResponseDenied("SPECIALIST_RESPONSE_CALL_MISMATCH")
    _json_object(observation.answer_json, bytes_limit=SPECIALIST_RESPONSE_BYTES_MAX)
    try:
        answer = SupportAnswer.model_validate_json(observation.answer_json, strict=True)
    except (ValidationError, ValueError):
        raise SpecialistResponseDenied("SPECIALIST_RESPONSE_SCHEMA_INVALID") from None
    sources = _excerpts(binding)
    citation_ids: list[UUID] = []
    for citation in answer.citations:
        if (citation.url is not None or citation.evidence_id is None
            or citation.evidence_id not in sources or citation.evidence_id in citation_ids):
            raise SpecialistResponseDenied("SPECIALIST_RESPONSE_CITATION_INVALID")
        citation_ids.append(citation.evidence_id)
    if answer.suggested_actions:
        raise SpecialistResponseDenied("SPECIALIST_RESPONSE_ACTIONS_NOT_ALLOWED")
    for conflict in answer.evidence_conflicts:
        if len(set(conflict.evidence_ids)) != len(conflict.evidence_ids) or any(item not in sources for item in conflict.evidence_ids):
            raise SpecialistResponseDenied("SPECIALIST_RESPONSE_CONFLICT_SOURCE_INVALID")
    if answer.evidence_conflicts or answer.escalation_recommended:
        raise SpecialistResponseDenied("SPECIALIST_RESPONSE_REQUIRES_PARENT_REVIEW")
    if not citation_ids and not task.expected_fact_keys:
        raise SpecialistResponseDenied("SPECIALIST_RESPONSE_EVIDENCE_MISSING")
    analysis = redact_sensitive_text(answer.answer, secrets=redaction_secrets)
    try:
        if len(analysis) > 4096 or len(analysis.encode("utf-8")) > 16384:
            raise SpecialistResponseDenied("SPECIALIST_RESPONSE_ANALYSIS_TOO_LARGE")
        result = SpecialistTaskOutput(task_id=task.task_id, input_digest=contract_digest(task), parent=task.parent,
            child_version=task.child_version, trace=task.trace, budget_scope_digest=contract_digest(task.budget),
            status="needs_input" if task.expected_fact_keys else "completed", finished_at=receipt.finished_at,
            analysis=analysis, missing_fact_keys=task.expected_fact_keys,
            usage=SpecialistUsage(model_calls=1, tool_calls=0, tokens=usage.input_tokens + usage.output_tokens,
                cost_microusd=accounting.settled_cost_microusd))
        validate_specialist_result(task, result)
    except SpecialistResponseDenied:
        raise
    except (ValueError, UnicodeError):
        raise SpecialistResponseDenied("SPECIALIST_RESPONSE_RESULT_INVALID") from None
    return SpecialistResultConversion(call_id=binding.call_id, task_input_digest=contract_digest(task),
        provider_observation_digest=contract_digest(observation), prepared_request_digest=contract_digest(prepared),
        accounting_digest=contract_digest(accounting), citation_evidence_ids=tuple(citation_ids),
        analysis_is_redacted=analysis != answer.answer, result=result, result_digest=contract_digest(result))


class SpecialistProviderAnswerSource(Protocol):
    """独立可信持久执行记录读取器，默认缺失；不得从模型/调用者报告补造原响应。

    须确认一次消费对应原物理SDK调用、固定请求/凭据和无工具/重试，取得原完整结构化
    回答JSON及实际结束时间。仅本地可信记录读取，不模型/工具/取密/联网，不提交或替换事务。
    原记录不存在或未知返回None，不重派、重建回答或修改费用。实际持久源实现仍待执行器。
    """

    async def read_answer(self, session: AsyncSession, *, reservation: SpecialistBudgetReservation,
        consumption: SpecialistDispatchConsumption, accounting: SpecialistAccountingRecord,
    ) -> SpecialistProviderAnswerObservation | None: ...


class SpecialistResultReader:
    """显式绑定最多32份原任务/请求，源缺失拒绝；不接客户端上传或注册执行器。"""

    _allowed_specialists: ClassVar[frozenset[str]] = frozenset({"knowledge", "diagnostic", "ticket_analysis"})

    def __init__(self, *, bindings: tuple[SpecialistResultBinding, ...], settings: Settings,
        source: SpecialistProviderAnswerSource | None = None, redaction_secrets: tuple[str, ...] = (),
    ) -> None:
        if source is None:
            raise SpecialistResponseDenied("SPECIALIST_PROVIDER_RESPONSE_SOURCE_UNAVAILABLE")
        if not 1 <= len(bindings) <= 32:
            raise SpecialistResponseDenied("SPECIALIST_RESPONSE_BINDINGS_INVALID")
        bindings = tuple(SpecialistResultBinding.model_validate_json(item.model_dump_json()) for item in bindings)
        if (len({item.call_id for item in bindings}) != len(bindings)
            or len({item.task.task_id for item in bindings}) != len(bindings)
            or len({contract_digest(item.task.parent) for item in bindings}) != 1
            or any(item.task.specialist not in self._allowed_specialists for item in bindings)):
            raise SpecialistResponseDenied("SPECIALIST_RESPONSE_BINDINGS_INVALID")
        self._bindings = {item.call_id: item for item in bindings}
        self._settings, self._source, self._redaction_secrets = settings, source, redaction_secrets

    async def read_result(self, session: AsyncSession, *, reservation: SpecialistBudgetReservation,
        consumption: SpecialistDispatchConsumption, accounting: SpecialistAccountingRecord,
    ) -> SpecialistResultObservation | None:
        binding = self._bindings.get(reservation.call_id)
        if (binding is None or reservation.task_input_digest != contract_digest(binding.task)
            or reservation.tenant_id != binding.task.parent.tenant_id
            or reservation.conversation_id != binding.task.parent.conversation_id
            or reservation.run_id != binding.task.parent.run_id or reservation.attempt_id != binding.task.parent.attempt_id
            or reservation.generation != binding.task.parent.attempt_generation
            or reservation.model_input_digest != binding.prepared.model_input_digest
            or consumption.record.call_id != reservation.call_id):
            raise SpecialistResponseDenied("SPECIALIST_RESPONSE_TASK_MISMATCH")
        if datetime.now(UTC) >= min(consumption.expires_at, binding.task.deadline):
            raise SpecialistResponseDenied("SPECIALIST_RESPONSE_READ_EXPIRED")
        copies = (SpecialistBudgetReservation.model_validate_json(reservation.model_dump_json()),
            SpecialistDispatchConsumption.model_validate_json(consumption.model_dump_json()),
            SpecialistAccountingRecord.model_validate_json(accounting.model_dump_json()))
        digests = tuple(contract_digest(item) for item in copies)
        transaction = session.get_transaction()
        loop = asyncio.get_running_loop()
        cutoff = loop.time() + min(1, max(0, (consumption.expires_at - datetime.now(UTC)).total_seconds()))
        try:
            async with asyncio.timeout_at(cutoff):
                observation = await self._source.read_answer(session, reservation=copies[0], consumption=copies[1], accounting=copies[2])
        except asyncio.CancelledError:
            raise
        except Exception:
            raise SpecialistResponseDenied("SPECIALIST_RESPONSE_READ_UNCONFIRMED") from None
        if (loop.time() >= cutoff or transaction is None or session.get_transaction() is not transaction
            or not transaction.is_active or tuple(contract_digest(item) for item in copies) != digests):
            raise SpecialistResponseDenied("SPECIALIST_RESPONSE_READ_UNCONFIRMED")
        if observation is None:
            return None
        if not isinstance(observation, SpecialistProviderAnswerObservation):
            raise SpecialistResponseDenied("SPECIALIST_RESPONSE_READ_INVALID")
        observation = SpecialistProviderAnswerObservation.model_validate_json(observation.model_dump_json())
        conversion = convert_specialist_answer(binding=binding, reservation=reservation,
            consumption=consumption, accounting=accounting, observation=observation,
            redaction_secrets=(self._settings.jwt_secret, self._settings.secret_encryption_key, *self._redaction_secrets))
        if loop.time() >= cutoff or datetime.now(UTC) >= consumption.expires_at:
            raise SpecialistResponseDenied("SPECIALIST_RESPONSE_READ_EXPIRED")
        return SpecialistResultObservation(observation_id=observation.observation_id, call_id=reservation.call_id,
            consumption_digest=contract_digest(consumption), accounting_receipt_digest=accounting.receipt_digest,
            result_digest=conversion.result_digest, observed_at=datetime.now(UTC))


class SpecialistKnowledgeResultReader(SpecialistResultReader):
    _allowed_specialists = frozenset({"knowledge"})
