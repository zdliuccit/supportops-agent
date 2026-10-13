"""租户每日额度与有效执行并发计数；以数据库事实为准。"""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from supportops_core.enums import RunStatus
from supportops_core.models import AgentRun, RunAttempt, Tenant


class TenantQuotaExceeded(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__("当前租户执行额度已用尽，请稍后重试")
        self.code = code


def quota_day_start() -> datetime:
    """每日额度以 UTC 自然日计数。"""
    return datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)


async def check_new_run_quota(session: AsyncSession, *, tenant_id: UUID, limit: int) -> None:
    # 调用方已锁会话；租户行锁使不同会话同时申请最后一个额度仍保持原子。
    await session.scalar(select(Tenant).where(Tenant.id == tenant_id).with_for_update(key_share=True))
    count = int(await session.scalar(select(func.count()).select_from(AgentRun).where(
        AgentRun.tenant_id == tenant_id, AgentRun.created_at >= quota_day_start(),
    )) or 0)
    if count >= limit:
        raise TenantQuotaExceeded("TENANT_RUN_DAILY_QUOTA_EXCEEDED")


async def active_tenant_attempt_count(session: AsyncSession, *, tenant_id: UUID) -> int:
    return int(await session.scalar(select(func.count()).select_from(RunAttempt)
        .join(AgentRun, AgentRun.current_attempt_id == RunAttempt.id)
        .where(RunAttempt.tenant_id == tenant_id, RunAttempt.status == "running",
               RunAttempt.lease_expires_at > datetime.now(UTC),
               AgentRun.status.in_((RunStatus.RUNNING, RunStatus.CANCELLING)))) or 0)
