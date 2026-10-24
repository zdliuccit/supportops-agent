"""固定只读任务图的PG准入/采纳适配；无默认批准或Worker注册。"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import ClassVar, Protocol
from uuid import uuid5

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from supportops_core.config import Settings
from supportops_core.models import RunEvent
from supportops_core.run_attempt_services import RunAttemptIdentity
from supportops_core.services import append_event
from supportops_core.specialist_accounting_contracts import SpecialistAccountingRecord
from supportops_core.specialist_accounting_services import (
    SpecialistUsageReader, read_specialist_accounting, settle_specialist_model_call,
)
from supportops_core.specialist_admission_contracts import (
    SpecialistAdmissionBinding, SpecialistResultAcceptance, SpecialistResultObservation,
)
from supportops_core.specialist_budget_contracts import SpecialistBudgetReservation
from supportops_core.specialist_budget_services import SpecialistBudgetAuthority, reserve_specialist_call
from supportops_core.specialist_material_contracts import material_matches_task, material_scope
from supportops_core.specialist_dispatch_contracts import SpecialistDispatchConsumption
from supportops_core.specialist_dispatch_history import read_specialist_dispatch_history
from supportops_core.specialist_dispatch_services import (
    _current, _owner_digest, _reservation, _transaction, claim_specialist_dispatch,
)
from supportops_core.specialist_scheduler import SpecialistAdmissionDenied, _graph_issue
from supportops_core.specialist_scheduler_contracts import SpecialistDispatchClaim, SpecialistSchedule
from supportops_core.specialist_task_contracts import (
    SpecialistTaskInput, SpecialistTaskOutput, SpecialistUsage, contract_digest, validate_specialist_result,
)


class SpecialistResultReader(Protocol):
    """默认缺失；独立核对原物理派发响应与其确定转换，不能接受调用者正文摘要自证。

    须按原call/模型请求/一次消费及真实费用回执读取可信执行记录，确认原Provider响应的
    结构校验、来源映射和确定性输出转换。只有完整原结果可返回其准确摘要；无观察返回None。
    不调用模型/工具、取密/联网或读取父历史，不修改/提交事务，也不自行批准业务成功。
    """

    async def read_result(self, session: AsyncSession, *, reservation: SpecialistBudgetReservation,
        consumption: SpecialistDispatchConsumption, accounting: SpecialistAccountingRecord,
    ) -> SpecialistResultObservation | None: ...


class SpecialistAcceptanceUnconfirmed(RuntimeError):
    code = "SPECIALIST_RESULT_ACCEPTANCE_UNCONFIRMED"

    def __init__(self) -> None:
        super().__init__("原子任务采纳提交结果暂不可确认，请只读原call_id，勿重派或补造结果")


def _usage_matches(result: SpecialistTaskOutput, accounting: SpecialistAccountingRecord,
    consumption: SpecialistDispatchConsumption,
) -> bool:
    receipt = accounting.receipt
    usage = receipt.usage
    if accounting.status != "settled" or accounting.reservation_exceeded or accounting.error_code is not None or usage is None:
        return False
    expected = SpecialistUsage(model_calls=1, tool_calls=0,
        tokens=usage.input_tokens + usage.output_tokens,
        cost_microusd=accounting.settled_cost_microusd)
    outcome = "completed" if result.status in {"completed", "needs_input"} else result.status
    return (receipt.outcome == outcome and receipt.finished_at is not None
        and consumption.consumed_at <= receipt.started_at <= receipt.finished_at <= result.finished_at
        and result.finished_at < consumption.expires_at and result.usage == expected)


async def read_specialist_result_acceptance(session: AsyncSession, *, reservation: SpecialistBudgetReservation,
    consumption: SpecialistDispatchConsumption, accounting: SpecialistAccountingRecord,
) -> SpecialistResultAcceptance | None:
    """内部调用者先授权并提供原Run锁或一致快照；原采纳到期仍可查证，不恢复许可。"""
    rows = (await session.scalars(select(RunEvent).where(RunEvent.tenant_id == reservation.tenant_id,
        RunEvent.run_id == reservation.run_id, RunEvent.attempt_id == reservation.attempt_id,
        RunEvent.event_type == "specialist.result_accepted",
        RunEvent.data["call_id"].as_string() == str(reservation.call_id)).order_by(RunEvent.sequence).limit(2))).all()
    if not rows:
        return None
    try:
        if len(rows) != 1 or set(rows[0].data) != {"call_id", "acceptance", "acceptance_digest"}:
            raise ValueError()
        data = rows[0].data
        record = SpecialistResultAcceptance.model_validate_json(json.dumps(data["acceptance"],
            ensure_ascii=False, sort_keys=True, allow_nan=False))
        if (data["acceptance_digest"] != contract_digest(record) or record.call_id != reservation.call_id
            or record.tenant_id != reservation.tenant_id or record.run_id != reservation.run_id
            or record.attempt_id != reservation.attempt_id or record.generation != reservation.generation
            or record.task_input_digest != reservation.task_input_digest or record.schedule_digest != reservation.schedule_digest
            or record.reservation_digest != contract_digest(reservation)
            or record.dispatch_record_digest != contract_digest(consumption.record)
            or record.consumption_digest != contract_digest(consumption)
            or record.accounting_digest != contract_digest(accounting)
            or record.observation.accounting_receipt_digest != accounting.receipt_digest
            or not accounting.recorded_at <= record.accepted_at <= datetime.now(UTC)
            or record.observation.observed_at < accounting.receipt.observed_at
            or accounting.status != "settled" or accounting.reservation_exceeded or accounting.error_code is not None
            or accounting.receipt.finished_at is None
            or record.result_finished_at < accounting.receipt.finished_at
            or record.expires_at > min(reservation.expires_at, consumption.expires_at)):
            raise ValueError()
        return record
    except (KeyError, TypeError, ValueError):
        raise SpecialistAdmissionDenied("CONTRACT_REJECTED") from None


async def _observe(session: AsyncSession, *, reader: SpecialistResultReader,
    reservation: SpecialistBudgetReservation, consumption: SpecialistDispatchConsumption,
    accounting: SpecialistAccountingRecord, expires_at: datetime,
) -> SpecialistResultObservation:
    # 独立副本避免reader通过嵌套原价格字典影响后续校验；提供者不能替换事务。
    copies = (SpecialistBudgetReservation.model_validate_json(reservation.model_dump_json()),
        SpecialistDispatchConsumption.model_validate_json(consumption.model_dump_json()),
        SpecialistAccountingRecord.model_validate_json(accounting.model_dump_json()))
    digests = tuple(contract_digest(value) for value in copies)
    transaction = session.get_transaction()
    loop = asyncio.get_running_loop()
    cutoff = loop.time() + min(2, max(0, (min(consumption.expires_at, expires_at) - datetime.now(UTC)).total_seconds()))
    async with asyncio.timeout_at(cutoff):
        result = await reader.read_result(session, reservation=copies[0], consumption=copies[1], accounting=copies[2])
    if (loop.time() >= cutoff or transaction is None or session.get_transaction() is not transaction
        or not transaction.is_active or tuple(contract_digest(value) for value in copies) != digests
        or not isinstance(result, SpecialistResultObservation)):
        raise SpecialistAdmissionDenied("CONTRACT_REJECTED")
    result = SpecialistResultObservation.model_validate_json(result.model_dump_json())
    if (result.call_id != reservation.call_id or result.consumption_digest != contract_digest(consumption)
        or result.accounting_receipt_digest != accounting.receipt_digest
        or not accounting.receipt.observed_at <= result.observed_at <= datetime.now(UTC)):
        raise SpecialistAdmissionDenied("CONTRACT_REJECTED")
    return result


class PostgresSpecialistAdmission:
    """每实例绑定完整固定图/父尝试及明确call映射；不根据回调新增任务或重新分配call。"""

    _allowed_specialists: ClassVar[frozenset[str]] = frozenset({"knowledge", "diagnostic", "ticket_analysis"})

    def __init__(self, factory: async_sessionmaker[AsyncSession], *, attempt: RunAttemptIdentity,
        schedule: SpecialistSchedule, bindings: tuple[SpecialistAdmissionBinding, ...], settings: Settings,
        authority: SpecialistBudgetAuthority | None = None, usage_reader: SpecialistUsageReader | None = None,
        result_reader: SpecialistResultReader | None = None, redaction_secrets: tuple[str, ...] = (),
    ) -> None:
        schedule = SpecialistSchedule.model_validate_json(schedule.model_dump_json())
        bindings = tuple(SpecialistAdmissionBinding.model_validate_json(row.model_dump_json()) for row in bindings)
        indexed = {row.task_id: row for row in bindings}
        if (_graph_issue(schedule) is not None or len(indexed) != len(bindings)
            or len({row.call_id for row in bindings}) != len(bindings)
            or set(indexed) != {task.task_id for task in schedule.tasks}):
            raise SpecialistAdmissionDenied("CONTRACT_REJECTED")
        for task in schedule.tasks:
            binding = indexed[task.task_id]
            scope = material_scope(binding.scope)
            parent = task.parent
            if (task.specialist not in self._allowed_specialists or not material_matches_task(task, binding.scope)
                or contract_digest(task) != binding.task_input_digest
                or parent.tenant_id != attempt.tenant_id or parent.conversation_id != attempt.conversation_id
                or parent.run_id != attempt.run_id or parent.attempt_id != attempt.attempt_id
                or parent.attempt_generation != attempt.generation or scope.parent != parent
                or scope.child_version != task.child_version
                or any(row not in scope.resources for row in task.resources)
                or any(row not in scope.evidence for row in task.evidence)
                or task.deadline > min(schedule.deadline, scope.expires_at)):
                raise SpecialistAdmissionDenied("CONTRACT_REJECTED")
        self._factory, self._attempt, self._schedule = factory, attempt, schedule
        self._bindings, self._settings = indexed, settings
        self._authority, self._usage_reader, self._result_reader = authority, usage_reader, result_reader
        self._redaction_secrets = redaction_secrets
        self._schedule_digest = contract_digest(schedule)

    def _binding(self, task: SpecialistTaskInput) -> SpecialistAdmissionBinding:
        binding = self._bindings.get(task.task_id)
        if binding is None or binding.task_input_digest != contract_digest(task):
            raise SpecialistAdmissionDenied("CONTRACT_REJECTED")
        return binding

    def _dependencies(self) -> tuple[SpecialistBudgetAuthority, SpecialistUsageReader, SpecialistResultReader]:
        if self._authority is None or self._usage_reader is None or self._result_reader is None:
            raise SpecialistAdmissionDenied("DEPENDENCY_FAILED")
        return self._authority, self._usage_reader, self._result_reader

    async def claim(self, *, task: SpecialistTaskInput, schedule_digest: str) -> SpecialistDispatchClaim:
        authority, _, _ = self._dependencies()
        task = SpecialistTaskInput.model_validate_json(task.model_dump_json())
        binding = self._binding(task)
        if schedule_digest != self._schedule_digest:
            raise SpecialistAdmissionDenied("CONTRACT_REJECTED")
        if not self._schedule.created_at <= datetime.now(UTC) < min(self._schedule.deadline, task.deadline):
            raise SpecialistAdmissionDenied("DEADLINE_EXCEEDED")
        # 两个独立提交；中间失败保留原预留，不凭本地缓存重新领取或生成新call。
        await reserve_specialist_call(self._factory, attempt=self._attempt, call_id=binding.call_id,
            task=task, scope=binding.scope, schedule_digest=self._schedule_digest, settings=self._settings,
            authority=authority, redaction_secrets=self._redaction_secrets)
        record = await claim_specialist_dispatch(self._factory, attempt=self._attempt, call_id=binding.call_id,
            task=task, scope=binding.scope, settings=self._settings, authority=authority,
            redaction_secrets=self._redaction_secrets)
        return record.claim

    async def accept(self, *, task: SpecialistTaskInput, claim: SpecialistDispatchClaim,
        result: SpecialistTaskOutput,
    ) -> bool:
        authority, usage_reader, result_reader = self._dependencies()
        task = SpecialistTaskInput.model_validate_json(task.model_dump_json())
        claim = SpecialistDispatchClaim.model_validate_json(claim.model_dump_json())
        result = SpecialistTaskOutput.model_validate_json(result.model_dump_json())
        binding = self._binding(task)
        validate_specialist_result(task, result)
        if (claim.dispatch_id != uuid5(binding.call_id, "specialist-dispatch-v1")
            or claim.task_id != task.task_id or claim.task_input_digest != binding.task_input_digest):
            raise SpecialistAdmissionDenied("CONTRACT_REJECTED")
        # 独立费用事务先收敛原消费；撤权/取消不免除费用，后续采纳失败也不回滚记账。
        await settle_specialist_model_call(self._factory, tenant_id=self._attempt.tenant_id,
            run_id=self._attempt.run_id, call_id=binding.call_id, usage_reader=usage_reader)
        wrote = False
        try:
            async with _transaction(self._factory, self._attempt) as session:
                reservation, _ = await _reservation(session, attempt=self._attempt, call_id=binding.call_id)
                history = await read_specialist_dispatch_history(session, reservation=reservation)
                record, consumption = history.record, history.consumption
                view = await read_specialist_accounting(session, tenant_id=self._attempt.tenant_id,
                    run_id=self._attempt.run_id, call_id=binding.call_id)
                accounting = view.accounting if view else None
                if (record is None or record.claim != claim or consumption is None or accounting is None
                    or record.owner_digest != _owner_digest(self._attempt)
                    or reservation.schedule_digest != self._schedule_digest
                    or not _usage_matches(result, accounting, consumption)):
                    raise SpecialistAdmissionDenied("CONTRACT_REJECTED")
                _, current_approval = await _current(session, attempt=self._attempt, reservation=reservation, task=task,
                    scope=binding.scope, settings=self._settings, authority=authority,
                    redaction_secrets=self._redaction_secrets)
                previous = await read_specialist_result_acceptance(session, reservation=reservation,
                    consumption=consumption, accounting=accounting)
                if previous is not None and previous.result_digest != contract_digest(result):
                    raise SpecialistAdmissionDenied("CONTRACT_REJECTED")
                if previous is None:
                    observation = await _observe(session, reader=result_reader, reservation=reservation,
                        consumption=consumption, accounting=accounting,
                        expires_at=min(current_approval.expires_at, self._schedule.deadline))
                    if observation.result_digest != contract_digest(result) or result.finished_at > observation.observed_at:
                        raise SpecialistAdmissionDenied("CONTRACT_REJECTED")
                else:
                    observation = previous.observation
                # 最后一次等待之后重查当前授权/原知识和完整请求，不凭旧结果来源证明授权。
                prepared, approval = await _current(session, attempt=self._attempt, reservation=reservation,
                    task=task, scope=binding.scope, settings=self._settings, authority=authority,
                    redaction_secrets=self._redaction_secrets)
                expires = min(consumption.expires_at, current_approval.expires_at, approval.expires_at, self._schedule.deadline,
                    previous.expires_at if previous else consumption.expires_at)
                now = datetime.now(UTC)
                if (contract_digest(prepared) != record.prepared_request_digest or not result.finished_at <= now < expires):
                    raise SpecialistAdmissionDenied("DEADLINE_EXCEEDED")
                if previous is None:
                    accepted = SpecialistResultAcceptance(tenant_id=reservation.tenant_id, run_id=reservation.run_id,
                        attempt_id=reservation.attempt_id, generation=reservation.generation, call_id=reservation.call_id,
                        task_input_digest=reservation.task_input_digest, schedule_digest=self._schedule_digest,
                        reservation_digest=contract_digest(reservation), dispatch_record_digest=contract_digest(record),
                        consumption_digest=contract_digest(consumption), accounting_digest=contract_digest(accounting),
                        result_digest=contract_digest(result), result_finished_at=result.finished_at,
                        observation=observation, observation_digest=contract_digest(observation),
                        accepted_at=now, expires_at=expires)
                    wrote = True
                    await append_event(session, run_id=reservation.run_id, attempt_id=reservation.attempt_id,
                        event_type="specialist.result_accepted", data={"call_id": str(reservation.call_id),
                            "acceptance": accepted.model_dump(mode="json"), "acceptance_digest": contract_digest(accepted)})
                if datetime.now(UTC) >= expires:
                    raise SpecialistAdmissionDenied("DEADLINE_EXCEEDED")
            if datetime.now(UTC) >= expires:
                raise SpecialistAdmissionDenied("DEADLINE_EXCEEDED")
            return True
        except BaseException:
            if wrote:
                raise SpecialistAcceptanceUnconfirmed() from None
            raise


class PostgresSpecialistKnowledgeAdmission(PostgresSpecialistAdmission):
    """旧知识专用入口维持原允许类型；业务类型须显式构造通用适配器。"""

    _allowed_specialists = frozenset({"knowledge"})
