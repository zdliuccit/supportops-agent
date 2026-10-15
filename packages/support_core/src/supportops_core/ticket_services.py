"""隔离 Mock 工单查询；资源授权在读取正文之前进行。"""

from datetime import datetime
from typing import Literal
from pydantic import Field
from uuid import UUID
from sqlalchemy import exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from supportops_core.agent_config import StrictModel
from supportops_core.approval_services import STAFF_ROLES, mock_project_id, require_mock_staff
from supportops_core.auth import IdentityContext
from supportops_core.config import Settings
from supportops_core.models import MockResourceGrant, MockSupportTicket, utc_now


class MockTicketView(StrictModel):
    schema_version: Literal["1"] = "1"
    data_environment: Literal["mock"] = "mock"
    connector_version: Literal["mock_ticket_read_v2"] = "mock_ticket_read_v2"
    data_trust: Literal["untrusted_ticket_data"] = "untrusted_ticket_data"
    status: Literal["available", "not_found", "unavailable"]
    observed_at: datetime
    external_id: str | None = Field(default=None, max_length=200)
    ticket_status: Literal["open", "in_progress", "escalated", "closed"] | None = None
    title: str | None = Field(default=None, max_length=200)
    summary: str | None = Field(default=None, max_length=2000)
    version: int | None = Field(default=None, ge=1, strict=True)
    error_code: str | None = Field(default=None, max_length=100, pattern=r"^[A-Z0-9_]+$")


def ticket_visibility(identity: IdentityContext):
    own = MockSupportTicket.owner_user_id == identity.user.id
    staff_grant = exists(select(MockResourceGrant.id).where(MockResourceGrant.tenant_id == MockSupportTicket.tenant_id,
        MockResourceGrant.project_id == MockSupportTicket.project_id, MockResourceGrant.owner_user_id == MockSupportTicket.owner_user_id,
        MockResourceGrant.user_id == identity.user.id, MockResourceGrant.permission == "ticket_handle", MockResourceGrant.is_enabled.is_(True)))
    return (MockSupportTicket.tenant_id == identity.principal.tenant_id) & (own | (staff_grant if STAFF_ROLES.intersection(identity.principal.roles) else False))


async def query_mock_support_ticket(session: AsyncSession, *, identity: IdentityContext, settings: Settings, ticket_id: str) -> MockTicketView:
    now = utc_now()
    if not settings.business_action_mock_enabled or settings.environment == "production":
        return MockTicketView(status="unavailable", observed_at=now, error_code="TICKET_CONNECTOR_UNAVAILABLE")
    missing = MockTicketView(status="not_found", observed_at=now)
    if not ticket_id.startswith("MOCK-") or len(ticket_id) > 200:
        return missing
    candidate = (await session.execute(select(MockSupportTicket.id, MockSupportTicket.owner_user_id, MockSupportTicket.project_id).where(
        MockSupportTicket.external_id == ticket_id, ticket_visibility(identity)))).one_or_none()
    if candidate is None or candidate.project_id != mock_project_id(identity.principal.tenant_id, candidate.owner_user_id):
        return missing
    if candidate.owner_user_id != identity.user.id:
        await require_mock_staff(session, tenant_id=identity.principal.tenant_id, owner_user_id=candidate.owner_user_id,
            user_id=identity.user.id, permission="ticket_handle")
    # 复查授权后读取最小正文；不返回 actor/project/logical_key 或其他工单。
    ticket = await session.scalar(select(MockSupportTicket).where(MockSupportTicket.id == candidate.id, ticket_visibility(identity)))
    if ticket is None:
        return missing
    return MockTicketView(status="available", observed_at=now, external_id=ticket.external_id,
        ticket_status=ticket.status, title=ticket.title, summary=ticket.summary, version=ticket.version)
