import asyncio
import signal

import structlog
from redis.asyncio import Redis
from supportops_core.config import get_settings
from supportops_core.db import create_engine, create_session_factory
from supportops_core.logging import configure_logging
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
    last_recovery = loop.time()
    try:
        while not stop.is_set():
            run_id = await queue.dequeue(timeout_seconds=1)
            if run_id is not None:
                await execute_agent_run(session_factory, run_id, settings=settings)
            if loop.time() - last_recovery >= settings.worker_recovery_interval_seconds:
                await recover()
                last_recovery = loop.time()
    finally:
        await redis.aclose()
        await engine.dispose()
        logger.info("worker_stopped")


def main() -> None:
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
