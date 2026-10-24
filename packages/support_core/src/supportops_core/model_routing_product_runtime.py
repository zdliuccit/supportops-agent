"""独立路由产品执行器；串联真实输入、协调、实际最终图及产品事务，无默认注册。"""

import asyncio
from collections.abc import Awaitable, Callable
from uuid import UUID

from langchain.agents.middleware import ModelRequest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from supportops_core.agent_config import SupportContext
from supportops_core.config import Settings
from supportops_core.model_routing_checkpoint import PostgresRoutingFinalCheckpoint, RoutingCheckpointStorageAuthority
from supportops_core.model_routing_coordinator import PostgresRoutingCoordinator
from supportops_core.model_routing_eligibility import RoutingDataBoundaryReader
from supportops_core.model_routing_failover_contracts import RoutingFailoverControls
from supportops_core.model_routing_product_contracts import RoutingProductCommitRecord
from supportops_core.model_routing_product_services import RoutingProductAuthority, RoutingProductUnconfirmed, commit_routing_support_result
from supportops_core.model_routing_reservations import RoutingReservationAuthority
from supportops_core.model_routing_result_services import RoutingResultDenied
from supportops_core.model_routing_runtime_input import RoutingModelInputGateway, RoutingRuntimeInput
from supportops_core.model_routing_run_request import build_owned_routing_request
from supportops_core.model_routing_run_lifecycle import RoutingOwnedRunResult, RoutingStopReason, stop_owned_routing_execution
from supportops_core.run_attempt_services import (
    RunAttemptIdentity, RunCancellationRequested, RunLeaseLostError, check_and_renew_attempt,
)

_PENDING_ROUTING_TASKS: set[asyncio.Task[object]] = set()


def retain_routing_cleanup_task(task: asyncio.Task[object]) -> None:
    """清理超预算时保留引用；只消费异常，不记录原异常/正文，也不宣称物理停止。"""
    _PENDING_ROUTING_TASKS.add(task)

    def finished(value: asyncio.Task[object]) -> None:
        _PENDING_ROUTING_TASKS.discard(value)
        if not value.cancelled():
            value.exception()

    task.add_done_callback(finished)


async def _cancel_tasks(tasks: tuple[asyncio.Task[object], ...]) -> None:
    if not tasks:
        return
    for task in tasks:
        if not task.done():
            task.cancel()
    try:
        done, pending = await asyncio.wait(tasks, timeout=1)
        for task in done:
            if not task.cancelled():
                task.exception()
        for task in pending:
            retain_routing_cleanup_task(task)
    except BaseException:
        for task in tasks:
            retain_routing_cleanup_task(task)
        raise


class PostgresRoutingProductExecutor:
    """由领取者显式注入全部独立批准及既有协调器；不替换原Worker或v3门禁。

    成功即已原子完成产品终结，领取者不得再调用旧Worker的正常终结分支。
    execute为低层组合调用；execute_supervised提供原领取者监督和停止事务。
    不自动重派、重提或恢复attempt，未确认回执不表示Run已终结。
    """

    def __init__(self, factory: async_sessionmaker[AsyncSession], *, settings: Settings,
        attempt: RunAttemptIdentity, controls: RoutingFailoverControls,
        coordinator: PostgresRoutingCoordinator | None = None,
        reservation_authority: RoutingReservationAuthority | None = None,
        boundary_reader: RoutingDataBoundaryReader | None = None,
        product_authority: RoutingProductAuthority | None = None,
        checkpoint_authority: RoutingCheckpointStorageAuthority | None = None,
        streaming: bool = True, redaction_secrets: tuple[str, ...] = (),
    ) -> None:
        if (coordinator is None or reservation_authority is None or boundary_reader is None
            or product_authority is None or checkpoint_authority is None):
            raise RoutingResultDenied("ROUTING_PRODUCT_RUNTIME_DEPENDENCIES_UNAVAILABLE")
        self._factory, self._settings, self._controls = factory, settings, controls
        self._attempt = attempt
        self._coordinator, self._reservation = coordinator, reservation_authority
        self._boundary, self._product, self._secrets = boundary_reader, product_authority, redaction_secrets
        self._gateway = RoutingModelInputGateway(factory, attempt=attempt, settings=settings, streaming=streaming)
        self._checkpoint = PostgresRoutingFinalCheckpoint(factory, settings=settings, authority=checkpoint_authority)
        self._used = False
        self._worker_entry_id: UUID | None = None

    def require_worker_binding(self, factory: async_sessionmaker[AsyncSession], *,
        settings: Settings, attempt: RunAttemptIdentity, worker_entry_id: UUID,
    ) -> None:
        """Worker调用前核对确切依赖实例和原领取身份；不返回批准或启动执行。"""
        if (self._factory is not factory or self._settings is not settings or self._attempt != attempt
            or self._used or self._worker_entry_id is not None or not isinstance(worker_entry_id, UUID)):
            raise RoutingResultDenied("ROUTING_WORKER_BINDING_INVALID")
        self._gateway.bind_worker_entry(worker_entry_id)
        self._worker_entry_id = worker_entry_id

    async def execute_supervised(self, request: ModelRequest[SupportContext]) -> RoutingOwnedRunResult:
        """显式领取者入口：续租/取消监督、一次执行和原停止事务，无Worker默认注册。

        用户/停机取消仍向调用方传播；停止回执不确认时只查原Run/入口，不重新执行。
        本方法不能嵌入仍在写同一thread的Agent图，具体存储批准仍须验证独占性。
        """
        return await self._supervise(lambda: self._execute(request))

    async def execute_persisted_supervised(self) -> RoutingOwnedRunResult:
        """原领取者从持久产品事实建立本轮新请求；不接受客户端正文、checkpoint或假SDK回执。"""
        async def operation() -> RoutingProductCommitRecord:
            request = await build_owned_routing_request(self._factory, attempt=self._attempt, settings=self._settings,
                worker_entry_id=self._worker_entry_id)
            return await self._execute(request)

        return await self._supervise(operation, reject_unstarted=True)

    async def _supervise(self, operation: Callable[[], Awaitable[RoutingProductCommitRecord]], *,
        reject_unstarted: bool = False,
    ) -> RoutingOwnedRunResult:
        if self._used:
            raise RoutingResultDenied("ROUTING_PRODUCT_RUNTIME_ALREADY_ENTERED")
        self._used = True
        tasks: tuple[asyncio.Task[object], ...] = ()
        reason: RoutingStopReason = "execution_failed"
        cancelled = False
        completed: RoutingOwnedRunResult | None = None

        async def check() -> None:
            await check_and_renew_attempt(self._factory, self._attempt,
                lease_seconds=self._settings.run_attempt_lease_seconds)

        async def monitor() -> None:
            while True:
                await asyncio.sleep(self._settings.run_attempt_heartbeat_seconds)
                await check()

        async def finish() -> RoutingOwnedRunResult:
            # 调用方重复取消不能打断清理后到停止事务之间的接线。
            await _cancel_tasks(tasks)
            if completed is not None:
                return completed
            return await stop_owned_routing_execution(self._factory,
                identity=self._attempt, reason=reason, reject_unstarted=reject_unstarted,
                worker_entry_id=self._worker_entry_id)

        try:
            async with asyncio.timeout(35):
                await check()
                execution = asyncio.create_task(operation(), name=f"routing:{self._attempt.attempt_id}")
                lease = asyncio.create_task(monitor(), name=f"routing-lease:{self._attempt.attempt_id}")
                tasks = (execution, lease)
                done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                # 产品已明确提交时优先返回原事实，不能被迟到的续租检查改成失败。
                if execution in done:
                    record = await execution
                    completed = RoutingOwnedRunResult("completed", root_call_id=record.root_call_id, product=record)
                else:
                    await lease
                    raise RunLeaseLostError("路由租约监督意外退出")
        except asyncio.CancelledError:
            reason, cancelled = "cancelled", True
        except RunCancellationRequested:
            reason = "cancelled"
        except RunLeaseLostError:
            reason = "lease_lost"
        except RoutingProductUnconfirmed:
            reason = "product_unconfirmed"
        except RoutingResultDenied as exc:
            reason = "checkpoint_unconfirmed" if exc.code.startswith("ROUTING_CHECKPOINT_") else "execution_failed"
        except Exception:
            reason = "execution_failed"
        finally:
            # 先同步创建/保留唯一收敛任务，再进入任何可被二次取消的等待。
            stopping = asyncio.create_task(finish(), name=f"routing-stop:{self._attempt.attempt_id}")
            retain_routing_cleanup_task(stopping)
        try:
            # 清理工作等待1秒，停止事务工作预算3秒；超时后仅保留原任务，不重建。
            async with asyncio.timeout(4.5):
                result = await asyncio.shield(stopping)
        except asyncio.CancelledError:
            # 若尚在清理，finish在开始停止事务时读取最新原因；已开始的事务不重派。
            reason = "cancelled"
            raise
        except Exception:
            result = completed if completed is not None else RoutingOwnedRunResult(
                "unconfirmed", "ROUTING_EXECUTION_STOP_UNCONFIRMED")
        if cancelled:
            raise asyncio.CancelledError()
        return result

    async def execute(self, request: ModelRequest[SupportContext]) -> RoutingProductCommitRecord:
        if self._used:
            raise RoutingResultDenied("ROUTING_PRODUCT_RUNTIME_ALREADY_ENTERED")
        self._used = True
        return await self._execute(request)

    async def _execute(self, request: ModelRequest[SupportContext]) -> RoutingProductCommitRecord:
        scope: RoutingRuntimeInput | None = None
        try:
            async with asyncio.timeout(30):
                scope = await self._gateway.open_for_product(request)
                controls = RoutingFailoverControls.model_validate_json(self._controls.model_dump_json())
                result = await self._coordinator.execute(scope, controls=controls)
                if result.status != "completed" or result.reason != "completed":
                    raise RoutingResultDenied("ROUTING_PRODUCT_RUNTIME_MODEL_BLOCKED")
                await self._checkpoint.capture(input=scope, coordination=result, controls=controls,
                    reservation_authority=self._reservation, boundary_reader=self._boundary,
                    redaction_secrets=self._secrets)
                return await commit_routing_support_result(self._factory, input=scope, coordination=result,
                    controls=controls, settings=self._settings, reservation_authority=self._reservation,
                    boundary_reader=self._boundary, product_authority=self._product,
                    checkpoint_reader=self._checkpoint, redaction_secrets=self._secrets)
        finally:
            self._checkpoint.close()
            if scope is not None:
                scope.close()
