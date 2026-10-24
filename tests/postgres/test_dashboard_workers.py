"""两个独立 Worker 进程、真实 PG/Redis 与 Dashboard 的服务聚合验收。

仅模型入口替换为阻塞协程；心跳、维护、调度、领取、续租、信号与查询使用实际实现。
"""

import asyncio
import os
import secrets
import signal
import sys
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine
from supportops_api.main import create_app
from supportops_core.config import Settings
from supportops_core.db import create_session_factory
from supportops_core.enums import RunStatus
from supportops_core.health_services import refresh_agent_runtime_health
from supportops_core.models import (
    AgentRun,
    RunAttempt,
    RunEvent,
    RuntimeAlert,
    RuntimeServiceLease,
    utc_now,
)
from supportops_core.runtime_alert_services import scan_runtime_alerts

from tests.conftest import auth_headers, provision_agent, retire_test_run_notifications
from tests.postgres.test_knowledge_access import engine as engine
from tests.postgres.test_knowledge_access import settings as settings
from tests.worker.test_worker import seed_run

pytestmark = pytest.mark.skipif(
    os.getenv("SUPPORTOPS_RUN_POSTGRES_TESTS") != "1", reason="需显式启用本机隔离 PG/Redis"
)
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


async def eventually[T](operation: Callable[[], Awaitable[T]], predicate: Callable[[T], bool]) -> T:
    async with asyncio.timeout(15):
        while True:
            value = await operation()
            if predicate(value):
                return value
            await asyncio.sleep(0.05)


@dataclass
class WorkerCluster:
    settings: Settings
    engine: AsyncEngine
    redis: Redis
    client: AsyncClient
    headers: dict[str, str]
    tenant_id: UUID
    cwd: Path
    observed_baseline: int
    processes: list[asyncio.subprocess.Process] = field(default_factory=list)
    acl_users: list[str] = field(default_factory=list)
    queues: set[str] = field(default_factory=set)

    @property
    def factory(self):
        return create_session_factory(self.engine)

    async def leases(self) -> list[RuntimeServiceLease]:
        async with self.factory() as session:
            rows = list((await session.scalars(select(RuntimeServiceLease))).all())
        markers = {f":{process.pid}:" for process in self.processes}
        return [row for row in rows if any(marker in row.instance_id for marker in markers)]

    async def summary(self, **params):
        response = await self.client.get(
            "/v1/admin/system/dashboard/summary", headers=self.headers, params=params
        )
        assert response.status_code == 200, response.text
        return response.json()

    async def start(self, *, queue: str | None = None, policy="tenant_round_robin", acl=False):
        self.queues.add(queue or self.settings.redis_queue_name)
        redis_url = self.settings.redis_url
        if acl:
            user, password = f"dashboard-{uuid4().hex}", secrets.token_urlsafe(24)
            await self.redis.execute_command(
                "ACL", "SETUSER", user, "on", f">{password}", "~*", "+@all"
            )
            self.acl_users.append(user)
            url = urlsplit(redis_url)
            redis_url = urlunsplit(
                url._replace(netloc=f"{user}:{quote(password, safe='')}@{url.hostname}:{url.port}")
            )
        values = {
            "environment": "test",
            "database_url": self.settings.database_url,
            "redis_url": redis_url,
            "redis_queue_name": queue or self.settings.redis_queue_name,
            "jwt_secret": self.settings.jwt_secret,
            "secret_encryption_key": self.settings.secret_encryption_key,
            "bootstrap_admin_enabled": "false",
            "log_level": "ERROR",
            "worker_heartbeat_interval_seconds": 0.3,
            "worker_service_lease_seconds": 1.8,
            "worker_health_refresh_interval_seconds": 0.5,
            "worker_max_concurrency": 1,
            "worker_scheduler_policy": policy,
            "worker_shutdown_grace_seconds": 0.5,
            "worker_shutdown_cancel_seconds": 2,
            "run_attempt_lease_seconds": 10,
            "run_attempt_heartbeat_seconds": 1,
            "worker_recovery_interval_seconds": 300,
            "run_attempt_recovery_interval_seconds": 300,
        }
        # 仅传测试所需环境，子进程在空临时目录启动，不读取仓库 .env。
        env = {key: os.environ[key] for key in ("PATH", "LANG", "LC_ALL") if key in os.environ}
        env.update({f"SUPPORTOPS_{key.upper()}": str(value) for key, value in values.items()})
        env["SUPPORTOPS_DASHBOARD_PROCESS_TEST"] = "1"
        env["PYTHONPATH"] = str(REPOSITORY_ROOT)
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            "from tests.postgres.test_dashboard_workers import run_isolated_worker; "
            "run_isolated_worker()",
            env=env,
            cwd=self.cwd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        self.processes.append(process)

        async def live_leases():
            assert process.returncode is None, f"隔离 Worker 启动退出，代码 {process.returncode}"
            return await self.leases()

        await eventually(
            live_leases, lambda rows: any(f":{process.pid}:" in row.instance_id for row in rows)
        )
        return process

    async def quiesce(self):
        """字段故障注入先停止写入者，不冻结持有数据库锁的事务。"""
        for process in self.processes:
            if process.returncode is None:
                process.terminate()
        codes = await asyncio.gather(
            *(asyncio.wait_for(process.wait(), timeout=5) for process in self.processes)
        )
        assert codes == [0] * len(self.processes)


@pytest.fixture
async def cluster(settings, engine, tmp_path) -> AsyncIterator[WorkerCluster]:
    url = urlsplit(settings.redis_url)
    assert url.hostname in {"127.0.0.1", "localhost"}
    assert make_url(settings.database_url).host in {"127.0.0.1", "localhost"}
    configured = settings.model_copy(
        update={"redis_queue_name": f"dashboard-workers-{uuid4().hex}"}
    )
    redis = Redis.from_url(configured.redis_url, decode_responses=False)
    api_redis = Redis.from_url(configured.redis_url, decode_responses=False)
    await redis.ping()
    tenant_id = uuid4()
    headers = await auth_headers(engine, configured, tenant_id, roles=("platform_admin",))
    factory = create_session_factory(engine)
    async with factory() as session:
        existing_ids = set((await session.scalars(select(AgentRun.id))).all())
        baseline = len((await session.scalars(select(RuntimeServiceLease.id))).all())
    app = create_app(configured, engine=engine, redis=api_redis)
    try:
        async with (
            app.router.lifespan_context(app),
            AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
        ):
            value = WorkerCluster(
                configured, engine, redis, client, headers, tenant_id, tmp_path, baseline
            )
            try:
                yield value
            finally:
                for process in value.processes:
                    if process.returncode is None:
                        process.send_signal(signal.SIGCONT)
                        process.terminate()
                for process in value.processes:
                    try:
                        await asyncio.wait_for(process.wait(), timeout=5)
                    except TimeoutError:
                        process.kill()
                        await asyncio.wait_for(process.wait(), timeout=3)
                rows = await value.leases()
                async with factory() as session, session.begin():
                    for row in rows:
                        lease = await session.get(RuntimeServiceLease, row.id)
                        lease.expires_at = utc_now() - timedelta(seconds=1)
                    # 仅收口本例合成任务，防止下一用例的全局恢复扫描领取。
                    runs = (
                        await session.scalars(
                            select(AgentRun).where(AgentRun.id.not_in(existing_ids))
                        )
                    ).all()
                    for run in runs:
                        if run.status == RunStatus.QUEUED:
                            run.status, run.finished_at = RunStatus.CANCELLED, utc_now()
                        if run.current_attempt_id:
                            attempt = await session.get(RunAttempt, run.current_attempt_id)
                            if attempt.status == "running":
                                attempt.lease_expires_at = utc_now() + timedelta(days=1)
                await retire_test_run_notifications(engine, existing_ids)
                await redis.delete(configured.redis_queue_name, *value.queues)
                if value.acl_users:
                    await redis.execute_command("ACL", "DELUSER", *value.acl_users)
    finally:
        await api_redis.aclose()
        await redis.aclose()


async def test_independent_process_heartbeats_survive_original_expiry_and_sigterm(cluster):
    # 兼容库内已有的旧命名/非进程租约，只观察本例启动的确切进程。
    async with cluster.factory() as session, session.begin():
        session.add(
            RuntimeServiceLease(
                instance_id=f"legacy-{uuid4()}",
                heartbeat_at=utc_now(),
                expires_at=utc_now() - timedelta(seconds=1),
                service_status="stopped",
            )
        )
    first, second = await cluster.start(), await cluster.start()
    initial = await cluster.leases()
    assert len(initial) == 2 and first.pid != second.pid
    expiry = max(row.expires_at for row in initial)
    renewed = await eventually(
        cluster.leases,
        lambda rows: (
            utc_now() > expiry
            and all(row.heartbeat_at > expiry and row.expires_at > utc_now() for row in rows)
        ),
    )
    assert len({row.instance_id for row in renewed}) == 2
    summary = await cluster.summary()
    assert summary["service"] == {
        "status": "healthy",
        "instances": 2,
        "observed_instances": cluster.observed_baseline + 3,
        "available_instances": 2,
    }
    assert summary["metrics"]["queue_depth"]["value"] == 0
    first.terminate()
    assert await asyncio.wait_for(first.wait(), timeout=5) == 0
    body = await eventually(
        cluster.summary, lambda body: body["service"]["available_instances"] == 1
    )
    assert body["service"]["status"] == "healthy", [
        row.metadata_payload for row in await cluster.leases()
    ]
    second.terminate()
    assert await asyncio.wait_for(second.wait(), timeout=5) == 0
    body = await cluster.summary()
    assert body["service"]["status"] == "unavailable"
    assert body["metrics"]["queue_depth"]["value"] is None


async def test_worker_alert_maintenance_can_group_current_run_denials_in_postgres(cluster):
    run_id = await seed_run(cluster.engine, cluster.settings)
    async with cluster.factory() as session, session.begin():
        run = await session.get(AgentRun, run_id)
        sequence = (
            await session.scalar(
                select(func.max(RunEvent.sequence)).where(RunEvent.run_id == run_id)
            )
            or 0
        )
        for index, code in enumerate(
            ("TOOL_ACCESS_DENIED", "TOOL_ACCESS_DENIED", "TOOL_REVOKED"), 1
        ):
            session.add(
                RunEvent(
                    tenant_id=run.tenant_id,
                    run_id=run_id,
                    sequence=sequence + index,
                    event_type="tool.denied",
                    data={"code": code},
                    correlation_id=run.correlation_id,
                )
            )
    await scan_runtime_alerts(cluster.factory, settings=cluster.settings)
    async with cluster.factory() as session:
        alert = await session.scalar(
            select(RuntimeAlert).where(
                RuntimeAlert.run_id == run_id, RuntimeAlert.rule_code == "AUTHORIZATION_DENIED"
            )
        )
        assert alert is not None and alert.metadata_payload["denied_call_count"] == 3


@pytest.mark.parametrize("policy", ["tenant_round_robin", "fifo"])
async def test_real_backlog_is_not_summed_across_busy_workers_or_tenants(cluster, policy):
    runs = [await seed_run(cluster.engine, cluster.settings) for _ in range(3)]
    await cluster.start(policy=policy)
    await cluster.start(policy=policy)
    rows = await eventually(
        cluster.leases,
        lambda rows: (
            len(rows) == 2
            and all(
                row.active_run_count == 1
                and row.queue_depth > 0
                and row.metadata_payload["queued_run_count"] == 1
                for row in rows
            )
        ),
    )
    depth = await cluster.redis.llen(cluster.settings.redis_queue_name)
    assert depth > 0 and all(row.queue_depth == depth for row in rows)
    body = await cluster.summary()
    assert body["metrics"]["queue_depth"]["value"] == depth
    assert body["metrics"]["queue_depth"]["sample_count"] == 1
    assert body["service"]["status"] == "healthy", [row.metadata_payload for row in rows]
    async with cluster.factory() as session:
        facts = list((await session.scalars(select(AgentRun).where(AgentRun.id.in_(runs)))).all())
    assert sorted(run.status.value for run in facts) == ["queued", "running", "running"]
    participant = next(run for run in facts if run.status == RunStatus.RUNNING)
    other_headers = await auth_headers(
        cluster.engine,
        cluster.settings,
        participant.tenant_id,
        "dashboard-reader",
        roles=("platform_admin",),
    )
    other = await cluster.client.get("/v1/admin/system/dashboard/summary", headers=other_headers)
    assert other.status_code == 200
    assert other.json()["metrics"]["agent_runs"]["value"] == 1
    assert body["metrics"]["agent_runs"]["value"] == 0
    assert other.json()["metrics"]["queue_depth"]["value"] == depth
    expiry = max(row.expires_at for row in rows)
    await eventually(
        cluster.leases,
        lambda rows: (
            utc_now() > expiry
            and all(row.heartbeat_at > expiry and row.active_run_count == 1 for row in rows)
        ),
    )


async def test_sigkill_expiry_excludes_dead_instance_without_taking_peer_offline(cluster):
    first, second = await cluster.start(), await cluster.start()
    first.kill()
    await asyncio.wait_for(first.wait(), timeout=3)
    body = await eventually(cluster.summary, lambda body: body["service"]["instances"] == 1)
    assert body["service"]["status"] == "healthy", [
        row.metadata_payload for row in await cluster.leases()
    ]
    first_row = next(row for row in await cluster.leases() if f":{first.pid}:" in row.instance_id)
    assert first_row.service_status == "healthy" and first_row.expires_at <= utc_now()
    second.kill()
    await asyncio.wait_for(second.wait(), timeout=3)
    body = await eventually(cluster.summary, lambda body: body["service"]["instances"] == 0)
    assert body["service"]["status"] == "unavailable"
    assert body["metrics"]["queue_depth"]["value"] is None


async def test_sigstop_resume_renews_same_instance_without_duplicating_inventory(cluster):
    first = await cluster.start()
    await cluster.start()
    before = next(row for row in await cluster.leases() if f":{first.pid}:" in row.instance_id)
    first.send_signal(signal.SIGSTOP)
    body = await eventually(cluster.summary, lambda body: body["service"]["instances"] == 1)
    assert body["service"]["status"] == "healthy", [
        row.metadata_payload for row in await cluster.leases()
    ]
    first.send_signal(signal.SIGCONT)
    body = await eventually(cluster.summary, lambda body: body["service"]["instances"] == 2)
    after = next(row for row in await cluster.leases() if f":{first.pid}:" in row.instance_id)
    assert after.id == before.id and after.heartbeat_at > before.expires_at
    assert body["service"]["observed_instances"] == cluster.observed_baseline + 2


async def test_real_redis_llen_permission_failure_is_unknown_then_recovers(cluster):
    await cluster.start(acl=True)
    await cluster.start(acl=True)
    await cluster.redis.execute_command("ACL", "SETUSER", cluster.acl_users[0], "-llen")
    await eventually(
        cluster.leases,
        lambda rows: (
            sum(row.metadata_payload["queue_depth_available"] is False for row in rows) == 1
        ),
    )
    partial = await cluster.summary()
    assert partial["service"]["status"] == "degraded"
    assert partial["service"]["instances"] == 2
    assert partial["metrics"]["queue_depth"]["value"] == 0
    await cluster.redis.execute_command("ACL", "SETUSER", cluster.acl_users[1], "-llen")
    await eventually(
        cluster.leases,
        lambda rows: (
            len(rows) == 2
            and all(
                row.service_status == "degraded"
                and row.metadata_payload["queue_depth_available"] is False
                for row in rows
            )
        ),
    )
    body = await cluster.summary()
    assert body["service"]["status"] == "degraded" and body["service"]["instances"] == 2
    assert body["metrics"]["queue_depth"]["value"] is None
    for user in cluster.acl_users:
        await cluster.redis.execute_command("ACL", "SETUSER", user, "+llen")
    body = await eventually(cluster.summary, lambda body: body["service"]["status"] == "healthy")
    assert body["metrics"]["queue_depth"]["value"] == 0


@pytest.mark.parametrize(
    "mode", ["stale", "future", "naive", "missing", "wrong_queue", "negative", "unavailable"]
)
async def test_invalid_queue_samples_never_fall_back_to_zero_or_old_depth(cluster, mode):
    await cluster.start()
    await cluster.start()
    await cluster.quiesce()
    async with cluster.factory() as session, session.begin():
        for row in await cluster.leases():
            lease = await session.get(RuntimeServiceLease, row.id)
            now = utc_now()
            lease.expires_at = now + timedelta(seconds=15)
            lease.heartbeat_at = now
            lease.service_status = "healthy"
            lease.queue_depth = -1 if mode == "negative" else 7
            metadata = dict(lease.metadata_payload)
            observed = (
                now - timedelta(seconds=91)
                if mode == "stale"
                else now + timedelta(seconds=6)
                if mode == "future"
                else now
            )
            metadata["queue_depth_observed_at"] = (
                observed.replace(tzinfo=None) if mode == "naive" else observed
            ).isoformat()
            if mode == "missing":
                metadata.pop("queue_depth_observed_at")
            if mode == "wrong_queue":
                metadata["queue_name"] = "other-isolated-queue"
            if mode == "unavailable":
                metadata["queue_depth_available"] = False
            lease.metadata_payload = metadata
    body = await cluster.summary()
    assert body["service"]["instances"] == 2
    assert body["metrics"]["queue_depth"]["value"] is None
    assert body["metrics"]["queue_depth"]["available"] is False
    assert body["metrics"]["queue_depth"]["sample_count"] == 0


async def test_latest_same_queue_sample_wins_without_adding_two_observations(cluster):
    await cluster.start()
    await cluster.start()
    await cluster.quiesce()
    rows = await cluster.leases()
    await cluster.redis.rpush(cluster.settings.redis_queue_name, *(str(uuid4()) for _ in range(3)))
    depths = [await cluster.redis.llen(cluster.settings.redis_queue_name)]
    await cluster.redis.rpush(cluster.settings.redis_queue_name, str(uuid4()), str(uuid4()))
    depths.append(await cluster.redis.llen(cluster.settings.redis_queue_name))
    async with cluster.factory() as session, session.begin():
        for index, row in enumerate(rows):
            lease = await session.get(RuntimeServiceLease, row.id)
            now = utc_now()
            lease.heartbeat_at, lease.expires_at = now, now + timedelta(seconds=15)
            lease.service_status = "healthy"
            lease.queue_depth = depths[index]
            lease.metadata_payload = {
                **lease.metadata_payload,
                "queue_depth_observed_at": (now - timedelta(seconds=1 - index)).isoformat(),
            }
    body = await cluster.summary()
    assert depths == [3, 5]
    assert body["metrics"]["queue_depth"]["value"] == 5
    assert body["metrics"]["queue_depth"]["sample_count"] == 1


async def test_other_queue_latest_sample_cannot_replace_requested_queue(cluster):
    sibling_queue = cluster.settings.redis_queue_name + "-sibling"
    await cluster.start()
    await cluster.start(queue=sibling_queue)
    await cluster.quiesce()
    await cluster.redis.rpush(cluster.settings.redis_queue_name, *(str(uuid4()) for _ in range(3)))
    await cluster.redis.rpush(sibling_queue, *(str(uuid4()) for _ in range(5)))
    async with cluster.factory() as session, session.begin():
        for row in await cluster.leases():
            lease = await session.get(RuntimeServiceLease, row.id)
            own = lease.metadata_payload["queue_name"] == cluster.settings.redis_queue_name
            now = utc_now()
            lease.service_status = "healthy"
            lease.heartbeat_at, lease.expires_at = now, now + timedelta(seconds=15)
            lease.queue_depth = await cluster.redis.llen(lease.metadata_payload["queue_name"])
            lease.metadata_payload = {
                **lease.metadata_payload,
                "queue_depth_observed_at": (now - timedelta(seconds=int(own))).isoformat(),
            }
    body = await cluster.summary()
    assert body["service"]["instances"] == 2
    assert body["metrics"]["queue_depth"]["value"] == 3


@pytest.mark.parametrize("seconds", [4, 6])
async def test_service_clock_tolerance_matches_dashboard_and_health_coordinator(cluster, seconds):
    await provision_agent(cluster.engine, cluster.settings, cluster.tenant_id)
    await cluster.start()
    await cluster.start()
    await cluster.quiesce()
    async with cluster.factory() as session, session.begin():
        for row in await cluster.leases():
            lease = await session.get(RuntimeServiceLease, row.id)
            lease.service_status = "healthy"
            lease.heartbeat_at = utc_now() + timedelta(seconds=seconds)
            lease.expires_at = utc_now() + timedelta(seconds=15)
        snapshots = await refresh_agent_runtime_health(
            session, tenant_id=cluster.tenant_id, settings=cluster.settings
        )
        assert snapshots[0].health_status == ("unknown" if seconds > 5 else "no_recent_activity")
    body = await cluster.summary()
    assert body["service"]["status"] == ("unavailable" if seconds > 5 else "healthy")
    assert body["service"]["instances"] == (0 if seconds > 5 else 2)


def run_isolated_worker() -> None:
    """测试子进程入口；不修改生产注册或调度实现。"""
    assert os.getenv("SUPPORTOPS_DASHBOARD_PROCESS_TEST") == "1"
    configured = Settings(_env_file=None)
    assert configured.environment == "test" and configured.database_url.endswith("_test")
    assert urlsplit(configured.redis_url).hostname in {"127.0.0.1", "localhost"}
    from supportops_core import worker_runtime
    from supportops_worker.main import main

    async def blocked_model(*args, **kwargs):
        await asyncio.Event().wait()

    worker_runtime.invoke_agent = blocked_model
    main()
