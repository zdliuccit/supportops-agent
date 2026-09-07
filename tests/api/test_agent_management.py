from uuid import UUID, uuid4

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker
from supportops_core.config import Settings
from supportops_core.enums import ModelEndpointStatus, ModelVerificationStatus
from supportops_core.models import (
    Agent,
    AgentAuditEvent,
    ModelCredential,
    ModelCredentialRevision,
    ModelEndpoint,
    ModelEndpointVersion,
)

from tests.conftest import auth_headers


def endpoint_payload() -> dict[str, object]:
    return {
        "name": "OpenAI 官方",
        "api_key": "sk-test-only-not-a-real-key",
        "version": {
            "provider_kind": "openai_official",
            "api_protocol": "responses",
            "base_url": "https://api.openai.com/v1",
            "remote_model_name": "gpt-test",
            "capabilities": {
                "streaming": True,
                "tool_calling": True,
                "structured_output": True,
                "parallel_tool_calls": True,
                "vision": False,
            },
        },
    }


def agent_payload(endpoint_id: str) -> dict[str, object]:
    return {
        "slug": "technical-support",
        "name": "企业技术支持 Agent",
        "description": "处理 AI/API 技术支持问题",
        "welcome_message": "请描述问题",
        "suggested_prompts": ["为什么 API 返回 429？"],
        "config": {
            "schema_version": "2",
            "prompt": {"system_prompt": "你是企业技术支持 Agent。"},
            "model": {"model_endpoint_id": endpoint_id},
            "tools": [],
            "runtime": {"response_strategy": "tool"},
        },
    }


async def mark_endpoint_verified(engine: AsyncEngine, endpoint_id: UUID) -> None:
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session, session.begin():
        endpoint = await session.get(ModelEndpoint, endpoint_id)
        assert endpoint is not None and endpoint.active_version_id is not None
        version = await session.get(ModelEndpointVersion, endpoint.active_version_id)
        assert version is not None
        endpoint.status = ModelEndpointStatus.ACTIVE
        version.verification_status = ModelVerificationStatus.VERIFIED
        version.verification_result = {"model": "passed"}


async def test_admin_agent_lifecycle_and_employee_catalog(
    client: AsyncClient, settings: Settings, engine: AsyncEngine
) -> None:
    tenant_id = uuid4()
    admin = await auth_headers(
        engine,
        settings,
        tenant_id,
        "admin",
        roles=("platform_admin", "employee"),
    )
    employee = await auth_headers(engine, settings, tenant_id, "employee", roles=("employee",))

    endpoint = await client.post(
        "/v1/admin/model-endpoints", json=endpoint_payload(), headers=admin
    )
    assert endpoint.status_code == 201
    assert "api_key" not in endpoint.text
    endpoint_id = endpoint.json()["id"]
    await mark_endpoint_verified(engine, UUID(endpoint_id))

    created = await client.post("/v1/admin/agents", json=agent_payload(endpoint_id), headers=admin)
    assert created.status_code == 201
    agent_id = created.json()["id"]
    assert created.json()["status"] == "draft"
    assert created.json()["read_only"] is False

    draft = await client.get(f"/v1/admin/agents/{agent_id}/draft", headers=admin)
    published = await client.post(
        f"/v1/admin/agents/{agent_id}/versions",
        json={"expected_revision": draft.json()["revision"], "activate": True},
        headers=admin,
    )
    assert published.status_code == 200
    await client.put(
        f"/v1/admin/agents/{agent_id}/grants",
        json={"grants": [{"subject_type": "role", "subject_id": "employee"}]},
        headers=admin,
    )

    catalog = await client.get("/v1/agents", headers=employee)
    assert [item["id"] for item in catalog.json()["items"]] == [agent_id]
    conversation = await client.post(
        "/v1/conversations",
        json={"agent_id": agent_id, "title": "API 429"},
        headers=employee,
    )
    assert conversation.status_code == 201
    assert conversation.json()["agent_id"] == agent_id
    assert conversation.json()["agent_version_id"] == published.json()["id"]


async def test_management_is_forbidden_and_agent_access_defaults_to_deny(
    client: AsyncClient, settings: Settings, engine: AsyncEngine
) -> None:
    tenant_id = uuid4()
    employee = await auth_headers(engine, settings, tenant_id, roles=("employee",))

    denied = await client.get("/v1/admin/agents", headers=employee)
    catalog = await client.get("/v1/agents", headers=employee)
    missing_agent = await client.post(
        "/v1/conversations",
        json={"agent_id": str(uuid4())},
        headers=employee,
    )

    assert denied.status_code == 403
    assert catalog.json() == {
        "items": [],
        "total": 0,
        "page": 1,
        "page_size": 20,
        "pages": 0,
    }
    assert missing_agent.status_code == 404


async def test_admin_agent_list_supports_status_filter_and_page_pagination(
    client: AsyncClient, settings: Settings, engine: AsyncEngine
) -> None:
    tenant_id = uuid4()
    admin = await auth_headers(engine, settings, tenant_id, roles=("platform_admin",))
    endpoint = await client.post(
        "/v1/admin/model-endpoints", json=endpoint_payload(), headers=admin
    )
    endpoint_id = endpoint.json()["id"]
    created_ids: set[str] = set()
    for index in range(3):
        payload = agent_payload(endpoint_id)
        payload["slug"] = f"support-agent-{index}"
        payload["name"] = f"支持 Agent {index}"
        created = await client.post("/v1/admin/agents", json=payload, headers=admin)
        assert created.status_code == 201
        created_ids.add(created.json()["id"])

    first_page = await client.get(
        "/v1/admin/agents?status=draft&page=1&page_size=2", headers=admin
    )
    second_page = await client.get(
        "/v1/admin/agents?status=draft&page=2&page_size=2", headers=admin
    )
    overflow_page = await client.get(
        "/v1/admin/agents?status=draft&page=99&page_size=2", headers=admin
    )
    invalid = await client.get("/v1/admin/agents?status=unknown", headers=admin)
    invalid_page = await client.get("/v1/admin/agents?page=0&page_size=101", headers=admin)

    assert first_page.status_code == 200
    assert first_page.json()["total"] == 3
    assert first_page.json()["page"] == 1
    assert first_page.json()["page_size"] == 2
    assert first_page.json()["pages"] == 2
    assert len(first_page.json()["items"]) == 2
    assert all(item["status"] == "draft" for item in first_page.json()["items"])
    assert second_page.json()["total"] == 3
    assert second_page.json()["page"] == 2
    assert len(second_page.json()["items"]) == 1
    assert overflow_page.json()["items"] == []
    assert overflow_page.json()["total"] == 3
    assert overflow_page.json()["page"] == 99
    assert overflow_page.json()["pages"] == 2
    returned_ids = {
        item["id"] for page in (first_page, second_page) for item in page.json()["items"]
    }
    assert returned_ids == created_ids
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "VALIDATION_ERROR"
    assert invalid_page.status_code == 422
    assert invalid_page.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_agent_draft_revision_conflict_preserves_current_revision(
    client: AsyncClient, settings: Settings, engine: AsyncEngine
) -> None:
    tenant_id = uuid4()
    admin = await auth_headers(engine, settings, tenant_id, roles=("platform_admin",))
    endpoint = await client.post(
        "/v1/admin/model-endpoints", json=endpoint_payload(), headers=admin
    )
    endpoint_id = endpoint.json()["id"]
    await mark_endpoint_verified(engine, UUID(endpoint_id))
    agent = await client.post("/v1/admin/agents", json=agent_payload(endpoint_id), headers=admin)
    agent_id = agent.json()["id"]
    draft = await client.get(f"/v1/admin/agents/{agent_id}/draft", headers=admin)
    payload = {"expected_revision": 1, "config": draft.json()["config"]}

    first = await client.patch(f"/v1/admin/agents/{agent_id}/draft", json=payload, headers=admin)
    conflict = await client.patch(f"/v1/admin/agents/{agent_id}/draft", json=payload, headers=admin)

    assert first.json()["revision"] == 2
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "AGENT_DRAFT_REVISION_CONFLICT"
    assert conflict.json()["error"]["current_revision"] == 2


async def test_migrated_read_only_agent_rejects_control_plane_mutation(
    client: AsyncClient, settings: Settings, engine: AsyncEngine
) -> None:
    tenant_id = uuid4()
    admin = await auth_headers(engine, settings, tenant_id, roles=("platform_admin",))
    endpoint = await client.post(
        "/v1/admin/model-endpoints", json=endpoint_payload(), headers=admin
    )
    endpoint_id = endpoint.json()["id"]
    await mark_endpoint_verified(engine, UUID(endpoint_id))
    created = await client.post("/v1/admin/agents", json=agent_payload(endpoint_id), headers=admin)
    agent_id = UUID(created.json()["id"])

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session, session.begin():
        agent = await session.get(Agent, agent_id)
        assert agent is not None
        agent.read_only = True

    response = await client.patch(
        f"/v1/admin/agents/{agent_id}/profile",
        json={
            "name": "不应生效",
            "description": "",
            "welcome_message": "",
            "suggested_prompts": [],
        },
        headers=admin,
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "AGENT_CONFLICT"


async def test_migrated_read_only_model_rejects_profile_and_key_changes(
    client: AsyncClient, settings: Settings, engine: AsyncEngine
) -> None:
    tenant_id = uuid4()
    admin = await auth_headers(engine, settings, tenant_id, roles=("platform_admin",))
    created = await client.post("/v1/admin/model-endpoints", json=endpoint_payload(), headers=admin)
    endpoint_id = UUID(created.json()["id"])

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session, session.begin():
        endpoint = await session.get(ModelEndpoint, endpoint_id)
        assert endpoint is not None
        endpoint.read_only = True

    profile = await client.patch(
        f"/v1/admin/model-endpoints/{endpoint_id}",
        json={"name": "不应生效", "logo_url": None},
        headers=admin,
    )
    credential = await client.put(
        f"/v1/admin/model-endpoints/{endpoint_id}/credential",
        json={"api_key": "replacement-must-not-be-stored"},
        headers=admin,
    )

    assert profile.status_code == 409
    assert credential.status_code == 409
    assert "replacement-must-not-be-stored" not in credential.text


async def test_existing_conversation_keeps_version_and_becomes_read_only_after_revocation(
    client: AsyncClient, settings: Settings, engine: AsyncEngine
) -> None:
    tenant_id = uuid4()
    admin = await auth_headers(
        engine, settings, tenant_id, "admin", roles=("platform_admin", "employee")
    )
    employee = await auth_headers(engine, settings, tenant_id, "employee", roles=("employee",))
    endpoint = await client.post(
        "/v1/admin/model-endpoints", json=endpoint_payload(), headers=admin
    )
    endpoint_id = endpoint.json()["id"]
    await mark_endpoint_verified(engine, UUID(endpoint_id))
    created = await client.post("/v1/admin/agents", json=agent_payload(endpoint_id), headers=admin)
    agent_id = created.json()["id"]
    draft = await client.get(f"/v1/admin/agents/{agent_id}/draft", headers=admin)
    version_one = await client.post(
        f"/v1/admin/agents/{agent_id}/versions",
        json={"expected_revision": draft.json()["revision"], "activate": True},
        headers=admin,
    )
    await client.put(
        f"/v1/admin/agents/{agent_id}/grants",
        json={"grants": [{"subject_type": "role", "subject_id": "employee"}]},
        headers=admin,
    )
    existing = await client.post("/v1/conversations", json={"agent_id": agent_id}, headers=employee)

    saved = await client.patch(
        f"/v1/admin/agents/{agent_id}/draft",
        json={
            "expected_revision": draft.json()["revision"],
            "config": draft.json()["config"],
        },
        headers=admin,
    )
    version_two = await client.post(
        f"/v1/admin/agents/{agent_id}/versions",
        json={"expected_revision": saved.json()["revision"], "activate": True},
        headers=admin,
    )
    newer = await client.post("/v1/conversations", json={"agent_id": agent_id}, headers=employee)

    assert existing.json()["agent_version_id"] == version_one.json()["id"]
    assert newer.json()["agent_version_id"] == version_two.json()["id"]
    historical = await client.get(f"/v1/conversations/{existing.json()['id']}", headers=employee)
    assert historical.json()["current_agent_version_id"] == version_two.json()["id"]

    disabled = await client.post(f"/v1/admin/agents/{agent_id}/disable", headers=admin)
    blocked_while_disabled = await client.post(
        f"/v1/conversations/{existing.json()['id']}/messages",
        json={"content": "停用后不应产生消息"},
        headers={**employee, "Idempotency-Key": "disabled-message"},
    )
    await client.post(
        f"/v1/admin/agents/{agent_id}/versions/{version_two.json()['id']}/activate",
        headers=admin,
    )

    assert disabled.json()["status"] == "disabled"
    assert blocked_while_disabled.status_code == 404

    revoked = await client.put(
        f"/v1/admin/agents/{agent_id}/grants",
        json={"grants": []},
        headers=admin,
    )
    history = await client.get(f"/v1/conversations/{existing.json()['id']}", headers=employee)
    blocked = await client.post(
        f"/v1/conversations/{existing.json()['id']}/messages",
        json={"content": "撤权后不应产生消息"},
        headers={**employee, "Idempotency-Key": "revoked-message"},
    )
    history_after = await client.get(f"/v1/conversations/{existing.json()['id']}", headers=employee)

    assert revoked.status_code == 200
    assert history.status_code == 200
    assert blocked.status_code == 404
    assert history_after.json()["messages"] == history.json()["messages"]


async def test_credential_rotation_never_returns_or_audits_plaintext_secret(
    client: AsyncClient, settings: Settings, engine: AsyncEngine
) -> None:
    tenant_id = uuid4()
    admin = await auth_headers(engine, settings, tenant_id, roles=("platform_admin",))
    created = await client.post("/v1/admin/model-endpoints", json=endpoint_payload(), headers=admin)
    endpoint_id = UUID(created.json()["id"])
    rotated_secret = "sk-rotated-test-secret-must-never-leak"
    rotated = await client.put(
        f"/v1/admin/model-endpoints/{endpoint_id}/credential",
        json={"api_key": rotated_secret},
        headers=admin,
    )

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        credential = await session.scalar(
            select(ModelCredential).where(ModelCredential.endpoint_id == endpoint_id)
        )
        audit = await session.scalar(
            select(AgentAuditEvent)
            .where(AgentAuditEvent.model_endpoint_id == endpoint_id)
            .order_by(AgentAuditEvent.id.desc())
        )
        revision = (
            await session.scalar(
                select(ModelCredentialRevision).where(
                    ModelCredentialRevision.credential_id == credential.id,
                    ModelCredentialRevision.revision == 2,
                )
            )
            if credential is not None
            else None
        )

    assert rotated.status_code == 200
    assert rotated_secret not in rotated.text
    assert credential is not None and credential.revision == 2
    assert revision is not None and rotated_secret not in revision.encrypted_secret
    assert audit is not None and rotated_secret not in str(audit.metadata_payload)


async def test_user_grant_revocation_and_cross_tenant_agent_hiding(
    client: AsyncClient, settings: Settings, engine: AsyncEngine
) -> None:
    tenant_id = uuid4()
    admin = await auth_headers(engine, settings, tenant_id, "admin", roles=("platform_admin",))
    employee = await auth_headers(engine, settings, tenant_id, "employee", roles=("employee",))
    other_admin = await auth_headers(
        engine, settings, uuid4(), "other-admin", roles=("platform_admin",)
    )
    identity = await client.get("/v1/auth/me", headers=employee)
    endpoint = await client.post(
        "/v1/admin/model-endpoints", json=endpoint_payload(), headers=admin
    )
    endpoint_id = endpoint.json()["id"]
    await mark_endpoint_verified(engine, UUID(endpoint_id))
    created = await client.post("/v1/admin/agents", json=agent_payload(endpoint_id), headers=admin)
    agent_id = created.json()["id"]
    draft = await client.get(f"/v1/admin/agents/{agent_id}/draft", headers=admin)
    await client.post(
        f"/v1/admin/agents/{agent_id}/versions",
        json={"expected_revision": draft.json()["revision"], "activate": True},
        headers=admin,
    )

    denied_before_grant = await client.get("/v1/agents", headers=employee)
    granted = await client.put(
        f"/v1/admin/agents/{agent_id}/grants",
        json={"grants": [{"subject_type": "user", "subject_id": identity.json()["user_id"]}]},
        headers=admin,
    )
    allowed = await client.get("/v1/agents", headers=employee)
    hidden = await client.get(f"/v1/admin/agents/{agent_id}", headers=other_admin)
    cross_tenant_grant = await client.put(
        f"/v1/admin/agents/{agent_id}/grants",
        json={"grants": []},
        headers=other_admin,
    )
    revoked = await client.put(
        f"/v1/admin/agents/{agent_id}/grants",
        json={"grants": []},
        headers=admin,
    )
    denied_after_revoke = await client.get("/v1/agents", headers=employee)

    assert denied_before_grant.json()["items"] == []
    assert denied_before_grant.json()["total"] == 0
    assert granted.status_code == 200
    assert [item["id"] for item in allowed.json()["items"]] == [agent_id]
    assert hidden.status_code == 404
    assert cross_tenant_grant.status_code == 404
    assert revoked.json()["items"] == []
    assert revoked.json()["total"] == 0
    assert denied_after_revoke.json()["items"] == []
    assert denied_after_revoke.json()["total"] == 0
