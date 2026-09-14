import asyncio
import os
import signal
import socket
from datetime import UTC, datetime, timedelta

import structlog
from redis.asyncio import Redis
from sqlalchemy import select
from supportops_core.agent_runtime import prune_expired_checkpoints
from supportops_core.config import get_settings
from supportops_core.db import create_engine, create_session_factory
from supportops_core.health_services import refresh_all_agent_runtime_health
from supportops_core.logging import configure_logging
from supportops_core.models import RuntimeServiceLease
from supportops_core.queue import RunQueue
from supportops_core.services import recover_queued_runs
from supportops_core.worker_runtime import execute_agent_run

logger = structlog.get_logger()


async def run_worker() -> None:
    settings = get_settings()
    configure_logging(service="supportops-agent-worker", level=settings.log_level)
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    redis = Redis.from_url(settings.redis_url, decode_responses=False)
    queue = RunQueue(redis, settings.redis_queue_name)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signal_name in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signal_name, stop.set)

    async def recover() -> None:
        for run_id in await recover_queued_runs(session_factory):
            await queue.enqueue(run_id)

    await recover()
    logger.info("worker_started", queue=settings.redis_queue_name)
    instance_id = f"{socket.gethostname()}:{os.getpid()}"

    async def heartbeat() -> None:
        now = datetime.now(UTC)
        depth = int(await redis.llen(settings.redis_queue_name))
        async with session_factory() as session, session.begin():
            lease = await session.scalar(
                select(RuntimeServiceLease)
                .where(RuntimeServiceLease.instance_id == instance_id)
                .with_for_update()
            )
            if lease is None:
                lease = RuntimeServiceLease(
                    instance_id=instance_id,
                    heartbeat_at=now,
                    expires_at=now + timedelta(seconds=15),
                    queue_depth=depth,
                    active_run_count=0,
                    service_status="healthy",
                )
                session.add(lease)
            else:
                lease.heartbeat_at = now
                lease.expires_at = now + timedelta(seconds=15)
                lease.queue_depth = depth
                lease.service_status = "healthy"

    await heartbeat()
    last_heartbeat = loop.time()
    last_recovery = loop.time()
    last_checkpoint_cleanup = loop.time()
    last_health_refresh = loop.time()
    try:
        while not stop.is_set():
            run_id = await queue.dequeue(timeout_seconds=1)
            if run_id is not None:
                await execute_agent_run(session_factory, run_id, settings=settings)
            if loop.time() - last_heartbeat >= 5:
                try:
                    await heartbeat()
                except Exception:
                    logger.exception("worker_heartbeat_failed", instance_id=instance_id)
                last_heartbeat = loop.time()
            if loop.time() - last_health_refresh >= 30:
                try:
                    async with session_factory() as session, session.begin():
                        refreshed = await refresh_all_agent_runtime_health(
                            session, now=datetime.now(UTC)
                        )
                    logger.info("agent_runtime_health_refreshed", tenants=refreshed)
                except Exception:
                    # 健康快照属于观测读模型，失败不应阻断 Run 消费。
                    logger.exception("agent_runtime_health_refresh_failed")
                last_health_refresh = loop.time()
            if loop.time() - last_recovery >= settings.worker_recovery_interval_seconds:
                await recover()
                last_recovery = loop.time()
            if loop.time() - last_checkpoint_cleanup >= (
                settings.langgraph_checkpoint_cleanup_interval_seconds
            ):
                try:
                    removed = await prune_expired_checkpoints(settings)
                    logger.info("checkpoint_retention_cleanup", removed_threads=removed)
                except Exception:
                    # 保留清理失败不应阻断在线 Run；下一轮周期继续重试。
                    logger.exception("checkpoint_retention_cleanup_failed")
                last_checkpoint_cleanup = loop.time()
    finally:
        try:
            async with session_factory() as session, session.begin():
                lease = await session.scalar(
                    select(RuntimeServiceLease)
                    .where(RuntimeServiceLease.instance_id == instance_id)
                    .with_for_update()
                )
                if lease is not None:
                    lease.service_status = "stopped"
                    lease.expires_at = datetime.now(UTC)
        except Exception:
            logger.exception("worker_lease_shutdown_failed", instance_id=instance_id)
        await redis.aclose()
        await engine.dispose()
        logger.info("worker_stopped")


def main() -> None:
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
