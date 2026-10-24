"""真实 API/Worker/PG 验收固定 Mock 429 事实工作流，无模型和外部连接器。"""

import os
from dataclasses import dataclass
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from supportops_core import agent_runtime, worker_runtime
from supportops_core.auth import IdentityContext, Principal
from supportops_core.config import Settings
from supportops_core.db import create_session_factory
from supportops_core.diagnostic_mock_services import (
    MOCK_DIAGNOSTIC_TOOLS,
    DiagnosticQuery,
    query_mock_diagnostic,
)
from supportops_core.enums import GrantSubjectType, MessageRole, RunStatus
from supportops_core.knowledge_services import create_knowledge_version
from supportops_core.models import (
    AgentAccessGrant,
    AgentRun,
    BusinessAction,
    Message,
    ModelCallLedger,
    RunAttempt,
    RunEvent,
    ToolCatalogEntry,
    ToolInvocation,
    User,
)
from supportops_core.tool_gateway import ToolExecutionGateway

from tests.api.test_knowledge_access import KnowledgeCase
from tests.api.test_knowledge_access import knowledge_case as knowledge_case
from tests.conftest import provision_agent
from tests.postgres.test_knowledge_access import engine as engine
from tests.postgres.test_knowledge_access import settings as settings
from tests.postgres.test_knowledge_lifecycle import approve, drain, publish

pytestmark = pytest.mark.skipif(
    os.getenv("SUPPORTOPS_RUN_POSTGRES_TESTS") != "1", reason="需显式启用隔离 PostgreSQL 测试库"
)

CODES = (
    "rpm_exceeded",
    "tpm_reservation_exceeded",
    "project_concurrency_exceeded",
    "plan_quota_exhausted",
    "upstream_rate_limited",
)
REQUESTS = (
    "mock-s01-rpm",
    "mock-s02-tpm",
    "mock-s03-concurrency",
    "mock-s04-account",
    "mock-s05-upstream",
    "mock-s06-unknown",
)
RULES = "# 429规则\n" + "\n".join(f"supportops_429_rule_v1:{code}" for code in CODES)


@pytest.fixture(autouse=True)
def forbid_model_calls(monkeypatch):
    async def forbidden(*args, **kwargs):
        raise AssertionError("固定诊断不应调用模型或制造checkpoint")

    monkeypatch.setattr(worker_runtime, "invoke_agent", forbidden)
    monkeypatch.setattr(agent_runtime.ModelAdapter, "build", forbidden)


@dataclass
class DiagnosticCase:
    knowledge: KnowledgeCase
    agent_id: UUID
    settings: Settings


async def prepare(
    case: KnowledgeCase,
    *,
    rules: str | None = RULES,
    tool_limit: int = 8,
    daily_limit: int = 1000,
    extra_tools: tuple[str, ...] = (),
) -> DiagnosticCase:
    if rules is not None:
        async with create_session_factory(case.engine)() as session, session.begin():
            version = await create_knowledge_version(
                session,
                document_id=case.document_id,
                tenant_id=case.tenant_id,
                actor_user_id=case.owner_id,
                title="429诊断规则",
                content_markdown=rules,
                expected_revision=1,
                correlation_id="diagnostic-rule-version",
            )
            version_id = version.id
        target = await approve(case, version_id)
        async with create_session_factory(case.engine)() as session, session.begin():
            await publish(session, case, target)
    await drain(case)
    agent_id = await provision_agent(
        case.engine,
        case.settings,
        case.tenant_id,
        subject="knowledge-reader",
        tool_ids=(*sorted(MOCK_DIAGNOSTIC_TOOLS), "search_knowledge", *extra_tools),
        tool_call_limit=tool_limit,
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
    return DiagnosticCase(
        case,
        agent_id,
        case.settings.model_copy(
            update={
                "diagnostic_mock_enabled": True,
                "tenant_tool_limit_per_day": daily_limit,
            }
        ),
    )


async def submit(case: DiagnosticCase, content: str) -> UUID:
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
    return UUID(accepted.json()["run_id"])


async def execute(case: DiagnosticCase, content: str):
    run_id = await submit(case, content)
    factory = create_session_factory(case.knowledge.engine)
    completed = await worker_runtime.execute_agent_run(factory, run_id, settings=case.settings)
    async with factory() as session:
        run = await session.get(AgentRun, run_id)
        assert completed and run.status == RunStatus.COMPLETED
        attempt = await session.get(RunAttempt, run.current_attempt_id)
        messages = (
            await session.scalars(
                select(Message).where(
                    Message.conversation_id == run.conversation_id,
                    Message.role == MessageRole.ASSISTANT,
                )
            )
        ).all()
        assert len(messages) == 1
        result = messages[0].support_result
        invocations = (
            await session.scalars(
                select(ToolInvocation)
                .where(
                    ToolInvocation.run_id == run_id,
                )
                .order_by(ToolInvocation.created_at, ToolInvocation.id)
            )
        ).all()
        events = (
            await session.scalars(
                select(RunEvent)
                .where(
                    RunEvent.run_id == run_id,
                )
                .order_by(RunEvent.sequence)
            )
        ).all()
        assert run.model_call_count == 0
        assert (
            await session.scalar(
                select(func.count())
                .select_from(ModelCallLedger)
                .where(ModelCallLedger.run_id == run_id)
            )
            == 0
        )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(BusinessAction)
                .where(BusinessAction.tenant_id == case.knowledge.tenant_id)
            )
            == 0
        )
        assert attempt.checkpoint_id is None
        assert result["diagnostic_data_environment"] == "mock"
        assert result["answer"].startswith("【模拟诊断】")
        assert result["business_actions"] == []
        assert len(invocations) <= 5
        assert len(result["diagnostic"]["stages"]) <= 10
        assert result["diagnostic"]["finished_at"] is not None
        assert result["diagnostic"] == attempt.context_snapshot["diagnostic"]
        assert sum(row.event_type == "result.ready" for row in events) == 1
        return run, result, invocations, events


@pytest.mark.parametrize("scenario", range(6))
async def test_six_fixed_429_paths_only_confirm_facts_and_authorized_rule(knowledge_case, scenario):
    case = await prepare(knowledge_case)
    run, result, invocations, events = await execute(case, f"429 请求 {REQUESTS[scenario]}")
    report = result["diagnostic"]
    assert report["workflow_version"] == "mock_429_workflow_v1"
    assert {row["tool_id"] for row in report["sources"]} == MOCK_DIAGNOSTIC_TOOLS
    assert {row["invocation_id"] for row in report["sources"]} == {
        str(row.id) for row in invocations if row.tool_id in MOCK_DIAGNOSTIC_TOOLS
    }
    assert len(report["stages"]) == 10 and len(report["facts"]) > 0
    assert all(fact["confirmation"] == "source_observed" for fact in report["facts"])
    if scenario < 5:
        assert report["status"] == "confirmed_mock"
        assert [row["code"] for row in report["candidates"]] == [CODES[scenario]]
        assert report["candidates"][0]["confirmation"] == "confirmed_mock"
        assert report["evidence_ids"] and result["citations"]
        assert report["escalation"] is None and result["escalation_status"] == "not_requested"
        assert len(invocations) == 5
        if scenario == 3:
            facts = {row["field"]: row["value"] for row in report["facts"]}
            assert facts["cash_balance_usd"] == 100 and facts["plan_remaining_tokens"] == 0
            assert "套餐" in result["answer"] and "余额不足" not in result["answer"]
    else:
        assert report["status"] == "unconfirmed" and report["candidates"] == []
        assert report["evidence_ids"] == [] and len(invocations) == 4
        assert report["escalation"]["status"] == "recommended"
        assert "DIAGNOSTIC_ROOT_CAUSE_NOT_UNIQUE" in report["reason_codes"]
        assert "尚不能确认" in result["answer"]
    # 刷新和SSE重读只有持久事实，不能重新执行查询或重建Run。
    for _ in range(2):
        refreshed = await case.knowledge.client.get(
            f"/v1/conversations/{run.conversation_id}", headers=case.knowledge.reader_headers
        )
        assert refreshed.status_code == 200
        assert refreshed.json()["messages"][-1]["support_result"] == result
        assert refreshed.json()["latest_run"]["diagnostic_stages"] == report["stages"]
    diagnostic = await case.knowledge.client.get(
        f"/v1/runs/{run.id}/diagnostic", headers=case.knowledge.reader_headers
    )
    assert diagnostic.status_code == 200 and diagnostic.json() == report
    stream = await case.knowledge.client.get(
        f"/v1/runs/{run.id}/events", headers=case.knowledge.reader_headers
    )
    assert stream.status_code == 200
    assert "diagnostic.complete" in stream.text and "run.completed" in stream.text
    async with create_session_factory(case.knowledge.engine)() as session:
        current = (
            await session.scalars(
                select(ToolInvocation.id)
                .where(ToolInvocation.run_id == run.id)
                .order_by(ToolInvocation.created_at, ToolInvocation.id)
            )
        ).all()
        assert current == [row.id for row in invocations]


@pytest.mark.parametrize(
    "mode,status,code",
    [
        ("empty", "unavailable", "DIAGNOSTIC_REQUIRED_SOURCE_UNAVAILABLE"),
        ("timeout", "unavailable", "MOCK_DEPENDENCY_TIMEOUT"),
        ("denied", "unavailable", "MOCK_DOWNSTREAM_ACCESS_DENIED"),
        ("conflict", "conflict", "DIAGNOSTIC_SOURCE_CONFLICT"),
    ],
)
async def test_dependency_modes_escalate_without_fabricated_root(
    knowledge_case, mode, status, code
):
    case = await prepare(knowledge_case)
    _, result, invocations, _ = await execute(case, f"429 mock-s01-rpm [mock:{mode}]")
    report = result["diagnostic"]
    assert report["status"] == status and report["candidates"] == []
    assert code in report["reason_codes"] and report["escalation"]["status"] == "recommended"
    assert result["escalation_status"] == "recommended" and len(invocations) == 4
    assert report["evidence_ids"] == []
    if mode != "conflict":
        assert report["facts"] == [] and report["escalation"]["confirmed_fact_count"] == 0


@pytest.mark.parametrize(
    "run_limit,daily_limit,successes,code",
    [
        (1, 1000, 1, "TOOL_CALL_QUOTA_EXCEEDED"),
        (4, 1000, 4, "TOOL_CALL_QUOTA_EXCEEDED"),
        (8, 1, 1, "TENANT_TOOL_DAILY_QUOTA_EXCEEDED"),
    ],
)
async def test_query_budget_exhaustion_preserves_partial_facts_and_stops_queries(
    knowledge_case, run_limit, daily_limit, successes, code
):
    case = await prepare(knowledge_case, tool_limit=run_limit, daily_limit=daily_limit)
    _, result, invocations, _ = await execute(case, "429 mock-s01-rpm")
    report = result["diagnostic"]
    assert report["status"] != "confirmed_mock" and report["escalation"]
    assert code in report["reason_codes"]
    assert len([row for row in invocations if row.status == "succeeded"]) == successes
    assert len(invocations) == successes + 1 and invocations[-1].status == "denied"
    assert report["facts"] and report["evidence_ids"] == []
    assert report["escalation"]["confirmed_fact_count"] == len(report["facts"])
    assert len(report["sources"]) == successes
    assert result["escalation_status"] == "recommended"


@pytest.mark.parametrize("mode", ["missing", "wrong", "revoked"])
async def test_missing_or_unapproved_knowledge_rule_cannot_confirm_candidate(knowledge_case, mode):
    case = await prepare(
        knowledge_case,
        rules="429\nsupportops_429_rule_v1:tpm_reservation_exceeded"
        if mode == "wrong"
        else None
        if mode == "missing"
        else RULES,
    )
    if mode == "revoked":
        await knowledge_case.acl([])
    _, result, invocations, _ = await execute(case, "429 mock-s01-rpm")
    report = result["diagnostic"]
    assert report["status"] == "unconfirmed"
    assert report["candidates"][0]["code"] == "rpm_exceeded"
    assert report["candidates"][0]["confirmation"] == "unconfirmed"
    assert report["evidence_ids"] == [] and result["citations"] == []
    assert "DIAGNOSTIC_KNOWLEDGE_RULE_UNCONFIRMED" in report["reason_codes"]
    assert len(invocations) == 5 and result["escalation_status"] == "recommended"


@pytest.mark.parametrize(
    "content",
    [
        "429没有请求ID",
        "429 mock-s01-rpm mock-s02-tpm",
        "429 request_id:" + "r" * 129,
        "429 mock-s01-" + "r" * 120,
    ],
)
async def test_missing_or_ambiguous_request_only_asks_for_information(knowledge_case, content):
    case = await prepare(knowledge_case)
    _, result, invocations, _ = await execute(case, content)
    report = result["diagnostic"]
    assert report["status"] == "needs_information" and invocations == []
    assert report["facts"] == [] and report["candidates"] == []
    assert "DIAGNOSTIC_REQUEST_ID_REQUIRED" in report["reason_codes"]
    assert "请补充" in result["answer"] and report["escalation"] is None


async def test_unconfigured_real_looking_request_does_not_invent_connector_results(knowledge_case):
    case = await prepare(knowledge_case)
    _, result, invocations, _ = await execute(case, "429 request_id:real-looking-request")
    report = result["diagnostic"]
    assert report["request_id"] == "unconfigured_request"
    assert report["status"] == "unavailable" and report["facts"] == []
    assert "BUSINESS_CONNECTOR_UNCONFIGURED" in report["reason_codes"]
    assert len(invocations) == 4 and all(row.status == "failed" for row in invocations)


@pytest.mark.parametrize(
    "content",
    [
        "429 request_id:mock-s01-rpm",
        "429 请求ID：mock-s01-rpm",
        "429 request id=MOCK-S01-RPM",
    ],
)
async def test_explicit_request_labels_use_the_same_exact_scope_parser(knowledge_case, content):
    case = await prepare(knowledge_case)
    _, result, invocations, _ = await execute(case, content)
    assert result["diagnostic"]["status"] == "confirmed_mock"
    assert result["diagnostic"]["request_id"] == "mock-s01-rpm"
    assert len(invocations) == 5


@pytest.mark.parametrize(
    "content",
    [
        "429 request_id:mock-s01-rpm-other",
        "429 request_id:another:mock-s01-rpm",
        "429 request_id:mock-s02-tpm",
    ],
)
async def test_query_cannot_use_a_substring_or_unprovided_request(knowledge_case, content):
    case = await prepare(knowledge_case)
    run_id = await submit(case, content)
    async with create_session_factory(knowledge_case.engine)() as session:
        user = await session.get(User, knowledge_case.reader_id)
        identity = IdentityContext(
            Principal(user.id, knowledge_case.tenant_id, tuple(user.roles), user.display_name),
            user,
        )
        run = await session.get(AgentRun, run_id)
        response = await query_mock_diagnostic(
            session,
            identity=identity,
            run=run,
            tool_id="get_request_logs",
            query=DiagnosticQuery(request_id="mock-s01-rpm"),
            history_turns=10,
        )
    assert response.status == "unavailable" and response.facts == {}
    assert response.error_code == "DIAGNOSTIC_REQUEST_CONTEXT_REQUIRED"


async def test_revoked_rule_hides_completed_diagnostic_details(knowledge_case):
    case = await prepare(knowledge_case)
    run, result, invocations, _ = await execute(case, "429 mock-s01-rpm")
    assert result["diagnostic"]["status"] == "confirmed_mock"
    await knowledge_case.acl([])
    hidden = await knowledge_case.client.get(
        f"/v1/runs/{run.id}/diagnostic", headers=knowledge_case.reader_headers
    )
    assert hidden.status_code == 200 and hidden.json()["status"] == "unavailable"
    assert hidden.json()["facts"] == [] and hidden.json()["candidates"] == []
    assert hidden.json()["sources"] == [] and hidden.json()["escalation"] is None
    refreshed = await knowledge_case.client.get(
        f"/v1/conversations/{run.conversation_id}", headers=knowledge_case.reader_headers
    )
    projected = refreshed.json()["messages"][-1]["support_result"]
    assert projected["diagnostic"]["status"] == "unavailable"
    assert projected["citations"][0]["status"] == "unavailable"


async def test_policy_revoked_after_query_blocks_product_confirmation(knowledge_case, monkeypatch):
    case = await prepare(knowledge_case)
    original = ToolExecutionGateway._finish
    revoked = False

    async def revoke_after_finish(gateway, permit, **kwargs):
        nonlocal revoked
        await original(gateway, permit, **kwargs)
        if not revoked:
            revoked = True
            async with gateway.session_factory() as session, session.begin():
                catalog = await session.scalar(
                    select(ToolCatalogEntry).where(
                        ToolCatalogEntry.tenant_id == knowledge_case.tenant_id,
                        ToolCatalogEntry.tool_id == "get_request_logs",
                    )
                )
                catalog.is_revoked = True

    monkeypatch.setattr(ToolExecutionGateway, "_finish", revoke_after_finish)
    run_id = await submit(case, "429 mock-s01-rpm")
    factory = create_session_factory(knowledge_case.engine)
    assert not await worker_runtime.execute_agent_run(factory, run_id, settings=case.settings)
    assert revoked
    async with factory() as session:
        run = await session.get(AgentRun, run_id)
        assert run.status == RunStatus.FAILED
        assert (
            await session.scalar(
                select(func.count())
                .select_from(Message)
                .where(
                    Message.conversation_id == run.conversation_id,
                    Message.role == MessageRole.ASSISTANT,
                )
            )
            == 0
        )
        assert not await session.scalar(
            select(RunEvent.id)
            .where(RunEvent.run_id == run_id, RunEvent.event_type == "result.ready")
            .limit(1)
        )
