"""有界 Run 消费、独立服务心跳、维护监督与优雅停机。"""

import asyncio
import os
import signal
import socket
from collections.abc import Awaitable, Callable, Coroutine
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import structlog
from redis.asyncio import Redis
from sqlalchemy import func, select
from supportops_core.agent_runtime import prune_expired_checkpoints
from supportops_core.config import get_settings
from supportops_core.db import create_engine, create_session_factory
from supportops_core.enums import RunStatus
from supportops_core.health_services import refresh_all_agent_runtime_health
from supportops_core.knowledge_index_services import consume_knowledge_events, invalidate_expired_knowledge
from supportops_core.logging import configure_logging
from supportops_core.models import AgentRun, RuntimeServiceLease
from supportops_core.queue import RunQueue
from supportops_core.run_outbox_services import dispatch_run_notifications
from supportops_core.run_attempt_services import RunClaim, recover_expired_attempts
from supportops_core.run_scheduler import claim_next_fair_run, ensure_scheduler_state
from supportops_core.model_probe_services import claim_probe, execute_probe, recover_expired_probes
from supportops_core.action_runtime import claim_action, execute_action, recover_expired_actions
from supportops_core.approval_services import maintain_waiting_approval
from supportops_core.runtime_alert_services import scan_runtime_alerts
from supportops_core.release_rollout_services import scan_release_rollouts
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
    # 加启动随机值，防止同主机 PID 复用覆盖上一实例的租约事实。
    instance_id = f"{socket.gethostname()[:60]}:{os.getpid()}:{uuid4().hex[:12]}"
    log = logger.bind(instance_id=instance_id)
    stop = asyncio.Event()
    maintenance_stop = asyncio.Event()
    capacity_changed = asyncio.Event()
    execution_tasks: dict[UUID, asyncio.Task[None]] = {}
    active_run_ids: set[UUID] = set()
    background_tasks: list[asyncio.Task[None]] = []
    maintenance_failures: dict[str, int] = {}
    service_error: BaseException | None = None
    draining = False
    active_probe_id: UUID | None = None
    active_action_id: UUID | None = None
    loop = asyncio.get_running_loop()
    signals = (signal.SIGINT, signal.SIGTERM)
    for signal_name in signals:
        loop.add_signal_handler(signal_name, stop.set)

    async def heartbeat() -> None:
        queue_available = True
        depth: int | None = None
        try:
            async with asyncio.timeout(settings.worker_heartbeat_interval_seconds):
                depth = int(await redis.llen(settings.redis_queue_name))
        except Exception:
            queue_available = False
            log.warning("worker_queue_depth_unavailable")
        now = datetime.now(UTC)
        async with session_factory() as session, session.begin():
            queued_count = int(await session.scalar(
                select(func.count()).select_from(AgentRun)
                .where(AgentRun.status == RunStatus.QUEUED)
            ) or 0)
            lease = await session.scalar(select(RuntimeServiceLease).where(
                RuntimeServiceLease.instance_id == instance_id,
            ).with_for_update())
            if lease is None:
                lease = RuntimeServiceLease(instance_id=instance_id)
                session.add(lease)
            lease.heartbeat_at = now
            lease.expires_at = now + timedelta(seconds=settings.worker_service_lease_seconds)
            # Redis 中可能有重复通知，不能将其当作持久化 queued Run 数。
            if depth is not None:
                lease.queue_depth = depth
            lease.active_run_count = len(active_run_ids)
            lease.service_status = (
                "degraded" if draining or service_error or not queue_available
                or any(maintenance_failures.values()) else "healthy"
            )
            lease.metadata_payload = {
                "queue_name": settings.redis_queue_name,
                "queue_depth_available": queue_available,
                "queue_depth_observed_at": now.isoformat() if queue_available else None,
                "queued_run_count": queued_count,
                "scheduler_policy": settings.worker_scheduler_policy,
                "active_model_probe_id": str(active_probe_id) if active_probe_id else None,
                "active_business_action_id": str(active_action_id) if active_action_id else None,
                "max_concurrency": settings.worker_max_concurrency,
                "draining": draining,
                "maintenance_failures": dict(maintenance_failures),
            }

    async def recover() -> None:
        for run_id in await recover_queued_runs(session_factory):
            if maintenance_stop.is_set():
                break
            await queue.enqueue(run_id)

    async def refresh_health() -> None:
        async with session_factory() as session, session.begin():
            refreshed = await refresh_all_agent_runtime_health(session, now=datetime.now(UTC), settings=settings)
        log.info("agent_runtime_health_refreshed", tenants=refreshed)

    async def refresh_alerts() -> None:
        await scan_runtime_alerts(session_factory, settings=settings)

    async def refresh_rollouts() -> None:
        await scan_release_rollouts(session_factory, settings=settings)

    async def dispatch_notifications() -> None:
        delivered = await dispatch_run_notifications(session_factory, queue=queue, settings=settings)
        if delivered:
            log.info("run_outbox_dispatched", delivered=delivered)

    async def recover_attempts() -> None:
        recovered = await recover_expired_attempts(session_factory, settings=settings)
        if recovered:
            log.info("expired_run_attempts_recovered", recovered=recovered)

    async def index_knowledge() -> None:
        consumed = await consume_knowledge_events(session_factory, settings=settings)
        invalidated = await invalidate_expired_knowledge(session_factory)
        if consumed or invalidated:
            log.info("knowledge_keyword_index_updated", consumed=consumed, invalidated=invalidated)

    async def clean_checkpoints() -> None:
        removed = await prune_expired_checkpoints(settings)
        log.info("checkpoint_retention_cleanup", removed_threads=removed)

    async def periodic(
        name: str, interval: float, operation: Callable[[], Awaitable[None]],
        *, critical: bool = False,
    ) -> None:
        while not maintenance_stop.is_set():
            started = loop.time()
            try:
                # 防止维护查询一直挂起；心跳有独立的、更短的超时预算。
                timeout = (2 * interval if critical else settings.worker_maintenance_timeout_seconds)
                async with asyncio.timeout(timeout):
                    await operation()
                maintenance_failures[name] = 0
            except Exception as exc:
                maintenance_failures[name] = maintenance_failures.get(name, 0) + 1
                log.exception("worker_periodic_failed", task=name, failures=maintenance_failures[name])
                if critical and maintenance_failures[name] >= settings.worker_heartbeat_failure_limit:
                    raise RuntimeError("Worker 连续心跳失败，停止新领取") from exc
            remaining = max(0.01, interval - (loop.time() - started))
            try:
                await asyncio.wait_for(maintenance_stop.wait(), timeout=remaining)
            except TimeoutError:
                pass

    def supervise(task: asyncio.Task[None]) -> None:
        nonlocal service_error
        if task.cancelled():
            if not stop.is_set():
                service_error = RuntimeError(f"Worker 后台任务意外取消：{task.get_name()}")
                stop.set()
            return
        error = task.exception()
        if error is not None or not stop.is_set():
            service_error = error or RuntimeError(f"Worker 后台任务意外退出：{task.get_name()}")
            log.error("worker_supervised_task_stopped", task=task.get_name(), error_type=type(service_error).__name__)
            stop.set()

    def start_background(name: str, coroutine: Coroutine[object, object, None]) -> None:
        task = asyncio.create_task(coroutine, name=name)
        task.add_done_callback(supervise)
        background_tasks.append(task)

    async def execute(run_id: UUID, claim: RunClaim | None = None) -> None:
        nonlocal service_error
        try:
            await execute_agent_run(
                session_factory, run_id, settings=settings,
                owner_id=instance_id,
                preclaimed=claim,
                on_claim=lambda: active_run_ids.add(run_id),
            )
        except Exception as exc:
            # 业务失败由执行器落库；逃出执行器的基础设施错误需要停止新领取。
            service_error = exc
            log.exception("worker_execution_task_failed", run_id=str(run_id))
            stop.set()
        finally:
            active_run_ids.discard(run_id)
            execution_tasks.pop(run_id, None)
            capacity_changed.set()

    async def consume_probes() -> None:
        nonlocal active_probe_id
        while not stop.is_set():
            async with asyncio.timeout(settings.worker_maintenance_timeout_seconds):
                async with session_factory() as session, session.begin():
                    await recover_expired_probes(session, settings=settings)
                    if stop.is_set():
                        break
                    identity = await claim_probe(session, settings=settings, owner_id=instance_id)
                    if stop.is_set():
                        await session.rollback()
                        break
            if identity is not None:
                active_probe_id = identity.job_id
                try:
                    await execute_probe(session_factory, identity=identity, settings=settings)
                finally:
                    active_probe_id = None
            else:
                try:
                    await asyncio.wait_for(stop.wait(), timeout=settings.model_probe_poll_seconds)
                except TimeoutError:
                    pass

    async def consume_actions() -> None:
        nonlocal active_action_id
        while not stop.is_set():
            async with asyncio.timeout(settings.worker_maintenance_timeout_seconds):
                async with session_factory() as session, session.begin():
                    await recover_expired_actions(session)
                    await maintain_waiting_approval(session, settings=settings)
                    if stop.is_set():
                        break
                    permit = await claim_action(session, settings=settings, owner_id=instance_id)
                    if stop.is_set():
                        await session.rollback()
                        break
            if permit is not None:
                active_action_id = permit.action_id
                try:
                    await execute_action(session_factory, permit=permit, settings=settings)
                finally:
                    active_action_id = None
            else:
                try:
                    await asyncio.wait_for(stop.wait(), timeout=settings.action_poll_seconds)
                except TimeoutError:
                    pass

    async def consume() -> None:
        while not stop.is_set():
            if len(execution_tasks) >= settings.worker_max_concurrency:
                capacity_changed.clear()
                try:
                    await asyncio.wait_for(capacity_changed.wait(), timeout=1)
                except TimeoutError:
                    pass
                continue
            if settings.worker_scheduler_policy == "tenant_round_robin":
                async with asyncio.timeout(settings.worker_maintenance_timeout_seconds):
                    async with session_factory() as session, session.begin():
                        if stop.is_set():
                            break
                        claim = await claim_next_fair_run(session, settings=settings, owner_id=instance_id)
                        if stop.is_set():
                            # 停机在数据库领取期间发生，回滚游标与 Run/attempt 整个事务。
                            await session.rollback()
                            break
                if claim is not None:
                    run_id = claim.identity.run_id
                    active_run_ids.add(run_id)
                    execution_tasks[run_id] = asyncio.create_task(execute(run_id, claim), name=f"run:{run_id}")
                    continue
                # 无可执行事实或另一个 Worker 正在轮转时，用通知等待降低空轮询。
                # UUID 仅作唤醒；已投递事实仍在数据库，消费通知不会跳过死信。
                try:
                    await queue.dequeue(timeout_seconds=1)
                except ValueError:
                    log.warning("worker_invalid_queue_message")
                continue
            try:
                run_id = await queue.dequeue(timeout_seconds=1)
            except ValueError:
                log.warning("worker_invalid_queue_message")
                continue
            if run_id is None or run_id in execution_tasks:
                continue
            if stop.is_set():
                # 已弹出的通知不再执行；重新通知失败时仍由 queued 事实恢复。
                async with asyncio.timeout(settings.worker_maintenance_timeout_seconds):
                    await queue.enqueue(run_id)
                break
            execution_tasks[run_id] = asyncio.create_task(execute(run_id), name=f"run:{run_id}")

    try:
        if settings.worker_scheduler_policy == "tenant_round_robin":
            async with session_factory() as session, session.begin():
                await ensure_scheduler_state(session, queue_name=settings.redis_queue_name)
        async with asyncio.timeout(2 * settings.worker_heartbeat_interval_seconds):
            await heartbeat()
        start_background("heartbeat", periodic(
            "heartbeat", settings.worker_heartbeat_interval_seconds, heartbeat, critical=True,
        ))
        start_background("recovery", periodic("recovery", settings.worker_recovery_interval_seconds, recover))
        start_background("run_outbox", periodic("run_outbox", settings.run_outbox_interval_seconds, dispatch_notifications))
        start_background("attempt_recovery", periodic("attempt_recovery", settings.run_attempt_recovery_interval_seconds, recover_attempts))
        start_background("knowledge_index", periodic("knowledge_index", settings.knowledge_index_interval_seconds, index_knowledge))
        start_background("health", periodic("health", settings.worker_health_refresh_interval_seconds, refresh_health))
        start_background("runtime_alerts", periodic("runtime_alerts", settings.runtime_alert_scan_interval_seconds, refresh_alerts))
        start_background("release_rollouts", periodic("release_rollouts", settings.release_rollout_scan_interval_seconds, refresh_rollouts))
        start_background("checkpoint_cleanup", periodic(
            "checkpoint_cleanup", settings.langgraph_checkpoint_cleanup_interval_seconds, clean_checkpoints,
        ))
        start_background("consumer", consume())
        start_background("model_probes", consume_probes())
        start_background("business_actions", consume_actions())
        log.info("worker_started", queue=settings.redis_queue_name, max_concurrency=settings.worker_max_concurrency,
            scheduler_policy=settings.worker_scheduler_policy)
        await stop.wait()
    finally:
        stop.set()
        draining = True
        # 先停止消费者，维护和心跳继续服务于在途任务。
        consumers = [task for task in background_tasks if task.get_name() == "consumer"]
        for task in consumers:
            task.cancel()
        await asyncio.gather(*consumers, return_exceptions=True)
        pending = set(execution_tasks.values())
        if pending:
            _, pending = await asyncio.wait(pending, timeout=settings.worker_shutdown_grace_seconds)
        if pending:
            log.warning("worker_shutdown_cancel_inflight", run_count=len(pending))
            for task in pending:
                task.cancel()
            _, pending = await asyncio.wait(pending, timeout=settings.worker_shutdown_cancel_seconds)
        maintenance_stop.set()
        for task in background_tasks:
            task.cancel()
        await asyncio.gather(*background_tasks, return_exceptions=True)
        try:
            async with asyncio.timeout(settings.worker_shutdown_cancel_seconds):
                async with session_factory() as session, session.begin():
                    lease = await session.scalar(select(RuntimeServiceLease).where(
                        RuntimeServiceLease.instance_id == instance_id,
                    ).with_for_update())
                    if lease is not None:
                        lease.service_status = "degraded" if pending else "stopped"
                        lease.expires_at = datetime.now(UTC)
                        lease.active_run_count = len(active_run_ids)
                        lease.metadata_payload = {
                            **lease.metadata_payload, "draining": True,
                            "shutdown_incomplete": bool(pending),
                        }
        except Exception:
            log.exception("worker_lease_shutdown_failed")
        await redis.aclose()
        await engine.dispose()
        for signal_name in signals:
            loop.remove_signal_handler(signal_name)
        log.info("worker_stopped", shutdown_incomplete=bool(pending))
        if pending:
            raise RuntimeError("Worker 在途任务未确认取消，需诊断后恢复，不允许直接移交")
    if service_error is not None:
        raise RuntimeError("Worker 监督任务失败，已停止领取并收敛在途任务") from service_error


def main() -> None:
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
