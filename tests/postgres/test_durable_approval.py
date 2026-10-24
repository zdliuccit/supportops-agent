"""真实 API/Worker/PG 的隔离 R4 审批门禁与持久化恢复；只替换外部模型。"""

import asyncio
import json
import os
import sys
from dataclasses import dataclass
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessageChunk
from langchain_core.outputs import ChatGenerationChunk
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from supportops_core import action_runtime, agent_runtime, worker_runtime
from supportops_core.enums import AuditAction, GrantSubjectType, MessageRole, RunStatus
from supportops_core.models import (
    AgentAccessGrant,
    AgentAuditEvent,
    AgentRun,
    ApprovalConsumption,
    ApprovalDecision,
    ApprovalRequest,
    BusinessAction,
    Conversation,
    Message,
    MockRemoteOperation,
    RunAttempt,
    RunEvent,
    User,
    utc_now,
)

from tests.conftest import auth_headers
from tests.postgres.test_action_reconciliation import ActionCase, completed_run, counts, step
from tests.postgres.test_action_reconciliation import action_case as action_case
from tests.postgres.test_action_reconciliation import knowledge_case as knowledge_case
from tests.postgres.test_action_reconciliation import settings as settings
from tests.postgres.test_knowledge_access import engine as engine

pytestmark = pytest.mark.skipif(
    os.getenv("SUPPORTOPS_RUN_POSTGRES_TESTS") != "1", reason="需显式启用隔离 PostgreSQL 测试库"
)


@dataclass
class ApprovalCase:
    action: ActionCase
    reviewer_id: UUID
    reviewer_headers: dict[str, str]
    stranger_id: UUID
    stranger_headers: dict[str, str]
    model_calls: list

    @property
    def knowledge(self):
        return self.action.knowledge

    @property
    def factory(self):
        return self.action.factory


@pytest.fixture
async def approval_case(action_case: ActionCase, monkeypatch):
    case = action_case.knowledge
    reviewer = await auth_headers(
        case.engine, case.settings, case.tenant_id, "approval-reviewer", roles=("employee",)
    )
    stranger = await auth_headers(
        case.engine, case.settings, case.tenant_id, "approval-stranger", roles=("employee",)
    )
    value = ApprovalCase(
        action_case,
        uuid5(NAMESPACE_URL, f"{case.tenant_id}:approval-reviewer"),
        reviewer,
        uuid5(NAMESPACE_URL, f"{case.tenant_id}:approval-stranger"),
        stranger,
        [],
    )
    # 经真实控制面固定 R4 策略；仍只连接隔离 Mock 目标。
    catalog = await case.client.get("/v1/admin/tools", headers=case.owner_headers)
    assert catalog.status_code == 200
    tool = next(item for item in catalog.json()["items"] if item["tool_id"] == "create_mock_ticket")
    changed = await case.client.patch(
        "/v1/admin/tools/create_mock_ticket",
        headers=case.owner_headers,
        json={
            "name": tool["name"],
            "description": tool["description"],
            "required_roles": tool["required_roles"],
            "risk_level": "high",
            "is_enabled": True,
        },
    )
    assert changed.status_code == 200, changed.text
    await grant(value, value.reviewer_id)

    class ProposalModel(BaseChatModel):
        @property
        def _llm_type(self):
            return "isolated-r4-approval"

        def bind_tools(self, tools, **kwargs):
            return self

        def _generate(self, messages, **kwargs):
            raise AssertionError("只允许隔离流式模型")

        async def _astream(self, messages, **kwargs):
            value.model_calls.append(messages)
            proposing = len(value.model_calls) % 2 == 1
            yield ChatGenerationChunk(
                message=AIMessageChunk(
                    id=f"approval-model-{len(value.model_calls)}",
                    content="",
                    tool_call_chunks=[
                        {
                            "index": 0,
                            "name": "create_mock_ticket" if proposing else "SupportAnswer",
                            "args": json.dumps(
                                {} if proposing else {"answer": "提案已冻结，等待独立审批。"}
                            ),
                            "id": f"approval-intent-{len(value.model_calls)}",
                        }
                    ],
                )
            )

    async def build(*args, **kwargs):
        return ProposalModel()

    monkeypatch.setattr(agent_runtime.ModelAdapter, "build", build)
    monkeypatch.setattr(worker_runtime, "invoke_agent", agent_runtime.invoke_agent)
    return value


async def grant(case: ApprovalCase, user_id: UUID, owner_id: UUID | None = None):
    response = await case.knowledge.client.post(
        "/v1/admin/mock-resource-grants",
        headers=case.knowledge.owner_headers,
        json={
            "owner_user_id": str(owner_id or case.knowledge.reader_id),
            "user_id": str(user_id),
            "permission": "approve",
            "is_enabled": True,
            "expected_revision": 0,
        },
    )
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    return response.json()


async def waiting(case: ApprovalCase):
    run_id = await completed_run(case.action)
    async with case.factory() as session:
        run = await session.get(AgentRun, run_id)
        approval = await session.scalar(
            select(ApprovalRequest).where(ApprovalRequest.run_id == run_id)
        )
        assert run.status == RunStatus.WAITING_APPROVAL and approval is not None
        action = await session.get(BusinessAction, approval.action_id)
        assert action.status == "waiting_approval" and action.risk_level == "R4"
        assert action.approval_required and approval.status == "pending"
        return run_id, approval, action


async def decide(case: ApprovalCase, approval: ApprovalRequest, decision="approved", **changes):
    response = await case.knowledge.client.post(
        f"/v1/approvals/{approval.id}/decision",
        headers=case.reviewer_headers,
        json={
            "expected_revision": approval.revision,
            "decision": decision,
            "reason": "核对当前隔离项目与冻结参数后决定",
            **changes,
        },
    )
    if response.status_code == 200:
        assert response.headers["cache-control"] == "no-store"
    return response


async def no_side_effects(case: ApprovalCase, action_id: UUID):
    assert await counts(case.action, action_id) == ([], [], [])
    async with case.factory() as session:
        for model in (ApprovalConsumption, MockRemoteOperation):
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(model)
                    .where(model.tenant_id == case.knowledge.tenant_id)
                )
                == 0
            )


async def journal(case: ApprovalCase, run_id: UUID):
    async with case.factory() as session:
        run = await session.get(AgentRun, run_id)
        conversation = await session.get(Conversation, run.conversation_id)
        attempt = await session.get(RunAttempt, run.current_attempt_id)
        messages = (
            await session.scalars(
                select(Message)
                .where(
                    Message.conversation_id == run.conversation_id,
                    Message.role == MessageRole.ASSISTANT,
                )
                .order_by(Message.created_at, Message.id)
            )
        ).all()
        return run, conversation, attempt, messages


async def test_pending_r4_releases_worker_and_preserves_frozen_request_without_side_effects(
    approval_case,
):
    case = approval_case
    run_id, approval, action = await waiting(case)
    run, conversation, attempt, messages = await journal(case, run_id)
    assert run.finished_at is None and attempt.status == "completed" and attempt.finished_at
    assert conversation.active_run_id is None and conversation.active_attempt_id is None
    assert len(messages) == 1 and messages[0].support_result["answer"] == messages[0].content
    assert approval.parameters_digest == action.parameters_digest
    assert (approval.tenant_id, approval.applicant_id, approval.approver_id) == (
        case.knowledge.tenant_id,
        case.knowledge.reader_id,
        case.reviewer_id,
    )
    assert approval.tool_id == action.tool_id and approval.catalog_version == action.catalog_version
    assert approval.implementation_version == action.implementation_version
    assert conversation.checkpoint_thread_id == attempt.checkpoint_thread_id
    assert conversation.checkpoint_id == attempt.checkpoint_id and attempt.checkpoint_id
    async with agent_runtime.checkpoint_saver(case.knowledge.settings) as saver:
        checkpoint = await saver.aget_tuple(
            {
                "configurable": {
                    "thread_id": attempt.checkpoint_thread_id,
                    "checkpoint_id": attempt.checkpoint_id,
                }
            }
        )
        assert checkpoint is not None
    async with case.factory() as session, session.begin():
        assert (
            await action_runtime.claim_action(
                session, settings=case.knowledge.settings, owner_id="unapproved-consumer"
            )
            is None
        )
    assert not await worker_runtime.execute_agent_run(
        case.factory, run_id, settings=case.knowledge.settings
    )
    assert len(case.model_calls) == 2
    await no_side_effects(case, action.id)


_RESUME = """
import asyncio
from supportops_core.config import Settings
from supportops_core.db import create_engine, create_session_factory
from supportops_core.action_runtime import claim_action, execute_action
async def main():
    settings = Settings()
    engine = create_engine(settings)
    factory = create_session_factory(engine)
    try:
        for kind in ('execute', 'verify'):
            async with factory() as session, session.begin():
                permit = await claim_action(
                    session, settings=settings, owner_id='fresh-r4-consumer')
                assert permit is not None and permit.kind == kind
            await execute_action(factory, permit=permit, settings=settings)
        async with factory() as session, session.begin():
            permit = await claim_action(session, settings=settings, owner_id='fresh-r4-consumer')
            assert permit is None
        print('R4_RESUMED', flush=True)
    finally:
        await engine.dispose()
asyncio.run(main())
"""


@pytest.mark.parametrize("restart", [False, True], ids=["new-session", "new-process"])
async def test_approved_r4_resumes_original_action_and_independently_verifies_once(
    approval_case, restart
):
    case = approval_case
    run_id, approval, action = await waiting(case)
    before = await journal(case, run_id)
    response = await decide(case, approval)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "approved" and response.json()["action_status"] == "queued"
    assert (await journal(case, run_id))[0].status == RunStatus.WAITING_ACTION
    await no_side_effects(case, action.id)
    if restart:
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            _RESUME,
            env={**os.environ, "SUPPORTOPS_BUSINESS_ACTION_MOCK_ENABLED": "true"},
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            async with asyncio.timeout(30):
                stdout, _ = await process.communicate()
            assert process.returncode == 0 and b"R4_RESUMED" in stdout, "新进程审批恢复未完成"
        finally:
            if process.returncode is None:
                process.kill()
                await process.communicate()
    else:
        await step(case.action, action.id)
        assert (await journal(case, run_id))[0].status == RunStatus.WAITING_ACTION
        attempts, receipts, tickets = await counts(case.action, action.id)
        assert len(attempts) == len(receipts) == len(tickets) == 1
        assert receipts[0].status == "submitted"
        await step(case.action, action.id)
    after = await journal(case, run_id)
    assert after[0].status == RunStatus.COMPLETED and after[0].finished_at
    assert after[0].current_attempt_id == before[0].current_attempt_id
    assert (after[1].checkpoint_thread_id, after[1].checkpoint_id) == (
        before[1].checkpoint_thread_id,
        before[1].checkpoint_id,
    )
    assert [item.id for item in after[3]] == [item.id for item in before[3]]
    assert len(case.model_calls) == 2
    attempts, receipts, tickets = await counts(case.action, action.id)
    assert [item.kind for item in attempts] == ["execute", "verify"]
    assert [item.status for item in receipts] == ["submitted", "verified"]
    assert len(tickets) == 1 and tickets[0].owner_user_id == case.knowledge.reader_id
    assert (tickets[0].title, tickets[0].summary, tickets[0].logical_key) == (
        action.parameters["title"],
        action.parameters["summary"],
        action.logical_key,
    )
    async with case.factory() as session:
        consumptions = (
            await session.scalars(
                select(ApprovalConsumption).where(ApprovalConsumption.action_id == action.id)
            )
        ).all()
        assert len(consumptions) == 1 and consumptions[0].attempt_id == attempts[0].id
        assert (await session.get(ApprovalRequest, approval.id)).status == "consumed"
        assert (await session.get(BusinessAction, action.id)).external_id == tickets[0].external_id
        assert (
            await session.scalar(
                select(func.count())
                .select_from(AgentAccessGrant)
                .where(AgentAccessGrant.subject_id == str(case.reviewer_id))
            )
            == 0
        )


@pytest.mark.parametrize(
    "actor", ["applicant", "admin", "stranger", "other-assigned", "other-project", "other-tenant"]
)
async def test_nonassigned_or_self_decision_is_denied_with_independent_audit(approval_case, actor):
    case = approval_case
    _, approval, action = await waiting(case)
    audit_tenant = case.knowledge.tenant_id
    if actor == "applicant":
        headers = case.knowledge.reader_headers
    elif actor == "admin":
        headers = case.knowledge.owner_headers
    elif actor == "other-tenant":
        audit_tenant = uuid4()
        headers = await auth_headers(
            case.knowledge.engine,
            case.knowledge.settings,
            audit_tenant,
            "other-tenant",
            roles=("platform_admin",),
        )
    else:
        headers = case.stranger_headers
        if actor in {"other-assigned", "other-project"}:
            await grant(
                case,
                case.stranger_id,
                case.knowledge.owner_id if actor == "other-project" else None,
            )
    response = await case.knowledge.client.post(
        f"/v1/approvals/{approval.id}/decision",
        headers=headers,
        json={"expected_revision": approval.revision, "decision": "approved", "reason": "不得扩权"},
    )
    assert response.status_code == (403 if actor == "applicant" else 404), response.text
    if actor != "applicant":
        assert (
            "parameters" not in response.json()
            and action.parameters["summary"] not in response.text
        )
    async with case.factory() as session:
        assert (await session.get(ApprovalRequest, approval.id)).status == "pending"
        assert (
            await session.scalar(
                select(func.count())
                .select_from(ApprovalDecision)
                .where(ApprovalDecision.request_id == approval.id)
            )
            == 0
        )
        audits = (
            await session.scalars(
                select(AgentAuditEvent).where(
                    AgentAuditEvent.tenant_id == audit_tenant,
                    AgentAuditEvent.action == AuditAction.APPROVAL_DENIED,
                )
            )
        ).all()
        assert len(audits) == 1 and audits[0].metadata_payload["requested_id"] == str(approval.id)
        assert set(audits[0].metadata_payload) == {"requested_id", "code", "data_environment"}
    await no_side_effects(case, action.id)


@pytest.mark.parametrize(
    "actor", ["self", "ungranted", "other-project", "customer", "other-tenant"]
)
async def test_request_rejects_self_or_person_without_current_project_permission(
    approval_case, actor
):
    case = approval_case
    run_id, approval, action = await waiting(case)
    user_id = case.knowledge.reader_id if actor == "self" else case.stranger_id
    if actor == "other-project":
        await grant(case, user_id, case.knowledge.owner_id)
    if actor == "customer":
        await grant(case, user_id)
        async with case.factory() as session, session.begin():
            (await session.get(User, user_id)).roles = ["customer"]
    if actor == "other-tenant":
        tenant = uuid4()
        await auth_headers(
            case.knowledge.engine, case.knowledge.settings, tenant, "foreign", roles=("employee",)
        )
        user_id = uuid5(NAMESPACE_URL, f"{tenant}:foreign")
    response = await case.knowledge.client.post(
        f"/v1/actions/{action.id}/approval",
        headers=case.knowledge.reader_headers,
        json={
            "context_run_id": str(run_id),
            "approver_id": str(user_id),
            "expected_generation": action.generation,
            "expected_parameters_digest": action.parameters_digest,
            "expected_catalog_version": action.catalog_version,
        },
    )
    assert response.status_code == 403, response.text
    async with case.factory() as session:
        rows = (
            await session.scalars(
                select(ApprovalRequest).where(ApprovalRequest.action_id == action.id)
            )
        ).all()
        assert len(rows) == 1 and rows[0].id == approval.id and rows[0].status == "pending"
    await no_side_effects(case, action.id)


async def test_pending_direct_execute_cannot_bypass_approval(approval_case):
    case = approval_case
    run_id, _, action = await waiting(case)
    response = await case.knowledge.client.post(
        f"/v1/actions/{action.id}/execute",
        headers=case.knowledge.reader_headers,
        json={
            "context_run_id": str(run_id),
            "expected_generation": action.generation,
            "expected_parameters_digest": action.parameters_digest,
            "expected_catalog_version": action.catalog_version,
        },
    )
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "ACTION_STATE_CONFLICT"
    await no_side_effects(case, action.id)


async def test_rejected_r4_finishes_wait_without_execution_or_another_model_answer(approval_case):
    case = approval_case
    run_id, approval, action = await waiting(case)
    before = await journal(case, run_id)
    response = await decide(case, approval, "rejected")
    assert response.status_code == 200 and response.json()["status"] == "rejected"
    assert response.json()["action_status"] == "cancelled"
    after = await journal(case, run_id)
    assert after[0].status == RunStatus.COMPLETED
    assert [item.id for item in after[3]] == [item.id for item in before[3]]
    assert len(case.model_calls) == 2
    async with case.factory() as session, session.begin():
        assert (
            await action_runtime.claim_action(
                session, settings=case.knowledge.settings, owner_id="rejected"
            )
            is None
        )
    await no_side_effects(case, action.id)


@pytest.mark.parametrize("reason", ["", "   ", "过" * 1001])
async def test_invalid_decision_reason_keeps_pending_and_has_no_execution(approval_case, reason):
    case = approval_case
    _, approval, action = await waiting(case)
    response = await decide(case, approval, reason=reason)
    assert response.status_code == (409 if reason == "   " else 422), response.text
    async with case.factory() as session:
        assert (await session.get(ApprovalRequest, approval.id)).status == "pending"
    await no_side_effects(case, action.id)


async def test_self_approval_is_also_rejected_by_database_constraint(approval_case):
    case = approval_case
    _, approval, action = await waiting(case)
    async with case.factory() as session:
        original = await session.get(ApprovalRequest, approval.id)
        with pytest.raises(IntegrityError, match="ck_approval_nonself"):
            async with session.begin_nested():
                original.approver_id = original.applicant_id
                await session.flush()
    await no_side_effects(case, action.id)


async def test_current_identity_filtered_list_and_detail_do_not_expose_other_pending_items(
    approval_case,
):
    case = approval_case
    _, approval, action = await waiting(case)
    for headers, visible in [
        (case.reviewer_headers, True),
        (case.knowledge.reader_headers, True),
        (case.stranger_headers, False),
        (case.knowledge.owner_headers, False),
    ]:
        listing = await case.knowledge.client.get(
            "/v1/approvals?page=1&page_size=1", headers=headers
        )
        assert listing.status_code == 200 and listing.headers["cache-control"] == "no-store"
        assert listing.json()["total"] == int(visible)
        assert all(
            "parameters" not in item and "scope_digest" not in item
            for item in listing.json()["items"]
        )
        detail = await case.knowledge.client.get(f"/v1/approvals/{approval.id}", headers=headers)
        assert detail.status_code == (200 if visible else 404)
        assert detail.headers["cache-control"] == "no-store"
        if visible:
            assert detail.json()["can_decide"] == (headers == case.reviewer_headers)
            assert detail.json()["parameters"] == action.parameters
    await no_side_effects(case, action.id)


async def test_reviewer_permission_cannot_replace_applicants_current_agent_authorization(
    approval_case,
):
    case = approval_case
    _, approval, action = await waiting(case)
    async with case.factory() as session, session.begin():
        access = await session.scalar(
            select(AgentAccessGrant).where(
                AgentAccessGrant.tenant_id == case.knowledge.tenant_id,
                AgentAccessGrant.agent_id == case.action.agent_id,
                AgentAccessGrant.subject_type == GrantSubjectType.USER,
                AgentAccessGrant.subject_id == str(case.knowledge.reader_id),
            )
        )
        assert access is not None
        access.deleted_at = utc_now()
    response = await decide(case, approval)
    assert response.status_code == 403, response.text
    async with case.factory() as session:
        assert (await session.get(ApprovalRequest, approval.id)).status == "pending"
        assert (
            await session.scalar(
                select(func.count())
                .select_from(ApprovalDecision)
                .where(ApprovalDecision.request_id == approval.id)
            )
            == 0
        )
    await no_side_effects(case, action.id)


async def test_decision_reason_is_redacted_and_only_digest_enters_events(approval_case):
    case = approval_case
    run_id, approval, action = await waiting(case)
    secret = "isolated-approval-test-value"
    response = await decide(
        case, approval, "rejected", reason=f"核对拒绝\nAuthorization: Bearer {secret}"
    )
    assert response.status_code == 200, response.text
    assert secret not in response.text and "[REDACTED]" in response.json()["decision_reason"]
    async with case.factory() as session:
        events = (
            await session.scalars(
                select(RunEvent).where(
                    RunEvent.run_id == run_id, RunEvent.event_type == "approval.decided"
                )
            )
        ).all()
        assert len(events) == 1 and len(events[0].data["reason_digest"]) == 64
        assert secret not in json.dumps(events[0].data) and "reason" not in events[0].data
        decision = await session.scalar(
            select(ApprovalDecision).where(ApprovalDecision.request_id == approval.id)
        )
        assert secret not in decision.reason and "[REDACTED]" in decision.reason
    await no_side_effects(case, action.id)
