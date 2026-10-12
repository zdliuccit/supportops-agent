from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine
from supportops_core.auth import IdentityContext, Principal
from supportops_core.config import Settings
from supportops_core.db import create_session_factory
from supportops_core.knowledge_services import knowledge_access_allowed
from supportops_core.models import (
    KnowledgeAclEntry,
    KnowledgeDocument,
    KnowledgeSnapshot,
    KnowledgeVersion,
    User,
)

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

    factory = create_session_factory(engine)

    async def can_read() -> bool:
        async with factory() as session:
            user = await session.get(User, owner_id)
            assert user is not None
            identity = IdentityContext(
                Principal(owner_id, tenant_id, ("platform_admin",), user.display_name), user
            )
            return await knowledge_access_allowed(
                session, document_id=UUID(document_id), tenant_id=tenant_id, identity=identity
            )

    assert await can_read()
    acl_path = f"/v1/admin/knowledge/documents/{document_id}/acl"
    assert (await client.put(acl_path, headers=headers, json={"entries": []})).status_code == 200
    assert not await can_read()
    for _ in range(2):
        assert (
            await client.put(
                acl_path,
                headers=headers,
                json={"entries": [{"subject_type": "role", "subject_id": "platform_admin"}]},
            )
        ).status_code == 200
    assert await can_read()

    document_path = f"/v1/admin/knowledge/documents/{document_id}"
    deleted = await client.delete(document_path, headers=headers)
    assert deleted.status_code == 200 and deleted.json()["status"] == "archived"
    for suffix in ("", "/versions", "/snapshot"):
        assert (await client.get(document_path + suffix, headers=headers)).status_code == 404
    for action in ("submit-review", "publish", "retire"):
        assert (
            await client.post(
                f"/v1/admin/knowledge/versions/{version_id}/{action}", headers=headers
            )
        ).status_code == 404
    listing = await client.get("/v1/admin/knowledge/documents", headers=headers)
    assert listing.json()["items"] == [] and listing.json()["total"] == 0
    async with factory() as session:
        for resource in (KnowledgeDocument, KnowledgeVersion, KnowledgeSnapshot, KnowledgeAclEntry):
            assert await session.scalar(select(func.count()).select_from(resource)) == 0
            history = list(
                (
                    await session.scalars(select(resource).execution_options(include_deleted=True))
                ).all()
            )
            assert history and all(record.deleted_at is not None for record in history)
        document = await session.scalar(
            select(KnowledgeDocument).execution_options(include_deleted=True)
        )
        assert document is not None and document.current_version_id == UUID(version_id)
    replacement = await client.post(
        "/v1/admin/knowledge/documents",
        headers=headers,
        json={
            "source_id": source_id,
            "external_key": "429-guide",
            "owner_user_id": str(owner_id),
            "title": "替代文档",
            "content_markdown": "# 新内容",
        },
    )
    assert replacement.status_code == 201 and replacement.json()["id"] != document_id


async def test_mock_import_is_idempotent_and_tenant_scoped(
    client: AsyncClient, engine: AsyncEngine, settings: Settings
) -> None:
    tenant_id = uuid4()
    headers = await auth_headers(engine, settings, tenant_id, roles=("platform_admin",))
    payload = {
        "source_external_key": "p0-mock",
        "source_name": "P0 Mock",
        "items": [{"external_key": "k01", "title": "429 SOP", "content_markdown": "# 429\nSOP"}],
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
    hidden = await client.get(f"/v1/admin/knowledge/documents/{document_id}", headers=other_headers)
    assert hidden.status_code == 404
