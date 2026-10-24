"""子任务用量越界停止同Run；不可表示的费用同时封锁受日费用限制的租户预算。"""

import json
from uuid import UUID

from sqlalchemy import BigInteger, and_, case, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from supportops_core.model_routing_accounting_guards import routing_accounting_blocks_budget
from supportops_core.models import ModelCallLedger, RunEvent
from supportops_core.release_manifest_services import manifest_digest
from supportops_core.specialist_budget_contracts import SPECIALIST_BUDGET_POLICY, SpecialistBudgetReservation
from supportops_core.specialist_task_contracts import contract_digest


async def specialist_accounting_blocks_budget(session: AsyncSession, *, tenant_id: UUID,
    run_id: UUID, enforce_tenant_cost: bool,
) -> bool:
    return await routing_accounting_blocks_budget(session, tenant_id=tenant_id, run_id=run_id,
        enforce_tenant_cost=enforce_tenant_cost,
        error_codes=("SPECIALIST_USAGE_BOUND_EXCEEDED", "SPECIALIST_USAGE_COST_OUT_OF_RANGE"))


def consumed_model_tokens() -> ColumnElement[int]:
    """完整已结算用实际量，其余保留原输入/输出预留；整数运算先提升BIGINT。"""
    return case((and_(ModelCallLedger.status == "settled", ModelCallLedger.input_tokens.is_not(None),
        ModelCallLedger.output_tokens.is_not(None)), cast(ModelCallLedger.input_tokens, BigInteger)
        + cast(ModelCallLedger.output_tokens, BigInteger)), else_=cast(ModelCallLedger.estimated_input_tokens, BigInteger)
        + cast(ModelCallLedger.reserved_output_tokens, BigInteger))


async def specialist_shared_tokens_allow(session: AsyncSession, *, tenant_id: UUID, run_id: UUID,
    input_tokens: int, output_tokens: int,
) -> bool:
    """所有父/路由/子调用都保留原子任务共享token硬上界，不因恢复或设置增加而清零。

    调用者须已持有原Run锁。没有子任务记录时沿用原路径；原记录缺失/损坏则拒绝。
    """
    ids = (await session.scalars(select(ModelCallLedger.id).where(ModelCallLedger.tenant_id == tenant_id,
        ModelCallLedger.run_id == run_id, ModelCallLedger.policy_version == SPECIALIST_BUDGET_POLICY).limit(33))).all()
    if not ids:
        return True
    if len(ids) > 32:
        return False
    events = (await session.scalars(select(RunEvent).where(RunEvent.tenant_id == tenant_id,
        RunEvent.run_id == run_id, RunEvent.event_type == "budget.model_reserved",
        RunEvent.data["call_id"].as_string().in_([str(item) for item in ids])).limit(33))).all()
    limits: list[int] = []
    seen: set[UUID] = set()
    try:
        if len(events) != len(ids):
            return False
        for event in events:
            record = SpecialistBudgetReservation.model_validate_json(json.dumps(event.data["specialist_reservation"],
                ensure_ascii=False, sort_keys=True, allow_nan=False))
            if (record.call_id not in ids or record.call_id in seen or record.tenant_id != tenant_id
                or record.run_id != run_id or record.attempt_id != event.attempt_id
                or event.data.get("specialist_reservation_digest") != contract_digest(record)
                or manifest_digest(record.price_snapshot.source_pricing) != record.price_snapshot.source_pricing_digest):
                return False
            seen.add(record.call_id)
            limits.append(record.budget.tokens_limit)
    except (KeyError, TypeError, ValueError):
        return False
    used = await session.scalar(select(func.coalesce(func.sum(consumed_model_tokens()), 0)).where(
        ModelCallLedger.tenant_id == tenant_id, ModelCallLedger.run_id == run_id))
    return int(used or 0) + input_tokens + output_tokens <= min(limits)
