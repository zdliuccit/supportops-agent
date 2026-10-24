"""已缓存完整路由回答的产品结果准备；不发布正文、不提交事务或启用Worker。"""

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, model_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from supportops_core.agent_config import SupportAnswer, SupportCitation, SupportEvidenceConflict
from supportops_core.config import Settings
from supportops_core.model_routing_accounting_contracts import RoutingAccountingView
from supportops_core.model_routing_contracts import Digest, RoutingFilterModel
from supportops_core.model_routing_coordinator import RoutingCoordinationResult, _BoundHistory
from supportops_core.model_routing_dispatch_contracts import RoutingDispatchConsumption
from supportops_core.model_routing_eligibility import RoutingDataBoundaryReader, filter_routing_candidates
from supportops_core.model_routing_execution_source import (
    PostgresRoutingExecutionHistoryReader, read_routing_sdk_observation,
)
from supportops_core.model_routing_failover_contracts import RoutingFailoverControls
from supportops_core.model_routing_request_contracts import bounded_request_json
from supportops_core.model_routing_request_services import routing_outcome_event_exists
from supportops_core.model_routing_reservation_contracts import RoutingReservationRecord
from supportops_core.model_routing_reservations import RoutingReservationAuthority, _identity
from supportops_core.model_routing_runtime_input import RoutingRuntimeInput
from supportops_core.model_routing_sdk_executor import RoutingSDKResult
from supportops_core.redaction import redact_sensitive_text
from supportops_core.release_manifest_services import manifest_digest
from supportops_core.run_attempt_services import current_run_identity, owned_run
from supportops_core.support_result_services import SupportResult, build_support_result

class RoutingResultDenied(ValueError):
    def __init__(self, code: str = "ROUTING_RESULT_UNCONFIRMED") -> None:
        super().__init__("原路由回答当前不可准备为产品结果，请只读查询原call，勿重新派发")
        self.code = code


class RoutingPreparedResultBinding(RoutingFilterModel):
    """短期准备事实；未来产品提交仍须同事务复检，不能把此对象作为批准。"""

    schema_version: Literal["routing_prepared_result_v1"] = "routing_prepared_result_v1"
    root_call_id: UUID
    root_plan_digest: Digest
    final_call_id: UUID
    actor_user_id: UUID
    history_source_digest: Digest
    sdk_observation_digest: Digest
    provider_answer_digest: Digest
    support_result_digest: Digest
    canonical_answer_digest: Digest
    checked_at: AwareDatetime
    expires_at: AwareDatetime
    product_commit_permitted: Literal[False] = False

    @model_validator(mode="after")
    def bounded_preparation(self) -> "RoutingPreparedResultBinding":
        if not 0 < (self.expires_at - self.checked_at).total_seconds() <= 1:
            raise ValueError("ROUTING_RESULT_PREPARATION_WINDOW_INVALID")
        return self


@dataclass(frozen=True, slots=True)
class PreparedRoutingSupportResult:
    binding: RoutingPreparedResultBinding
    answer: SupportAnswer = field(repr=False)
    support_result: SupportResult = field(repr=False)


async def assert_routing_product_unpublished(session: AsyncSession, *, tenant_id: UUID, run_id: UUID) -> None:
    """原Run锁下检查完整缓存路径，空正文事件同样不属于未开始的产品发布。"""
    if await routing_outcome_event_exists(session, tenant_id=tenant_id, run_id=run_id):
        raise RoutingResultDenied("ROUTING_RESULT_OUTPUT_ALREADY_STARTED")


def _canonical_answer(result: SupportResult) -> SupportAnswer:
    """沿用服务器降级/冲突判断，引用标题取当前获准证据，不能沿用模型自报标题。"""
    if (any(row.status != "available" or not row.title for row in result.citations)
        or any(row.status != "available" or not row.reason for row in result.evidence_conflicts)
        or result.diagnostic is not None or result.business_actions):
        raise RoutingResultDenied("ROUTING_RESULT_ADAPTER_UNSUPPORTED")
    return SupportAnswer(
        answer=result.answer, summary=result.summary,
        citations=[SupportCitation(title=row.title, evidence_id=row.evidence_id)
            for row in result.citations if row.title is not None],
        suggested_actions=[row.description for row in result.suggested_actions],
        escalation_recommended=result.escalation_status == "recommended",
        evidence_conflicts=[SupportEvidenceConflict(evidence_ids=row.evidence_ids, reason=row.reason)
            for row in result.evidence_conflicts if row.reason is not None],
    )


def _copy_attempt(result: RoutingSDKResult) -> RoutingSDKResult:
    """在任何等待前复制内存回答和回执，后续仍以持久原观察逐项查证。"""
    if (result.product_adoption_permitted is not False or result.io_stop_confirmed is not True
        or result.outcome not in {"completed", "failed", "unknown", "contract_failure"}
        or result.accounting is None):
        raise RoutingResultDenied("ROUTING_RESULT_CALL_UNCONFIRMED")
    return RoutingSDKResult(
        reservation=RoutingReservationRecord.model_validate_json(result.reservation.model_dump_json()),
        consumption=RoutingDispatchConsumption.model_validate_json(result.consumption.model_dump_json()),
        accounting=RoutingAccountingView.model_validate_json(result.accounting.model_dump_json()),
        outcome=result.outcome,
        answer=SupportAnswer.model_validate_json(result.answer.model_dump_json(), strict=True)
            if result.answer is not None else None,
        io_stop_confirmed=True,
    )


async def prepare_routing_support_result(
    session: AsyncSession, *, input: RoutingRuntimeInput, coordination: RoutingCoordinationResult,
    controls: RoutingFailoverControls, settings: Settings,
    authority: RoutingReservationAuthority | None = None,
    boundary_reader: RoutingDataBoundaryReader | None = None,
    redaction_secrets: tuple[str, ...] = (),
) -> PreparedRoutingSupportResult:
    """只在原输入协程及调用者PG事务中准备；原Run锁一直由调用者持有。

    首个适配只支持完全缓存、无公开正文/工具的成功路径。失败或已有正文整份拒绝，
    不清除其他attempt正文、不调用SDK/工具、不自动回退。返回值不能绕过未来v3
    Worker提交、checkpoint、W23门禁及最终当前授权复检；当前不注册到产品流水线。
    """
    if authority is None or boundary_reader is None:
        raise RoutingResultDenied("ROUTING_RESULT_DEPENDENCIES_UNAVAILABLE")
    try:
        root = input.plan
        controls = RoutingFailoverControls.model_validate_json(controls.model_dump_json())
        if (coordination.product_adoption_permitted is not False or coordination.status != "completed"
            or coordination.reason != "completed" or coordination.root_call_id != root.call_id
            or coordination.root_plan_digest != manifest_digest(root.model_dump(mode="json"))
            or not 1 <= len(coordination.attempts) <= controls.max_physical_calls):
            raise RoutingResultDenied("ROUTING_RESULT_COORDINATION_INCOMPLETE")
        attempts = tuple(_copy_attempt(row) for row in coordination.attempts)
        final = attempts[-1]
        if final.outcome != "completed" or final.answer is None:
            raise RoutingResultDenied("ROUTING_RESULT_ANSWER_UNAVAILABLE")
        answer = final.answer
        serialized = answer.model_dump_json()
        bounded_request_json(serialized)
        secrets = (settings.jwt_secret, settings.secret_encryption_key, *redaction_secrets)
        if redact_sensitive_text(serialized, secrets=secrets) != serialized:
            raise RoutingResultDenied("ROUTING_RESULT_REDACTION_REQUIRED")
        answer_digest = manifest_digest(answer.model_dump(mode="json"))
        transaction = session.get_transaction()
        if transaction is None or not transaction.is_active or session.get_bind().dialect.name != "postgresql":
            raise RoutingResultDenied("ROUTING_RESULT_TRANSACTION_REQUIRED")
        remaining = (root.expires_at - datetime.now(UTC)).total_seconds()
        if remaining <= 0:
            raise RoutingResultDenied("ROUTING_RESULT_EXPIRED")
        async with asyncio.timeout(min(5, remaining)):
            await session.execute(text("SET LOCAL lock_timeout = '250ms'"))
            await session.execute(text("SET LOCAL statement_timeout = '2s'"))
            await input.read(session, plan=root)
            await assert_routing_product_unpublished(session, tenant_id=root.requirements.tenant_id, run_id=root.run_id)
            history = _BoundHistory(root, controls,
                PostgresRoutingExecutionHistoryReader(settings=settings, authority=authority))
            history.results = {row.reservation.call_id: row for row in attempts}
            if len(history.results) != len(attempts):
                raise RoutingResultDenied("ROUTING_RESULT_DUPLICATE_CALL")
            before = await history.observe(session)
            if tuple(row.reservation.call_id for row in attempts) != tuple(
                row.accounting.reservation.call_id for row in before.attempts
            ):
                raise RoutingResultDenied("ROUTING_RESULT_CALL_ORDER_MISMATCH")
            if (any(row.public_text_emitted or row.business_tool_calls_started or row.tool_outcome != "none"
                    for row in before.attempts)
                or before.attempts[-1].outcome != "completed"
                or any(row.outcome not in {"transient_failure", "rate_limited"} for row in before.attempts[:-1])):
                raise RoutingResultDenied("ROUTING_RESULT_OUTPUT_ALREADY_STARTED")
            observation = await read_routing_sdk_observation(session, reservation=final.reservation)
            receipt = observation.receipt
            if (observation.schema_version != "routing_sdk_observation_v2"
                or observation.answer_digest != answer_digest or observation.answer_contract != "accepted"
                or observation.classification != "completed" or receipt is None or receipt.finished_at is None
                or receipt.finished_at > min(root.expires_at, final.consumption.record.expires_at)
                or final.accounting is None or final.accounting.accounting.status != "settled"
                or final.accounting.accounting.reservation_exceeded or final.accounting.accounting.error_code is not None):
                raise RoutingResultDenied("ROUTING_RESULT_OBSERVATION_MISMATCH")
            identity_scope = _identity(root)
            owned = await owned_run(session, identity_scope)
            identity = await current_run_identity(session, run=owned.run, conversation=owned.conversation)
            result = await build_support_result(session, answer=answer, identity=identity, attempt=identity_scope)
            canonical = _canonical_answer(result)
            content = canonical.model_dump_json()
            bounded_request_json(content)
            if redact_sensitive_text(content, secrets=secrets) != content:
                raise RoutingResultDenied("ROUTING_RESULT_REDACTION_REQUIRED")
            # 最后重新过滤实际获胜候选，完成后撤销/探测失效不能沿用SDK结束时的观察。
            report = await filter_routing_candidates(session, requirements=root.requirements,
                candidates=root.candidates, boundary_reader=boundary_reader)
            selected = next((row for row in report.candidates if row.candidate_id == final.reservation.candidate_id), None)
            if (selected is None or selected.status != "eligible" or selected.latest_probe_id is None
                or selected.estimated_cost_microusd != final.reservation.reserved_cost_microusd):
                raise RoutingResultDenied("ROUTING_RESULT_CANDIDATE_UNAVAILABLE")
            await input.read(session, plan=root)
            after = await history.observe(session)
            current_observation = await read_routing_sdk_observation(session, reservation=final.reservation)
            if input.plan != root:
                raise RoutingResultDenied("ROUTING_RESULT_INPUT_CLOSED")
            await assert_routing_product_unpublished(session, tenant_id=root.requirements.tenant_id, run_id=root.run_id)
            checked = datetime.now(UTC)
            expires = min(root.expires_at, after.expires_at, report.observed_at + timedelta(seconds=1))
            if (session.get_transaction() is not transaction or not transaction.is_active
                or before.attempts != after.attempts or before.source_reference_digest != after.source_reference_digest
                or observation != current_observation or checked >= expires or report.observed_at > checked):
                raise RoutingResultDenied("ROUTING_RESULT_CHANGED")
            binding = RoutingPreparedResultBinding(root_call_id=root.call_id,
                root_plan_digest=manifest_digest(root.model_dump(mode="json")), final_call_id=final.reservation.call_id,
                actor_user_id=identity.principal.user_id, history_source_digest=after.source_reference_digest,
                sdk_observation_digest=manifest_digest(observation.model_dump(mode="json")),
                provider_answer_digest=answer_digest, support_result_digest=manifest_digest(result.model_dump(mode="json")),
                canonical_answer_digest=manifest_digest(canonical.model_dump(mode="json")), checked_at=checked, expires_at=expires)
            return PreparedRoutingSupportResult(binding, canonical, result)
    except RoutingResultDenied:
        raise
    except Exception:
        raise RoutingResultDenied() from None
