"""真实 API/PG 下稳定动作键、独立复核及故障后的未知结果对账。"""

import asyncio
import os
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session
from supportops_core import action_proposal_services, action_runtime, worker_runtime
from supportops_core.action_ledger_services import ActionStateConflict, transition_business_action
from supportops_core.action_runtime import ActionLeaseLost, ActionPermit
from supportops_core.agent_config import SupportAnswer
from supportops_core.config import Settings
from supportops_core.db import create_session_factory
from supportops_core.enums import AuditAction, GrantSubjectType
from supportops_core.mock_action_connector import submit_mock_ticket
from supportops_core.models import (
    ActionAttempt,
    ActionReceipt,
    AgentAccessGrant,
    AgentAuditEvent,
    AgentRun,
    BusinessAction,
    MockSupportTicket,
    ToolInvocation,
)

from tests.api.test_knowledge_access import KnowledgeCase
from tests.api.test_knowledge_access import knowledge_case as knowledge_case
from tests.conftest import provision_agent
from tests.postgres.test_knowledge_access import engine as engine

pytestmark = pytest.mark.skipif(
    os.getenv("SUPPORTOPS_RUN_POSTGRES_TESTS") != "1", reason="需显式启用隔离 PostgreSQL 测试库"
)


@pytest.fixture
def settings() -> Settings:
    value = Settings()
    url = make_url(value.database_url)
    assert value.environment == "test" and url.drivername == "postgresql+asyncpg"
    assert url.host in {"127.0.0.1", "localhost", "::1"}
    assert url.database and url.database.endswith("_test")
    return value.model_copy(
        update={
            "business_action_mock_enabled": True,
            "action_max_reconciliations": 3,
        }
    )


@dataclass
class ActionCase:
    knowledge: KnowledgeCase
    agent_id: UUID

    @property
    def factory(self):
        return create_session_factory(self.knowledge.engine)


@pytest.fixture
async def action_case(knowledge_case, monkeypatch):
    case = knowledge_case
    agent_id = await provision_agent(
        case.engine,
        case.settings,
        case.tenant_id,
        subject="knowledge-reader",
        tool_ids=("create_mock_ticket",),
        max_calls_per_tool=20,
        tool_call_limit=20,
    )
    async with create_session_factory(case.engine)() as session, session.begin():
        session.add(
            AgentAccessGrant(
                tenant_id=case.tenant_id,
                agent_id=agent_id,
                subject_type=GrantSubjectType.USER,
                subject_id=str(case.reader_id),
                created_by=case.owner_id,
            )
        )

    async def answer(*args, **kwargs):
        return SupportAnswer(answer="只读回答：如需建单，请单独确认提案与执行。")

    monkeypatch.setattr(worker_runtime, "invoke_agent", answer)
    value = ActionCase(case, agent_id)
    try:
        yield value
    finally:
        # 保留故障历史，只延后本例未终结任务，避免全局消费者误领下一例遗留。
        async with value.factory() as session, session.begin():
            actions = await session.scalars(
                select(BusinessAction).where(
                    BusinessAction.tenant_id == case.tenant_id,
                )
            )
            for row in actions:
                row.next_attempt_at = datetime.now(UTC) + timedelta(days=1)
                if row.current_attempt_id:
                    attempt = await session.get(ActionAttempt, row.current_attempt_id)
                    if attempt.status == "running":
                        attempt.lease_expires_at = datetime.now(UTC) + timedelta(days=1)


async def completed_run(case: ActionCase, content="需要支持 mock-s01-rpm") -> UUID:
    original = case.knowledge
    created = await original.client.post(
        "/v1/conversations", headers=original.reader_headers, json={"agent_id": str(case.agent_id)}
    )
    assert created.status_code == 201
    accepted = await original.client.post(
        f"/v1/conversations/{created.json()['id']}/messages",
        headers={**original.reader_headers, "Idempotency-Key": str(uuid4())},
        json={"content": content},
    )
    assert accepted.status_code == 202
    run_id = UUID(accepted.json()["run_id"])
    assert await worker_runtime.execute_agent_run(case.factory, run_id, settings=original.settings)
    return run_id


async def proposal(case: ActionCase, run_id: UUID):
    response = await case.knowledge.client.post(
        "/v1/actions/mock-ticket-proposals",
        headers=case.knowledge.reader_headers,
        json={"run_id": str(run_id)},
    )
    assert response.status_code == 201, response.text
    return response.json()


async def queue(case: ActionCase, payload: dict, run_id: UUID | None = None):
    response = await case.knowledge.client.post(
        f"/v1/actions/{payload['id']}/execute",
        headers=case.knowledge.reader_headers,
        json={
            "expected_generation": payload["generation"],
            "expected_parameters_digest": payload["parameters_digest"],
            "expected_catalog_version": payload["effective_catalog_version"],
            "context_run_id": str(run_id) if run_id else None,
        },
    )
    assert response.status_code == 202, response.text
    return response.json()


async def read_action(case: ActionCase, action_id: UUID):
    async with case.factory() as session:
        row = await session.get(BusinessAction, action_id)
        assert row is not None
        return row


async def due(case: ActionCase, action_id: UUID):
    async with case.factory() as session, session.begin():
        row = await session.get(BusinessAction, action_id)
        row.next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)


async def claim(case: ActionCase, action_id: UUID, owner="test-action-owner") -> ActionPermit:
    await due(case, action_id)
    async with case.factory() as session, session.begin():
        permit = await action_runtime.claim_action(
            session,
            settings=case.knowledge.settings,
            owner_id=owner,
        )
        assert permit is not None and permit.action_id == action_id
        return permit


async def step(case: ActionCase, action_id: UUID) -> ActionPermit:
    permit = await claim(case, action_id)
    await action_runtime.execute_action(
        case.factory, permit=permit, settings=case.knowledge.settings
    )
    return permit


async def remote_submit(case: ActionCase, permit: ActionPermit, **changes):
    async with case.factory() as session:
        row = await session.get(BusinessAction, permit.action_id)
    return await submit_mock_ticket(
        case.factory,
        settings=case.knowledge.settings,
        tenant_id=permit.tenant_id,
        owner_user_id=permit.owner_user_id,
        project_id=permit.project_id,
        logical_key=permit.logical_key,
        generation=permit.generation,
        parameters=permit.parameters,
        mode=permit.mode,
        conversation_id=permit.conversation_id,
        action_id=permit.action_id,
        agent_version_id=row.execution_agent_version_id,
        **changes,
    )


async def counts(case: ActionCase, action_id: UUID):
    async with case.factory() as session:
        action = await session.get(BusinessAction, action_id)
        attempts = (
            await session.scalars(
                select(ActionAttempt)
                .where(ActionAttempt.action_id == action_id)
                .order_by(ActionAttempt.generation)
            )
        ).all()
        receipts = (
            await session.scalars(
                select(ActionReceipt)
                .where(ActionReceipt.action_id == action_id)
                .order_by(ActionReceipt.observed_at, ActionReceipt.id)
            )
        ).all()
        tickets = (
            await session.scalars(
                select(MockSupportTicket).where(
                    MockSupportTicket.tenant_id == action.tenant_id,
                    MockSupportTicket.logical_key == action.logical_key,
                )
            )
        ).all()
        return attempts, receipts, tickets


async def expire_and_recover(case: ActionCase, action_id: UUID):
    async with case.factory() as session, session.begin():
        action = await session.get(BusinessAction, action_id)
        attempt = await session.get(ActionAttempt, action.current_attempt_id)
        attempt.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    async with case.factory() as session, session.begin():
        assert await action_runtime.recover_expired_actions(session) == 1
    assert (await read_action(case, action_id)).status == "unknown"


async def test_duplicate_intents_and_new_runs_keep_one_action_and_original_provenance(action_case):
    case = action_case
    first = await completed_run(case)
    original = await proposal(case, first)
    repeated = await proposal(case, first)
    other = await completed_run(case)
    relinked = await proposal(case, other)
    assert original["id"] == repeated["id"] == relinked["id"]
    assert (
        relinked["run_id"] == str(first)
        and relinked["parameters_digest"] == original["parameters_digest"]
    )
    assert (await counts(case, UUID(original["id"])))[2] == []
    await queue(case, relinked, other)
    await queue(case, relinked, other)
    async with case.factory() as session:
        action = await session.get(BusinessAction, UUID(original["id"]))
        assert action.execution_run_id == other and action.run_id == first
        assert (
            await session.scalar(
                select(func.count())
                .select_from(BusinessAction)
                .where(BusinessAction.tenant_id == case.knowledge.tenant_id)
            )
            == 1
        )
        audits = (
            await session.scalars(
                select(AgentAuditEvent).where(
                    AgentAuditEvent.tenant_id == case.knowledge.tenant_id,
                    AgentAuditEvent.action.in_(
                        [AuditAction.BUSINESS_ACTION_PROPOSED, AuditAction.BUSINESS_ACTION_QUEUED]
                    ),
                )
            )
        ).all()
        assert len(audits) == 2
    assert (await step(case, action.id)).kind == "execute"
    assert (await step(case, action.id)).kind == "verify"
    assert (await read_action(case, action.id)).status == "succeeded"


async def test_parallel_runs_and_execution_intents_share_one_business_key(action_case, monkeypatch):
    case = action_case
    runs = await asyncio.gather(completed_run(case), completed_run(case))
    ready = asyncio.Event()

    async def send(run_id):
        await ready.wait()
        return await proposal(case, run_id)

    tasks = [asyncio.create_task(send(run_id)) for run_id in runs]
    ready.set()
    async with asyncio.timeout(10):
        payloads = await asyncio.gather(*tasks)
    assert payloads[0]["id"] == payloads[1]["id"]
    # 两个请求均持有各自来源 Run，强制非原始 Run 先取得动作锁。
    # 旧实现两次 UPDATE 会复检原 Run 外键，与另一请求的动作锁等待构成死锁。
    lock_action = action_proposal_services.lock_business_action
    both_sources_locked = asyncio.Event()
    action_locked = asyncio.Event()
    arrivals = 0

    async def synchronized_lock(session, **kwargs):
        nonlocal arrivals
        source_id = next(
            item.id
            for item in session.identity_map.values()
            if isinstance(item, AgentRun) and item.id in runs
        )
        arrivals += 1
        if arrivals == 2:
            both_sources_locked.set()
        await both_sources_locked.wait()
        if str(source_id) == payloads[0]["run_id"]:
            await action_locked.wait()
        result = await lock_action(session, **kwargs)
        action_locked.set()
        return result

    monkeypatch.setattr(action_proposal_services, "lock_business_action", synchronized_lock)
    await asyncio.gather(*(queue(case, payloads[i], runs[i]) for i in range(2)))
    action_id = UUID(payloads[0]["id"])
    permits = await asyncio.gather(*(_claim_optional(case, f"parallel-{i}") for i in range(2)))
    claimed = [permit for permit in permits if permit is not None]
    assert len(claimed) == 1 and claimed[0].action_id == action_id
    await action_runtime.execute_action(
        case.factory, permit=claimed[0], settings=case.knowledge.settings
    )
    await step(case, action_id)
    attempts, receipts, tickets = await counts(case, action_id)
    assert [row.kind for row in attempts] == ["execute", "verify"]
    assert [row.status for row in receipts] == ["submitted", "verified"] and len(tickets) == 1


async def _claim_optional(case, owner):
    async with case.factory() as session, session.begin():
        return await action_runtime.claim_action(
            session, settings=case.knowledge.settings, owner_id=owner
        )


@pytest.mark.parametrize(
    "mode", ["normal", "lost", "timeout", "not_executed", "unknown", "conflict"]
)
async def test_target_faults_require_independent_proof_and_bounded_reconciliation(
    action_case, mode
):
    case = action_case
    run = await completed_run(case, f"请支持 mock-s01-rpm [action:{mode}]")
    payload = await proposal(case, run)
    await queue(case, payload)
    action_id = UUID(payload["id"])
    assert (await step(case, action_id)).kind == "execute"
    state = await read_action(case, action_id)
    assert state.status == "unknown" and state.external_id is None
    second = await step(case, action_id)
    assert second.kind == ("verify" if mode in {"normal", "conflict"} else "reconcile")
    if mode in {"timeout", "not_executed"}:
        state = await read_action(case, action_id)
        assert state.status == "queued" and state.error_code == "ACTION_PROVEN_NOT_EXECUTED"
        old_attempts, receipts, tickets = await counts(case, action_id)
        assert receipts[-1].status == "not_executed" and tickets == []
        assert receipts[-1].verified_fields["execution_fence"] >= second.generation
        assert (await step(case, action_id)).kind == "execute"
        assert (await step(case, action_id)).kind == "verify"
    elif mode == "unknown":
        for _ in range(2):
            assert (await step(case, action_id)).kind == "reconcile"
    final = await read_action(case, action_id)
    attempts, receipts, tickets = await counts(case, action_id)
    if mode == "unknown":
        assert final.status == "needs_attention" and final.reconciliation_count == 3
        assert len(tickets) == 0 and sum(row.kind == "execute" for row in attempts) == 1
        assert all(row.status == "unknown" for row in receipts)
    elif mode == "conflict":
        assert (
            final.status == "needs_attention" and final.error_code == "ACTION_VERIFICATION_CONFLICT"
        )
        assert len(tickets) == 1 and receipts[-1].status == "conflict" and final.external_id is None
    else:
        assert final.status == "succeeded" and final.external_id == tickets[0].external_id
        assert len(tickets) == 1 and receipts[-1].status == "verified"
        assert attempts[-1].kind in {"verify", "reconcile"}
    assert len({row.parameters_digest for row in attempts}) == 1
    async with case.factory() as session:
        parent = await session.get(AgentRun, run)
        assert parent.status.value == "completed"


async def test_duplicate_target_submission_is_idempotent_without_claiming_success(action_case):
    case = action_case
    payload = await proposal(case, await completed_run(case))
    await queue(case, payload)
    action_id = UUID(payload["id"])
    permit = await claim(case, action_id)
    first, second = await asyncio.gather(remote_submit(case, permit), remote_submit(case, permit))
    assert first.external_id == second.external_id and first.status == second.status == "submitted"
    await action_runtime.record_action_outcome(
        case.factory,
        permit=permit,
        settings=case.knowledge.settings,
        proof=first,
        code=None,
    )
    assert (await read_action(case, action_id)).status == "unknown"
    with pytest.raises(ActionStateConflict):
        async with case.factory() as session, session.begin():
            await transition_business_action(
                session,
                action_id=action_id,
                tenant_id=permit.tenant_id,
                expected_generation=permit.generation,
                target="succeeded",
            )
    await step(case, action_id)
    assert len((await counts(case, action_id))[2]) == 1


@pytest.mark.parametrize("boundary", ["proposal", "queue", "claim", "receipt", "observation"])
async def test_audit_failure_is_atomic_or_preserves_already_committed_business_facts(
    action_case, monkeypatch, boundary
):
    case = action_case
    run = await completed_run(case)
    if boundary in {"proposal", "queue"}:
        payload = await proposal(case, run) if boundary == "queue" else None

        def fail_audit(session, context, instances):
            if any(
                isinstance(row, AgentAuditEvent)
                and row.tenant_id == case.knowledge.tenant_id
                and row.action
                == (
                    AuditAction.BUSINESS_ACTION_PROPOSED
                    if boundary == "proposal"
                    else AuditAction.BUSINESS_ACTION_QUEUED
                )
                for row in session.new
            ):
                raise RuntimeError("injected-action-audit-failure")

        event.listen(Session, "before_flush", fail_audit)
        try:
            # ASGITransport保留原异常，不能把服务端500误判为已提交业务。
            with pytest.raises(RuntimeError, match="injected-action-audit"):
                if boundary == "proposal":
                    await proposal(case, run)
                else:
                    await queue(case, payload)
        finally:
            event.remove(Session, "before_flush", fail_audit)
        async with case.factory() as session:
            actions = (
                await session.scalars(
                    select(BusinessAction).where(
                        BusinessAction.tenant_id == case.knowledge.tenant_id
                    )
                )
            ).all()
            assert (
                actions == []
                if boundary == "proposal"
                else len(actions) == 1 and actions[0].status == "proposed"
            )
        payload = await proposal(case, run)
        await queue(case, payload)
        await step(case, UUID(payload["id"]))
        await step(case, UUID(payload["id"]))
        return
    payload = await proposal(case, run)
    await queue(case, payload)
    action_id = UUID(payload["id"])
    if boundary == "claim":
        original = action_runtime.append_event

        async def fail_claim(*args, **kwargs):
            if kwargs["event_type"] == "action.started":
                raise RuntimeError("injected-action-claim-audit")
            return await original(*args, **kwargs)

        with monkeypatch.context() as patch:
            patch.setattr(action_runtime, "append_event", fail_claim)
            with pytest.raises(RuntimeError, match="injected-action-claim"):
                await claim(case, action_id)
        assert (await read_action(case, action_id)).status == "queued"
        assert await counts(case, action_id) == ([], [], [])
        await step(case, action_id)
        await step(case, action_id)
        assert (await read_action(case, action_id)).status == "succeeded"
        return
    permit = await claim(case, action_id)
    if boundary == "receipt":
        original = action_runtime.append_event

        async def fail_event(*args, **kwargs):
            if kwargs["event_type"] == "action.updated":
                raise RuntimeError("injected-action-event-failure")
            return await original(*args, **kwargs)

        with monkeypatch.context() as patch:
            patch.setattr(action_runtime, "append_event", fail_event)
            with pytest.raises(RuntimeError, match="injected-action-event"):
                await action_runtime.execute_action(
                    case.factory, permit=permit, settings=case.knowledge.settings
                )
        attempts, receipts, tickets = await counts(case, action_id)
        assert receipts == [] and len(tickets) == 1 and attempts[0].status == "running"
        await expire_and_recover(case, action_id)
        await step(case, action_id)
    else:

        async def fail_observation(*args, **kwargs):
            raise RuntimeError("injected-observation-failure")

        monkeypatch.setattr(action_runtime, "record_tool_observation", fail_observation)
        await action_runtime.execute_action(
            case.factory, permit=permit, settings=case.knowledge.settings
        )
        await step(case, action_id)
    assert (await read_action(case, action_id)).status == "succeeded"
    assert len((await counts(case, action_id))[2]) == 1


_CRASH_PROGRAM = r"""
import asyncio,sys
from supportops_core import action_runtime
from supportops_core.config import Settings
from supportops_core.db import create_engine,create_session_factory
from supportops_core.models import BusinessAction

async def main():
    settings=Settings()
    engine=create_engine(settings)
    factory=create_session_factory(engine)
    mode=sys.argv[1]
    try:
        async with factory() as session,session.begin():
            permit=await action_runtime.claim_action(
                session,settings=settings,owner_id="crashing-consumer")
            assert permit is not None
        if mode=="after":
            async with factory() as session:
                action=await session.get(BusinessAction,permit.action_id)
            await action_runtime.submit_mock_ticket(factory,settings=settings,
                tenant_id=permit.tenant_id,owner_user_id=permit.owner_user_id,
                project_id=permit.project_id,logical_key=permit.logical_key,
                generation=permit.generation,parameters=permit.parameters,mode=permit.mode,
                conversation_id=permit.conversation_id,action_id=permit.action_id,
                agent_version_id=action.execution_agent_version_id)
        print("ACTION_READY",flush=True)
        await asyncio.Event().wait()
    finally:
        await engine.dispose()
asyncio.run(main())
"""


@pytest.mark.parametrize("boundary", ["before", "after"])
async def test_process_crash_reconciles_original_key_and_fences_old_owner(action_case, boundary):
    case = action_case
    payload = await proposal(case, await completed_run(case))
    await queue(case, payload)
    action_id = UUID(payload["id"])
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        _CRASH_PROGRAM,
        boundary,
        env={**os.environ, "SUPPORTOPS_BUSINESS_ACTION_MOCK_ENABLED": "true"},
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        async with asyncio.timeout(10):
            assert await process.stdout.readline() == b"ACTION_READY\n", "崩溃消费者未到达故障点"
        attempts, receipts, tickets = await counts(case, action_id)
        assert len(attempts) == 1 and receipts == []
        assert len(tickets) == (1 if boundary == "after" else 0)
    finally:
        if process.returncode is None:
            process.kill()
        async with asyncio.timeout(5):
            await process.communicate()
    await expire_and_recover(case, action_id)
    recovery = await step(case, action_id)
    assert recovery.kind == "reconcile"
    if boundary == "before":
        assert (await read_action(case, action_id)).status == "queued"
        await step(case, action_id)
        await step(case, action_id)
    final = await read_action(case, action_id)
    assert final.status == "succeeded" and final.logical_key == recovery.logical_key
    attempts, receipts, tickets = await counts(case, action_id)
    assert len(tickets) == 1 and receipts[-1].status == "verified"
    assert sum(row.kind == "execute" for row in attempts) == (2 if boundary == "before" else 1)


async def test_late_outcome_from_expired_owner_cannot_overwrite_reconciled_success(action_case):
    case = action_case
    payload = await proposal(case, await completed_run(case))
    await queue(case, payload)
    action_id = UUID(payload["id"])
    old = await claim(case, action_id)
    proof = await remote_submit(case, old)
    await expire_and_recover(case, action_id)
    await step(case, action_id)
    final = await read_action(case, action_id)
    with pytest.raises(ActionLeaseLost):
        await action_runtime.record_action_outcome(
            case.factory,
            permit=old,
            settings=case.knowledge.settings,
            proof=proof,
            code=None,
        )
    again = await read_action(case, action_id)
    assert (again.status, again.generation, again.external_id) == (
        final.status,
        final.generation,
        final.external_id,
    )
    assert len((await counts(case, action_id))[1]) == 1


async def test_same_key_changed_parameters_is_rejected_without_overwriting_original(action_case):
    case = action_case
    payload = await proposal(case, await completed_run(case))
    action_id = UUID(payload["id"])
    original = action_proposal_services.frozen_parameters_digest
    # 模拟后续提案给同一个真实业务键传入不同冻结摘要，不能覆盖原参数。
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(action_proposal_services, "frozen_parameters_digest", lambda _: "0" * 64)
        rejected = await case.knowledge.client.post(
            "/v1/actions/mock-ticket-proposals",
            headers=case.knowledge.reader_headers,
            json={"run_id": payload["run_id"]},
        )
        assert rejected.status_code == 409
        assert rejected.json()["error"]["code"] == "ACTION_STATE_CONFLICT"
    assert action_proposal_services.frozen_parameters_digest is original
    row = await read_action(case, action_id)
    assert row.parameters_digest == payload["parameters_digest"] and row.generation == 0


@pytest.mark.parametrize(
    "content",
    [
        "需要支持 MOCK-S01-RPM",
        "需要支持 request_id:MOCK-S01-RPM",
        "需要支持 请求ID：Mock-S01-Rpm",
        "需要支持 mock-s01-rpm MOCK-S01-RPM",
    ],
)
async def test_case_insensitive_request_intent_does_not_create_another_action(action_case, content):
    case = action_case
    first = await proposal(case, await completed_run(case))
    second = await proposal(case, await completed_run(case, content))
    assert first["id"] == second["id"]
    assert second["parameters"]["issue_key"] == "request:mock-s01-rpm"


async def test_repeated_model_tool_intents_are_one_proposal_and_never_implicit_execution(
    action_case, monkeypatch
):
    import json

    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.messages import AIMessageChunk, ToolMessage
    from langchain_core.outputs import ChatGenerationChunk
    from supportops_core import agent_runtime

    case = action_case
    calls = []

    class RepeatingModel(BaseChatModel):
        @property
        def _llm_type(self):
            return "isolated-duplicate-action-intent"

        def bind_tools(self, tools, **kwargs):
            return self

        def _generate(self, messages, **kwargs):
            raise AssertionError("只允许隔离流式模型")

        async def _astream(self, messages, **kwargs):
            calls.append(messages)
            assert len(calls) <= 3
            name = "create_mock_ticket" if len(calls) < 3 else "SupportAnswer"
            arguments = {} if len(calls) < 3 else {"answer": "提案已记录，尚未执行"}
            yield ChatGenerationChunk(
                message=AIMessageChunk(
                    id=f"model-intent-{len(calls)}",
                    content="",
                    tool_call_chunks=[
                        {
                            "index": 0,
                            "name": name,
                            "args": json.dumps(arguments),
                            "id": f"different-intent-{len(calls)}",
                        }
                    ],
                )
            )

    async def build(*args, **kwargs):
        return RepeatingModel()

    monkeypatch.setattr(agent_runtime.ModelAdapter, "build", build)
    monkeypatch.setattr(worker_runtime, "invoke_agent", agent_runtime.invoke_agent)
    run_id = await completed_run(case)
    assert len(calls) == 3
    returned = [
        message
        for message in calls[-1]
        if isinstance(message, ToolMessage) and message.name == "create_mock_ticket"
    ]
    assert len(returned) == 2
    async with case.factory() as session:
        actions = (
            await session.scalars(
                select(BusinessAction).where(BusinessAction.tenant_id == case.knowledge.tenant_id)
            )
        ).all()
        invoked = (
            await session.scalars(
                select(ToolInvocation).where(
                    ToolInvocation.run_id == run_id, ToolInvocation.tool_id == "create_mock_ticket"
                )
            )
        ).all()
        assert len(actions) == 1 and actions[0].status == "proposed"
        assert len(invoked) == 2 and {row.business_action_id for row in invoked} == {actions[0].id}
        assert all(row.status == "succeeded" for row in invoked)
    assert await counts(case, actions[0].id) == ([], [], [])
