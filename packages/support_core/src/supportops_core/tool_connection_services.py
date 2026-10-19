"""隔离连接探测的持久化许可、人工熔断和追加复核；无手工通过入口。"""

from datetime import timedelta
from typing import Literal, cast
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from supportops_core.auth import IdentityContext, Principal
from supportops_core.config import Settings
from supportops_core.enums import AuditAction, AuditResourceType
from supportops_core.mcp_isolated_runtime import source_fingerprint
from supportops_core.mcp_isolated_contracts import mock_descriptor
from supportops_core.models import AgentAuditEvent, ToolConnectionEvent, ToolConnectionProbeJob, ToolDiscoveryRevision, utc_now
from supportops_core.services import ResourceNotFoundError
from supportops_core.tool_connection_contracts import ConnectionCommand, ConnectionView, ProbeJobView
from supportops_core.tool_connection_state import aware, connection_view, latest_connection
from supportops_core.tool_discovery_contracts import ToolSourcePolicy, discovery_digest
from supportops_core.tool_discovery_services import ToolDiscoveryConflict, current_source_for_admin, require_source_snapshot


def job_view(row: ToolConnectionProbeJob) -> ProbeJobView:
    return ProbeJobView(id=row.id, source_key=row.source_key, source_record_id=row.source_record_id,
        status=cast(Literal["queued", "running", "passed", "failed", "cancelled"], row.status), revision=row.revision, error_code=row.error_code,
        created_at=row.created_at, updated_at=row.updated_at, expires_at=row.expires_at)


def _readiness(source: ToolDiscoveryRevision, policy: ToolSourcePolicy, settings: Settings) -> str | None:
    if not settings.tool_connection_mock_enabled or settings.environment not in {"local", "dev", "test"}:
        return "MOCK_PROBE_DISABLED"
    if source.status != "ready" or not policy.enabled or policy.valid_until <= utc_now():
        return "SOURCE_NOT_READY"
    try:
        require_source_snapshot(source)
        fingerprint = source_fingerprint()
    except Exception:
        return "SOURCE_IMPLEMENTATION_UNAVAILABLE"
    expected = mock_descriptor()
    if fingerprint != policy.source_fingerprint or [tool.model_dump() for tool in policy.allowed_tools] != [
        {"name": expected.name, "descriptor_digest": discovery_digest(expected.model_dump(mode="json"))}]:
        return "SOURCE_IMPLEMENTATION_MISMATCH"
    return None


async def read_connection(session: AsyncSession, identity: IdentityContext, *, source_key: str, settings: Settings) -> ConnectionView:
    source, policy = await current_source_for_admin(session, identity, source_key=source_key)
    event = await latest_connection(session, source)
    reason = _readiness(source, policy, settings)
    if event and event.retry_after is not None and aware(event.retry_after) > utc_now():
        reason = reason or "RECOVERY_COOLDOWN"
    active = await session.scalar(select(ToolConnectionProbeJob.id).where(ToolConnectionProbeJob.tenant_id == source.tenant_id,
        ToolConnectionProbeJob.source_key == source_key, ToolConnectionProbeJob.status.in_(("queued", "running"))).limit(1))
    if active is not None:
        reason = reason or "PROBE_IN_PROGRESS"
    return connection_view(source, event, available=reason is None, reason=reason)


async def append_connection(session: AsyncSession, source: ToolDiscoveryRevision, *, actor_id: UUID,
    previous: ToolConnectionEvent | None, kind: str, circuit: str, health: str = "unknown", failures: int = 0,
    job: ToolConnectionProbeJob | None = None,
) -> ToolConnectionEvent:
    now = utc_now()
    parent = previous.revision if previous else 0
    if parent >= 2_147_483_646:
        raise ToolDiscoveryConflict("连接修订已耗尽")
    event = ToolConnectionEvent(tenant_id=source.tenant_id, source_key=source.source_key, source_record_id=source.id,
        policy_digest=source.policy_digest, revision=parent+1, parent_revision=parent, kind=kind,
        circuit_state=circuit, health_state=health, failure_count=failures, probe_job_id=job.id if job else None,
        actor_user_id=actor_id, checked_at=now if health == "healthy" else None,
        valid_until=min(now+timedelta(seconds=60), ToolSourcePolicy.model_validate(source.policy).valid_until) if health == "healthy" else None,
        retry_after=now+timedelta(seconds=30) if circuit == "open" else None, created_at=now)
    session.add(event)
    session.add(AgentAuditEvent(tenant_id=source.tenant_id, actor_user_id=actor_id, resource_type=AuditResourceType.TOOL,
        action=AuditAction.TOOL_UPDATED, correlation_id=f"connection:{event.id or source.id}",
        metadata_payload={"connection_event": kind, "connection_revision": parent+1,
            "source_record_id": str(source.id), "probe_job_id": str(job.id) if job else None}))
    await session.flush()
    return event


async def open_circuit(session: AsyncSession, identity: IdentityContext, *, source_key: str,
    command: ConnectionCommand, settings: Settings,
) -> ConnectionView:
    source, _policy = await current_source_for_admin(session, identity, source_key=source_key, lock=True)
    previous = await latest_connection(session, source)
    if not command.confirm or command.expected_source_record_id != source.id or command.expected_revision != (previous.revision if previous else 0):
        raise ToolDiscoveryConflict("请读取当前连接修订后显式确认")
    # 原任务永久围栏，不能以人工熔断后的迟到通过结果自动恢复。
    active = await session.scalar(select(ToolConnectionProbeJob).where(ToolConnectionProbeJob.tenant_id == source.tenant_id,
        ToolConnectionProbeJob.source_key == source_key, ToolConnectionProbeJob.status.in_(("queued", "running"))).with_for_update())
    if active:
        terminate_job(active, "cancelled", "PROBE_MANUALLY_BLOCKED")
    await append_connection(session, source, actor_id=identity.user.id, previous=previous, kind="manual_open", circuit="open")
    return await read_connection(session, identity, source_key=source_key, settings=settings)


async def request_probe(session: AsyncSession, identity: IdentityContext, *, source_key: str,
    command: ConnectionCommand, settings: Settings,
) -> ProbeJobView:
    source, policy = await current_source_for_admin(session, identity, source_key=source_key, lock=True)
    view = await read_connection(session, identity, source_key=source_key, settings=settings)
    if not command.confirm or command.expected_source_record_id != source.id or command.expected_revision != view.revision:
        raise ToolDiscoveryConflict("连接修订已变化，请重新确认探测")
    if not view.probe_available:
        raise ToolDiscoveryConflict("当前来源或隔离探测条件未满足，请读取连接状态")
    expiry = identity.principal.token_expires_at
    if identity.principal.session_id is None or identity.principal.auth_generation is None or expiry is None:
        raise ToolDiscoveryConflict("需要当前有效管理员会话")
    now = utc_now()
    expires = min(now+timedelta(seconds=60), policy.valid_until, expiry)
    if expires <= now+timedelta(seconds=15):
        raise ToolDiscoveryConflict("批准或登录有效期不足探测预算")
    job = ToolConnectionProbeJob(id=uuid4(), tenant_id=source.tenant_id, source_key=source_key, source_record_id=source.id,
        policy_digest=source.policy_digest, requested_by=identity.user.id, auth_session_id=identity.principal.session_id,
        auth_generation=identity.principal.auth_generation, expires_at=expires, created_at=now, updated_at=now,
        status="queued", revision=1, generation=0)
    session.add(job)
    await session.flush()
    previous = await latest_connection(session, source)
    await append_connection(session, source, actor_id=identity.user.id, previous=previous, kind="probe_queued",
        circuit="half_open" if view.circuit_state != "closed" else "closed", failures=view.failure_count, job=job)
    return job_view(job)


def terminate_job(job: ToolConnectionProbeJob, status: str, code: str | None, *, result_digest: str | None = None) -> None:
    job.status, job.error_code, job.result_digest = status, code, result_digest
    job.owner_id, job.lease_until = None, None
    job.generation += 1
    job.revision += 1
    job.updated_at = utc_now()


async def scoped_probe(session: AsyncSession, identity: IdentityContext, *, source_key: str, job_id: UUID,
    lock: bool = False,
) -> tuple[ToolDiscoveryRevision, ToolConnectionProbeJob]:
    source, _ = await current_source_for_admin(session, identity, source_key=source_key, lock=lock)
    query = select(ToolConnectionProbeJob).where(ToolConnectionProbeJob.id == job_id,
        ToolConnectionProbeJob.tenant_id == source.tenant_id, ToolConnectionProbeJob.source_key == source_key).execution_options(populate_existing=True)
    job = await session.scalar(query.with_for_update() if lock else query)
    if job is None:
        raise ResourceNotFoundError("连接探测任务不存在")
    return source, job


async def cancel_probe(session: AsyncSession, identity: IdentityContext, *, source_key: str, job_id: UUID,
    revision: int,
) -> ProbeJobView:
    source, job = await scoped_probe(session, identity, source_key=source_key, job_id=job_id, lock=True)
    if job.revision != revision or job.status not in {"queued", "running"}:
        raise ToolDiscoveryConflict("探测修订或状态已变化")
    terminate_job(job, "cancelled", "PROBE_CANCELLED")
    previous = await latest_connection(session, source)
    if previous is not None and previous.probe_job_id == job.id and job.source_record_id == source.id:
        await append_connection(session, source, actor_id=identity.user.id, previous=previous,
            kind="probe_cancelled", circuit="open" if previous.circuit_state == "half_open" else previous.circuit_state, job=job)
    await session.flush()
    return job_view(job)


def probe_principal(job: ToolConnectionProbeJob) -> Principal:
    return Principal(user_id=job.requested_by, tenant_id=job.tenant_id, roles=(), display_name="",
        session_id=job.auth_session_id, auth_generation=job.auth_generation, token_expires_at=aware(job.expires_at))
