"""端点探测的独立持久化任务、租约、有限重试与取消。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

import structlog

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from supportops_core.config import Settings
from supportops_core.enums import ModelTestStatus
from supportops_core.model_services import ModelProbeLeaseLostError, execute_model_test_run
from supportops_core.models import ModelCredentialRevision, ModelEndpoint, ModelEndpointTestRun, ModelEndpointVersion, ModelProbeJob, Tenant, User
from supportops_core.secrets import LocalEnvelopeSecretProvider
from supportops_core.services import ResourceNotFoundError

logger = structlog.get_logger()


class ModelProbeAuthorizationDenied(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ProbeIdentity:
    job_id: UUID
    test_run_id: UUID
    tenant_id: UUID
    owner_id: str
    generation: int


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


async def _owned_job(session: AsyncSession, identity: ProbeIdentity) -> ModelProbeJob:
    job = await session.scalar(select(ModelProbeJob).where(ModelProbeJob.id == identity.job_id,
        ModelProbeJob.tenant_id == identity.tenant_id).execution_options(populate_existing=True).with_for_update())
    if (job is None or job.status != "running" or job.owner_id != identity.owner_id or job.generation != identity.generation
        or job.cancel_requested or job.lease_expires_at is None or _aware(job.lease_expires_at) <= datetime.now(UTC)):
        raise ModelProbeLeaseLostError("探测 owner/generation/租约或取消状态已变化")
    return job


async def _authorize(session: AsyncSession, job: ModelProbeJob, run: ModelEndpointTestRun) -> None:
    user = await session.scalar(select(User).where(User.id == run.created_by, User.tenant_id == job.tenant_id,
        User.status == "active").execution_options(populate_existing=True))
    tenant = await session.scalar(select(Tenant).where(Tenant.id == job.tenant_id, Tenant.status == "active")
        .execution_options(populate_existing=True))
    endpoint = await session.scalar(select(ModelEndpoint.id).where(ModelEndpoint.id == job.endpoint_id, ModelEndpoint.tenant_id == job.tenant_id))
    fixed_reference = await session.scalar(select(ModelEndpointVersion.id).join(ModelCredentialRevision,
        ModelCredentialRevision.credential_id == ModelEndpointVersion.credential_id).where(
        ModelEndpointVersion.id == job.model_version_id, ModelEndpointVersion.tenant_id == job.tenant_id,
        ModelEndpointVersion.endpoint_id == job.endpoint_id, ModelEndpointVersion.endpoint_model_id == job.endpoint_model_id,
        ModelCredentialRevision.tenant_id == job.tenant_id, ModelCredentialRevision.revision == job.credential_revision))
    if user is None or "platform_admin" not in user.roles or tenant is None or endpoint is None or fixed_reference is None:
        raise ModelProbeAuthorizationDenied("探测主体、租户或端点授权已失效")
    if (run.id != job.test_run_id or run.tenant_id != job.tenant_id or run.endpoint_id != job.endpoint_id or run.endpoint_model_id != job.endpoint_model_id
        or run.model_version_id != job.model_version_id or run.credential_revision != job.credential_revision):
        raise ModelProbeAuthorizationDenied("探测固定引用与产品记录不一致")


def _settle(job: ModelProbeJob, run: ModelEndpointTestRun, *, settings: Settings, status: str, code: str | None = None) -> None:
    """已消耗的领取次数不回退，未知/超时只在明确无业务副作用的探测中有限重试。"""
    now = datetime.now(UTC)
    if job.attempt_count > 0 and not any(item.get("generation") == job.generation for item in job.attempt_history):
        job.attempt_history = [*job.attempt_history, {"attempt": job.attempt_count, "generation": job.generation,
            "status": status, "stage": job.stage, "error_code": code, "finished_at": now.isoformat()}]
    job.error_code = code
    retry = (status == "failed" and code in {"MODEL_PROBE_DEPENDENCY_TEMPORARY", "MODEL_PROBE_LEASE_EXPIRED", "MODEL_PROBE_TIMEOUT"}
        and job.attempt_count < settings.model_probe_max_attempts
        and (now - _aware(job.created_at)).total_seconds() < settings.model_probe_max_age_seconds and not job.cancel_requested)
    job.owner_id, job.lease_expires_at = None, None
    if retry:
        job.status, job.stage = "queued", "retry_queued"
        job.next_attempt_at = now + timedelta(seconds=min(30, 2 ** job.attempt_count))
        run.status, run.stage, run.completed_at = ModelTestStatus.QUEUED, "retry_queued", None
        run.error_code, run.error_message = code, "探测依赖暂不可用，已安排有限重试；累计次数不会重置。"
    else:
        job.status, job.finished_at = status, now
        run.status = {"succeeded": ModelTestStatus.PASSED, "cancelled": ModelTestStatus.CANCELLED}.get(status, ModelTestStatus.FAILED)
        run.completed_at = run.completed_at or now
        if code is not None:
            run.error_code = code
            run.error_message = "探测已取消。" if status == "cancelled" else "探测未完成；请检查权限、端点与安全错误码后重新发起。"
        job.stage = run.stage = "completed" if status == "succeeded" else "cancelled" if status == "cancelled" else "failed"


async def recover_expired_probes(session: AsyncSession, *, settings: Settings) -> int:
    jobs = (await session.scalars(select(ModelProbeJob).where(ModelProbeJob.status == "running",
        ModelProbeJob.lease_expires_at <= datetime.now(UTC)).order_by(ModelProbeJob.lease_expires_at, ModelProbeJob.id)
        .limit(20).with_for_update(skip_locked=True))).all()
    for job in jobs:
        run = await session.scalar(select(ModelEndpointTestRun).where(ModelEndpointTestRun.id == job.test_run_id,
            ModelEndpointTestRun.tenant_id == job.tenant_id).with_for_update())
        if run is None:
            job.status, job.error_code, job.finished_at = "failed", "MODEL_ENDPOINT_REFERENCE_MISSING", datetime.now(UTC)
            job.owner_id, job.lease_expires_at = None, None
            continue
        _settle(job, run, settings=settings, status="cancelled" if job.cancel_requested else "failed",
            code="MODEL_PROBE_CANCELLED" if job.cancel_requested else "MODEL_PROBE_LEASE_EXPIRED")
    return len(jobs)


async def claim_probe(session: AsyncSession, *, settings: Settings, owner_id: str) -> ProbeIdentity | None:
    job = await session.scalar(select(ModelProbeJob).where(ModelProbeJob.status == "queued", ModelProbeJob.next_attempt_at <= datetime.now(UTC))
        .order_by(ModelProbeJob.next_attempt_at, ModelProbeJob.created_at, ModelProbeJob.id).limit(1).with_for_update(skip_locked=True))
    if job is None:
        return None
    run = await session.scalar(select(ModelEndpointTestRun).where(ModelEndpointTestRun.id == job.test_run_id,
        ModelEndpointTestRun.tenant_id == job.tenant_id).with_for_update())
    if run is None:
        job.status, job.error_code, job.finished_at = "failed", "MODEL_ENDPOINT_REFERENCE_MISSING", datetime.now(UTC)
        return None
    try:
        await _authorize(session, job, run)
    except ModelProbeAuthorizationDenied:
        _settle(job, run, settings=settings, status="failed", code="MODEL_PROBE_AUTHORIZATION_DENIED")
        return None
    if job.cancel_requested:
        _settle(job, run, settings=settings, status="cancelled", code="MODEL_PROBE_CANCELLED")
        return None
    if job.attempt_count >= settings.model_probe_max_attempts or (datetime.now(UTC) - _aware(job.created_at)).total_seconds() >= settings.model_probe_max_age_seconds:
        _settle(job, run, settings=settings, status="failed", code="MODEL_PROBE_LIMIT_EXCEEDED")
        return None
    job.status, job.stage, job.owner_id = "running", "claimed", owner_id
    job.generation += 1
    job.attempt_count += 1
    job.lease_expires_at = datetime.now(UTC) + timedelta(seconds=settings.model_probe_lease_seconds)
    run.status, run.stage, run.started_at, run.completed_at = ModelTestStatus.QUEUED, "claimed", None, None
    run.response_content = ""
    run.provider_status = run.response_headers_ms = run.first_content_ms = run.total_ms = None
    run.error_code, run.error_message = None, None
    run.milestones = {"queued": True, "attempt": job.attempt_count, "generation": job.generation}
    return ProbeIdentity(job.id, job.test_run_id, job.tenant_id, owner_id, job.generation)


async def execute_probe(session_factory: async_sessionmaker[AsyncSession], *, identity: ProbeIdentity, settings: Settings) -> None:
    async def guard(session: AsyncSession, run: ModelEndpointTestRun) -> None:
        job = await _owned_job(session, identity)
        await _authorize(session, job, run)
        job.stage = run.stage
        if run.status == ModelTestStatus.PASSED:
            _settle(job, run, settings=settings, status="succeeded")
        elif run.status == ModelTestStatus.FAILED:
            _settle(job, run, settings=settings, status="failed", code=run.error_code)
        else:
            job.lease_expires_at = datetime.now(UTC) + timedelta(seconds=settings.model_probe_lease_seconds)

    async def invoke() -> None:
        async with session_factory() as session:
            await execute_model_test_run(session, test_run_id=identity.test_run_id,
                secret_provider=LocalEnvelopeSecretProvider(settings.secret_encryption_key),
                allow_private_networks=settings.model_endpoint_allow_private_networks,
                allowed_hosts=settings.model_endpoint_allowed_hosts, timeout_seconds=settings.model_test_timeout_seconds,
                max_response_bytes=settings.model_test_max_response_bytes, progress_guard=guard)

    async def monitor() -> None:
        while True:
            await asyncio.sleep(min(10, settings.model_probe_lease_seconds / 3))
            async with session_factory() as session, session.begin():
                job = await session.scalar(select(ModelProbeJob).where(ModelProbeJob.id == identity.job_id,
                    ModelProbeJob.tenant_id == identity.tenant_id).with_for_update())
                if job is not None and job.generation == identity.generation and job.status in {"succeeded", "failed", "cancelled", "queued"}:
                    return
                job = await _owned_job(session, identity)
                run = await session.get(ModelEndpointTestRun, job.test_run_id)
                if run is None:
                    raise ModelProbeAuthorizationDenied("固定探测记录不存在")
                await _authorize(session, job, run)
                job.lease_expires_at = datetime.now(UTC) + timedelta(seconds=settings.model_probe_lease_seconds)

    task, watcher = asyncio.create_task(invoke()), asyncio.create_task(monitor())
    code = None
    try:
        async with asyncio.timeout(settings.model_probe_run_timeout_seconds):
            done, _ = await asyncio.wait({task, watcher}, return_when=asyncio.FIRST_COMPLETED)
            if watcher in done:
                await watcher
            await task
    except TimeoutError:
        code = "MODEL_PROBE_TIMEOUT"
    except ModelProbeAuthorizationDenied:
        code = "MODEL_PROBE_AUTHORIZATION_DENIED"
    except ModelProbeLeaseLostError:
        # 当前 owner 的过期由恢复器处理；旧 generation 不得写任何结果。
        return
    except asyncio.CancelledError:
        # 停机只让租约到期恢复，不把系统停止等同于用户取消。
        raise
    except Exception as exc:
        logger.error("model_probe_execution_failed", job_id=str(identity.job_id), generation=identity.generation, error_type=type(exc).__name__)
        code = "MODEL_PROBE_EXECUTION_FAILED"
    finally:
        task.cancel()
        watcher.cancel()
        await asyncio.gather(task, watcher, return_exceptions=True)
    if code is not None:
        async with session_factory() as session, session.begin():
            job = await session.scalar(select(ModelProbeJob).where(ModelProbeJob.id == identity.job_id,
                ModelProbeJob.tenant_id == identity.tenant_id).with_for_update())
            if job is None or job.status != "running" or job.owner_id != identity.owner_id or job.generation != identity.generation:
                return
            run = await session.get(ModelEndpointTestRun, job.test_run_id)
            if run is not None:
                _settle(job, run, settings=settings, status="failed", code=code)


async def cancel_model_probe(session: AsyncSession, *, test_run_id: UUID, tenant_id: UUID, settings: Settings) -> ModelEndpointTestRun:
    job = await session.scalar(select(ModelProbeJob).where(ModelProbeJob.test_run_id == test_run_id,
        ModelProbeJob.tenant_id == tenant_id).with_for_update())
    if job is None:
        raise ResourceNotFoundError("可取消的持久化探测不存在")
    run = await session.get(ModelEndpointTestRun, job.test_run_id)
    if run is None:
        raise ResourceNotFoundError("探测记录不存在")
    if job.status in {"queued", "running"}:
        job.cancel_requested = True
        _settle(job, run, settings=settings, status="cancelled", code="MODEL_PROBE_CANCELLED")
        job.generation += 1
    return run
