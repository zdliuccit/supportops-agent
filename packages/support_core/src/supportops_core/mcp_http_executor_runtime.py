"""独立HTTP执行前置检查与非秘密只读策略；不宣称完整OS隔离或实际服务批准。"""

import os
import stat
import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx

from supportops_core.mcp_http_authorization import HttpMcpAuthorizationError
from supportops_core.mcp_http_client import _object
from supportops_core.mcp_http_executor_contracts import HttpMcpExecutorPolicy, HttpMcpExecutorReport, HttpMcpExecutorRequest
from supportops_core.outbound_http import OutboundHttpPolicy, _addresses, _destination, _permitted_address

EXECUTOR_UID = 10004
EXECUTOR_ENV = {
    "PATH": "/opt/supportops/.venv/bin:/usr/local/bin:/usr/bin:/bin",
    "HOME": "/nonexistent", "LANG": "C.UTF-8", "SUPPORTOPS_HTTP_MCP_MODE": "one-shot-v1",
}
POLICY_DIR = Path("/etc/supportops-mcp")
POLICY_PATH = POLICY_DIR / "http-policy.json"


def require_http_executor_runtime() -> None:
    """仅在创建事件循环前检查；不接受继承的应用环境、额外描述符或无限资源。"""
    try:
        if (sys.platform != "linux" or os.geteuid() != EXECUTOR_UID or os.getegid() != EXECUTOR_UID
            or dict(os.environ) != EXECUTOR_ENV or not sys.flags.isolated or not sys.dont_write_bytecode):
            raise ValueError()
        import resource
        limits = ((resource.RLIMIT_CORE, 0), (resource.RLIMIT_NOFILE, 64), (resource.RLIMIT_NPROC, 16),
            (resource.RLIMIT_CPU, 22), (resource.RLIMIT_AS, 536_870_912))
        for kind, maximum in limits:
            soft, hard = resource.getrlimit(kind)
            if soft < 0 or hard < 0 or soft > hard or hard > maximum:
                raise ValueError()
        with Path("/proc/self/status").open("rb") as handle:
            raw = handle.read(65_537)
        if len(raw) > 65_536:
            raise ValueError()
        status = dict(line.split(":", 1) for line in raw.decode("ascii").splitlines() if ":" in line)
        if (status.get("NoNewPrivs", "").strip() != "1" or status.get("Seccomp", "").strip() != "2"
            or any(int(status.get(key, "1").strip(), 16) != 0 for key in ("CapEff", "CapPrm", "CapAmb"))):
            raise ValueError()
        for name in os.listdir("/proc/self/fd"):
            descriptor = int(name)
            if descriptor > 2:
                try:
                    os.fstat(descriptor)
                except OSError:
                    continue  # 枚举目录自身已关闭的临时描述符。
                raise ValueError()
        for descriptor in (0, 1):
            mode = os.fstat(descriptor).st_mode
            if not (stat.S_ISFIFO(mode) or stat.S_ISSOCK(mode)):
                raise ValueError()
    except Exception:
        raise HttpMcpAuthorizationError("MCP_HTTP_EXECUTOR_RUNTIME_REQUIRED") from None


def load_http_executor_policy() -> HttpMcpExecutorPolicy:
    """固定root拥有的普通文件；不跟随符号链接、不读取环境或任意路径。"""
    descriptor = None
    try:
        directory = POLICY_DIR.lstat()
        if (not stat.S_ISDIR(directory.st_mode) or directory.st_uid != 0
            or stat.S_IMODE(directory.st_mode) != 0o755):
            raise ValueError()
        descriptor = os.open(POLICY_PATH, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK)
        before = os.fstat(descriptor)
        if (not stat.S_ISREG(before.st_mode) or before.st_uid != 0 or before.st_nlink != 1
            or stat.S_IMODE(before.st_mode) != 0o444 or not 0 < before.st_size <= 32_768):
            raise ValueError()
        raw = os.read(descriptor, 32_769)
        after = os.fstat(descriptor)
        if ((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
            != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
            or len(raw) != before.st_size):
            raise ValueError()
        policy = HttpMcpExecutorPolicy.model_validate(_object(raw, max_bytes=32_768))
        if policy.authorization_policy.valid_until <= datetime.now(UTC):
            raise ValueError()
        # 在输入读取及任何broker/网络请求之前核对实际IP语义和目标规范形式。
        outbound_policy(policy)
        return policy
    except Exception:
        raise HttpMcpAuthorizationError("MCP_HTTP_EXECUTOR_POLICY_REJECTED") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def outbound_policy(policy: HttpMcpExecutorPolicy) -> OutboundHttpPolicy:
    endpoint = httpx.URL(policy.authorization_policy.resource)
    allowed, private = _addresses(frozenset(policy.allowed_ips)), _addresses(frozenset(policy.private_ips))
    if (len(allowed) != len(policy.allowed_ips) or len(private) != len(policy.private_ips)
        or any(str(address) not in policy.allowed_ips for address in allowed)
        or any(str(address) not in policy.private_ips for address in private)
        or any(address.is_global for address in private)
        or any(not _permitted_address(address, private) for address in allowed | private)):
        raise ValueError()
    result = OutboundHttpPolicy(allowed_hosts=frozenset({endpoint.host}),
        allowed_paths=frozenset({endpoint.raw_path.decode("ascii")}), allowed_ports=frozenset({endpoint.port or 443}),
        allowed_ips=frozenset(policy.allowed_ips), private_ips=frozenset(policy.private_ips))
    _destination(policy.authorization_policy.resource, result)
    return result


def require_executor_request(request: HttpMcpExecutorRequest, policy: HttpMcpExecutorPolicy) -> None:
    if (request.deployment_policy_digest != policy.policy_digest or request.request.policy != policy.authorization_policy
        or request.request.binding.expires_at <= datetime.now(UTC) or policy.authorization_policy.valid_until <= datetime.now(UTC)):
        raise HttpMcpAuthorizationError("MCP_HTTP_EXECUTOR_POLICY_REJECTED")


async def execute_http_executor_request(request: HttpMcpExecutorRequest, policy: HttpMcpExecutorPolicy) -> HttpMcpExecutorReport:
    """单次stdin与认证派发通道共用相同策略、取密、协议及输出过滤路径。"""
    from supportops_core.mcp_http_broker_ipc import call_http_mcp_tool_via_ipc

    require_executor_request(request, policy)
    observation = await call_http_mcp_tool_via_ipc(request.request, descriptor=request.descriptor,
        arguments=request.arguments, outbound_policy=outbound_policy(policy), platform_issuer=policy.platform_issuer,
        platform_audience=policy.platform_audience)
    return HttpMcpExecutorReport(nonce=request.nonce, dispatch_id=request.request.binding.dispatch_id,
        request_digest=request.request_digest, status="observed", observation=observation,
        invocation_attempted=True, result_confirmed=True)
