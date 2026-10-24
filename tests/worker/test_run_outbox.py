"""Run 通知的批次、重复投递、事务未知与跳锁契约。"""

from datetime import timedelta
from typing import cast
from uuid import UUID

import pytest
from redis.asyncio import Redis
from sqlalchemy import event, func, select
from sqlalchemy.orm import Session
from supportops_core import worker_runtime
from supportops_core.agent_config import SupportAnswer
from supportops_core.db import create_session_factory
from supportops_core.models import (
    AgentRun,
    Conversation,
    Message,
    RunEvent,
    RunNotificationOutbox,
    utc_now,
)
from supportops_core.queue import RunQueue
from supportops_core.run_outbox_services import dispatch_run_notifications
from supportops_core.services import create_message_and_run

from tests.worker.test_service_lifecycle import ServiceEnvironment
from tests.worker.test_service_lifecycle import service_environment as service_environment
from tests.worker.test_worker import seed_run


async def make_due(environment: ServiceEnvironment, run_ids: list[UUID]) -> None:
    async with create_session_factory(environment.engine)() as session, session.begin():
        notifications = (
            await session.scalars(
                select(RunNotificationOutbox).where(
                    RunNotificationOutbox.run_id.in_(run_ids),
                )
            )
        ).all()
        for notification in notifications:
            notification.next_attempt_at = utc_now() - timedelta(seconds=1)


async def test_input_transaction_failure_leaves_no_partial_message_run_event_or_outbox(
    service_environment: ServiceEnvironment,
) -> None:
    environment = service_environment
    run_id = await seed_run(environment.engine, environment.settings)
    factory = create_session_factory(environment.engine)
    async with factory() as session:
        original = await session.get(AgentRun, run_id)
        assert original is not None
        conversation_id = original.conversation_id
    with pytest.raises(RuntimeError, match="input commit fault"):
        async with factory() as session, session.begin():
            conversation = await session.get(Conversation, conversation_id)
            assert conversation is not None
            created = await create_message_and_run(
                session,
                conversation=conversation,
                user_id=conversation.user_id,
                identity_roles=("customer",),
                content="必须整笔回滚的输入",
                idempotency_key="rollback-input",
                correlation_id="input-rollback",
            )
            rollback_run_id, rollback_message_id = created.run.id, created.message.id
            raise RuntimeError("input commit fault")
    async with factory() as session:
        assert await session.get(AgentRun, rollback_run_id) is None
        assert await session.get(Message, rollback_message_id) is None
        assert (
            await session.scalar(
                select(func.count())
                .select_from(RunEvent)
                .where(
                    RunEvent.run_id == rollback_run_id,
                )
            )
            == 0
        )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(RunNotificationOutbox)
                .where(
                    RunNotificationOutbox.run_id == rollback_run_id,
                )
            )
            == 0
        )
        assert await session.get(AgentRun, run_id) is not None
    assert await environment.redis.llen(environment.settings.redis_queue_name) == 0


async def test_batch_limit_caps_candidates_and_preserves_unselected_notifications(
    service_environment: ServiceEnvironment,
) -> None:
    environment = service_environment
    settings = environment.settings.model_copy(update={"run_outbox_batch_size": 2})
    factory = create_session_factory(environment.engine)
    run_ids = [await seed_run(environment.engine, settings) for _ in range(3)]
    queue = RunQueue(cast(Redis, environment.redis), settings.redis_queue_name)
    assert await dispatch_run_notifications(factory, queue=queue, settings=settings, limit=100) == 2
    assert await environment.redis.llen(settings.redis_queue_name) == 2
    async with factory() as session:
        notifications = (
            await session.scalars(
                select(RunNotificationOutbox).where(
                    RunNotificationOutbox.run_id.in_(run_ids),
                )
            )
        ).all()
        assert sorted(item.attempt_count for item in notifications) == [0, 1, 1]
    assert await dispatch_run_notifications(factory, queue=queue, settings=settings, limit=1) == 1
    assert await dispatch_run_notifications(factory, queue=queue, settings=settings, limit=0) == 0


@pytest.mark.parametrize("failure_boundary", ["none", "redis_ack", "database_commit"])
async def test_redelivery_keeps_one_business_input_and_one_effective_execution(
    service_environment: ServiceEnvironment,
    monkeypatch: pytest.MonkeyPatch,
    failure_boundary: str,
) -> None:
    environment = service_environment
    factory = create_session_factory(environment.engine)
    run_id = await seed_run(environment.engine, environment.settings)
    queue = RunQueue(cast(Redis, environment.redis), environment.settings.redis_queue_name)
    enqueued = False
    fail_once = True
    enqueue = queue.enqueue

    async def enqueue_with_fault(value: UUID) -> None:
        nonlocal enqueued, fail_once
        await enqueue(value)
        enqueued = True
        if failure_boundary == "redis_ack" and fail_once:
            fail_once = False
            raise OSError("isolated Redis acknowledgement lost")

    def fail_commit(session: Session) -> None:
        nonlocal fail_once
        if failure_boundary == "database_commit" and enqueued and fail_once:
            fail_once = False
            raise OSError("isolated database commit fault after Redis write")

    monkeypatch.setattr(queue, "enqueue", enqueue_with_fault)
    event.listen(Session, "before_commit", fail_commit)
    try:
        if failure_boundary == "database_commit":
            with pytest.raises(OSError, match="database commit fault"):
                await dispatch_run_notifications(
                    factory,
                    queue=queue,
                    settings=environment.settings,
                    run_id=run_id,
                )
        else:
            first = await dispatch_run_notifications(
                factory,
                queue=queue,
                settings=environment.settings,
                run_id=run_id,
            )
            assert first == (0 if failure_boundary == "redis_ack" else 1)
    finally:
        event.remove(Session, "before_commit", fail_commit)
    async with factory() as session:
        notification = await session.scalar(
            select(RunNotificationOutbox).where(
                RunNotificationOutbox.run_id == run_id,
            )
        )
        assert notification is not None
        assert notification.attempt_count == (0 if failure_boundary == "database_commit" else 1)
        assert notification.status == ("delivered" if failure_boundary == "none" else "pending")
    await make_due(environment, [run_id])
    assert (
        await dispatch_run_notifications(
            factory,
            queue=queue,
            settings=environment.settings,
            run_id=run_id,
        )
        == 1
    )
    assert await queue.dequeue(timeout_seconds=1) == run_id
    assert await queue.dequeue(timeout_seconds=1) == run_id
    invoked = 0

    async def model(*args: object, **kwargs: object) -> SupportAnswer:
        nonlocal invoked
        invoked += 1
        return SupportAnswer(answer="重复通知只执行一次")

    monkeypatch.setattr(worker_runtime, "invoke_agent", model)
    assert await worker_runtime.execute_agent_run(factory, run_id, settings=environment.settings)
    assert not await worker_runtime.execute_agent_run(
        factory, run_id, settings=environment.settings
    )
    assert invoked == 1
    async with factory() as session:
        run = await session.get(AgentRun, run_id)
        assert run is not None
        assert (
            await session.scalar(
                select(func.count())
                .select_from(AgentRun)
                .where(
                    AgentRun.input_message_id == run.input_message_id,
                )
            )
            == 1
        )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(RunNotificationOutbox)
                .where(
                    RunNotificationOutbox.run_id == run_id,
                )
            )
            == 1
        )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(Message)
                .where(
                    Message.conversation_id == run.conversation_id,
                )
            )
            == 2
        )


@pytest.mark.parametrize("locked_resource", ["run", "notification"])
async def test_locked_first_candidate_does_not_block_next_or_spend_attempt(
    service_environment: ServiceEnvironment,
    locked_resource: str,
) -> None:
    environment = service_environment
    if environment.engine.dialect.name != "postgresql":
        pytest.skip("SKIP LOCKED 需要真实 PostgreSQL")
    factory = create_session_factory(environment.engine)
    run_ids = [await seed_run(environment.engine, environment.settings) for _ in range(2)]
    queue = RunQueue(cast(Redis, environment.redis), environment.settings.redis_queue_name)
    async with factory() as lock_session, lock_session.begin():
        model = AgentRun if locked_resource == "run" else RunNotificationOutbox
        field = AgentRun.id if locked_resource == "run" else RunNotificationOutbox.run_id
        await lock_session.scalar(select(model).where(field == run_ids[0]).with_for_update())
        assert (
            await dispatch_run_notifications(
                factory,
                queue=queue,
                settings=environment.settings,
                limit=2,
            )
            == 1
        )
        assert await queue.dequeue(timeout_seconds=1) == run_ids[1]
        async with factory() as session:
            notification = await session.scalar(
                select(RunNotificationOutbox).where(
                    RunNotificationOutbox.run_id == run_ids[0],
                )
            )
            assert notification is not None and notification.attempt_count == 0
            assert notification.status == "pending"
    assert (
        await dispatch_run_notifications(
            factory,
            queue=queue,
            settings=environment.settings,
            run_id=run_ids[0],
        )
        == 1
    )
