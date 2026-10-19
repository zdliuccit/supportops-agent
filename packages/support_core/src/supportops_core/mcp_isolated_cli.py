"""隔离MCP的单次运维入口；不加载Settings、数据库、Redis或模型客户端。"""

import asyncio
import json
import logging
import sys
from typing import Literal

from supportops_core.mcp_isolated_client import run_isolated_mcp
from supportops_core.mcp_isolated_runtime import IsolatedMcpError


def _emit(value: dict[str, object]) -> bool:
    try:
        sys.stdout.write(json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n")
        sys.stdout.flush()
        return True
    except (OSError, UnicodeError):
        return False


def _failure(code: str, exit_code: int) -> int:
    emitted = _emit({"schema_version": "isolated_mcp_failure_v1", "error_code": code,
        "result_confirmed": False, "business_execution_enabled": False})
    return exit_code if emitted else 4


def main() -> int:
    logging.disable(logging.CRITICAL)
    arguments = sys.argv[1:]
    if arguments == ["--help"]:
        return 0 if _emit({"commands": ["catalog", "read-status"], "runtime": "isolated_mock_only",
            "business_execution_enabled": False}) else 4
    if arguments not in (["catalog"], ["read-status"], ["serve"]):
        return _failure("MCP_ISOLATED_ARGUMENTS_INVALID", 2)
    try:
        if arguments == ["serve"]:
            from supportops_core.mcp_isolated_ipc import serve_probes
            asyncio.run(serve_probes())
            return 0
        operation: Literal["catalog", "read_status"] = "catalog" if arguments == ["catalog"] else "read_status"
        report = asyncio.run(run_isolated_mcp(operation))
    except (KeyboardInterrupt, asyncio.CancelledError):
        return _failure("MCP_ISOLATED_INTERRUPTED", 130)
    except IsolatedMcpError as exc:
        return _failure(exc.code, 3)
    except Exception:
        return _failure("MCP_ISOLATED_FAILED", 3)
    # 输出失败不重新发现/调用；这不证明Mock调用未发生。
    return 0 if _emit(report.model_dump(mode="json")) else 4


if __name__ == "__main__":
    raise SystemExit(main())
