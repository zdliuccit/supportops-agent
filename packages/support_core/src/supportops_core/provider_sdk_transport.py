"""固定SDK的一次有界POST；只有可信执行者在原消费提交后显式调用。"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal, TypeVar

import httpx
import httpx2
import openai
from pydantic import SecretStr

from supportops_core.outbound_http import OutboundHttpPolicy, _addresses, _destination, _permitted_address, _resolve

SDKErrorPrefix = Literal["SPECIALIST_SDK", "ROUTING_SDK"]
PROVIDER_RESPONSE_BYTES_MAX = 2_097_152
_T = TypeVar("_T")
# 取消后尚未退出的I/O保持强引用，不把协作取消称为物理停止。
_UNFINISHED_IO: set[asyncio.Task[None]] = set()


def assert_provider_sdk_supported(prefix: SDKErrorPrefix) -> None:
    if openai.__version__ != "3.8.0":
        raise ValueError(prefix + "_VERSION_UNSUPPORTED")
    if any(logging.getLogger(name).isEnabledFor(logging.DEBUG)
        for name in ("openai._base_client", "httpx", "httpx2", "httpcore")):
        raise ValueError(prefix + "_VERBOSE_LOGGING_UNSUPPORTED")


@dataclass(frozen=True, slots=True)
class ProviderSDKObservation:
    raw_body: bytes | None = field(repr=False)
    started_at: datetime | None
    finished_at: datetime | None
    io_stop_confirmed: bool


class OneProviderPostTransport(httpx2.AsyncBaseTransport):
    def __init__(self, *, target: str, body: bytes, key: SecretStr, policy: OutboundHttpPolicy,
        streaming: bool, organization: str | None, fence: Callable[[], Awaitable[None]], expires_at: datetime,
        prefix: SDKErrorPrefix, continuous_fence: bool = False,
        entry_expires_at: datetime | None = None,
    ) -> None:
        self.endpoint, self.hostname = _destination(target, policy)
        self._body, self._key, self._policy = body, key, policy
        self._streaming, self._organization, self._fence = streaming, organization, fence
        self._expires_at, self._prefix, self._continuous = expires_at, prefix, continuous_fence
        self._entry_expires_at = entry_expires_at
        self._end = asyncio.get_running_loop().time() + max(0, (expires_at - datetime.now(UTC)).total_seconds())
        self._inner = httpx.AsyncHTTPTransport(retries=0, trust_env=False,
            limits=httpx.Limits(max_connections=1, max_keepalive_connections=0))
        self._entered, self._closed = False, False
        self.started_at: datetime | None = None
        self.finished_at: datetime | None = None
        self.raw_body: bytes | None = None
        self.io_stop_confirmed = True

    def _remaining(self) -> float:
        value = min(self._end - asyncio.get_running_loop().time(), (self._expires_at - datetime.now(UTC)).total_seconds())
        if value <= 0:
            raise ValueError(self._prefix + "_WINDOW_EXPIRED")
        return value

    async def _io(self, operation: Awaitable[_T]) -> _T:
        if not self._continuous:
            return await operation
        pending = asyncio.ensure_future(operation)
        returned = False
        try:
            while True:
                done, _ = await asyncio.wait((pending,), timeout=min(0.5, self._remaining()))
                if done:
                    result = await pending
                    returned = True
                    return result
                # 在原调用协程读取真实middleware材料；子任务只承担这次I/O，不持DB会话。
                await self._fence()
        finally:
            if not returned:
                pending.cancel()
                try:
                    await asyncio.wait((pending,), timeout=0.1)
                finally:
                    if not pending.done():
                        self.io_stop_confirmed = False

                    async def dispose() -> None:
                        try:
                            result = await asyncio.shield(pending)
                            if isinstance(result, httpx.Response):
                                await result.aclose()
                        except BaseException:
                            pass  # 不回显运输异常或迟到正文，不重发。

                    closing = asyncio.create_task(dispose())
                    _UNFINISHED_IO.add(closing)
                    closing.add_done_callback(_UNFINISHED_IO.discard)

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        if (self._entered or self._closed or request.method != "POST" or str(request.url) != str(self.endpoint)
            or self._entry_expires_at is not None and datetime.now(UTC) >= self._entry_expires_at
            or request.content != self._body):
            raise ValueError(self._prefix + "_REQUEST_REJECTED")
        self._entered = True
        token = self._key.get_secret_value()
        if (not 1 <= len(token) <= 4096 or any(ord(char) < 33 or ord(char) > 126 for char in token)
            or self._organization is not None and (len(self._organization) > 200
                or any(ord(char) < 33 or ord(char) > 126 for char in self._organization))):
            raise ValueError(self._prefix + "_AUTHENTICATION_INVALID")
        async with asyncio.timeout(self._remaining()):
            await self._fence()
            allowed, private = _addresses(self._policy.allowed_ips), _addresses(self._policy.private_ips)
            if any(not _permitted_address(address, private) for address in allowed | private):
                raise ValueError(self._prefix + "_ADDRESS_DENIED")
            resolved = await _resolve(self.hostname, self.endpoint.port or 443)
            if (not resolved or any(not _permitted_address(address, private) for address in resolved)
                or allowed and not resolved <= allowed):
                raise ValueError(self._prefix + "_ADDRESS_DENIED")
            await self._fence()
            timeout = self._remaining()
            host = f"[{self.hostname}]" if ":" in self.hostname else self.hostname
            if self.endpoint.port not in (None, 443):
                host += f":{self.endpoint.port}"
            headers = {"Host": host, "Content-Type": "application/json", "Accept-Encoding": "identity",
                "Accept": "text/event-stream" if self._streaming else "application/json", "Authorization": "Bearer " + token}
            if self._organization is not None:
                headers["OpenAI-Organization"] = self._organization
            wire = httpx.Request("POST", self.endpoint.copy_with(host=str(sorted(resolved, key=str)[0])),
                headers=headers, content=self._body, extensions={"sni_hostname": self.hostname,
                    "timeout": {part: timeout for part in ("connect", "read", "write", "pool")}})
            self.started_at = datetime.now(UTC)
            response = await self._io(self._inner.handle_async_request(wire))
            try:
                if response.status_code != 200:
                    return httpx2.Response(response.status_code, content=b"{}", headers={"Content-Type": "application/json"}, request=request)
                expected = "text/event-stream" if self._streaming else "application/json"
                if (response.headers.get("content-type", "").split(";", 1)[0].strip().lower() != expected
                    or response.headers.get("content-encoding", "identity").lower() != "identity"):
                    raise ValueError(self._prefix + "_RESPONSE_TYPE_INVALID")
                data = bytearray()
                iterator = response.aiter_raw().__aiter__()
                while True:
                    try:
                        part = await self._io(iterator.__anext__())
                    except StopAsyncIteration:
                        break
                    if len(data) + len(part) > PROVIDER_RESPONSE_BYTES_MAX:
                        raise ValueError(self._prefix + "_RESPONSE_TOO_LARGE")
                    data.extend(part)
                    self._remaining()
                self.finished_at, self.raw_body = datetime.now(UTC), bytes(data)
                return httpx2.Response(200, content=self.raw_body, headers={"Content-Type": expected}, request=request)
            finally:
                await response.aclose()

    async def aclose(self) -> None:
        self._closed = True
        await self._inner.aclose()


async def invoke_provider_sdk_once(*, base_url: str, path: Literal["responses", "chat/completions"], body: bytes,
    key: SecretStr, organization: str | None, streaming: bool, policy: OutboundHttpPolicy,
    fence: Callable[[], Awaitable[None]], expires_at: datetime, prefix: SDKErrorPrefix,
    continuous_fence: bool = False, observe: Callable[[ProviderSDKObservation], None] | None = None,
    entry_expires_at: datetime | None = None,
) -> ProviderSDKObservation:
    """低层一次POST；observe在取消路径仍可保留本次实际观察，不能借历史再次调用。"""
    assert_provider_sdk_supported(prefix)
    transport = OneProviderPostTransport(target=base_url.rstrip("/") + "/" + path, body=body, key=key,
        policy=policy, streaming=streaming, organization=organization, fence=fence, expires_at=expires_at,
        prefix=prefix, continuous_fence=continuous_fence, entry_expires_at=entry_expires_at)
    try:
        async with httpx2.AsyncClient(transport=transport, trust_env=False, follow_redirects=False) as http:
            async with openai.AsyncOpenAI(api_key=key.get_secret_value(), admin_api_key="", organization=organization or "",
                project="", webhook_secret="", base_url=base_url.rstrip("/") + "/", max_retries=0,
                http_client=http, timeout=max(0.001, (expires_at - datetime.now(UTC)).total_seconds()),
                default_headers={"Authorization": "Bearer " + key.get_secret_value()}) as client:
                await client.post(path, cast_to=httpx2.Response, content=body, options={"headers": {"Content-Type": "application/json"}})
    except asyncio.CancelledError:
        if transport.raw_body is None:
            raise
    except Exception:
        pass  # 只返回有界真实观察，不传播SDK原异常/头/正文或重试。
    finally:
        observation = ProviderSDKObservation(transport.raw_body, transport.started_at, transport.finished_at, transport.io_stop_confirmed)
        if observe is not None:
            observe(observation)
    return observation
