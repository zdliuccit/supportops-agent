"""隔离 Mock 工单队列、本人查询与显式人员接管。"""

from typing import Literal
from uuid import UUID
from fastapi import APIRouter, Depends, Path, Query, Request, Response
from sqlalchemy import func, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession
from supportops_core.approval_services import STAFF_ROLES, live_identity, require_mock, require_mock_staff
from supportops_core.auth import IdentityContext
from supportops_core.models import BusinessAction, MockResourceGrant, MockSupportCase, MockSupportTicket, MockTicketCommand, TicketEscalationPackage, User
from supportops_core.escalation_services import project_escalation_package
from supportops_core.knowledge_access_services import knowledge_read_transaction
from supportops_core.services import ResourceNotFoundError
from supportops_core.ticket_command_services import can_handle_ticket, execute_ticket_command, read_ticket_row
from supportops_core.ticket_services import ticket_visibility
from supportops_api.dependencies import current_identity, database_session, settings_from
from supportops_api.pagination import PaginationParams, pagination_metadata, pagination_params
from supportops_api.schemas import (
    MockTicketCommandListResponse, MockTicketCommandRequest, MockTicketCommandResponse,
    MockTicketListResponse, MockTicketResponse,
    EscalationPackageListResponse, EscalationPackageSummaryResponse, EscalationPackageDetailResponse,
)

router = APIRouter(prefix="/v1/mock-tickets", tags=["mock-tickets"])


async def _responses(session: AsyncSession, tickets: list[MockSupportTicket], identity: IdentityContext) -> list[MockTicketResponse]:
    if not tickets:
        return []
    owner_ids = {ticket.owner_user_id for ticket in tickets}
    pairs = [(ticket.project_id, ticket.issue_key) for ticket in tickets]
    cases = {(case.project_id, case.issue_key): case for case in (await session.scalars(select(MockSupportCase).where(
        MockSupportCase.tenant_id == identity.principal.tenant_id, tuple_(MockSupportCase.project_id, MockSupportCase.issue_key).in_(pairs)))).all()}
    handler_ids = {case.handler_user_id for case in cases.values() if case.handler_user_id is not None}
    user_ids = owner_ids | handler_ids | {ticket.assigned_to for ticket in tickets if ticket.assigned_to is not None}
    user_rows = (await session.execute(select(User.id, User.display_name, User.status, User.roles).where(User.tenant_id == identity.principal.tenant_id,
        User.id.in_(user_ids)))).all()
    users = {user.id: user.display_name for user in user_rows}
    valid_staff = {user.id for user in user_rows if user.status == "active" and STAFF_ROLES.intersection(user.roles)}
    grants = (await session.scalars(select(MockResourceGrant).where(MockResourceGrant.tenant_id == identity.principal.tenant_id,
        MockResourceGrant.owner_user_id.in_(owner_ids), MockResourceGrant.user_id.in_(handler_ids | {identity.user.id}),
        MockResourceGrant.permission.in_(["ticket_handle", "support_context_read"]), MockResourceGrant.is_enabled.is_(True)))).all()
    handled = {(grant.project_id, grant.owner_user_id) for grant in grants if grant.user_id == identity.user.id and grant.permission == "ticket_handle"} if STAFF_ROLES.intersection(identity.principal.roles) else set()
    context_readers = {(grant.project_id, grant.owner_user_id) for grant in grants if grant.user_id == identity.user.id and grant.permission == "support_context_read"} if STAFF_ROLES.intersection(identity.principal.roles) else set()
    valid_handlers = {(grant.user_id, grant.project_id, grant.owner_user_id) for grant in grants if grant.user_id in valid_staff and grant.permission == "ticket_handle"}
    actions = {action.logical_key: action.id for action in (await session.scalars(select(BusinessAction).where(
        BusinessAction.tenant_id == identity.principal.tenant_id, BusinessAction.tool_id == "create_mock_ticket",
        BusinessAction.logical_key.in_([ticket.logical_key for ticket in tickets])))).all()}
    result = []
    for ticket in tickets:
        case = cases.get((ticket.project_id, ticket.issue_key))
        active = case is not None and case.is_active
        permitted = (ticket.project_id, ticket.owner_user_id) in handled
        existing_handler_valid = active and (case.handler_user_id, ticket.project_id, ticket.owner_user_id) in valid_handlers
        result.append(MockTicketResponse.model_validate(ticket).model_copy(update={"owner_name": users.get(ticket.owner_user_id),
            "handler_name": users.get(ticket.assigned_to), "takeover_active": active,
            "can_claim": permitted and ticket.status != "closed" and ((not active and ticket.assigned_to is None) or (active and not existing_handler_valid)),
            "can_update": permitted and ticket.status != "closed" and active and case.handler_user_id == identity.user.id and ticket.assigned_to == identity.user.id,
            "source_action_id": actions.get(ticket.logical_key),
            "can_read_context": ticket.owner_user_id == identity.user.id or permitted and (ticket.project_id, ticket.owner_user_id) in context_readers}))
    return result


@router.get("", response_model=MockTicketListResponse)
async def list_mock_tickets(request: Request, state: Literal["open", "in_progress", "escalated", "closed"] | None = Query(default=None),
    keywords: str = Query(default="", max_length=200), pagination: PaginationParams = Depends(pagination_params),
    identity: IdentityContext = Depends(current_identity), session: AsyncSession = Depends(database_session),
) -> MockTicketListResponse:
    require_mock(settings_from(request))
    current = await live_identity(session, tenant_id=identity.principal.tenant_id, user_id=identity.user.id)
    filters = [ticket_visibility(current)]
    if state is not None:
        filters.append(MockSupportTicket.status == state)
    if keywords.strip():
        escaped = keywords.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        filters.append(MockSupportTicket.title.ilike("%" + escaped + "%", escape="\\"))
    total = int(await session.scalar(select(func.count()).select_from(MockSupportTicket).where(*filters)) or 0)
    items = list((await session.scalars(select(MockSupportTicket).where(*filters).order_by(MockSupportTicket.updated_at.desc(), MockSupportTicket.id.desc())
        .offset(pagination.offset).limit(pagination.page_size))).all())
    metadata = pagination_metadata(total, pagination)
    return MockTicketListResponse(items=await _responses(session, items, current), total=metadata.total,
        page=metadata.page, page_size=metadata.page_size, pages=metadata.pages)


@router.get("/{external_id}", response_model=MockTicketResponse)
async def get_mock_ticket(request: Request, external_id: str = Path(min_length=1, max_length=200),
    identity: IdentityContext = Depends(current_identity), session: AsyncSession = Depends(database_session),
) -> MockTicketResponse:
    require_mock(settings_from(request))
    current = await live_identity(session, tenant_id=identity.principal.tenant_id, user_id=identity.user.id)
    ticket = await read_ticket_row(session, identity=current, external_id=external_id)
    return (await _responses(session, [ticket], current))[0]


@router.get("/{external_id}/commands", response_model=MockTicketCommandListResponse)
async def list_ticket_commands(request: Request, external_id: str = Path(min_length=1, max_length=200),
    pagination: PaginationParams = Depends(pagination_params), identity: IdentityContext = Depends(current_identity),
    session: AsyncSession = Depends(database_session),
) -> MockTicketCommandListResponse:
    require_mock(settings_from(request))
    current = await live_identity(session, tenant_id=identity.principal.tenant_id, user_id=identity.user.id)
    ticket = await read_ticket_row(session, identity=current, external_id=external_id)
    filters = [MockTicketCommand.tenant_id == current.user.tenant_id, MockTicketCommand.ticket_id == ticket.id]
    if not await can_handle_ticket(session, identity=current, owner_user_id=ticket.owner_user_id):
        filters.append(MockTicketCommand.kind != "note")
    total = int(await session.scalar(select(func.count()).select_from(MockTicketCommand).where(*filters)) or 0)
    items = (await session.scalars(select(MockTicketCommand).where(*filters).order_by(MockTicketCommand.created_at.desc(), MockTicketCommand.id.desc())
        .offset(pagination.offset).limit(pagination.page_size))).all()
    names = dict((await session.execute(select(User.id, User.display_name).where(User.tenant_id == current.user.tenant_id,
        User.id.in_([item.actor_user_id for item in items])))).all()) if items else {}
    metadata = pagination_metadata(total, pagination)
    return MockTicketCommandListResponse(items=[MockTicketCommandResponse.model_validate(item).model_copy(update={"actor_name": names.get(item.actor_user_id)}) for item in items],
        total=metadata.total, page=metadata.page, page_size=metadata.page_size, pages=metadata.pages)


async def _escalation_filters(session: AsyncSession, *, identity: IdentityContext, external_id: str):
    ticket = await read_ticket_row(session, identity=identity, external_id=external_id)
    if ticket.owner_user_id != identity.user.id:
        for permission in ("ticket_handle", "support_context_read"):
            await require_mock_staff(session, tenant_id=identity.principal.tenant_id, owner_user_id=ticket.owner_user_id,
                user_id=identity.user.id, permission=permission)
    # 工单逻辑键来自目标事实，客户端不能指定另一工单的来源动作。
    action_ids = select(BusinessAction.id).where(BusinessAction.tenant_id == ticket.tenant_id,
        BusinessAction.proposed_by == ticket.owner_user_id, BusinessAction.tool_id == "create_mock_ticket",
        BusinessAction.logical_key == ticket.logical_key)
    return (TicketEscalationPackage.tenant_id == ticket.tenant_id,
        TicketEscalationPackage.user_id == ticket.owner_user_id, TicketEscalationPackage.action_id.in_(action_ids))


@router.get("/{external_id}/escalations", response_model=EscalationPackageListResponse)
async def list_ticket_escalations(request: Request, external_id: str = Path(min_length=1, max_length=200),
    pagination: PaginationParams = Depends(pagination_params), identity: IdentityContext = Depends(current_identity),
    session: AsyncSession = Depends(database_session),
) -> EscalationPackageListResponse:
    require_mock(settings_from(request))
    current = await live_identity(session, tenant_id=identity.principal.tenant_id, user_id=identity.user.id)
    filters = await _escalation_filters(session, identity=current, external_id=external_id)
    total = int(await session.scalar(select(func.count()).select_from(TicketEscalationPackage).where(*filters)) or 0)
    items = (await session.scalars(select(TicketEscalationPackage).where(*filters)
        .order_by(TicketEscalationPackage.created_at.desc(), TicketEscalationPackage.id.desc())
        .offset(pagination.offset).limit(pagination.page_size))).all()
    metadata = pagination_metadata(total, pagination)
    return EscalationPackageListResponse(items=[EscalationPackageSummaryResponse.model_validate(item) for item in items],
        total=metadata.total, page=metadata.page, page_size=metadata.page_size, pages=metadata.pages)


@router.get("/{external_id}/escalations/{package_id}", response_model=EscalationPackageDetailResponse)
async def read_ticket_escalation(request: Request, response: Response, package_id: UUID, external_id: str = Path(min_length=1, max_length=200),
    identity: IdentityContext = Depends(current_identity), session: AsyncSession = Depends(database_session),
) -> EscalationPackageDetailResponse:
    require_mock(settings_from(request))
    response.headers["Cache-Control"] = "no-store"
    async with knowledge_read_transaction(session):
        current = await live_identity(session, tenant_id=identity.principal.tenant_id, user_id=identity.user.id)
        filters = await _escalation_filters(session, identity=current, external_id=external_id)
        package = await session.scalar(select(TicketEscalationPackage).where(*filters, TicketEscalationPackage.id == package_id))
        if package is None:
            raise ResourceNotFoundError("升级包不存在或不可访问")
        payload = await project_escalation_package(session, package=package, identity=current)
        result = EscalationPackageDetailResponse(**EscalationPackageSummaryResponse.model_validate(package).model_dump(), payload=payload)
    return result


@router.post("/{external_id}/commands", response_model=MockTicketCommandResponse)
async def command_mock_ticket(payload: MockTicketCommandRequest, request: Request, external_id: str = Path(min_length=1, max_length=200),
    identity: IdentityContext = Depends(current_identity), session: AsyncSession = Depends(database_session),
) -> MockTicketCommandResponse:
    async with session.begin():
        command = await execute_ticket_command(session, identity=identity, settings=settings_from(request), external_id=external_id,
            kind=payload.kind, body=payload.body, expected_version=payload.expected_version,
            idempotency_key=payload.idempotency_key, correlation_id=request.state.correlation_id)
    return MockTicketCommandResponse.model_validate(command).model_copy(update={"actor_name": identity.user.display_name})
