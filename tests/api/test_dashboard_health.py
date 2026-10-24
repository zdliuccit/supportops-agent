"""健康协调、发布差异与只读状态投影；历史样本为隔离数据库事实。"""

from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest
from supportops_core.enums import RunStatus
from supportops_core.health_services import refresh_agent_runtime_health
from supportops_core.model_services import revoke_model_credential_revision
from supportops_core.models import (
    Agent,
    AgentDraft,
    AgentRun,
    AgentRuntimeHealth,
    AgentVersion,
    ModelEndpoint,
    ModelEndpointVersion,
    RunAttempt,
    RuntimeServiceLease,
    User,
    utc_now,
)

from tests.api.test_dashboard_contract import historical_run
from tests.api.test_dashboard_states import StateCase, item, running, summary
from tests.api.test_dashboard_states import state_case as state_case


async def sample_history(case: StateCase, outcomes: list[str], latencies: list[int | None]):
    now = utc_now()
    async with case.factory() as session, session.begin():
        original = await session.get(AgentRun, case.run_id)
        original.status, original.finished_at = RunStatus.CANCELLED, now - timedelta(days=2)
        original.created_at = original.finished_at
        agent = await session.get(Agent, case.agent_id)
        version = await session.get(AgentVersion, agent.active_version_id)
        user = await session.get(User, agent.created_by)
        ids = []
        for index, (outcome, latency) in enumerate(zip(outcomes, latencies, strict=True)):
            run, _ = await historical_run(
                session,
                agent,
                version,
                user,
                now - timedelta(minutes=10 - index),
                failed=outcome == "failed",
            )
            run.status, run.end_to_end_latency_ms = RunStatus(outcome), latency
            ids.append(run.id)
    return now, ids


async def compute(case: StateCase, now: datetime | None = None):
    async with case.factory() as session, session.begin():
        rows = await refresh_agent_runtime_health(
            session, tenant_id=case.tenant_id, settings=case.settings, now=now
        )
        assert len(rows) == 1
        return rows[0]


@pytest.mark.parametrize(
    "outcomes,expected_rate,expected_health",
    [
        (["failed"], None, "unknown"),
        (["failed", "failed"], None, "unknown"),
        (["failed"] * 3, 100, "degraded"),
        (["completed", "completed", "failed"], 33.33, "healthy"),
        (["completed", "failed", "completed", "failed"], 50, "degraded"),
        (["completed"] * 3, 0, "healthy"),
        (["cancelled", "waiting_approval", "waiting_action"], None, "healthy"),
    ],
)
async def test_health_failure_threshold_uses_terminal_business_runs(
    state_case, outcomes, expected_rate, expected_health
):
    case = state_case
    now, _ = await sample_history(case, outcomes, [100] * len(outcomes))
    snapshot = await compute(case, now)
    row = await item(case)
    assert row["health_status"] == snapshot.health_status == expected_health
    assert row["error_rate"] == snapshot.error_rate == expected_rate
    terminal = sum(outcome in {"completed", "failed"} for outcome in outcomes)
    assert row["p95_latency_ms"] == (100 if terminal >= 3 else None)
    if terminal < 3 and expected_health == "unknown":
        assert "样本不足" in row["health_reason"]
    if expected_health == "degraded":
        assert "失败率较高" in row["health_reason"]
    counts = (await summary(case))["status_counts"]
    assert counts[f"health_{expected_health}"] == 1


@pytest.mark.parametrize(
    "latencies,expected",
    [
        ([0, 100, 500], 500),
        ([None, 100, 500], None),
        ([-1, 100, 500], None),
        ([-1, 0, 100, 500], 500),
    ],
)
async def test_health_p95_requires_three_nonnegative_terminal_samples(
    state_case, latencies, expected
):
    case = state_case
    now, _ = await sample_history(case, ["completed"] * len(latencies), latencies)
    snapshot = await compute(case, now)
    assert snapshot.p95_latency_ms == expected
    assert (await item(case))["p95_latency_ms"] == expected


async def test_health_history_uses_left_closed_right_open_window(state_case):
    case = state_case
    now, ids = await sample_history(case, ["completed"] * 3 + ["failed"] * 3, [100] * 6)
    async with case.factory() as session, session.begin():
        times = [
            now - timedelta(hours=24),
            now - timedelta(hours=24) + timedelta(microseconds=1),
            now - timedelta(seconds=2),
            now - timedelta(hours=24, microseconds=1),
            now,
            now + timedelta(seconds=1),
        ]
        for run_id, when in zip(ids, times, strict=True):
            run = await session.get(AgentRun, run_id)
            run.created_at, run.started_at, run.finished_at = (
                when,
                when,
                when + timedelta(seconds=1),
            )
    snapshot = await compute(case, now)
    assert snapshot.error_rate == 0 and snapshot.health_status == "healthy"
    assert snapshot.window_start.replace(tzinfo=UTC) == now - timedelta(hours=24)
    assert snapshot.window_end.replace(tzinfo=UTC) == now


@pytest.mark.parametrize("mode", ["draft_changed", "draft_invalid", "restored"])
async def test_pending_publish_is_independent_of_health_and_explains_fixed_version(
    state_case, mode
):
    case = state_case
    now, _ = await sample_history(case, ["completed"] * 3, [100] * 3)
    await compute(case, now)
    async with case.factory() as session, session.begin():
        draft = await session.get(AgentDraft, case.agent_id)
        original = deepcopy(draft.config)
        changed = deepcopy(original)
        changed["prompt"]["system_prompt"] += "\n补充隔离说明"
        draft.config = {} if mode == "draft_invalid" else changed
        await session.flush()
        if mode == "restored":
            draft.config = original
    # GET直接比较当前草稿，不能等待下一次协调或把待发布当离线。
    read = await item(case)
    assert read["pending_publish"] is (mode != "restored")
    assert read["health_status"] == "healthy"
    if mode != "restored":
        assert "待发布" in read["health_reason"] and "运行版本" in read["health_reason"]
    snapshot = await compute(case)
    assert snapshot.pending_publish is (mode != "restored")
    if snapshot.pending_publish:
        assert "待发布" in snapshot.health_reason


@pytest.mark.parametrize(
    "damage", ["disabled_model", "revoked_credential", "missing_active_version"]
)
async def test_current_version_failures_have_explicit_health_reason(state_case, damage):
    case = state_case
    now, _ = await sample_history(case, ["completed"] * 3, [100] * 3)
    async with case.factory() as session, session.begin():
        agent = await session.get(Agent, case.agent_id)
        version = await session.get(AgentVersion, agent.active_version_id)
        model = await session.get(ModelEndpointVersion, version.model_endpoint_version_id)
        if damage == "disabled_model":
            endpoint = await session.get(ModelEndpoint, model.endpoint_id)
            endpoint.is_enabled = False
        elif damage == "revoked_credential":
            await revoke_model_credential_revision(
                session,
                tenant_id=case.tenant_id,
                endpoint_id=model.endpoint_id,
                revision=1,
                expected_current_revision=1,
                actor_user_id=agent.created_by,
                reason="隔离健康测试撤销",
                correlation_id="health-test-revoke",
            )
        else:
            agent.active_version_id = None
    snapshot = await compute(case, now)
    expected = {
        "disabled_model": "模型端点停用",
        "revoked_credential": "已撤销",
        "missing_active_version": "活动版本不可用",
    }[damage]
    assert snapshot.health_status == "degraded" and expected in snapshot.health_reason
    assert (await item(case))["health_status"] == "degraded"


@pytest.mark.parametrize("waiting", ["waiting_approval", "waiting_action"])
async def test_waiting_keeps_historical_failures_but_releases_model_activity(state_case, waiting):
    case = state_case
    now, _ = await sample_history(case, ["failed"] * 3, [100, 200, 300])
    async with case.factory() as session, session.begin():
        run = await session.get(AgentRun, case.run_id)
        run.status, run.created_at, run.finished_at = RunStatus(waiting), now, None
    snapshot = await compute(case, now)
    value = await item(case)
    assert snapshot.health_status == "healthy" and snapshot.error_rate == 100
    assert "模型执行已释放" in snapshot.health_reason
    assert value["execution_status"] == waiting and value["active_run_count"] == 0
    assert value["p95_latency_ms"] == 300


@pytest.mark.parametrize("service", ["expired", "stopped", "degraded"])
async def test_unavailable_or_degraded_service_never_overwrites_execution_fact(state_case, service):
    case = state_case
    await running(case)
    async with case.factory() as session, session.begin():
        lease = await session.get(RuntimeServiceLease, case.lease_id)
        if service == "expired":
            lease.expires_at = utc_now() - timedelta(seconds=1)
        else:
            lease.service_status = service
    snapshot = await compute(case)
    value = await item(case)
    assert value["execution_status"] == "running" and value["active_run_count"] == 1
    assert snapshot.health_status == ("degraded" if service == "degraded" else "unknown")
    assert ("维护异常" if service == "degraded" else "执行服务不可用") in snapshot.health_reason


@pytest.mark.parametrize(
    "field,seconds",
    [
        ("observed_at", -91),
        ("computed_at", -91),
        ("observed_at", 6),
        ("computed_at", 6),
        ("observed_at", 4),
        ("computed_at", 4),
    ],
)
async def test_stale_snapshot_hides_metrics_and_get_never_recomputes(state_case, field, seconds):
    case = state_case
    now, _ = await sample_history(case, ["completed"] * 3, [100] * 3)
    snapshot = await compute(case, now)
    async with case.factory() as session, session.begin():
        stored = await session.get(AgentRuntimeHealth, snapshot.id)
        setattr(stored, field, now + timedelta(seconds=seconds))
    value = await item(case)
    stale = seconds != 4
    assert value["health_stale"] is stale
    assert value["health_status"] == ("unknown" if stale else "healthy")
    assert value["error_rate"] == (None if stale else 0)
    assert value["p95_latency_ms"] == (None if stale else 100)
    async with case.factory() as session:
        stored = await session.get(AgentRuntimeHealth, snapshot.id)
        assert getattr(stored, field).replace(tzinfo=UTC) == now + timedelta(seconds=seconds)


async def test_expired_attempt_is_local_not_global_service_failure(state_case):
    case = state_case
    await running(case)
    async with case.factory() as session, session.begin():
        run = await session.get(AgentRun, case.run_id)
        attempt = await session.get(RunAttempt, run.current_attempt_id)
        attempt.lease_expires_at = utc_now() - timedelta(seconds=1)
    await compute(case)
    value, overview = await item(case), await summary(case)
    assert value["health_status"] == "degraded" and value["execution_status"] == "needs_attention"
    assert value["active_run_count"] == 0 and overview["service"]["status"] == "healthy"
    assert "过期" in value["health_reason"]


async def test_reverted_draft_removes_cached_pending_reason_without_rewriting_snapshot(state_case):
    case = state_case
    now, _ = await sample_history(case, ["completed"] * 3, [100] * 3)
    async with case.factory() as session, session.begin():
        draft = await session.get(AgentDraft, case.agent_id)
        original = deepcopy(draft.config)
        draft.config = {**original, "prompt": {"system_prompt": "隔离待发布内容"}}
    snapshot = await compute(case, now)
    assert snapshot.pending_publish and "待发布" in snapshot.health_reason
    async with case.factory() as session, session.begin():
        draft = await session.get(AgentDraft, case.agent_id)
        draft.config = original
    value = await item(case)
    assert not value["pending_publish"] and "待发布" not in value["health_reason"]
    assert value["health_status"] == "healthy"
    async with case.factory() as session:
        stored = await session.get(AgentRuntimeHealth, snapshot.id)
        assert stored.pending_publish and "待发布" in stored.health_reason
        assert stored.computed_at.replace(tzinfo=UTC) == now


@pytest.mark.parametrize("mode", ["failed_history", "unavailable_service"])
async def test_pending_publish_preserves_primary_health_reason(state_case, mode):
    case = state_case
    now, _ = await sample_history(case, ["failed"] * 3, [100] * 3)
    async with case.factory() as session, session.begin():
        draft = await session.get(AgentDraft, case.agent_id)
        original = deepcopy(draft.config)
        draft.config = {**original, "prompt": {"system_prompt": "隔离待发布说明"}}
        if mode == "unavailable_service":
            lease = await session.get(RuntimeServiceLease, case.lease_id)
            lease.expires_at = now - timedelta(seconds=1)
    snapshot = await compute(case, now)
    value = await item(case)
    primary = "失败率较高" if mode == "failed_history" else "执行服务不可用"
    assert primary in snapshot.health_reason and primary in value["health_reason"]
    assert value["health_reason"].count("待发布") == 1
    assert value["pending_publish"]


async def test_recompute_overwrites_one_snapshot_and_clears_expired_history_metrics(state_case):
    case = state_case
    now, ids = await sample_history(case, ["failed"] * 3, [100, 200, 300])
    first = await compute(case, now)
    assert first.health_status == "degraded" and first.error_rate == 100
    async with case.factory() as session, session.begin():
        for run_id in ids:
            run = await session.get(AgentRun, run_id)
            run.created_at = now - timedelta(days=2)
            run.finished_at = run.created_at + timedelta(seconds=1)
    second = await compute(case, now + timedelta(seconds=1))
    assert second.id == first.id and second.health_status == "no_recent_activity"
    assert second.error_rate is None and second.p95_latency_ms is None
    value = await item(case)
    assert value["health_status"] == "no_recent_activity" and value["error_rate"] is None
