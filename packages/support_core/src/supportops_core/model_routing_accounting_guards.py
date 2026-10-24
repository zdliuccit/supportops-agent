"""路由用量异常的共享预算阻断；不因结算路径或Run恢复绕过未知大额消费。"""

from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from supportops_core.models import ModelCallLedger
from supportops_core.quota_services import quota_day_start


async def routing_accounting_blocks_budget(session: AsyncSession, *, tenant_id: UUID,
    run_id: UUID, enforce_tenant_cost: bool,
    error_codes: tuple[str, str] = ("ROUTING_USAGE_BOUND_EXCEEDED", "ROUTING_USAGE_COST_OUT_OF_RANGE"),
) -> bool:
    # 普通超界消费已有精确费用，阻止原Run继续；不可表示的费用还须阻断受日费用限制的
    # 同租户其他Run。原创建UTC日归属不因迟到结算或补记变化。
    scope: ColumnElement[bool] = ModelCallLedger.run_id == run_id
    if enforce_tenant_cost:
        scope = or_(scope, and_(ModelCallLedger.created_at >= quota_day_start(),
            ModelCallLedger.error_code == error_codes[1]))
    return await session.scalar(select(ModelCallLedger.id).where(ModelCallLedger.tenant_id == tenant_id,
        scope, ModelCallLedger.error_code.in_(error_codes))
        .limit(1)) is not None
