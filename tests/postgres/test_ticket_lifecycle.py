"""真实 API/PG 的隔离工单、人工接管与升级包验收；不连接真实工单系统。"""

import asyncio
import os
from dataclasses import dataclass
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import pytest
from sqlalchemy import event, func, select, text
from sqlalchemy.orm import Session
from supportops_core import action_runtime, mock_action_connector, ticket_command_services
from supportops_core.approval_services import live_identity
from supportops_core.enums import AuditAction, KnowledgeAclSubjectType
from supportops_core.models import (
    AgentAccessGrant,
    AgentAuditEvent,
    AgentRun,
    MockResourceGrant,
    MockTicketCommand,
    TicketEscalationPackage,
    utc_now,
)
from supportops_core.ticket_services import query_mock_support_ticket

from tests.conftest import auth_headers
from tests.postgres.test_action_reconciliation import (
    ActionCase,
    claim,
    completed_run,
    counts,
    due,
    proposal,
    queue,
    read_action,
    remote_submit,
    step,
)
from tests.postgres.test_action_reconciliation import action_case as action_case
from tests.postgres.test_action_reconciliation import knowledge_case as knowledge_case
from tests.postgres.test_action_reconciliation import settings as settings
from tests.postgres.test_diagnostic_workflow import RULES, execute, prepare
from tests.postgres.test_knowledge_access import engine as engine

pytestmark = pytest.mark.skipif(
    os.getenv("SUPPORTOPS_RUN_POSTGRES_TESTS") != "1", reason="需显式启用隔离 PostgreSQL 测试库"
)


@dataclass
class TicketCase:
    action: ActionCase
    run_id: UUID
    action_id: UUID
    external_id: str
    package_id: str
    staff_id: UUID
    staff_headers: dict[str, str]
    other_id: UUID
    other_headers: dict[str, str]
    conversation_id: UUID

    @property
    def knowledge(self):
        return self.action.knowledge

    @property
    def factory(self):
        return self.action.factory

    @property
    def path(self):
        return f"/v1/mock-tickets/{self.external_id}"


async def grant(case: TicketCase, *, user_id=None, permission="ticket_handle", enabled=True):
    user_id = user_id or case.staff_id
    async with case.factory() as session:
        existing = await session.scalar(
            select(MockResourceGrant).where(
                MockResourceGrant.tenant_id == case.knowledge.tenant_id,
                MockResourceGrant.owner_user_id == case.knowledge.reader_id,
                MockResourceGrant.user_id == user_id,
                MockResourceGrant.permission == permission,
            )
        )
    response = await case.knowledge.client.post(
        "/v1/admin/mock-resource-grants",
        headers=case.knowledge.owner_headers,
        json={
            "owner_user_id": str(case.knowledge.reader_id),
            "user_id": str(user_id),
            "permission": permission,
            "is_enabled": enabled,
            "expected_revision": existing.revision if existing else 0,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


async def ticket(case: TicketCase, headers=None):
    response = await case.knowledge.client.get(case.path, headers=headers or case.staff_headers)
    assert response.status_code == 200, response.text
    return response.json()


async def command(case: TicketCase, kind="claim", *, body="", version=None, key=None, headers=None):
    headers = headers or case.staff_headers
    if version is None:
        version = (await ticket(case, headers))["version"]
    return await case.knowledge.client.post(
        f"{case.path}/commands",
        headers=headers,
        json={
            "kind": kind,
            "body": body,
            "expected_version": version,
            "idempotency_key": key or str(uuid4()),
        },
    )


async def build_case(action: ActionCase, run_id: UUID):
    payload = await proposal(action, run_id)
    action_id = UUID(payload["id"])
    await queue(action, payload)
    await step(action, action_id)
    await step(action, action_id)
    row = await read_action(action, action_id)
    assert row.status == "succeeded" and row.external_id
    knowledge = action.knowledge
    staff = await auth_headers(
        knowledge.engine,
        knowledge.settings,
        knowledge.tenant_id,
        "ticket-staff",
        roles=("employee",),
    )
    other = await auth_headers(
        knowledge.engine,
        knowledge.settings,
        knowledge.tenant_id,
        "ticket-other",
        roles=("employee",),
    )
    packages = await knowledge.client.get(
        f"/v1/actions/{action_id}/escalations", headers=knowledge.reader_headers
    )
    assert packages.status_code == 200 and packages.json()["total"] == 1
    async with action.factory() as session:
        run = await session.get(AgentRun, run_id)
    value = TicketCase(
        action,
        run_id,
        action_id,
        row.external_id,
        packages.json()["items"][0]["id"],
        uuid5(NAMESPACE_URL, f"{knowledge.tenant_id}:ticket-staff"),
        staff,
        uuid5(NAMESPACE_URL, f"{knowledge.tenant_id}:ticket-other"),
        other,
        run.conversation_id,
    )
    await grant(value)
    return value


@pytest.fixture
async def ticket_case(action_case):
    return await build_case(action_case, await completed_run(action_case))


@pytest.fixture
async def context_case(knowledge_case, request):
    diagnostic = await prepare(
        knowledge_case,
        tool_limit=20,
        extra_tools=("create_mock_ticket",),
        rules=RULES if getattr(request, "param", "confirmed") == "confirmed" else None,
    )
    run, report, _, _ = await execute(diagnostic, "请排查 429 mock-s01-rpm")
    value = await build_case(ActionCase(knowledge_case, diagnostic.agent_id), run.id)
    await grant(value, permission="support_context_read")
    await knowledge_case.acl(
        [
            (KnowledgeAclSubjectType.USER, str(knowledge_case.reader_id)),
            (KnowledgeAclSubjectType.USER, str(value.staff_id)),
        ]
    )
    return value, report["diagnostic"]


@pytest.mark.parametrize("actor", ["cross", "admin", "stranger"])
async def test_unrelated_identity_sees_no_ticket_metadata_body_commands_or_packages(
    ticket_case, actor
):
    case = ticket_case
    headers = case.knowledge.owner_headers if actor == "admin" else case.other_headers
    if actor == "cross":
        headers = await auth_headers(
            case.knowledge.engine,
            case.knowledge.settings,
            uuid4(),
            "foreign-admin",
            roles=("platform_admin",),
        )
    for suffix in ["", "/commands", "/escalations", f"/escalations/{case.package_id}"]:
        response = await case.knowledge.client.get(case.path + suffix, headers=headers)
        assert response.status_code == 404, response.text
        assert response.json()["error"]["code"] == "NOT_FOUND"
        assert case.external_id not in response.text
    listed = await case.knowledge.client.get("/v1/mock-tickets", headers=headers)
    assert listed.status_code == 200 and listed.json()["total"] == 0
    assert (await command(case, version=1, headers=headers)).status_code == 404
    assert (await ticket(case))["version"] == 1


async def test_owner_can_read_but_cannot_claim_and_ticket_grant_does_not_grant_context(ticket_case):
    case = ticket_case
    own = await ticket(case, case.knowledge.reader_headers)
    assert not own["can_claim"] and not own["can_update"] and own["can_read_context"]
    assert (
        await command(case, headers=case.knowledge.reader_headers, version=1)
    ).status_code == 403
    staff = await ticket(case)
    assert staff["can_claim"] and not staff["can_read_context"]
    for suffix in ["/escalations", f"/escalations/{case.package_id}"]:
        assert (
            await case.knowledge.client.get(case.path + suffix, headers=case.staff_headers)
        ).status_code == 403
    await grant(case, permission="support_context_read")
    detail = await case.knowledge.client.get(
        f"{case.path}/escalations/{case.package_id}", headers=case.staff_headers
    )
    assert detail.status_code == 200 and detail.headers["cache-control"] == "no-store"


async def test_claim_reply_private_note_release_and_close_use_current_business_state(ticket_case):
    case = ticket_case
    assert (await command(case)).status_code == 200
    assert (await ticket(case))["takeover_active"]
    note = await command(case, "note", body="仅客服可读的内部备注")
    reply = await command(case, "reply", body="申请者可读的回复")
    assert note.status_code == reply.status_code == 200
    for headers, private in [(case.knowledge.reader_headers, False), (case.staff_headers, True)]:
        history = await case.knowledge.client.get(f"{case.path}/commands", headers=headers)
        assert ("仅客服可读的内部备注" in history.text) is private
        assert "申请者可读的回复" in history.text
        assert history.json()["total"] == (3 if private else 2)
    assert (await command(case, "escalate")).json()["ticket_status"] == "escalated"
    assert (await command(case, "release")).json()["ticket_status"] == "open"
    current = await ticket(case)
    assert current["can_claim"] and not current["takeover_active"]
    assert (await command(case)).status_code == 200
    assert (await command(case, "close")).json()["ticket_status"] == "closed"
    current = await ticket(case)
    assert not current["takeover_active"] and not current["can_claim"] and not current["can_update"]
    assert (await command(case, "reply", body="关闭后回复")).status_code == 409
    takeover = await case.knowledge.client.get(
        f"/v1/conversations/{case.conversation_id}/support-takeover",
        headers=case.knowledge.reader_headers,
    )
    assert takeover.status_code == 200 and not takeover.json()["active"]


@pytest.mark.parametrize("operation", ["note", "reply", "close", "escalate", "release"])
async def test_non_handler_cannot_modify_a_claimed_ticket(ticket_case, operation):
    case = ticket_case
    await grant(case, user_id=case.other_id)
    assert (await command(case)).status_code == 200
    response = await command(case, operation, body="无权处理", headers=case.other_headers)
    assert response.status_code == 403
    assert (await ticket(case))["version"] == 2


async def test_same_command_replays_original_receipt_without_regressing_current_state(ticket_case):
    case = ticket_case
    key = str(uuid4())
    first = await command(case, key=key, version=1)
    assert first.status_code == 200
    assert (await command(case, "reply", body="已处理")).status_code == 200
    replay = await command(case, key=key, version=1)
    assert replay.status_code == 200 and replay.json() == first.json()
    assert (await ticket(case))["version"] == 3
    mismatch = await command(case, key=key, version=1, body="修改原参数")
    assert mismatch.status_code == 409
    await grant(case, user_id=case.other_id)
    assert (await command(case, key=key, version=1, headers=case.other_headers)).status_code == 409
    async with case.factory() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(MockTicketCommand)
                .where(MockTicketCommand.tenant_id == case.knowledge.tenant_id)
            )
            == 2
        )


async def test_parallel_same_command_and_competing_claims_have_one_winner(ticket_case):
    case = ticket_case
    key = str(uuid4())
    async with asyncio.timeout(10):
        results = await asyncio.gather(
            command(case, key=key, version=1), command(case, key=key, version=1)
        )
    assert [response.status_code for response in results] == [200, 200]
    assert results[0].json()["id"] == results[1].json()["id"]
    assert (await command(case, "release")).status_code == 200
    await grant(case, user_id=case.other_id)
    async with asyncio.timeout(10):
        results = await asyncio.gather(
            command(case, version=3), command(case, version=3, headers=case.other_headers)
        )
    assert sorted(response.status_code for response in results) == [200, 409]
    assert (await ticket(case))["version"] == 4


async def test_revoked_handler_loses_reads_and_old_receipt_but_another_staff_can_take_over(
    ticket_case,
):
    case = ticket_case
    key = str(uuid4())
    assert (await command(case, key=key, version=1)).status_code == 200
    await grant(case, enabled=False)
    assert (
        await case.knowledge.client.get(case.path, headers=case.staff_headers)
    ).status_code == 404
    assert (await command(case, key=key, version=1)).status_code == 404
    await grant(case, user_id=case.other_id)
    assert (await ticket(case, case.other_headers))["can_claim"]
    assert (await command(case, headers=case.other_headers)).status_code == 200
    assert (await ticket(case, case.other_headers))["handler_name"] == "ticket-other"


async def test_command_audit_failure_rolls_back_target_and_takeover_atomically(ticket_case):
    case = ticket_case

    def reject(session, *_):
        if any(
            isinstance(row, AgentAuditEvent) and row.action == AuditAction.MOCK_TICKET_COMMAND
            for row in session.new
        ):
            raise RuntimeError("isolated-command-audit-failure")

    event.listen(Session, "before_flush", reject)
    try:
        with pytest.raises(RuntimeError, match="isolated-command-audit-failure"):
            await command(case)
    finally:
        event.remove(Session, "before_flush", reject)
    current = await ticket(case)
    assert current["version"] == 1 and not current["takeover_active"]
    async with case.factory() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(MockTicketCommand)
                .where(MockTicketCommand.tenant_id == case.knowledge.tenant_id)
            )
            == 0
        )
    assert (await command(case)).status_code == 200


async def second_action(case: TicketCase):
    accepted = await case.knowledge.client.post(
        f"/v1/conversations/{case.conversation_id}/messages",
        headers={**case.knowledge.reader_headers, "Idempotency-Key": str(uuid4())},
        json={"content": "继续处理 mock-s02-tpm"},
    )
    assert accepted.status_code == 202, accepted.text
    run_id = UUID(accepted.json()["run_id"])
    from supportops_core import worker_runtime

    assert await worker_runtime.execute_agent_run(
        case.factory, run_id, settings=case.knowledge.settings
    )
    payload = await proposal(case.action, run_id)
    assert UUID(payload["id"]) != case.action_id
    return run_id, payload


@pytest.mark.parametrize("stage", ["queue", "claim", "outbound", "target"])
async def test_human_takeover_freezes_other_issue_writes_in_same_conversation(ticket_case, stage):
    case = ticket_case
    _, payload = await second_action(case)
    action_id = UUID(payload["id"])
    if stage != "queue":
        await queue(case.action, payload)
    permit = await claim(case.action, action_id) if stage in {"outbound", "target"} else None
    assert (await command(case)).status_code == 200
    if stage == "queue":
        response = await case.knowledge.client.post(
            f"/v1/actions/{action_id}/execute",
            headers=case.knowledge.reader_headers,
            json={
                "expected_generation": payload["generation"],
                "expected_parameters_digest": payload["parameters_digest"],
                "expected_catalog_version": payload["effective_catalog_version"],
            },
        )
        assert response.status_code == 409
    elif stage == "claim":
        async with case.factory() as session, session.begin():
            assert (
                await action_runtime.claim_action(
                    session, settings=case.knowledge.settings, owner_id="takeover-blocked"
                )
                is None
            )
    elif stage == "outbound":
        await action_runtime.execute_action(
            case.factory, permit=permit, settings=case.knowledge.settings
        )
        assert (await read_action(case.action, action_id)).status != "succeeded"
    else:
        with pytest.raises(mock_action_connector.MockTargetError, match="MOCK_CASE_HUMAN_TAKEOVER"):
            await remote_submit(case.action, permit)
    assert (await counts(case.action, action_id))[2] == []
    takeover = await case.knowledge.client.get(
        f"/v1/conversations/{case.conversation_id}/support-takeover",
        headers=case.knowledge.reader_headers,
    )
    assert takeover.json()["active"] and takeover.json()["active_case_count"] == 1


async def test_target_commit_and_human_claim_serialize_before_freezing_followup_writes(
    ticket_case, monkeypatch
):
    case = ticket_case
    _, payload = await second_action(case)
    action_id = UUID(payload["id"])
    await queue(case.action, payload)
    permit = await claim(case.action, action_id)
    entered, release, claim_started = asyncio.Event(), asyncio.Event(), asyncio.Event()
    original = mock_action_connector.assert_mock_write_allowed
    read_ticket = ticket_command_services.read_ticket_row

    async def pause(*args, **kwargs):
        await original(*args, **kwargs)
        entered.set()
        await release.wait()

    async def observe(*args, **kwargs):
        result = await read_ticket(*args, **kwargs)
        claim_started.set()
        return result

    monkeypatch.setattr(mock_action_connector, "assert_mock_write_allowed", pause)
    monkeypatch.setattr(ticket_command_services, "read_ticket_row", observe)
    target = asyncio.create_task(remote_submit(case.action, permit))
    human = None
    try:
        async with asyncio.timeout(10):
            await entered.wait()
            human = asyncio.create_task(command(case, version=1))
            await claim_started.wait()
            assert not human.done()
            release.set()
            proof, receipt = await asyncio.gather(target, human)
        assert proof.status == "submitted" and receipt.status_code == 200
        monkeypatch.setattr(mock_action_connector, "assert_mock_write_allowed", original)
        with pytest.raises(mock_action_connector.MockTargetError, match="MOCK_CASE_HUMAN_TAKEOVER"):
            await remote_submit(case.action, permit)
        assert len((await counts(case.action, action_id))[2]) == 1
    finally:
        release.set()
        for task in [target, human]:
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(
            *[task for task in [target, human] if task is not None], return_exceptions=True
        )


@pytest.mark.parametrize("context_case", ["confirmed", "unconfirmed"], indirect=True)
async def test_full_upgrade_context_uses_original_facts_evidence_and_trace(context_case):
    case, report = context_case
    response = await case.knowledge.client.get(
        f"{case.path}/escalations/{case.package_id}", headers=case.staff_headers
    )
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    payload = response.json()["payload"]
    assert payload["question"] == "请排查 429 mock-s01-rpm"
    assert payload["run_id"] == str(case.run_id)
    assert payload["attempt_id"] == report["attempt_id"]
    assert payload["observed_facts"] == report["facts"] and len(payload["observed_facts"]) > 0
    assert payload["sources"] == report["sources"] and len(payload["sources"]) == 4
    assert payload["confirmed_causes"] == [
        row for row in report["candidates"] if row["confirmation"] == "confirmed_mock"
    ]
    assert payload["unconfirmed_candidates"] == [
        row for row in report["candidates"] if row["confirmation"] == "unconfirmed"
    ]
    assert payload["steps"] == report["stages"] and len(payload["steps"]) <= 10
    assert payload["evidence_ids"] == report["evidence_ids"]
    assert bool(payload["evidence_ids"]) == bool(payload["confirmed_causes"])
    assert payload["trace_id"] and payload["captured_at"]
    assert "content_markdown" not in response.text
    same = await proposal(case.action, case.run_id)
    assert UUID(same["id"]) == case.action_id
    async with case.factory() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(TicketEscalationPackage)
                .where(TicketEscalationPackage.action_id == case.action_id)
            )
            == 1
        )
    assert len((await counts(case.action, case.action_id))[2]) == 1


@pytest.mark.parametrize("reader", ["source", "staff"])
async def test_either_knowledge_acl_revocation_hides_entire_upgrade_context(context_case, reader):
    case, _ = context_case
    kept = case.staff_id if reader == "source" else case.knowledge.reader_id
    await case.knowledge.acl([(KnowledgeAclSubjectType.USER, str(kept))])
    response = await case.knowledge.client.get(
        f"{case.path}/escalations/{case.package_id}", headers=case.staff_headers
    )
    assert response.status_code == 200, response.text
    payload = response.json()["payload"]
    assert payload["question"] == "原来源已失效或无权读取。"
    for field in [
        "observed_facts",
        "sources",
        "steps",
        "confirmed_causes",
        "unconfirmed_candidates",
        "evidence_ids",
    ]:
        assert payload[field] == []
    assert payload["reason_codes"] == ["ESCALATION_SOURCE_UNAVAILABLE"]


@pytest.mark.parametrize("permission", ["ticket_handle", "support_context_read", "agent"])
async def test_context_rechecks_current_project_and_original_agent_grants(context_case, permission):
    case, _ = context_case
    if permission == "agent":
        async with case.factory() as session, session.begin():
            grants = await session.scalars(
                select(AgentAccessGrant).where(
                    AgentAccessGrant.tenant_id == case.knowledge.tenant_id,
                    AgentAccessGrant.subject_id == str(case.knowledge.reader_id),
                )
            )
            for row in grants:
                row.deleted_at = utc_now()
    else:
        await grant(case, permission=permission, enabled=False)
    response = await case.knowledge.client.get(
        f"{case.path}/escalations/{case.package_id}", headers=case.staff_headers
    )
    assert response.status_code in {403, 404}, response.text
    assert "rpm_count" not in response.text and "mock-s01-rpm" not in response.text


@pytest.mark.parametrize("route", ["ticket", "action"])
async def test_upgrade_read_lock_timeout_returns_conflict_without_old_context(context_case, route):
    case, _ = context_case
    path = (
        f"{case.path}/escalations/{case.package_id}"
        if route == "ticket"
        else f"/v1/actions/{case.action_id}/escalations/{case.package_id}"
    )
    headers = case.staff_headers if route == "ticket" else case.knowledge.reader_headers
    async with case.factory() as holder, holder.begin():
        await holder.execute(
            text("SELECT id FROM knowledge_documents WHERE id = :document FOR UPDATE"),
            {"document": case.knowledge.document_id},
        )
        async with asyncio.timeout(6):
            response = await case.knowledge.client.get(path, headers=headers)
    assert response.status_code == 409 and response.json()["error"]["code"] == "KNOWLEDGE_CONFLICT"
    assert "rpm_count" not in response.text
    response = await case.knowledge.client.get(path, headers=headers)
    assert response.status_code == 200 and response.json()["payload"]["observed_facts"]


@pytest.mark.parametrize("endpoint", ["tickets", "commands", "escalations"])
async def test_ticket_lists_preserve_pagination_boundaries_and_stable_order(ticket_case, endpoint):
    case = ticket_case
    second_run, second = await second_action(case)
    await queue(case.action, second, second_run)
    await step(case.action, UUID(second["id"]))
    await step(case.action, UUID(second["id"]))
    # 同一原动作在另一个真实 Run 中形成第二份独立来源升级包。
    same_run = await completed_run(case.action)
    assert UUID((await proposal(case.action, same_run))["id"]) == case.action_id
    assert (await command(case)).status_code == 200
    assert (await command(case, "reply", body="当前处理记录")).status_code == 200
    path = "/v1/mock-tickets" if endpoint == "tickets" else f"{case.path}/{endpoint}"
    observed = []
    for page_number in [1, 2, 3]:
        response = await case.knowledge.client.get(
            path,
            headers=case.knowledge.reader_headers,
            params={"page": page_number, "page_size": 1},
        )
        assert response.status_code == 200, response.text
        value = response.json()
        assert value["total"] == value["pages"] == 2
        assert value["page"] == page_number and value["page_size"] == 1
        assert len(value["items"]) == int(page_number < 3)
        observed.extend(item["id"] for item in value["items"])
    assert len(observed) == len(set(observed)) == 2
    for params in [{"page": 0}, {"page": -1}, {"page_size": 0}, {"page_size": 101}]:
        response = await case.knowledge.client.get(
            path, headers=case.knowledge.reader_headers, params=params
        )
        assert response.status_code == 422
    if endpoint == "tickets":
        for params in [{"keywords": "%"}, {"keywords": "_"}, {"state": "closed"}]:
            response = await case.knowledge.client.get(
                path, headers=case.knowledge.reader_headers, params=params
            )
            assert response.status_code == 200 and response.json()["total"] == 0


@pytest.mark.parametrize("committed", [False, True], ids=["before-target", "after-target"])
async def test_real_target_deadline_then_takeover_allows_proof_but_no_repeated_write(
    ticket_case, monkeypatch, committed
):
    case = ticket_case
    _, payload = await second_action(case)
    action_id = UUID(payload["id"])
    await queue(case.action, payload)
    permit = await claim(case.action, action_id)
    original = action_runtime.submit_mock_ticket
    entered = asyncio.Event()

    async def delayed(*args, **kwargs):
        if committed:
            await original(*args, **kwargs)
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(action_runtime, "submit_mock_ticket", delayed)
    settings = case.knowledge.settings.model_copy(update={"action_target_timeout_seconds": 0.1})
    await action_runtime.execute_action(case.factory, permit=permit, settings=settings)
    assert entered.is_set()
    current = await read_action(case.action, action_id)
    assert current.status == "unknown" and current.error_code == "ACTION_DEPENDENCY_TIMEOUT"
    assert len((await counts(case.action, action_id))[2]) == int(committed)
    assert (await command(case)).status_code == 200
    monkeypatch.setattr(action_runtime, "submit_mock_ticket", original)
    verification = await step(case.action, action_id)
    assert verification.kind == "reconcile"
    attempts, receipts, tickets = await counts(case.action, action_id)
    assert [row.kind for row in attempts] == ["execute", "reconcile"]
    assert receipts[-1].status == ("verified" if committed else "not_executed")
    if committed:
        assert (await read_action(case.action, action_id)).status == "succeeded"
    else:
        await due(case.action, action_id)
        async with case.factory() as session, session.begin():
            assert (
                await action_runtime.claim_action(
                    session, settings=case.knowledge.settings, owner_id="no-write-during-takeover"
                )
                is None
            )
        assert (await read_action(case.action, action_id)).status == "failed"
    assert len(tickets) == int(committed)
    assert len((await counts(case.action, action_id))[0]) == 2


async def test_takeover_keeps_read_only_ticket_query_without_implicit_write_permission(ticket_case):
    case = ticket_case
    assert (await command(case)).status_code == 200
    before = await ticket(case)
    for user_id in [case.knowledge.reader_id, case.staff_id, case.other_id]:
        async with case.factory() as session:
            identity = await live_identity(
                session, tenant_id=case.knowledge.tenant_id, user_id=user_id
            )
            result = await query_mock_support_ticket(
                session,
                identity=identity,
                settings=case.knowledge.settings,
                ticket_id=case.external_id,
            )
        assert result.status == ("not_found" if user_id == case.other_id else "available")
        if result.status == "available":
            assert result.ticket_status == "in_progress" and result.version == before["version"]
        else:
            assert result.title is None and result.summary is None and result.external_id is None
    assert await ticket(case) == before
