"""真实 PostgreSQL 中并发提交、同会话串行及跨会话并行。"""

import asyncio
import os
from collections import Counter
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack
from dataclasses import dataclass
from typing import cast
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from redis.asyncio import Redis
from sqlalchemy import select
from supportops_api.main import create_app
from supportops_core import worker_runtime
from supportops_core.agent_config import SupportAnswer
from supportops_core.agent_runtime import AgentRuntimeSnapshot
from supportops_core.db import create_session_factory
from supportops_core.enums import RunStatus
from supportops_core.models import (
    AgentRun,
    Conversation,
    Message,
    RunAttempt,
    RunNotificationOutbox,
)

from tests.conftest import FakeRedis, auth_headers, provision_agent
from tests.postgres.test_run_fencing import PostgresWorkers
from tests.postgres.test_run_fencing import workers as workers

pytestmark = pytest.mark.skipif(
    os.getenv("SUPPORTOPS_RUN_POSTGRES_TESTS") != "1",
    reason="需显式启用隔离 PostgreSQL 测试库",
)


@dataclass(frozen=True)
class ApiPair:
    workers: PostgresWorkers
    clients: tuple[AsyncClient, AsyncClient]
    headers: dict[str, str]
    agent_id: UUID
    conversation_id: UUID


@pytest.fixture
async def apis(workers: PostgresWorkers) -> AsyncIterator[ApiPair]:
    tenant_id = uuid4()
    headers = await auth_headers(workers.engines[0], workers.settings, tenant_id)
    agent_id = await provision_agent(workers.engines[0], workers.settings, tenant_id)
    redis = cast(Redis, FakeRedis())
    apps = [create_app(workers.settings, engine=engine, redis=redis) for engine in workers.engines]
    async with AsyncExitStack() as stack:
        for app in apps:
            await stack.enter_async_context(app.router.lifespan_context(app))
        clients = tuple(
            [
                await stack.enter_async_context(
                    AsyncClient(
                        transport=ASGITransport(app=app),
                        base_url="http://ordering.test",
                    )
                )
                for app in apps
            ]
        )
        response = await clients[0].post(
            "/v1/conversations", json={"agent_id": str(agent_id)}, headers=headers
        )
        assert response.status_code == 201
        yield ApiPair(
            workers,
            cast(tuple[AsyncClient, AsyncClient], clients),
            headers,
            agent_id,
            UUID(response.json()["id"]),
        )


async def submit(
    apis: ApiPair, index: int, *, content: str, key: str, conversation_id: UUID | None = None
):
    return await apis.clients[index % 2].post(
        f"/v1/conversations/{conversation_id or apis.conversation_id}/messages",
        json={"content": content},
        headers={**apis.headers, "Idempotency-Key": key},
    )


@pytest.mark.parametrize("idempotency", ["different", "same", "conflict"])
async def test_simultaneous_api_submissions_allocate_unique_order_or_reuse_same_input(
    apis: ApiPair,
    idempotency: str,
) -> None:
    start = asyncio.Event()

    async def send(index: int):
        await start.wait()
        return await submit(
            apis,
            index,
            content="并发输入" if idempotency == "same" else f"并发输入{index}",
            key=f"input-{index}" if idempotency == "different" else "same-key",
        )

    tasks = [asyncio.create_task(send(index)) for index in range(2)]
    start.set()
    responses = await asyncio.wait_for(asyncio.gather(*tasks), timeout=5)
    assert sorted(response.status_code for response in responses) == (
        [202, 409] if idempotency == "conflict" else [202, 202]
    )
    accepted = [response.json() for response in responses if response.status_code == 202]
    expected = 2 if idempotency == "different" else 1
    assert len({item["run_id"] for item in accepted}) == expected
    assert len({item["message_id"] for item in accepted}) == expected
    assert all(item["status"] == "queued" for item in accepted)
    async with create_session_factory(apis.workers.engines[0])() as session:
        runs = (
            await session.scalars(
                select(AgentRun)
                .where(
                    AgentRun.conversation_id == apis.conversation_id,
                )
                .order_by(AgentRun.input_sequence)
            )
        ).all()
        assert [run.input_sequence for run in runs] == list(range(1, expected + 1))
        assert all(
            run.status == RunStatus.QUEUED and run.current_attempt_id is None for run in runs
        )
        conversation = await session.get(Conversation, apis.conversation_id)
        assert conversation is not None and conversation.next_input_sequence == expected + 1
        assert conversation.active_attempt_id is None
        messages = (
            await session.scalars(
                select(Message).where(
                    Message.conversation_id == apis.conversation_id,
                )
            )
        ).all()
        assert len(messages) == expected
        assert {message.turn_sequence for message in messages} == set(range(1, expected + 1))
        notifications = (
            await session.scalars(
                select(RunNotificationOutbox).where(
                    RunNotificationOutbox.run_id.in_([run.id for run in runs]),
                )
            )
        ).all()
        assert len(notifications) == expected


@pytest.mark.parametrize("first_fails", [False, True])
async def test_competing_workers_serialize_conversation_and_continue_after_first_terminal(
    apis: ApiPair,
    monkeypatch: pytest.MonkeyPatch,
    first_fails: bool,
) -> None:
    responses = await asyncio.gather(
        *(
            submit(apis, index, content=f"排队输入{index}", key=f"ordered-{index}")
            for index in range(2)
        )
    )
    assert all(response.status_code == 202 for response in responses)
    factories = tuple(create_session_factory(engine) for engine in apis.workers.engines)
    async with factories[0]() as session:
        runs = (
            await session.scalars(
                select(AgentRun)
                .where(
                    AgentRun.conversation_id == apis.conversation_id,
                )
                .order_by(AgentRun.input_sequence)
            )
        ).all()
        assert len(runs) == 2
        first_id, second_id = [run.id for run in runs]
        first_input = await session.get(Message, runs[0].input_message_id)
        second_input = await session.get(Message, runs[1].input_message_id)
        assert first_input is not None and second_input is not None
        first_text, second_text = first_input.content, second_input.content
    entered, release = asyncio.Event(), asyncio.Event()
    calls: Counter[UUID] = Counter()
    active: set[UUID] = set()
    maximum_active = 0

    async def model(snapshot: AgentRuntimeSnapshot, **kwargs: object) -> SupportAnswer:
        nonlocal maximum_active
        calls[snapshot.context.run_id] += 1
        active.add(snapshot.context.run_id)
        maximum_active = max(maximum_active, len(active))
        try:
            if snapshot.context.run_id == first_id:
                assert snapshot.product_messages == (("user", first_text),)
                assert second_input.id not in snapshot.product_message_ids
                entered.set()
                await release.wait()
                if first_fails:
                    raise RuntimeError("isolated first-turn model failure")
                return SupportAnswer(answer="前轮完成回答")
            assert snapshot.context.run_id == second_id
            assert snapshot.input_text == second_text
            expected = (("user", first_text),)
            if not first_fails:
                expected += (("assistant", "前轮完成回答"),)
            assert snapshot.product_messages == (*expected, ("user", second_text))
            assert snapshot.product_message_ids.count(second_input.id) == 1
            return SupportAnswer(answer="后轮完成回答")
        finally:
            active.discard(snapshot.context.run_id)

    monkeypatch.setattr(worker_runtime, "invoke_agent", model)

    async def execute(run_id: UUID, index: int) -> bool:
        return await worker_runtime.execute_agent_run(
            factories[index % 2],
            run_id,
            settings=apis.workers.settings,
            owner_id=f"ordering-worker-{index}",
        )

    # 后轮即使先收到通知，也必须保持 queued，不能抢到第一轮许可。
    assert not await execute(second_id, 1)
    first_tasks = [asyncio.create_task(execute(first_id, index)) for index in range(4)]
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
        assert (
            await asyncio.wait_for(
                asyncio.gather(*(execute(second_id, index) for index in range(4))), timeout=5
            )
            == [False] * 4
        )
        async with factories[1]() as session:
            conversation = await session.get(Conversation, apis.conversation_id)
            second = await session.get(AgentRun, second_id)
            assert conversation is not None and conversation.active_run_id == first_id
            assert second is not None and second.status == RunStatus.QUEUED
            assert second.current_attempt_id is None
        release.set()
        assert sum(await asyncio.wait_for(asyncio.gather(*first_tasks), timeout=5)) == (
            0 if first_fails else 1
        )
        assert (
            sum(
                await asyncio.wait_for(
                    asyncio.gather(*(execute(second_id, index) for index in range(4))), timeout=5
                )
            )
            == 1
        )
    finally:
        release.set()
        await asyncio.gather(*first_tasks, return_exceptions=True)
    assert maximum_active == 1 and calls == Counter({first_id: 1, second_id: 1})
    async with factories[0]() as session:
        conversation = await session.get(Conversation, apis.conversation_id)
        assert conversation is not None and conversation.active_run_id is None
        assert conversation.active_attempt_id is None
        first, second = (
            await session.get(AgentRun, first_id),
            await session.get(AgentRun, second_id),
        )
        assert first is not None and first.status == (
            RunStatus.FAILED if first_fails else RunStatus.COMPLETED
        )
        assert second is not None and second.status == RunStatus.COMPLETED
        attempts = (
            await session.scalars(
                select(RunAttempt).where(
                    RunAttempt.run_id.in_((first_id, second_id)),
                )
            )
        ).all()
        assert len(attempts) == 2 and all(attempt.status != "running" for attempt in attempts)


async def test_same_tenant_different_conversations_execute_in_parallel(
    apis: ApiPair,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created = await apis.clients[1].post(
        "/v1/conversations", json={"agent_id": str(apis.agent_id)}, headers=apis.headers
    )
    assert created.status_code == 201
    conversations = (apis.conversation_id, UUID(created.json()["id"]))
    responses = await asyncio.gather(
        *(
            submit(
                apis,
                index,
                content=f"独立会话输入{index}",
                key=f"parallel-{index}",
                conversation_id=conversation_id,
            )
            for index, conversation_id in enumerate(conversations)
        )
    )
    assert all(response.status_code == 202 for response in responses)
    run_ids = [UUID(response.json()["run_id"]) for response in responses]
    release, both_entered = asyncio.Event(), asyncio.Event()
    active: set[UUID] = set()
    calls: Counter[UUID] = Counter()

    async def model(snapshot: AgentRuntimeSnapshot, **kwargs: object) -> SupportAnswer:
        calls[snapshot.context.conversation_id] += 1
        active.add(snapshot.context.conversation_id)
        if len(active) == 2:
            both_entered.set()
        try:
            assert snapshot.product_messages == (("user", snapshot.input_text),)
            await release.wait()
            return SupportAnswer(answer=f"独立回答{snapshot.input_text}")
        finally:
            active.discard(snapshot.context.conversation_id)

    monkeypatch.setattr(worker_runtime, "invoke_agent", model)
    tasks = [
        asyncio.create_task(
            worker_runtime.execute_agent_run(
                create_session_factory(apis.workers.engines[index]),
                run_id,
                settings=apis.workers.settings,
                owner_id=f"parallel-worker-{index}",
            )
        )
        for index, run_id in enumerate(run_ids)
    ]
    try:
        await asyncio.wait_for(both_entered.wait(), timeout=5)
        assert active == set(conversations) and not any(task.done() for task in tasks)
        async with create_session_factory(apis.workers.engines[0])() as session:
            rows = (
                await session.scalars(
                    select(Conversation).where(
                        Conversation.id.in_(conversations),
                    )
                )
            ).all()
            assert {row.active_run_id for row in rows} == set(run_ids)
            assert len({row.active_attempt_id for row in rows}) == 2
        release.set()
        assert await asyncio.wait_for(asyncio.gather(*tasks), timeout=5) == [True, True]
    finally:
        release.set()
        await asyncio.gather(*tasks, return_exceptions=True)
    assert calls == Counter({conversation_id: 1 for conversation_id in conversations})
    async with create_session_factory(apis.workers.engines[1])() as session:
        for conversation_id in conversations:
            conversation = await session.get(Conversation, conversation_id)
            assert conversation is not None and conversation.active_run_id is None
            assert conversation.active_attempt_id is None
