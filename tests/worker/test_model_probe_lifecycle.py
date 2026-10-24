"""持久化模型探测的故障、取消和旧执行者围栏；供应商使用 HTTP Mock。"""

import asyncio
from collections.abc import AsyncIterator
from datetime import timedelta
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.orm import Session
from supportops_core import model_probe_services as probes
from supportops_core import model_services
from supportops_core.config import Settings
from supportops_core.db import create_session_factory
from supportops_core.enums import ModelProviderKind, ModelTestStatus, ModelVerificationStatus
from supportops_core.model_services import (
    ModelEndpointSelection,
    list_model_endpoint_models,
    save_model_endpoint_configuration,
    start_model_test_run,
)
from supportops_core.models import (
    ModelEndpointTestRun,
    ModelEndpointVersion,
    ModelProbeJob,
    User,
    utc_now,
)
from supportops_core.secrets import LocalEnvelopeSecretProvider

from tests.conftest import auth_headers


async def seed_probe(engine: AsyncEngine, settings: Settings) -> ModelEndpointTestRun:
    tenant_id = uuid4()
    await auth_headers(engine, settings, tenant_id, roles=("platform_admin",))
    async with create_session_factory(engine)() as session, session.begin():
        actor = await session.scalar(select(User).where(User.tenant_id == tenant_id))
        assert actor is not None
        endpoint, _ = await save_model_endpoint_configuration(
            session,
            endpoint_id=None,
            tenant_id=tenant_id,
            actor_user_id=actor.id,
            name="隔离探测",
            provider_preset=None,
            provider_kind=ModelProviderKind.OPENAI_COMPATIBLE,
            base_url="https://probe.example/v1",
            api_key="probe-test-only",
            models=[
                ModelEndpointSelection(
                    id=None,
                    upstream_model_id="mock-model",
                    extension_options={},
                )
            ],
            request_enable=False,
            secret_provider=LocalEnvelopeSecretProvider(settings.secret_encryption_key),
            allow_private_networks=False,
            allowed_hosts=[],
            correlation_id="probe-create",
        )
        models = await list_model_endpoint_models(
            session, endpoint_id=endpoint.id, tenant_id=tenant_id
        )
        return await start_model_test_run(
            session,
            endpoint_id=endpoint.id,
            endpoint_model_id=models[0].id,
            tenant_id=tenant_id,
            actor_user_id=actor.id,
            correlation_id="probe-run",
        )


@pytest.fixture(autouse=True)
def require_http_mock(monkeypatch: pytest.MonkeyPatch) -> None:
    async def reject_unmocked(*args: object, **kwargs: object) -> None:
        raise AssertionError("探测测试必须显式提供 HTTP Mock")

    monkeypatch.setattr(probes, "execute_model_test_run", reject_unmocked)


def bind_http_mock(monkeypatch: pytest.MonkeyPatch, client: httpx.AsyncClient) -> None:
    async def resolve(*args: object, **kwargs: object) -> list[str]:
        return ["203.0.113.10"]

    async def execute(session, **kwargs) -> None:
        await model_services.execute_model_test_run(session, **kwargs, client=client)

    monkeypatch.setattr(model_services, "resolve_and_validate_endpoint", resolve)
    monkeypatch.setattr(probes, "execute_model_test_run", execute)


async def test_probe_claim_rollback_preserves_one_queued_job(
    engine: AsyncEngine,
    settings: Settings,
) -> None:
    test = await seed_probe(engine, settings)
    factory = create_session_factory(engine)
    with pytest.raises(RuntimeError, match="claim commit fault"):
        async with factory() as session, session.begin():
            assert await probes.claim_probe(session, settings=settings, owner_id="lost-owner")
            raise RuntimeError("claim commit fault")
    async with factory() as session:
        job = await session.get(ModelProbeJob, test.id)
        assert job is not None and job.status == "queued" and job.attempt_count == 0
        assert job.generation == 0 and job.owner_id is None
        assert await session.scalar(select(func.count()).select_from(ModelProbeJob)) == 1
    async with factory() as session, session.begin():
        identity = await probes.claim_probe(session, settings=settings, owner_id="new-owner")
        assert identity is not None and identity.job_id == test.id and identity.generation == 1


@pytest.mark.parametrize("boundary", ["cancel", "takeover"])
async def test_late_stream_cannot_overwrite_cancellation_or_new_generation(
    engine: AsyncEngine,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    boundary: str,
) -> None:
    settings = settings.model_copy(update={"model_probe_max_attempts": 2})
    test = await seed_probe(engine, settings)
    factory = create_session_factory(engine)
    async with factory() as session, session.begin():
        identity = await probes.claim_probe(session, settings=settings, owner_id="old-owner")
        assert identity is not None
    entered, release = asyncio.Event(), asyncio.Event()

    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self) -> AsyncIterator[bytes]:
            yield b'data: {"choices":[{"delta":{"content":"first"}}]}\n\n'
            entered.set()
            await release.wait()
            yield b'data: {"choices":[{"delta":{"content":"late"}}]}\n\n'
            yield b"data: [DONE]\n\n"

    transport = httpx.MockTransport(
        lambda _: httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            stream=Stream(),
        )
    )
    async with httpx.AsyncClient(transport=transport) as client:
        bind_http_mock(monkeypatch, client)
        execution = asyncio.create_task(
            probes.execute_probe(factory, identity=identity, settings=settings)
        )
        try:
            await asyncio.wait_for(entered.wait(), timeout=3)
            async with factory() as session, session.begin():
                if boundary == "cancel":
                    await probes.cancel_model_probe(
                        session,
                        test_run_id=test.id,
                        tenant_id=test.tenant_id,
                        settings=settings,
                    )
                else:
                    job = await session.get(ModelProbeJob, test.id)
                    assert job is not None
                    job.lease_expires_at = utc_now() - timedelta(seconds=1)
            if boundary == "takeover":
                async with factory() as session, session.begin():
                    assert await probes.recover_expired_probes(session, settings=settings) == 1
                    job = await session.get(ModelProbeJob, test.id)
                    assert job is not None
                    job.next_attempt_at = utc_now() - timedelta(seconds=1)
                async with factory() as session, session.begin():
                    newer = await probes.claim_probe(
                        session, settings=settings, owner_id="new-owner"
                    )
                    assert newer is not None and newer.generation > identity.generation
            release.set()
            await asyncio.wait_for(execution, timeout=3)
        finally:
            release.set()
            await asyncio.gather(execution, return_exceptions=True)
    async with factory() as session:
        job = await session.get(ModelProbeJob, test.id)
        stored = await session.get(ModelEndpointTestRun, test.id)
        version = await session.get(ModelEndpointVersion, test.model_version_id)
        assert job is not None and stored is not None and version is not None
        assert version.verification_status != ModelVerificationStatus.VERIFIED
        assert job.generation > identity.generation
        if boundary == "cancel":
            assert job.status == "cancelled" and stored.status == ModelTestStatus.CANCELLED
            assert job.attempt_count == 1
        else:
            assert job.status == "running" and job.owner_id == "new-owner"
            assert stored.status == ModelTestStatus.QUEUED and job.attempt_count == 2
    if boundary == "takeover":
        async with factory() as session, session.begin():
            job = await session.get(ModelProbeJob, test.id)
            assert job is not None
            job.lease_expires_at = utc_now() - timedelta(seconds=1)
        async with factory() as session, session.begin():
            assert await probes.recover_expired_probes(session, settings=settings) == 1
        async with factory() as session:
            job = await session.get(ModelProbeJob, test.id)
            assert job is not None and job.status == "failed" and job.owner_id is None
            assert len(job.attempt_history) == 2


async def test_dependency_failure_retries_same_job_then_exhausts(
    engine: AsyncEngine,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = settings.model_copy(update={"model_probe_max_attempts": 2})
    test = await seed_probe(engine, settings)
    factory = create_session_factory(engine)
    calls = 0

    def unavailable(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(503, json={"error": "isolated upstream unavailable"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(unavailable)) as client:
        bind_http_mock(monkeypatch, client)
        for attempt in range(1, 3):
            async with factory() as session, session.begin():
                job = await session.get(ModelProbeJob, test.id)
                assert job is not None
                job.next_attempt_at = utc_now() - timedelta(seconds=1)
            async with factory() as session, session.begin():
                identity = await probes.claim_probe(
                    session, settings=settings, owner_id=f"owner-{attempt}"
                )
                assert identity is not None and identity.job_id == test.id
            await probes.execute_probe(factory, identity=identity, settings=settings)
            async with factory() as session:
                job = await session.get(ModelProbeJob, test.id)
                stored = await session.get(ModelEndpointTestRun, test.id)
                assert job is not None and stored is not None
                assert job.status == ("queued" if attempt == 1 else "failed")
                assert stored.status == (
                    ModelTestStatus.QUEUED if attempt == 1 else ModelTestStatus.FAILED
                )
                assert job.attempt_count == attempt and len(job.attempt_history) == attempt
                assert job.owner_id is None and job.error_code == "MODEL_PROBE_DEPENDENCY_TEMPORARY"
    assert calls == 2


@pytest.mark.parametrize("fault", ["timeout", "progress_commit"])
async def test_probe_failure_has_bounded_explanatory_state(
    engine: AsyncEngine,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    fault: str,
) -> None:
    settings = settings.model_copy(
        update={
            "model_probe_run_timeout_seconds": 0.1,
            "model_probe_max_attempts": 1,
        }
    )
    test = await seed_probe(engine, settings)
    factory = create_session_factory(engine)
    async with factory() as session, session.begin():
        identity = await probes.claim_probe(session, settings=settings, owner_id="failure-owner")
        assert identity is not None
    failed = False

    def fail_progress(session: Session) -> None:
        nonlocal failed
        if not failed and any(isinstance(item, ModelEndpointTestRun) for item in session.dirty):
            failed = True
            raise OSError("isolated probe progress commit fault")

    async def blocked(*args: object, **kwargs: object) -> None:
        await asyncio.Event().wait()

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(500),
        )
    ) as client:
        if fault == "timeout":
            monkeypatch.setattr(probes, "execute_model_test_run", blocked)
        else:
            bind_http_mock(monkeypatch, client)
            event.listen(Session, "before_commit", fail_progress)
        try:
            await asyncio.wait_for(
                probes.execute_probe(factory, identity=identity, settings=settings), timeout=3
            )
        finally:
            if fault == "progress_commit":
                event.remove(Session, "before_commit", fail_progress)
    async with factory() as session:
        job = await session.get(ModelProbeJob, test.id)
        stored = await session.get(ModelEndpointTestRun, test.id)
        assert job is not None and stored is not None
        assert job.status == "failed" and stored.status == ModelTestStatus.FAILED
        assert job.owner_id is None and job.lease_expires_at is None
        assert job.error_code == (
            "MODEL_PROBE_TIMEOUT" if fault == "timeout" else "MODEL_PROBE_EXECUTION_FAILED"
        )
        assert len(job.attempt_history) == 1
