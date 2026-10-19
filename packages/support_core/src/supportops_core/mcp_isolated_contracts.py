"""W8.5隔离MCP的固定协议与只读Mock契约，不提供业务工具执行许可。"""

from typing import Literal

from supportops_core.tool_discovery_contracts import (
    Digest, DiscoveredTool, DiscoveryCatalog, DiscoveryContract,
)

SDK_VERSION = "1.30.0"
PROTOCOL_VERSION = "2025-11-25"
SERVER_NAME = "supportops-isolated-mock"
SERVER_VERSION = "1"
MOCK_TOOL_NAME = "get_isolated_service_status"
ISOLATED_UID = 10002
ISOLATED_ENV = {
    "PATH": "/opt/supportops/.venv/bin:/usr/local/bin:/usr/bin:/bin",
    "HOME": "/nonexistent",
    "LANG": "C.UTF-8",
    "SUPPORTOPS_ISOLATED_MCP_MODE": "mock-only",
}


class IsolatedServiceStatus(DiscoveryContract):
    """常量模拟观察，不对应真实业务系统或模型运行状态。"""

    schema_version: Literal["isolated_service_status_v1"] = "isolated_service_status_v1"
    environment: Literal["isolated_mock"] = "isolated_mock"
    service: Literal["supportops_demo"] = "supportops_demo"
    status: Literal["available"] = "available"
    real_service_checked: Literal[False] = False


class IsolatedMcpReport(DiscoveryContract):
    schema_version: Literal["isolated_mcp_report_v1"] = "isolated_mcp_report_v1"
    operation: Literal["catalog", "read_status"]
    sdk_version: Literal["1.30.0"] = "1.30.0"
    protocol_version: Literal["2025-11-25"] = "2025-11-25"
    source_fingerprint: Digest
    descriptor_digest: Digest
    input_schema_digest: Digest
    output_schema_digest: Digest
    catalog: DiscoveryCatalog
    observation: IsolatedServiceStatus | None = None
    business_execution_enabled: Literal[False] = False


def mock_descriptor() -> DiscoveredTool:
    """每次返回独立JSON树，调用方不能修改共享批准定义。"""
    return DiscoveredTool(
        name=MOCK_TOOL_NAME,
        description="读取隔离演示服务的固定模拟状态，不查询真实业务资源。",
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        output_schema={
            "type": "object", "additionalProperties": False,
            "properties": {
                "schema_version": {"type": "string", "const": "isolated_service_status_v1"},
                "environment": {"type": "string", "const": "isolated_mock"},
                "service": {"type": "string", "const": "supportops_demo"},
                "status": {"type": "string", "const": "available"},
                "real_service_checked": {"type": "boolean", "const": False},
            },
            "required": ["schema_version", "environment", "service", "status", "real_service_checked"],
        },
    )
