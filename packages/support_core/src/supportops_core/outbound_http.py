"""固定服务端目标的 HTTPS 传输边界；不承担业务授权、审批或写重试。"""

import asyncio
import ipaddress
import json
import math
import re
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Literal

import httpx
from pydantic import SecretStr

IpAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
_PRIVATE_NETWORKS = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("fc00::/7"),
)
_HOST_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")
# 子进程只加载标准库；固定程序、有限输出，不继承应用环境或打印解析异常。
_DNS_PROGRAM = """
import json, socket, sys
try:
    addresses = sorted({str(item[4][0]) for item in socket.getaddrinfo(
        sys.argv[1], int(sys.argv[2]), type=socket.SOCK_STREAM)})
    if not 0 < len(addresses) <= 32 or any(len(value) > 64 for value in addresses):
        raise ValueError()
    sys.stdout.write(json.dumps(addresses))
except Exception:
    sys.exit(1)
"""


class OutboundHttpError(Exception):
    """仅暴露固定码/HTTP状态，不包含目标、令牌、异常或响应正文。"""

    def __init__(self, code: str, *, http_status: int | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.http_status = http_status


@dataclass(frozen=True, slots=True)
class OutboundHttpPolicy:
    """由受控服务端配置创建，空域名集合拒绝全部请求。"""

    allowed_hosts: frozenset[str]
    # 使用URL.raw_path的ASCII形式精确匹配，不允许模型切换同域其他接口。
    allowed_paths: frozenset[str]
    allowed_ports: frozenset[int] = frozenset({443})
    allowed_ips: frozenset[str] = frozenset()
    # 只允许逐个批准的RFC1918/ULA地址，无全局允许私网开关。
    private_ips: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class OutboundHttpResponse:
    status_code: int
    body: bytes = field(repr=False)


def _host(value: str) -> str:
    if not value or len(value) > 253 or any(char.isspace() for char in value):
        raise OutboundHttpError("OUTBOUND_HOST_INVALID")
    if any(char in value for char in "/\\@?#%"):
        raise OutboundHttpError("OUTBOUND_HOST_INVALID")
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        pass
    try:
        canonical = value.rstrip(".").encode("idna").decode("ascii").lower()
    except UnicodeError:
        raise OutboundHttpError("OUTBOUND_HOST_INVALID") from None
    if not canonical or len(canonical) > 253 or any(
        _HOST_LABEL.fullmatch(label) is None for label in canonical.split(".")
    ):
        raise OutboundHttpError("OUTBOUND_HOST_INVALID")
    return canonical


def _addresses(values: frozenset[str]) -> frozenset[IpAddress]:
    if len(values) > 32 or any("%" in value for value in values):
        raise OutboundHttpError("OUTBOUND_IP_POLICY_INVALID")
    try:
        return frozenset(ipaddress.ip_address(value) for value in values)
    except ValueError:
        raise OutboundHttpError("OUTBOUND_IP_POLICY_INVALID") from None


def _permitted_address(address: IpAddress, private: frozenset[IpAddress]) -> bool:
    # 不让IPv4封装/过渡地址绕过对内层目标的私网或回环限制。
    if isinstance(address, ipaddress.IPv6Address) and (
        address.ipv4_mapped is not None or address.sixtofour is not None
        or address.teredo is not None
    ):
        return False
    if (address.is_loopback or address.is_link_local or address.is_multicast
        or address.is_unspecified or address.is_reserved):
        return False
    if address.is_global:
        return True
    return address in private and any(
        address.version == network.version and address in network
        for network in _PRIVATE_NETWORKS
    )


def _destination(url: str, policy: OutboundHttpPolicy) -> tuple[httpx.URL, str]:
    if (not url or len(url) > 2048 or "\\" in url
        or any(ord(char) <= 32 or ord(char) == 127 for char in url)):
        raise OutboundHttpError("OUTBOUND_URL_INVALID")
    try:
        endpoint = httpx.URL(url)
    except httpx.InvalidURL:
        raise OutboundHttpError("OUTBOUND_URL_INVALID") from None
    if (endpoint.scheme != "https" or not endpoint.host or endpoint.userinfo
        or endpoint.query or endpoint.fragment):
        raise OutboundHttpError("OUTBOUND_URL_INVALID")
    if not 0 < len(policy.allowed_hosts) <= 100 or not policy.allowed_ports:
        raise OutboundHttpError("OUTBOUND_TARGET_DENIED")
    if any(type(port) is not int or not 1 <= port <= 65535 for port in policy.allowed_ports):
        raise OutboundHttpError("OUTBOUND_PORT_POLICY_INVALID")
    hostname = _host(endpoint.raw_host.decode("ascii"))
    if hostname not in {_host(value) for value in policy.allowed_hosts}:
        raise OutboundHttpError("OUTBOUND_TARGET_DENIED")
    if (not 0 < len(policy.allowed_paths) <= 100
        or endpoint.raw_path.decode("ascii") not in policy.allowed_paths):
        raise OutboundHttpError("OUTBOUND_TARGET_DENIED")
    port = endpoint.port if endpoint.port is not None else 443
    if port not in policy.allowed_ports:
        raise OutboundHttpError("OUTBOUND_TARGET_DENIED")
    return endpoint, hostname


async def _resolve(hostname: str, port: int) -> frozenset[IpAddress]:
    try:
        return frozenset({ipaddress.ip_address(hostname)})
    except ValueError:
        pass
    # 线程中的getaddrinfo不能协作取消，独立进程使超时后可结束系统解析。
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-I", "-S", "-c", _DNS_PROGRAM, hostname, str(port),
        env={}, close_fds=True, stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        stdout, _ = await process.communicate()
        if process.returncode != 0 or len(stdout) > 4096:
            raise OutboundHttpError("OUTBOUND_DNS_FAILED")
        values = json.loads(stdout)
        if (not isinstance(values, list) or not 0 < len(values) <= 32
            or any(not isinstance(value, str) for value in values)):
            raise OutboundHttpError("OUTBOUND_DNS_FAILED")
        return _addresses(frozenset(values))
    finally:
        if process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            await process.wait()


async def request_fixed_json(
    *,
    url: str,
    policy: OutboundHttpPolicy,
    method: Literal["GET", "POST"],
    payload: bytes = b"",
    bearer_token: SecretStr | None = None,
    timeout_seconds: float = 10,
    max_request_bytes: int = 1_000_000,
    max_response_bytes: int = 65_536,
    accepted_statuses: frozenset[int] = frozenset({200}),
    mcp_json_profile: bool = False,
    before_send: Callable[[], Awaitable[None]] | None = None,
) -> OutboundHttpResponse:
    """每次复检目标并固定IP；调用方只能传服务端固定URL，不能转发模型URL/头。"""
    endpoint, hostname = _destination(url, policy)
    allowed, private = _addresses(policy.allowed_ips), _addresses(policy.private_ips)
    if any(not _permitted_address(address, private) for address in allowed | private):
        raise OutboundHttpError("OUTBOUND_IP_POLICY_INVALID")
    if (type(timeout_seconds) not in {int, float}
        or not 0 < timeout_seconds <= 30 or not math.isfinite(timeout_seconds)
        or type(max_request_bytes) is not int or not 0 < max_request_bytes <= 4_000_000
        or type(max_response_bytes) is not int or not 0 < max_response_bytes <= 4_000_000
        or method not in {"GET", "POST"} or not isinstance(payload, bytes) or type(mcp_json_profile) is not bool
        or mcp_json_profile and method != "POST"
        or not accepted_statuses
        or any(type(status) is not int or not 200 <= status < 300 for status in accepted_statuses)):
        raise OutboundHttpError("OUTBOUND_LIMIT_INVALID")
    if len(payload) > max_request_bytes or (method == "GET" and payload):
        raise OutboundHttpError("OUTBOUND_REQUEST_TOO_LARGE")
    host_header = f"[{hostname}]" if ":" in hostname else hostname
    if endpoint.port is not None and endpoint.port != 443:
        host_header = f"{host_header}:{endpoint.port}"
    headers = {"Host": host_header, "Content-Type": "application/json",
        "Accept": "application/json", "Accept-Encoding": "identity"}
    if mcp_json_profile:
        # 固定、无会话的MCP配置，不接受任意头或SDK自动重连/重定向。
        headers["Accept"] = "application/json, text/event-stream"
        headers["MCP-Protocol-Version"] = "2025-11-25"
    if bearer_token is not None:
        token = bearer_token.get_secret_value()
        if not token or len(token) > (8192 if mcp_json_profile else 4096) or any(ord(char) < 33 or ord(char) > 126 for char in token):
            raise OutboundHttpError("OUTBOUND_TOKEN_INVALID")
        headers["Authorization"] = f"Bearer {token}"
    try:
        async with asyncio.timeout(timeout_seconds):
            resolved = await _resolve(hostname, endpoint.port or 443)
            if (not resolved or any(not _permitted_address(address, private) for address in resolved)
                or (allowed and not resolved <= allowed)):
                raise OutboundHttpError("OUTBOUND_ADDRESS_DENIED")
            pinned = str(sorted(resolved, key=str)[0])
            if before_send is not None:
                # DNS等待不延长业务许可，连接固定IP前再次复查。
                await before_send()
            async with httpx.AsyncClient(
                timeout=timeout_seconds, trust_env=False, follow_redirects=False,
                transport=httpx.AsyncHTTPTransport(retries=0, trust_env=False),
            ) as client:
                async with client.stream(
                    method, endpoint.copy_with(host=pinned), content=payload, headers=headers,
                    extensions={"sni_hostname": hostname},
                ) as response:
                    if 300 <= response.status_code < 400:
                        raise OutboundHttpError(
                            "OUTBOUND_REDIRECT_DENIED", http_status=response.status_code,
                        )
                    if response.status_code not in accepted_statuses:
                        raise OutboundHttpError(
                            "OUTBOUND_HTTP_REJECTED", http_status=response.status_code,
                        )
                    if mcp_json_profile and "mcp-session-id" in response.headers:
                        raise OutboundHttpError("OUTBOUND_MCP_SESSION_UNSUPPORTED")
                    content_type = response.headers.get("content-type", "").split(";", 1)[0]
                    empty_notification = mcp_json_profile and response.status_code == 202
                    if not empty_notification and content_type.strip().lower() != "application/json":
                        raise OutboundHttpError("OUTBOUND_RESPONSE_TYPE_INVALID")
                    if response.headers.get("content-encoding", "identity").lower() != "identity":
                        raise OutboundHttpError("OUTBOUND_RESPONSE_ENCODING_DENIED")
                    body = bytearray()
                    async for chunk in response.aiter_raw():
                        if len(body) + len(chunk) > max_response_bytes:
                            raise OutboundHttpError("OUTBOUND_RESPONSE_TOO_LARGE")
                        body.extend(chunk)
                    if empty_notification and body:
                        raise OutboundHttpError("OUTBOUND_MCP_NOTIFICATION_REJECTED")
                    return OutboundHttpResponse(response.status_code, bytes(body))
    except TimeoutError:
        raise OutboundHttpError("OUTBOUND_TIMEOUT") from None
    except (OSError, ValueError, httpx.HTTPError):
        raise OutboundHttpError("OUTBOUND_TRANSPORT_FAILED") from None
