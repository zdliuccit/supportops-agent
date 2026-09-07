from httpx import AsyncClient

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

        response_schema = operation["responses"]["200"]["content"]["application/json"][
            "schema"
        ]
        response_name = response_schema["$ref"].rsplit("/", 1)[-1]
        response_properties = component_schemas[response_name]["properties"]
        assert {"items", "total", "page", "page_size", "pages"} <= set(
            response_properties
        )


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
