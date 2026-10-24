from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker
from supportops_core.agent_config import TOOL_REGISTRY
from supportops_core.config import Settings
from supportops_core.models import (
    KnowledgeDocument,
    KnowledgeSource,
    KnowledgeVersion,
    ToolCatalogEntry,
)
from supportops_core.tool_services import count_tool_catalog, list_tool_catalog

from tests.conftest import auth_headers

PAGINATED_PATHS = (
    "/v1/conversations",
    "/v1/agents",
    "/v1/admin/agents",
    "/v1/admin/agents/{agent_id}/versions",
    "/v1/admin/agents/{agent_id}/grants",
    "/v1/admin/agents/{agent_id}/audit-events",
    "/v1/admin/model-endpoints",
    "/v1/admin/model-endpoints/{endpoint_id}/versions",
    "/v1/admin/users",
    "/v1/admin/tools",
    "/v1/admin/knowledge/sources",
    "/v1/admin/knowledge/documents/{document_id}/versions",
)


async def test_all_flat_list_endpoints_expose_standard_pagination_contract(
    client: AsyncClient,
) -> None:
    """防止后续列表端点重新出现自定义 limit/offset 或缺少分页元数据。"""

    schema = (await client.get("/openapi.json")).json()
    component_schemas = schema["components"]["schemas"]

    for path in PAGINATED_PATHS:
        operation = schema["paths"][path]["get"]
        query_parameters = {
            parameter["name"]: parameter
            for parameter in operation["parameters"]
            if parameter["in"] == "query"
        }
        assert query_parameters["page"]["schema"]["default"] == 1
        assert query_parameters["page"]["schema"]["minimum"] == 1
        assert query_parameters["page_size"]["schema"]["default"] == 20
        assert query_parameters["page_size"]["schema"]["minimum"] == 1
        assert query_parameters["page_size"]["schema"]["maximum"] == 100
        assert "limit" not in query_parameters
        assert "offset" not in query_parameters

        response_schema = operation["responses"]["200"]["content"]["application/json"]["schema"]
        response_name = response_schema["$ref"].rsplit("/", 1)[-1]
        response_properties = component_schemas[response_name]["properties"]
        assert {"items", "total", "page", "page_size", "pages"} <= set(response_properties)


async def test_complete_tree_endpoint_is_explicitly_not_paginated(client: AsyncClient) -> None:
    """组织树必须一次返回完整层级，不能套用普通扁平列表分页。"""

    schema = (await client.get("/openapi.json")).json()
    operation = schema["paths"]["/v1/admin/organization-units"]["get"]
    query_names = {
        parameter["name"]
        for parameter in operation.get("parameters", [])
        if parameter["in"] == "query"
    }
    assert "page" not in query_names
    assert "page_size" not in query_names


@dataclass
class PaginationData:
    headers: dict[str, str]
    other_headers: dict[str, str]
    tenant_id: UUID
    empty_tenant_id: UUID
    document_id: UUID
    empty_document_id: UUID
    source_ids: list[str]
    version_ids: list[str]

    def path(self, kind: str) -> str:
        return {
            "tools": "/v1/admin/tools",
            "sources": "/v1/admin/knowledge/sources",
            "versions": f"/v1/admin/knowledge/documents/{self.document_id}/versions",
        }[kind]

    def expected_ids(self, kind: str) -> list[str]:
        if kind == "tools":
            return sorted([*TOOL_REGISTRY, "page-a", "page-b", "page-c"])
        return self.source_ids if kind == "sources" else self.version_ids


@pytest.fixture
async def pagination_data(engine: AsyncEngine, settings: Settings) -> PaginationData:
    """同名、同时间和逆序插入的数据用于验证稳定排序及租户隔离。"""
    tenant_id, other_tenant_id, empty_tenant_id = uuid4(), uuid4(), uuid4()
    headers = await auth_headers(engine, settings, tenant_id, roles=("platform_admin",))
    other_headers = await auth_headers(engine, settings, other_tenant_id, roles=("platform_admin",))
    document_id, empty_document_id = uuid4(), uuid4()
    source_ids = [UUID(int=n) for n in (101, 102, 103)]
    version_ids = [uuid4() for _ in range(3)]
    timestamp = datetime(2026, 1, 1, tzinfo=UTC)
    async with async_sessionmaker(engine)() as session, session.begin():
        for tenant, prefix in [(tenant_id, "page"), (other_tenant_id, "foreign")]:
            actor = uuid5(NAMESPACE_URL, f"{tenant}:user-1")
            for suffix in ("c", "a", "b"):
                session.add(
                    ToolCatalogEntry(
                        tenant_id=tenant,
                        tool_id=f"{prefix}-{suffix}",
                        name="同名工具",
                        description="分页测试",
                        implementation_key="test-only",
                        created_by=actor,
                        updated_by=actor,
                        created_at=timestamp,
                    )
                )
            ids = source_ids if tenant == tenant_id else [uuid4()]
            for source_id in reversed(ids):
                session.add(
                    KnowledgeSource(
                        id=source_id,
                        tenant_id=tenant,
                        external_key=str(source_id),
                        name="同名来源",
                        source_type="manual",
                        owner_user_id=actor,
                        created_by=actor,
                        created_at=timestamp,
                    )
                )
        await session.flush()
        actor = uuid5(NAMESPACE_URL, f"{tenant_id}:user-1")
        for ident in (document_id, empty_document_id):
            session.add(
                KnowledgeDocument(
                    id=ident,
                    tenant_id=tenant_id,
                    source_id=source_ids[0],
                    external_key=str(ident),
                    owner_user_id=actor,
                    created_by=actor,
                )
            )
        await session.flush()
        for number in (2, 1, 3):
            session.add(
                KnowledgeVersion(
                    id=version_ids[number - 1],
                    tenant_id=tenant_id,
                    document_id=document_id,
                    version_number=number,
                    title=f"版本{number}",
                    content_markdown="分页正文",
                    content_digest="a" * 64,
                    created_by=actor,
                    created_at=timestamp,
                )
            )
    return PaginationData(
        headers,
        other_headers,
        tenant_id,
        empty_tenant_id,
        document_id,
        empty_document_id,
        [str(i) for i in source_ids],
        [str(i) for i in reversed(version_ids)],
    )


@pytest.mark.parametrize("kind", ["tools", "sources", "versions"])
async def test_remaining_lists_pages_and_out_of_range(
    client: AsyncClient,
    pagination_data: PaginationData,
    kind: str,
) -> None:
    data = pagination_data
    expected = data.expected_ids(kind)
    seen = []
    for page in range(1, (len(expected) + 1) // 2 + 1):
        response = await client.get(
            data.path(kind),
            headers=data.headers,
            params={"page": page, "page_size": 2},
        )
        assert response.status_code == 200
        body = response.json()
        assert {k: body[k] for k in ("total", "page", "page_size", "pages")} == {
            "total": len(expected),
            "page": page,
            "page_size": 2,
            "pages": (len(expected) + 1) // 2,
        }
        key = "tool_id" if kind == "tools" else "id"
        ids = [item[key] for item in body["items"]]
        assert ids == expected[(page - 1) * 2 : page * 2]
        seen.extend(ids)
    assert seen == expected and len(set(seen)) == len(seen)
    response = await client.get(
        data.path(kind),
        headers=data.headers,
        params={"page": 999, "page_size": 2},
    )
    assert response.status_code == 200
    assert response.json() == {
        "items": [],
        "total": len(expected),
        "page": 999,
        "page_size": 2,
        "pages": (len(expected) + 1) // 2,
    }


@pytest.mark.parametrize("kind", ["tools", "sources", "versions"])
@pytest.mark.parametrize(
    "params",
    [
        {"page": 0},
        {"page": -1},
        {"page": "bad"},
        {"page_size": 0},
        {"page_size": 101},
        {"page_size": "bad"},
    ],
)
async def test_remaining_lists_reject_invalid_pagination(
    client: AsyncClient,
    pagination_data: PaginationData,
    kind: str,
    params: dict[str, int | str],
) -> None:
    response = await client.get(
        pagination_data.path(kind),
        headers=pagination_data.headers,
        params=params,
    )
    assert response.status_code == 422


async def test_sources_empty_search_and_cross_tenant(
    client: AsyncClient,
    pagination_data: PaginationData,
) -> None:
    data = pagination_data
    response = await client.get(data.path("sources"), headers=data.other_headers)
    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert not set(data.source_ids).intersection(i["id"] for i in response.json()["items"])
    for keywords, total in [("同名来源", 3), ("不存在", 0), ("%", 0), ("_", 0)]:
        response = await client.get(
            data.path("sources"),
            headers=data.headers,
            params={"keywords": keywords, "page_size": 2},
        )
        assert response.status_code == 200
        assert response.json()["total"] == total
        assert len(response.json()["items"]) == min(total, 2)


async def test_sources_empty_tenant(
    client: AsyncClient,
    engine: AsyncEngine,
    settings: Settings,
) -> None:
    headers = await auth_headers(engine, settings, uuid4(), roles=("platform_admin",))
    response = await client.get("/v1/admin/knowledge/sources", headers=headers)
    assert response.status_code == 200
    assert response.json() == {
        "items": [],
        "total": 0,
        "page": 1,
        "page_size": 20,
        "pages": 0,
    }


@pytest.mark.parametrize("kind", ["tools", "sources", "versions"])
async def test_remaining_lists_deny_non_admin(
    client: AsyncClient,
    engine: AsyncEngine,
    settings: Settings,
    pagination_data: PaginationData,
    kind: str,
) -> None:
    headers = await auth_headers(engine, settings, uuid4(), roles=("employee",))
    response = await client.get(pagination_data.path(kind), headers=headers)
    assert response.status_code == 403


async def test_versions_empty_document_and_hidden_foreign_document(
    client: AsyncClient,
    pagination_data: PaginationData,
) -> None:
    data = pagination_data
    empty = await client.get(
        f"/v1/admin/knowledge/documents/{data.empty_document_id}/versions",
        headers=data.headers,
    )
    assert empty.status_code == 200
    assert empty.json() == {"items": [], "total": 0, "page": 1, "page_size": 20, "pages": 0}
    foreign = await client.get(data.path("versions"), headers=data.other_headers)
    missing = await client.get(
        f"/v1/admin/knowledge/documents/{uuid4()}/versions",
        headers=data.other_headers,
    )
    assert foreign.status_code == missing.status_code == 404
    assert foreign.json()["error"]["code"] == missing.json()["error"]["code"] == "NOT_FOUND"
    assert foreign.json()["error"]["message"] == missing.json()["error"]["message"]


async def test_tools_tenant_isolation_and_unseeded_empty_query(
    client: AsyncClient,
    engine: AsyncEngine,
    pagination_data: PaginationData,
) -> None:
    data = pagination_data
    response = await client.get(data.path("tools"), headers=data.other_headers)
    assert response.status_code == 200
    ids = [i["tool_id"] for i in response.json()["items"]]
    assert not set(ids).intersection({"page-a", "page-b", "page-c"})
    assert response.json()["total"] == len(TOOL_REGISTRY) + 3
    # API会补齐内置目录；真正的空集合在未补齐租户的数据库查询边界验证。
    async with async_sessionmaker(engine)() as session:
        assert await count_tool_catalog(session, tenant_id=data.empty_tenant_id) == 0
        assert (
            await list_tool_catalog(
                session,
                tenant_id=data.empty_tenant_id,
                page_size=2,
                offset=0,
            )
            == []
        )
