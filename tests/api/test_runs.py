from uuid import UUID, uuid4

from httpx import AsyncClient
from pytest import MonkeyPatch
from sqlalchemy.ext.asyncio import AsyncEngine
from supportops_core import worker_runtime
from supportops_core.agent_config import SupportAnswer
from supportops_core.config import Settings
from supportops_core.db import create_session_factory

from tests.conftest import auth_headers, provision_agent


async def create_run(
    client: AsyncClient, settings: Settings, engine: AsyncEngine
) -> tuple[dict[str, str], UUID]:
    tenant_id = uuid4()
    headers = await auth_headers(engine, settings, tenant_id)
    agent_id = await provision_agent(engine, settings, tenant_id)
    conversation = await client.post(
        "/v1/conversations", json={"agent_id": str(agent_id)}, headers=headers
    )
    accepted = await client.post(
        f"/v1/conversations/{conversation.json()['id']}/messages",
        json={"content": "hello"},
        headers={**headers, "Idempotency-Key": "message-1"},
    )
    return headers, UUID(accepted.json()["run_id"])


async def test_cancel_queued_run_is_idempotent(
    client: AsyncClient, settings: Settings, engine: AsyncEngine
) -> None:
    headers, run_id = await create_run(client, settings, engine)

    first = await client.post(f"/v1/runs/{run_id}/cancel", headers=headers)
    second = await client.post(f"/v1/runs/{run_id}/cancel", headers=headers)

    assert first.status_code == 200
    assert first.json()["status"] == "cancelled"
    assert second.json()["status"] == "cancelled"


async def test_sse_replays_ordered_events(
    client: AsyncClient, settings: Settings, engine: AsyncEngine, monkeypatch: MonkeyPatch
) -> None:
    headers, run_id = await create_run(client, settings, engine)

    async def fake_invoke(*args: object, **kwargs: object) -> SupportAnswer:
        return SupportAnswer(answer="测试回答")

    monkeypatch.setattr(worker_runtime, "invoke_agent", fake_invoke)
    processed = await worker_runtime.execute_agent_run(
        create_session_factory(engine), run_id, settings=settings
    )
    assert processed is True

    response = await client.get(f"/v1/runs/{run_id}/events?cursor=0", headers=headers)

    assert response.status_code == 200
    body = response.text
    assert "event: run.queued" in body
    assert "event: run.started" in body
    assert "event: text.delta" in body
    assert "event: run.completed" in body
    ids = [int(line.removeprefix("id: ")) for line in body.splitlines() if line.startswith("id: ")]
    assert ids == sorted(ids)
    assert len(ids) == len(set(ids))

    replay = await client.get(f"/v1/runs/{run_id}/events?cursor={ids[-2]}", headers=headers)
    replay_ids = [
        int(line.removeprefix("id: "))
        for line in replay.text.splitlines()
        if line.startswith("id: ")
    ]
    assert replay_ids == [ids[-1]]
