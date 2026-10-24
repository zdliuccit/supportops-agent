"""真实查询下的生命周期、执行许可与健康快照契约；不启动模型或业务动作。"""

from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine
from supportops_core.config import Settings
from supportops_core.db import create_session_factory
from supportops_core.enums import AgentStatus, RunStatus
from supportops_core.health_services import agent_activity, refresh_agent_runtime_health
from supportops_core.models import (
    Agent,
    AgentRun,
    AgentRuntimeHealth,
    Conversation,
    RunAttempt,
    RuntimeServiceLease,
    utc_now,
)
from supportops_core.run_attempt_services import claim_run_attempt
from supportops_core.services import create_message_and_run

from tests.conftest import auth_headers, retire_test_run_notifications
from tests.worker.test_worker import seed_run


@dataclass
class StateCase:
    client: AsyncClient
    engine: AsyncEngine
    settings: Settings
    tenant_id: UUID
    agent_id: UUID
    run_id: UUID
    headers: dict[str, str]
    lease_id: UUID

    @property
    def factory(self):
        return create_session_factory(self.engine)


@pytest.fixture
async def state_case(client, engine, settings):
    factory = create_session_factory(engine)
    async with factory() as session:
        existing_run_ids = set((await session.scalars(select(AgentRun.id))).all())
    run_id = await seed_run(engine, settings)
    async with factory() as session, session.begin():
        run = await session.get(AgentRun, run_id)
        now = utc_now()
        lease = RuntimeServiceLease(
            instance_id=f"states-{uuid4()}",
            heartbeat_at=now,
            expires_at=now + timedelta(minutes=5),
            service_status="healthy",
        )
        session.add(lease)
        await session.flush()
        tenant_id, agent_id, lease_id = run.tenant_id, run.agent_id, lease.id
    headers = await auth_headers(
        engine, settings, tenant_id, "dashboard-admin", roles=("platform_admin",)
    )
    value = StateCase(client, engine, settings, tenant_id, agent_id, run_id, headers, lease_id)
    try:
        yield value
    finally:
        async with factory() as session, session.begin():
            lease = await session.get(RuntimeServiceLease, lease_id)
            lease.service_status, lease.expires_at = "stopped", utc_now() - timedelta(seconds=1)
            # 保留测试事实，但延后本例未终结租约，避免后续全局恢复扫描误领。
            attempts = await session.scalars(
                select(RunAttempt).where(
                    RunAttempt.tenant_id == tenant_id,
                    RunAttempt.status == "running",
                )
            )
            for attempt in attempts:
                attempt.lease_expires_at = utc_now() + timedelta(days=1)
        await retire_test_run_notifications(engine, existing_run_ids)


async def running(case: StateCase):
    async with case.factory() as session, session.begin():
        claim = await claim_run_attempt(
            session, case.run_id, owner_id="state-owner", lease_seconds=120
        )
        assert claim is not None
        return claim


async def refresh(case: StateCase):
    async with case.factory() as session, session.begin():
        return (
            await refresh_agent_runtime_health(
                session, tenant_id=case.tenant_id, settings=case.settings
            )
        )[0]


async def item(case: StateCase, **params):
    response = await case.client.get(
        "/v1/admin/system/dashboard/agents/status", headers=case.headers, params=params
    )
    assert response.status_code == 200, response.text
    assert response.json()["total"] == 1
    return response.json()["items"][0]


async def summary(case: StateCase, **params):
    response = await case.client.get(
        "/v1/admin/system/dashboard/summary", headers=case.headers, params=params
    )
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize(
    "mode,execution,active,health",
    [
        ("queued", "queued", 1, "healthy"),
        ("running", "running", 1, "healthy"),
        ("cancelling", "cancelling", 1, "healthy"),
        ("expired", "needs_attention", 0, "degraded"),
        ("dead_letter", "needs_attention", 0, "degraded"),
        ("waiting_approval", "waiting_approval", 0, "healthy"),
        ("waiting_action", "waiting_action", 0, "healthy"),
        ("completed", "idle", 0, "unknown"),
        ("failed", "idle", 0, "unknown"),
        ("cancelled", "idle", 0, "no_recent_activity"),
    ],
)
async def test_three_states_are_independent_and_waits_are_not_model_execution(
    state_case, mode, execution, active, health
):
    case = state_case
    if mode in {"running", "cancelling", "expired"}:
        await running(case)
    async with case.factory() as session, session.begin():
        run = await session.get(AgentRun, case.run_id)
        if mode == "expired":
            attempt = await session.get(RunAttempt, run.current_attempt_id)
            attempt.lease_expires_at = utc_now() - timedelta(seconds=1)
        elif mode == "dead_letter":
            run.recovery_reason = "RUN_NOTIFICATION_EXHAUSTED"
        else:
            run.status = RunStatus(mode)
            if mode in {"completed", "failed", "cancelled"}:
                run.finished_at = utc_now()
    snapshot = await refresh(case)
    value = await item(case)
    assert value["lifecycle_status"] == "active"
    assert value["execution_status"] == execution and value["active_run_count"] == active
    assert value["health_status"] == snapshot.health_status == health
    assert (await summary(case))["status_counts"]["running"] == int(active > 0)
    if mode.startswith("waiting"):
        assert "等待" in value["health_reason"] and "模型执行已释放" in value["health_reason"]
        assert value["error_rate"] is None and value["p95_latency_ms"] is None
    if mode == "failed":
        assert "样本不足" in value["health_reason"]


@pytest.mark.parametrize("lifecycle", ["draft", "disabled"])
async def test_non_active_lifecycle_preserves_inflight_execution_without_claiming_healthy(
    state_case, lifecycle
):
    case = state_case
    await running(case)
    async with case.factory() as session, session.begin():
        agent = await session.get(Agent, case.agent_id)
        agent.status = AgentStatus(lifecycle)
    await refresh(case)
    value = await item(case)
    assert value["lifecycle_status"] == lifecycle and value["execution_status"] == "running"
    assert value["health_status"] == "unknown" and value["active_run_count"] == 1
    assert "未启用" in value["health_reason"]


async def idle(case: StateCase):
    async with case.factory() as session, session.begin():
        run = await session.get(AgentRun, case.run_id)
        run.status, run.finished_at = RunStatus.COMPLETED, utc_now() - timedelta(days=2)
        run.created_at = run.finished_at


@pytest.mark.parametrize("service", [True, False])
async def test_active_without_recent_work_explains_idle_or_unavailable_service(state_case, service):
    case = state_case
    await idle(case)
    if not service:
        async with case.factory() as session, session.begin():
            lease = await session.get(RuntimeServiceLease, case.lease_id)
            lease.expires_at = utc_now() - timedelta(seconds=1)
    await refresh(case)
    value = await item(case)
    assert value["lifecycle_status"] == "active" and value["execution_status"] == "idle"
    assert value["active_run_count"] == 0
    assert value["health_status"] == ("no_recent_activity" if service else "unknown")
    assert ("暂无运行" if service else "执行服务不可用") in value["health_reason"]


@pytest.mark.parametrize(
    "status", ["queued", "running", "cancelling", "waiting_approval", "waiting_action"]
)
async def test_current_activity_is_not_cut_off_by_historical_dashboard_window(state_case, status):
    case = state_case
    if status in {"running", "cancelling"}:
        await running(case)
    async with case.factory() as session, session.begin():
        run = await session.get(AgentRun, case.run_id)
        run.status, run.created_at = RunStatus(status), utc_now() - timedelta(days=30)
    await refresh(case)
    value = await item(case, days=1)
    assert value["execution_status"] == status
    assert value["active_run_count"] == int(not status.startswith("waiting"))
    current = await summary(case, days=1)
    assert current["metrics"]["agent_runs"]["value"] == 0
    assert current["status_counts"]["running"] == value["active_run_count"]


async def test_old_attempts_and_multiple_runs_never_duplicate_business_or_agent_counts(state_case):
    case = state_case
    await running(case)
    async with case.factory() as session, session.begin():
        run = await session.get(AgentRun, case.run_id)
        current = await session.get(RunAttempt, run.current_attempt_id)
        session.add(
            RunAttempt(
                tenant_id=case.tenant_id,
                run_id=case.run_id,
                generation=2,
                owner_id="old-or-unadopted-owner",
                status="running",
                lease_expires_at=utc_now() + timedelta(minutes=1),
                checkpoint_thread_id=f"unadopted:{uuid4()}",
            )
        )
        current.status = "running"
        conversation = await session.get(Conversation, run.conversation_id)
        await create_message_and_run(
            session,
            conversation=conversation,
            user_id=conversation.user_id,
            identity_roles=("customer",),
            content="第二个排队问题",
            idempotency_key=str(uuid4()),
            correlation_id="dashboard-state-second-run",
        )
    await refresh(case)
    value = await item(case)
    current = await summary(case)
    assert value["active_run_count"] == 2 and current["status_counts"]["running"] == 1
    assert current["metrics"]["agent_runs"]["value"] == 2


@pytest.mark.parametrize(
    "mismatch",
    ["missing_attempt", "generation", "conversation_attempt", "conversation_run", "attempt_status"],
)
async def test_unproven_current_attempt_cannot_be_reported_as_active_execution(
    state_case, mismatch
):
    case = state_case
    await running(case)
    async with case.factory() as session, session.begin():
        run = await session.get(AgentRun, case.run_id)
        conversation = await session.get(Conversation, run.conversation_id)
        attempt = await session.get(RunAttempt, run.current_attempt_id)
        if mismatch == "missing_attempt":
            run.current_attempt_id = None
        elif mismatch == "generation":
            run.execution_generation += 1
        elif mismatch == "conversation_attempt":
            conversation.active_attempt_id = None
        elif mismatch == "conversation_run":
            conversation.active_run_id = None
        else:
            attempt.status = "expired"
    await refresh(case)
    value = await item(case)
    assert value["execution_status"] == "needs_attention" and value["active_run_count"] == 0
    assert (await summary(case))["status_counts"]["running"] == 0


async def test_status_get_is_read_only_and_never_invents_health_without_snapshot(state_case):
    case = state_case
    await idle(case)
    for _ in range(2):
        value = await item(case)
        assert value["health_status"] == "unknown" and value["health_stale"]
        assert value["observed_at"] is None and value["computed_at"] is None
        assert "尚未生成" in value["health_reason"]
    async with case.factory() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(AgentRuntimeHealth)
                .where(AgentRuntimeHealth.tenant_id == case.tenant_id)
            )
            == 0
        )
        activity = await agent_activity(session, tenant_id=case.tenant_id, agent_id=case.agent_id)
        assert activity.execution_status == "idle"
