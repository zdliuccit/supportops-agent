"""原路由派发领取/一次消费历史；调用者先授权并提供锁或一致快照。"""

import json
from datetime import UTC, datetime
from uuid import uuid5

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from supportops_core.model_routing_dispatch_contracts import (
    RoutingDispatchConsumption, RoutingDispatchRecord, RoutingDispatchView,
)
from supportops_core.model_routing_reservation_contracts import RoutingReservationRecord
from supportops_core.models import RunEvent
from supportops_core.release_manifest_services import manifest_digest


def _digest(value: BaseModel) -> str:
    return manifest_digest(value.model_dump(mode="json"))


async def read_routing_dispatch_history(session: AsyncSession, *, reservation: RoutingReservationRecord,
) -> RoutingDispatchView:
    """不要求历史主体仍可运行；摘要不认证来源或数据库不可改性，不恢复执行许可。"""
    events = (await session.scalars(select(RunEvent).where(RunEvent.tenant_id == reservation.tenant_id,
        RunEvent.run_id == reservation.run_id, RunEvent.attempt_id == reservation.attempt_id,
        RunEvent.event_type.in_(("routing.dispatch_claimed", "routing.dispatch_consumed")),
        RunEvent.data["call_id"].as_string() == str(reservation.call_id)).order_by(RunEvent.sequence).limit(3))).all()
    record: RoutingDispatchRecord | None = None
    consumption: RoutingDispatchConsumption | None = None
    try:
        if len(events) > 2:
            raise ValueError()
        for event in events:
            data = event.data
            if event.event_type == "routing.dispatch_claimed":
                if record is not None or consumption is not None or set(data) != {"call_id", "record", "record_digest"}:
                    raise ValueError()
                record = RoutingDispatchRecord.model_validate_json(json.dumps(data["record"], allow_nan=False))
                if (data["call_id"] != str(reservation.call_id) or data["record_digest"] != _digest(record)
                    or record.reservation != reservation or record.dispatch_id != uuid5(reservation.call_id, "routing-dispatch-v1")
                    or record.claimed_at > datetime.now(UTC)):
                    raise ValueError()
            else:
                if record is None or consumption is not None or set(data) != {"call_id", "consumption", "consumption_digest"}:
                    raise ValueError()
                consumption = RoutingDispatchConsumption.model_validate_json(json.dumps(data["consumption"], allow_nan=False))
                if (data["call_id"] != str(reservation.call_id) or data["consumption_digest"] != _digest(consumption)
                    or consumption.record != record or consumption.consumed_at > datetime.now(UTC)):
                    raise ValueError()
        return RoutingDispatchView(record=record, consumption=consumption)
    except (KeyError, TypeError, ValueError):
        raise ValueError("ROUTING_DISPATCH_HISTORY_INVALID") from None
