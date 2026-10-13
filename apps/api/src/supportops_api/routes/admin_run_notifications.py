"""管理员按租户查询投递状态，并显式恢复有限投递周期。"""

from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from supportops_core.agent_services import require_platform_admin
from supportops_core.auth import IdentityContext
from supportops_core.run_outbox_services import (
    NotificationStatus,
    list_run_notifications,
    retry_run_notification,
)

from supportops_api.dependencies import current_identity, database_session
from supportops_api.pagination import PaginationParams, pagination_metadata, pagination_params
from supportops_api.schemas import RunNotificationListResponse, RunNotificationResponse, RunNotificationRetry

router = APIRouter(prefix="/v1/admin/run-notifications", tags=["admin-run-notifications"])


async def platform_admin_identity(identity: IdentityContext = Depends(current_identity)) -> IdentityContext:
    require_platform_admin(identity)
    return identity


@router.get("", response_model=RunNotificationListResponse)
async def read_notifications(
    status_filter: NotificationStatus | None = Query(default=None, alias="status"),
    pagination: PaginationParams = Depends(pagination_params),
    identity: IdentityContext = Depends(platform_admin_identity),
    session: AsyncSession = Depends(database_session),
) -> RunNotificationListResponse:
    items, total = await list_run_notifications(
        session, tenant_id=identity.principal.tenant_id, status=status_filter,
        page_size=pagination.page_size, offset=pagination.offset,
    )
    metadata = pagination_metadata(total, pagination)
    return RunNotificationListResponse(
        items=[RunNotificationResponse.model_validate(item) for item in items],
        total=metadata.total, page=metadata.page, page_size=metadata.page_size, pages=metadata.pages,
    )


@router.post("/{notification_id}/retry", response_model=RunNotificationResponse)
async def retry_notification(
    notification_id: UUID,
    payload: RunNotificationRetry,
    identity: IdentityContext = Depends(platform_admin_identity),
    session: AsyncSession = Depends(database_session),
) -> RunNotificationResponse:
    async with session.begin():
        notification = await retry_run_notification(
            session, tenant_id=identity.principal.tenant_id, notification_id=notification_id,
            expected_revision=payload.expected_revision, actor_user_id=identity.user.id,
        )
    return RunNotificationResponse.model_validate(notification)
