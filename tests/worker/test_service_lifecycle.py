"""驱动完整 Worker 主循环，验证独立心跳、监督和停机。"""

import asyncio
import os
import signal
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from redis.asyncio import Redis
from sqlalchemy import event, func, select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine
from supportops_core.config import Settings
from supportops_core.db import create_engine, create_session_factory
from supportops_core.enums import RunStatus
from supportops_core.models import AgentRun, RuntimeServiceLease
from supportops_worker import main as worker_main

from tests.conftest import FakeRedis, retire_test_run_notifications
from tests.worker.test_worker import seed_run


class ServiceRedis(FakeRedis):
    async def llen(self, name: str) -> int:
        return len(self.queues[name])

    async def blpop(
        self,
        name: str,
        timeout: int = 0,  # noqa: ASYNC109 - 兼容 redis-py 签名
    ) -> tuple[bytes, bytes] | None:
        if not self.queues[name]:
            await asyncio.sleep(0.01)
        return await super().blpop(name, timeout=timeout)

    async def aclose(self) -> None:
        pass


@dataclass
class ServiceEnvironment:
    settings: Settings
    engine: AsyncEngine
    redis: Redis | ServiceRedis
    connect_redis: Callable[[], Redis | ServiceRedis]


@pytest.fixture(params=["sqlite", "postgres"])
async def service_environment(
    request: pytest.FixtureRequest,
    settings: Settings,
    engine: AsyncEngine,
) -> AsyncIterator[ServiceEnvironment]:
    if request.param == "postgres":
        if os.getenv("SUPPORTOPS_RUN_POSTGRES_TESTS") != "1":
            pytest.skip("真实 PostgreSQL/Redis 用例需显式启用隔离测试环境")
        configured = Settings()
        database = make_url(configured.database_url)
        assert configured.environment == "test"
        assert database.drivername == "postgresql+asyncpg"
        assert database.database and database.database.endswith("_test")
        settings = configured
        engine = create_engine(settings)
        redis = Redis.from_url(settings.redis_url, decode_responses=False)

        def connect_redis() -> Redis:
            return Redis.from_url(settings.redis_url, decode_responses=False)

    else:
        redis = ServiceRedis()

        def connect_redis() -> ServiceRedis:
            return redis

    settings = settings.model_copy(
        update={
            "redis_queue_name": f"worker-test-{uuid4().hex}",
            "worker_scheduler_policy": "fifo",
            "worker_max_concurrency": 1,
            "worker_heartbeat_interval_seconds": 0.1,
            "worker_service_lease_seconds": 0.6,
            "worker_shutdown_grace_seconds": 0.2,
            "worker_shutdown_cancel_seconds": 1,
            "worker_heartbeat_failure_limit": 2,
        }
    )
    async with create_session_factory(engine)() as session:
        existing_run_ids = set((await session.scalars(select(AgentRun.id))).all())
    try:
        yield ServiceEnvironment(settings, engine, redis, connect_redis)
    finally:
        if isinstance(redis, Redis):
            try:
                await retire_test_run_notifications(engine, existing_run_ids)
                await redis.delete(settings.redis_queue_name)
            finally:
                await redis.aclose()
                await engine.dispose()


def configure_worker(
    monkeypatch: pytest.MonkeyPatch,
    environment: ServiceEnvironment,
) -> list[Callable[[], None]]:
    """仅隔离无关维护和信号注册，保留真实主循环、租约事务与 Redis 通知。"""
    stops: list[Callable[[], None]] = []
    loop = asyncio.get_running_loop()

    def add_signal_handler(signum: signal.Signals, callback: Callable[[], None]) -> None:
        if signum == signal.SIGTERM:
            stops.append(callback)

    monkeypatch.setattr(loop, "add_signal_handler", add_signal_handler)
    monkeypatch.setattr(loop, "remove_signal_handler", lambda _: True)
    monkeypatch.setattr(worker_main, "get_settings", lambda: environment.settings)
    monkeypatch.setattr(worker_main, "create_engine", lambda _: create_engine(environment.settings))
    monkeypatch.setattr(
        worker_main,
        "Redis",
        SimpleNamespace(
            from_url=lambda *args, **kwargs: environment.connect_redis(),
        ),
    )

    async def no_work(*args: object, **kwargs: object) -> None:
        return None

    async def no_count(*args: object, **kwargs: object) -> int:
        return 0

    async def no_queued(*args: object, **kwargs: object) -> list[UUID]:
        return []

    for name in (
        "claim_probe",
        "claim_action",
        "recover_expired_probes",
        "recover_expired_actions",
        "maintain_waiting_approval",
        "scan_runtime_alerts",
        "scan_release_rollouts",
        "process_one_probe",
    ):
        monkeypatch.setattr(worker_main, name, no_work)
    for name in (
        "dispatch_run_notifications",
        "recover_expired_attempts",
        "consume_knowledge_events",
        "invalidate_expired_knowledge",
        "refresh_all_agent_runtime_health",
        "prune_expired_checkpoints",
    ):
        monkeypatch.setattr(worker_main, name, no_count)
    monkeypatch.setattr(worker_main, "recover_queued_runs", no_queued)
    return stops


async def read_leases(environment: ServiceEnvironment) -> list[RuntimeServiceLease]:
    async with create_session_factory(environment.engine)() as session:
        leases = list((await session.scalars(select(RuntimeServiceLease))).all())
        return [
            lease
            for lease in leases
            if lease.metadata_payload.get("queue_name") == environment.settings.redis_queue_name
        ]


async def eventually(operation: Callable, predicate: Callable, *, seconds: float = 5):
    async with asyncio.timeout(seconds):
        while True:
            value = await operation()
            if predicate(value):
                return value
            await asyncio.sleep(0.02)


async def test_multiple_workers_keep_heartbeats_and_drain_only_owned_runs(
    service_environment: ServiceEnvironment,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    environment = service_environment
    stops = configure_worker(monkeypatch, environment)
    factory = create_session_factory(environment.engine)
    async with factory() as session:
        baseline = int(
            await session.scalar(
                select(func.count())
                .select_from(AgentRun)
                .where(
                    AgentRun.status == RunStatus.QUEUED,
                )
            )
            or 0
        )
    run_ids = [await seed_run(environment.engine, environment.settings) for _ in range(3)]
    started: set[UUID] = set()
    finished: set[UUID] = set()

    async def long_execution(session_factory, run_id: UUID, *, on_claim, **kwargs) -> None:
        async with session_factory() as session, session.begin():
            run = await session.get(AgentRun, run_id)
            assert run is not None and run.status == RunStatus.QUEUED
            run.status = RunStatus.RUNNING
        on_claim()
        started.add(run_id)
        try:
            await asyncio.Event().wait()
        finally:
            finished.add(run_id)

    monkeypatch.setattr(worker_main, "execute_agent_run", long_execution)
    workers = [asyncio.create_task(worker_main.run_worker()) for _ in range(2)]
    try:
        await eventually(lambda: read_leases(environment), lambda leases: len(leases) == 2)
        for run_id in run_ids:
            await environment.redis.rpush(environment.settings.redis_queue_name, str(run_id))
        initial = await eventually(
            lambda: read_leases(environment),
            lambda leases: (
                len(started) == 2
                and len(leases) == 2
                and all(
                    lease.active_run_count == 1
                    and lease.queue_depth == 1
                    and lease.metadata_payload["queued_run_count"] == baseline + 1
                    for lease in leases
                )
            ),
        )
        assert len({lease.instance_id for lease in initial}) == 2
        # 故意跨过原服务租约截止时间，不能只验证一次心跳。
        original_expiry = max(lease.expires_at.replace(tzinfo=UTC) for lease in initial)
        renewed = await eventually(
            lambda: read_leases(environment),
            lambda leases: (
                datetime.now(UTC) > original_expiry
                and len(leases) == 2
                and all(
                    lease.service_status == "healthy"
                    and lease.active_run_count == 1
                    and lease.expires_at.replace(tzinfo=UTC) > datetime.now(UTC)
                    for lease in leases
                )
            ),
        )
        assert all(
            lease.heartbeat_at.replace(tzinfo=UTC) > original_expiry - timedelta(seconds=0.3)
            for lease in renewed
        )
        stops[0]()
        draining = await eventually(
            lambda: read_leases(environment),
            lambda leases: any(
                lease.metadata_payload["draining"]
                and lease.service_status == "degraded"
                and lease.active_run_count == 1
                for lease in leases
            ),
        )
        assert len(draining) == 2
        await asyncio.wait_for(workers[0], timeout=3)
        assert len(started) == 2 and len(finished) == 1
        stopped = await read_leases(environment)
        assert sorted(lease.service_status for lease in stopped) == ["healthy", "stopped"]
        assert sorted(lease.active_run_count for lease in stopped) == [0, 1]
        assert await environment.redis.llen(environment.settings.redis_queue_name) == 1
        stops[1]()
        await asyncio.wait_for(workers[1], timeout=3)
        assert len(finished) == 2
        assert all(
            lease.service_status == "stopped"
            and lease.active_run_count == 0
            and not lease.metadata_payload["shutdown_incomplete"]
            for lease in await read_leases(environment)
        )
    finally:
        for stop in stops:
            stop()
        await asyncio.gather(*workers, return_exceptions=True)


@pytest.mark.parametrize("persistent", [False, True])
async def test_heartbeat_database_failure_recovers_or_stops_claiming(
    service_environment: ServiceEnvironment,
    monkeypatch: pytest.MonkeyPatch,
    persistent: bool,
) -> None:
    environment = service_environment
    stops = configure_worker(monkeypatch, environment)
    engine = create_engine(environment.settings)
    monkeypatch.setattr(worker_main, "create_engine", lambda _: engine)
    failed = asyncio.Event()
    enabled = False
    failures = 0

    def fail_heartbeat(connection, cursor, statement, parameters, context, executemany) -> None:
        nonlocal failures
        if enabled and "FROM runtime_service_leases" in statement and (persistent or failures == 0):
            failures += 1
            failed.set()
            raise OSError("isolated heartbeat database fault")

    event.listen(engine.sync_engine, "before_cursor_execute", fail_heartbeat)
    task = asyncio.create_task(worker_main.run_worker())
    try:
        await eventually(lambda: read_leases(environment), lambda leases: len(leases) == 1)
        enabled = True
        await asyncio.wait_for(failed.wait(), timeout=3)
        if persistent:
            with pytest.raises(RuntimeError, match="监督任务失败"):
                await asyncio.wait_for(asyncio.shield(task), timeout=3)
            assert failures >= environment.settings.worker_heartbeat_failure_limit
        else:
            await eventually(
                lambda: read_leases(environment),
                lambda leases: (
                    len(leases) == 1
                    and leases[0].service_status == "healthy"
                    and leases[0].metadata_payload["maintenance_failures"].get("heartbeat") == 0
                ),
            )
            assert not task.done() and failures == 1
            stops[0]()
            await asyncio.wait_for(task, timeout=3)
        if persistent:
            # 数据库仍故障时无法写 stopped；原租约到期后不能继续被视为在线。
            await eventually(
                lambda: read_leases(environment),
                lambda leases: (
                    len(leases) == 1
                    and leases[0].expires_at.replace(tzinfo=UTC) <= datetime.now(UTC)
                ),
            )
        else:
            assert all(
                lease.service_status == "stopped" for lease in await read_leases(environment)
            )
    finally:
        enabled = False
        for stop in stops:
            stop()
        await asyncio.gather(task, return_exceptions=True)
        event.remove(engine.sync_engine, "before_cursor_execute", fail_heartbeat)
