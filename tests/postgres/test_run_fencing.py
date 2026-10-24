"""真实 PostgreSQL 多执行者领取、恢复及产品/checkpoint fencing 验收。"""

import asyncio
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from langgraph.checkpoint.base import BaseCheckpointSaver, empty_checkpoint
from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine
from supportops_core import worker_runtime
from supportops_core.agent_config import SupportAnswer
from supportops_core.agent_runtime import (
    AgentExecutionResult,
    AgentRuntimeSnapshot,
    checkpoint_saver,
    checkpoint_thread_id,
)
from supportops_core.config import Settings
from supportops_core.db import create_engine, create_session_factory
from supportops_core.enums import MessageRole, RunStatus
from supportops_core.models import AgentRun, Conversation, Message, RunAttempt, RunEvent, utc_now
from supportops_core.run_attempt_services import (
    RunLeaseLostError,
    claim_run_attempt,
    owned_run,
    recover_expired_attempts,
)

from tests.conftest import retire_test_run_notifications
from tests.worker.test_worker import seed_run

pytestmark = pytest.mark.skipif(
    os.getenv("SUPPORTOPS_RUN_POSTGRES_TESTS") != "1",
    reason="需显式启用隔离 PostgreSQL 测试库",
)


@dataclass(frozen=True)
class PostgresWorkers:
    settings: Settings
    engines: tuple[AsyncEngine, AsyncEngine]


@pytest.fixture
async def workers() -> AsyncIterator[PostgresWorkers]:
    settings = Settings()
    url = make_url(settings.database_url)
    assert settings.environment == "test"
    assert url.drivername == "postgresql+asyncpg"
    assert url.database and url.database.endswith("_test")
    settings = Settings(
        **{
            **settings.model_dump(),
            "run_attempt_lease_seconds": 90,
            "run_attempt_heartbeat_seconds": 30,
            "langgraph_checkpoint_schema": f"fencing_{uuid4().hex}",
        }
    )
    engines = (create_engine(settings), create_engine(settings))
    async with create_session_factory(engines[0])() as session:
        existing_run_ids = set((await session.scalars(select(AgentRun.id))).all())
    try:
        async with engines[0].begin() as connection:
            # 名称完全由服务器端 UUID 构造，仅创建本用例的隔离 checkpoint 空间。
            await connection.execute(text(f"CREATE SCHEMA {settings.langgraph_checkpoint_schema}"))
        async with checkpoint_saver(settings):
            pass
        yield PostgresWorkers(settings, engines)
    finally:
        try:
            await retire_test_run_notifications(engines[0], existing_run_ids)
        finally:
            await asyncio.gather(*(engine.dispose() for engine in engines))


async def save_checkpoint(settings: Settings, thread_id: str, marker: str) -> dict[str, Any]:
    async with checkpoint_saver(settings) as raw:
        saver = cast(BaseCheckpointSaver[Any], raw)
        checkpoint = empty_checkpoint()
        version = saver.get_next_version(None, None)
        checkpoint["channel_values"] = {"fencing_marker": marker}
        checkpoint["channel_versions"] = {"fencing_marker": version}
        checkpoint["updated_channels"] = ["fencing_marker"]
        return cast(
            dict[str, Any],
            await saver.aput(
                {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}},
                checkpoint,
                {"source": "input", "step": 0, "parents": {}},
                {"fencing_marker": version},
            ),
        )


async def checkpoint_marker(settings: Settings, config: dict[str, Any]) -> str:
    async with checkpoint_saver(settings) as raw:
        result = await raw.aget_tuple(config)
        assert result is not None
        return str(result.checkpoint["channel_values"]["fencing_marker"])


async def assert_product(
    workers: PostgresWorkers,
    run_id: UUID,
    *,
    answer: str,
    attempt_id: UUID,
    checkpoint: dict[str, Any],
) -> None:
    async with create_session_factory(workers.engines[1])() as session:
        run = await session.get(AgentRun, run_id)
        assert run is not None and run.status == RunStatus.COMPLETED
        assert run.current_attempt_id == attempt_id
        conversation = await session.get(Conversation, run.conversation_id)
        assert conversation is not None
        assert conversation.active_run_id is None and conversation.active_attempt_id is None
        assert conversation.checkpoint_thread_id == checkpoint["configurable"]["thread_id"]
        assert conversation.checkpoint_id == checkpoint["configurable"]["checkpoint_id"]
        assert conversation.checkpoint_input_sequence == run.input_sequence
        attempt = await session.get(RunAttempt, attempt_id)
        assert attempt is not None and attempt.status == "completed"
        assert attempt.checkpoint_id == conversation.checkpoint_id
        messages = (
            await session.scalars(
                select(Message).where(
                    Message.conversation_id == conversation.id,
                    Message.role == MessageRole.ASSISTANT,
                )
            )
        ).all()
        assert len(messages) == 1 and messages[0].content == answer
        assert messages[0].support_result["attempt_id"] == str(attempt_id)
        events = (
            await session.scalars(
                select(RunEvent)
                .where(
                    RunEvent.run_id == run_id,
                )
                .order_by(RunEvent.sequence)
            )
        ).all()
        assert [event.sequence for event in events] == list(range(1, len(events) + 1))
        terminals = [
            event
            for event in events
            if event.event_type
            in {
                "run.completed",
                "run.failed",
                "run.cancelled",
            }
        ]
        assert len(terminals) == 1 and terminals[0].event_type == "run.completed"
        assert terminals[0].attempt_id == attempt_id


async def test_competing_workers_commit_one_attempt_product_and_checkpoint(
    workers: PostgresWorkers,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = await seed_run(workers.engines[0], workers.settings)
    calls: list[UUID] = []
    saved: dict[str, Any] = {}

    async def model(snapshot: AgentRuntimeSnapshot, **kwargs: object) -> AgentExecutionResult:
        assert snapshot.context.attempt_id is not None
        calls.append(snapshot.context.attempt_id)
        config = await save_checkpoint(
            workers.settings, checkpoint_thread_id(snapshot.context), "winner"
        )
        saved.update(config)
        return AgentExecutionResult(
            answer=SupportAnswer(answer="唯一获胜回答"),
            checkpoint_thread_id=checkpoint_thread_id(snapshot.context),
            checkpoint_id=config["configurable"]["checkpoint_id"],
        )

    monkeypatch.setattr(worker_runtime, "invoke_agent", model)
    start = asyncio.Event()

    async def compete(index: int) -> bool:
        await start.wait()
        return await worker_runtime.execute_agent_run(
            create_session_factory(workers.engines[index % 2]),
            run_id,
            settings=workers.settings,
            owner_id=f"competing-worker-{index}",
        )

    tasks = [asyncio.create_task(compete(index)) for index in range(8)]
    start.set()
    results = await asyncio.wait_for(asyncio.gather(*tasks), timeout=15)
    assert sum(results) == 1 and len(calls) == 1
    async with create_session_factory(workers.engines[0])() as session:
        attempts = (
            await session.scalars(select(RunAttempt).where(RunAttempt.run_id == run_id))
        ).all()
        assert len(attempts) == 1 and attempts[0].generation == 1
    await assert_product(
        workers, run_id, answer="唯一获胜回答", attempt_id=calls[0], checkpoint=saved
    )
    assert await checkpoint_marker(workers.settings, saved) == "winner"


async def test_lease_takeover_rejects_late_old_product_text_and_checkpoint_pointer(
    workers: PostgresWorkers,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = await seed_run(workers.engines[0], workers.settings)
    factories = tuple(create_session_factory(engine) for engine in workers.engines)
    old_entered, release_old = asyncio.Event(), asyncio.Event()
    checkpoints: dict[str, dict[str, Any]] = {}
    identities: dict[str, UUID] = {}

    async def model(snapshot: AgentRuntimeSnapshot, **kwargs: object) -> AgentExecutionResult:
        attempt_id = snapshot.context.attempt_id
        assert attempt_id is not None
        kind = "old" if not identities else "new"
        identities[kind] = attempt_id
        thread = checkpoint_thread_id(snapshot.context)
        if kind == "old":
            checkpoints["old_before"] = await save_checkpoint(
                workers.settings, thread, "old_before"
            )
            old_entered.set()
            await release_old.wait()
        checkpoints[kind] = await save_checkpoint(workers.settings, thread, kind)
        return AgentExecutionResult(
            answer=SupportAnswer(answer=f"{kind}回答"),
            checkpoint_thread_id=thread,
            checkpoint_id=checkpoints[kind]["configurable"]["checkpoint_id"],
        )

    monkeypatch.setattr(worker_runtime, "invoke_agent", model)
    old_task = asyncio.create_task(
        worker_runtime.execute_agent_run(
            factories[0],
            run_id,
            settings=workers.settings,
            owner_id="old-worker",
        )
    )
    try:
        await asyncio.wait_for(old_entered.wait(), timeout=8)
        async with factories[1]() as session, session.begin():
            attempt = await session.get(RunAttempt, identities["old"])
            assert attempt is not None
            attempt.lease_expires_at = utc_now() - timedelta(seconds=1)
        # 两个恢复者同时发现过期记录，也只能产生一次恢复事实。
        recovered = await asyncio.wait_for(
            asyncio.gather(
                *(
                    recover_expired_attempts(factory, settings=workers.settings)
                    for factory in factories
                )
            ),
            timeout=5,
        )
        assert sum(recovered) == 1
        async with factories[1]() as session:
            run = await session.get(AgentRun, run_id)
            assert run is not None and run.recovery_count == 1 and run.current_attempt_id is None
            conversation = await session.get(Conversation, run.conversation_id)
            assert conversation is not None and conversation.checkpoint_id is None
        assert await worker_runtime.execute_agent_run(
            factories[1],
            run_id,
            settings=workers.settings,
            owner_id="new-worker",
        )
        await assert_product(
            workers,
            run_id,
            answer="new回答",
            attempt_id=identities["new"],
            checkpoint=checkpoints["new"],
        )
        release_old.set()
        assert not await asyncio.wait_for(old_task, timeout=8)
        # 旧 saver 仍可写自己的隔离 thread；产品指针必须保持新 attempt 的精确提交。
        assert (
            checkpoints["old"]["configurable"]["thread_id"]
            != checkpoints["new"]["configurable"]["thread_id"]
        )
        assert await checkpoint_marker(workers.settings, checkpoints["old"]) == "old"
        assert await checkpoint_marker(workers.settings, checkpoints["new"]) == "new"
        await assert_product(
            workers,
            run_id,
            answer="new回答",
            attempt_id=identities["new"],
            checkpoint=checkpoints["new"],
        )
        async with factories[1]() as session:
            attempts = (
                await session.scalars(
                    select(RunAttempt)
                    .where(
                        RunAttempt.run_id == run_id,
                    )
                    .order_by(RunAttempt.generation)
                )
            ).all()
            assert [attempt.status for attempt in attempts] == ["expired", "completed"]
            assert [attempt.generation for attempt in attempts] == [1, 2]
            events = (
                await session.scalars(
                    select(RunEvent)
                    .where(
                        RunEvent.run_id == run_id,
                    )
                    .order_by(RunEvent.sequence)
                )
            ).all()
            recovery = next(
                event.sequence for event in events if event.event_type == "run.recovering"
            )
            assert not any(
                event.attempt_id == identities["old"] and event.sequence > recovery
                for event in events
            )
    finally:
        release_old.set()
        await asyncio.gather(old_task, return_exceptions=True)


@pytest.mark.parametrize("changed", ["owner", "generation"])
async def test_old_identity_is_rejected_even_with_live_lease(
    workers: PostgresWorkers,
    changed: str,
) -> None:
    from dataclasses import replace

    run_id = await seed_run(workers.engines[0], workers.settings)
    factory = create_session_factory(workers.engines[0])
    async with factory() as session, session.begin():
        claim = await claim_run_attempt(session, run_id, owner_id="current-owner", lease_seconds=90)
        assert claim is not None
    stale = replace(
        claim.identity,
        **(
            {"owner_id": "stale-owner"}
            if changed == "owner"
            else {"generation": claim.identity.generation + 1}
        ),
    )
    async with create_session_factory(workers.engines[1])() as session, session.begin():
        with pytest.raises(RunLeaseLostError):
            await owned_run(session, stale)
    async with factory() as session, session.begin():
        assert (await owned_run(session, claim.identity)).attempt.id == claim.attempt.id


async def test_checkpoint_saved_before_product_failure_is_not_adopted_by_recovery(
    workers: PostgresWorkers,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = await seed_run(workers.engines[0], workers.settings)
    factory = create_session_factory(workers.engines[0])
    checkpoints: list[dict[str, Any]] = []
    attempts: list[UUID] = []
    original_persist = worker_runtime.persist_support_message

    async def model(snapshot: AgentRuntimeSnapshot, **kwargs: object) -> AgentExecutionResult:
        assert snapshot.context.attempt_id is not None
        attempts.append(snapshot.context.attempt_id)
        config = await save_checkpoint(
            workers.settings, checkpoint_thread_id(snapshot.context), str(len(attempts))
        )
        checkpoints.append(config)
        return AgentExecutionResult(
            answer=SupportAnswer(answer=f"第{len(attempts)}次回答"),
            checkpoint_thread_id=checkpoint_thread_id(snapshot.context),
            checkpoint_id=config["configurable"]["checkpoint_id"],
        )

    async def fail_after_message_flush(*args, **kwargs):
        await original_persist(*args, **kwargs)
        raise OSError("isolated product transaction failure after message flush")

    monkeypatch.setattr(worker_runtime, "invoke_agent", model)
    monkeypatch.setattr(worker_runtime, "persist_support_message", fail_after_message_flush)
    assert not await worker_runtime.execute_agent_run(factory, run_id, settings=workers.settings)
    async with factory() as session:
        run = await session.get(AgentRun, run_id)
        assert run is not None and run.status == RunStatus.QUEUED and run.recovery_count == 1
        attempt = await session.get(RunAttempt, attempts[0])
        assert (
            attempt is not None
            and attempt.status == "quarantined"
            and attempt.checkpoint_id is None
        )
        conversation = await session.get(Conversation, run.conversation_id)
        assert conversation is not None and conversation.checkpoint_id is None
        assert not (
            await session.scalars(
                select(Message).where(
                    Message.conversation_id == conversation.id,
                    Message.role == MessageRole.ASSISTANT,
                )
            )
        ).all()
    assert await checkpoint_marker(workers.settings, checkpoints[0]) == "1"
    monkeypatch.setattr(worker_runtime, "persist_support_message", original_persist)
    assert await worker_runtime.execute_agent_run(
        create_session_factory(workers.engines[1]),
        run_id,
        settings=workers.settings,
    )
    assert len(attempts) == 2 and attempts[0] != attempts[1]
    await assert_product(
        workers, run_id, answer="第2次回答", attempt_id=attempts[1], checkpoint=checkpoints[1]
    )
    assert await checkpoint_marker(workers.settings, checkpoints[0]) == "1"
    assert await checkpoint_marker(workers.settings, checkpoints[1]) == "2"
