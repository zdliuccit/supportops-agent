"""应用身份到独立HTTP执行身份的单次认证派发；不传令牌、不启动任意进程或容器。"""

import asyncio
import os
import signal
import stat
from pathlib import Path

from supportops_core.mcp_http_authorization import HttpMcpAuthorizationError
from supportops_core.mcp_http_broker_ipc import _close, _peer, _runtime
from supportops_core.mcp_http_client import _object
from supportops_core.mcp_http_executor_contracts import (
    MAX_INPUT_BYTES, MAX_OUTPUT_BYTES, HttpMcpExecutorReport, HttpMcpExecutorRequest, unconfirmed_executor_report,
)
from supportops_core.tool_discovery_contracts import canonical_json

GATEWAY_UID = 10001
EXECUTOR_UID = 10004
SOCKET_DIR = Path("/run/supportops-mcp-executor")
SOCKET_PATH = SOCKET_DIR / "dispatch.sock"


def _path(*, endpoint: bool) -> None:
    directory = SOCKET_DIR.lstat()
    if (not stat.S_ISDIR(directory.st_mode) or directory.st_uid != EXECUTOR_UID or directory.st_gid != GATEWAY_UID
        or stat.S_IMODE(directory.st_mode) != 0o770):
        raise HttpMcpAuthorizationError("MCP_HTTP_IPC_PEER_REJECTED")
    if endpoint:
        channel = SOCKET_PATH.lstat()
        if (not stat.S_ISSOCK(channel.st_mode) or channel.st_uid != EXECUTOR_UID or channel.st_gid != GATEWAY_UID
            or stat.S_IMODE(channel.st_mode) != 0o660):
            raise HttpMcpAuthorizationError("MCP_HTTP_IPC_PEER_REJECTED")


async def dispatch_http_executor(request: HttpMcpExecutorRequest) -> HttpMcpExecutorReport:
    """只供已保存派发许可的可信应用桥接调用；一次连接/写入，无自动重连或重发。"""
    writer = None
    try:
        _runtime(GATEWAY_UID)
        _path(endpoint=True)
        # 首次await前冻结嵌套字典，调用者后续修改不改变发出的请求或回执关联。
        raw = canonical_json(request.model_dump(mode="json")).encode("utf-8") + b"\n"
        fixed = HttpMcpExecutorRequest.model_validate(_object(raw, max_bytes=MAX_INPUT_BYTES))
        async with asyncio.timeout(23):
            reader, writer = await asyncio.open_unix_connection(str(SOCKET_PATH), limit=MAX_OUTPUT_BYTES)
            _peer(writer, EXECUTOR_UID)
            _path(endpoint=True)
            writer.write(raw)
            await writer.drain()
            reply = HttpMcpExecutorReport.model_validate(_object(await reader.readuntil(b"\n"), max_bytes=MAX_OUTPUT_BYTES))
            if (reply.nonce != fixed.nonce or reply.dispatch_id != fixed.request.binding.dispatch_id
                or reply.request_digest != fixed.request_digest):
                raise ValueError()
            if reply.observation is not None and (reply.observation.source_record_id != fixed.request.policy.source_record_id
                or reply.observation.descriptor_digest != fixed.request.binding.descriptor_digest):
                raise ValueError()
            return reply
    except HttpMcpAuthorizationError:
        raise
    except Exception:
        raise HttpMcpAuthorizationError("MCP_HTTP_EXECUTOR_UNCONFIRMED") from None
    finally:
        if writer is not None:
            try:
                await _close(writer)
            except Exception:
                pass  # _close已abort，不用清理故障覆盖原派发未知。


async def serve_one_http_dispatch() -> int:
    """固定10004进程最多消费一个10001连接；退出保留DB派发事实，不负责进程重启。"""
    from supportops_core.mcp_http_executor_runtime import (
        execute_http_executor_request, load_http_executor_policy, require_executor_request,
    )

    _runtime(EXECUTOR_UID)
    _path(endpoint=False)
    policy = load_http_executor_policy()
    try:
        SOCKET_PATH.lstat()
    except FileNotFoundError:
        pass
    else:
        raise HttpMcpAuthorizationError("MCP_HTTP_IPC_PEER_REJECTED")
    loop, task = asyncio.get_running_loop(), asyncio.current_task()
    if task is None:
        return 3
    finished: asyncio.Future[int] = loop.create_future()
    accepted = False
    handler_task: asyncio.Task[None] | None = None
    server: asyncio.Server | None = None
    created: os.stat_result | None = None
    active_writer: asyncio.StreamWriter | None = None

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        nonlocal accepted, handler_task, active_writer
        acquired = False
        request: HttpMcpExecutorRequest | None = None
        attempted, exit_code = False, 3
        try:
            _peer(writer, GATEWAY_UID)
            if accepted:
                return
            accepted, acquired = True, True
            handler_task, active_writer = asyncio.current_task(), writer
            if server is not None:
                server.close()  # 第一项认证连接即消费本进程，坏帧也不接受第二项。
            try:
                async with asyncio.timeout(20):
                    async with asyncio.timeout(3):
                        request = HttpMcpExecutorRequest.model_validate(_object(
                            await reader.readuntil(b"\n"), max_bytes=MAX_INPUT_BYTES))
                    require_executor_request(request, policy)
                    attempted = True
                    report = await execute_http_executor_request(request, policy)
                    exit_code = 0
            except asyncio.CancelledError:
                report, exit_code = unconfirmed_executor_report("MCP_HTTP_EXECUTOR_INTERRUPTED", request, attempted), 130
            except HttpMcpAuthorizationError as exc:
                report = unconfirmed_executor_report(exc.code, request, attempted)
            except Exception:
                report = unconfirmed_executor_report("MCP_HTTP_EXECUTOR_UNCONFIRMED", request, attempted)
            encoded = canonical_json(report.model_dump(mode="json")).encode("utf-8") + b"\n"
            _object(encoded, max_bytes=MAX_OUTPUT_BYTES)
            _peer(writer, GATEWAY_UID)
            async with asyncio.timeout(2):
                writer.write(encoded)
                await writer.drain()
        except Exception:
            writer.transport.abort()
            exit_code = 4
        finally:
            try:
                await _close(writer)
            except Exception:
                pass
            if acquired and not finished.done():
                finished.set_result(exit_code)

    loop.add_signal_handler(signal.SIGTERM, task.cancel)
    loop.add_signal_handler(signal.SIGINT, task.cancel)
    try:
        server = await asyncio.start_unix_server(handle, path=str(SOCKET_PATH), limit=MAX_INPUT_BYTES, start_serving=False)
        os.chown(SOCKET_PATH, EXECUTOR_UID, GATEWAY_UID)
        os.chmod(SOCKET_PATH, 0o660)
        _path(endpoint=True)
        created = SOCKET_PATH.lstat()
        async with server, asyncio.timeout(23):
            await server.start_serving()
            return await finished
    except (Exception, asyncio.CancelledError):
        return 4
    finally:
        loop.remove_signal_handler(signal.SIGTERM)
        loop.remove_signal_handler(signal.SIGINT)
        if server is not None:
            server.close()
        if active_writer is not None:
            active_writer.transport.abort()
        if handler_task is not None and not handler_task.done():
            handler_task.cancel()
            try:
                async with asyncio.timeout(1):
                    await handler_task
            except (Exception, asyncio.CancelledError):
                pass
        if created is not None:
            try:
                current = SOCKET_PATH.lstat()
                if (stat.S_ISSOCK(current.st_mode) and current.st_uid == EXECUTOR_UID
                    and (current.st_dev, current.st_ino) == (created.st_dev, created.st_ino)):
                    SOCKET_PATH.unlink()  # 仅本进程创建且仍同inode的临时socket，不清理未知旧端点。
            except FileNotFoundError:
                pass
