from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine
from supportops_core import worker_runtime
from supportops_core.config import Settings
from supportops_core.db import create_session_factory
from supportops_core.enums import RunStatus
from supportops_core.models import AgentRun

from tests.conftest import auth_headers
from tests.worker.test_worker import seed_run


@pytest.mark.anyio
async def test_dashboard_endpoints_return_empty_tenant_state(
    client: AsyncClient,
    engine: AsyncEngine,
    settings: Settings,
) -> None:
    tenant_id = uuid4()
    headers = await auth_headers(
        engine,
        settings,
        tenant_id,
        roles=("platform_admin",),
    )
    paths = (
        "/v1/admin/dashboard/summary",
        "/v1/admin/dashboard/timeseries",
        "/v1/admin/dashboard/runs?page=1&page_size=10",
        "/v1/admin/dashboard/errors",
        "/v1/admin/system/dashboard/summary",
        "/v1/admin/system/dashboard/timeseries",
        "/v1/admin/system/dashboard/agents/status?page=1&page_size=10",
        "/v1/admin/system/dashboard/rankings",
    )
    for path in paths:
        response = await client.get(path, headers=headers)
        assert response.status_code == 200, f"{path}: {response.text}"


@pytest.mark.anyio
async def test_error_event_list_and_detail_are_tenant_scoped(
    client: AsyncClient,
    engine: AsyncEngine,
    settings: Settings,
) -> None:
    run_id = await seed_run(engine, settings)
    session_factory = create_session_factory(engine)
    async with session_factory() as session, session.begin():
        run = await session.get(AgentRun, run_id)
        assert run is not None
        run.status = RunStatus.RUNNING
        run.started_at = run.created_at
        tenant_id = run.tenant_id
    await worker_runtime._fail_run(session_factory, run_id=run_id, error_code="AGENT_RUNTIME_ERROR")
    headers = await auth_headers(
        engine, settings, tenant_id, subject="worker-user", roles=("platform_admin",)
    )
    runs_response = await client.get(
        "/v1/admin/dashboard/runs?page=1&page_size=10", headers=headers
    )
    assert runs_response.status_code == 200, runs_response.text
    assert runs_response.json()["items"][0]["user_name"] == "worker-user"
    assert runs_response.json()["items"][0]["department_name"] is None
    response = await client.get("/v1/admin/analytics/errors?page=1&page_size=10", headers=headers)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["total"] == 1
    event_id = payload["items"][0]["id"]
    filtered = await client.get(
        "/v1/admin/analytics/errors?severity=error&error_code=AGENT_RUNTIME_ERROR&page=1&page_size=1",
        headers=headers,
    )
    assert filtered.status_code == 200
    assert filtered.json()["page_size"] == 1
    assert filtered.json()["items"][0]["error_code"] == "AGENT_RUNTIME_ERROR"
    detail = await client.get(f"/v1/admin/analytics/errors/{event_id}", headers=headers)
    assert detail.status_code == 200, detail.text
    assert detail.json()["error_code"] == "AGENT_RUNTIME_ERROR"
    other_headers = await auth_headers(engine, settings, uuid4(), roles=("platform_admin",))
    missing = await client.get(f"/v1/admin/analytics/errors/{event_id}", headers=other_headers)
    assert missing.status_code == 404


@pytest.mark.anyio
async def test_dashboard_metrics_keep_agent_scope_and_zero_usage_available(
    client: AsyncClient,
    engine: AsyncEngine,
    settings: Settings,
) -> None:
    run_id = await seed_run(engine, settings)
    session_factory = create_session_factory(engine)
    async with session_factory() as session, session.begin():
        run = await session.get(AgentRun, run_id)
        assert run is not None
        run.status = RunStatus.COMPLETED
        run.started_at = run.created_at
        run.finished_at = run.created_at
        run.input_tokens = 0
        run.output_tokens = 0
        tenant_id = run.tenant_id
        agent_id = run.agent_id
    headers = await auth_headers(
        engine,
        settings,
        tenant_id,
        subject="worker-user",
        roles=("platform_admin",),
    )
    await auth_headers(engine, settings, tenant_id, subject="unused-user")

    system_summary = await client.get("/v1/admin/system/dashboard/summary?days=7", headers=headers)
    agent_summary = await client.get(
        f"/v1/admin/dashboard/summary?days=7&agent_id={agent_id}", headers=headers
    )
    timeseries = await client.get(
        f"/v1/admin/dashboard/timeseries?days=7&agent_id={agent_id}", headers=headers
    )
    rankings = await client.get("/v1/admin/system/dashboard/rankings?days=7", headers=headers)

    assert system_summary.status_code == 200
    assert agent_summary.status_code == 200
    assert system_summary.json()["metrics"]["total_users"]["value"] == 2
    assert agent_summary.json()["metrics"]["total_users"]["value"] == 1
    populated_bucket = next(
        item for item in timeseries.json()["items"] if item["values"]["agent_runs"] == 1
    )
    assert populated_bucket["values"]["input_tokens"] == 0
    assert populated_bucket["values"]["output_tokens"] == 0
    assert set(rankings.json()["rankings"]) == {
        "agent_runs",
        "tokens",
        "cost_microusd",
        "errors",
    }
