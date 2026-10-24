"""有界只读子任务协调；无默认准入/执行器，不注册Agent或接入Worker。"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Literal, Protocol, get_args
from uuid import UUID

from supportops_core.specialist_scheduler_contracts import (
    SpecialistDispatchClaim, SpecialistSchedule, SpecialistScheduleIssue, SpecialistScheduleReport,
)
from supportops_core.specialist_task_contracts import (
    FailureCode, SpecialistTaskInput, SpecialistTaskOutput, SpecialistUsage,
    aggregate_specialist_results, contract_digest, validate_specialist_result,
)


def _now() -> datetime:
    return datetime.now(UTC)


class SpecialistAdmissionDenied(ValueError):
    def __init__(self, code: FailureCode = "AUTHORIZATION_CHANGED") -> None:
        super().__init__("专业子任务未获当前许可")
        self.code: FailureCode = code if code in get_args(FailureCode) else "CONTRACT_REJECTED"


class SpecialistAdmission(Protocol):
    """W26.3可信提供者：核对26.1/W19/W23、当前本人/资源/父子版本及父共享账本。

    claim须在独立事务中持久化原task/input/batch的一次领取和真实预算预留，提交成功才返回；
    不得凭输入中的范围声明或false字段自批，也不能接受模型/客户端上传准入记录。
    同task不重派，未知领取/超时/取消保留消费，不按零usage退额；期限覆盖所有调用边界。
    accept须补记真实用量（包括失败/未知），再次查询当前授权/版本/证据与原领取，只有
    仍可采纳的结果返回True；输出usage须与真实共享账本核对，不接受模型自报计量，未知不退额。
    执行器失败时也须由持久执行账本独立收敛消费，不能依赖本方法
    一定被调用。提供者还负责并发进程、崩溃恢复和迟到执行的fencing；本协调器不代替这些能力。
    """

    async def claim(self, *, task: SpecialistTaskInput, schedule_digest: str) -> SpecialistDispatchClaim: ...

    async def accept(self, *, task: SpecialistTaskInput, claim: SpecialistDispatchClaim,
        result: SpecialistTaskOutput) -> bool: ...


class SpecialistReadOnlyExecutor(Protocol):
    """只消费一份可信已领取的固定子任务，不得到父对话/秘密或新增委派、写工具权限。

    仅运行固定只读能力，真实模型/工具每次调用仍经共享网关复检授权与预算；禁止隐式重试，
    须协作取消并复检原领取期限/围栏。不得捕获取消后继续派发、扩大上下文或创建新子任务。
    回执仍是不可信观察，返回本身不确认业务成功。不将asyncio取消外推为OS强制终止保证。
    """

    async def execute(self, *, task: SpecialistTaskInput, claim: SpecialistDispatchClaim) -> SpecialistTaskOutput: ...


def _failure(task: SpecialistTaskInput, code: FailureCode) -> SpecialistTaskOutput:
    return SpecialistTaskOutput(task_id=task.task_id, input_digest=contract_digest(task), parent=task.parent,
        child_version=task.child_version, trace=task.trace, budget_scope_digest=contract_digest(task.budget),
        status="cancelled" if code == "CANCELLED" else "failed", failure_code=code,
        finished_at=max(_now(), task.created_at), usage=SpecialistUsage())


def _graph_issue(schedule: SpecialistSchedule) -> SpecialistScheduleIssue | None:
    tasks = schedule.tasks
    if len(tasks) > schedule.limits.max_tasks:
        return SpecialistScheduleIssue(code="TASK_LIMIT_EXCEEDED")
    try:
        # 复用原契约的父范围、共享预算、Trace及唯一ID核对，不把缺结果误当成功。
        aggregate_specialist_results(tasks, ())
    except ValueError:
        return SpecialistScheduleIssue(code="SCOPE_MISMATCH")
    indexed = {task.task_id: task for task in tasks}
    root_spans: set[str] = set()
    child_spans = {task.trace.child_span_id for task in tasks}
    for task in sorted(tasks, key=lambda item: str(item.task_id)):
        if task.created_at > schedule.created_at:
            return SpecialistScheduleIssue(code="SCOPE_MISMATCH", task_id=task.task_id)
        parent_id = task.trace.parent_task_id
        if parent_id is None:
            root_spans.add(task.trace.parent_span_id)
        elif parent_id not in indexed:
            return SpecialistScheduleIssue(code="PARENT_TASK_MISSING", task_id=task.task_id)
        else:
            parent = indexed[parent_id]
            if (task.trace.parent_span_id != parent.trace.child_span_id or task.deadline > parent.deadline
                or not {contract_digest(item) for item in task.resources} <= {contract_digest(item) for item in parent.resources}
                or not {contract_digest(item) for item in task.evidence} <= {contract_digest(item) for item in parent.evidence}):
                return SpecialistScheduleIssue(code="SCOPE_MISMATCH", task_id=task.task_id)
        path: set[UUID] = set()
        versions: set[UUID] = {task.parent.version.agent_version_id}
        current: SpecialistTaskInput | None = task
        while current is not None:
            if current.task_id in path:
                return SpecialistScheduleIssue(code="DELEGATION_CYCLE", task_id=task.task_id)
            path.add(current.task_id)
            if current.child_version.agent_version_id in versions:
                return SpecialistScheduleIssue(code="ANCESTOR_VERSION_REPEATED", task_id=task.task_id)
            versions.add(current.child_version.agent_version_id)
            ancestor_id = current.trace.parent_task_id
            if ancestor_id is not None and ancestor_id not in indexed:
                return SpecialistScheduleIssue(code="PARENT_TASK_MISSING", task_id=task.task_id)
            current = indexed[ancestor_id] if ancestor_id is not None else None
        if len(path) > schedule.limits.max_depth:
            return SpecialistScheduleIssue(code="DEPTH_LIMIT_EXCEEDED", task_id=task.task_id)
    if len(root_spans) != 1 or root_spans & child_spans:
        return SpecialistScheduleIssue(code="SCOPE_MISMATCH")
    return None


class SpecialistCoordinator:
    """每实例单批次，最多32任务/4层/8并行；未退出回调占用实例，不新派发补位。"""

    def __init__(self, *, admission: SpecialistAdmission | None = None,
        executor: SpecialistReadOnlyExecutor | None = None,
    ) -> None:
        self.admission, self.executor = admission, executor
        self._active = False
        self._unfinished: dict[asyncio.Task[SpecialistTaskOutput], UUID] = {}

    def _retain(self, task: asyncio.Task[SpecialistTaskOutput], task_id: UUID) -> None:
        self._unfinished[task] = task_id

        def finished(value: asyncio.Task[SpecialistTaskOutput]) -> None:
            self._unfinished.pop(value, None)
            if not value.cancelled():
                value.exception()  # 只消费异常，绝不采纳迟到事实或重派。

        task.add_done_callback(finished)

    async def _stop(self, running: dict[asyncio.Task[SpecialistTaskOutput], SpecialistTaskInput]) -> tuple[UUID, ...]:
        if not running:
            return ()
        for future in running:
            if not future.done():
                future.cancel()
        try:
            # 不用无限gather等待吞取消的回调，也不释放实例去启动替补。
            await asyncio.wait(tuple(running), timeout=0.1)
        finally:
            for future, task in running.items():
                if future.done():
                    if not future.cancelled():
                        future.exception()
                elif future not in self._unfinished:
                    self._retain(future, task.task_id)
        return tuple(sorted((task_id for future, task_id in self._unfinished.items() if not future.done()), key=str))

    async def _one(self, task: SpecialistTaskInput, *, schedule_digest: str, end: float,
        entered: set[UUID], dispatches: set[UUID],
    ) -> SpecialistTaskOutput:
        admission, executor = self.admission, self.executor
        if admission is None or executor is None:
            return _failure(task, "DEPENDENCY_FAILED")
        loop = asyncio.get_running_loop()
        cutoff = min(end, loop.time() + max(0, (task.deadline - _now()).total_seconds()))
        try:
            async with asyncio.timeout_at(cutoff) as window:
                since = _now()
                claim = await admission.claim(task=task, schedule_digest=schedule_digest)
                if not isinstance(claim, SpecialistDispatchClaim):
                    raise SpecialistAdmissionDenied("CONTRACT_REJECTED")
                claim = SpecialistDispatchClaim.model_validate_json(claim.model_dump_json())
                if loop.time() >= cutoff:
                    return _failure(task, "DEADLINE_EXCEEDED")
                if (claim.task_id != task.task_id or claim.task_input_digest != contract_digest(task)
                    or claim.authorization_scope_digest != task.parent.authorization_scope_digest
                    or claim.budget_scope_digest != contract_digest(task.budget)
                    or not since <= claim.claimed_at <= _now() < claim.expires_at
                    or claim.expires_at > min(task.deadline, task.parent.authorized_until, task.budget.deadline)
                    or claim.dispatch_id in dispatches):
                    raise SpecialistAdmissionDenied()
                cutoff = min(cutoff, loop.time() + max(0, (claim.expires_at - _now()).total_seconds()))
                window.reschedule(cutoff)
                if loop.time() >= cutoff:
                    return _failure(task, "DEADLINE_EXCEEDED")
                dispatches.add(claim.dispatch_id)
                entered.add(task.task_id)
                result = await executor.execute(task=task, claim=claim)
                if not isinstance(result, SpecialistTaskOutput):
                    raise SpecialistAdmissionDenied("CONTRACT_REJECTED")
                result = SpecialistTaskOutput.model_validate_json(result.model_dump_json())
                validate_specialist_result(task, result)
                if result.finished_at > _now():
                    raise SpecialistAdmissionDenied("CONTRACT_REJECTED")
                if await admission.accept(task=task, claim=claim, result=result) is not True:
                    raise SpecialistAdmissionDenied()
                if _now() >= min(task.deadline, claim.expires_at) or loop.time() >= cutoff:
                    return _failure(task, "DEADLINE_EXCEEDED")
                return result
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            return _failure(task, "DEADLINE_EXCEEDED")
        except SpecialistAdmissionDenied as exc:
            return _failure(task, exc.code)
        except ValueError:
            return _failure(task, "CONTRACT_REJECTED")
        except Exception:
            return _failure(task, "DEPENDENCY_FAILED")

    async def run(self, schedule: SpecialistSchedule) -> SpecialistScheduleReport:
        # JSON严格复核独立复制元数据；不改变原任务/预算/来源或它们的false许可字段。
        schedule = SpecialistSchedule.model_validate_json(schedule.model_dump_json())
        digest = contract_digest(schedule)

        def blocked(issue: SpecialistScheduleIssue) -> SpecialistScheduleReport:
            return SpecialistScheduleReport(batch_id=schedule.batch_id, schedule_digest=digest,
                status="blocked", issues=(issue,), observed_at=_now())

        if self._active or any(not future.done() for future in self._unfinished):
            return blocked(SpecialistScheduleIssue(code="COORDINATOR_BUSY"))
        issue = _graph_issue(schedule)
        if issue is not None:
            return blocked(issue)
        if not schedule.created_at <= _now() < schedule.deadline:
            return blocked(SpecialistScheduleIssue(code="DEADLINE_EXCEEDED"))
        if self.admission is None or self.executor is None:
            return blocked(SpecialistScheduleIssue(code="DEPENDENCIES_UNAVAILABLE"))
        self._active = True
        loop = asyncio.get_running_loop()
        end = loop.time() + min(schedule.limits.timeout_seconds, (schedule.deadline - _now()).total_seconds())
        pending = {task.task_id: task for task in schedule.tasks}
        running: dict[asyncio.Task[SpecialistTaskOutput], SpecialistTaskInput] = {}
        results: dict[UUID, SpecialistTaskOutput] = {}
        entered: set[UUID] = set()
        dispatches: set[UUID] = set()
        issues: list[SpecialistScheduleIssue] = []
        unfinished: tuple[UUID, ...] = ()
        try:
            while pending or running:
                if loop.time() >= end or _now() >= schedule.deadline:
                    issues.append(SpecialistScheduleIssue(code="DEADLINE_EXCEEDED"))
                    unfinished = await self._stop(running)
                    for task in (*pending.values(), *running.values()):
                        results[task.task_id] = _failure(task, "DEADLINE_EXCEEDED")
                    pending.clear(); running.clear()
                    break
                for task in sorted(tuple(pending.values()), key=lambda item: str(item.task_id)):
                    parent = results.get(task.trace.parent_task_id) if task.trace.parent_task_id else None
                    if _now() >= task.deadline:
                        results[task.task_id] = _failure(task, "DEADLINE_EXCEEDED")
                    elif parent is not None and parent.status != "completed":
                        results[task.task_id] = _failure(task, "CANCELLED" if parent.status == "cancelled" else "DEPENDENCY_FAILED")
                    elif task.trace.parent_task_id is not None and parent is None:
                        continue
                    elif len(running) < schedule.limits.max_parallel:
                        future = asyncio.create_task(self._one(task, schedule_digest=digest, end=end,
                            entered=entered, dispatches=dispatches))
                        running[future] = task
                    else:
                        continue
                    pending.pop(task.task_id)
                if not running:
                    if pending:
                        # 前轮失败可能刚使后续任务可收敛，下一轮只推进固定图。
                        continue
                    break
                done, _ = await asyncio.wait(tuple(running), timeout=max(0, end - loop.time()),
                    return_when=asyncio.FIRST_COMPLETED)
                for future in sorted(done, key=lambda item: str(running[item].task_id)):
                    task = running.pop(future)
                    if future.cancelled():
                        result = _failure(task, "CANCELLED")
                    else:
                        result = future.result()
                    if loop.time() >= end or _now() >= min(schedule.deadline, task.deadline):
                        result = _failure(task, "DEADLINE_EXCEEDED")
                    results[task.task_id] = result
            if unfinished:
                issues.append(SpecialistScheduleIssue(code="CALLBACK_UNCONFIRMED"))
            ordered = tuple(results[key] for key in sorted(results, key=str))
            aggregate = aggregate_specialist_results(schedule.tasks, ordered)
            next_action: Literal["parent_review", "request_input", "human_attention"] = "human_attention"
            if not unfinished and aggregate.status == "ready_for_parent_review":
                next_action = "parent_review"
            elif not unfinished and aggregate.status == "needs_input":
                next_action = "request_input"
            return SpecialistScheduleReport(batch_id=schedule.batch_id, schedule_digest=digest,
                status="unconfirmed" if unfinished else "collected", next_action=next_action, issues=tuple(issues), results=ordered,
                aggregate=aggregate,
                executor_entered_task_ids=tuple(sorted(entered, key=str)), unfinished_task_ids=unfinished, observed_at=_now())
        finally:
            try:
                if running:
                    await self._stop(running)
            finally:
                self._active = False
