"""HTTP MCP授权编排；仅供可信执行器集成，默认无broker，不执行OAuth发现或联网。"""

import asyncio
import base64
import hashlib
import hmac
import json
import math
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Protocol, TypeVar, get_args
from uuid import UUID

from pydantic import SecretStr

from supportops_core.mcp_http_authorization_contracts import (
    HttpMcpAuthorizationAudit,
    HttpMcpAuthorizationCode,
    HttpMcpAuditEvent,
    HttpMcpCredentialRequest,
    HttpMcpTokenState,
)

T = TypeVar("T")
_BEARER = re.compile(r"[A-Za-z0-9._~+/-]+=*\Z")


class HttpMcpAuthorizationError(Exception):
    """固定失败域；call_started仅表示已进入派发边界，不能证明远端是否执行。"""

    def __init__(self, code: HttpMcpAuthorizationCode, *, call_started: bool = False) -> None:
        if code not in get_args(HttpMcpAuthorizationCode):
            code = "MCP_HTTP_AUTHORIZATION_UNCONFIRMED"
        super().__init__(code)
        self.code = code
        self.call_started = call_started


@dataclass(frozen=True, slots=True)
class HttpMcpTokenGrant:
    """令牌仅在可信执行内存存在，不支持JSON序列化或前端投影。"""

    state: HttpMcpTokenState = field(repr=False)
    access_token: SecretStr = field(repr=False)


class HttpMcpCredentialBroker(Protocol):
    """未来获准提供者须经认证信道实现；调用者输入或UUID摘要不是授权证明。"""

    provider_id: str

    async def assert_current_permit(self, request: HttpMcpCredentialRequest) -> None:
        """独立查询当前主体/资源/调用/attempt围栏/审批和固定来源，不信任请求自报许可。"""
        ...

    async def claim(self, request: HttpMcpCredentialRequest) -> HttpMcpTokenGrant:
        """原子消费持久化dispatch_id，验证签名/内省及受众/scope后签发一次短期lease。"""
        ...

    async def current(self, request: HttpMcpCredentialRequest, lease_id: UUID) -> HttpMcpTokenState:
        """独立核对凭据revision/lease当前撤销状态；不返回明文或缓存旧active作为事实。"""
        ...

    async def revoke(self, request: HttpMcpCredentialRequest, lease_id: UUID) -> None:
        """以当前受控撤销权限撤销指定lease，不凭原执行许可提权，不替换或刷新revision。"""
        ...


class HttpMcpAuditSink(Protocol):
    async def record(self, event: HttpMcpAuthorizationAudit) -> None:
        """独立持久化最小审计，提交不明须报错，不能仅写内存日志后宣称成功。"""
        ...


async def _bounded(operation: Awaitable[T], seconds: float = 2) -> T:
    async with asyncio.timeout(seconds):
        return await operation


def _scope_matches(request: HttpMcpCredentialRequest, state: HttpMcpTokenState) -> None:
    policy, binding = request.policy, request.binding
    if (state.request_digest != request.request_digest or state.provider_id != policy.provider_id
        or state.credential_id != policy.credential_id or state.credential_revision != policy.credential_revision
        or state.tenant_id != binding.tenant_id or state.actor_user_id != binding.actor_user_id
        or state.issuer != policy.issuer or state.audiences != (policy.resource,)
        or state.scopes != policy.scopes
        or state.expires_at > min(policy.valid_until, binding.expires_at)
        or state.expires_at-state.issued_at > timedelta(seconds=policy.max_token_lifetime_seconds)):
        raise HttpMcpAuthorizationError("MCP_HTTP_CREDENTIAL_SCOPE_REJECTED")


def _active(request: HttpMcpCredentialRequest, state: HttpMcpTokenState | None = None) -> None:
    now = datetime.now(UTC)
    if min(request.policy.valid_until, request.binding.expires_at) <= now:
        raise HttpMcpAuthorizationError("MCP_HTTP_AUTHORIZATION_EXPIRED")
    if state is not None:
        _scope_matches(request, state)
        if state.revoked_at is not None:
            raise HttpMcpAuthorizationError("MCP_HTTP_CREDENTIAL_REVOKED")
        if state.issued_at > now or state.expires_at <= now:
            raise HttpMcpAuthorizationError("MCP_HTTP_CREDENTIAL_EXPIRED")


def _reject_platform_jwt(token: str, *, platform_issuer: str, platform_audience: str) -> None:
    """只作额外拒绝，不把未验签claims当可信身份；签名/内省必须由broker负责。"""
    if token.count(".") != 2:
        return
    try:
        encoded = token.split(".")[1]
        raw = base64.b64decode(encoded + "=" * (-len(encoded) % 4), altchars=b"-_", validate=True)
        def pairs(items: list[tuple[str, object]]) -> dict[str, object]:
            result: dict[str, object] = {}
            for key, value in items:
                if key in result:
                    raise ValueError()
                result[key] = value
            return result
        def invalid(_value: str) -> object:
            raise ValueError()
        def finite(value: str) -> float:
            parsed = float(value)
            if not math.isfinite(parsed):
                raise ValueError()
            return parsed
        claims = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs, parse_constant=invalid, parse_float=finite)
        if not isinstance(claims, dict):
            raise ValueError()
        audience = claims.get("aud")
        if claims.get("iss") == platform_issuer or audience == platform_audience or (
            isinstance(audience, list) and platform_audience in audience
        ):
            raise ValueError()
    except Exception:
        raise HttpMcpAuthorizationError("MCP_HTTP_PLATFORM_TOKEN_REJECTED") from None


def _token(request: HttpMcpCredentialRequest, grant: HttpMcpTokenGrant, *, platform_issuer: str, platform_audience: str) -> None:
    if not isinstance(grant, HttpMcpTokenGrant) or not isinstance(grant.state, HttpMcpTokenState) or not isinstance(grant.access_token, SecretStr):
        raise HttpMcpAuthorizationError("MCP_HTTP_CREDENTIAL_REJECTED")
    _active(request, grant.state)
    raw = grant.access_token.get_secret_value()
    if not 1 <= len(raw) <= 8192 or _BEARER.fullmatch(raw) is None:
        raise HttpMcpAuthorizationError("MCP_HTTP_CREDENTIAL_REJECTED")
    digest = hashlib.sha256(raw.encode("ascii")).hexdigest()
    if not hmac.compare_digest(digest, grant.state.token_digest):
        raise HttpMcpAuthorizationError("MCP_HTTP_CREDENTIAL_REJECTED")
    _reject_platform_jwt(raw, platform_issuer=platform_issuer, platform_audience=platform_audience)


async def _current(broker: HttpMcpCredentialBroker, request: HttpMcpCredentialRequest, state: HttpMcpTokenState) -> None:
    await _bounded(broker.assert_current_permit(request))
    current = await _bounded(broker.current(request, state.lease_id))
    if not isinstance(current, HttpMcpTokenState):
        raise HttpMcpAuthorizationError("MCP_HTTP_AUTHORIZATION_CHANGED")
    _active(request, current)
    if current != state:
        raise HttpMcpAuthorizationError("MCP_HTTP_AUTHORIZATION_CHANGED")


async def _audit(sink: HttpMcpAuditSink, request: HttpMcpCredentialRequest, *,
    event: HttpMcpAuditEvent, code: HttpMcpAuthorizationCode, lease_id: UUID | None, call_started: bool,
) -> None:
    try:
        await _bounded(sink.record(HttpMcpAuthorizationAudit(request_digest=request.request_digest,
            dispatch_id=request.binding.dispatch_id, lease_id=lease_id, event=event, code=code, observed_at=datetime.now(UTC))))
    except Exception:
        raise HttpMcpAuthorizationError("MCP_HTTP_AUDIT_UNAVAILABLE", call_started=call_started) from None


async def run_authorized_http_mcp_call(
    request: HttpMcpCredentialRequest, *, operation: Callable[[SecretStr, Callable[[], Awaitable[None]]], Awaitable[T]],
    broker: HttpMcpCredentialBroker | None = None, audit_sink: HttpMcpAuditSink | None = None,
    platform_issuer: str, platform_audience: str,
) -> T:
    """一次取密/一次可信适配器调用，无自动刷新、重试、scope升级或401发现。

    只供未来独立身份执行器使用；operation接收SecretStr和仅本次回调有效的复检函数。
    固定适配器须在每次HTTP交换前后调用复检，并复用出站策略及输出Schema。
    本函数不实现认证IPC、数据库许可、OS隔离或HTTP MCP会话，不挂入现有Agent链。
    """
    if broker is None or audit_sink is None:
        raise HttpMcpAuthorizationError("MCP_HTTP_PROVIDER_NOT_CONFIGURED")
    started, lease_id = False, None
    try:
        async with asyncio.timeout(15):
            if not platform_issuer or not platform_audience or broker.provider_id != request.policy.provider_id:
                raise HttpMcpAuthorizationError("MCP_HTTP_PROVIDER_REJECTED")
            if request.policy.issuer == platform_issuer or request.policy.resource == platform_audience:
                raise HttpMcpAuthorizationError("MCP_HTTP_PLATFORM_TOKEN_REJECTED")
            _active(request)
            await _bounded(broker.assert_current_permit(request))
            # 持久化broker先提交领取再取密/绑定；超过总预算保留已领取，不重试。
            grant = await _bounded(broker.claim(request), 6)
            _token(request, grant, platform_issuer=platform_issuer, platform_audience=platform_audience)
            lease_id = grant.state.lease_id
            if grant.state.expires_at <= datetime.now(UTC)+timedelta(seconds=5):
                raise HttpMcpAuthorizationError("MCP_HTTP_CREDENTIAL_EXPIRED")
            await _current(broker, request, grant.state)
            await _audit(audit_sink, request, event="credential_acquired", code="MCP_HTTP_CREDENTIAL_ACQUIRED",
                lease_id=lease_id, call_started=False)
            # 审计等待不延长许可；只把broker签发的SecretStr交给固定可信适配器。
            await _current(broker, request, grant.state)
            remaining = min(10.0, (grant.state.expires_at-datetime.now(UTC)).total_seconds())
            if remaining <= 0:
                raise HttpMcpAuthorizationError("MCP_HTTP_CREDENTIAL_EXPIRED")
            started = True
            callback_active = True
            async def recheck() -> None:
                if not callback_active:
                    raise HttpMcpAuthorizationError("MCP_HTTP_AUTHORIZATION_EXPIRED")
                await _current(broker, request, grant.state)
                if not callback_active:
                    raise HttpMcpAuthorizationError("MCP_HTTP_AUTHORIZATION_EXPIRED")
            try:
                result = await _bounded(operation(grant.access_token, recheck), remaining)
            finally:
                callback_active = False
            await _current(broker, request, grant.state)
            await _audit(audit_sink, request, event="call_confirmed", code="MCP_HTTP_CALL_CONFIRMED",
                lease_id=lease_id, call_started=True)
            await _current(broker, request, grant.state)
            return result
    except Exception as exc:
        code: HttpMcpAuthorizationCode = exc.code if isinstance(exc, HttpMcpAuthorizationError) else "MCP_HTTP_AUTHORIZATION_UNCONFIRMED"
        if code == "MCP_HTTP_AUDIT_UNAVAILABLE":
            raise HttpMcpAuthorizationError(code, call_started=started) from None
        await _audit(audit_sink, request, event="call_unconfirmed" if started else "authorization_rejected",
            code=code, lease_id=lease_id, call_started=started)
        raise HttpMcpAuthorizationError(code, call_started=started) from None


async def revoke_http_mcp_lease(request: HttpMcpCredentialRequest, *, lease_id: UUID,
    broker: HttpMcpCredentialBroker | None = None, audit_sink: HttpMcpAuditSink | None = None,
) -> HttpMcpTokenState:
    """只撤销原lease并重新查询确认；过期租约仍可撤销，未知不伪造revoked事实。"""
    if broker is None or audit_sink is None:
        raise HttpMcpAuthorizationError("MCP_HTTP_PROVIDER_NOT_CONFIGURED")
    try:
        async with asyncio.timeout(10):
            if broker.provider_id != request.policy.provider_id:
                raise ValueError()
            previous = await _bounded(broker.current(request, lease_id))
            if not isinstance(previous, HttpMcpTokenState) or previous.lease_id != lease_id:
                raise ValueError()
            _scope_matches(request, previous)
            # broker须独立复检当前撤销权限；原调用期限不授予/否定新的撤销权限。
            await _bounded(broker.revoke(request, lease_id), 3)
            current = await _bounded(broker.current(request, lease_id))
            if not isinstance(current, HttpMcpTokenState) or current.lease_id != lease_id or current.revoked_at is None:
                raise ValueError()
            _scope_matches(request, current)
            if current.model_copy(update={"revoked_at": previous.revoked_at}) != previous:
                raise ValueError()
            if previous.revoked_at is not None and current.revoked_at != previous.revoked_at:
                raise ValueError()
            if current.revoked_at > datetime.now(UTC):
                raise ValueError()
            await _audit(audit_sink, request, event="revocation_confirmed", code="MCP_HTTP_REVOCATION_CONFIRMED",
                lease_id=lease_id, call_started=False)
            return current
    except Exception as exc:
        if isinstance(exc, HttpMcpAuthorizationError) and exc.code == "MCP_HTTP_AUDIT_UNAVAILABLE":
            raise HttpMcpAuthorizationError("MCP_HTTP_AUDIT_UNAVAILABLE") from None
        await _audit(audit_sink, request, event="revocation_unconfirmed", code="MCP_HTTP_REVOCATION_UNCONFIRMED",
            lease_id=lease_id, call_started=False)
        raise HttpMcpAuthorizationError("MCP_HTTP_REVOCATION_UNCONFIRMED") from None
