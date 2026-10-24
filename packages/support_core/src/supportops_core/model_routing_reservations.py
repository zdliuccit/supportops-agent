"""路由选型和共享账本的原子预留；无默认政策授权源或模型派发入口。"""

from __future__ import annotations

import asyncio
import math
from datetime import UTC, datetime
from decimal import Decimal
from fractions import Fraction
from typing import Protocol
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from supportops_core.config import Settings
from supportops_core.context_services import ContextSnapshot
from supportops_core.history_context_services import assert_history_context_authorized
from supportops_core.knowledge_retrieval import assert_attempt_knowledge_authorized
from supportops_core.model_routing_eligibility import _price, RoutingDataBoundaryReader, filter_routing_candidates
from supportops_core.model_routing_policy_services import RoutingPolicyConflict, assert_routing_policy_plan
from supportops_core.model_routing_accounting_contracts import RoutingPriceSnapshot, routing_usage_cost
from supportops_core.model_routing_accounting_guards import routing_accounting_blocks_budget
from supportops_core.model_routing_contracts import RoutingCandidateFilterReport
from supportops_core.specialist_accounting_guards import specialist_accounting_blocks_budget, specialist_shared_tokens_allow
from supportops_core.model_routing_reservation_contracts import (
    RoutingBudgetAuthorization, RoutingCallPlan, RoutingReservationRecord,
)
from supportops_core.models import AgentRun, ModelCallLedger, ModelEndpointVersion, RunAttempt, RunEvent, Tenant, utc_now
from supportops_core.quota_services import quota_day_start
from supportops_core.release_manifest_services import manifest_digest
from supportops_core.run_attempt_services import (
    RunAttemptIdentity, current_run_identity, owned_run,
)
from supportops_core.services import ResourceNotFoundError, append_event
from supportops_core.tool_context_services import assert_tool_context_authorized


class RoutingReservationDenied(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__("路由调用预留未获许可")
        self.code = code


class RoutingReservationUnconfirmed(RuntimeError):
    code = "ROUTING_RESERVATION_UNCONFIRMED"

    def __init__(self) -> None:
        super().__init__("路由预留提交结果暂不可确认，请只读检查原调用标识")


class RoutingReservationAuthority(Protocol):
    """可信存储须复查24.1、W19/W23、不可变政策、v3固定候选和真实完整输入计量。

    必须验证plan对应当前Run/主体/Agent版本/attempt/generation/原模型输入与ContextSnapshot，
    候选及各revision属于原发布解析集合，未撤回/撤权，固定政策和预算当前获批且未撤销。
    仅返回该发布配置的限制，不能用模型/客户端声明的摘要或布尔值代替批准和输入计量。
    输入预留须沿用utf8_upper_bound_v1完整输入保守计量，并覆盖系统/历史/工具/知识与输出。
    提供者不得提交、回滚或替换调用者事务。当前未提供默认实现；
    该协议不是批准记录、预算许可或运行时开关。
    """

    async def authorize(
        self, session: AsyncSession, *, plan: RoutingCallPlan,
    ) -> RoutingBudgetAuthorization: ...


def _identity(plan: RoutingCallPlan) -> RunAttemptIdentity:
    return RunAttemptIdentity(tenant_id=plan.requirements.tenant_id, conversation_id=plan.conversation_id,
        run_id=plan.run_id, attempt_id=plan.attempt_id, generation=plan.generation, owner_id=plan.owner_id)


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _window(plan: RoutingCallPlan) -> None:
    if not plan.created_at <= utc_now() < plan.expires_at:
        raise RoutingReservationDenied("ROUTING_PLAN_EXPIRED")


async def _authorization(
    session: AsyncSession, *, plan: RoutingCallPlan, authority: RoutingReservationAuthority, settings: Settings,
) -> RoutingBudgetAuthorization:
    _window(plan)
    loop = asyncio.get_running_loop()
    checked_since = utc_now()
    deadline = loop.time() + min(1, (plan.expires_at - utc_now()).total_seconds())
    async with asyncio.timeout_at(deadline):
        authorization = await authority.authorize(session, plan=plan)
    _window(plan)
    if (loop.time() >= deadline or not isinstance(authorization, RoutingBudgetAuthorization)
        or authorization.plan_digest != manifest_digest(plan.model_dump(mode="json"))
        or not checked_since <= authorization.checked_at <= utc_now() < authorization.expires_at
        or authorization.expires_at > plan.expires_at
        or authorization.run_model_call_limit > settings.agent_model_call_limit_max
        or authorization.run_timeout_seconds > settings.agent_run_timeout_seconds_max):
        raise RoutingReservationDenied("ROUTING_AUTHORIZATION_UNCONFIRMED")
    return authorization


async def _current_context(
    session: AsyncSession, *, plan: RoutingCallPlan, settings: Settings, authorization: RoutingBudgetAuthorization,
) -> None:
    try:
        await assert_routing_policy_plan(session, plan=plan)
    except RoutingPolicyConflict as exc:
        raise RoutingReservationDenied(exc.code) from None
    owned = await owned_run(session, _identity(plan))
    identity = await current_run_identity(session, run=owned.run, conversation=owned.conversation)
    if (owned.run.agent_version_id != plan.requirements.agent_version_id
        or identity.principal.user_id != authorization.actor_user_id
        or owned.attempt.context_snapshot is None
        or manifest_digest(owned.attempt.context_snapshot) != plan.context_snapshot_digest):
        raise RoutingReservationDenied("ROUTING_CONTEXT_MISMATCH")
    snapshot = ContextSnapshot.model_validate(owned.attempt.context_snapshot)
    await assert_history_context_authorized(session, snapshot=snapshot, identity=identity,
        conversation_id=plan.conversation_id, run=owned.run)
    await assert_attempt_knowledge_authorized(session, identity=identity, attempt=_identity(plan))
    await assert_tool_context_authorized(session, snapshot=snapshot, identity=identity, run=owned.run,
        settings=settings)
    _window(plan)
    if not authorization.checked_at <= utc_now() < authorization.expires_at:
        raise RoutingReservationDenied("ROUTING_AUTHORIZATION_UNCONFIRMED")


async def read_routing_reservation(
    session: AsyncSession, *, tenant_id: UUID, run_id: UUID, call_id: UUID,
) -> RoutingReservationRecord:
    """可信同租户读取入口复核原记录，不要求原计划窗口仍有效，不返回派发许可。"""
    ledger = await session.scalar(select(ModelCallLedger).where(
        ModelCallLedger.id == call_id, ModelCallLedger.run_id == run_id, ModelCallLedger.tenant_id == tenant_id)
        .execution_options(populate_existing=True))
    if ledger is None or ledger.policy_version != "routing_priority_v1":
        raise ResourceNotFoundError("路由预留记录不存在")
    run = (await session.execute(select(AgentRun.conversation_id, AgentRun.agent_version_id,
        AgentRun.model_endpoint_version_id).where(AgentRun.id == run_id, AgentRun.tenant_id == tenant_id))).one_or_none()
    attempt = await session.scalar(select(RunAttempt.generation).where(
        RunAttempt.id == ledger.attempt_id, RunAttempt.run_id == run_id, RunAttempt.tenant_id == tenant_id))
    events = (await session.scalars(select(RunEvent).where(
        RunEvent.tenant_id == tenant_id, RunEvent.run_id == run_id, RunEvent.attempt_id == ledger.attempt_id,
        RunEvent.event_type == "budget.model_reserved", RunEvent.data["call_id"].as_string() == str(call_id),
    ).order_by(RunEvent.sequence).limit(2))).all()
    try:
        if run is None or attempt is None or len(events) != 1:
            raise ValueError()
        data = events[0].data
        record = RoutingReservationRecord.model_validate(data["routing_selection"])
        if (data.get("routing_selection_digest") != manifest_digest(record.model_dump(mode="json"))
            or record.call_id != call_id or record.run_id != run_id or record.tenant_id != tenant_id
            or record.attempt_id != ledger.attempt_id or record.generation != attempt
            or record.conversation_id != run.conversation_id or record.agent_version_id != run.agent_version_id
            or record.run_original_model_version_id != run.model_endpoint_version_id
            or record.actual_model_version_id != ledger.model_endpoint_version_id
            or record.call_number != ledger.call_number or ledger.retry_index != 0
            or ledger.token_strategy != "utf8_upper_bound_v1"
            or record.reserved_input_tokens != ledger.estimated_input_tokens
            or record.reserved_output_tokens != ledger.reserved_output_tokens
            or record.reserved_cost_microusd != ledger.reserved_cost_microusd
            or ledger.status not in {"reserved", "settled", "unknown"}):
            raise ValueError()
        return record
    except (KeyError, TypeError, ValueError, ValidationError):
        raise RoutingReservationDenied("ROUTING_RESERVATION_HISTORY_INVALID") from None


async def _existing_record(session: AsyncSession, *, plan: RoutingCallPlan) -> RoutingReservationRecord | None:
    scope = (await session.execute(select(ModelCallLedger.tenant_id, ModelCallLedger.run_id,
        ModelCallLedger.attempt_id).where(ModelCallLedger.id == plan.call_id))).one_or_none()
    if scope is None:
        return None
    if (scope.tenant_id != plan.requirements.tenant_id or scope.run_id != plan.run_id
        or scope.attempt_id != plan.attempt_id):
        raise RoutingReservationDenied("ROUTING_CALL_ID_CONFLICT")
    record = await read_routing_reservation(session, tenant_id=plan.requirements.tenant_id,
        run_id=plan.run_id, call_id=plan.call_id)
    try:
        selected = next((candidate for candidate in plan.candidates if candidate.candidate_id == record.candidate_id), None)
        if (record.plan_digest != manifest_digest(plan.model_dump(mode="json"))
            or record.conversation_id != plan.conversation_id or record.generation != plan.generation
            or record.requirements_digest != manifest_digest(plan.requirements.model_dump(mode="json"))
            or record.model_input_digest != plan.model_input_digest
            or record.context_snapshot_digest != plan.context_snapshot_digest
            or record.policy_version_id != plan.requirements.policy_version_id
            or record.policy_digest != plan.requirements.policy_digest
            or selected is None or selected.model_version_id != record.actual_model_version_id
            or selected.credential_revision != record.credential_revision
            or manifest_digest(selected.model_dump(mode="json")) != record.candidate_digest):
            raise ValueError()
        return record
    except (KeyError, TypeError, ValueError, ValidationError):
        raise RoutingReservationDenied("ROUTING_RESERVATION_HISTORY_INVALID") from None


async def _check_budget(
    session: AsyncSession, *, plan: RoutingCallPlan, authorization: RoutingBudgetAuthorization,
    cost: int, settings: Settings, already_reserved: bool = False,
) -> int:
    if not await specialist_shared_tokens_allow(session, tenant_id=plan.requirements.tenant_id, run_id=plan.run_id,
        input_tokens=0 if already_reserved else plan.requirements.reserved_input_tokens,
        output_tokens=0 if already_reserved else plan.requirements.reserved_output_tokens):
        raise RoutingReservationDenied("SPECIALIST_SHARED_TOKEN_BUDGET_EXCEEDED")
    if await specialist_accounting_blocks_budget(session, tenant_id=plan.requirements.tenant_id, run_id=plan.run_id,
        enforce_tenant_cost=settings.tenant_model_cost_usd_per_day is not None):
        raise RoutingReservationDenied("SPECIALIST_USAGE_BOUND_EXCEEDED")
    if await routing_accounting_blocks_budget(session, tenant_id=plan.requirements.tenant_id, run_id=plan.run_id,
        enforce_tenant_cost=settings.tenant_model_cost_usd_per_day is not None):
        raise RoutingReservationDenied("ROUTING_USAGE_BOUND_EXCEEDED")
    # 共享锁顺序沿用会话→Run→attempt→租户；所有固定/路由模型均消费同一账本。
    first_start = await session.scalar(select(func.min(RunAttempt.started_at)).where(
        RunAttempt.run_id == plan.run_id, RunAttempt.tenant_id == plan.requirements.tenant_id))
    if first_start is None or (utc_now() - _utc(first_start)).total_seconds() >= authorization.run_timeout_seconds:
        raise RoutingReservationDenied("AGENT_RUN_TIME_BUDGET_EXCEEDED")
    consumed = func.coalesce(ModelCallLedger.settled_cost_microusd, ModelCallLedger.reserved_cost_microusd)
    count, run_cost, unknown = (await session.execute(select(func.count(), func.coalesce(func.sum(consumed), 0),
        func.count().filter(consumed.is_(None))).where(
        ModelCallLedger.run_id == plan.run_id, ModelCallLedger.tenant_id == plan.requirements.tenant_id))).one()
    increment = 0 if already_reserved else 1
    additional_cost = 0 if already_reserved else cost
    if count + increment > authorization.run_model_call_limit:
        raise RoutingReservationDenied("AGENT_MODEL_CALL_BUDGET_EXCEEDED")
    if unknown or run_cost + additional_cost > authorization.run_cost_limit_microusd:
        raise RoutingReservationDenied("AGENT_COST_BUDGET_EXCEEDED")
    day_count, day_cost, day_unknown = (await session.execute(select(func.count(), func.coalesce(func.sum(consumed), 0),
        func.count().filter(consumed.is_(None))).where(
        ModelCallLedger.tenant_id == plan.requirements.tenant_id,
        ModelCallLedger.created_at >= quota_day_start()))).one()
    if day_count + increment > settings.tenant_model_call_limit_per_day:
        raise RoutingReservationDenied("TENANT_MODEL_CALL_QUOTA_EXCEEDED")
    if settings.tenant_model_cost_usd_per_day is not None:
        amount = settings.tenant_model_cost_usd_per_day
        if not math.isfinite(amount):
            raise RoutingReservationDenied("TENANT_MODEL_COST_QUOTA_EXCEEDED")
        limit = Fraction(Decimal(str(amount))) * 1_000_000
        limit_microusd = limit.numerator // limit.denominator
        if day_unknown or day_cost + additional_cost > limit_microusd:
            raise RoutingReservationDenied("TENANT_MODEL_COST_QUOTA_EXCEEDED")
    # call_number为既有单调序号；检测历史缺口，不能仅按count制造重复序号。
    last = int(await session.scalar(select(func.max(ModelCallLedger.call_number)).where(
        ModelCallLedger.run_id == plan.run_id, ModelCallLedger.tenant_id == plan.requirements.tenant_id)) or 0)
    if last != count:
        raise RoutingReservationDenied("ROUTING_RESERVATION_HISTORY_INVALID")
    return last + 1


class _RoutingReservationSelection(Protocol):
    """包内回退适配；沿用同一事务/预算/价格，不提交事务，不授予派发许可。"""

    async def choose(self, session: AsyncSession, *, plan: RoutingCallPlan,
        report: RoutingCandidateFilterReport,
    ) -> UUID: ...

    async def record(self, session: AsyncSession, *, reservation: RoutingReservationRecord) -> None: ...


async def reserve_routed_model_call(
    session_factory: async_sessionmaker[AsyncSession], *, plan: RoutingCallPlan, settings: Settings,
    authority: RoutingReservationAuthority | None = None, boundary_reader: RoutingDataBoundaryReader | None = None,
) -> RoutingReservationRecord:
    """提交选型/预留后返回记录；不派发SDK，不自动重试未知提交，不改变Run原模型。"""
    return await _reserve_routed_model_call(session_factory, plan=plan, settings=settings,
        authority=authority, boundary_reader=boundary_reader)


async def _reserve_routed_model_call(
    session_factory: async_sessionmaker[AsyncSession], *, plan: RoutingCallPlan, settings: Settings,
    authority: RoutingReservationAuthority | None, boundary_reader: RoutingDataBoundaryReader | None,
    selection: _RoutingReservationSelection | None = None,
) -> RoutingReservationRecord:
    if authority is None or boundary_reader is None:
        raise RoutingReservationDenied("ROUTING_DEPENDENCIES_UNAVAILABLE")
    _window(plan)
    try:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 20
        async with asyncio.timeout_at(deadline):
            async with session_factory() as session, session.begin():
                if session.bind is None or session.bind.dialect.name != "postgresql":
                    raise RoutingReservationDenied("ROUTING_POSTGRES_REQUIRED")
                await session.execute(text("SET LOCAL lock_timeout = '250ms'"))
                await session.execute(text("SET LOCAL statement_timeout = '2s'"))
                owned = await owned_run(session, _identity(plan))
                tenant = await session.scalar(select(Tenant).where(Tenant.id == plan.requirements.tenant_id)
                    .execution_options(populate_existing=True).with_for_update(key_share=selection is None))
                if tenant is None or tenant.status != "active":
                    raise RoutingReservationDenied("ROUTING_AUTHORIZATION_UNCONFIRMED")
                authorization = await _authorization(session, plan=plan, authority=authority, settings=settings)
                await _current_context(session, plan=plan, settings=settings, authorization=authorization)
                prior = await _existing_record(session, plan=plan)
                if prior is not None:
                    if selection is not None:
                        raise RoutingReservationDenied("ROUTING_FAILOVER_CALL_ALREADY_RESERVED")
                    if prior.selection_reason != "first_eligible_priority_v1":
                        raise RoutingReservationDenied("ROUTING_CALL_ID_CONFLICT")
                    return prior
                report = await filter_routing_candidates(session, requirements=plan.requirements,
                    candidates=plan.candidates, boundary_reader=boundary_reader)
                eligible = [item for item in report.candidates if item.status == "eligible"]
                if not eligible:
                    raise RoutingReservationDenied("ROUTING_NO_ELIGIBLE_CANDIDATE")
                selected = eligible[0]
                if selection is not None:
                    transaction = session.get_transaction()
                    selected_id = await selection.choose(session, plan=plan, report=report)
                    if transaction is None or session.get_transaction() is not transaction or not transaction.is_active:
                        raise RoutingReservationDenied("ROUTING_FAILOVER_TRANSACTION_CHANGED")
                    choice = next((item for item in eligible if item.candidate_id == selected_id), None)
                    if choice is None:
                        raise RoutingReservationDenied("ROUTING_FAILOVER_CANDIDATE_EXCLUDED")
                    selected = choice
                if selected.estimated_cost_microusd is None:
                    raise RoutingReservationDenied("MODEL_PRICING_UNKNOWN")
                candidate = next(item for item in plan.candidates if item.candidate_id == selected.candidate_id)
                # 外部治理等待后再次复检。原预算政策不能在同次预留中悄悄变化。
                current = await _authorization(session, plan=plan, authority=authority, settings=settings)
                if (current.actor_user_id != authorization.actor_user_id
                    or current.approval_reference_digest != authorization.approval_reference_digest
                    or current.run_model_call_limit != authorization.run_model_call_limit
                    or current.run_cost_limit_microusd != authorization.run_cost_limit_microusd
                    or current.run_timeout_seconds != authorization.run_timeout_seconds):
                    raise RoutingReservationDenied("ROUTING_AUTHORIZATION_UNCONFIRMED")
                await _current_context(session, plan=plan, settings=settings, authorization=current)
                call_number = await _check_budget(session, plan=plan, authorization=current,
                    cost=selected.estimated_cost_microusd, settings=settings)
                record = RoutingReservationRecord(call_id=plan.call_id, tenant_id=plan.requirements.tenant_id,
                    run_id=plan.run_id, conversation_id=plan.conversation_id, attempt_id=plan.attempt_id,
                    generation=plan.generation, plan_digest=manifest_digest(plan.model_dump(mode="json")),
                    model_input_digest=plan.model_input_digest, context_snapshot_digest=plan.context_snapshot_digest,
                    agent_version_id=plan.requirements.agent_version_id,
                    run_original_model_version_id=owned.run.model_endpoint_version_id,
                    policy_version_id=plan.requirements.policy_version_id, policy_digest=plan.requirements.policy_digest,
                    approval_reference_digest=current.approval_reference_digest,
                    candidate_id=candidate.candidate_id, candidate_digest=manifest_digest(candidate.model_dump(mode="json")),
                    selection_reason="first_eligible_failover_v1" if selection is not None else "first_eligible_priority_v1",
                    actual_model_version_id=candidate.model_version_id, credential_revision=candidate.credential_revision,
                    filter_report_digest=manifest_digest(report.model_dump(mode="json")),
                    requirements_digest=report.requirements_digest, candidate_set_digest=report.candidate_set_digest,
                    call_number=call_number, reserved_input_tokens=plan.requirements.reserved_input_tokens,
                    reserved_output_tokens=plan.requirements.reserved_output_tokens,
                    reserved_cost_microusd=selected.estimated_cost_microusd, recorded_at=utc_now())
                # 与预留同事务冻结所选版本原价格；旧记录缺价格时不使用当前价格补造。
                pricing = await session.scalar(select(ModelEndpointVersion.pricing).where(
                    ModelEndpointVersion.tenant_id == record.tenant_id,
                    ModelEndpointVersion.id == record.actual_model_version_id).with_for_update(read=True))
                if not isinstance(pricing, dict) or manifest_digest(pricing) != candidate.pricing_digest:
                    raise RoutingReservationDenied("ROUTING_PRICING_CHANGED")
                input_rate = _price(pricing.get("input_per_million_tokens"))
                output_rate = _price(pricing.get("output_per_million_tokens"))
                if pricing.get("currency") != "USD" or input_rate is None or output_rate is None:
                    raise RoutingReservationDenied("MODEL_PRICING_UNKNOWN")
                price_snapshot = RoutingPriceSnapshot(call_id=record.call_id, tenant_id=record.tenant_id,
                    actual_model_version_id=record.actual_model_version_id, candidate_digest=record.candidate_digest,
                    source_pricing_digest=candidate.pricing_digest, source_pricing=pricing,
                    input_per_million_tokens=format(input_rate.copy_abs(), "f"), output_per_million_tokens=format(output_rate.copy_abs(), "f"))
                if routing_usage_cost(price_snapshot, input_tokens=record.reserved_input_tokens,
                    output_tokens=record.reserved_output_tokens) != record.reserved_cost_microusd:
                    raise RoutingReservationDenied("ROUTING_PRICING_CHANGED")
                session.add(ModelCallLedger(id=plan.call_id, tenant_id=plan.requirements.tenant_id,
                    run_id=plan.run_id, attempt_id=plan.attempt_id, call_number=call_number, retry_index=0,
                    model_endpoint_version_id=candidate.model_version_id, policy_version="routing_priority_v1",
                    token_strategy="utf8_upper_bound_v1",
                    status="reserved", estimated_input_tokens=record.reserved_input_tokens,
                    reserved_output_tokens=record.reserved_output_tokens,
                    reserved_cost_microusd=record.reserved_cost_microusd,
                    omitted_message_count=plan.omitted_message_count))
                await session.flush()
                await append_event(session, run_id=plan.run_id, attempt_id=plan.attempt_id,
                    event_type="budget.model_reserved", data={"call_id": str(plan.call_id), "call_number": call_number,
                        "token_strategy": "utf8_upper_bound_v1", "input_tokens_reserved": record.reserved_input_tokens,
                        "output_tokens_reserved": record.reserved_output_tokens,
                        "cost_reserved_microusd": record.reserved_cost_microusd,
                        "omitted_message_count": plan.omitted_message_count,
                        "routing_selection": record.model_dump(mode="json"),
                        "routing_selection_digest": manifest_digest(record.model_dump(mode="json")),
                        "routing_pricing_snapshot": price_snapshot.model_dump(mode="json"),
                        "routing_pricing_snapshot_digest": manifest_digest(price_snapshot.model_dump(mode="json"))})
                if selection is not None:
                    transaction = session.get_transaction()
                    await selection.record(session, reservation=record)
                    if transaction is None or session.get_transaction() is not transaction or not transaction.is_active:
                        raise RoutingReservationDenied("ROUTING_FAILOVER_TRANSACTION_CHANGED")
                await _current_context(session, plan=plan, settings=settings, authorization=current)
                _window(plan)
            # 提交退出后再次检查窗口；迟到提交保留原事实，不能返回可重用许可。
            if not plan.created_at <= utc_now() < plan.expires_at or loop.time() >= deadline:
                raise RoutingReservationUnconfirmed()
            return record
    except RoutingReservationDenied:
        raise
    except Exception:
        # 既有同键记录可能已提交，未知结果不再预留、不删除或退还消费；取消由上层原样处理。
        raise RoutingReservationUnconfirmed() from None
