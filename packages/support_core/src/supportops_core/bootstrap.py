"""首次启动时幂等创建本地企业和未分配部门的平台管理员。"""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from supportops_core.config import Settings
from supportops_core.enums import ROLE_EMPLOYEE, ROLE_PLATFORM_ADMIN
from supportops_core.models import Tenant, User, utc_now
from supportops_core.passwords import hash_password


async def bootstrap_enterprise_identity(
    session_factory: async_sessionmaker[AsyncSession], settings: Settings
) -> None:
    """仅在启用且用户表为空时创建初始身份，不覆盖任何现有账号。"""
    if not settings.bootstrap_admin_enabled:
        return
    async with session_factory() as session, session.begin():
        user_count = await session.scalar(select(func.count()).select_from(User))
        if user_count:
            return
        tenant = Tenant(
            name=settings.bootstrap_company_name,
            slug=settings.bootstrap_company_slug,
            contact_email=settings.bootstrap_admin_email.strip().lower(),
        )
        session.add(tenant)
        await session.flush()
        now = utc_now()
        session.add(
            User(
                tenant_id=tenant.id,
                organization_unit_id=None,
                email=settings.bootstrap_admin_email.strip().lower(),
                password_hash=hash_password(settings.bootstrap_admin_password),
                display_name=settings.bootstrap_admin_name,
                job_title="平台管理员",
                roles=[ROLE_PLATFORM_ADMIN, ROLE_EMPLOYEE],
                password_changed_at=now,
            )
        )
