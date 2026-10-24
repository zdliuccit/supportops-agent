"""显式注入的Worker路由分支；无默认工厂、环境导入或固定模型回退。"""

import asyncio
from datetime import UTC, datetime
from typing import Protocol
from uuid import uuid4, uuid5

from sqlalchemy import and_, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from supportops_core.config import Settings
from supportops_core.enums import MessageRole
from supportops_core.model_routing_product_runtime import PostgresRoutingProductExecutor, retain_routing_cleanup_task
from supportops_core.model_routing_request_contracts import RoutingWorkerStart
from supportops_core.model_routing_request_services import (
    read_routing_worker_start, routing_business_activity_exists, routing_outcome_event_exists,
)
from supportops_core.model_routing_result_services import RoutingResultDenied
from supportops_core.model_routing_run_lifecycle import RoutingOwnedRunResult, RoutingStopReason, stop_owned_routing_execution
from supportops_core.models import AgentVersion, Message, ModelCallLedger, RunEvent
from supportops_core.run_attempt_services import (
    RunAttemptIdentity, RunCancellationRequested, RunLeaseLostError, owned_run,
)
from supportops_core.release_manifest_services import manifest_digest
from supportops_core.services import append_event


class RoutingWorkerExecutorFactory(Protocol):
    """获批集成仅同步构造本轮内存依赖，不读库/取密/联网或先执行任何方法。

    所有独立治理/预算/数据/产品/存储批准及凭据/出站依赖必须显式配置；工厂、
    依赖实例或入口登记都不是批准记录。禁止复用其他领取者或已执行的实例。
    """

    def build(self, factory: async_sessionmaker[AsyncSession], *, settings: Settings,
        attempt: RunAttemptIdentity, start: RoutingWorkerStart,
    ) -> PostgresRoutingProductExecutor: ...


async def execute_claimed_routing_run(factory: async_sessionmaker[AsyncSession], *,
    settings: Settings, attempt: RunAttemptIdentity,
    executor_factory: RoutingWorkerExecutorFactory | None = None,
) -> RoutingOwnedRunResult | None:
    """None仅表示精确读取后确认不是v3；v3任何结果均不能回到旧Worker终结分支。"""
    selected = False
    entry_confirmed = False
    entered_runtime = False
    entry_id = uuid4()
    reason: RoutingStopReason = "worker_binding_invalid"
    stopping: asyncio.Task[RoutingOwnedRunResult] | None = None

    async def wait_for_stop(stop_reason: RoutingStopReason) -> RoutingOwnedRunResult:
        nonlocal stopping
        if stopping is None:
            # 原入口明确提交后才调用；同步创建/保留一次，重复取消不能重派停止事务。
            stopping = asyncio.create_task(stop_owned_routing_execution(factory, identity=attempt,
                reason=stop_reason, reject_unstarted=True, worker_entry_id=entry_id),
                name=f"routing-worker-stop:{attempt.attempt_id}")
            retain_routing_cleanup_task(stopping)
        try:
            async with asyncio.timeout(3.5):
                return await asyncio.shield(stopping)
        except Exception:
            # 超时/异常只查原入口；原任务继续受停止事务本身的预算和租约围栏约束。
            return RoutingOwnedRunResult("unconfirmed", "ROUTING_EXECUTION_STOP_UNCONFIRMED")

    try:
        async with asyncio.timeout(5):
            async with factory() as session, session.begin():
                if session.get_bind().dialect.name == "postgresql":
                    await session.execute(text("SET LOCAL lock_timeout = '250ms'"))
                    await session.execute(text("SET LOCAL statement_timeout = '2s'"))
                owned = await owned_run(session, attempt, allow_cancelling=True)
                schema = await session.scalar(select(AgentVersion.schema_version).where(
                    AgentVersion.tenant_id == attempt.tenant_id, AgentVersion.agent_id == owned.run.agent_id,
                    AgentVersion.id == owned.run.agent_version_id))
                if schema != "3":
                    return None
                selected = True
                if session.get_bind().dialect.name != "postgresql":
                    raise RoutingResultDenied("ROUTING_WORKER_POSTGRES_REQUIRED")
                # 无工厂也先核对/登记本次入口，不能停止同身份的先前执行者。
                prior = await session.scalar(select(RunEvent.id).where(RunEvent.tenant_id == attempt.tenant_id,
                    RunEvent.run_id == attempt.run_id, or_(RunEvent.event_type.in_((
                        "routing.worker_started", "routing.execution_started")),
                        and_(RunEvent.event_type == "stage.started", RunEvent.data["stage"].as_string() == "agent.execute")))
                    .limit(1))
                ledger = await session.scalar(select(ModelCallLedger.id).where(ModelCallLedger.tenant_id == attempt.tenant_id,
                    ModelCallLedger.run_id == attempt.run_id).limit(1))
                business_activity = await routing_business_activity_exists(session,
                    tenant_id=attempt.tenant_id, run_id=attempt.run_id)
                product = await session.scalar(select(Message.id).where(Message.tenant_id == attempt.tenant_id,
                    Message.conversation_id == attempt.conversation_id, Message.turn_sequence == owned.run.input_sequence,
                    Message.role == MessageRole.ASSISTANT).limit(1))
                if (prior is not None or ledger is not None or business_activity or product is not None
                    or await routing_outcome_event_exists(session, tenant_id=attempt.tenant_id, run_id=attempt.run_id)
                    or owned.attempt.side_effect_state != "none" or owned.attempt.checkpoint_id is not None):
                    return RoutingOwnedRunResult("unconfirmed", "ROUTING_WORKER_ALREADY_ENTERED")
                start = RoutingWorkerStart(operation_id=uuid5(attempt.run_id, "routing-worker-start-v1"), entry_id=entry_id,
                    tenant_id=attempt.tenant_id, conversation_id=attempt.conversation_id, run_id=attempt.run_id,
                    attempt_id=attempt.attempt_id, generation=attempt.generation, agent_version_id=owned.run.agent_version_id,
                    config_digest=owned.run.config_digest, created_at=datetime.now(UTC))
                owned.attempt.replay_policy = "manual"
                await append_event(session, run_id=attempt.run_id, attempt_id=attempt.attempt_id,
                    event_type="routing.worker_started", data={"record": start.model_dump(mode="json"),
                        "record_digest": manifest_digest(start.model_dump(mode="json"))})
                await append_event(session, run_id=attempt.run_id, attempt_id=attempt.attempt_id,
                    event_type="stage.started", data={"stage": "agent.execute",
                        "attempt_id": str(attempt.attempt_id), "generation": attempt.generation})
                # 原记录和紧邻阶段都符合后续读取契约，才允许提交本次入口。
                if await read_routing_worker_start(session, attempt=attempt) != start:
                    raise RoutingResultDenied("ROUTING_WORKER_START_HISTORY_INVALID")
        entry_confirmed = True
        if executor_factory is None:
            return await wait_for_stop("worker_unavailable")
        # 入口事务已明确提交后才构造；工厂不接受Prompt/秘密或客户端目标。
        executor = executor_factory.build(factory, settings=settings, attempt=attempt, start=start)
        if type(executor) is not PostgresRoutingProductExecutor:
            raise RoutingResultDenied("ROUTING_WORKER_BINDING_INVALID")
        executor.require_worker_binding(factory, settings=settings, attempt=attempt, worker_entry_id=entry_id)
        entered_runtime = True
        result = await executor.execute_persisted_supervised()
        if result.status == "completed":
            # 完整原产品事实独立查证；已完成分支只读，不凭内存回执返回业务成功。
            confirmed = await stop_owned_routing_execution(factory, identity=attempt, reason="product_unconfirmed",
                worker_entry_id=entry_id)
            if (confirmed.status != "completed" or result.root_call_id != confirmed.root_call_id
                or result.product is None or result.product != confirmed.product):
                return RoutingOwnedRunResult("unconfirmed", "ROUTING_WORKER_RECEIPT_UNCONFIRMED", result.root_call_id)
            return confirmed
        return result
    except asyncio.CancelledError:
        if entry_confirmed and not entered_runtime:
            try:
                await wait_for_stop("cancelled")
            except asyncio.CancelledError:
                pass  # 继续传播调用方取消，保留原停止任务及首次原因。
        raise
    except RunCancellationRequested:
        if not selected:
            raise
        reason = "cancelled"
    except RunLeaseLostError:
        if not selected:
            raise
        return RoutingOwnedRunResult("fenced", "ROUTING_EXECUTION_FENCED")
    except Exception:
        if not selected:
            raise  # 未知版本/基础设施错误不能被解释为可走固定模型。
    if not entry_confirmed:
        return RoutingOwnedRunResult("unconfirmed", "ROUTING_WORKER_ENTRY_UNCONFIRMED")
    if entered_runtime:
        # 执行器已接管唯一收敛任务；回执查证异常不能再创建第二个停止事务。
        return RoutingOwnedRunResult("unconfirmed", "ROUTING_WORKER_RECEIPT_UNCONFIRMED")
    return await wait_for_stop(reason)
