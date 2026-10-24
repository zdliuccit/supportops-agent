"""取消覆盖当前授权、审批等待及已派发动作的迟到回执。"""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import cast
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine
from supportops_core import action_runtime, worker_runtime
from supportops_core.action_contracts import MockTicketParameters, frozen_parameters_digest
from supportops_core.action_ledger_services import ActionStateConflict, transition_business_action
from supportops_core.agent_config import SupportAnswer
from supportops_core.approval_services import mock_project_id, settle_waiting_run
from supportops_core.config import Settings
from supportops_core.db import create_session_factory
from supportops_core.enums import MessageRole, RunStatus
from supportops_core.mock_action_connector import MockTargetProof
from supportops_core.models import (
    ActionAttempt,
    ActionReceipt,
    AgentAccessGrant,
    AgentRun,
    ApprovalRequest,
    BusinessAction,
    Conversation,
    Message,
    RunAttempt,
    RunEvent,
    ToolInvocation,
    User,
    utc_now,
)
from supportops_core.run_attempt_services import OwnedRun, claim_run_attempt, release_conversation
from supportops_core.services import cancel_run, delete_conversation

from tests.conftest import auth_headers
from tests.worker.test_worker import seed_run


@pytest.mark.parametrize("boundary", ["cancel", "delete", "revoke_then_cancel", "revoke"])
async def test_late_model_answer_cannot_survive_cancel_or_revocation(
    engine: AsyncEngine,
    settings: Settings,
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    boundary: str,
) -> None:
    settings = settings.model_copy(update={"run_attempt_heartbeat_seconds": 0.02})
    run_id = await seed_run(engine, settings)
    factory = create_session_factory(engine)
    async with factory() as session:
        run = await session.get(AgentRun, run_id)
        assert run is not None
        tenant_id, conversation_id = run.tenant_id, run.conversation_id
    headers = await auth_headers(engine, settings, tenant_id, "worker-user")
    entered, cancelled, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    boundary_ready = asyncio.Event()
    renew = worker_runtime.check_and_renew_attempt

    async def ordered_boundary(*args: object, **kwargs: object) -> None:
        if boundary == "revoke_then_cancel" and entered.is_set():
            await boundary_ready.wait()
        await renew(*args, **kwargs)

    async def late_model(*args: object, **kwargs: object) -> SupportAnswer:
        callback = cast(Callable[[str], Awaitable[None]], kwargs["on_answer"])
        entered.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            # 模拟上游未及时退出，仍试图交付晚到正文和答案。
            cancelled.set()
        await callback("不得复活的迟到正文")
        return SupportAnswer(answer="不得复活的迟到答案")

    monkeypatch.setattr(worker_runtime, "invoke_agent", late_model)
    monkeypatch.setattr(worker_runtime, "check_and_renew_attempt", ordered_boundary)
    task = asyncio.create_task(worker_runtime.execute_agent_run(factory, run_id, settings=settings))
    try:
        await asyncio.wait_for(entered.wait(), timeout=3)
        if boundary.startswith("revoke"):
            async with factory() as session, session.begin():
                grants = (
                    await session.scalars(
                        select(AgentAccessGrant).where(
                            AgentAccessGrant.tenant_id == tenant_id,
                        )
                    )
                ).all()
                assert grants
                for grant in grants:
                    grant.deleted_at = utc_now()
        if boundary == "delete":
            async with factory() as session, session.begin():
                conversation = await session.get(Conversation, conversation_id)
                assert conversation is not None
                await delete_conversation(
                    session,
                    conversation_id=conversation_id,
                    tenant_id=tenant_id,
                    user_id=conversation.user_id,
                )
        elif boundary != "revoke":
            response = await client.post(f"/v1/runs/{run_id}/cancel", headers=headers)
            assert response.status_code == 200 and response.json()["status"] == "cancelling"
        boundary_ready.set()
        assert not await asyncio.wait_for(task, timeout=3)
        assert cancelled.is_set()
    finally:
        boundary_ready.set()
        release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    async with factory() as session:
        run = await session.get(AgentRun, run_id)
        assert run is not None
        assert run.status == (RunStatus.FAILED if boundary == "revoke" else RunStatus.CANCELLED)
        if boundary == "revoke":
            assert run.error_code == "RUNTIME_AUTHORIZATION_DENIED"
        conversation = await session.get(
            Conversation, conversation_id, execution_options={"include_deleted": True}
        )
        assert conversation is not None
        assert conversation.active_run_id is None and conversation.active_attempt_id is None
        assert conversation.checkpoint_id is None
        messages = (
            await session.scalars(
                select(Message).where(
                    Message.conversation_id == conversation_id,
                    Message.role == MessageRole.ASSISTANT,
                )
            )
        ).all()
        assert messages == []
        events = (await session.scalars(select(RunEvent).where(RunEvent.run_id == run_id))).all()
        assert not any("不得复活" in str(event.data) for event in events)
        terminals = [
            event.event_type
            for event in events
            if event.event_type
            in {
                "run.completed",
                "run.cancelled",
                "run.failed",
            }
        ]
        assert terminals == ["run.failed" if boundary == "revoke" else "run.cancelled"]


@dataclass(frozen=True)
class WaitingAction:
    run_id: UUID
    action_id: UUID
    approval_id: UUID | None
    permit: action_runtime.ActionPermit | None


async def seed_waiting_action(
    engine: AsyncEngine,
    settings: Settings,
    *,
    status: str,
    approval_status: str | None = None,
) -> WaitingAction:
    """保存既有等待/派发事实，测试不生成真实批准，也不调用目标写入。"""
    run_id = await seed_run(engine, settings)
    factory = create_session_factory(engine)
    async with factory() as session, session.begin():
        claim = await claim_run_attempt(
            session, run_id, owner_id="finished-model", lease_seconds=60
        )
        assert claim is not None
        run, attempt = claim.run, claim.attempt
        conversation = await session.get(Conversation, run.conversation_id)
        assert conversation is not None
        run.status = (
            RunStatus.WAITING_APPROVAL if status == "waiting_approval" else RunStatus.WAITING_ACTION
        )
        attempt.status, attempt.finished_at = "completed", utc_now()
        release_conversation(OwnedRun(conversation, run, attempt))
        parameters = MockTicketParameters(
            title="隔离取消用例", summary="仅用于验证取消边界", issue_key="cancel-test"
        )
        digest = frozen_parameters_digest(parameters)
        project_id = mock_project_id(run.tenant_id, conversation.user_id)
        action = BusinessAction(
            tenant_id=run.tenant_id,
            proposed_by=conversation.user_id,
            run_id=run.id,
            execution_run_id=run.id,
            agent_version_id=run.agent_version_id,
            execution_agent_version_id=run.agent_version_id,
            tool_id="create_mock_ticket",
            implementation_version=1,
            catalog_version=1,
            execution_catalog_version=1,
            logical_key=uuid4().hex.ljust(64, "0"),
            parameters=parameters.model_dump(mode="json"),
            parameters_digest=digest,
            parameter_schema_version="mock_ticket_v1",
            resource_kind="mock_project",
            resource_id=str(project_id),
            risk_level="R2",
            approval_required=approval_status is not None,
            status=status,
            error_code="ACTION_RESULT_UNKNOWN" if status in {"unknown", "reconciling"} else None,
        )
        session.add(action)
        await session.flush()
        approval_id = None
        if approval_status is not None:
            approver = User(
                tenant_id=run.tenant_id,
                email=f"{uuid4()}@example.test",
                password_hash="not-a-login-account",
                display_name="隔离指定审批人",
                roles=["employee"],
            )
            session.add(approver)
            await session.flush()
            approval = ApprovalRequest(
                tenant_id=run.tenant_id,
                action_id=action.id,
                run_id=run.id,
                applicant_id=conversation.user_id,
                approver_id=approver.id,
                tool_id=action.tool_id,
                implementation_version=1,
                catalog_version=1,
                parameters_digest=digest,
                scope_digest="0" * 64,
                status=approval_status,
                expires_at=utc_now() + timedelta(minutes=5),
            )
            session.add(approval)
            await session.flush()
            approval_id = approval.id
        permit = None
        if status in {"running", "reconciling"}:
            kind = "execute" if status == "running" else "reconcile"
            action_attempt = ActionAttempt(
                tenant_id=run.tenant_id,
                action_id=action.id,
                generation=1,
                kind=kind,
                owner_id="action-owner",
                parameters_digest=digest,
                lease_expires_at=utc_now() + timedelta(seconds=60),
            )
            session.add(action_attempt)
            await session.flush()
            action.generation, action.current_attempt_id = 1, action_attempt.id
            invocation = ToolInvocation(
                tenant_id=run.tenant_id,
                run_id=run.id,
                attempt_id=attempt.id,
                tool_call_id="action:cancel-test",
                tool_id=action.tool_id,
                implementation_version=1,
                catalog_version=1,
                risk_level="R1" if kind == "reconcile" else "R2",
                arguments_digest=digest,
                business_action_id=action.id,
            )
            session.add(invocation)
            await session.flush()
            permit = action_runtime.ActionPermit(
                action.id,
                action_attempt.id,
                invocation.id,
                run.tenant_id,
                run.id,
                conversation.user_id,
                project_id,
                "action-owner",
                1,
                kind,
                action.logical_key,
                parameters,
                "normal",
                conversation.id,
            )
        return WaitingAction(run.id, action.id, approval_id, permit)


@pytest.mark.parametrize("approval_status", ["pending", "approved"])
@pytest.mark.parametrize("cancel_by_delete", [False, True])
async def test_waiting_approval_or_unstarted_action_is_cancelled_once(
    engine: AsyncEngine,
    settings: Settings,
    approval_status: str,
    cancel_by_delete: bool,
) -> None:
    settings = settings.model_copy(update={"business_action_mock_enabled": True})
    state = await seed_waiting_action(
        engine,
        settings,
        status="waiting_approval" if approval_status == "pending" else "queued",
        approval_status=approval_status,
    )
    factory = create_session_factory(engine)
    async with factory() as session, session.begin():
        run = await session.get(AgentRun, state.run_id)
        assert run is not None
        if cancel_by_delete:
            conversation = await session.get(Conversation, run.conversation_id)
            assert conversation is not None
            await delete_conversation(
                session,
                conversation_id=conversation.id,
                tenant_id=run.tenant_id,
                user_id=conversation.user_id,
            )
        else:
            await cancel_run(session, run)
        await cancel_run(session, run)
    async with factory() as session, session.begin():
        run = await session.get(AgentRun, state.run_id)
        action = await session.get(BusinessAction, state.action_id)
        approval = await session.get(ApprovalRequest, state.approval_id)
        assert run is not None and run.status == RunStatus.CANCELLED
        assert action is not None and action.status == "cancelled"
        assert action.error_code == "RUN_CANCELLED_BEFORE_ACTION"
        assert approval is not None and approval.status == "cancelled" and approval.revision == 2
        with pytest.raises(ActionStateConflict):
            await transition_business_action(
                session,
                action_id=action.id,
                tenant_id=run.tenant_id,
                expected_generation=0,
                target="queued",
            )
        assert (
            await action_runtime.claim_action(session, settings=settings, owner_id="late-worker")
            is None
        )
        await settle_waiting_run(session, run=run)
        assert run.status == RunStatus.CANCELLED
        events = (await session.scalars(select(RunEvent).where(RunEvent.run_id == run.id))).all()
        assert sum(event.event_type == "run.cancelled" for event in events) == 1
        assert sum(event.event_type == "action.cancelled" for event in events) == 1
        assert not (await session.scalars(select(ActionAttempt))).all()
        assert not (await session.scalars(select(ActionReceipt))).all()


@pytest.mark.parametrize("proof_status", ["submitted", "verified", "not_executed", "unknown"])
async def test_cancel_preserves_action_receipt_without_resurrecting_run_or_retry(
    engine: AsyncEngine,
    settings: Settings,
    proof_status: str,
) -> None:
    state = await seed_waiting_action(
        engine, settings, status="running" if proof_status == "submitted" else "reconciling"
    )
    assert state.permit is not None
    permit = state.permit
    factory = create_session_factory(engine)
    async with factory() as session, session.begin():
        run = await session.get(AgentRun, state.run_id)
        assert run is not None
        await cancel_run(session, run)
        action = await session.get(BusinessAction, state.action_id)
        assert action is not None and action.status in {"running", "reconciling"}
    proof = MockTargetProof(
        status=proof_status,
        logical_key=permit.logical_key,
        parameters_digest=frozen_parameters_digest(permit.parameters),
        owner_user_id=permit.owner_user_id,
        external_id="mock-ticket-cancel-test"
        if proof_status in {"submitted", "verified"}
        else None,
        execution_fence=permit.generation,
        observed_at=utc_now(),
    )
    await action_runtime.record_action_outcome(
        factory, permit=permit, settings=settings, proof=proof, code=None
    )
    with pytest.raises(action_runtime.ActionLeaseLost):
        await action_runtime.record_action_outcome(
            factory, permit=permit, settings=settings, proof=proof, code=None
        )
    async with factory() as session:
        run = await session.get(AgentRun, state.run_id)
        action = await session.get(BusinessAction, state.action_id)
        assert run is not None and run.status == RunStatus.CANCELLED
        assert action is not None
        assert (
            action.status
            == {
                "submitted": "unknown",
                "verified": "succeeded",
                "not_executed": "failed",
                "unknown": "unknown",
            }[proof_status]
        )
        receipts = (
            await session.scalars(select(ActionReceipt).where(ActionReceipt.action_id == action.id))
        ).all()
        assert len(receipts) == 1 and receipts[0].status == proof_status
        events = (await session.scalars(select(RunEvent).where(RunEvent.run_id == run.id))).all()
        assert sum(event.event_type == "run.cancelled" for event in events) == 1
        assert not any(event.event_type == "run.completed" for event in events)
        conversation = await session.get(Conversation, run.conversation_id)
        assert conversation is not None and conversation.active_run_id is None
        attempt = await session.get(RunAttempt, run.current_attempt_id)
        assert attempt is not None and attempt.status == "completed"


async def test_confirmed_not_executed_remains_retryable_for_active_waiting_run(
    engine: AsyncEngine,
    settings: Settings,
) -> None:
    state = await seed_waiting_action(engine, settings, status="reconciling")
    assert state.permit is not None
    permit = state.permit
    proof = MockTargetProof(
        status="not_executed",
        logical_key=permit.logical_key,
        parameters_digest=frozen_parameters_digest(permit.parameters),
        owner_user_id=permit.owner_user_id,
        execution_fence=permit.generation,
        observed_at=utc_now(),
    )
    factory = create_session_factory(engine)
    await action_runtime.record_action_outcome(
        factory,
        permit=permit,
        settings=settings,
        proof=proof,
        code=None,
    )
    async with factory() as session:
        run = await session.get(AgentRun, state.run_id)
        action = await session.get(BusinessAction, state.action_id)
        assert run is not None and run.status == RunStatus.WAITING_ACTION
        assert action is not None and action.status == "queued"


async def test_cancel_unknown_action_preserves_unconfirmed_side_effect(
    engine: AsyncEngine,
    settings: Settings,
) -> None:
    state = await seed_waiting_action(engine, settings, status="unknown")
    async with create_session_factory(engine)() as session, session.begin():
        run = await session.get(AgentRun, state.run_id)
        assert run is not None
        await cancel_run(session, run)
        await settle_waiting_run(session, run=run)
        action = await session.get(BusinessAction, state.action_id)
        assert run.status == RunStatus.CANCELLED
        assert action is not None and action.status == "unknown"
        assert action.error_code == "ACTION_RESULT_UNKNOWN" and action.finished_at is None
