"""故障后核对唯一产品终态、会话释放与隔离 Mock 目标的副作用一致性。"""

import asyncio
from datetime import timedelta
from uuid import UUID

import httpx
import pytest
from sqlalchemy import select
from supportops_core import action_runtime, worker_runtime
from supportops_core.action_proposal_services import propose_mock_ticket, request_action_execution
from supportops_core.agent_config import SupportAnswer
from supportops_core.approval_services import live_identity
from supportops_core.db import create_session_factory
from supportops_core.enums import MessageRole, RunStatus
from supportops_core.models import (
    ActionAttempt,
    ActionReceipt,
    AgentRun,
    BusinessAction,
    Conversation,
    Message,
    MockRemoteOperation,
    MockSupportTicket,
    RunAttempt,
    RunEvent,
    ToolInvocation,
    utc_now,
)
from supportops_core.run_attempt_services import claim_run_attempt, recover_expired_attempts
from supportops_core.services import cancel_run

from tests.worker.test_service_lifecycle import ServiceEnvironment
from tests.worker.test_service_lifecycle import service_environment as service_environment
from tests.worker.test_worker import seed_run


async def assert_run_settled(
    environment: ServiceEnvironment,
    run_id: UUID,
    status: RunStatus,
) -> None:
    """从新会话读取产品/许可/事件，不用执行器返回值充当成功证明。"""
    async with create_session_factory(environment.engine)() as session:
        run = await session.get(AgentRun, run_id)
        assert run is not None and run.status == status and run.finished_at is not None
        conversation = await session.get(Conversation, run.conversation_id)
        assert conversation is not None
        assert conversation.active_run_id is None and conversation.active_attempt_id is None
        attempts = (
            await session.scalars(select(RunAttempt).where(RunAttempt.run_id == run.id))
        ).all()
        assert attempts and all(attempt.status != "running" for attempt in attempts)
        events = (
            await session.scalars(
                select(RunEvent)
                .where(
                    RunEvent.run_id == run.id,
                )
                .order_by(RunEvent.sequence)
            )
        ).all()
        assert [event.sequence for event in events] == list(range(1, len(events) + 1))
        terminal = [
            event
            for event in events
            if event.event_type
            in {
                "run.completed",
                "run.failed",
                "run.cancelled",
            }
        ]
        assert len(terminal) == 1 and terminal[0].event_type == f"run.{status.value}"
        messages = (
            await session.scalars(
                select(Message).where(
                    Message.conversation_id == conversation.id,
                    Message.role == MessageRole.ASSISTANT,
                )
            )
        ).all()
        assert len(messages) == (1 if status == RunStatus.COMPLETED else 0)


@pytest.mark.parametrize(
    "fault",
    [
        "model_error",
        "dependency_retry",
        "product_transaction",
        "expired",
        "expired_cancel",
        "exhausted",
    ],
)
async def test_resolved_run_faults_leave_one_terminal_and_no_execution_permit(
    service_environment: ServiceEnvironment,
    monkeypatch: pytest.MonkeyPatch,
    fault: str,
) -> None:
    environment = service_environment
    settings = environment.settings.model_copy(
        update={
            "run_attempt_max_recoveries": 0 if fault == "exhausted" else 2,
        }
    )
    factory = create_session_factory(environment.engine)
    run_id = await seed_run(environment.engine, settings)
    calls = 0
    persist = worker_runtime.persist_support_message

    async def model(*args: object, **kwargs: object) -> SupportAnswer:
        nonlocal calls
        calls += 1
        if fault == "model_error":
            raise RuntimeError("isolated non-retryable model failure")
        if fault == "dependency_retry" and calls == 1:
            raise httpx.ConnectError("isolated dependency connection failure")
        return SupportAnswer(answer="故障收敛后的唯一回答")

    async def fail_product_once(*args, **kwargs):
        await persist(*args, **kwargs)
        if calls == 1:
            raise OSError("isolated product transaction failure")

    monkeypatch.setattr(worker_runtime, "invoke_agent", model)
    if fault == "product_transaction":
        monkeypatch.setattr(worker_runtime, "persist_support_message", fail_product_once)
    if fault.startswith("expired") or fault == "exhausted":
        async with factory() as session, session.begin():
            claim = await claim_run_attempt(
                session, run_id, owner_id="crashed-worker", lease_seconds=60
            )
            assert claim is not None
            if fault == "expired_cancel":
                await cancel_run(session, claim.run)
            claim.attempt.lease_expires_at = utc_now() - timedelta(seconds=1)
        assert await recover_expired_attempts(factory, settings=settings) == (
            1 if fault == "expired" else 0
        )
    else:
        assert not await worker_runtime.execute_agent_run(factory, run_id, settings=settings)
    async with factory() as session:
        run = await session.get(AgentRun, run_id)
        assert run is not None
        if run.status == RunStatus.QUEUED:
            assert run.recovery_count == 1 and run.current_attempt_id is None
            conversation = await session.get(Conversation, run.conversation_id)
            assert conversation is not None and conversation.active_attempt_id is None
    expected = (
        RunStatus.CANCELLED
        if fault == "expired_cancel"
        else RunStatus.FAILED
        if fault in {"model_error", "exhausted"}
        else RunStatus.COMPLETED
    )
    if expected == RunStatus.COMPLETED:
        assert await worker_runtime.execute_agent_run(factory, run_id, settings=settings)
    assert not await worker_runtime.execute_agent_run(factory, run_id, settings=settings)
    assert await recover_expired_attempts(factory, settings=settings) == 0
    await assert_run_settled(environment, run_id, expected)


async def seed_queued_action(
    environment: ServiceEnvironment,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
) -> tuple[UUID, UUID]:
    settings = environment.settings
    factory = create_session_factory(environment.engine)
    run_id = await seed_run(
        environment.engine,
        settings,
        tool_ids=("create_mock_ticket",),
        content=f"隔离故障演练 [action:{mode}]",
    )

    async def model(*args: object, **kwargs: object) -> SupportAnswer:
        return SupportAnswer(answer="等待显式隔离建单意图")

    monkeypatch.setattr(worker_runtime, "invoke_agent", model)
    assert await worker_runtime.execute_agent_run(factory, run_id, settings=settings)
    async with factory() as session, session.begin():
        run = await session.get(AgentRun, run_id)
        assert run is not None
        conversation = await session.get(Conversation, run.conversation_id)
        assert conversation is not None
        identity = await live_identity(
            session, tenant_id=run.tenant_id, user_id=conversation.user_id
        )
        action = await propose_mock_ticket(
            session,
            run_id=run_id,
            identity=identity,
            settings=settings,
            correlation_id=run.correlation_id,
        )
        duplicate = await propose_mock_ticket(
            session,
            run_id=run_id,
            identity=identity,
            settings=settings,
            correlation_id=run.correlation_id,
        )
        assert duplicate.id == action.id
        await request_action_execution(
            session,
            action_id=action.id,
            identity=identity,
            settings=settings,
            expected_generation=action.generation,
            expected_parameters_digest=action.parameters_digest,
            expected_catalog_version=action.catalog_version,
            correlation_id=run.correlation_id,
            context_run_id=run_id,
        )
        return run_id, action.id


@pytest.mark.parametrize(
    "fault", ["lost", "target_commit_crash", "reconcile_crash", "parallel", "conflict", "unknown"]
)
async def test_action_faults_do_not_duplicate_completed_target_or_pin_run(
    service_environment: ServiceEnvironment,
    monkeypatch: pytest.MonkeyPatch,
    fault: str,
) -> None:
    environment = service_environment
    settings = environment.settings.model_copy(
        update={
            "business_action_mock_enabled": True,
            "action_max_reconciliations": 2,
        }
    )
    environment = ServiceEnvironment(
        settings, environment.engine, environment.redis, environment.connect_redis
    )
    factory = create_session_factory(environment.engine)
    mode = (
        "lost"
        if fault in {"lost", "reconcile_crash"}
        else fault
        if fault in {"conflict", "unknown"}
        else "normal"
    )
    run_id, action_id = await seed_queued_action(environment, monkeypatch, mode)
    async with factory() as session, session.begin():
        permit = await action_runtime.claim_action(
            session, settings=settings, owner_id="execute-worker"
        )
        assert permit is not None and permit.action_id == action_id and permit.kind == "execute"
    record = action_runtime.record_action_outcome

    async def simulate_process_exit(*args: object, **kwargs: object) -> None:
        raise asyncio.CancelledError("isolated process-stop boundary after target transaction")

    if fault == "target_commit_crash":
        monkeypatch.setattr(action_runtime, "record_action_outcome", simulate_process_exit)
        with pytest.raises(asyncio.CancelledError):
            await action_runtime.execute_action(factory, permit=permit, settings=settings)
        monkeypatch.setattr(action_runtime, "record_action_outcome", record)
    elif fault == "parallel":
        await asyncio.wait_for(
            asyncio.gather(
                *(
                    action_runtime.execute_action(factory, permit=permit, settings=settings)
                    for _ in range(2)
                )
            ),
            timeout=5,
        )
    else:
        await action_runtime.execute_action(factory, permit=permit, settings=settings)

    async def expire_and_recover() -> None:
        async with factory() as session, session.begin():
            action = await session.get(BusinessAction, action_id)
            assert action is not None
            attempt = await session.get(ActionAttempt, action.current_attempt_id)
            assert attempt is not None
            attempt.lease_expires_at = utc_now() - timedelta(seconds=1)
        async with factory() as session, session.begin():
            assert await action_runtime.recover_expired_actions(session) == 1

    if fault == "target_commit_crash":
        await expire_and_recover()
        # 原许可恢复前后均不能再次把执行请求发到目标。
        await action_runtime.execute_action(factory, permit=permit, settings=settings)
    for index in range(3):
        async with factory() as session, session.begin():
            action = await session.get(BusinessAction, action_id)
            assert action is not None
            if action.status in {"succeeded", "needs_attention"}:
                break
            action.next_attempt_at = utc_now() - timedelta(seconds=1)
        async with factory() as session, session.begin():
            check = await action_runtime.claim_action(
                session, settings=settings, owner_id=f"reconcile-{index}"
            )
            assert check is not None and check.action_id == action_id
            assert check.kind in {"verify", "reconcile"}
        if fault == "reconcile_crash" and index == 0:
            monkeypatch.setattr(action_runtime, "record_action_outcome", simulate_process_exit)
            with pytest.raises(asyncio.CancelledError):
                await action_runtime.execute_action(factory, permit=check, settings=settings)
            monkeypatch.setattr(action_runtime, "record_action_outcome", record)
            await expire_and_recover()
        else:
            await action_runtime.execute_action(factory, permit=check, settings=settings)
    async with factory() as session, session.begin():
        assert (
            await action_runtime.claim_action(
                session, settings=settings, owner_id="duplicate-worker"
            )
            is None
        )
        assert await action_runtime.recover_expired_actions(session) == 0
        action = await session.get(BusinessAction, action_id)
        assert action is not None
        assert action.status == (
            "needs_attention" if fault in {"conflict", "unknown"} else "succeeded"
        )
        attempts = (
            await session.scalars(select(ActionAttempt).where(ActionAttempt.action_id == action_id))
        ).all()
        assert sum(attempt.kind == "execute" for attempt in attempts) == 1
        assert all(attempt.status != "running" for attempt in attempts)
        invocations = (
            await session.scalars(
                select(ToolInvocation).where(
                    ToolInvocation.business_action_id == action_id,
                )
            )
        ).all()
        assert invocations and all(invocation.status != "running" for invocation in invocations)
        receipts = (
            await session.scalars(
                select(ActionReceipt).where(
                    ActionReceipt.action_id == action_id,
                )
            )
        ).all()
        assert sum(receipt.status == "verified" for receipt in receipts) == (
            1 if action.status == "succeeded" else 0
        )
        targets = (
            await session.scalars(
                select(MockSupportTicket).where(
                    MockSupportTicket.logical_key == action.logical_key,
                )
            )
        ).all()
        assert len(targets) == (0 if fault == "unknown" else 1)
        operations = (
            await session.scalars(
                select(MockRemoteOperation).where(
                    MockRemoteOperation.logical_key == action.logical_key,
                )
            )
        ).all()
        assert len(operations) == 1
        assert operations[0].status == ("unknown" if fault == "unknown" else "executed")
        if action.status == "succeeded":
            assert action.external_id == targets[0].external_id == operations[0].external_id
    await assert_run_settled(environment, run_id, RunStatus.COMPLETED)
