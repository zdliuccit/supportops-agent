"""真实 PostgreSQL 的模型预留、结算、重试与恢复；模型响应显式替身。"""

import asyncio
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import timedelta
from uuid import uuid4

import httpx
import pytest
from langchain.agents.middleware import ModelRequest, ModelResponse
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from openai import APIStatusError
from sqlalchemy import select
from supportops_core import model_budget_services, worker_runtime
from supportops_core.agent_config import SupportAnswer
from supportops_core.agent_runtime import AgentRuntimeSnapshot
from supportops_core.context_services import ContextBudgetExceeded
from supportops_core.db import create_session_factory
from supportops_core.enums import RunStatus
from supportops_core.model_budget_services import (
    ModelBudgetExceeded,
    ModelBudgetGateway,
    model_budget_summary,
)
from supportops_core.models import (
    AgentRun,
    Conversation,
    ModelCallLedger,
    RunAttempt,
    RunEvent,
    utc_now,
)
from supportops_core.run_attempt_services import (
    RunClaim,
    RunLeaseLostError,
    claim_run_attempt,
    recover_expired_attempts,
)
from supportops_core.services import create_message_and_run

from tests.postgres.test_run_fencing import PostgresWorkers
from tests.postgres.test_run_fencing import workers as workers
from tests.worker.test_worker import seed_run

pytestmark = pytest.mark.skipif(
    os.getenv("SUPPORTOPS_RUN_POSTGRES_TESTS") != "1",
    reason="需显式启用隔离 PostgreSQL 测试库",
)


@dataclass(frozen=True)
class BudgetContext:
    workers: PostgresWorkers
    snapshot: AgentRuntimeSnapshot
    claim: RunClaim

    def gateway(self, index=0, **settings):
        return ModelBudgetGateway(
            create_session_factory(self.workers.engines[index % 2]),
            snapshot=self.snapshot,
            identity=self.claim.identity,
            settings=self.workers.settings.model_copy(update=settings),
        )

    def request(self):
        assert self.snapshot.context_snapshot is not None
        return ModelRequest(
            model=FakeListChatModel(responses=["禁止实际调用"]),
            messages=[
                HumanMessage(
                    content=self.snapshot.input_text,
                    id=str(self.snapshot.context_snapshot.current_input_id),
                )
            ],
            system_prompt="隔离测试系统指令",
            tools=[],
        )


async def load_context(workers, run_id, owner):
    factory = create_session_factory(workers.engines[0])
    async with factory() as session, session.begin():
        claim = await claim_run_attempt(session, run_id, owner_id=owner, lease_seconds=90)
        assert claim is not None
    async with factory() as session, session.begin():
        snapshot = await worker_runtime._load_snapshot(
            session,
            run_id=run_id,
            settings=workers.settings,
            attempt_identity=claim.identity,
            audit_session_factory=factory,
        )
    return BudgetContext(workers, snapshot, claim)


@pytest.fixture
async def budget_context(workers, request) -> AsyncIterator[BudgetContext]:
    options = getattr(request, "param", {})
    run_id = await seed_run(
        workers.engines[0],
        workers.settings,
        runtime_options=options.get("runtime"),
        generation_options=options.get("generation"),
        model_pricing=options.get(
            "pricing",
            {"currency": "USD", "input_per_million_tokens": 1, "output_per_million_tokens": 2},
        ),
    )
    context = await load_context(workers, run_id, "budget-owner")
    try:
        yield context
    finally:
        await worker_runtime._cancel_owned_attempt(
            create_session_factory(workers.engines[0]),
            context.claim.identity,
            reason="isolated_budget_test_finished",
        )


async def reserve(context, gateway=None, tokens=100):
    request = context.request()
    return await (gateway or context.gateway())._reserve(
        input_tokens=tokens,
        omitted=0,
        retry_index=0,
        selected_source_ids={request.messages[0].id},
        tool_messages=request.messages,
    )


def response(*, usage=True, inputs=10, outputs=5):
    metadata = (
        {
            "input_tokens": inputs,
            "output_tokens": outputs,
            "total_tokens": inputs + outputs,
            "input_token_details": {"cache_read": 3},
            "output_token_details": {"reasoning": 2},
        }
        if usage
        else None
    )
    return ModelResponse(
        result=[AIMessage(content="隔离回答", usage_metadata=metadata)],
        structured_response=SupportAnswer(answer="隔离回答"),
    )


async def rows(context):
    async with create_session_factory(context.workers.engines[1])() as session:
        return (
            await session.scalars(
                select(ModelCallLedger)
                .where(
                    ModelCallLedger.run_id == context.claim.identity.run_id,
                )
                .order_by(ModelCallLedger.call_number)
            )
        ).all()


async def summary(context, attempt_id=None):
    async with create_session_factory(context.workers.engines[1])() as session:
        return await model_budget_summary(
            session,
            tenant_id=context.claim.identity.tenant_id,
            run_id=context.claim.identity.run_id,
            attempt_id=attempt_id,
        )


async def test_known_usage_settles_once_with_reserved_price_and_inclusive_details(budget_context):
    context = budget_context
    gateway = context.gateway()
    permit = await reserve(context)
    original = (await rows(context))[0]
    assert original.status == "reserved" and original.reserved_cost_microusd == 8292
    # 只变更脱离会话的内存对象，验证结算使用原permit价格，不改历史模型版本。
    context.snapshot.model_version.pricing = {
        "input_per_million_tokens": 999,
        "output_per_million_tokens": 999,
    }
    await gateway._settle(permit, response=response())
    await context.gateway(1)._settle(permit, response=response(inputs=999, outputs=999))
    ledger = (await rows(context))[0]
    assert ledger.status == "settled" and ledger.settled_cost_microusd == 20
    assert (ledger.input_tokens, ledger.output_tokens) == (10, 5)
    assert (ledger.cached_input_tokens, ledger.reasoning_tokens) == (3, 2)
    result = await summary(context)
    assert result["total_cost_microusd"] == 20 and result["cost_source"] == "calculated"
    assert result["model_call_count"] == 1 and result["unknown_usage_call_count"] == 0


@pytest.mark.parametrize("usage_kind", ["missing", "partial", "boolean", "negative"])
async def test_unknown_or_invalid_usage_keeps_full_reservation(budget_context, usage_kind):
    context = budget_context
    gateway = context.gateway()
    permit = await reserve(context)
    model_response = response(usage=False)
    if usage_kind != "missing":
        usage = {"input_tokens": 10, "output_tokens": 5}
        if usage_kind == "partial":
            del usage["output_tokens"]
        elif usage_kind == "boolean":
            usage["input_tokens"] = True
        else:
            usage["output_tokens"] = -1
        # 模拟 SDK 边界的损坏usage；不通过宽松fixture把无效类型规范化。
        model_response.result[0] = model_response.result[0].model_copy(
            update={"usage_metadata": usage}
        )
    await gateway._settle(permit, response=model_response)
    ledger = (await rows(context))[0]
    assert ledger.status == "unknown" and ledger.reserved_cost_microusd == 8292
    assert ledger.settled_cost_microusd is None and ledger.error_code == "MODEL_USAGE_UNKNOWN"
    result = await summary(context)
    assert (
        result["input_tokens"] is result["output_tokens"] is result["total_cost_microusd"] is None
    )
    assert result["unknown_usage_call_count"] == 1
    await gateway._settle(permit, response=response())
    assert (await summary(context))["total_cost_microusd"] == 20


@pytest.mark.parametrize("status", [429, 503, "network"])
async def test_each_retry_is_a_distinct_physical_ledger_and_unknown_is_not_zero(
    budget_context, status
):
    context = budget_context
    seen = 0

    async def handler(request):
        nonlocal seen
        seen += 1
        if seen < 3:
            if status == "network":
                raise httpx.ConnectError("isolated connection failure")
            raw = httpx.Response(
                status, request=httpx.Request("POST", "https://model.example.invalid")
            )
            raise APIStatusError("isolated provider error", response=raw, body=None)
        return response()

    assert await context.gateway().execute(context.request(), handler)
    records = await rows(context)
    assert seen == 3 and [item.call_number for item in records] == [1, 2, 3]
    assert [item.retry_index for item in records] == [0, 1, 2]
    assert [item.status for item in records] == ["unknown", "unknown", "settled"]
    assert records[0].reserved_cost_microusd == records[1].reserved_cost_microusd > 0
    assert all(item.estimated_input_tokens == records[0].estimated_input_tokens for item in records)
    result = await summary(context)
    assert result["retry_count"] == result["unknown_usage_call_count"] == 2
    assert result["input_tokens"] is result["total_cost_microusd"] is None


@pytest.mark.parametrize("failure", ["bad_request", "schema", "cancelled"])
async def test_nonretryable_failure_or_cancel_preserves_consumption(budget_context, failure):
    context = budget_context
    calls = 0

    async def handler(request):
        nonlocal calls
        calls += 1
        if failure == "bad_request":
            raw = httpx.Response(
                400, request=httpx.Request("POST", "https://model.example.invalid")
            )
            raise APIStatusError("isolated rejected request", response=raw, body=None)
        if failure == "cancelled":
            raise asyncio.CancelledError()
        raise ValueError("isolated structured result error")

    with pytest.raises(
        {"bad_request": APIStatusError, "schema": ValueError, "cancelled": asyncio.CancelledError}[
            failure
        ]
    ):
        await context.gateway().execute(context.request(), handler)
    records = await rows(context)
    assert calls == len(records) == 1 and records[0].status == "unknown"
    assert records[0].reserved_cost_microusd > 0
    assert records[0].error_code == (
        "MODEL_CALL_CANCELLED" if failure == "cancelled" else "MODEL_CALL_FAILED_USAGE_UNKNOWN"
    )


@pytest.mark.parametrize("budget_context", [{"runtime": {"model_call_limit": 2}}], indirect=True)
async def test_retry_stops_at_cumulative_run_call_limit(budget_context):
    calls = 0

    async def handler(request):
        nonlocal calls
        calls += 1
        raise httpx.ConnectError("isolated retryable failure")

    with pytest.raises(ModelBudgetExceeded) as error:
        await budget_context.gateway().execute(budget_context.request(), handler)
    assert error.value.code == "AGENT_MODEL_CALL_BUDGET_EXCEEDED"
    assert calls == len(await rows(budget_context)) == 2


@pytest.mark.parametrize("budget_context", [{"generation": {"max_retries": 1}}], indirect=True)
async def test_retry_count_exhaustion_keeps_all_unknown_reservations(budget_context):
    calls = 0

    async def handler(request):
        nonlocal calls
        calls += 1
        raise httpx.ConnectError("isolated repeated connection failure")

    with pytest.raises(httpx.ConnectError):
        await budget_context.gateway().execute(budget_context.request(), handler)
    records = await rows(budget_context)
    assert calls == len(records) == 2
    assert [item.retry_index for item in records] == [0, 1]
    assert all(item.status == "unknown" and item.reserved_cost_microusd > 0 for item in records)
    assert (await summary(budget_context))["model_call_count"] == 2


@pytest.mark.parametrize("budget_context", [{"runtime": {"model_call_limit": 1}}], indirect=True)
async def test_six_independent_gateways_compete_for_one_run_reservation(budget_context):
    context = budget_context
    start = asyncio.Event()

    async def compete(index):
        await start.wait()
        return await reserve(context, context.gateway(index))

    tasks = [asyncio.create_task(compete(index)) for index in range(6)]
    start.set()
    results = await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=8)
    failures = [result for result in results if isinstance(result, ModelBudgetExceeded)]
    assert len(failures) == 5 and all(
        item.code == "AGENT_MODEL_CALL_BUDGET_EXCEEDED" for item in failures
    )
    assert len(await rows(context)) == 1


async def another_conversation(context):
    factory = create_session_factory(context.workers.engines[1])
    async with factory() as session, session.begin():
        original = await session.get(Conversation, context.claim.identity.conversation_id)
        conversation = Conversation(
            tenant_id=original.tenant_id,
            user_id=original.user_id,
            agent_id=original.agent_id,
            agent_version_id=original.agent_version_id,
        )
        session.add(conversation)
        await session.flush()
        accepted = await create_message_and_run(
            session,
            conversation=conversation,
            user_id=original.user_id,
            identity_roles=context.snapshot.context.roles,
            content="独立会话",
            idempotency_key=str(uuid4()),
            correlation_id="budget-competition",
        )
    return await load_context(context.workers, accepted.run.id, "other-budget-owner")


@pytest.mark.parametrize(
    "kind,expected",
    [
        ("calls", "TENANT_MODEL_CALL_QUOTA_EXCEEDED"),
        ("cost", "TENANT_MODEL_COST_QUOTA_EXCEEDED"),
    ],
)
async def test_different_conversations_cannot_overspend_tenant_last_quota(
    budget_context, kind, expected
):
    context = budget_context
    other = await another_conversation(context)
    settings = (
        {"tenant_model_call_limit_per_day": 1}
        if kind == "calls"
        else {"tenant_model_cost_usd_per_day": 0.008292}
    )
    try:
        results = await asyncio.wait_for(
            asyncio.gather(
                reserve(context, context.gateway(0, **settings)),
                reserve(other, other.gateway(1, **settings)),
                return_exceptions=True,
            ),
            timeout=8,
        )
        errors = [value for value in results if isinstance(value, ModelBudgetExceeded)]
        assert len(errors) == 1 and errors[0].code == expected
        assert len(await rows(context)) + len(await rows(other)) == 1
    finally:
        await worker_runtime._cancel_owned_attempt(
            create_session_factory(context.workers.engines[1]),
            other.claim.identity,
            reason="isolated_other_budget_finished",
        )


@pytest.mark.parametrize("budget_context", [{"runtime": {"max_cost_usd": 0.008292}}], indirect=True)
async def test_settled_usage_releases_only_unused_cost_not_call_count(budget_context):
    context = budget_context
    gateway = context.gateway()
    permit = await reserve(context)
    with pytest.raises(ModelBudgetExceeded, match="累计费用"):
        await reserve(context)
    await gateway._settle(permit, response=response())
    await reserve(context, tokens=80)
    assert len(await rows(context)) == 2
    assert (await summary(context))["total_cost_microusd"] is None


@pytest.mark.parametrize("hard_budget", ["run", "tenant"])
@pytest.mark.parametrize("budget_context", [{"pricing": {}}], indirect=True)
async def test_unknown_pricing_never_passes_hard_cost_gate(budget_context, hard_budget):
    context = budget_context
    if hard_budget == "run":
        # 与可信配置语义一致的内存快照，仅用于验证预算前置拒绝。
        from dataclasses import replace

        runtime = context.snapshot.config.runtime.model_copy(update={"max_cost_usd": 1})
        config = context.snapshot.config.model_copy(update={"runtime": runtime})
        context = replace(context, snapshot=replace(context.snapshot, config=config))
    gateway = context.gateway(
        **({"tenant_model_cost_usd_per_day": 1} if hard_budget == "tenant" else {})
    )
    with pytest.raises(ModelBudgetExceeded) as error:
        await reserve(context, gateway)
    assert error.value.code == "MODEL_PRICING_UNKNOWN" and not await rows(context)


@pytest.mark.parametrize(
    "budget_context",
    [{"runtime": {"model_call_limit": 1}}, {"runtime": {"model_call_limit": 2}}],
    indirect=True,
)
async def test_recovery_keeps_old_reservation_and_late_usage_cannot_restore_lease(budget_context):
    context = budget_context
    permit = await reserve(context)
    factory = create_session_factory(context.workers.engines[0])
    async with factory() as session, session.begin():
        attempt = await session.get(RunAttempt, context.claim.identity.attempt_id)
        attempt.lease_expires_at = utc_now() - timedelta(seconds=1)
    assert await recover_expired_attempts(factory, settings=context.workers.settings, limit=10) == 1
    new = await load_context(
        context.workers, context.claim.identity.run_id, "recovered-budget-owner"
    )
    try:
        exhausted = context.snapshot.config.runtime.model_call_limit == 1
        if exhausted:
            with pytest.raises(ModelBudgetExceeded) as error:
                await reserve(new)
            assert error.value.code == "AGENT_MODEL_CALL_BUDGET_EXCEEDED"
        else:
            await reserve(new)
            assert [item.call_number for item in await rows(context)] == [1, 2]
        assert (await rows(context))[0].status == "unknown"
        await context.gateway()._settle(permit, response=response())
        await context.gateway(1)._settle(permit, response=response())
        assert (await rows(context))[0].settled_cost_microusd == 20
        assert (await summary(context, context.claim.identity.attempt_id))[
            "total_cost_microusd"
        ] == 20
        assert (await summary(new, new.claim.identity.attempt_id))["model_call_count"] == (
            0 if exhausted else 1
        )
        with pytest.raises(RunLeaseLostError):
            await reserve(context)
        async with factory() as session:
            run = await session.get(AgentRun, context.claim.identity.run_id)
            conversation = await session.get(Conversation, run.conversation_id)
            assert (
                run.status == RunStatus.RUNNING
                and run.current_attempt_id == new.claim.identity.attempt_id
            )
            assert conversation.active_attempt_id == new.claim.identity.attempt_id
    finally:
        await worker_runtime._cancel_owned_attempt(
            factory, new.claim.identity, reason="isolated_recovery_finished"
        )


async def test_reservation_and_event_commit_failure_roll_back_together(budget_context, monkeypatch):
    context = budget_context

    async def fail_event(*args, **kwargs):
        raise OSError("isolated event storage error")

    with monkeypatch.context() as patch:
        patch.setattr(model_budget_services, "append_event", fail_event)
        with pytest.raises(OSError):
            await reserve(context)
    assert not await rows(context)
    await reserve(context)
    assert (await rows(context))[0].call_number == 1
    async with create_session_factory(context.workers.engines[1])() as session:
        events = (
            await session.scalars(
                select(RunEvent).where(
                    RunEvent.run_id == context.claim.identity.run_id,
                    RunEvent.event_type == "budget.model_reserved",
                )
            )
        ).all()
        assert len(events) == 1


async def test_large_current_tool_output_fails_before_reservation_or_model(budget_context):
    context = budget_context
    request = context.request().override(
        messages=[
            *context.request().messages,
            ToolMessage(content="超大工具输出" * 100_000, tool_call_id="untrusted-test-call"),
        ]
    )

    async def forbidden(request):
        raise AssertionError("超预算不能进入模型")

    with pytest.raises(ContextBudgetExceeded):
        await context.gateway().execute(request, forbidden)
    assert not await rows(context)


@pytest.mark.parametrize("budget_context", [{"pricing": {}}], indirect=True)
async def test_usage_without_known_price_remains_unknown_cost(budget_context):
    context = budget_context
    permit = await reserve(context)
    await context.gateway()._settle(permit, response=response())
    record = (await rows(context))[0]
    assert record.status == "settled" and record.input_tokens == 10
    assert record.reserved_cost_microusd is record.settled_cost_microusd is None
    result = await summary(context)
    assert result["input_tokens"] == 10 and result["total_cost_microusd"] is None
    assert result["cost_source"] == "unknown"


async def test_settlement_storage_failure_keeps_consumed_reservation(budget_context, monkeypatch):
    context = budget_context
    gateway = context.gateway()
    original = gateway._settle
    calls = 0

    async def fail_settle(*args, **kwargs):
        raise OSError("isolated settlement unavailable")

    async def handler(request):
        nonlocal calls
        calls += 1
        return response()

    monkeypatch.setattr(gateway, "_settle", fail_settle)
    with pytest.raises(OSError):
        await gateway.execute(context.request(), handler)
    record = (await rows(context))[0]
    assert calls == 1 and record.status == "reserved" and record.reserved_cost_microusd > 0
    # 重连查账后，只补原许可结算，不再调用模型。
    from supportops_core.model_budget_services import ModelCallPermit

    await original(ModelCallPermit(record.id, 1, 2), response=response())
    assert (await rows(context))[0].status == "settled" and calls == 1
