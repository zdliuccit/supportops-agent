import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from pytest import MonkeyPatch
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from supportops_core import product_message_services, run_attempt_services, worker_runtime
from supportops_core.agent_config import SupportAnswer
from supportops_core.agent_runtime import (
    AgentCapabilityError,
    AgentCheckpointError,
    AgentExecutionResult,
    AgentObservationResult,
    AgentReferenceError,
    AgentRuntimeSnapshot,
    AgentSecretResolutionError,
    AgentStructuredOutputError,
)
from supportops_core.config import Settings
from supportops_core.db import create_session_factory
from supportops_core.enums import MessageRole, RunStatus
from supportops_core.health_services import refresh_agent_runtime_health
from supportops_core.model_services import revoke_model_credential_revision, rotate_model_credential
from supportops_core.models import (
    Agent,
    AgentRun,
    AgentRunObservation,
    Conversation,
    Message,
    ModelCredential,
    RunAttempt,
    RunEvent,
    RuntimeServiceLease,
    User,
)
from supportops_core.run_attempt_services import RunLeaseLostError, claim_run_attempt, owned_run
from supportops_core.runtime_errors import runtime_error_message
from supportops_core.secrets import LocalEnvelopeSecretProvider
from supportops_core.services import (
    create_message_and_run,
    delete_conversation,
    recover_queued_runs,
)

from tests.conftest import provision_agent


@pytest.fixture(autouse=True)
def require_explicit_model_mock(monkeypatch: MonkeyPatch) -> None:
    async def unexpected_model_call(*args: object, **kwargs: object) -> None:
        raise AssertionError("Worker 单元测试必须显式提供模型 Mock")

    monkeypatch.setattr(worker_runtime, "invoke_agent", unexpected_model_call)


async def seed_run(
    engine: AsyncEngine,
    settings: Settings,
    *,
    tool_ids: tuple[str, ...] = (),
    content: str = "test",
    tool_call_limit: int | None = None,
    max_calls_per_tool: int = 4,
    runtime_options: dict[str, Any] | None = None,
    generation_options: dict[str, Any] | None = None,
    model_pricing: dict[str, Any] | None = None,
) -> UUID:
    tenant_id = uuid4()
    agent_id = await provision_agent(
        engine,
        settings,
        tenant_id,
        subject="worker-user",
        tool_ids=tool_ids,
        tool_call_limit=tool_call_limit,
        max_calls_per_tool=max_calls_per_tool,
        runtime_options=runtime_options,
        generation_options=generation_options,
        model_pricing=model_pricing,
    )
    session_factory = create_session_factory(engine)
    async with session_factory() as session, session.begin():
        unique_id = str(uuid4())
        user = await session.scalar(
            select(User).where(
                User.tenant_id == tenant_id,
                User.email == f"worker-user-{tenant_id}@example.test",
            )
        )
        agent = await session.get(Agent, agent_id)
        assert user is not None and agent is not None and agent.active_version_id is not None
        conversation = Conversation(
            tenant_id=tenant_id,
            user_id=user.id,
            agent_id=agent.id,
            agent_version_id=agent.active_version_id,
        )
        session.add(conversation)
        await session.flush()
        result = await create_message_and_run(
            session,
            conversation=conversation,
            user_id=user.id,
            identity_roles=tuple(user.roles),
            content=content,
            idempotency_key=f"worker-test-{unique_id}",
            correlation_id="worker-correlation",
        )
        return result.run.id


async def test_duplicate_delivery_only_executes_once(
    engine: AsyncEngine, settings: Settings, monkeypatch: MonkeyPatch
) -> None:
    run_id = await seed_run(engine, settings)
    session_factory = create_session_factory(engine)

    async def fake_invoke(*args: object, **kwargs: object) -> SupportAnswer:
        return SupportAnswer(answer="测试回答")

    monkeypatch.setattr(worker_runtime, "invoke_agent", fake_invoke)

    first = await worker_runtime.execute_agent_run(session_factory, run_id, settings=settings)
    second = await worker_runtime.execute_agent_run(session_factory, run_id, settings=settings)

    assert first is True
    assert second is False
    async with session_factory() as session:
        run = await session.get(AgentRun, run_id)
        events = list(
            (
                await session.scalars(
                    select(RunEvent).where(RunEvent.run_id == run_id).order_by(RunEvent.sequence)
                )
            ).all()
        )
        assert run is not None
        assert run.status == RunStatus.COMPLETED
        assert [event.sequence for event in events] == list(range(1, len(events) + 1))
        assistant_messages = list(
            (
                await session.scalars(
                    select(Message).where(
                        Message.conversation_id == run.conversation_id,
                        Message.role == MessageRole.ASSISTANT,
                    )
                )
            ).all()
        )
        assert len(assistant_messages) == 1


@pytest.mark.parametrize("delete_before_snapshot", [False, True])
async def test_delete_during_run_finishes_cancellation_without_late_reply(
    engine: AsyncEngine,
    settings: Settings,
    monkeypatch: MonkeyPatch,
    delete_before_snapshot: bool,
) -> None:
    """覆盖领取与加载快照之间、供应商执行期间两个删除时机。"""
    run_id = await seed_run(engine, settings)
    factory = create_session_factory(engine)
    invoked = False

    async def remove_conversation() -> None:
        async with factory() as session, session.begin():
            run = await session.get(AgentRun, run_id)
            assert run is not None
            conversation = await session.get(Conversation, run.conversation_id)
            assert conversation is not None
            await delete_conversation(
                session,
                conversation_id=conversation.id,
                tenant_id=conversation.tenant_id,
                user_id=conversation.user_id,
            )

    original_snapshot = worker_runtime._load_snapshot

    async def load_snapshot(
        session: AsyncSession, *, run_id: UUID, settings: Settings, **kwargs: object
    ) -> AgentRuntimeSnapshot:
        await remove_conversation()
        return await original_snapshot(session, run_id=run_id, settings=settings, **kwargs)

    async def invoke(*args: object, **kwargs: object) -> SupportAnswer:
        nonlocal invoked
        invoked = True
        await remove_conversation()
        return SupportAnswer(answer="不应追加的晚到回复")

    if delete_before_snapshot:
        monkeypatch.setattr(worker_runtime, "_load_snapshot", load_snapshot)
    monkeypatch.setattr(worker_runtime, "invoke_agent", invoke)
    assert not await worker_runtime.execute_agent_run(factory, run_id, settings=settings)
    assert invoked is not delete_before_snapshot
    async with factory() as session:
        run = await session.get(AgentRun, run_id)
        assert run is not None and run.status == RunStatus.CANCELLED and run.error_code is None
        assert (
            await session.scalars(
                select(Message).where(
                    Message.conversation_id == run.conversation_id,
                    Message.role == MessageRole.ASSISTANT,
                )
            )
        ).all() == []
        events = list(
            (
                await session.scalars(
                    select(RunEvent).where(RunEvent.run_id == run_id).order_by(RunEvent.sequence)
                )
            ).all()
        )
        assert events[-1].event_type == "run.cancelled"


async def test_runtime_observations_are_persisted_with_parent_trace(
    engine: AsyncEngine, settings: Settings, monkeypatch: MonkeyPatch
) -> None:
    run_id = await seed_run(engine, settings)
    session_factory = create_session_factory(engine)

    async def fake_invoke(*args: object, **kwargs: object) -> AgentExecutionResult:
        now = datetime.now(UTC)
        return AgentExecutionResult(
            answer=SupportAnswer(answer="带观测的回答"),
            input_tokens=10,
            output_tokens=5,
            observations=(
                AgentObservationResult(
                    kind="llm",
                    name="model.generate",
                    status="completed",
                    started_at=now,
                    finished_at=now,
                    input_tokens=10,
                    output_tokens=5,
                    metadata={"provider_request_id": "req-1", "content": "must-drop"},
                ),
            ),
        )

    monkeypatch.setattr(worker_runtime, "invoke_agent", fake_invoke)
    assert (
        await worker_runtime.execute_agent_run(session_factory, run_id, settings=settings) is True
    )

    async with session_factory() as session:
        observations = list(
            (
                await session.scalars(
                    select(AgentRunObservation)
                    .where(AgentRunObservation.run_id == run_id)
                    .order_by(AgentRunObservation.started_at, AgentRunObservation.id)
                )
            ).all()
        )
    assert len(observations) == 2
    parent, child = observations
    assert parent.kind == "agent"
    assert child.kind == "llm"
    assert child.parent_observation_id == parent.id
    assert child.trace_id == parent.trace_id
    assert child.metadata_payload == {"provider_request_id": "req-1"}


async def test_runtime_health_uses_service_lease_and_persists_reason(
    engine: AsyncEngine, settings: Settings
) -> None:
    run_id = await seed_run(engine, settings)
    session_factory = create_session_factory(engine)
    now = datetime.now(UTC)
    async with session_factory() as session, session.begin():
        run = await session.get(AgentRun, run_id)
        assert run is not None
        run.status = RunStatus.COMPLETED
        run.started_at = now
        run.finished_at = now
        run.end_to_end_latency_ms = 120
        session.add(
            RuntimeServiceLease(
                instance_id=f"health-test-{run_id}",
                heartbeat_at=now,
                expires_at=now.replace(microsecond=0),
                service_status="healthy",
            )
        )
        tenant_id = run.tenant_id
    # 过期租约应明确返回 unknown，而不是误报为 healthy。
    async with session_factory() as session, session.begin():
        snapshots = await refresh_agent_runtime_health(session, tenant_id=tenant_id, now=now)
        assert len(snapshots) == 1
        assert snapshots[0].health_status == "unknown"
        assert "租约" in snapshots[0].health_reason

    async with session_factory() as session, session.begin():
        lease = await session.scalar(
            select(RuntimeServiceLease).where(
                RuntimeServiceLease.instance_id == f"health-test-{run_id}"
            )
        )
        assert lease is not None
        lease.expires_at = now.replace(microsecond=0) + timedelta(minutes=1)
        snapshots = await refresh_agent_runtime_health(session, tenant_id=tenant_id, now=now)
        assert snapshots[0].health_status == "unknown"
        assert "样本不足" in snapshots[0].health_reason
        assert snapshots[0].error_rate is None
        assert snapshots[0].p95_latency_ms is None


async def test_recovery_finds_only_queued_runs(
    engine: AsyncEngine, settings: Settings, monkeypatch: MonkeyPatch
) -> None:
    queued_run_id = await seed_run(engine, settings)
    completed_run_id = await seed_run(engine, settings)
    session_factory = create_session_factory(engine)

    async def fake_invoke(*args: object, **kwargs: object) -> SupportAnswer:
        return SupportAnswer(answer="测试回答")

    monkeypatch.setattr(worker_runtime, "invoke_agent", fake_invoke)
    await worker_runtime.execute_agent_run(session_factory, completed_run_id, settings=settings)

    # 旧记录没有 Outbox 时仍须恢复，新记录由持久通知分发负责。
    async with session_factory() as session, session.begin():
        queued = await session.get(AgentRun, queued_run_id)
        assert queued is not None
        original = await session.get(Message, queued.input_message_id)
        assert original is not None
        message = Message(
            tenant_id=queued.tenant_id,
            conversation_id=queued.conversation_id,
            user_id=original.user_id,
            role=MessageRole.USER,
            content="legacy",
            content_hash="0" * 64,
            idempotency_key=str(uuid4()),
            correlation_id="legacy-recovery",
            turn_sequence=2,
        )
        session.add(message)
        await session.flush()
        legacy = AgentRun(
            tenant_id=queued.tenant_id,
            conversation_id=queued.conversation_id,
            agent_id=queued.agent_id,
            agent_version_id=queued.agent_version_id,
            model_endpoint_version_id=queued.model_endpoint_version_id,
            credential_revision=queued.credential_revision,
            config_digest=queued.config_digest,
            identity_roles=queued.identity_roles,
            input_message_id=message.id,
            input_sequence=2,
            correlation_id="legacy-recovery",
        )
        session.add(legacy)
        await session.flush()
        legacy_run_id = legacy.id

    recovered = await recover_queued_runs(session_factory)

    assert legacy_run_id in recovered
    assert queued_run_id not in recovered
    assert completed_run_id not in recovered


@pytest.mark.parametrize("revoke_original", [False, True])
async def test_queued_run_keeps_fixed_revision_and_honors_explicit_revocation(
    engine: AsyncEngine,
    settings: Settings,
    monkeypatch: MonkeyPatch,
    revoke_original: bool,
) -> None:
    run_id = await seed_run(engine, settings)
    session_factory = create_session_factory(engine)
    provider = LocalEnvelopeSecretProvider(settings.secret_encryption_key)

    async with session_factory() as session, session.begin():
        run = await session.get(AgentRun, run_id)
        assert run is not None
        credential = await session.scalar(
            select(ModelCredential).where(ModelCredential.tenant_id == run.tenant_id)
        )
        user = await session.scalar(select(User).where(User.tenant_id == run.tenant_id))
        assert credential is not None and user is not None
        await rotate_model_credential(
            session,
            endpoint_id=credential.endpoint_id,
            tenant_id=run.tenant_id,
            actor_user_id=user.id,
            api_key="rotated-api-key",
            secret_provider=provider,
            correlation_id="credential-rotation-test",
        )
        assert run.credential_revision == 1
        assert credential.revision == 2
        if revoke_original:
            await revoke_model_credential_revision(
                session,
                tenant_id=run.tenant_id,
                endpoint_id=credential.endpoint_id,
                revision=1,
                expected_current_revision=2,
                actor_user_id=user.id,
                reason="test revocation",
                correlation_id="credential-revocation-test",
            )

    invoked = False

    async def fixed_revision_model(
        snapshot: AgentRuntimeSnapshot, **kwargs: object
    ) -> SupportAnswer:
        nonlocal invoked
        invoked = True
        assert snapshot.api_key == "test-api-key"
        return SupportAnswer(answer="固定 revision 的模拟回答")

    monkeypatch.setattr(worker_runtime, "invoke_agent", fixed_revision_model)
    assert (
        await worker_runtime.execute_agent_run(
            session_factory,
            run_id,
            settings=settings,
        )
        is not revoke_original
    )
    assert invoked is not revoke_original
    async with session_factory() as session:
        run = await session.get(AgentRun, run_id)
        assert run is not None and run.credential_revision == 1
        if revoke_original:
            assert run.status == RunStatus.FAILED
            assert run.error_code == "MODEL_CREDENTIAL_REVOKED"
            assert run.current_attempt_id is None
        else:
            assert run.status == RunStatus.COMPLETED


async def test_worker_maps_runtime_failures_to_stable_terminal_codes(
    engine: AsyncEngine, settings: Settings, monkeypatch: MonkeyPatch
) -> None:
    settings = settings.model_copy(update={"run_attempt_max_recoveries": 0})
    failures = [
        (AgentReferenceError("missing"), "AGENT_VERSION_REFERENCE_INVALID"),
        (AgentSecretResolutionError("secret"), "MODEL_CREDENTIAL_UNAVAILABLE"),
        (AgentCapabilityError("capability"), "MODEL_CAPABILITY_INCOMPATIBLE"),
        (AgentStructuredOutputError("structured"), "AGENT_STRUCTURED_OUTPUT_INVALID"),
        (AgentCheckpointError("checkpoint"), "AGENT_CHECKPOINT_FAILED"),
        (TimeoutError(), "AGENT_RUN_TIMEOUT"),
    ]
    session_factory = create_session_factory(engine)

    for failure, expected_code in failures:
        run_id = await seed_run(engine, settings)

        async def fail_snapshot(
            *args: object, _failure: Exception = failure, **kwargs: object
        ) -> None:
            raise _failure

        monkeypatch.setattr(worker_runtime, "_load_snapshot", fail_snapshot)
        completed = await worker_runtime.execute_agent_run(
            session_factory, run_id, settings=settings
        )

        async with session_factory() as session:
            run = await session.get(AgentRun, run_id)
            failed_event = await session.scalar(
                select(RunEvent).where(
                    RunEvent.run_id == run_id,
                    RunEvent.event_type == "run.failed",
                )
            )
        assert completed is False
        assert run is not None and run.status == RunStatus.FAILED
        assert run.error_code == expected_code
        assert failed_event is not None
        assert failed_event.data == {
            "status": "failed",
            "code": expected_code,
            "message": runtime_error_message(expected_code),
        }


async def test_failed_callback_observation_is_persisted(
    engine: AsyncEngine, settings: Settings, monkeypatch: MonkeyPatch
) -> None:
    run_id = await seed_run(engine, settings)
    session_factory = create_session_factory(engine)

    async def fail_invoke(*args: object, **kwargs: object) -> None:
        callback = kwargs.get("on_observations")
        assert callable(callback)
        now = datetime.now(UTC)
        callback(
            (
                AgentObservationResult(
                    kind="llm",
                    name="model.generate",
                    status="failed",
                    started_at=now,
                    finished_at=now,
                    error_code="MODEL_CALL_FAILED",
                ),
            )
        )
        raise RuntimeError("provider failure")

    monkeypatch.setattr(worker_runtime, "invoke_agent", fail_invoke)
    assert (
        await worker_runtime.execute_agent_run(session_factory, run_id, settings=settings) is False
    )

    async with session_factory() as session:
        observations = list(
            (
                await session.scalars(
                    select(AgentRunObservation)
                    .where(AgentRunObservation.run_id == run_id)
                    .order_by(AgentRunObservation.started_at, AgentRunObservation.id)
                )
            ).all()
        )
    assert len(observations) == 2
    assert observations[1].kind == "llm"
    assert observations[1].status == "failed"
    assert observations[1].error_code == "MODEL_CALL_FAILED"


async def test_product_message_write_failure_never_marks_run_completed(
    engine: AsyncEngine, settings: Settings, monkeypatch: MonkeyPatch
) -> None:
    settings = settings.model_copy(update={"run_attempt_max_recoveries": 0})
    run_id = await seed_run(engine, settings)
    session_factory = create_session_factory(engine)

    async def fake_invoke(*args: object, **kwargs: object) -> SupportAnswer:
        return SupportAnswer(answer="不得提交的不完整回答")

    def fail_product_write(content: str) -> str:
        raise RuntimeError("simulated product message write failure")

    monkeypatch.setattr(worker_runtime, "invoke_agent", fake_invoke)
    monkeypatch.setattr(product_message_services, "content_digest", fail_product_write)

    completed = await worker_runtime.execute_agent_run(session_factory, run_id, settings=settings)

    async with session_factory() as session:
        run = await session.get(AgentRun, run_id)
        assert run is not None
        assistant_messages = list(
            (
                await session.scalars(
                    select(Message).where(
                        Message.conversation_id == run.conversation_id,
                        Message.role == MessageRole.ASSISTANT,
                    )
                )
            ).all()
        )
        events = list(
            (await session.scalars(select(RunEvent).where(RunEvent.run_id == run_id))).all()
        )

    assert completed is False
    assert run.status == RunStatus.FAILED
    assert run.error_code == "AGENT_PRODUCT_STATE_COMMIT_FAILED"
    assert assistant_messages == []
    assert not any(event.event_type == "run.completed" for event in events)
    assert any(event.event_type == "run.failed" for event in events)


async def test_long_model_call_renews_attempt_beyond_original_lease(
    engine: AsyncEngine,
    settings: Settings,
    monkeypatch: MonkeyPatch,
) -> None:
    settings = Settings(
        **{
            **settings.model_dump(),
            "run_attempt_lease_seconds": 10,
            "run_attempt_heartbeat_seconds": 0.1,
        }
    )
    run_id = await seed_run(engine, settings)
    factory = create_session_factory(engine)
    entered = asyncio.Event()
    finish = asyncio.Event()

    async def long_model(*args: object, **kwargs: object) -> SupportAnswer:
        entered.set()
        await finish.wait()
        return SupportAnswer(answer="跨原租约完成的模拟回答")

    monkeypatch.setattr(worker_runtime, "invoke_agent", long_model)
    execution = asyncio.create_task(
        worker_runtime.execute_agent_run(factory, run_id, settings=settings)
    )
    try:
        await asyncio.wait_for(entered.wait(), timeout=3)
        async with factory() as session:
            run = await session.get(AgentRun, run_id)
            assert run is not None
            attempt = await session.get(RunAttempt, run.current_attempt_id)
            assert attempt is not None
            original_expiry = attempt.lease_expires_at.replace(tzinfo=UTC)
            attempt_id = attempt.id
        await asyncio.sleep(max(0, (original_expiry - datetime.now(UTC)).total_seconds()) + 0.05)
        assert not execution.done()
        async with factory() as session:
            run = await session.get(AgentRun, run_id)
            attempt = await session.get(RunAttempt, attempt_id)
            assert run is not None and run.status == RunStatus.RUNNING
            assert attempt is not None and attempt.lease_expires_at.replace(
                tzinfo=UTC
            ) > datetime.now(UTC)
        finish.set()
        assert await asyncio.wait_for(execution, timeout=3)
    finally:
        finish.set()
        await asyncio.gather(execution, return_exceptions=True)


async def test_attempt_heartbeat_failure_cancels_model_without_late_answer(
    engine: AsyncEngine,
    settings: Settings,
    monkeypatch: MonkeyPatch,
) -> None:
    settings = settings.model_copy(update={"run_attempt_heartbeat_seconds": 0.05})
    run_id = await seed_run(engine, settings)
    factory = create_session_factory(engine)
    entered = asyncio.Event()
    cancelled = asyncio.Event()
    renew = worker_runtime.check_and_renew_attempt

    async def blocked_model(*args: object, **kwargs: object) -> SupportAnswer:
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()
        raise AssertionError("阻塞模型不能正常返回")

    async def fail_after_entering(*args: object, **kwargs: object) -> None:
        if entered.is_set():
            raise OSError("isolated attempt heartbeat fault")
        await renew(*args, **kwargs)

    monkeypatch.setattr(worker_runtime, "invoke_agent", blocked_model)
    monkeypatch.setattr(worker_runtime, "check_and_renew_attempt", fail_after_entering)
    assert not await asyncio.wait_for(
        worker_runtime.execute_agent_run(factory, run_id, settings=settings),
        timeout=3,
    )
    assert cancelled.is_set()
    async with factory() as session:
        run = await session.get(AgentRun, run_id)
        assert run is not None and run.status == RunStatus.FAILED
        assert run.error_code == "AGENT_RUNTIME_ERROR"
        assert not (
            await session.scalars(
                select(Message).where(
                    Message.conversation_id == run.conversation_id,
                    Message.role == MessageRole.ASSISTANT,
                )
            )
        ).all()
        conversation = await session.get(Conversation, run.conversation_id)
        assert conversation is not None and conversation.active_attempt_id is None


@pytest.mark.parametrize("offset_ms", [-1, 0, 1])
async def test_attempt_expiry_uses_exact_utc_boundary(
    engine: AsyncEngine,
    settings: Settings,
    monkeypatch: MonkeyPatch,
    offset_ms: int,
) -> None:
    run_id = await seed_run(engine, settings)
    factory = create_session_factory(engine)
    async with factory() as session, session.begin():
        claim = await claim_run_attempt(session, run_id, owner_id="clock-test", lease_seconds=10)
        assert claim is not None
        observed = claim.attempt.lease_expires_at + timedelta(milliseconds=offset_ms)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return observed.astimezone(tz)

    monkeypatch.setattr(run_attempt_services, "datetime", Clock)
    async with factory() as session, session.begin():
        if offset_ms < 0:
            assert (await owned_run(session, claim.identity)).attempt.id == claim.attempt.id
        else:
            with pytest.raises(RunLeaseLostError):
                await owned_run(session, claim.identity)
