from uuid import UUID, uuid4

from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine
from supportops_core.config import Settings
from supportops_core.models import AgentRun, Message, RunEvent

from tests.conftest import FakeRedis, auth_headers, provision_agent


async def conversation_context(
    engine: AsyncEngine,
    settings: Settings,
    *,
    tenant_id: UUID | None = None,
    subject: str = "user-1",
) -> tuple[dict[str, str], UUID]:
    active_tenant_id = tenant_id or uuid4()
    agent_id = await provision_agent(engine, settings, active_tenant_id, subject=subject)
    return await auth_headers(engine, settings, active_tenant_id, subject), agent_id


async def test_authentication_is_required(client: AsyncClient) -> None:
    response = await client.post("/v1/conversations", json={})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "HTTP_401"


async def test_health_and_correlation_id(client: AsyncClient) -> None:
    live = await client.get("/health/live", headers={"X-Correlation-ID": "test-request"})
    ready = await client.get("/health/ready")

    assert live.status_code == 200
    assert live.headers["X-Correlation-ID"] == "test-request"
    assert ready.status_code == 200
    assert ready.json()["dependencies"] == {"postgresql": "ok", "redis": "ok"}


async def test_conversation_is_tenant_scoped(
    client: AsyncClient, settings: Settings, engine: AsyncEngine
) -> None:
    owner, agent_id = await conversation_context(engine, settings, subject="owner")
    other = await auth_headers(engine, settings, uuid4(), "other")
    created = await client.post(
        "/v1/conversations", json={"agent_id": str(agent_id), "title": "429"}, headers=owner
    )
    assert created.status_code == 201

    hidden = await client.get(f"/v1/conversations/{created.json()['id']}", headers=other)
    assert hidden.status_code == 404


async def test_conversation_list_is_user_scoped_and_newest_first(
    client: AsyncClient, settings: Settings, engine: AsyncEngine
) -> None:
    tenant_id = uuid4()
    owner = await auth_headers(engine, settings, tenant_id, "owner")
    teammate = await auth_headers(engine, settings, tenant_id, "teammate")
    agent_id = await provision_agent(engine, settings, tenant_id, subject="owner")
    payload = {"agent_id": str(agent_id)}
    first = await client.post(
        "/v1/conversations", json={**payload, "title": "第一条"}, headers=owner
    )
    second = await client.post(
        "/v1/conversations", json={**payload, "title": "第二条"}, headers=owner
    )
    await client.post("/v1/conversations", json={**payload, "title": "其他用户"}, headers=teammate)

    response = await client.get(
        "/v1/conversations", params={"agent_id": str(agent_id)}, headers=owner
    )
    second_page = await client.get(
        "/v1/conversations",
        params={"agent_id": str(agent_id), "page": 2, "page_size": 1},
        headers=owner,
    )

    assert response.status_code == 200
    assert [item["id"] for item in response.json()["items"]] == [
        second.json()["id"],
        first.json()["id"],
    ]
    assert all("messages" not in item for item in response.json()["items"])
    assert response.json()["total"] == 2
    assert response.json()["page"] == 1
    assert response.json()["page_size"] == 20
    assert response.json()["pages"] == 1
    assert second_page.json()["items"][0]["id"] == first.json()["id"]
    assert second_page.json()["total"] == 2
    assert second_page.json()["page"] == 2
    assert second_page.json()["page_size"] == 1
    assert second_page.json()["pages"] == 2


async def test_conversation_can_be_renamed_and_pinned(
    client: AsyncClient, settings: Settings, engine: AsyncEngine
) -> None:
    headers, agent_id = await conversation_context(engine, settings)
    payload = {"agent_id": str(agent_id)}
    pinned = await client.post(
        "/v1/conversations", json={**payload, "title": "原名称"}, headers=headers
    )
    latest = await client.post(
        "/v1/conversations", json={**payload, "title": "较新会话"}, headers=headers
    )

    renamed = await client.patch(
        f"/v1/conversations/{pinned.json()['id']}",
        json={"title": "  生产事故复盘  ", "is_pinned": True},
        headers=headers,
    )
    history = await client.get(
        "/v1/conversations", params={"agent_id": str(agent_id)}, headers=headers
    )

    assert renamed.status_code == 200
    assert renamed.json()["title"] == "生产事故复盘"
    assert renamed.json()["is_pinned"] is True
    assert [item["id"] for item in history.json()["items"]] == [
        pinned.json()["id"],
        latest.json()["id"],
    ]


async def test_conversation_update_and_delete_are_user_scoped(
    client: AsyncClient, settings: Settings, engine: AsyncEngine
) -> None:
    tenant_id = uuid4()
    owner = await auth_headers(engine, settings, tenant_id, "owner")
    teammate = await auth_headers(engine, settings, tenant_id, "teammate")
    agent_id = await provision_agent(engine, settings, tenant_id, subject="owner")
    created = await client.post(
        "/v1/conversations",
        json={"agent_id": str(agent_id), "title": "私有会话"},
        headers=owner,
    )
    path = f"/v1/conversations/{created.json()['id']}"

    rename = await client.patch(path, json={"title": "越权重命名"}, headers=teammate)
    deletion = await client.delete(path, headers=teammate)

    assert rename.status_code == 404
    assert deletion.status_code == 404
    assert (await client.get(path, headers=owner)).status_code == 200


async def test_conversation_delete_removes_related_records(
    client: AsyncClient,
    settings: Settings,
    engine: AsyncEngine,
) -> None:
    headers, agent_id = await conversation_context(engine, settings)
    created = await client.post(
        "/v1/conversations",
        json={"agent_id": str(agent_id), "title": "待删除"},
        headers=headers,
    )
    conversation_id = created.json()["id"]
    conversation_uuid = UUID(conversation_id)
    accepted = await client.post(
        f"/v1/conversations/{conversation_id}/messages",
        json={"content": "测试删除"},
        headers={**headers, "Idempotency-Key": "delete-test"},
    )

    deleted = await client.delete(f"/v1/conversations/{conversation_id}", headers=headers)

    assert accepted.status_code == 202
    assert deleted.status_code == 204
    assert deleted.content == b""
    missing = await client.get(f"/v1/conversations/{conversation_id}", headers=headers)
    assert missing.status_code == 404
    async with engine.connect() as connection:
        assert (
            await connection.scalar(
                select(func.count())
                .select_from(Message)
                .where(Message.conversation_id == conversation_uuid)
            )
            == 0
        )
        assert (
            await connection.scalar(
                select(func.count())
                .select_from(AgentRun)
                .where(AgentRun.conversation_id == conversation_uuid)
            )
            == 0
        )
        assert (
            await connection.scalar(
                select(func.count())
                .select_from(RunEvent)
                .where(RunEvent.run_id == UUID(accepted.json()["run_id"]))
            )
            == 0
        )


async def test_conversation_update_rejects_empty_payload(
    client: AsyncClient, settings: Settings, engine: AsyncEngine
) -> None:
    headers, agent_id = await conversation_context(engine, settings)
    created = await client.post(
        "/v1/conversations", json={"agent_id": str(agent_id)}, headers=headers
    )

    response = await client.patch(
        f"/v1/conversations/{created.json()['id']}", json={"title": "   "}, headers=headers
    )

    assert response.status_code == 422


async def test_message_submission_is_idempotent(
    client: AsyncClient, settings: Settings, fake_redis: FakeRedis, engine: AsyncEngine
) -> None:
    headers, agent_id = await conversation_context(engine, settings)
    created = await client.post(
        "/v1/conversations", json={"agent_id": str(agent_id)}, headers=headers
    )
    conversation_id = created.json()["id"]
    request_headers = {**headers, "Idempotency-Key": "same-message"}

    first = await client.post(
        f"/v1/conversations/{conversation_id}/messages",
        json={"content": "为什么返回 429？"},
        headers=request_headers,
    )
    second = await client.post(
        f"/v1/conversations/{conversation_id}/messages",
        json={"content": "为什么返回 429？"},
        headers=request_headers,
    )

    assert first.status_code == 202
    assert second.status_code == 202
    assert first.json()["message_id"] == second.json()["message_id"]
    assert first.json()["run_id"] == second.json()["run_id"]
    assert len(fake_redis.queues[settings.redis_queue_name]) == 1


async def test_idempotency_key_reuse_with_different_content_conflicts(
    client: AsyncClient, settings: Settings, engine: AsyncEngine
) -> None:
    headers, agent_id = await conversation_context(engine, settings)
    conversation = await client.post(
        "/v1/conversations", json={"agent_id": str(agent_id)}, headers=headers
    )
    path = f"/v1/conversations/{conversation.json()['id']}/messages"
    request_headers = {**headers, "Idempotency-Key": "reused"}
    await client.post(path, json={"content": "first"}, headers=request_headers)

    response = await client.post(path, json={"content": "second"}, headers=request_headers)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
