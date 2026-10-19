"""随包固定的stdio MCP Server；只有无参数只读Mock，不读取业务库、业务文件或秘密。"""

import asyncio
import logging

from supportops_core.mcp_isolated_contracts import (
    MOCK_TOOL_NAME, SERVER_NAME, SERVER_VERSION, IsolatedServiceStatus, mock_descriptor,
)
from supportops_core.mcp_isolated_runtime import require_isolated_runtime, require_sdk_version


async def serve() -> None:
    require_isolated_runtime()
    require_sdk_version()
    # SDK只在显式隔离入口加载，普通API/Worker无需安装可选依赖。
    import mcp.types as types
    from mcp.server.lowlevel import Server
    from mcp.server.stdio import stdio_server

    server = Server(SERVER_NAME, version=SERVER_VERSION)

    @server.list_tools()
    async def list_tools(request: types.ListToolsRequest) -> types.ListToolsResult:
        # SDK通过精确请求类型识别新式handler，内部缓存刷新仍可能传None。
        if request is not None and request.params is not None:
            raise ValueError("MCP_ISOLATED_CURSOR_DENIED")
        descriptor = mock_descriptor()
        return types.ListToolsResult(tools=[types.Tool(name=descriptor.name,
            description=descriptor.description, inputSchema=descriptor.input_schema, outputSchema=descriptor.output_schema)])

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, object]) -> dict[str, object]:
        if name != MOCK_TOOL_NAME or arguments:
            raise ValueError("MCP_ISOLATED_CALL_DENIED")
        return IsolatedServiceStatus().model_dump(mode="json")

    async with stdio_server() as (incoming, outgoing):
        await server.run(incoming, outgoing, server.create_initialization_options())


def main() -> int:
    logging.disable(logging.CRITICAL)
    try:
        asyncio.run(serve())
        return 0
    except KeyboardInterrupt:
        return 130
    except Exception:
        # stdout专供SDK协议，不输出回溯或普通诊断。
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
