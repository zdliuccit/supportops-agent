"""只读子任务逐物理模型调用预留；固定/路由/子任务共用原Run/租户账本。"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from fractions import Fraction
from typing import Protocol
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from supportops_core.agent_config import AgentConfigV2, agent_config_digest, parse_agent_config
from supportops_core.config import Settings
from supportops_core.context_services import ContextSnapshot
from supportops_core.credential_services import assert_credential_revision_active
from supportops_core.history_context_services import assert_history_context_authorized
from supportops_core.model_routing_accounting_contracts import RoutingPriceSnapshot, routing_usage_cost
from supportops_core.model_routing_accounting_guards import routing_accounting_blocks_budget
from supportops_core.model_routing_eligibility import _price
from supportops_core.models import (
    AgentVersion, ModelCallLedger, ModelCredential, ModelEndpointVersion, RunAttempt, RunEvent, Tenant,
)
from supportops_core.quota_services import quota_day_start
from supportops_core.release_manifest_services import manifest_digest
from supportops_core.run_attempt_services import RunAttemptIdentity, current_run_identity, owned_run
from supportops_core.services import append_event
from supportops_core.specialist_budget_contracts import (
    SPECIALIST_BUDGET_POLICY, SpecialistBudgetAuthorization, SpecialistBudgetReservation,
)
from supportops_core.specialist_accounting_guards import (
    consumed_model_tokens, specialist_accounting_blocks_budget, specialist_shared_tokens_allow,
)
from supportops_core.specialist_context_contracts import SpecialistKnowledgeContext, SpecialistKnowledgeScope
from supportops_core.specialist_context_services import _utc, _version_ref
from supportops_core.specialist_material_contracts import SpecialistMaterialContext, SpecialistMaterialScope, material_context_digest
from supportops_core.specialist_material_services import prepare_specialist_material
from supportops_core.specialist_request_contracts import SpecialistPreparedRequest
from supportops_core.specialist_task_contracts import SpecialistBudgetBinding, SpecialistTaskInput, contract_digest
from supportops_core.tool_context_services import assert_tool_context_authorized

# 沿用原共享账本策略标识/结构和费用口径；具体类型绑定task/context/完整请求摘要。
POLICY_VERSION = SPECIALIST_BUDGET_POLICY


class SpecialistBudgetDenied(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__("只读子任务当前未获父级共享预算预留")
        self.code = code


class SpecialistBudgetUnconfirmed(RuntimeError):
    code = "SPECIALIST_BUDGET_COMMIT_UNCONFIRMED"

    def __init__(self) -> None:
        super().__init__("子任务预留提交结果暂不可确认，请只读查询原call_id，勿重新预留或派发")


class SpecialistBudgetAuthority(Protocol):
    """独立可信启动批准与完整输入计量；默认缺失，不接受模型或客户端自报。

    必须查询26.1/W19/W23真实批准及当前父子版本/来源/数据处理边界，验证固定schedule
    已通过26.4图校验，且call_id只对应原task的一次物理模型调用。须核对prepare提供的
    原child版本完整系统/消息/输出Schema/生成参数、上下文和固定凭据；本适配器不接受
    工具或供应商扩展，不混入父历史/Prompt或更多资料。计量必须逐项等于原prepared，
    model_input_digest绑定该完整不可变请求，不能以context.payload_bytes代替完整输入。
    必须固定原credential_revision，禁工具、动态委派和隐式重试。此处不取密或调用模型。
    不提交/替换调用者事务，不用候选字段、false声明或摘要自批。真实持久一次派发及用量
    提供者须显式接入；本协议及预留记录均不是派发许可。业务context必须另外核对原
    五分钟观察、工单当前版本/显式人员授权及对应数据处理批准，不继承知识批准。
    """

    async def authorize(self, session: AsyncSession, *, call_id: UUID, task: SpecialistTaskInput,
        schedule_digest: str, context: SpecialistMaterialContext, budget: SpecialistBudgetBinding,
        prepared: SpecialistPreparedRequest,
    ) -> SpecialistBudgetAuthorization: ...


def _microusd(value: float | None) -> int:
    if value is None:
        raise SpecialistBudgetDenied("SPECIALIST_PARENT_COST_LIMIT_REQUIRED")
    amount = Decimal(str(value))
    if not amount.is_finite() or amount <= 0:
        raise SpecialistBudgetDenied("SPECIALIST_COST_LIMIT_INVALID")
    precise = Fraction(amount) * 1_000_000
    return precise.numerator // precise.denominator


def _input_limit(config: AgentConfigV2, defaults: dict[str, object], settings: Settings) -> int:
    limit = config.runtime.max_input_tokens or settings.agent_default_max_input_tokens
    window = defaults.get("context_window_tokens")
    if isinstance(window, int) and not isinstance(window, bool):
        limit = min(limit, window - config.model.generation.max_output_tokens)
    if limit <= 0:
        raise SpecialistBudgetDenied("SPECIALIST_MODEL_CONTEXT_EXHAUSTED")
    return limit


async def project_specialist_shared_budget(
    session: AsyncSession, *, attempt: RunAttemptIdentity, settings: Settings,
    expires_at: datetime | None = None,
) -> SpecialistBudgetBinding:
    """从真实父配置投影同Run总边界，不预留、不清零，也不按子任务复制可用余额。

    ledger_revision=1为此投影算法修订；消费序号由真实ModelCallLedger独立单调维护。
    范围摘要固定父配置和当前平台限制，不使用消费次数作修订，以允许并行原子竞争。
    """
    owned = await owned_run(session, attempt)
    await current_run_identity(session, run=owned.run, conversation=owned.conversation)
    version = await session.scalar(select(AgentVersion).where(AgentVersion.id == owned.run.agent_version_id,
        AgentVersion.tenant_id == attempt.tenant_id).execution_options(populate_existing=True))
    if version is None:
        raise SpecialistBudgetDenied("SPECIALIST_PARENT_VERSION_UNAVAILABLE")
    parent_ref = _version_ref(version)
    config = parse_agent_config(version.config)
    if (version.agent_id != owned.run.agent_id or version.config_digest != owned.run.config_digest
        or agent_config_digest(config) != owned.run.config_digest
        or config.runtime.model_call_limit > settings.agent_model_call_limit_max
        or config.runtime.tool_call_limit > settings.agent_tool_call_limit_max
        or config.runtime.run_timeout_seconds > settings.agent_run_timeout_seconds_max):
        raise SpecialistBudgetDenied("SPECIALIST_PARENT_BUDGET_CHANGED")
    defaults = await session.scalar(select(ModelEndpointVersion.defaults).where(
        ModelEndpointVersion.id == owned.run.model_endpoint_version_id,
        ModelEndpointVersion.tenant_id == attempt.tenant_id))
    if not isinstance(defaults, dict) or parent_ref.model_version_id != owned.run.model_endpoint_version_id:
        raise SpecialistBudgetDenied("SPECIALIST_PARENT_VERSION_UNAVAILABLE")
    first_start = await session.scalar(select(func.min(RunAttempt.started_at)).where(
        RunAttempt.tenant_id == attempt.tenant_id, RunAttempt.run_id == attempt.run_id))
    if first_start is None:
        raise SpecialistBudgetDenied("SPECIALIST_PARENT_BUDGET_UNAVAILABLE")
    deadline = min(_utc(owned.attempt.lease_expires_at),
        _utc(first_start) + timedelta(seconds=config.runtime.run_timeout_seconds))
    if expires_at is not None:
        if expires_at.tzinfo is None:
            raise SpecialistBudgetDenied("SPECIALIST_BUDGET_WINDOW_INVALID")
        deadline = min(deadline, expires_at.astimezone(UTC))
    if datetime.now(UTC) >= deadline:
        raise SpecialistBudgetDenied("AGENT_RUN_TIME_BUDGET_EXCEEDED")
    # 原父逐调用输入/输出上界×调用数，是同Run累计token的保守硬包络，不是精确tokenizer。
    tokens = (_input_limit(config, defaults, settings) + config.model.generation.max_output_tokens) * config.runtime.model_call_limit
    cost = _microusd(config.runtime.max_cost_usd)
    if not 1 <= tokens <= 1_000_000_000 or not 0 <= cost <= 1_000_000_000:
        raise SpecialistBudgetDenied("SPECIALIST_BUDGET_CONTRACT_LIMIT_EXCEEDED")
    scope_digest = manifest_digest({"schema_version": POLICY_VERSION,
        "tenant_id": str(attempt.tenant_id), "run_id": str(attempt.run_id),
        "attempt_id": str(attempt.attempt_id), "generation": attempt.generation,
        "config_digest": version.config_digest, "parent_version": parent_ref.model_dump(mode="json"),
        "model_calls_limit": config.runtime.model_call_limit, "tool_calls_limit": config.runtime.tool_call_limit,
        "tokens_limit": tokens, "cost_microusd_limit": cost,
        "run_timeout_seconds": config.runtime.run_timeout_seconds,
        "tenant_model_call_limit_per_day": settings.tenant_model_call_limit_per_day,
        "tenant_model_cost_usd_per_day": settings.tenant_model_cost_usd_per_day})
    return SpecialistBudgetBinding(parent_run_id=attempt.run_id, ledger_scope_digest=scope_digest,
        ledger_revision=1, model_calls_limit=config.runtime.model_call_limit,
        tool_calls_limit=config.runtime.tool_call_limit, tokens_limit=tokens,
        cost_microusd_limit=cost, deadline=deadline)


async def _authorize(session: AsyncSession, *, authority: SpecialistBudgetAuthority, call_id: UUID,
    task: SpecialistTaskInput, schedule_digest: str, context: SpecialistMaterialContext,
    budget: SpecialistBudgetBinding, prepared: SpecialistPreparedRequest,
) -> SpecialistBudgetAuthorization:
    if ((isinstance(context, SpecialistKnowledgeContext) and task.specialist != "knowledge")
        or (not isinstance(context, SpecialistKnowledgeContext) and task.specialist != context.specialist)):
        raise SpecialistBudgetDenied("SPECIALIST_MATERIAL_TYPE_MISMATCH")
    checked_since = datetime.now(UTC)
    transaction = session.get_transaction()
    loop = asyncio.get_running_loop()
    deadline = loop.time() + min(1, max(0, (task.deadline - checked_since).total_seconds()))
    async with asyncio.timeout_at(deadline):
        result = await authority.authorize(session, call_id=call_id, task=task,
            schedule_digest=schedule_digest, context=context, budget=budget, prepared=prepared)
    now = datetime.now(UTC)
    if (loop.time() >= deadline or transaction is None or session.get_transaction() is not transaction
        or not transaction.is_active or not isinstance(result, SpecialistBudgetAuthorization)):
        raise SpecialistBudgetDenied("SPECIALIST_INPUT_AUTHORIZATION_UNCONFIRMED")
    result = SpecialistBudgetAuthorization.model_validate_json(result.model_dump_json())
    if (result.call_id != call_id or result.task_input_digest != contract_digest(task)
        or result.schedule_digest != schedule_digest or result.context_digest != material_context_digest(context)
        or result.budget_digest != contract_digest(budget)
        or not checked_since <= result.checked_at <= now < result.expires_at <= task.deadline
        or prepared.task_id != task.task_id or prepared.task_input_digest != contract_digest(task)
        or prepared.context_digest != material_context_digest(context)
        or prepared.child_version != task.child_version
        or result.model_input_digest != prepared.model_input_digest
        or result.credential_revision != prepared.credential_revision
        or result.reserved_input_tokens != prepared.reserved_input_tokens
        or result.reserved_output_tokens != prepared.reserved_output_tokens):
        raise SpecialistBudgetDenied("SPECIALIST_INPUT_AUTHORIZATION_UNCONFIRMED")
    return result


async def _call_number(session: AsyncSession, *, attempt: RunAttemptIdentity,
    budget: SpecialistBudgetBinding, authorization: SpecialistBudgetAuthorization, cost: int, settings: Settings,
) -> int:
    if await specialist_accounting_blocks_budget(session, tenant_id=attempt.tenant_id, run_id=attempt.run_id,
        enforce_tenant_cost=settings.tenant_model_cost_usd_per_day is not None):
        raise SpecialistBudgetDenied("SPECIALIST_USAGE_BOUND_EXCEEDED")
    if await routing_accounting_blocks_budget(session, tenant_id=attempt.tenant_id, run_id=attempt.run_id,
        enforce_tenant_cost=settings.tenant_model_cost_usd_per_day is not None):
        raise SpecialistBudgetDenied("ROUTING_USAGE_BOUND_EXCEEDED")
    consumed_cost = func.coalesce(ModelCallLedger.settled_cost_microusd, ModelCallLedger.reserved_cost_microusd)
    count, last, run_cost, unknown, tokens = (await session.execute(select(func.count(),
        func.coalesce(func.max(ModelCallLedger.call_number), 0), func.coalesce(func.sum(consumed_cost), 0),
        func.count().filter(consumed_cost.is_(None)), func.coalesce(func.sum(consumed_model_tokens()), 0)).where(
        ModelCallLedger.tenant_id == attempt.tenant_id, ModelCallLedger.run_id == attempt.run_id))).one()
    if last != count:
        raise SpecialistBudgetDenied("SPECIALIST_BUDGET_HISTORY_INVALID")
    if count >= budget.model_calls_limit:
        raise SpecialistBudgetDenied("AGENT_MODEL_CALL_BUDGET_EXCEEDED")
    if unknown or run_cost + cost > budget.cost_microusd_limit:
        raise SpecialistBudgetDenied("AGENT_COST_BUDGET_EXCEEDED")
    if tokens + authorization.reserved_input_tokens + authorization.reserved_output_tokens > budget.tokens_limit:
        raise SpecialistBudgetDenied("SPECIALIST_SHARED_TOKEN_BUDGET_EXCEEDED")
    if not await specialist_shared_tokens_allow(session, tenant_id=attempt.tenant_id, run_id=attempt.run_id,
        input_tokens=authorization.reserved_input_tokens, output_tokens=authorization.reserved_output_tokens):
        raise SpecialistBudgetDenied("SPECIALIST_SHARED_TOKEN_BUDGET_EXCEEDED")
    day_count, day_cost, day_unknown = (await session.execute(select(func.count(),
        func.coalesce(func.sum(consumed_cost), 0), func.count().filter(consumed_cost.is_(None))).where(
        ModelCallLedger.tenant_id == attempt.tenant_id, ModelCallLedger.created_at >= quota_day_start()))).one()
    if day_count >= settings.tenant_model_call_limit_per_day:
        raise SpecialistBudgetDenied("TENANT_MODEL_CALL_QUOTA_EXCEEDED")
    if settings.tenant_model_cost_usd_per_day is not None and (
        day_unknown or day_cost + cost > _microusd(settings.tenant_model_cost_usd_per_day)):
        raise SpecialistBudgetDenied("TENANT_MODEL_COST_QUOTA_EXCEEDED")
    return int(last) + 1


async def _parent_context(session: AsyncSession, *, attempt: RunAttemptIdentity, settings: Settings) -> None:
    owned = await owned_run(session, attempt)
    identity = await current_run_identity(session, run=owned.run, conversation=owned.conversation)
    if owned.attempt.context_snapshot is None:
        raise SpecialistBudgetDenied("SPECIALIST_PARENT_CONTEXT_UNAVAILABLE")
    snapshot = ContextSnapshot.model_validate(owned.attempt.context_snapshot)
    await assert_history_context_authorized(session, snapshot=snapshot, identity=identity,
        conversation_id=attempt.conversation_id, run=owned.run)
    await assert_tool_context_authorized(session, snapshot=snapshot, identity=identity, run=owned.run, settings=settings)


async def reserve_specialist_knowledge_call(
    session_factory: async_sessionmaker[AsyncSession], *, attempt: RunAttemptIdentity, call_id: UUID,
    task: SpecialistTaskInput, scope: SpecialistKnowledgeScope, schedule_digest: str, settings: Settings,
    authority: SpecialistBudgetAuthority | None = None, redaction_secrets: tuple[str, ...] = (),
) -> SpecialistBudgetReservation:
    """旧知识专用入口不扩展接受范围；新业务类型须使用明确通用入口。"""
    if task.specialist != "knowledge" or not isinstance(scope, SpecialistKnowledgeScope):
        raise SpecialistBudgetDenied("SPECIALIST_MATERIAL_TYPE_MISMATCH")
    return await reserve_specialist_call(session_factory, attempt=attempt, call_id=call_id, task=task,
        scope=scope, schedule_digest=schedule_digest, settings=settings, authority=authority,
        redaction_secrets=redaction_secrets)


async def reserve_specialist_call(
    session_factory: async_sessionmaker[AsyncSession], *, attempt: RunAttemptIdentity, call_id: UUID,
    task: SpecialistTaskInput, scope: SpecialistMaterialScope, schedule_digest: str, settings: Settings,
    authority: SpecialistBudgetAuthority | None = None, redaction_secrets: tuple[str, ...] = (),
) -> SpecialistBudgetReservation:
    """只为原task的一次物理模型请求预留；没有派发/自动重试或预算信封的重复计费。

    所有返回记录execution_permitted=False。原任务/调用出现过即拒绝再次预留，只读原call；
    失败/取消/未知提交不退额。领取、真实请求物化/派发及实际usage结算仍须独立接线。
    """
    if authority is None:
        raise SpecialistBudgetDenied("SPECIALIST_BUDGET_AUTHORITY_UNAVAILABLE")
    if not isinstance(call_id, UUID) or re.fullmatch(r"[0-9a-f]{64}", schedule_digest) is None:
        raise SpecialistBudgetDenied("SPECIALIST_BUDGET_REQUEST_INVALID")
    wrote = False
    try:
        async with asyncio.timeout(10):
            async with session_factory() as session, session.begin():
                if session.bind is None or session.bind.dialect.name != "postgresql":
                    raise SpecialistBudgetDenied("SPECIALIST_POSTGRES_REQUIRED")
                await session.execute(text("SET LOCAL lock_timeout = '250ms'"))
                await session.execute(text("SET LOCAL statement_timeout = '2s'"))
                await owned_run(session, attempt)
                tenant = await session.scalar(select(Tenant).where(Tenant.id == attempt.tenant_id)
                    .execution_options(populate_existing=True).with_for_update(key_share=True))
                if tenant is None or tenant.status != "active":
                    raise SpecialistBudgetDenied("SPECIALIST_AUTHORIZATION_CHANGED")
                prior = await session.scalar(select(RunEvent.id).where(RunEvent.tenant_id == attempt.tenant_id,
                    RunEvent.run_id == attempt.run_id, RunEvent.event_type == "budget.model_reserved",
                    or_(RunEvent.data["call_id"].as_string() == str(call_id),
                        RunEvent.data["specialist_task_id"].as_string() == str(task.task_id))).limit(1))
                if prior is not None or await session.scalar(select(ModelCallLedger.id).where(ModelCallLedger.id == call_id)) is not None:
                    raise SpecialistBudgetDenied("SPECIALIST_TASK_ALREADY_RESERVED")
                await _parent_context(session, attempt=attempt, settings=settings)
                context, prepared = await prepare_specialist_material(session, attempt=attempt, task=task,
                    scope=scope, settings=settings, redaction_secrets=redaction_secrets)
                budget = await project_specialist_shared_budget(session, attempt=attempt, settings=settings,
                    expires_at=task.budget.deadline)
                if budget != task.budget:
                    raise SpecialistBudgetDenied("SPECIALIST_PARENT_BUDGET_CHANGED")
                authorization = await _authorize(session, authority=authority, call_id=call_id,
                    task=task, schedule_digest=schedule_digest, context=context, budget=budget, prepared=prepared)
                model = (await session.execute(select(ModelEndpointVersion.credential_id, ModelEndpointVersion.pricing,
                    ModelEndpointVersion.defaults, ModelEndpointVersion.endpoint_id, ModelCredential.revision)
                    .join(ModelCredential, ModelCredential.id == ModelEndpointVersion.credential_id).where(
                    ModelEndpointVersion.id == task.child_version.model_version_id,
                    ModelEndpointVersion.tenant_id == attempt.tenant_id, ModelCredential.tenant_id == attempt.tenant_id,
                    ModelCredential.endpoint_id == ModelEndpointVersion.endpoint_id)
                    .with_for_update(read=True, of=ModelCredential))).one_or_none()
                child = await session.scalar(select(AgentVersion).where(AgentVersion.id == task.child_version.agent_version_id,
                    AgentVersion.tenant_id == attempt.tenant_id).execution_options(populate_existing=True))
                if model is None or child is None or _version_ref(child) != task.child_version:
                    raise SpecialistBudgetDenied("SPECIALIST_CHILD_VERSION_UNAVAILABLE")
                if not isinstance(model.defaults, dict) or not isinstance(model.pricing, dict):
                    raise SpecialistBudgetDenied("SPECIALIST_MODEL_METADATA_INVALID")
                child_config = parse_agent_config(child.config)
                if (authorization.credential_revision != model.revision
                    or authorization.reserved_input_tokens > _input_limit(child_config, model.defaults, settings)
                    or authorization.reserved_output_tokens != child_config.model.generation.max_output_tokens):
                    raise SpecialistBudgetDenied("SPECIALIST_CALL_BOUND_MISMATCH")
                credential = await assert_credential_revision_active(session, tenant_id=attempt.tenant_id,
                    credential_id=model.credential_id, revision=model.revision)
                if credential.endpoint_id != model.endpoint_id:
                    raise SpecialistBudgetDenied("SPECIALIST_CHILD_CREDENTIAL_CHANGED")
                pricing = model.pricing
                input_rate = _price(pricing.get("input_per_million_tokens"))
                output_rate = _price(pricing.get("output_per_million_tokens"))
                if pricing.get("currency") != "USD" or input_rate is None or output_rate is None:
                    raise SpecialistBudgetDenied("MODEL_PRICING_UNKNOWN")
                price = RoutingPriceSnapshot(call_id=call_id, tenant_id=attempt.tenant_id,
                    actual_model_version_id=task.child_version.model_version_id, candidate_digest=contract_digest(task.child_version),
                    source_pricing_digest=manifest_digest(pricing), source_pricing=pricing,
                    input_per_million_tokens=format(input_rate.copy_abs(), "f"),
                    output_per_million_tokens=format(output_rate.copy_abs(), "f"))
                cost = routing_usage_cost(price, input_tokens=authorization.reserved_input_tokens,
                    output_tokens=authorization.reserved_output_tokens)
                # 独立批准等待后完整重查父来源/知识；不以原摘要或空false字段免除当前授权。
                await _parent_context(session, attempt=attempt, settings=settings)
                current_context, current_prepared = await prepare_specialist_material(session, attempt=attempt, task=task,
                    scope=scope, settings=settings, redaction_secrets=redaction_secrets)
                current_budget = await project_specialist_shared_budget(session, attempt=attempt, settings=settings,
                    expires_at=task.budget.deadline)
                if material_context_digest(current_context) != material_context_digest(context) or current_budget != budget:
                    raise SpecialistBudgetDenied("SPECIALIST_PARENT_CONTEXT_CHANGED")
                if current_prepared != prepared:
                    raise SpecialistBudgetDenied("SPECIALIST_MODEL_REQUEST_CHANGED")
                current = await _authorize(session, authority=authority, call_id=call_id,
                    task=task, schedule_digest=schedule_digest, context=current_context, budget=current_budget, prepared=current_prepared)
                if current.model_dump(exclude={"checked_at", "expires_at"}) != authorization.model_dump(exclude={"checked_at", "expires_at"}):
                    raise SpecialistBudgetDenied("SPECIALIST_INPUT_AUTHORIZATION_CHANGED")
                await _parent_context(session, attempt=attempt, settings=settings)
                final_context, final_prepared = await prepare_specialist_material(session, attempt=attempt, task=task,
                    scope=scope, settings=settings, redaction_secrets=redaction_secrets)
                final_budget = await project_specialist_shared_budget(session, attempt=attempt, settings=settings,
                    expires_at=task.budget.deadline)
                if material_context_digest(final_context) != current.context_digest or final_budget != budget:
                    raise SpecialistBudgetDenied("SPECIALIST_PARENT_CONTEXT_CHANGED")
                if final_prepared != prepared:
                    raise SpecialistBudgetDenied("SPECIALIST_MODEL_REQUEST_CHANGED")
                await assert_credential_revision_active(session, tenant_id=attempt.tenant_id,
                    credential_id=model.credential_id, revision=model.revision)
                call_number = await _call_number(session, attempt=attempt, budget=budget,
                    authorization=current, cost=cost, settings=settings)
                now = datetime.now(UTC)
                expires = min(task.deadline, authorization.expires_at, current.expires_at, budget.deadline)
                if now >= expires:
                    raise SpecialistBudgetDenied("SPECIALIST_BUDGET_EXPIRED")
                record = SpecialistBudgetReservation(call_id=call_id, tenant_id=attempt.tenant_id,
                    conversation_id=attempt.conversation_id, run_id=attempt.run_id, attempt_id=attempt.attempt_id,
                    generation=attempt.generation, task_id=task.task_id, task_input_digest=contract_digest(task),
                    schedule_digest=schedule_digest, context_digest=material_context_digest(context),
                    authorization_scope_digest=task.parent.authorization_scope_digest,
                    authorization_digest=contract_digest(current), governance_decision_digest=current.governance_decision_digest,
                    model_input_digest=current.model_input_digest, parent_version=task.parent.version,
                    child_version=task.child_version, credential_revision=model.revision, budget=budget,
                    price_snapshot=price, call_number=call_number, reserved_input_tokens=current.reserved_input_tokens,
                    reserved_output_tokens=current.reserved_output_tokens, reserved_cost_microusd=cost,
                    recorded_at=now, expires_at=expires)
                session.add(ModelCallLedger(id=call_id, tenant_id=attempt.tenant_id, run_id=attempt.run_id,
                    attempt_id=attempt.attempt_id, model_endpoint_version_id=task.child_version.model_version_id,
                    policy_version=POLICY_VERSION, call_number=call_number, retry_index=0, status="reserved",
                    estimated_input_tokens=record.reserved_input_tokens, reserved_output_tokens=record.reserved_output_tokens,
                    reserved_cost_microusd=cost, omitted_message_count=0))
                wrote = True
                await session.flush()
                await append_event(session, run_id=attempt.run_id, attempt_id=attempt.attempt_id,
                    event_type="budget.model_reserved", data={"call_id": str(call_id), "call_number": call_number,
                        "token_strategy": current.token_strategy, "input_tokens_reserved": record.reserved_input_tokens,
                        "output_tokens_reserved": record.reserved_output_tokens, "cost_reserved_microusd": cost,
                        "omitted_message_count": 0, "specialist_task_id": str(task.task_id),
                        "specialist_reservation": record.model_dump(mode="json"),
                        "specialist_reservation_digest": contract_digest(record)})
                if datetime.now(UTC) >= expires:
                    raise SpecialistBudgetDenied("SPECIALIST_BUDGET_EXPIRED")
            # 独立事务提交成功才返回；提交期间超时/取消/连接错误统一保留未知，不自动重试。
        return record
    except BaseException:
        if wrote:
            raise SpecialistBudgetUnconfirmed() from None
        raise


async def read_specialist_budget_reservation(session: AsyncSession, *, tenant_id: UUID,
    run_id: UUID, call_id: UUID,
) -> SpecialistBudgetReservation | None:
    """内部调用者先完成读取授权；只查原预留，到期/失去租约不恢复派发许可。"""
    events = (await session.scalars(select(RunEvent).where(RunEvent.tenant_id == tenant_id,
        RunEvent.run_id == run_id, RunEvent.event_type == "budget.model_reserved",
        RunEvent.data["call_id"].as_string() == str(call_id)).limit(2))).all()
    ledger = await session.scalar(select(ModelCallLedger).where(ModelCallLedger.id == call_id,
        ModelCallLedger.tenant_id == tenant_id, ModelCallLedger.run_id == run_id))
    if not events and ledger is None:
        return None
    specialist_events = any("specialist_reservation" in event.data or "specialist_task_id" in event.data for event in events)
    if not specialist_events and (ledger is None or ledger.policy_version != POLICY_VERSION):
        return None
    try:
        if len(events) != 1 or ledger is None or ledger.policy_version != POLICY_VERSION:
            raise ValueError()
        payload = events[0].data
        record = SpecialistBudgetReservation.model_validate_json(json.dumps(payload["specialist_reservation"],
            ensure_ascii=False, sort_keys=True, allow_nan=False))
        if (payload.get("specialist_reservation_digest") != contract_digest(record)
            or record.call_id != call_id or record.tenant_id != tenant_id or record.run_id != run_id
            or events[0].attempt_id != record.attempt_id or ledger.attempt_id != record.attempt_id
            or ledger.call_number != record.call_number or ledger.retry_index != 0
            or ledger.model_endpoint_version_id != record.child_version.model_version_id
            or ledger.token_strategy != "utf8_upper_bound_v1"
            or ledger.estimated_input_tokens != record.reserved_input_tokens
            or ledger.reserved_output_tokens != record.reserved_output_tokens
            or ledger.reserved_cost_microusd != record.reserved_cost_microusd
            or ledger.omitted_message_count != 0
            or manifest_digest(record.price_snapshot.source_pricing) != record.price_snapshot.source_pricing_digest
            or payload.get("specialist_task_id") != str(record.task_id)
            or payload.get("call_number") != record.call_number
            or payload.get("token_strategy") != ledger.token_strategy
            or payload.get("input_tokens_reserved") != record.reserved_input_tokens
            or payload.get("output_tokens_reserved") != record.reserved_output_tokens
            or payload.get("cost_reserved_microusd") != record.reserved_cost_microusd
            or payload.get("omitted_message_count") != 0):
            raise ValueError()
        return record
    except (KeyError, TypeError, ValueError, ValidationError):
        raise SpecialistBudgetDenied("SPECIALIST_BUDGET_HISTORY_INVALID") from None
