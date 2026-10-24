"""一次入口历史的封闭读取，供派发围栏和费用结算共用；不要求历史主体仍可执行。"""

import json
from datetime import UTC, datetime
from uuid import uuid5

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from supportops_core.models import RunEvent
from supportops_core.specialist_budget_contracts import SpecialistBudgetReservation
from supportops_core.specialist_dispatch_contracts import (
    SpecialistDispatchConsumption, SpecialistDispatchRecord, SpecialistDispatchView,
)
from supportops_core.specialist_task_contracts import contract_digest


class SpecialistDispatchHistoryInvalid(ValueError):
    code = "SPECIALIST_DISPATCH_HISTORY_INVALID"

    def __init__(self) -> None:
        super().__init__("子任务原领取/一次消费历史暂不可确认")


async def read_specialist_dispatch_history(session: AsyncSession, *, reservation: SpecialistBudgetReservation,
) -> SpecialistDispatchView:
    """内部调用者先完成读取授权并提供原Run锁或一致只读快照；不恢复执行许可。"""
    events = (await session.scalars(select(RunEvent).where(RunEvent.tenant_id == reservation.tenant_id,
        RunEvent.run_id == reservation.run_id, RunEvent.attempt_id == reservation.attempt_id,
        RunEvent.event_type.in_(("specialist.dispatch_claimed", "specialist.dispatch_consumed")),
        RunEvent.data["call_id"].as_string() == str(reservation.call_id)).order_by(RunEvent.sequence).limit(3))).all()
    record: SpecialistDispatchRecord | None = None
    consumption: SpecialistDispatchConsumption | None = None
    try:
        if len(events) > 2:
            raise ValueError()
        for event in events:
            data = event.data
            if event.event_type == "specialist.dispatch_claimed":
                if record is not None or consumption is not None or set(data) != {"call_id", "record", "record_digest"}:
                    raise ValueError()
                record = SpecialistDispatchRecord.model_validate_json(json.dumps(data["record"],
                    ensure_ascii=False, sort_keys=True, allow_nan=False))
                claim = record.claim
                if (data["record_digest"] != contract_digest(record) or record.call_id != reservation.call_id
                    or record.tenant_id != reservation.tenant_id or record.conversation_id != reservation.conversation_id
                    or record.run_id != reservation.run_id or record.attempt_id != reservation.attempt_id
                    or record.generation != reservation.generation or record.model_input_digest != reservation.model_input_digest
                    or claim.dispatch_id != uuid5(reservation.call_id, "specialist-dispatch-v1")
                    or claim.task_id != reservation.task_id or claim.task_input_digest != reservation.task_input_digest
                    or claim.authorization_scope_digest != reservation.authorization_scope_digest
                    or claim.budget_scope_digest != contract_digest(reservation.budget)
                    or claim.budget_reservation_digest != contract_digest(reservation)
                    or claim.governance_decision_digest != reservation.governance_decision_digest
                    or not reservation.recorded_at <= claim.claimed_at <= datetime.now(UTC)
                    or claim.expires_at > reservation.expires_at):
                    raise ValueError()
            elif event.event_type == "specialist.dispatch_consumed":
                if (record is None or consumption is not None
                    or set(data) != {"call_id", "consumption", "consumption_digest"}):
                    raise ValueError()
                consumption = SpecialistDispatchConsumption.model_validate_json(json.dumps(data["consumption"],
                    ensure_ascii=False, sort_keys=True, allow_nan=False))
                if (data["consumption_digest"] != contract_digest(consumption)
                    or consumption.record != record or consumption.consumed_at > datetime.now(UTC)):
                    raise ValueError()
        return SpecialistDispatchView(record=record, consumption=consumption)
    except (KeyError, TypeError, ValueError):
        raise SpecialistDispatchHistoryInvalid() from None
