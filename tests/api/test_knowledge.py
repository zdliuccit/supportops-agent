from uuid import NAMESPACE_URL, uuid4, uuid5

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine
from supportops_core.config import Settings

from tests.conftest import auth_headers


async def test_knowledge_lifecycle_api(
    client: AsyncClient, engine: AsyncEngine, settings: Settings
) -> None:
    tenant_id = uuid4()
    subject = "knowledge-admin"
    headers = await auth_headers(
        engine, settings, tenant_id, subject=subject, roles=("platform_admin",)
    )
    owner_id = uuid5(NAMESPACE_URL, f"{tenant_id}:{subject}")
    source_response = await client.post(
        "/v1/admin/knowledge/sources",
        headers=headers,
        json={
            "external_key": "support-handbook",
            "name": "Support handbook",
            "source_type": "manual",
            "environment": "dev",
            "owner_user_id": str(owner_id),
        },
    )
    assert source_response.status_code == 201
    source_id = source_response.json()["id"]

    document_response = await client.post(
        "/v1/admin/knowledge/documents",
        headers=headers,
        json={
            "source_id": source_id,
            "external_key": "429-guide",
            "owner_user_id": str(owner_id),
            "title": "429 guide",
            "content_markdown": "# 429\n\nCheck RPM",
        },
    )
    assert document_response.status_code == 201
    document_id = document_response.json()["id"]

    acl_response = await client.put(
        f"/v1/admin/knowledge/documents/{document_id}/acl",
        headers=headers,
        json={"entries": [{"subject_type": "role", "subject_id": "platform_admin"}]},
    )
    assert acl_response.status_code == 200

    versions_response = await client.get(
        f"/v1/admin/knowledge/documents/{document_id}/versions", headers=headers
    )
    assert versions_response.status_code == 200
    version_id = versions_response.json()[0]["id"]
    assert versions_response.json()[0]["status"] == "draft"

    assert (
        await client.post(
            f"/v1/admin/knowledge/versions/{version_id}/submit-review", headers=headers
        )
    ).status_code == 200
    publish_response = await client.post(
        f"/v1/admin/knowledge/versions/{version_id}/publish", headers=headers
    )
    assert publish_response.status_code == 200
    assert publish_response.json()["status"] == "published"

    snapshot_response = await client.get(
        f"/v1/admin/knowledge/documents/{document_id}/snapshot", headers=headers
    )
    assert snapshot_response.status_code == 200
    assert snapshot_response.json()["version_id"] == version_id

    list_response = await client.get(
        "/v1/admin/knowledge/documents?status=published&page=1&page_size=10", headers=headers
    )
    assert list_response.status_code == 200
    assert list_response.json()["total"] == 1

    audit_response = await client.get(
        "/v1/admin/knowledge/audit-events?page=1&page_size=100", headers=headers
    )
    assert audit_response.status_code == 200
    assert audit_response.json()["total"] >= 4


async def test_mock_import_is_idempotent_and_tenant_scoped(
    client: AsyncClient, engine: AsyncEngine, settings: Settings
) -> None:
    tenant_id = uuid4()
    headers = await auth_headers(engine, settings, tenant_id, roles=("platform_admin",))
    payload = {
        "source_external_key": "p0-mock",
        "source_name": "P0 Mock",
        "items": [
            {"external_key": "k01", "title": "429 SOP", "content_markdown": "# 429\nSOP"}
        ],
    }
    first = await client.post("/v1/admin/knowledge/mock-import", headers=headers, json=payload)
    second = await client.post("/v1/admin/knowledge/mock-import", headers=headers, json=payload)
    assert first.status_code == 200
    assert first.json() == {"created": 1, "skipped": 0}
    assert second.status_code == 200
    assert second.json() == {"created": 0, "skipped": 1}

    other_headers = await auth_headers(
        engine, settings, uuid4(), subject="other-admin", roles=("platform_admin",)
    )
    document_id = (await client.get("/v1/admin/knowledge/documents", headers=headers)).json()[
        "items"
    ][0]["id"]
    hidden = await client.get(
        f"/v1/admin/knowledge/documents/{document_id}", headers=other_headers
    )
    assert hidden.status_code == 404
