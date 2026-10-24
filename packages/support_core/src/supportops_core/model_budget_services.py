"""逐物理调用预留；恢复共享 Run/租户账本，未知 usage 保留消费。"""

from __future__ import annotations

import asyncio
import math
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, TypedDict
from uuid import UUID

import httpx
from langchain.agents.middleware import ModelRequest, ModelResponse
from langchain_core.messages import BaseMessage
from openai import APIConnectionError, APIStatusError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from supportops_core.agent_config import SupportAnswer, SupportContext
from supportops_core.agent_runtime import AgentRuntimeSnapshot
from supportops_core.config import Settings
from supportops_core.context_services import ContextBudgetExceeded, ContextSnapshot, fit_model_context
from supportops_core.history_context_services import assert_history_context_authorized
from supportops_core.tool_context_services import assert_tool_context_authorized
from supportops_core.knowledge_retrieval import assert_attempt_knowledge_authorized
from supportops_core.model_context_boundary import record_model_input_context
from supportops_core.models import AgentRun, ModelCallLedger, RunAttempt, Tenant
from supportops_core.model_routing_accounting_guards import routing_accounting_blocks_budget
from supportops_core.specialist_accounting_guards import specialist_accounting_blocks_budget, specialist_shared_tokens_allow
from supportops_core.quota_services import quota_day_start
from supportops_core.run_attempt_services import RunAttemptIdentity, current_run_identity, lock_run_scope, owned_run
from supportops_core.services import append_event


class ModelBudgetExceeded(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class ModelCallPermit:
    id: UUID
    input_price: float | None
    output_price: float | None


def _price(value: object) -> float | None:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value) and value >= 0 else None


def _usage(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


class ModelBudgetGateway:
    """完整输入门禁、预算许可和有限依赖重试，不把 SDK 隐式重试漏记。"""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession], *, snapshot: AgentRuntimeSnapshot,
                 identity: RunAttemptIdentity, settings: Settings) -> None:
        self.session_factory, self.snapshot, self.identity, self.settings = session_factory, snapshot, identity, settings

    def _input_limit(self) -> int:
        runtime = self.snapshot.config.runtime
        limit = runtime.max_input_tokens or self.settings.agent_default_max_input_tokens
        window = self.snapshot.model_version.defaults.get("context_window_tokens")
        if isinstance(window, int):
            limit = min(limit, window - self.snapshot.config.model.generation.max_output_tokens)
        if limit <= 0:
            raise ContextBudgetExceeded("模型输出预留已占满上下文窗口")
        return limit

    async def _reserve(self, *, input_tokens: int, omitted: int, retry_index: int, selected_source_ids: set[str],
        tool_messages: list[BaseMessage],
    ) -> ModelCallPermit:
        pricing = self.snapshot.model_version.pricing
        input_price, output_price = _price(pricing.get("input_per_million_tokens")), _price(pricing.get("output_per_million_tokens"))
        if pricing.get("currency", "USD") != "USD":
            input_price, output_price = None, None
        output = self.snapshot.config.model.generation.max_output_tokens
        cost = math.ceil(input_tokens * input_price + output * output_price) if input_price is not None and output_price is not None else None
        run_cost_limit = self.snapshot.config.runtime.max_cost_usd
        tenant_cost_limit = self.settings.tenant_model_cost_usd_per_day
        if cost is None and (run_cost_limit is not None or tenant_cost_limit is not None):
            raise ModelBudgetExceeded("MODEL_PRICING_UNKNOWN", "费用硬预算需要可信 USD 定价，未知定价不能按零处理")
        async with self.session_factory() as session, session.begin():
            owned = await owned_run(session, self.identity)
            current_identity = await current_run_identity(session, run=owned.run, conversation=owned.conversation)
            await record_model_input_context(session, owned=owned, identity=current_identity,
                attempt=self.identity, settings=self.settings, messages=tool_messages,
                selected_source_ids=selected_source_ids, fallback_snapshot=self.snapshot.context_snapshot)
            await session.scalar(select(Tenant).where(Tenant.id == self.identity.tenant_id).with_for_update(key_share=True))
            if not await specialist_shared_tokens_allow(session, tenant_id=self.identity.tenant_id,
                run_id=self.identity.run_id, input_tokens=input_tokens, output_tokens=output):
                raise ModelBudgetExceeded("SPECIALIST_SHARED_TOKEN_BUDGET_EXCEEDED", "累计模型消费和本次预留超出原父级共享Token上界")
            if await specialist_accounting_blocks_budget(session, tenant_id=self.identity.tenant_id,
                run_id=self.identity.run_id, enforce_tenant_cost=tenant_cost_limit is not None):
                raise ModelBudgetExceeded("SPECIALIST_USAGE_BOUND_EXCEEDED", "子任务实际消费超出原预留，停止后续模型调用")
            if await routing_accounting_blocks_budget(session, tenant_id=self.identity.tenant_id,
                run_id=self.identity.run_id, enforce_tenant_cost=tenant_cost_limit is not None):
                raise ModelBudgetExceeded("ROUTING_USAGE_BOUND_EXCEEDED", "原路由消费超出预留或费用范围，停止后续模型调用")
            first_start = await session.scalar(select(func.min(RunAttempt.started_at)).where(
                RunAttempt.run_id == self.identity.run_id, RunAttempt.tenant_id == self.identity.tenant_id,
            ))
            if first_start is not None:
                if first_start.tzinfo is None:
                    first_start = first_start.replace(tzinfo=UTC)
                if (datetime.now(UTC) - first_start).total_seconds() >= self.snapshot.config.runtime.run_timeout_seconds:
                    raise ModelBudgetExceeded("AGENT_RUN_TIME_BUDGET_EXCEEDED", "累计执行时长预算已耗尽")
            consumed = func.coalesce(ModelCallLedger.settled_cost_microusd, ModelCallLedger.reserved_cost_microusd)
            count, run_cost, unknown = (await session.execute(select(func.count(), func.coalesce(func.sum(consumed), 0),
                func.count().filter(consumed.is_(None))).where(ModelCallLedger.run_id == self.identity.run_id,
                    ModelCallLedger.tenant_id == self.identity.tenant_id))).one()
            if count >= self.snapshot.config.runtime.model_call_limit:
                raise ModelBudgetExceeded("AGENT_MODEL_CALL_BUDGET_EXCEEDED", "整个 Run 的模型调用额度已耗尽，恢复不会清零")
            if run_cost_limit is not None and (unknown or run_cost + (cost or 0) > math.floor(run_cost_limit * 1_000_000)):
                raise ModelBudgetExceeded("AGENT_COST_BUDGET_EXCEEDED", "累计费用和本次预留超过 Run 预算")
            day_count, day_cost, day_unknown = (await session.execute(select(func.count(), func.coalesce(func.sum(consumed), 0),
                func.count().filter(consumed.is_(None))).where(ModelCallLedger.tenant_id == self.identity.tenant_id,
                    ModelCallLedger.created_at >= quota_day_start()))).one()
            if day_count >= self.settings.tenant_model_call_limit_per_day:
                raise ModelBudgetExceeded("TENANT_MODEL_CALL_QUOTA_EXCEEDED", "租户 UTC 每日模型调用额度已耗尽")
            if tenant_cost_limit is not None and (day_unknown or day_cost + (cost or 0) > math.floor(tenant_cost_limit * 1_000_000)):
                raise ModelBudgetExceeded("TENANT_MODEL_COST_QUOTA_EXCEEDED", "租户累计费用与预留超过每日预算")
            ledger = ModelCallLedger(tenant_id=self.identity.tenant_id, run_id=self.identity.run_id,
                attempt_id=self.identity.attempt_id, call_number=count + 1, retry_index=retry_index, status="reserved",
                model_endpoint_version_id=self.snapshot.model_version.id,
                estimated_input_tokens=input_tokens, reserved_output_tokens=output,
                reserved_cost_microusd=cost, omitted_message_count=omitted)
            session.add(ledger)
            await session.flush()
            await append_event(session, run_id=self.identity.run_id, attempt_id=self.identity.attempt_id,
                event_type="budget.model_reserved", data={"call_id": str(ledger.id), "call_number": ledger.call_number,
                    "token_strategy": ledger.token_strategy, "input_tokens_reserved": input_tokens,
                    "output_tokens_reserved": output, "cost_reserved_microusd": cost, "omitted_message_count": omitted})
            return ModelCallPermit(ledger.id, input_price, output_price)

    async def _settle(self, permit: ModelCallPermit, *, response: ModelResponse[SupportAnswer] | None, error_code: str | None = None) -> None:
        # 允许已失效的尝试补计实际费用，但不能借此修改产品结果/当前许可。
        async with self.session_factory() as session, session.begin():
            await lock_run_scope(session, self.identity.run_id, tenant_id=self.identity.tenant_id)
            await session.scalar(select(Tenant).where(Tenant.id == self.identity.tenant_id).with_for_update(key_share=True))
            ledger = await session.scalar(select(ModelCallLedger).where(
                ModelCallLedger.id == permit.id, ModelCallLedger.tenant_id == self.identity.tenant_id,
                ModelCallLedger.attempt_id == self.identity.attempt_id,
            ).with_for_update())
            if ledger is None or ledger.status == "settled" or (ledger.status == "unknown" and response is None):
                return
            usage = [getattr(message, "usage_metadata", None) for message in response.result
                     if getattr(message, "type", None) == "ai"] if response is not None else []
            complete = bool(usage) and all(isinstance(item, dict) and _usage(item.get("input_tokens")) is not None
                and _usage(item.get("output_tokens")) is not None for item in usage)
            if complete:
                ledger.input_tokens = sum(item["input_tokens"] for item in usage)
                ledger.output_tokens = sum(item["output_tokens"] for item in usage)
                cached = [(item.get("input_token_details") or {}).get("cache_read") for item in usage]
                reasoning = [(item.get("output_token_details") or {}).get("reasoning") for item in usage]
                ledger.cached_input_tokens = sum(cached) if all(_usage(value) is not None for value in cached) else None
                ledger.reasoning_tokens = sum(reasoning) if all(_usage(value) is not None for value in reasoning) else None
                if permit.input_price is not None and permit.output_price is not None:
                    ledger.settled_cost_microusd = math.ceil(ledger.input_tokens * permit.input_price + ledger.output_tokens * permit.output_price)
                ledger.status = "settled"
            else:
                ledger.status = "unknown"
                ledger.error_code = error_code or "MODEL_USAGE_UNKNOWN"
            ledger.finished_at = datetime.now(UTC)

    async def execute(self, request: ModelRequest[SupportContext],
                      handler: Callable[[ModelRequest[SupportContext]], Awaitable[ModelResponse[SupportAnswer]]]) -> ModelResponse[SupportAnswer]:
        selected_source_ids: set[str] = set()
        planned, input_tokens, omitted = fit_model_context(request, input_limit=self._input_limit(),
            history_excerpts=self.snapshot.context_snapshot.history_excerpts if self.snapshot.context_snapshot else None,
            on_selected_sources=selected_source_ids.update)
        for retry in range(self.snapshot.config.model.generation.max_retries + 1):
            permit = await self._reserve(input_tokens=input_tokens, omitted=omitted, retry_index=retry,
                selected_source_ids=selected_source_ids, tool_messages=planned.messages)
            try:
                response = await handler(planned)
            except BaseException as exc:
                await self._settle(permit, response=None, error_code="MODEL_CALL_CANCELLED" if isinstance(exc, asyncio.CancelledError) else "MODEL_CALL_FAILED_USAGE_UNKNOWN")
                retryable = isinstance(exc, (APIConnectionError, httpx.NetworkError, httpx.TimeoutException)) or (
                    isinstance(exc, APIStatusError) and (exc.status_code in {408, 409, 429} or exc.status_code >= 500))
                if not retryable or retry >= self.snapshot.config.model.generation.max_retries:
                    raise
                await asyncio.sleep(min(2, 0.25 * 2 ** retry))
                continue
            await self._settle(permit, response=response)
            async with self.session_factory() as session, session.begin():
                owned = await owned_run(session, self.identity)
                current_identity = await current_run_identity(session, run=owned.run, conversation=owned.conversation)
                history_snapshot = ContextSnapshot.model_validate(owned.attempt.context_snapshot) if owned.attempt.context_snapshot else self.snapshot.context_snapshot
                await assert_history_context_authorized(session, snapshot=history_snapshot, identity=current_identity,
                    conversation_id=owned.conversation.id, run=owned.run)
                await assert_attempt_knowledge_authorized(session, identity=current_identity, attempt=self.identity)
                await assert_tool_context_authorized(session, snapshot=history_snapshot, identity=current_identity,
                    run=owned.run, settings=self.settings)
            return response
        raise RuntimeError("模型调用循环未产生结果")


class ModelBudgetSummary(TypedDict):
    model_call_count: int
    retry_count: int
    unknown_usage_call_count: int
    input_tokens: int | None
    output_tokens: int | None
    cached_input_tokens: int | None
    reasoning_tokens: int | None
    total_cost_microusd: int | None
    cost_source: Literal["calculated", "unknown"]


async def assert_current_cost_budget(session: AsyncSession, *, tenant_id: UUID, run_id: UUID,
                                     run_limit_usd: float | None, tenant_limit_usd: float | None) -> None:
    """本地工具没有外部费用时仍复查累计预算，不绕过未知费用门禁。"""
    if await specialist_accounting_blocks_budget(session, tenant_id=tenant_id, run_id=run_id,
        enforce_tenant_cost=tenant_limit_usd is not None):
        raise ModelBudgetExceeded("SPECIALIST_USAGE_BOUND_EXCEEDED", "子任务实际消费超出原预留，停止后续工具调用")
    if await routing_accounting_blocks_budget(session, tenant_id=tenant_id, run_id=run_id,
        enforce_tenant_cost=tenant_limit_usd is not None):
        raise ModelBudgetExceeded("ROUTING_USAGE_BOUND_EXCEEDED", "路由实际用量超出原预留，停止后续工具调用")
    consumed = func.coalesce(ModelCallLedger.settled_cost_microusd, ModelCallLedger.reserved_cost_microusd)
    for limit, filters, code in (
        (run_limit_usd, [ModelCallLedger.tenant_id == tenant_id, ModelCallLedger.run_id == run_id], "AGENT_COST_BUDGET_EXCEEDED"),
        (tenant_limit_usd, [ModelCallLedger.tenant_id == tenant_id, ModelCallLedger.created_at >= quota_day_start()], "TENANT_MODEL_COST_QUOTA_EXCEEDED"),
    ):
        if limit is None:
            continue
        cost, unknown = (await session.execute(select(func.coalesce(func.sum(consumed), 0),
            func.count().filter(consumed.is_(None))).where(*filters))).one()
        if unknown or cost > math.floor(limit * 1_000_000):
            raise ModelBudgetExceeded(code, "累计消费/预留已超预算或存在未知费用，停止后续工具调用")


async def model_budget_summary(session: AsyncSession, *, tenant_id: UUID, run_id: UUID, attempt_id: UUID | None = None) -> ModelBudgetSummary:
    """汇总整个 Run 的物理调用；缺失任一 usage 不把部分合计伪称完整用量。"""
    filters = [ModelCallLedger.tenant_id == tenant_id, ModelCallLedger.run_id == run_id]
    if attempt_id is not None:
        filters.append(ModelCallLedger.attempt_id == attempt_id)
    rows = (await session.scalars(select(ModelCallLedger).where(*filters).order_by(ModelCallLedger.call_number))).all()
    complete_usage = bool(rows) and all(row.status == "settled" and row.input_tokens is not None and row.output_tokens is not None for row in rows)
    complete_cost = bool(rows) and all(row.settled_cost_microusd is not None for row in rows)
    return {"model_call_count": len(rows), "retry_count": sum(row.retry_index > 0 for row in rows),
        "unknown_usage_call_count": sum(row.status != "settled" for row in rows),
        "input_tokens": sum(row.input_tokens or 0 for row in rows) if complete_usage else None,
        "output_tokens": sum(row.output_tokens or 0 for row in rows) if complete_usage else None,
        "cached_input_tokens": sum(row.cached_input_tokens or 0 for row in rows) if rows and all(row.cached_input_tokens is not None for row in rows) else None,
        "reasoning_tokens": sum(row.reasoning_tokens or 0 for row in rows) if rows and all(row.reasoning_tokens is not None for row in rows) else None,
        "total_cost_microusd": sum(row.settled_cost_microusd or 0 for row in rows) if complete_cost else None,
        "cost_source": "calculated" if complete_cost else "unknown"}


def apply_model_budget_summary(run: AgentRun, budget: ModelBudgetSummary) -> None:
    """成功、失败与取消共用原物理账本口径，不按当前模型价格重新计费。"""
    run.model_call_count, run.retry_count = budget["model_call_count"], budget["retry_count"]
    run.input_tokens, run.output_tokens = budget["input_tokens"], budget["output_tokens"]
    run.cached_input_tokens, run.reasoning_tokens = budget["cached_input_tokens"], budget["reasoning_tokens"]
    run.total_cost_microusd, run.cost_source = budget["total_cost_microusd"], budget["cost_source"]
