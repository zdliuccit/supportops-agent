"""官方SDK的固定stdio客户端；只连接随包Mock，不接受URL、命令、令牌或任意工具名。"""

import asyncio
import json
import os
import sys
from datetime import timedelta
from typing import Literal

from supportops_core.mcp_isolated_contracts import (
    ISOLATED_ENV, MOCK_TOOL_NAME, PROTOCOL_VERSION, SERVER_NAME, SERVER_VERSION,
    IsolatedMcpReport, IsolatedServiceStatus, mock_descriptor,
)
from supportops_core.mcp_isolated_runtime import (
    IsolatedMcpError, require_isolated_runtime, require_sdk_version, source_fingerprint,
)
from supportops_core.tool_discovery_contracts import (
    canonical_json, discovery_digest, parse_discovery_catalog,
)


async def run_isolated_mcp(operation: Literal["catalog", "read_status"]) -> IsolatedMcpReport:
    if operation not in {"catalog", "read_status"}:
        raise IsolatedMcpError("MCP_ISOLATED_OPERATION_DENIED")
    require_isolated_runtime()
    require_sdk_version()
    fingerprint = source_fingerprint()
    changed = asyncio.Event()

    # 延迟加载可选SDK，不为普通API/Worker引入MCP运行时或进程创建。
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    async def handle_message(_message: object) -> None:
        # 本固定来源不应产生任何额外通知；listChanged/未知消息均使本次观察失效。
        changed.set()

    def check_unchanged() -> None:
        if changed.is_set():
            raise IsolatedMcpError("MCP_ISOLATED_SOURCE_CHANGED")

    expected = mock_descriptor()
    observation = None
    deadline = asyncio.get_running_loop().time() + 10
    try:
        # SDK终止进程/任务组清理可能超过预算，不宣称外部取消或硬截止已验证。
        async with asyncio.timeout(10):
            with open(os.devnull, "w", encoding="utf-8") as errlog:
                parameters = StdioServerParameters(command=sys.executable,
                    args=["-I", "-B", "-m", "supportops_core.mcp_isolated_server"],
                    env=dict(ISOLATED_ENV), cwd="/opt/supportops")
                async with stdio_client(parameters, errlog=errlog) as (incoming, outgoing):
                    async with ClientSession(incoming, outgoing, read_timeout_seconds=timedelta(seconds=3),
                        message_handler=handle_message) as session:
                        initialized = await session.initialize()
                        check_unchanged()
                        capabilities = initialized.capabilities.model_dump(mode="json", by_alias=True, exclude_none=True)
                        if (initialized.protocolVersion != PROTOCOL_VERSION
                            or initialized.serverInfo.name != SERVER_NAME or initialized.serverInfo.version != SERVER_VERSION
                            or capabilities != {"tools": {"listChanged": False}, "experimental": {}}
                            or initialized.instructions is not None):
                            raise IsolatedMcpError("MCP_ISOLATED_HANDSHAKE_REJECTED")
                        listed = await session.list_tools()
                        check_unchanged()
                        if listed.nextCursor is not None or len(listed.tools) != 1:
                            raise IsolatedMcpError("MCP_ISOLATED_CATALOG_REJECTED")
                        tool = listed.tools[0]
                        wire = tool.model_dump(mode="json", by_alias=True, exclude_none=True)
                        if set(wire) != {"name", "description", "inputSchema", "outputSchema"}:
                            raise IsolatedMcpError("MCP_ISOLATED_CATALOG_REJECTED")
                        catalog = parse_discovery_catalog(canonical_json({"schema_version": "tool_discovery_catalog_v1",
                            "tools": [{"name": tool.name, "description": tool.description,
                                "input_schema": tool.inputSchema, "output_schema": tool.outputSchema}]}))
                        if canonical_json(catalog.tools[0].model_dump(mode="json")) != canonical_json(expected.model_dump(mode="json")):
                            raise IsolatedMcpError("MCP_ISOLATED_CATALOG_CHANGED")
                        if operation == "read_status":
                            # 只调用一个固定无参数Mock；SDK先前已缓存获准输出Schema，不自动发现任意工具。
                            check_unchanged()
                            result = await session.call_tool(MOCK_TOOL_NAME, {})
                            check_unchanged()
                            value = result.model_dump(mode="json", by_alias=True, exclude_none=True)
                            if len(canonical_json(value).encode("utf-8")) > 4096 or set(value) != {"content", "structuredContent", "isError"}:
                                raise IsolatedMcpError("MCP_ISOLATED_RESULT_REJECTED")
                            if result.isError or len(result.content) != 1:
                                raise IsolatedMcpError("MCP_ISOLATED_RESULT_REJECTED")
                            content = result.content[0].model_dump(mode="json", by_alias=True, exclude_none=True)
                            if set(content) != {"type", "text"} or content["type"] != "text":
                                raise IsolatedMcpError("MCP_ISOLATED_RESULT_REJECTED")
                            expected_status = IsolatedServiceStatus().model_dump(mode="json")
                            # SDK结构化结果和文本镜像必须一致，不接受额外内容/链接/artifact。
                            if (canonical_json(result.structuredContent) != canonical_json(expected_status)
                                or content["text"] != json.dumps(expected_status, indent=2)):
                                raise IsolatedMcpError("MCP_ISOLATED_RESULT_REJECTED")
                            observation = IsolatedServiceStatus.model_validate(result.structuredContent)
                        check_unchanged()
        if asyncio.get_running_loop().time() >= deadline:
            raise IsolatedMcpError("MCP_ISOLATED_TIMEOUT")
        require_isolated_runtime()
        check_unchanged()
        if source_fingerprint() != fingerprint:
            raise IsolatedMcpError("MCP_ISOLATED_SOURCE_CHANGED")
        return IsolatedMcpReport(operation=operation, source_fingerprint=fingerprint,
            descriptor_digest=discovery_digest(expected.model_dump(mode="json")),
            input_schema_digest=discovery_digest(expected.input_schema),
            output_schema_digest=discovery_digest(expected.output_schema), catalog=catalog, observation=observation)
    except IsolatedMcpError:
        raise
    except TimeoutError:
        raise IsolatedMcpError("MCP_ISOLATED_TIMEOUT") from None
    except Exception:
        # 不返回SDK异常链或来自子进程的正文，也不做自动重试。
        raise IsolatedMcpError("MCP_ISOLATED_FAILED") from None
