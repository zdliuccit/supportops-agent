"""显式资源权限的人工 Mock 工单命令；目标变更与命令事实同事务。"""

import hashlib
import json
from uuid import UUID
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from supportops_core.action_ledger_services import ActionStateConflict
from supportops_core.approval_services import live_identity, require_mock, require_mock_staff
from supportops_core.auth import IdentityContext
from supportops_core.config import Settings
from supportops_core.enums import AuditAction, AuditResourceType
from supportops_core.models import (
    AgentAuditEvent, AgentRun, BusinessAction, Conversation, MockSupportCase, MockSupportTicket, MockTicketCommand, utc_now,
)
from supportops_core.redaction import redact_sensitive_text
from supportops_core.services import ResourceNotFoundError
from supportops_core.ticket_services import ticket_visibility
from supportops_core.ticket_takeover_services import ensure_mock_case
from supportops_core.tool_policy import ToolPolicyError


async def can_handle_ticket(session: AsyncSession, *, identity: IdentityContext, owner_user_id: UUID) -> bool:
    try:
        await require_mock_staff(session, tenant_id=identity.principal.tenant_id, owner_user_id=owner_user_id,
            user_id=identity.user.id, permission="ticket_handle")
    except ToolPolicyError:
        return False
    return True


async def read_ticket_row(session: AsyncSession, *, identity: IdentityContext, external_id: str) -> MockSupportTicket:
    current = await live_identity(session, tenant_id=identity.principal.tenant_id, user_id=identity.user.id)
    ticket = await session.scalar(select(MockSupportTicket).where(MockSupportTicket.external_id == external_id,
        ticket_visibility(current)))
    if ticket is None:
        raise ResourceNotFoundError("模拟工单不存在或当前不可访问")
    return ticket


async def execute_ticket_command(session: AsyncSession, *, identity: IdentityContext, settings: Settings, external_id: str,
    kind: str, body: str, expected_version: int, idempotency_key: str, correlation_id: str,
) -> MockTicketCommand:
    require_mock(settings)
    current = await live_identity(session, tenant_id=identity.principal.tenant_id, user_id=identity.user.id)
    initial = await read_ticket_row(session, identity=current, external_id=external_id)
    await require_mock_staff(session, tenant_id=initial.tenant_id, owner_user_id=initial.owner_user_id,
        user_id=current.user.id, permission="ticket_handle")
    # 与 Agent 共用会话→问题→目标锁；这里不反向加 Run/Action 锁或追加 RunEvent。
    case = await session.scalar(select(MockSupportCase).where(MockSupportCase.tenant_id == initial.tenant_id,
        MockSupportCase.project_id == initial.project_id, MockSupportCase.issue_key == initial.issue_key))
    conversation_id = case.conversation_id if case is not None else None
    if conversation_id is None:
        action = await session.scalar(select(BusinessAction).where(BusinessAction.tenant_id == initial.tenant_id,
            BusinessAction.logical_key == initial.logical_key, BusinessAction.proposed_by == initial.owner_user_id))
        run = await session.get(AgentRun, action.execution_run_id or action.run_id) if action is not None else None
        conversation_id = run.conversation_id if run is not None else None
    if conversation_id is not None:
        await session.scalar(select(Conversation).where(Conversation.id == conversation_id, Conversation.tenant_id == initial.tenant_id)
            .execution_options(include_deleted=True).with_for_update(key_share=True))
    case = await ensure_mock_case(session, tenant_id=initial.tenant_id, project_id=initial.project_id,
        owner_user_id=initial.owner_user_id, issue_key=initial.issue_key, conversation_id=conversation_id)
    ticket = await session.scalar(select(MockSupportTicket).where(MockSupportTicket.id == initial.id, ticket_visibility(current))
        .execution_options(populate_existing=True).with_for_update())
    if ticket is None:
        raise ResourceNotFoundError("模拟工单不可访问")
    await require_mock_staff(session, tenant_id=ticket.tenant_id, owner_user_id=ticket.owner_user_id,
        user_id=current.user.id, permission="ticket_handle", lock=True)
    safe_body = redact_sensitive_text(body.strip())[:1000]
    digest = hashlib.sha256(json.dumps({"kind": kind, "body": safe_body, "version": expected_version,
        "actor": str(current.user.id)}, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    previous = await session.scalar(select(MockTicketCommand).where(MockTicketCommand.tenant_id == ticket.tenant_id,
        MockTicketCommand.ticket_id == ticket.id, MockTicketCommand.idempotency_key == idempotency_key))
    if previous is not None:
        if previous.actor_user_id != current.user.id or previous.request_digest != digest:
            raise ActionStateConflict("该操作幂等键已用于不同命令或人员，不能覆盖原结果")
        return previous
    if ticket.version != expected_version:
        raise ActionStateConflict("工单版本已变化，请重新读取后确认操作")
    if kind not in {"claim", "note", "reply", "close", "escalate", "release"}:
        raise ActionStateConflict("未知人工工单操作")
    if kind in {"note", "reply"} and not safe_body:
        raise ActionStateConflict("备注或回复必须填写正文")
    if ticket.status == "closed":
        raise ActionStateConflict("已关闭工单不能再次认领或回复")
    if kind == "claim":
        if case.is_active:
            old_valid = False
            if case.handler_user_id is not None:
                try:
                    await require_mock_staff(session, tenant_id=ticket.tenant_id, owner_user_id=ticket.owner_user_id,
                        user_id=case.handler_user_id, permission="ticket_handle")
                    old_valid = True
                except ToolPolicyError:
                    old_valid = False
            if old_valid:
                raise ActionStateConflict("该工单已有有效接管人员，需由当前人员明确释放")
        elif ticket.assigned_to is not None:
            raise ActionStateConflict("旧指派缺少可验证的接管记录，需管理员明确处理")
        case.is_active, case.handler_user_id = True, current.user.id
        ticket.assigned_to, ticket.status = current.user.id, "in_progress"
    else:
        if not case.is_active or case.handler_user_id != current.user.id or ticket.assigned_to != current.user.id:
            raise ToolPolicyError("TICKET_TAKEOVER_REQUIRED", "仅明确认领的当前处理人可修改工单")
        if kind == "close":
            ticket.status, case.is_active = "closed", False
        elif kind == "escalate":
            ticket.status = "escalated"
        elif kind == "release":
            ticket.status, ticket.assigned_to = "open", None
            case.is_active, case.handler_user_id = False, None
    ticket.version, ticket.updated_at = ticket.version + 1, utc_now()
    case.version, case.updated_at = case.version + 1, utc_now()
    command = MockTicketCommand(tenant_id=ticket.tenant_id, ticket_id=ticket.id, actor_user_id=current.user.id,
        idempotency_key=idempotency_key, request_digest=digest, kind=kind, body=safe_body,
        ticket_version=ticket.version, ticket_status=ticket.status)
    session.add(command)
    await session.flush()
    session.add(AgentAuditEvent(tenant_id=ticket.tenant_id, actor_user_id=current.user.id,
        resource_type=AuditResourceType.TOOL, action=AuditAction.MOCK_TICKET_COMMAND, correlation_id=correlation_id,
        metadata_payload={"command_id": str(command.id), "ticket_id": str(ticket.id), "kind": kind,
            "version": ticket.version, "status": ticket.status, "request_digest": digest, "data_environment": "mock"}))
    return command
