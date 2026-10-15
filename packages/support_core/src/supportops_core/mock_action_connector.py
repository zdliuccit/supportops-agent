"""隔离 Mock 目标：独立提交、稳定幂等、封锁迟到请求后证明未执行。"""

from datetime import UTC, datetime
from typing import Literal
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from supportops_core.action_contracts import MockTicketParameters, frozen_parameters_digest
from supportops_core.agent_config import StrictModel
from supportops_core.config import Settings
from supportops_core.models import BusinessAction, Conversation, MockRemoteOperation, MockSupportTicket
from supportops_core.ticket_takeover_services import assert_mock_write_allowed
from supportops_core.action_ledger_services import ActionStateConflict, assert_action_release_allowed


class MockTargetError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class MockTargetProof(StrictModel):
    connector_version: Literal["mock_ticket_target_v1"] = "mock_ticket_target_v1"
    data_environment: Literal["mock"] = "mock"
    status: Literal["submitted", "verified", "not_executed", "unknown", "conflict"]
    logical_key: str
    parameters_digest: str
    owner_user_id: UUID
    external_id: str | None = None
    ticket_status: str | None = None
    execution_fence: int = Field(ge=0)
    observed_at: datetime


def _require_mock(settings: Settings) -> None:
    if not settings.business_action_mock_enabled or settings.environment == "production":
        raise MockTargetError("ACTION_CONNECTOR_UNAVAILABLE")


async def _operation(session: AsyncSession, *, tenant_id: UUID, owner_user_id: UUID,
    logical_key: str, digest: str,
) -> MockRemoteOperation:
    lookup = select(MockRemoteOperation).where(MockRemoteOperation.tenant_id == tenant_id,
        MockRemoteOperation.logical_key == logical_key).with_for_update()
    row = await session.scalar(lookup)
    if row is None:
        try:
            async with session.begin_nested():
                row = MockRemoteOperation(tenant_id=tenant_id, owner_user_id=owner_user_id, logical_key=logical_key,
                    parameters_digest=digest, status="not_executed", execution_fence=0, fault_consumed=False)
                session.add(row)
                await session.flush()
        except IntegrityError:
            row = await session.scalar(lookup)
            if row is None:
                raise
    if row.owner_user_id != owner_user_id or row.parameters_digest != digest:
        raise MockTargetError("MOCK_TARGET_IDEMPOTENCY_CONFLICT")
    return row


async def submit_mock_ticket(session_factory: async_sessionmaker[AsyncSession], *, settings: Settings, tenant_id: UUID,
    owner_user_id: UUID, project_id: UUID, logical_key: str, generation: int, parameters: MockTicketParameters,
    mode: str, conversation_id: UUID, action_id: UUID, agent_version_id: UUID,
) -> MockTargetProof:
    _require_mock(settings)
    digest = frozen_parameters_digest(parameters)
    pending_error = None
    async with session_factory() as session, session.begin():
        conversation = await session.scalar(select(Conversation).where(Conversation.id == conversation_id,
            Conversation.tenant_id == tenant_id, Conversation.user_id == owner_user_id)
            .execution_options(include_deleted=True).with_for_update(key_share=True))
        if conversation is None or conversation.deleted_at is not None:
            raise MockTargetError("MOCK_SOURCE_CONVERSATION_UNAVAILABLE")
        action = await session.scalar(select(BusinessAction).where(BusinessAction.id == action_id,
            BusinessAction.tenant_id == tenant_id).execution_options(populate_existing=True).with_for_update())
        if (action is None or action.proposed_by != owner_user_id or action.resource_id != str(project_id)
            or action.logical_key != logical_key or action.parameters_digest != digest or action.generation != generation
            or action.status != "running" or action.execution_agent_version_id != agent_version_id
            or conversation.agent_version_id != agent_version_id):
            raise MockTargetError("MOCK_ACTION_EXECUTION_SCOPE_CHANGED")
        # 与回滚的版本撤回锁串行，检查与隔离目标写入同事务，关闭出站后的间隙。
        try:
            await assert_action_release_allowed(session, tenant_id=tenant_id, version_ids={agent_version_id, action.agent_version_id})
        except ActionStateConflict as exc:
            raise MockTargetError("ACTION_RELEASE_WITHDRAWN") from exc
        try:
            await assert_mock_write_allowed(session, tenant_id=tenant_id, project_id=project_id, owner_user_id=owner_user_id,
                issue_key=parameters.issue_key, conversation_id=conversation_id)
        except ActionStateConflict as exc:
            raise MockTargetError("MOCK_CASE_HUMAN_TAKEOVER") from exc
        operation = await _operation(session, tenant_id=tenant_id, owner_user_id=owner_user_id, logical_key=logical_key, digest=digest)
        if operation.status == "executed":
            return MockTargetProof(status="submitted", logical_key=logical_key, parameters_digest=digest, owner_user_id=owner_user_id,
                external_id=operation.external_id, execution_fence=operation.execution_fence, observed_at=datetime.now(UTC))
        if operation.status == "unknown":
            return MockTargetProof(status="unknown", logical_key=logical_key, parameters_digest=digest, owner_user_id=owner_user_id,
                execution_fence=operation.execution_fence, observed_at=datetime.now(UTC))
        if generation <= operation.execution_fence:
            raise MockTargetError("MOCK_REMOTE_ATTEMPT_FENCED")
        if not operation.fault_consumed and mode in {"timeout", "not_executed", "unknown"}:
            operation.fault_consumed = True
            operation.execution_fence = generation
            operation.status = "unknown" if mode == "unknown" else "not_executed"
            proof = MockTargetProof(status="unknown", logical_key=logical_key, parameters_digest=digest,
                owner_user_id=owner_user_id, execution_fence=generation, observed_at=datetime.now(UTC))
            if mode != "unknown":
                pending_error = "MOCK_ACTION_TIMEOUT"
        else:
            ticket_id = uuid5(NAMESPACE_URL, f"supportops:mock-ticket:{tenant_id}:{logical_key}")
            ticket = MockSupportTicket(id=ticket_id, tenant_id=tenant_id, owner_user_id=owner_user_id, project_id=project_id,
                logical_key=logical_key, external_id=f"MOCK-{ticket_id}", title=parameters.title, summary=parameters.summary,
                issue_key=parameters.issue_key, status="open", version=1)
            if mode == "conflict" and not operation.fault_consumed:
                ticket.title = "模拟：目标返回成功但状态与冻结参数不同"
            session.add(ticket)
            operation.status, operation.external_id = "executed", ticket.external_id
            if mode in {"lost", "conflict"} and not operation.fault_consumed:
                operation.fault_consumed = True
                if mode == "lost":
                    pending_error = "MOCK_ACTION_RESPONSE_LOST"
            proof = MockTargetProof(status="submitted", logical_key=logical_key, parameters_digest=digest,
                owner_user_id=owner_user_id, external_id=ticket.external_id, execution_fence=operation.execution_fence,
                observed_at=datetime.now(UTC))
    # 目标事务已提交；响应丢失不能回滚目标，也不能改用新业务键。
    if pending_error is not None:
        raise MockTargetError(pending_error)
    return proof


async def query_mock_ticket_proof(session_factory: async_sessionmaker[AsyncSession], *, settings: Settings,
    tenant_id: UUID, owner_user_id: UUID, logical_key: str, generation: int, parameters: MockTicketParameters,
) -> MockTargetProof:
    _require_mock(settings)
    digest = frozen_parameters_digest(parameters)
    async with session_factory() as session, session.begin():
        operation = await _operation(session, tenant_id=tenant_id, owner_user_id=owner_user_id, logical_key=logical_key, digest=digest)
        if operation.status == "unknown":
            return MockTargetProof(status="unknown", logical_key=logical_key, parameters_digest=digest,
                owner_user_id=owner_user_id, execution_fence=operation.execution_fence, observed_at=datetime.now(UTC))
        if operation.status == "not_executed":
            # 这是一项明确的目标端能力：封锁旧请求，而非把普通 404 当作未执行证明。
            operation.execution_fence = max(operation.execution_fence, generation)
            return MockTargetProof(status="not_executed", logical_key=logical_key, parameters_digest=digest,
                owner_user_id=owner_user_id, execution_fence=operation.execution_fence, observed_at=datetime.now(UTC))
        ticket = await session.scalar(select(MockSupportTicket).where(MockSupportTicket.tenant_id == tenant_id,
            MockSupportTicket.logical_key == logical_key))
        expected_project = uuid5(NAMESPACE_URL, f"supportops:mock-project:{tenant_id}:{owner_user_id}")
        if (ticket is None or ticket.owner_user_id != owner_user_id or ticket.external_id != operation.external_id
            or ticket.project_id != expected_project or ticket.status not in {"open", "in_progress", "escalated", "closed"}):
            return MockTargetProof(status="conflict", logical_key=logical_key, parameters_digest=digest,
                owner_user_id=owner_user_id, external_id=operation.external_id, execution_fence=operation.execution_fence,
                observed_at=datetime.now(UTC))
        actual = MockTicketParameters(title=ticket.title, summary=ticket.summary, issue_key=ticket.issue_key)
        actual_digest = frozen_parameters_digest(actual)
        return MockTargetProof(status="verified" if actual_digest == digest else "conflict", logical_key=logical_key,
            parameters_digest=actual_digest, owner_user_id=owner_user_id, external_id=ticket.external_id,
            ticket_status=ticket.status, execution_fence=operation.execution_fence, observed_at=datetime.now(UTC))
