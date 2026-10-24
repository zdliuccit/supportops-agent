"""Dashboard 管理权限、数据库分页及跨接口范围；只构造隔离历史事实。"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine
from supportops_core.db import create_session_factory
from supportops_core.enums import AgentStatus, MessageRole, RunStatus
from supportops_core.models import (
    Agent,
    AgentErrorEvent,
    AgentRun,
    AgentRunObservation,
    AgentRuntimeHealth,
    AgentVersion,
    Conversation,
    Message,
    ModelEndpointVersion,
    OrganizationUnit,
    User,
    utc_now,
)

from tests.conftest import auth_headers, provision_agent

ROOT = "/v1/admin/"
COLLECTIONS = (
    "dashboard/summary",
    "dashboard/timeseries",
    "dashboard/runs",
    "dashboard/errors",
    "system/dashboard/summary",
    "system/dashboard/timeseries",
    "system/dashboard/agents/status",
    "system/dashboard/rankings",
    "analytics/errors",
)
PAGED = ("dashboard/runs", "analytics/errors", "system/dashboard/agents/status")


@dataclass
class DashboardCase:
    client: AsyncClient
    engine: AsyncEngine
    headers: dict[str, str]
    tenant_id: UUID
    agents: list[Agent]
    versions: list[AgentVersion]
    users: list[User]
    departments: list[OrganizationUnit]
    runs: list[AgentRun]
    errors: list[AgentErrorEvent]
    start: datetime
    end: datetime
    foreign_run: AgentRun
    foreign_error: AgentErrorEvent

    @property
    def params(self):
        return {"start": self.start.isoformat(), "end": self.end.isoformat()}

    async def get(self, path, **params):
        response = await self.client.get(ROOT + path, headers=self.headers, params=params)
        assert response.status_code == 200, response.text
        return response.json()


async def historical_run(session, agent, version, user, when, *, failed=False, known=True):
    """事实夹具不执行 Worker，不把聚合验收冒充模型成功验收。"""
    conversation = Conversation(
        tenant_id=agent.tenant_id,
        user_id=user.id,
        agent_id=agent.id,
        agent_version_id=version.id,
        created_at=when,
    )
    session.add(conversation)
    await session.flush()
    message = Message(
        tenant_id=agent.tenant_id,
        conversation_id=conversation.id,
        user_id=user.id,
        role=MessageRole.USER,
        content="隔离统计样本",
        content_hash="a" * 64,
        idempotency_key=str(uuid4()),
        correlation_id=str(uuid4()),
    )
    session.add(message)
    await session.flush()
    run = AgentRun(
        tenant_id=agent.tenant_id,
        conversation_id=conversation.id,
        agent_id=agent.id,
        agent_version_id=version.id,
        model_endpoint_version_id=version.model_endpoint_version_id,
        credential_revision=1,
        config_digest=version.config_digest,
        input_message_id=message.id,
        status=RunStatus.FAILED if failed else RunStatus.COMPLETED,
        correlation_id=str(uuid4()),
        created_at=when,
        started_at=when,
        finished_at=when + timedelta(seconds=1),
        error_code="AGENT_RUNTIME_ERROR" if failed else None,
        input_tokens=0 if known else None,
        output_tokens=2 if known else None,
        total_cost_microusd=0 if known else None,
        end_to_end_latency_ms=100,
        tool_call_count=0,
    )
    session.add(run)
    await session.flush()
    event = None
    if failed:
        event = AgentErrorEvent(
            tenant_id=agent.tenant_id,
            run_id=run.id,
            conversation_id=conversation.id,
            agent_id=agent.id,
            agent_version_id=version.id,
            model_endpoint_version_id=version.model_endpoint_version_id,
            user_id=user.id,
            occurred_at=when,
            error_code="AGENT_RUNTIME_ERROR",
            reason="隔离失败样本",
        )
        session.add(event)
        await session.flush()
    return run, event


@pytest.fixture
async def dashboard_case(client, engine, settings):
    tenant_id, other_id = uuid4(), uuid4()
    first_id = await provision_agent(engine, settings, tenant_id)
    foreign_id = await provision_agent(engine, settings, other_id)
    headers = await auth_headers(
        engine, settings, tenant_id, "dashboard-admin", roles=("platform_admin",)
    )
    await auth_headers(engine, settings, tenant_id, "second-user")
    factory = create_session_factory(engine)
    async with factory() as session, session.begin():
        first = await session.get(Agent, first_id)
        first.name = "同名 Agent"
        version = await session.get(AgentVersion, first.active_version_id)
        model = await session.get(ModelEndpointVersion, version.model_endpoint_version_id)
        second_model = ModelEndpointVersion(
            tenant_id=tenant_id,
            endpoint_id=model.endpoint_id,
            version_number=2,
            provider_kind=model.provider_kind,
            api_protocol=model.api_protocol,
            base_url=model.base_url,
            remote_model_name="second-test-model",
            credential_id=model.credential_id,
            created_by=model.created_by,
        )
        session.add(second_model)
        second = Agent(
            tenant_id=tenant_id,
            slug="second-agent",
            name=first.name,
            status=AgentStatus.DISABLED,
            created_by=first.created_by,
            updated_by=first.updated_by,
        )
        session.add(second)
        await session.flush()
        second_version = AgentVersion(
            tenant_id=tenant_id,
            agent_id=second.id,
            version_number=1,
            schema_version=version.schema_version,
            config=version.config,
            config_digest=version.config_digest,
            model_endpoint_version_id=second_model.id,
            published_by=version.published_by,
        )
        session.add(second_version)
        await session.flush()
        second.active_version_id = second_version.id
        users = list(
            (
                await session.scalars(
                    select(User)
                    .where(
                        User.tenant_id == tenant_id,
                        User.display_name.in_(("user-1", "second-user")),
                    )
                    .order_by(User.display_name)
                )
            ).all()
        )
        departments = [OrganizationUnit(tenant_id=tenant_id, name=f"部门{i}") for i in range(2)]
        session.add_all(departments)
        await session.flush()
        for user, department in zip(users, departments, strict=True):
            user.organization_unit_id = department.id
        start, end = utc_now() - timedelta(hours=2), utc_now() - timedelta(hours=1)
        agents, versions, runs, errors = [first, second], [version, second_version], [], []
        session.add(
            AgentRuntimeHealth(
                tenant_id=tenant_id,
                agent_id=first.id,
                health_status="healthy",
                health_reason="隔离快照",
                observed_at=utc_now(),
                computed_at=utc_now(),
            )
        )
        for index in range(6):
            which = int(index >= 3)
            run, event = await historical_run(
                session,
                agents[which],
                versions[which],
                users[which],
                start,
                failed=index in (1, 2, 3, 4),
                known=index != 5,
            )
            runs.append(run)
            if event:
                errors.append(event)
        # 左边界之前和右边界本身均不得进入窗口。
        for when in (start - timedelta(microseconds=1), end):
            await historical_run(session, first, version, users[0], when, failed=True)
        foreign = await session.get(Agent, foreign_id)
        foreign_version = await session.get(AgentVersion, foreign.active_version_id)
        foreign_user = await session.get(User, foreign.created_by)
        foreign_run, foreign_error = await historical_run(
            session, foreign, foreign_version, foreign_user, start, failed=True
        )
        # 同 Run 上错误归属的观测也不能穿过租户过滤。
        for tenant, name in ((tenant_id, "本租户观测"), (other_id, "外租户观测")):
            session.add(
                AgentRunObservation(
                    tenant_id=tenant,
                    run_id=runs[0].id,
                    trace_id=str(uuid4()),
                    span_id=str(uuid4()),
                    kind="agent",
                    name=name,
                    status="completed",
                    started_at=start,
                )
            )
    return DashboardCase(
        client,
        engine,
        headers,
        tenant_id,
        agents,
        versions,
        users,
        departments,
        runs,
        errors,
        start,
        end,
        foreign_run,
        foreign_error,
    )


@pytest.mark.parametrize("role", [None, "customer", "employee", "agent_user"])
async def test_all_dashboard_routes_require_current_administrator(client, engine, settings, role):
    headers = {} if role is None else await auth_headers(engine, settings, uuid4(), roles=(role,))
    paths = (
        *COLLECTIONS,
        f"dashboard/runs/{uuid4()}/trace",
        f"dashboard/runs/{uuid4()}/actions",
        f"analytics/errors/{uuid4()}",
    )
    for path in paths:
        response = await client.get(ROOT + path, headers=headers)
        assert response.status_code == (401 if role is None else 403), (path, response.text)


@pytest.mark.parametrize("path", PAGED)
async def test_database_pages_have_stable_ties_totals_and_out_of_range(dashboard_case, path):
    case = dashboard_case
    expected = {
        "dashboard/runs": sorted((str(run.id) for run in case.runs), reverse=True),
        "analytics/errors": sorted((str(event.id) for event in case.errors), reverse=True),
        "system/dashboard/agents/status": sorted(str(agent.id) for agent in case.agents),
    }[path]
    collected = []
    for page in range(1, len(expected) + 1):
        body = await case.get(path, **case.params, page=page, page_size=1)
        assert (body["total"], body["pages"], body["page"], body["page_size"]) == (
            len(expected),
            len(expected),
            page,
            1,
        )
        collected.append(body["items"][0]["id"])
    assert collected == expected
    assert (await case.get(path, **case.params, page=99, page_size=1))["items"] == []
    assert (await case.get(path, **case.params, page_size=100))["total"] == len(expected)


@pytest.mark.parametrize(
    "params", [{"page": 0}, {"page": -1}, {"page_size": 0}, {"page_size": 101}, {"page": "x"}]
)
async def test_pagination_boundaries_are_validated(client, engine, settings, params):
    headers = await auth_headers(engine, settings, uuid4(), roles=("platform_admin",))
    for path in (*PAGED, f"dashboard/runs/{uuid4()}/actions"):
        response = await client.get(ROOT + path, headers=headers, params=params)
        assert response.status_code == 422, (path, response.text)


@pytest.mark.parametrize(
    "dimension",
    [
        "all",
        "agent_id",
        "user_id",
        "organization_unit_id",
        "agent_version_id",
        "model_endpoint_version_id",
        "status",
        "lifecycle",
        "health",
        "combined",
    ],
)
async def test_summary_trends_rankings_lists_share_exact_scope(dashboard_case, dimension):
    case = dashboard_case
    choices = {
        "agent_id": str(case.agents[0].id),
        "user_id": str(case.users[0].id),
        "organization_unit_id": str(case.departments[0].id),
        "agent_version_id": str(case.versions[0].id),
        "model_endpoint_version_id": str(case.versions[0].model_endpoint_version_id),
        "status": "failed",
        "lifecycle": "active",
        "health": "healthy",
    }
    params = dict(case.params)
    if dimension == "combined":
        params.update({key: value for key, value in choices.items() if key != "health"})
    elif dimension != "all":
        params[dimension] = choices[dimension]
    expected = (
        case.runs
        if dimension == "all"
        else (
            [run for run in case.runs if run.status == RunStatus.FAILED]
            if dimension == "status"
            else case.runs[:3]
        )
    )
    if dimension == "combined":
        expected = [run for run in expected if run.status == RunStatus.FAILED]
    failed = sum(run.status == RunStatus.FAILED for run in expected)
    listing = await case.get("dashboard/runs", **params)
    assert set(row["id"] for row in listing["items"]) == {str(run.id) for run in expected}
    assert listing["total"] == len(expected)
    summary = await case.get("system/dashboard/summary", **params)
    alias = await case.get("dashboard/summary", **params)
    assert summary == alias
    metrics = summary["metrics"]
    assert metrics["agent_runs"]["value"] == len(expected)
    assert metrics["error_count"]["value"] == failed
    assert metrics["active_users"]["value"] == len({run.agent_id for run in expected})
    trend = await case.get("system/dashboard/timeseries", **params)
    assert sum(row["values"]["agent_runs"] for row in trend["items"]) == len(expected)
    assert sum(row["values"]["error_count"] for row in trend["items"]) == failed
    rankings = (await case.get("system/dashboard/rankings", **params))["rankings"]
    assert sum(row["value"] for row in rankings["agent_runs"]) == len(expected)
    assert sum(row["value"] for row in rankings["errors"]) == failed
    groups = await case.get("dashboard/errors", **params)
    assert sum(row["count"] for row in groups["items"]) == failed
    error_params = {
        "run_status" if key == "status" else key: value for key, value in params.items()
    }
    events = await case.get("analytics/errors", **error_params)
    assert events["total"] == failed
    assert {row["run_id"] for row in events["items"]} == {
        str(run.id) for run in expected if run.status == RunStatus.FAILED
    }
    state = await case.get("system/dashboard/agents/status", **params)
    assert state["total"] == summary["status_counts"]["total"]


async def test_known_zero_and_missing_usage_are_not_interchangeable(dashboard_case):
    case = dashboard_case
    for index in (0, 1):
        params = {**case.params, "agent_id": str(case.agents[index].id)}
        metrics = (await case.get("dashboard/summary", **params))["metrics"]
        assert metrics["input_tokens"]["value"] == (0 if index == 0 else None)
        assert metrics["input_tokens"]["available"] is (index == 0)
        assert metrics["input_tokens"]["missing_count"] == index
        assert metrics["p95_latency_ms"]["value"] == 100
        rankings = (await case.get("system/dashboard/rankings", **params))["rankings"]
        assert rankings["tokens"][0]["value"] == (6 if index == 0 else None)
        assert rankings["cost_microusd"][0]["value"] == (0 if index == 0 else None)


@pytest.mark.parametrize("path", PAGED)
async def test_filtered_pages_count_only_matching_tenant_rows(dashboard_case, path):
    case = dashboard_case
    params = {**case.params, "agent_id": str(case.agents[0].id)}
    expected = {"dashboard/runs": 3, "analytics/errors": 2, "system/dashboard/agents/status": 1}[
        path
    ]
    first = await case.get(path, **params, page_size=1)
    assert first["total"] == first["pages"] == expected
    assert len(first["items"]) == 1
    last = await case.get(path, **params, page_size=1, page=expected + 1)
    assert last["items"] == [] and last["total"] == expected
    empty = await case.get(path, **params, user_id=str(uuid4()), page=2)
    assert empty["items"] == [] and empty["total"] == empty["pages"] == 0


@pytest.mark.parametrize(
    "params,expected",
    [
        ({"severity": "critical"}, 1),
        ({"status": "resolved"}, 1),
        ({"error_code": "OTHER_TEST_ERROR"}, 1),
        ({"severity": "critical", "status": "unresolved"}, 0),
    ],
)
async def test_error_specific_filters_and_detail_keep_original_run(
    dashboard_case, params, expected
):
    case = dashboard_case
    async with create_session_factory(case.engine)() as session, session.begin():
        event = await session.get(AgentErrorEvent, case.errors[0].id)
        event.severity, event.resolution_status = "critical", "resolved"
        event.error_code = "OTHER_TEST_ERROR"
    body = await case.get("analytics/errors", **case.params, **params, page_size=1)
    assert body["total"] == expected
    if expected:
        row = body["items"][0]
        assert row["id"] == str(case.errors[0].id)
        detail = await case.get(f"analytics/errors/{row['id']}")
        assert detail["run_id"] == str(case.errors[0].run_id)
        assert detail["agent_id"] == str(case.agents[0].id)
        assert detail["user_name"] == case.users[0].display_name


async def test_explicit_timezone_and_bucket_boundaries_preserve_selected_window(dashboard_case):
    from datetime import timezone

    case = dashboard_case
    offset = timezone(timedelta(hours=8))
    params = {
        "start": case.start.astimezone(offset).isoformat(),
        "end": case.end.astimezone(offset).isoformat(),
    }
    trend = await case.get("dashboard/timeseries", **params)
    alias = await case.get("system/dashboard/timeseries", **params)
    assert trend == alias

    def parse(value):
        return datetime.fromisoformat(value)

    assert parse(trend["items"][0]["bucket_start"]) == case.start
    assert parse(trend["items"][-1]["bucket_end"]) == case.end
    for left, right in zip(trend["items"], trend["items"][1:], strict=False):
        assert left["bucket_end"] == right["bucket_start"]
    assert sum(item["values"]["agent_runs"] for item in trend["items"]) == 6


async def test_empty_tenant_has_precise_zero_null_and_empty_pages(client, engine, settings):
    headers = await auth_headers(engine, settings, uuid4(), roles=("platform_admin",))
    summary = (await client.get(ROOT + "system/dashboard/summary", headers=headers)).json()
    assert summary["status_counts"]["total"] == 0
    for key in (
        "agent_runs",
        "active_users",
        "conversations",
        "error_count",
        "input_tokens",
        "output_tokens",
    ):
        assert summary["metrics"][key]["value"] == 0
    for key in ("success_rate", "p50_latency_ms", "p95_latency_ms", "p99_latency_ms"):
        assert summary["metrics"][key]["value"] is None
    for path in PAGED:
        response = await client.get(
            ROOT + path, headers=headers, params={"page": 4, "page_size": 2}
        )
        assert response.status_code == 200
        assert response.json() == {"items": [], "total": 0, "page": 4, "page_size": 2, "pages": 0}
    rankings = (await client.get(ROOT + "system/dashboard/rankings", headers=headers)).json()
    assert all(not items for items in rankings["rankings"].values())


async def test_foreign_details_are_hidden_and_trace_observations_are_tenant_scoped(dashboard_case):
    case = dashboard_case
    for path in (
        f"dashboard/runs/{case.foreign_run.id}/trace",
        f"dashboard/runs/{case.foreign_run.id}/actions",
        f"analytics/errors/{case.foreign_error.id}",
    ):
        response = await case.client.get(ROOT + path, headers=case.headers)
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"
    trace = await case.get(f"dashboard/runs/{case.runs[0].id}/trace")
    assert [row["name"] for row in trace["observations"]] == ["本租户观测"]


async def test_cross_tenant_and_unknown_agent_filters_are_rejected_consistently(dashboard_case):
    case = dashboard_case
    for agent_id in (case.foreign_run.agent_id, uuid4()):
        for path in COLLECTIONS:
            response = await case.client.get(
                ROOT + path, headers=case.headers, params={**case.params, "agent_id": str(agent_id)}
            )
            assert response.status_code == 404, (path, response.text)


async def test_soft_deleted_history_still_supports_list_to_trace_drilldown(dashboard_case):
    case = dashboard_case
    async with create_session_factory(case.engine)() as session, session.begin():
        conversation = await session.get(Conversation, case.runs[0].conversation_id)
        agent = await session.get(Agent, case.agents[0].id)
        conversation.deleted_at = agent.deleted_at = utc_now()
    listing = await case.get("dashboard/runs", **case.params, agent_id=str(case.agents[0].id))
    assert listing["total"] == 3
    trace = await case.get(f"dashboard/runs/{case.runs[0].id}/trace")
    assert trace["run"]["id"] == str(case.runs[0].id)


@pytest.mark.parametrize(
    "params",
    [
        {"days": 0},
        {"days": 91},
        {"agent_id": "invalid"},
        {"health": "green"},
        {"lifecycle": "running"},
        {"user_id": "bad"},
        {"start": "2026-10-20T00:00:00Z"},
        {"start": "2026-10-20T00:00:00", "end": "2026-10-21T00:00:00"},
        {"start": "2026-10-21T00:00:00Z", "end": "2026-10-20T00:00:00Z"},
        {"start": "2026-01-01T00:00:00Z", "end": "2026-10-20T00:00:00Z"},
    ],
)
async def test_invalid_scopes_never_fall_back_to_unfiltered_query(client, engine, settings, params):
    headers = await auth_headers(engine, settings, uuid4(), roles=("platform_admin",))
    for path in COLLECTIONS:
        response = await client.get(ROOT + path, headers=headers, params=params)
        assert response.status_code == 422, (path, response.text)
