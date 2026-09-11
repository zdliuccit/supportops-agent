import json
from uuid import UUID, uuid4

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker
from supportops_api.routes import admin_models as admin_models_routes
from supportops_core import model_services
from supportops_core.config import Settings
from supportops_core.enums import (
    ModelProviderKind,
    ModelTestStatus,
    ModelVerificationStatus,
)
from supportops_core.model_services import (
    ModelEndpointSelection,
    execute_model_test_run,
    list_model_endpoint_models,
    model_version_digest,
    save_model_endpoint_configuration,
    start_model_test_run,
)
from supportops_core.models import (
    Agent,
    AgentAuditEvent,
    ModelCredential,
    ModelCredentialRevision,
    ModelEndpoint,
    ModelEndpointModel,
    ModelEndpointTestRun,
    ModelEndpointVersion,
    User,
)
from supportops_core.secrets import LocalEnvelopeSecretProvider

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
        endpoint.is_enabled = True
        version.verification_status = ModelVerificationStatus.VERIFIED
        version.verification_result = {"model": "passed"}


async def test_model_endpoint_uses_enabled_flag_and_invalidates_old_tests(
    client: AsyncClient, settings: Settings, engine: AsyncEngine
) -> None:
    """模型连接没有草稿状态，且密钥变化后旧测试不能继续用于启用。"""
    tenant_id = uuid4()
    admin = await auth_headers(engine, settings, tenant_id, roles=("platform_admin",))
    payload = {
        "name": "企业中转站",
        "provider_preset": None,
        "provider_kind": "openai_compatible",
        "base_url": "https://relay.example/v1",
        "api_key": "sk-initial-test-key",
        "models": [
            {
                "upstream_model_id": "support-fast",
                "extension_options": {"service_tier": "auto"},
            },
            {"upstream_model_id": "support-deep", "extension_options": {}},
        ],
        "is_enabled": False,
    }

    created = await client.post("/v1/admin/model-endpoints", json=payload, headers=admin)

    assert created.status_code == 201
    endpoint = created.json()
    assert "status" not in endpoint
    assert endpoint["is_enabled"] is False
    assert {item["upstream_model_id"] for item in endpoint["models"]} == {
        "support-fast",
        "support-deep",
    }
    assert {item["test_status"] for item in endpoint["models"]} == {"untested"}
    assert endpoint["models"][0]["extension_options"] == {"service_tier": "auto"}

    endpoint_id = UUID(endpoint["id"])
    enable_requested = await client.put(
        f"/v1/admin/model-endpoints/{endpoint_id}/configuration",
        json={**payload, "api_key": None, "is_enabled": True, "models": endpoint["models"]},
        headers=admin,
    )
    assert enable_requested.status_code == 200
    assert enable_requested.json()["is_enabled"] is False
    assert len(enable_requested.json()["enable_blockers"]) == 2

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session, session.begin():
        stored_endpoint = await session.get(ModelEndpoint, endpoint_id)
        assert stored_endpoint is not None
        credential = await session.scalar(
            select(ModelCredential).where(ModelCredential.endpoint_id == endpoint_id)
        )
        assert credential is not None
        models = list(
            (
                await session.scalars(
                    select(ModelEndpointModel).where(
                        ModelEndpointModel.endpoint_id == endpoint_id,
                        ModelEndpointModel.archived_at.is_(None),
                    )
                )
            ).all()
        )
        for model in models:
            assert model.current_version_id is not None
            version = await session.get(ModelEndpointVersion, model.current_version_id)
            assert version is not None
            session.add(
                ModelEndpointTestRun(
                    tenant_id=tenant_id,
                    endpoint_id=endpoint_id,
                    endpoint_model_id=model.id,
                    model_version_id=version.id,
                    credential_revision=credential.revision,
                    config_digest=model_version_digest(version, credential.revision),
                    status=ModelTestStatus.PASSED,
                    stage="completed",
                    request_host="relay.example",
                    request_path="/v1/chat/completions",
                    milestones={"completed": True},
                    correlation_id="test-enable",
                    created_by=stored_endpoint.created_by,
                )
            )

    enabled = await client.post(f"/v1/admin/model-endpoints/{endpoint_id}/enable", headers=admin)
    assert enabled.status_code == 200
    assert enabled.json()["is_enabled"] is True
    assert {item["test_status"] for item in enabled.json()["models"]} == {"passed"}

    rotated = await client.put(
        f"/v1/admin/model-endpoints/{endpoint_id}/configuration",
        json={
            **payload,
            "api_key": "sk-rotated-test-key",
            "is_enabled": True,
            "models": enabled.json()["models"],
        },
        headers=admin,
    )
    # 启用中的连接不允许直接编辑；先停用后才能轮换密钥并使旧测试失效。
    assert rotated.status_code == 409

    disabled = await client.post(
        f"/v1/admin/model-endpoints/{endpoint_id}/disable", headers=admin
    )
    assert disabled.status_code == 200
    assert disabled.json()["is_enabled"] is False

    rotated = await client.put(
        f"/v1/admin/model-endpoints/{endpoint_id}/configuration",
        json={
            **payload,
            "api_key": "sk-rotated-test-key",
            "is_enabled": True,
            "models": enabled.json()["models"],
        },
        headers=admin,
    )
    assert rotated.status_code == 200
    assert rotated.json()["is_enabled"] is False
    assert {item["test_status"] for item in rotated.json()["models"]} == {"stale"}

    deleted = await client.delete(f"/v1/admin/model-endpoints/{endpoint_id}", headers=admin)
    assert deleted.status_code == 204
    missing = await client.get(f"/v1/admin/model-endpoints/{endpoint_id}", headers=admin)
    assert missing.status_code == 404


async def test_model_discovery_uses_request_base_url_and_api_key(
    client: AsyncClient,
    settings: Settings,
    engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """通用发现接口必须显式接收 Base URL 和 API Key，不依赖已保存端点。"""
    tenant_id = uuid4()
    admin = await auth_headers(engine, settings, tenant_id, roles=("platform_admin",))
    captured: dict[str, object] = {}

    async def fake_discovery(**kwargs: object) -> list[str]:
        captured.update(kwargs)
        return ["support-a", "support-b"]

    monkeypatch.setattr(admin_models_routes, "discover_remote_models", fake_discovery)
    discovered = await client.post(
        "/v1/admin/model-endpoints/discover",
        json={"base_url": "https://relay.example/v1", "api_key": "sk-explicit-test"},
        headers=admin,
    )
    missing_key = await client.post(
        "/v1/admin/model-endpoints/discover",
        json={"base_url": "https://relay.example/v1"},
        headers=admin,
    )

    assert discovered.status_code == 200
    assert discovered.json() == {"items": ["support-a", "support-b"]}
    assert captured["base_url"] == "https://relay.example/v1"
    assert captured["api_key"] == "sk-explicit-test"
    assert missing_key.status_code == 422


async def test_streaming_model_test_persists_real_milestones(
    engine: AsyncEngine,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """逐模型测试应持久化响应头、首包和完成阶段，而非模拟计时。"""
    tenant_id = uuid4()
    await auth_headers(engine, settings, tenant_id, roles=("platform_admin",))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    secret_provider = LocalEnvelopeSecretProvider(settings.secret_encryption_key)

    async with factory() as session, session.begin():
        actor = await session.scalar(select(User).where(User.tenant_id == tenant_id))
        assert actor is not None
        endpoint, _ = await save_model_endpoint_configuration(
            session,
            endpoint_id=None,
            tenant_id=tenant_id,
            actor_user_id=actor.id,
            name="流式测试中转",
            provider_preset=None,
            provider_kind=ModelProviderKind.OPENAI_COMPATIBLE,
            base_url="https://relay.example/v1",
            api_key="sk-stream-test",
            models=[
                ModelEndpointSelection(
                    id=None,
                    upstream_model_id="support-stream",
                    extension_options={"service_tier": "auto"},
                )
            ],
            request_enable=False,
            secret_provider=secret_provider,
            allow_private_networks=False,
            allowed_hosts=[],
            correlation_id="create-stream-test",
        )
        models = await list_model_endpoint_models(
            session, endpoint_id=endpoint.id, tenant_id=tenant_id
        )
        test_run = await start_model_test_run(
            session,
            endpoint_id=endpoint.id,
            endpoint_model_id=models[0].id,
            tenant_id=tenant_id,
            actor_user_id=actor.id,
            correlation_id="run-stream-test",
        )

    async def allow_test_endpoint(base_url: str, *, allow_private_networks: bool) -> list[str]:
        del base_url, allow_private_networks
        return ["203.0.113.10"]

    monkeypatch.setattr(model_services, "resolve_and_validate_endpoint", allow_test_endpoint)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://relay.example/v1/chat/completions"
        request_payload = json.loads(request.content)
        assert request_payload["service_tier"] == "auto"
        assert request_payload["messages"] == [
            {"role": "user", "content": "请回答：1+1等于几？"}
        ]
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=(
                'data: {"choices":[{"delta":{"content":"1+1"}}]}\n\n'
                'data: {"choices":[{"delta":{"content":"等于2"}}]}\n\n'
                "data: [DONE]\n\n"
            ).encode(),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        async with factory() as session:
            await execute_model_test_run(
                session,
                test_run_id=test_run.id,
                secret_provider=secret_provider,
                allow_private_networks=False,
                allowed_hosts=[],
                timeout_seconds=5,
                max_response_bytes=4096,
                client=http_client,
            )

    async with factory() as session:
        stored = await session.get(ModelEndpointTestRun, test_run.id)
        assert stored is not None
        assert stored.status == ModelTestStatus.PASSED
        assert stored.stage == "completed"
        assert stored.provider_status == 200
        assert stored.response_headers_ms is not None
        assert stored.first_content_ms is not None
        assert stored.total_ms is not None
        assert stored.response_content == "1+1等于2"
        assert stored.milestones == {
            "queued": True,
            "request_sent": True,
            "response_headers": True,
            "provider_status": 200,
            "first_content": True,
            "completed": True,
        }


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
    running = await client.get(f"/v1/admin/agents/{agent_id}", headers=admin)
    assert running.status_code == 200
    assert running.json()["active_version_config_digest"] == draft.json()["config_digest"]
    assert running.json()["active_version_published_at"] is not None
    changed_config = {
        **draft.json()["config"],
        "prompt": {
            **draft.json()["config"]["prompt"],
            "system_prompt": (
                f'{draft.json()["config"]["prompt"]["system_prompt"]}\n请补充排障步骤。'
            ),
        },
    }
    saved = await client.patch(
        f"/v1/admin/agents/{agent_id}/draft",
        json={"expected_revision": draft.json()["revision"], "config": changed_config},
        headers=admin,
    )
    assert saved.status_code == 200
    pending = await client.get(f"/v1/admin/agents/{agent_id}", headers=admin)
    assert pending.json()["active_version_config_digest"] != saved.json()["config_digest"]
    model_list = await client.get("/v1/admin/model-endpoints", headers=admin)
    assert model_list.status_code == 200
    assert model_list.json()["items"][0]["used_agent_count"] == 1
    assert model_list.json()["items"][0]["used_agent_names"] == ["企业技术支持 Agent"]
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

    first_page = await client.get("/v1/admin/agents?status=draft&page=1&page_size=2", headers=admin)
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
