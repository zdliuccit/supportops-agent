"""同机Unix socket的固定Mock探测通道；只读诊断，不是业务连接器执行broker。"""

import asyncio
import json
import os
import socket
import stat
import struct
from pathlib import Path
from uuid import UUID

from supportops_core.mcp_isolated_contracts import IsolatedMcpReport
from supportops_core.mcp_isolated_runtime import IsolatedMcpError, require_isolated_runtime

SOCKET_DIR = Path("/run/supportops-mcp")
SOCKET_PATH = SOCKET_DIR / "control.sock"


def _object(raw: bytes) -> dict[str, object]:
    def pairs(items: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in items:
            if key in result:
                raise ValueError()
            result[key] = value
        return result
    def invalid(_value: str) -> object:
        raise ValueError()
    value = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs, parse_constant=invalid)
    if not isinstance(value, dict):
        raise ValueError()
    return value


def _peer(writer: asyncio.StreamWriter, uid: int) -> None:
    channel = writer.get_extra_info("socket")
    if channel is None or not hasattr(socket, "SO_PEERCRED"):
        raise IsolatedMcpError("MCP_IPC_PEER_REJECTED")
    _pid, actual_uid, _gid = struct.unpack("3i", channel.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
    if actual_uid != uid:
        raise IsolatedMcpError("MCP_IPC_PEER_REJECTED")


async def dispatch_probe(request_id: UUID) -> IsolatedMcpReport:
    """只接受任务UUID；不传令牌、环境、URL、任意操作或业务参数。"""
    writer = None
    try:
        directory = SOCKET_DIR.lstat()
        endpoint = SOCKET_PATH.lstat()
        if (not stat.S_ISDIR(directory.st_mode) or directory.st_uid != 10002 or directory.st_gid != 10001
            or stat.S_IMODE(directory.st_mode) != 0o770 or not stat.S_ISSOCK(endpoint.st_mode)
            or endpoint.st_uid != 10002 or endpoint.st_gid != 10001 or stat.S_IMODE(endpoint.st_mode) != 0o660):
            raise IsolatedMcpError("MCP_IPC_PATH_REJECTED")
        async with asyncio.timeout(12):
            reader, writer = await asyncio.open_unix_connection(str(SOCKET_PATH), limit=32_768)
            _peer(writer, 10002)
            request = {"schema_version": "isolated_mcp_dispatch_v1", "request_id": str(request_id), "operation": "read_status"}
            writer.write(json.dumps(request, separators=(",", ":")).encode()+b"\n")
            await writer.drain()
            raw = await reader.readuntil(b"\n")
            if len(raw) > 32_768:
                raise ValueError()
            value = _object(raw)
            if set(value) != {"schema_version", "request_id", "report"} or value["schema_version"] != "isolated_mcp_dispatch_result_v1" or value["request_id"] != str(request_id):
                raise ValueError()
            report = IsolatedMcpReport.model_validate(value["report"])
            if report.operation != "read_status" or report.observation is None:
                raise ValueError()
            return report
    except IsolatedMcpError:
        raise
    except Exception:
        raise IsolatedMcpError("MCP_IPC_RESULT_UNCONFIRMED") from None
    finally:
        if writer is not None:
            writer.close()
            try:
                async with asyncio.timeout(1):
                    await writer.wait_closed()
            except Exception:
                writer.transport.abort()


async def serve_probes() -> None:
    require_isolated_runtime()
    from supportops_core.mcp_isolated_client import run_isolated_mcp

    directory = SOCKET_DIR.lstat()
    if (not stat.S_ISDIR(directory.st_mode) or directory.st_uid != 10002 or directory.st_gid != 10001
        or stat.S_IMODE(directory.st_mode) != 0o770):
        raise IsolatedMcpError("MCP_IPC_PATH_REJECTED")
    if SOCKET_PATH.exists():
        existing = SOCKET_PATH.lstat()
        if not stat.S_ISSOCK(existing.st_mode) or existing.st_uid != 10002:
            raise IsolatedMcpError("MCP_IPC_PATH_REJECTED")
        SOCKET_PATH.unlink()
    busy = False

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        nonlocal busy
        acquired = False
        try:
            _peer(writer, 10001)
            if busy:
                return
            busy = True
            acquired = True
            async with asyncio.timeout(15):
                raw = await reader.readuntil(b"\n")
                if len(raw) > 1024:
                    return
                value = _object(raw)
                if set(value) != {"schema_version", "request_id", "operation"} or value["schema_version"] != "isolated_mcp_dispatch_v1" or value["operation"] != "read_status":
                    return
                request_id = UUID(str(value["request_id"]))
                if str(request_id) != value["request_id"]:
                    return
                report = await run_isolated_mcp("read_status")
                reply = {"schema_version": "isolated_mcp_dispatch_result_v1", "request_id": str(request_id), "report": report.model_dump(mode="json")}
                encoded = json.dumps(reply, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()+b"\n"
                if len(encoded) > 32_768:
                    return
                writer.write(encoded)
                await writer.drain()
        except Exception:
            # 拒绝/忙/超时均关闭连接，由持久化任务记录固定未确认，不回传原异常。
            writer.transport.abort()
        finally:
            if acquired:
                busy = False
            writer.close()
            try:
                async with asyncio.timeout(1):
                    await writer.wait_closed()
            except Exception:
                writer.transport.abort()

    server = await asyncio.start_unix_server(handle, path=str(SOCKET_PATH), limit=1024, start_serving=False)
    os.chown(SOCKET_PATH, -1, 10001)
    os.chmod(SOCKET_PATH, 0o660)
    async with server:
        await server.serve_forever()
