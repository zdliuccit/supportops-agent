"""只读连接探测的持久化领取、隔离派发与过期收敛。"""

import asyncio
from datetime import timedelta
from uuid import UUID, uuid4

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from supportops_core.auth import ensure_identity
from supportops_core.config import Settings
from supportops_core.mcp_isolated_contracts import IsolatedServiceStatus, mock_descriptor
from supportops_core.mcp_isolated_ipc import dispatch_probe
from supportops_core.models import Tenant, ToolConnectionProbeJob, ToolDiscoveryRevision, utc_now
from supportops_core.tool_connection_services import append_connection, probe_principal, terminate_job
from supportops_core.tool_connection_state import aware, latest_connection
from supportops_core.tool_discovery_contracts import canonical_json, discovery_digest
from supportops_core.tool_discovery_services import current_source_for_admin, require_source_snapshot


async def _budget(session: AsyncSession) -> None:
    if session.get_bind().dialect.name == "postgresql":
        await session.execute(text("SET LOCAL statement_timeout='3s'"))
        await session.execute(text("SET LOCAL lock_timeout='500ms'"))


async def _source(session: AsyncSession, job: ToolConnectionProbeJob) -> ToolDiscoveryRevision:
    identity = await ensure_identity(session, probe_principal(job), require_session=True)
    source, policy = await current_source_for_admin(session, identity, source_key=job.source_key, lock=True)
    if source.id != job.source_record_id or source.policy_digest != job.policy_digest or source.status != "ready" or not policy.enabled or policy.valid_until <= utc_now():
        raise ValueError("PROBE_SCOPE_CHANGED")
    require_source_snapshot(source)
    return source


async def process_one_probe(factory: async_sessionmaker[AsyncSession], *, settings: Settings) -> bool:
    async with asyncio.timeout(5), factory() as session, session.begin():
        await _budget(session)
        candidate = (await session.execute(select(ToolConnectionProbeJob.id, ToolConnectionProbeJob.tenant_id)
            .where(ToolConnectionProbeJob.status.in_(("queued", "running")))
            .order_by(ToolConnectionProbeJob.created_at, ToolConnectionProbeJob.id).limit(20))).all()
    claimed: tuple[UUID, UUID, UUID, int] | None = None
    for job_id, tenant_id in candidate:
        async with asyncio.timeout(5), factory() as session, session.begin():
            await _budget(session)
            tenant = await session.scalar(select(Tenant).where(Tenant.id == tenant_id).with_for_update(skip_locked=True))
            if tenant is None:
                continue
            job = await session.scalar(select(ToolConnectionProbeJob).where(ToolConnectionProbeJob.id == job_id).with_for_update(skip_locked=True))
            if job is None or job.status not in {"queued", "running"}:
                continue
            if not settings.tool_connection_mock_enabled:
                terminate_job(job, "failed", "PROBE_DISABLED")
                await session.flush()
                return True
            expired = aware(job.expires_at) <= utc_now() or job.status == "running" and (job.lease_until is None or aware(job.lease_until) <= utc_now())
            if job.status == "running" and not expired:
                continue
            try:
                source = await _source(session, job)
            except Exception:
                terminate_job(job, "failed", "PROBE_AUTHORIZATION_OR_SCOPE_CHANGED")
                await session.flush()
                return True
            if expired:
                await _finish(session, source, job, passed=False, code="PROBE_EXPIRED")
                return True
            event = await latest_connection(session, source)
            if event is None or event.probe_job_id != job.id or event.kind != "probe_queued":
                terminate_job(job, "failed", "PROBE_FENCED")
                await session.flush()
                return True
            job.status, job.owner_id = "running", uuid4()
            job.generation += 1
            job.revision += 1
            job.lease_until = min(utc_now()+timedelta(seconds=20), aware(job.expires_at))
            job.updated_at = utc_now()
            await session.flush()
            claimed = (job.id, job.tenant_id, job.owner_id, job.generation)
        if claimed:
            break
    if claimed is None:
        return False
    job_id, tenant_id, owner, generation = claimed
    # 领取事务提交成功后才进入IPC；期间不持业务数据库锁。没有自动重发。
    report, code = None, "PROBE_RESULT_UNCONFIRMED"
    try:
        report = await dispatch_probe(job_id)
    except Exception:
        code = "PROBE_TRANSPORT_FAILED"
    async with asyncio.timeout(5), factory() as session, session.begin():
        await _budget(session)
        await session.scalar(select(Tenant).where(Tenant.id == tenant_id).with_for_update())
        job = await session.scalar(select(ToolConnectionProbeJob).where(ToolConnectionProbeJob.id == job_id).with_for_update())
        if job is None or job.status != "running" or job.owner_id != owner or job.generation != generation:
            return True
        try:
            source = await _source(session, job)
        except Exception:
            terminate_job(job, "failed", "PROBE_AUTHORIZATION_OR_SCOPE_CHANGED")
            await session.flush()
            return True
        event = await latest_connection(session, source)
        if event is None or event.probe_job_id != job.id or event.kind != "probe_queued":
            terminate_job(job, "failed", "PROBE_FENCED")
            await session.flush()
            return True
        passed = False
        if job.lease_until is not None and aware(job.lease_until) > utc_now() and aware(job.expires_at) > utc_now() and report is not None:
            policy = source.policy
            expected = mock_descriptor()
            allowed = policy.get("allowed_tools")
            try:
                passed = (len(report.catalog.tools) == 1 and report.source_fingerprint == policy.get("source_fingerprint")
                    and canonical_json(report.catalog.tools[0].model_dump(mode="json")) == canonical_json(expected.model_dump(mode="json"))
                    and report.descriptor_digest == discovery_digest(expected.model_dump(mode="json"))
                    and report.input_schema_digest == discovery_digest(expected.input_schema)
                    and report.output_schema_digest == discovery_digest(expected.output_schema)
                    and canonical_json(report.observation.model_dump(mode="json") if report.observation else None) == canonical_json(IsolatedServiceStatus().model_dump(mode="json"))
                    and allowed == [{"name": expected.name, "descriptor_digest": report.descriptor_digest}])
            except Exception:
                code = "PROBE_RESULT_REJECTED"
        await _finish(session, source, job, passed=passed, code=None if passed else code,
            digest=discovery_digest(report.model_dump(mode="json")) if passed and report else None)
    return True


async def _finish(session: AsyncSession, source: ToolDiscoveryRevision, job: ToolConnectionProbeJob, *,
    passed: bool, code: str | None, digest: str | None = None,
) -> None:
    previous = await latest_connection(session, source)
    terminate_job(job, "passed" if passed else "failed", code, result_digest=digest)
    await session.flush()
    if previous is None or previous.probe_job_id != job.id:
        return
    failures = 0 if passed else min(3, previous.failure_count+1)
    circuit = "closed" if passed else "open" if failures >= 3 or previous.circuit_state == "half_open" else "closed"
    await append_connection(session, source, actor_id=job.requested_by, previous=previous,
        kind="probe_passed" if passed else "probe_failed", circuit=circuit,
        health="healthy" if passed else "unhealthy", failures=failures, job=job)
