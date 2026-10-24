"""隔离 R4 的改参重审、重放/并发决定、失效边界与超时对账。"""

import asyncio
import os
from datetime import timedelta
from uuid import UUID

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.orm import Session
from supportops_core import action_runtime
from supportops_core.approval_services import maintain_waiting_approval
from supportops_core.enums import AuditAction, GrantSubjectType, RunStatus
from supportops_core.models import (
    AgentAccessGrant,
    AgentAuditEvent,
    AgentRun,
    AgentVersion,
    ApprovalConsumption,
    ApprovalDecision,
    ApprovalRequest,
    BusinessAction,
    MockResourceGrant,
    ModelCredentialRevision,
    ModelEndpointVersion,
    RunEvent,
    ToolCatalogEntry,
    User,
    utc_now,
)

from tests.postgres.test_action_reconciliation import (
    claim,
    completed_run,
    counts,
    due,
    expire_and_recover,
    read_action,
    remote_submit,
    step,
)
from tests.postgres.test_durable_approval import (
    ApprovalCase,
    decide,
    journal,
    no_side_effects,
    waiting,
)
from tests.postgres.test_durable_approval import action_case as action_case
from tests.postgres.test_durable_approval import approval_case as approval_case
from tests.postgres.test_durable_approval import engine as engine
from tests.postgres.test_durable_approval import knowledge_case as knowledge_case
from tests.postgres.test_durable_approval import settings as settings

pytestmark = pytest.mark.skipif(
    os.getenv("SUPPORTOPS_RUN_POSTGRES_TESTS") != "1", reason="需显式启用隔离 PostgreSQL 测试库"
)


async def start(case: ApprovalCase, mode="normal"):
    if mode == "normal":
        return await waiting(case)
    run_id = await completed_run(case.action, f"需要支持 mock-s01-rpm [action:{mode}]")
    async with case.factory() as session:
        approval = await session.scalar(
            select(ApprovalRequest).where(ApprovalRequest.run_id == run_id)
        )
        assert approval is not None
        action = await session.get(BusinessAction, approval.action_id)
        assert action.risk_level == "R4" and action.status == "waiting_approval"
        return run_id, approval, action


async def amendment(case: ApprovalCase, action: BusinessAction, **changes):
    return await case.knowledge.client.post(
        f"/v1/actions/{action.id}/amend",
        headers=case.knowledge.reader_headers,
        json={
            "context_run_id": str(action.execution_run_id or action.run_id),
            "approver_id": str(case.reviewer_id),
            "expected_generation": action.generation,
            "expected_parameters_digest": action.parameters_digest,
            "expected_catalog_version": action.execution_catalog_version or action.catalog_version,
            "title": "重新核对后的隔离标题",
            "summary": "只含最小支持摘要的新意图",
            **changes,
        },
    )


async def child_approval(case: ApprovalCase, action_id: UUID):
    async with case.factory() as session:
        return await session.scalar(
            select(ApprovalRequest).where(ApprovalRequest.action_id == action_id)
        )


async def consumption_count(case: ApprovalCase, action_id: UUID):
    async with case.factory() as session:
        return await session.scalar(
            select(func.count())
            .select_from(ApprovalConsumption)
            .where(ApprovalConsumption.action_id == action_id)
        )


@pytest.mark.parametrize("approved", [False, True], ids=["pending", "approved"])
async def test_amendment_keeps_old_facts_and_requires_new_independent_decision(
    approval_case, approved
):
    case = approval_case
    run_id, old, action = await start(case)
    before = await journal(case, run_id)
    if approved:
        assert (await decide(case, old)).status_code == 200
    response = await amendment(case, action)
    assert response.status_code == 201, response.text
    replacement = response.json()
    child = await child_approval(case, UUID(replacement["id"]))
    assert replacement["supersedes_action_id"] == str(action.id)
    assert replacement["risk_level"] == "R4" and replacement["approval_required"]
    assert replacement["parameters_digest"] != action.parameters_digest
    assert replacement["parameters"]["issue_key"] == action.parameters["issue_key"]
    assert replacement["status"] == "waiting_approval" and child.status == "pending"
    async with case.factory() as session:
        assert (await session.get(ApprovalRequest, old.id)).status == "invalidated"
        original = await session.get(BusinessAction, action.id)
        assert original.status == "cancelled" and original.parameters == action.parameters
        decisions = (
            await session.scalars(
                select(ApprovalDecision).where(ApprovalDecision.request_id == old.id)
            )
        ).all()
        assert len(decisions) == int(approved)
    replay = await amendment(case, action)
    assert replay.status_code == 201 and replay.json()["id"] == replacement["id"]
    stale = await decide(case, old)
    assert stale.status_code == (200 if approved else 409)
    async with case.factory() as session, session.begin():
        assert (
            await action_runtime.claim_action(
                session, settings=case.knowledge.settings, owner_id="before-new-approval"
            )
            is None
        )
    await no_side_effects(case, action.id)
    assert (await decide(case, child)).status_code == 200
    await step(case.action, child.action_id)
    await step(case.action, child.action_id)
    attempts, receipts, tickets = await counts(case.action, child.action_id)
    assert [item.kind for item in attempts] == ["execute", "verify"]
    assert receipts[-1].status == "verified" and len(tickets) == 1
    assert tickets[0].title == replacement["parameters"]["title"]
    assert await counts(case.action, action.id) == ([], [], [])
    after = await journal(case, run_id)
    assert after[0].status == RunStatus.COMPLETED
    assert (after[1].checkpoint_id, [item.id for item in after[3]]) == (
        before[1].checkpoint_id,
        [item.id for item in before[3]],
    )
    assert len(case.model_calls) == 2


@pytest.mark.parametrize("stage", ["claimed", "unknown", "succeeded"])
async def test_consumed_approval_cannot_amend_into_a_new_execution_key(approval_case, stage):
    case = approval_case
    _, approval, action = await start(case)
    assert (await decide(case, approval)).status_code == 200
    permit = await claim(case.action, action.id)
    if stage != "claimed":
        await action_runtime.execute_action(
            case.factory, permit=permit, settings=case.knowledge.settings
        )
    if stage == "succeeded":
        await step(case.action, action.id)
    current = await read_action(case.action, action.id)
    response = await amendment(case, current)
    assert response.status_code == 409, response.text
    async with case.factory() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(BusinessAction)
                .where(BusinessAction.tenant_id == case.knowledge.tenant_id)
            )
            == 1
        )
    assert await consumption_count(case, action.id) == 1


@pytest.mark.parametrize("phase", ["queued", "consumed", "verified"])
async def test_decision_replay_returns_existing_fact_without_new_intent(approval_case, phase):
    case = approval_case
    run_id, approval, action = await start(case)
    assert (await decide(case, approval)).status_code == 200
    if phase != "queued":
        await step(case.action, action.id)
    if phase == "verified":
        await step(case.action, action.id)
    before = await counts(case.action, action.id)
    for _ in range(2):
        response = await decide(case, approval)
        assert response.status_code == 200
        assert response.json()["status"] == ("approved" if phase == "queued" else "consumed")
    conflict = await decide(case, approval, "rejected")
    assert conflict.status_code == 409
    after = await counts(case.action, action.id)
    assert [[item.id for item in rows] for rows in after] == [
        [item.id for item in rows] for rows in before
    ]
    async with case.factory() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(ApprovalDecision)
                .where(ApprovalDecision.request_id == approval.id)
            )
            == 1
        )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(RunEvent)
                .where(RunEvent.run_id == run_id, RunEvent.event_type == "approval.decided")
            )
            == 1
        )


@pytest.mark.parametrize("other", ["approved", "rejected"])
async def test_parallel_decisions_record_one_fact_and_at_most_one_consumption(approval_case, other):
    case = approval_case
    _, approval, action = await start(case)
    ready = asyncio.Event()

    async def send(decision):
        await ready.wait()
        return await decide(case, approval, decision)

    tasks = [asyncio.create_task(send(value)) for value in ("approved", other)]
    ready.set()
    async with asyncio.timeout(10):
        responses = await asyncio.gather(*tasks)
    assert sorted(item.status_code for item in responses) == (
        [200, 200] if other == "approved" else [200, 409]
    )
    async with case.factory() as session:
        decisions = (
            await session.scalars(
                select(ApprovalDecision).where(ApprovalDecision.request_id == approval.id)
            )
        ).all()
        assert len(decisions) == 1
        winner = decisions[0].decision
    if winner == "approved":
        await due(case.action, action.id)

        async def take(owner):
            async with case.factory() as session, session.begin():
                return await action_runtime.claim_action(
                    session, settings=case.knowledge.settings, owner_id=owner
                )

        async with asyncio.timeout(10):
            permits = await asyncio.gather(take("consumer-a"), take("consumer-b"))
        valid = [item for item in permits if item]
        assert len(valid) == 1 and await consumption_count(case, action.id) == 1
        await action_runtime.execute_action(
            case.factory, permit=valid[0], settings=case.knowledge.settings
        )
        await step(case.action, action.id)
        assert len((await counts(case.action, action.id))[2]) == 1
    else:
        await no_side_effects(case, action.id)


async def invalidate(case: ApprovalCase, run_id: UUID, approval_id: UUID, reason: str):
    async with case.factory() as session, session.begin():
        run = await session.get(AgentRun, run_id)
        if reason == "expires":
            (await session.get(ApprovalRequest, approval_id)).expires_at = utc_now() - timedelta(
                seconds=1
            )
        elif reason in {"applicant", "reviewer"}:
            user = await session.get(
                User, case.knowledge.reader_id if reason == "applicant" else case.reviewer_id
            )
            user.status = "inactive"
        elif reason == "agent-grant":
            row = await session.scalar(
                select(AgentAccessGrant).where(
                    AgentAccessGrant.agent_id == case.action.agent_id,
                    AgentAccessGrant.subject_type == GrantSubjectType.USER,
                    AgentAccessGrant.subject_id == str(case.knowledge.reader_id),
                )
            )
            row.deleted_at = utc_now()
        elif reason == "staff-grant":
            row = await session.scalar(
                select(MockResourceGrant).where(
                    MockResourceGrant.tenant_id == case.knowledge.tenant_id,
                    MockResourceGrant.owner_user_id == case.knowledge.reader_id,
                    MockResourceGrant.user_id == case.reviewer_id,
                    MockResourceGrant.permission == "approve",
                )
            )
            row.is_enabled = False
        elif reason in {"tool", "catalog"}:
            row = await session.scalar(
                select(ToolCatalogEntry).where(
                    ToolCatalogEntry.tenant_id == case.knowledge.tenant_id,
                    ToolCatalogEntry.tool_id == "create_mock_ticket",
                )
            )
            if reason == "tool":
                row.is_revoked = True
            else:
                row.version += 1
        elif reason == "credential":
            model = await session.get(ModelEndpointVersion, run.model_endpoint_version_id)
            row = await session.scalar(
                select(ModelCredentialRevision).where(
                    ModelCredentialRevision.credential_id == model.credential_id,
                    ModelCredentialRevision.revision == run.credential_revision,
                )
            )
            row.revoked_at, row.revoked_by, row.revocation_reason = (
                utc_now(),
                case.knowledge.owner_id,
                "隔离撤销",
            )
        elif reason == "release":
            row = await session.get(AgentVersion, run.agent_version_id)
            row.withdrawn_at, row.withdrawn_by = utc_now(), case.knowledge.owner_id
        else:
            raise AssertionError(reason)


INVALIDATIONS = [
    "expires",
    "applicant",
    "reviewer",
    "agent-grant",
    "staff-grant",
    "tool",
    "catalog",
    "credential",
    "release",
]


@pytest.mark.parametrize("reason", INVALIDATIONS)
async def test_current_scope_is_rechecked_before_consuming_approved_action(approval_case, reason):
    case = approval_case
    run_id, approval, action = await start(case)
    assert (await decide(case, approval)).status_code == 200
    await invalidate(case, run_id, approval.id, reason)
    async with case.factory() as session, session.begin():
        assert (
            await action_runtime.claim_action(
                session, settings=case.knowledge.settings, owner_id="invalid-scope"
            )
            is None
        )
    assert (await read_action(case.action, action.id)).status in {"cancelled", "failed"}
    await no_side_effects(case, action.id)


@pytest.mark.parametrize("reason", INVALIDATIONS)
async def test_current_scope_is_rechecked_after_consumption_before_target(approval_case, reason):
    case = approval_case
    run_id, approval, action = await start(case)
    assert (await decide(case, approval)).status_code == 200
    permit = await claim(case.action, action.id)
    await invalidate(case, run_id, approval.id, reason)
    await action_runtime.execute_action(
        case.factory, permit=permit, settings=case.knowledge.settings
    )
    attempts, receipts, tickets = await counts(case.action, action.id)
    assert len(attempts) == 1 and receipts == [] and tickets == []
    assert (await read_action(case.action, action.id)).status == "unknown"
    assert await consumption_count(case, action.id) == 1


@pytest.mark.parametrize("reason", ["expires", "staff-grant", "tool"])
async def test_maintenance_terminalizes_approved_scope_without_reviving_it(approval_case, reason):
    case = approval_case
    run_id, approval, action = await start(case)
    assert (await decide(case, approval)).status_code == 200
    await invalidate(case, run_id, approval.id, reason)
    async with case.factory() as session, session.begin():
        (await session.get(ApprovalRequest, approval.id)).next_check_at = utc_now() - timedelta(
            days=2
        )
    async with case.factory() as session, session.begin():
        assert await maintain_waiting_approval(session, settings=case.knowledge.settings) == 1
    async with case.factory() as session:
        record = await session.get(ApprovalRequest, approval.id)
        assert record.status == ("expired" if reason == "expires" else "invalidated")
        assert (await session.get(BusinessAction, action.id)).status == "cancelled"
    await no_side_effects(case, action.id)


@pytest.mark.parametrize(
    "mode,expected",
    [
        ("lost", "succeeded"),
        ("timeout", "failed"),
        ("not_executed", "failed"),
        ("unknown", "needs_attention"),
        ("conflict", "needs_attention"),
    ],
)
async def test_consumed_r4_faults_only_reconcile_never_automatically_execute_again(
    approval_case, mode, expected
):
    case = approval_case
    _, approval, action = await start(case, mode)
    assert (await decide(case, approval)).status_code == 200
    await step(case.action, action.id)
    assert (await read_action(case.action, action.id)).status == "unknown"
    for _ in range(3):
        if (await read_action(case.action, action.id)).status != "unknown":
            break
        await step(case.action, action.id)
    final = await read_action(case.action, action.id)
    assert final.status == expected
    attempts, receipts, tickets = await counts(case.action, action.id)
    assert sum(item.kind == "execute" for item in attempts) == 1
    assert await consumption_count(case, action.id) == 1
    assert len(tickets) == int(mode in {"lost", "conflict"})
    if expected == "succeeded":
        assert receipts[-1].status == "verified" and final.external_id == tickets[0].external_id
    else:
        assert final.external_id is None


@pytest.mark.parametrize("committed", [False, True], ids=["before-target", "after-target-commit"])
async def test_actual_target_deadline_keeps_unknown_until_independent_proof(
    approval_case, monkeypatch, committed
):
    case = approval_case
    _, approval, action = await start(case)
    assert (await decide(case, approval)).status_code == 200
    permit = await claim(case.action, action.id)
    submit = action_runtime.submit_mock_ticket
    entered = asyncio.Event()

    async def delayed(*args, **kwargs):
        if committed:
            await submit(*args, **kwargs)
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(action_runtime, "submit_mock_ticket", delayed)
    settings = case.knowledge.settings.model_copy(update={"action_target_timeout_seconds": 0.1})
    await action_runtime.execute_action(case.factory, permit=permit, settings=settings)
    assert entered.is_set()
    current = await read_action(case.action, action.id)
    assert current.status == "unknown" and current.error_code == "ACTION_DEPENDENCY_TIMEOUT"
    assert len((await counts(case.action, action.id))[2]) == int(committed)
    await step(case.action, action.id)
    final = await read_action(case.action, action.id)
    assert final.status == ("succeeded" if committed else "failed")
    attempts, receipts, tickets = await counts(case.action, action.id)
    assert [item.kind for item in attempts] == ["execute", "reconcile"]
    assert receipts[-1].status == ("verified" if committed else "not_executed")
    assert len(tickets) == int(committed) and await consumption_count(case, action.id) == 1


@pytest.mark.parametrize("committed", [False, True])
async def test_consumed_lease_recovery_never_issues_another_execute_permit(
    approval_case, committed
):
    case = approval_case
    _, approval, action = await start(case)
    assert (await decide(case, approval)).status_code == 200
    permit = await claim(case.action, action.id)
    if committed:
        await remote_submit(case.action, permit)
    await expire_and_recover(case.action, action.id)
    recovery = await step(case.action, action.id)
    assert recovery.kind == "reconcile"
    assert (await read_action(case.action, action.id)).status == (
        "succeeded" if committed else "failed"
    )
    assert await consumption_count(case, action.id) == 1


async def test_consumption_audit_failure_rolls_back_permit_and_can_retry_once(approval_case):
    case = approval_case
    _, approval, action = await start(case)
    assert (await decide(case, approval)).status_code == 200

    def fail_audit(session, *_):
        if any(
            isinstance(item, AgentAuditEvent) and item.action == AuditAction.APPROVAL_CONSUMED
            for item in session.new
        ):
            raise RuntimeError("isolated consumption audit rollback")

    event.listen(Session, "before_flush", fail_audit)
    try:
        with pytest.raises(RuntimeError, match="consumption audit rollback"):
            await claim(case.action, action.id)
    finally:
        event.remove(Session, "before_flush", fail_audit)
    await no_side_effects(case, action.id)
    async with case.factory() as session:
        assert (await session.get(ApprovalRequest, approval.id)).status == "approved"
    await step(case.action, action.id)
    await step(case.action, action.id)
    assert await consumption_count(case, action.id) == 1


@pytest.mark.parametrize("approved", [False, True])
async def test_cancelled_wait_cannot_be_revived_by_an_old_approval(approval_case, approved):
    case = approval_case
    run_id, approval, action = await start(case)
    if approved:
        assert (await decide(case, approval)).status_code == 200
    cancelled = await case.knowledge.client.post(
        f"/v1/runs/{run_id}/cancel", headers=case.knowledge.reader_headers
    )
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"
    response = await decide(case, approval)
    assert response.status_code == (200 if approved else 409), response.text
    if approved:
        assert response.json()["status"] == "cancelled"
    async with case.factory() as session, session.begin():
        assert (
            await action_runtime.claim_action(
                session, settings=case.knowledge.settings, owner_id="after-cancel"
            )
            is None
        )
    await no_side_effects(case, action.id)
