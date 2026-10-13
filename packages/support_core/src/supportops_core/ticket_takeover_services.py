"""隔离客服显式接管；公共问题锁守护 Agent 出站与人工命令。"""

from uuid import UUID
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from supportops_core.models import MockSupportCase, utc_now
from supportops_core.action_ledger_services import ActionStateConflict


async def ensure_mock_case(session: AsyncSession, *, tenant_id: UUID, project_id: UUID, owner_user_id: UUID,
    issue_key: str, conversation_id: UUID | None,
) -> MockSupportCase:
    query = select(MockSupportCase).where(MockSupportCase.tenant_id == tenant_id, MockSupportCase.project_id == project_id,
        MockSupportCase.issue_key == issue_key).execution_options(populate_existing=True).with_for_update()
    row = await session.scalar(query)
    if row is None:
        try:
            async with session.begin_nested():
                row = MockSupportCase(tenant_id=tenant_id, project_id=project_id, owner_user_id=owner_user_id,
                    issue_key=issue_key, conversation_id=conversation_id, is_active=False, version=1, updated_at=utc_now())
                session.add(row)
                await session.flush()
        except IntegrityError:
            row = await session.scalar(query)
            if row is None:
                raise
    if row.owner_user_id != owner_user_id:
        raise ActionStateConflict("Mock 问题主体与资源不一致")
    return row


async def assert_mock_write_allowed(session: AsyncSession, *, tenant_id: UUID, project_id: UUID, owner_user_id: UUID,
    issue_key: str, conversation_id: UUID | None,
) -> None:
    row = await ensure_mock_case(session, tenant_id=tenant_id, project_id=project_id, owner_user_id=owner_user_id,
        issue_key=issue_key, conversation_id=conversation_id)
    blocker = await session.scalar(select(MockSupportCase.id).where(MockSupportCase.tenant_id == tenant_id,
        MockSupportCase.project_id == project_id, MockSupportCase.owner_user_id == owner_user_id,
        MockSupportCase.is_active.is_(True), or_(MockSupportCase.issue_key == issue_key,
            MockSupportCase.conversation_id == conversation_id if conversation_id is not None else False)).limit(1))
    if row.is_active or blocker is not None:
        raise ActionStateConflict("该问题或关联会话已由客服接管，Agent 写动作被冻结；只读查询仍可继续")


async def conversation_takeover_count(session: AsyncSession, *, tenant_id: UUID, owner_user_id: UUID, conversation_id: UUID) -> int:
    """仅当前主体会话的接管聚合；无列表分页语义，不读取工单或排查正文。"""
    from supportops_core.approval_services import mock_project_id
    return int(await session.scalar(select(func.count()).select_from(MockSupportCase).where(MockSupportCase.tenant_id == tenant_id,
        MockSupportCase.owner_user_id == owner_user_id, MockSupportCase.project_id == mock_project_id(tenant_id, owner_user_id),
        MockSupportCase.conversation_id == conversation_id, MockSupportCase.is_active.is_(True))) or 0)
