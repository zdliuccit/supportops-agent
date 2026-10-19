"""固定身份单次HTTP执行入口；stdin只接封闭请求，stdout只接有界脱敏观察或固定失败码。"""

import asyncio
import logging
import os
import signal
import sys

from supportops_core.mcp_http_authorization import HttpMcpAuthorizationError
from supportops_core.mcp_http_client import _object
from supportops_core.mcp_http_executor_contracts import (
    MAX_INPUT_BYTES, MAX_OUTPUT_BYTES, HttpMcpExecutorReport, HttpMcpExecutorRequest,
    unconfirmed_executor_report,
)
from supportops_core.mcp_http_executor_runtime import (
    execute_http_executor_request, load_http_executor_policy, require_executor_request, require_http_executor_runtime,
)
from supportops_core.tool_discovery_contracts import canonical_json


async def _ready(descriptor: int, *, writing: bool) -> None:
    loop = asyncio.get_running_loop()
    ready: asyncio.Future[None] = loop.create_future()
    def complete() -> None:
        if not ready.done():
            ready.set_result(None)
    if writing:
        loop.add_writer(descriptor, complete)
    else:
        loop.add_reader(descriptor, complete)
    try:
        await ready
    finally:
        if writing:
            loop.remove_writer(descriptor)
        else:
            loop.remove_reader(descriptor)


async def _read_request() -> HttpMcpExecutorRequest:
    try:
        async with asyncio.timeout(3):
            os.set_blocking(0, False)
            raw = bytearray()
            while True:
                try:
                    part = os.read(0, min(65_536, MAX_INPUT_BYTES + 1 - len(raw)))
                except BlockingIOError:
                    await _ready(0, writing=False)
                    continue
                if not part:
                    break
                raw.extend(part)
                if len(raw) > MAX_INPUT_BYTES:
                    raise ValueError()
                if b"\n" in raw:
                    break  # 首帧结束即处理，不等待容器stdin的EOF，也不接收第二次调用。
            if not raw.endswith(b"\n") or raw.count(b"\n") != 1:
                raise ValueError()
            return HttpMcpExecutorRequest.model_validate(_object(bytes(raw), max_bytes=MAX_INPUT_BYTES))
    except Exception:
        raise HttpMcpAuthorizationError("MCP_HTTP_EXECUTOR_REQUEST_REJECTED") from None


async def _emit(report: HttpMcpExecutorReport) -> bool:
    try:
        raw = canonical_json(report.model_dump(mode="json")).encode("utf-8") + b"\n"
        if len(raw) > MAX_OUTPUT_BYTES:
            return False
        async with asyncio.timeout(2):
            os.set_blocking(1, False)
            remaining = memoryview(raw)
            while remaining:
                try:
                    written = os.write(1, remaining)
                except BlockingIOError:
                    await _ready(1, writing=True)
                    continue
                if written <= 0:
                    return False
                remaining = remaining[written:]
        return True
    except (Exception, asyncio.CancelledError):
        # 输出未知不再次发送，不能据此推断HTTP未发生。
        return False


async def _run() -> int:
    request: HttpMcpExecutorRequest | None = None
    attempted = False
    loop, task = asyncio.get_running_loop(), asyncio.current_task()
    if task is None:
        return 3
    loop.add_signal_handler(signal.SIGTERM, task.cancel)
    loop.add_signal_handler(signal.SIGINT, task.cancel)
    try:
        try:
            async with asyncio.timeout(20):
                policy = load_http_executor_policy()
                request = await _read_request()
                require_executor_request(request, policy)
                attempted = True  # 进入调用适配器，不证明取密/字节出站或远端执行。
                report = await execute_http_executor_request(request, policy)
                exit_code = 0
        except asyncio.CancelledError:
            report, exit_code = unconfirmed_executor_report("MCP_HTTP_EXECUTOR_INTERRUPTED", request, attempted), 130
        except HttpMcpAuthorizationError as exc:
            report, exit_code = unconfirmed_executor_report(exc.code, request, attempted), 3
        except Exception:
            report, exit_code = unconfirmed_executor_report("MCP_HTTP_EXECUTOR_UNCONFIRMED", request, attempted), 3
        return exit_code if await _emit(report) else 4
    finally:
        loop.remove_signal_handler(signal.SIGTERM)
        loop.remove_signal_handler(signal.SIGINT)


def main() -> int:
    logging.disable(logging.CRITICAL)
    try:
        # 不保留SDK/运行时原始stderr；结构化结果只通过stdout的一次有界写入。
        descriptor = os.open(os.devnull, os.O_WRONLY | os.O_CLOEXEC)
        try:
            os.dup2(descriptor, 2)
        finally:
            os.close(descriptor)
        require_http_executor_runtime()
        if sys.argv[1:] not in (["call"], ["serve-once"]):
            raise HttpMcpAuthorizationError("MCP_HTTP_EXECUTOR_REQUEST_REJECTED")
        os.umask(0o077)
        def deadline(_signum: int, _frame: object) -> None:
            # 不等待可能已阻塞的清理；父进程必须按未确认处理原dispatch，不能重发。
            os._exit(124)
        signal.signal(signal.SIGALRM, deadline)
        signal.setitimer(signal.ITIMER_REAL, 25)
    except Exception:
        # 前置身份/描述符失败时不读取stdin或策略，不向不可信输出位置写数据。
        return 2
    try:
        if sys.argv[1:] == ["serve-once"]:
            from supportops_core.mcp_http_executor_ipc import serve_one_http_dispatch
            return asyncio.run(serve_one_http_dispatch())
        return asyncio.run(_run())
    except (Exception, KeyboardInterrupt, asyncio.CancelledError):
        return 4  # 输出/事件循环中断仍不重发，不打印原异常。
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)


if __name__ == "__main__":
    raise SystemExit(main())
