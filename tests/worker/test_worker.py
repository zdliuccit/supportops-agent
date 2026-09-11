from uuid import UUID, uuid4

from pytest import MonkeyPatch
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine
from supportops_core import worker_runtime
from supportops_core.agent_config import SupportAnswer
from supportops_core.agent_runtime import (
    AgentCapabilityError,
    AgentCheckpointError,
    AgentReferenceError,
    AgentSecretResolutionError,
    AgentStructuredOutputError,
)
from supportops_core.config import Settings
from supportops_core.db import create_session_factory
from supportops_core.enums import MessageRole, RunStatus
from supportops_core.model_services import rotate_model_credential
from supportops_core.models import (
    Agent,
    AgentRun,
    Conversation,
    Message,
    ModelCredential,
    RunEvent,
    User,
)
from supportops_core.secrets import LocalEnvelopeSecretProvider
from supportops_core.services import create_message_and_run, recover_queued_runs

from tests.conftest import provision_agent


async def seed_run(engine: AsyncEngine, settings: Settings) -> UUID:
    tenant_id = uuid4()
    agent_id = await provision_agent(engine, settings, tenant_id, subject="worker-user")
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
            content="test",
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

    recovered = await recover_queued_runs(session_factory)

    assert queued_run_id in recovered
    assert completed_run_id not in recovered


async def test_queued_run_keeps_exact_credential_revision_after_rotation(
    engine: AsyncEngine, settings: Settings
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

    async with session_factory() as session:
        snapshot = await worker_runtime._load_snapshot(session, run_id=run_id, settings=settings)

    assert snapshot.api_key == "test-api-key"


async def test_worker_maps_runtime_failures_to_stable_terminal_codes(
    engine: AsyncEngine, settings: Settings, monkeypatch: MonkeyPatch
) -> None:
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
        assert failed_event.data == {"status": "failed", "code": expected_code}


async def test_product_message_write_failure_never_marks_run_completed(
    engine: AsyncEngine, settings: Settings, monkeypatch: MonkeyPatch
) -> None:
    run_id = await seed_run(engine, settings)
    session_factory = create_session_factory(engine)

    async def fake_invoke(*args: object, **kwargs: object) -> SupportAnswer:
        return SupportAnswer(answer="不得提交的不完整回答")

    def fail_product_write(content: str) -> str:
        raise RuntimeError("simulated product message write failure")

    monkeypatch.setattr(worker_runtime, "invoke_agent", fake_invoke)
    monkeypatch.setattr(worker_runtime, "content_digest", fail_product_write)

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
            (
                await session.scalars(select(RunEvent).where(RunEvent.run_id == run_id))
            ).all()
        )

    assert completed is False
    assert run.status == RunStatus.FAILED
    assert run.error_code == "AGENT_PRODUCT_STATE_COMMIT_FAILED"
    assert assistant_messages == []
    assert not any(event.event_type == "run.completed" for event in events)
    assert any(event.event_type == "run.failed" for event in events)
