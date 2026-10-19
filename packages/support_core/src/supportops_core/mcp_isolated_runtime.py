"""只允许无网络隔离运行配置的前置检查；不作为完整OS隔离认证。"""

import hashlib
import os
import socket
import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from supportops_core.mcp_isolated_contracts import (
    ISOLATED_ENV, ISOLATED_UID, PROTOCOL_VERSION, SDK_VERSION, SERVER_NAME, SERVER_VERSION,
)
from supportops_core.tool_discovery_contracts import discovery_digest


class IsolatedMcpError(Exception):
    """固定码；不携带SDK异常、环境、路径、协议正文或凭据。"""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def require_isolated_runtime() -> None:
    """拒绝普通API/Worker环境；完整挂载/网络/资源隔离仍依赖实际部署。"""
    try:
        if (sys.platform != "linux" or os.geteuid() != ISOLATED_UID or os.getegid() != ISOLATED_UID
            or dict(os.environ) != ISOLATED_ENV
            or {name for _, name in socket.if_nameindex()} != {"lo"}):
            raise ValueError()
        with Path("/proc/self/status").open("rb") as handle:
            raw = handle.read(65_537)
        if len(raw) > 65_536:
            raise ValueError()
        status = dict(line.split(":", 1) for line in raw.decode("ascii").splitlines() if ":" in line)
        if status.get("NoNewPrivs", "").strip() != "1" or any(
            int(status.get(key, "1").strip(), 16) != 0 for key in ("CapEff", "CapPrm", "CapAmb")
        ):
            raise ValueError()
    except Exception:
        raise IsolatedMcpError("MCP_ISOLATED_RUNTIME_REQUIRED") from None


def require_sdk_version() -> None:
    try:
        actual = version("mcp")
    except PackageNotFoundError:
        raise IsolatedMcpError("MCP_ISOLATED_SDK_MISSING") from None
    if actual != SDK_VERSION:
        raise IsolatedMcpError("MCP_ISOLATED_SDK_VERSION_MISMATCH")


def source_fingerprint() -> str:
    """只读本包固定源码，摘要不证明镜像批准或正式连接器身份。"""
    try:
        sources: dict[str, str] = {}
        for name in ("mcp_isolated_contracts", "mcp_isolated_runtime", "mcp_isolated_server", "mcp_isolated_client", "mcp_isolated_ipc"):
            with Path(__file__).with_name(name + ".py").open("rb") as handle:
                content = handle.read(65_537)
            if len(content) > 65_536:
                raise ValueError()
            sources[name] = hashlib.sha256(content).hexdigest()
        return discovery_digest({"implementation": "isolated_mock_stdio_v1", "sdk": SDK_VERSION,
            "protocol": PROTOCOL_VERSION, "server": SERVER_NAME, "server_version": SERVER_VERSION, "sources": sources})
    except Exception:
        raise IsolatedMcpError("MCP_ISOLATED_SOURCE_UNAVAILABLE") from None
