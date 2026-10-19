"""HTTP MCP认证本机broker通道；客户端不导入数据库，令牌仅在认证socket及内存传递。"""

import asyncio
import os
import socket
import stat
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal
from uuid import UUID, uuid4

from pydantic import Field, SecretStr, model_validator

from supportops_core.mcp_http_authorization import (
    HttpMcpAuthorizationError, HttpMcpTokenGrant, _scope_matches, _token,
)
from supportops_core.mcp_http_authorization_contracts import (
    HttpMcpAuthorizationAudit, HttpMcpAuthorizationCode, HttpMcpCredentialRequest, HttpMcpTokenState,
)
from supportops_core.mcp_http_client import HttpMcpToolObservation, _object, call_http_mcp_tool
from supportops_core.outbound_http import OutboundHttpPolicy
from supportops_core.tool_discovery_contracts import Digest, DiscoveredTool, DiscoveryContract, SourceKey, canonical_json

if TYPE_CHECKING:
    from supportops_core.mcp_http_broker import DurableHttpMcpCredentialBroker

BROKER_UID = 10003
EXECUTOR_UID = 10004
SOCKET_DIR = Path("/run/supportops-mcp-broker")
SOCKET_PATH = SOCKET_DIR / "control.sock"
MAX_FRAME_BYTES = 32_768
Operation = Literal["assert", "claim", "current", "revoke", "audit"]


class _Command(DiscoveryContract):
    schema_version: Literal["http_mcp_broker_command_v1"] = "http_mcp_broker_command_v1"
    nonce: UUID
    operation: Operation
    dispatch_id: UUID
    request_digest: Digest
    lease_id: UUID | None = None
    audit: HttpMcpAuthorizationAudit | None = None

    @model_validator(mode="after")
    def grouped_fields(self) -> "_Command":
        if (self.operation in {"current", "revoke"}) != (self.lease_id is not None):
            raise ValueError()
        if (self.operation == "audit") != (self.audit is not None):
            raise ValueError()
        if self.audit is not None and (self.audit.dispatch_id != self.dispatch_id or self.audit.request_digest != self.request_digest):
            raise ValueError()
        return self


class _Reply(DiscoveryContract):
    schema_version: Literal["http_mcp_broker_reply_v1"] = "http_mcp_broker_reply_v1"
    nonce: UUID
    operation: Operation
    dispatch_id: UUID
    request_digest: Digest
    provider_id: SourceKey
    status: Literal["ok", "rejected"]
    code: HttpMcpAuthorizationCode | None = None
    state: HttpMcpTokenState | None = Field(default=None, repr=False)
    access_token: SecretStr | None = Field(default=None, repr=False)

    @model_validator(mode="after")
    def grouped_fields(self) -> "_Reply":
        if self.status == "rejected":
            if (self.code is None or self.state is not None or self.access_token is not None
                or self.code in {"MCP_HTTP_CREDENTIAL_ACQUIRED", "MCP_HTTP_CALL_CONFIRMED", "MCP_HTTP_REVOCATION_CONFIRMED"}):
                raise ValueError()
        elif (self.code is not None or (self.operation in {"claim", "current"}) != (self.state is not None)
            or (self.operation == "claim") != (self.access_token is not None)):
            raise ValueError()
        if self.access_token is not None and not 1 <= len(self.access_token.get_secret_value()) <= 8192:
            raise ValueError()
        return self


def _runtime(uid: int) -> None:
    if not hasattr(socket, "SO_PEERCRED") or os.geteuid() != uid:
        raise HttpMcpAuthorizationError("MCP_HTTP_IPC_PEER_REJECTED")


def _path(*, endpoint: bool) -> None:
    directory = SOCKET_DIR.lstat()
    if (not stat.S_ISDIR(directory.st_mode) or directory.st_uid != BROKER_UID or directory.st_gid != EXECUTOR_UID
        or stat.S_IMODE(directory.st_mode) != 0o770):
        raise HttpMcpAuthorizationError("MCP_HTTP_IPC_PEER_REJECTED")
    if endpoint:
        channel = SOCKET_PATH.lstat()
        if (not stat.S_ISSOCK(channel.st_mode) or channel.st_uid != BROKER_UID or channel.st_gid != EXECUTOR_UID
            or stat.S_IMODE(channel.st_mode) != 0o660):
            raise HttpMcpAuthorizationError("MCP_HTTP_IPC_PEER_REJECTED")


def _peer(writer: asyncio.StreamWriter, uid: int) -> None:
    channel = writer.get_extra_info("socket")
    if channel is None or not hasattr(socket, "SO_PEERCRED"):
        raise HttpMcpAuthorizationError("MCP_HTTP_IPC_PEER_REJECTED")
    _pid, actual_uid, _gid = struct.unpack("3i", channel.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
    if actual_uid != uid:
        raise HttpMcpAuthorizationError("MCP_HTTP_IPC_PEER_REJECTED")


def _frame(payload: dict[str, object]) -> bytes:
    raw = canonical_json(payload).encode("utf-8") + b"\n"
    _object(raw, max_bytes=MAX_FRAME_BYTES)
    return raw


async def _close(writer: asyncio.StreamWriter) -> None:
    writer.close()
    try:
        async with asyncio.timeout(1):
            await writer.wait_closed()
    except BaseException:
        writer.transport.abort()
        raise


@dataclass(frozen=True, slots=True)
class IpcHttpMcpCredentialBroker:
    """固定10004执行器使用；不允许自选socket、目标URL、完整policy或凭据路径。"""

    provider_id: str
    platform_issuer: str = field(repr=False)
    platform_audience: str = field(repr=False)

    async def _exchange(self, request: HttpMcpCredentialRequest, operation: Operation, *,
        lease_id: UUID | None = None, audit: HttpMcpAuthorizationAudit | None = None,
    ) -> _Reply:
        writer = None
        try:
            _runtime(EXECUTOR_UID)
            _path(endpoint=True)
            if not self.platform_issuer or not self.platform_audience or self.provider_id != request.policy.provider_id:
                raise HttpMcpAuthorizationError("MCP_HTTP_PROVIDER_REJECTED")
            command = _Command(nonce=uuid4(), operation=operation, dispatch_id=request.binding.dispatch_id,
                request_digest=request.request_digest, lease_id=lease_id, audit=audit)
            async with asyncio.timeout(6 if operation == "claim" else 3):
                reader, writer = await asyncio.open_unix_connection(str(SOCKET_PATH), limit=MAX_FRAME_BYTES)
                _peer(writer, BROKER_UID)
                _path(endpoint=True)
                writer.write(_frame(command.model_dump(mode="json")))
                await writer.drain()
                reply = _Reply.model_validate(_object(await reader.readuntil(b"\n"), max_bytes=MAX_FRAME_BYTES))
                if (reply.nonce != command.nonce or reply.operation != operation or reply.dispatch_id != command.dispatch_id
                    or reply.request_digest != command.request_digest or reply.provider_id != self.provider_id):
                    raise ValueError()
                if reply.status == "rejected":
                    raise HttpMcpAuthorizationError(reply.code or "MCP_HTTP_IPC_UNCONFIRMED")
                if reply.state is not None:
                    _scope_matches(request, reply.state)
                    if lease_id is not None and reply.state.lease_id != lease_id:
                        raise ValueError()
                return reply
        except HttpMcpAuthorizationError:
            raise
        except Exception:
            # 不保留验证异常链，其中可能包含令牌；无重连/重发。
            raise HttpMcpAuthorizationError("MCP_HTTP_IPC_UNCONFIRMED") from None
        finally:
            if writer is not None:
                try:
                    await _close(writer)
                except Exception:
                    pass  # 已中止transport，不用清理结果覆盖已读取的响应。

    async def assert_current_permit(self, request: HttpMcpCredentialRequest) -> None:
        await self._exchange(request, "assert")

    async def claim(self, request: HttpMcpCredentialRequest) -> HttpMcpTokenGrant:
        reply = await self._exchange(request, "claim")
        if reply.state is None or reply.access_token is None:
            raise HttpMcpAuthorizationError("MCP_HTTP_IPC_UNCONFIRMED")
        grant = HttpMcpTokenGrant(state=reply.state, access_token=reply.access_token)
        _token(request, grant, platform_issuer=self.platform_issuer, platform_audience=self.platform_audience)
        return grant

    async def current(self, request: HttpMcpCredentialRequest, lease_id: UUID) -> HttpMcpTokenState:
        reply = await self._exchange(request, "current", lease_id=lease_id)
        if reply.state is None:
            raise HttpMcpAuthorizationError("MCP_HTTP_IPC_UNCONFIRMED")
        return reply.state

    async def revoke(self, request: HttpMcpCredentialRequest, lease_id: UUID) -> None:
        await self._exchange(request, "revoke", lease_id=lease_id)


@dataclass(frozen=True, slots=True)
class IpcHttpMcpAuditSink:
    broker: IpcHttpMcpCredentialBroker = field(repr=False)
    request: HttpMcpCredentialRequest = field(repr=False)

    async def record(self, event: HttpMcpAuthorizationAudit) -> None:
        try:
            await self.broker._exchange(self.request, "audit", audit=event)
        except Exception:
            raise HttpMcpAuthorizationError("MCP_HTTP_AUDIT_UNAVAILABLE") from None


async def call_http_mcp_tool_via_ipc(request: HttpMcpCredentialRequest, *, descriptor: DiscoveredTool,
    arguments: dict[str, object], outbound_policy: OutboundHttpPolicy, platform_issuer: str, platform_audience: str,
) -> HttpMcpToolObservation:
    """固定执行身份使用的完整调用适配；不接受数据库、内联令牌或替代broker。"""
    _runtime(EXECUTOR_UID)
    broker = IpcHttpMcpCredentialBroker(request.policy.provider_id, platform_issuer, platform_audience)
    return await call_http_mcp_tool(request, descriptor=descriptor, arguments=arguments, outbound_policy=outbound_policy,
        broker=broker, audit_sink=IpcHttpMcpAuditSink(broker, request), platform_issuer=platform_issuer,
        platform_audience=platform_audience)


async def serve_http_mcp_broker(broker: "DurableHttpMcpCredentialBroker") -> None:
    """仅库入口；维护者须提供真实上游及独立broker运行身份，不隐式启动/创建目录。"""
    from supportops_core.mcp_http_audit import DatabaseHttpMcpAuditSink
    from supportops_core.mcp_http_broker import DurableHttpMcpCredentialBroker

    _runtime(BROKER_UID)
    _path(endpoint=False)
    if (not isinstance(broker, DurableHttpMcpCredentialBroker) or not broker.provider_id
        or not broker.platform_issuer or not broker.platform_audience):
        raise HttpMcpAuthorizationError("MCP_HTTP_PROVIDER_NOT_CONFIGURED")
    # 不删除旧socket；包括断开的符号链接和已有活动服务都拒绝覆盖。
    try:
        SOCKET_PATH.lstat()
    except FileNotFoundError:
        pass
    else:
        raise HttpMcpAuthorizationError("MCP_HTTP_IPC_PEER_REJECTED")
    active = 0

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        nonlocal active
        acquired = False
        try:
            _peer(writer, EXECUTOR_UID)
            if active >= 4:
                return
            active += 1
            acquired = True
            async with asyncio.timeout(7):
                command = _Command.model_validate(_object(await reader.readuntil(b"\n"), max_bytes=MAX_FRAME_BYTES))
                request = await broker.resolve_dispatch(command.dispatch_id, command.request_digest, peer_uid=EXECUTOR_UID)
                state, secret, code = None, None, None
                try:
                    if command.operation == "assert":
                        await broker.assert_current_permit(request)
                    elif command.operation == "claim":
                        grant = await broker.claim(request)
                        # 回传前再查当前授权与原lease；失败留下bound而非重新签发。
                        await broker.assert_current_permit(request)
                        if await broker.current(request, grant.state.lease_id) != grant.state:
                            raise HttpMcpAuthorizationError("MCP_HTTP_AUTHORIZATION_CHANGED")
                        state, secret = grant.state, grant.access_token
                    elif command.operation == "current" and command.lease_id is not None:
                        state = await broker.current(request, command.lease_id)
                    elif command.operation == "revoke" and command.lease_id is not None:
                        await broker.revoke(request, command.lease_id)
                    elif command.operation == "audit" and command.audit is not None:
                        await broker.validate_audit_binding(request, command.audit)
                        await DatabaseHttpMcpAuditSink(broker.session_factory, request).record(command.audit)
                    else:
                        raise ValueError()
                except HttpMcpAuthorizationError as exc:
                    state, secret, code = None, None, exc.code
                reply = _Reply(nonce=command.nonce, operation=command.operation, dispatch_id=command.dispatch_id,
                    request_digest=command.request_digest, provider_id=broker.provider_id, status="rejected" if code else "ok",
                    code=code, state=state, access_token=secret)
                # SecretStr默认序列化仍脱敏；仅认证socket的这一处显式展开，绝不进入日志/文件。
                payload = reply.model_dump(mode="json", exclude={"access_token"})
                payload["access_token"] = secret.get_secret_value() if secret is not None else None
                _peer(writer, EXECUTOR_UID)
                writer.write(_frame(payload))
                await writer.drain()
        except Exception:
            writer.transport.abort()  # 固定未确认；不把DB/上游/JSON异常回传或打印。
        finally:
            if acquired:
                active -= 1
            try:
                await _close(writer)
            except Exception:
                pass

    server = await asyncio.start_unix_server(handle, path=str(SOCKET_PATH), limit=MAX_FRAME_BYTES, start_serving=False)
    try:
        os.chown(SOCKET_PATH, BROKER_UID, EXECUTOR_UID)
        os.chmod(SOCKET_PATH, 0o660)
        _path(endpoint=True)
        async with server:
            await server.serve_forever()
    finally:
        server.close()
        await server.wait_closed()
