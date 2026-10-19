"""固定公钥的受限JWT访问令牌验签；不发现密钥、不签发凭据、不自行映射外部主体。"""

import base64
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID

import jwt
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicNumbers
from pydantic import AwareDatetime, Field, StrictInt, model_validator

from supportops_core.mcp_http_authorization import (
    HttpMcpAuthorizationError, HttpMcpCredentialBroker, HttpMcpTokenGrant, _bounded, _scope_matches, _token,
)
from supportops_core.mcp_http_authorization_contracts import HttpsIdentifier, HttpMcpCredentialRequest, HttpMcpTokenState
from supportops_core.mcp_http_client import _object
from supportops_core.tool_discovery_contracts import Digest, DiscoveryContract, discovery_digest

Identifier = Annotated[str, Field(strict=True, min_length=1, max_length=256, pattern=r"^[\x21-\x7e]+$")]
Base64Url = Annotated[str, Field(strict=True, min_length=1, max_length=684, pattern=r"^[A-Za-z0-9_-]+$")]


def _decode(value: str, maximum: int) -> bytes:
    if not value or len(value) > maximum * 2 or re.fullmatch(r"[A-Za-z0-9_-]+", value) is None:
        raise ValueError()
    raw = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
    if len(raw) > maximum or base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii") != value:
        raise ValueError()
    return raw


class HttpMcpRsaVerificationKey(DiscoveryContract):
    """只能由可信部署批准的公开JWK；不接受私钥、x5u/jku或客户端上传密钥。"""

    kty: Literal["RSA"] = "RSA"
    alg: Literal["RS256"] = "RS256"
    use: Literal["sig"] = "sig"
    key_ops: tuple[Literal["verify"], ...] = ("verify",)
    kid: Identifier
    n: Base64Url = Field(repr=False)
    e: Literal["AQAB"] = "AQAB"

    @model_validator(mode="after")
    def public_key(self) -> "HttpMcpRsaVerificationKey":
        modulus = _decode(self.n, 512)
        if (self.key_ops != ("verify",) or not modulus or modulus[0] == 0
            or not 2048 <= int.from_bytes(modulus, "big").bit_length() <= 4096):
            raise ValueError()
        RSAPublicNumbers(65537, int.from_bytes(modulus, "big")).public_key()
        return self


class HttpMcpJwtVerificationPolicy(DiscoveryContract):
    """可信主体映射者给出的单次固定验证范围；摘要不产生批准或取密许可。"""

    schema_version: Literal["http_mcp_jwt_verification_policy_v1"] = "http_mcp_jwt_verification_policy_v1"
    request_digest: Digest
    external_subject: Identifier = Field(repr=False)
    client_id: Identifier = Field(repr=False)
    keys: Annotated[tuple[HttpMcpRsaVerificationKey, ...], Field(min_length=1, max_length=8)] = Field(repr=False)
    valid_until: AwareDatetime

    @model_validator(mode="after")
    def unique_keys(self) -> "HttpMcpJwtVerificationPolicy":
        if len({key.kid for key in self.keys}) != len(self.keys) or len({key.n for key in self.keys}) != len(self.keys):
            raise ValueError()
        return self

    @property
    def policy_digest(self) -> str:
        return discovery_digest(self.model_dump(mode="json"))


class _AccessClaims(DiscoveryContract):
    iss: HttpsIdentifier
    sub: Identifier = Field(repr=False)
    aud: HttpsIdentifier
    client_id: Identifier = Field(repr=False)
    jti: Identifier = Field(repr=False)
    scope: Annotated[str, Field(strict=True, min_length=1, max_length=2063)] = Field(repr=False)
    iat: Annotated[StrictInt, Field(ge=0, le=253402300799)]
    exp: Annotated[StrictInt, Field(ge=0, le=253402300799)]
    nbf: Annotated[StrictInt, Field(ge=0, le=253402300799)] | None = None


def verify_http_mcp_jwt(request: HttpMcpCredentialRequest, grant: HttpMcpTokenGrant, *,
    policy: HttpMcpJwtVerificationPolicy, platform_issuer: str, platform_audience: str,
) -> None:
    """仅RS256/at+jwt严格profile；验签成功也不证明当前许可或撤销状态。"""
    try:
        if (not platform_issuer or not platform_audience or policy.request_digest != request.request_digest
            or policy.valid_until <= datetime.now(UTC) or policy.valid_until > request.binding.expires_at):
            raise ValueError()
        _token(request, grant, platform_issuer=platform_issuer, platform_audience=platform_audience)
        raw = grant.access_token.get_secret_value()
        parts = raw.split(".")
        if len(parts) != 3:
            raise ValueError()
        header = _object(_decode(parts[0], 1024), max_bytes=1024)
        if set(header) != {"alg", "typ", "kid"} or header["alg"] != "RS256" or header["typ"] != "at+jwt":
            raise ValueError()
        selected = [key for key in policy.keys if key.kid == header["kid"]]
        if len(selected) != 1:
            raise ValueError()
        key = selected[0]
        modulus = _decode(key.n, 512)
        if len(_decode(parts[2], 512)) != len(modulus):
            raise ValueError()
        # 预解析仅拒绝重复/未知字段及宽松时间类型，不使用任何claims作为许可。
        claims = _AccessClaims.model_validate(_object(_decode(parts[1], 6144), max_bytes=6144))
        public_key = RSAPublicNumbers(65537, int.from_bytes(modulus, "big")).public_key()
        jwt.decode(raw, public_key, algorithms=["RS256"], issuer=request.policy.issuer,
            audience=request.policy.resource, subject=policy.external_subject, leeway=0,
            options={"require": ["iss", "sub", "aud", "exp", "iat", "jti", "client_id", "scope"],
                "verify_signature": True, "verify_exp": True, "verify_iat": True, "verify_nbf": True,
                "verify_iss": True, "verify_aud": True, "verify_sub": True, "verify_jti": True, "strict_aud": True})
        scopes = claims.scope.split(" ")
        if (claims.client_id != policy.client_id or claims.sub != policy.external_subject
            or claims.iss != request.policy.issuer or claims.aud != request.policy.resource
            or not all(scopes) or len(set(scopes)) != len(scopes) or tuple(sorted(scopes)) != request.policy.scopes
            or claims.exp <= claims.iat or claims.exp > policy.valid_until.timestamp()
            or claims.nbf is not None and not claims.iat <= claims.nbf < claims.exp
            or datetime.fromtimestamp(claims.iat, UTC) != grant.state.issued_at
            or datetime.fromtimestamp(claims.exp, UTC) != grant.state.expires_at):
            raise ValueError()
    except Exception:
        # 隐藏PyJWT/Pydantic原异常及令牌/claims；取消不在同步验签入口消化。
        raise HttpMcpAuthorizationError("MCP_HTTP_CREDENTIAL_REJECTED") from None


@dataclass(frozen=True, slots=True)
class VerifiedJwtHttpMcpCredentialBroker:
    """接在持久化broker与真实上游之间；不缓存令牌，策略变化关闭原许可。"""

    upstream: HttpMcpCredentialBroker = field(repr=False)
    policy: HttpMcpJwtVerificationPolicy = field(repr=False)
    current_policy: Callable[[HttpMcpCredentialRequest], Awaitable[HttpMcpJwtVerificationPolicy]] = field(repr=False)
    platform_issuer: str = field(repr=False)
    platform_audience: str = field(repr=False)

    @property
    def provider_id(self) -> str:
        return self.upstream.provider_id

    async def _policy(self, request: HttpMcpCredentialRequest) -> None:
        try:
            if (self.provider_id != request.policy.provider_id or not self.platform_issuer or not self.platform_audience
                or request.policy.issuer == self.platform_issuer or request.policy.resource == self.platform_audience
                or self.policy.request_digest != request.request_digest or self.policy.valid_until > request.binding.expires_at):
                raise ValueError()
            current = await _bounded(self.current_policy(request))
            if not isinstance(current, HttpMcpJwtVerificationPolicy) or current != self.policy:
                raise ValueError()
            if self.policy.valid_until <= datetime.now(UTC):
                raise ValueError()
        except Exception:
            raise HttpMcpAuthorizationError("MCP_HTTP_AUTHORIZATION_CHANGED") from None

    async def assert_current_permit(self, request: HttpMcpCredentialRequest) -> None:
        try:
            await self._policy(request)
            await _bounded(self.upstream.assert_current_permit(request))
            await self._policy(request)
        except Exception:
            raise HttpMcpAuthorizationError("MCP_HTTP_AUTHORIZATION_CHANGED") from None

    async def claim(self, request: HttpMcpCredentialRequest) -> HttpMcpTokenGrant:
        try:
            await self.assert_current_permit(request)
            grant = await _bounded(self.upstream.claim(request), 3)
            verify_http_mcp_jwt(request, grant, policy=self.policy, platform_issuer=self.platform_issuer,
                platform_audience=self.platform_audience)
            await self.assert_current_permit(request)
            return grant
        except Exception:
            raise HttpMcpAuthorizationError("MCP_HTTP_CREDENTIAL_REJECTED") from None

    async def current(self, request: HttpMcpCredentialRequest, lease_id: UUID) -> HttpMcpTokenState:
        # 不依赖旧公钥/映射仍有效；正常调用路径先assert，原lease撤销可在轮换/过期后读取。
        try:
            if self.provider_id != request.policy.provider_id or self.policy.request_digest != request.request_digest:
                raise ValueError()
            state = await _bounded(self.upstream.current(request, lease_id))
            if not isinstance(state, HttpMcpTokenState) or state.lease_id != lease_id:
                raise ValueError()
            _scope_matches(request, state)
            return state
        except Exception:
            raise HttpMcpAuthorizationError("MCP_HTTP_CREDENTIAL_REJECTED") from None

    async def revoke(self, request: HttpMcpCredentialRequest, lease_id: UUID) -> None:
        # 上游独立鉴定当前撤销权；公钥轮换或映射失效不能阻止安全撤销原lease。
        try:
            state = await self.current(request, lease_id)
            if state.revoked_at is None:
                await _bounded(self.upstream.revoke(request, lease_id), 3)
            current = await self.current(request, lease_id)
            if (current.revoked_at is None or current.revoked_at > datetime.now(UTC)
                or current.model_copy(update={"revoked_at": state.revoked_at}) != state
                or state.revoked_at is not None and current.revoked_at != state.revoked_at):
                raise ValueError()
        except Exception:
            raise HttpMcpAuthorizationError("MCP_HTTP_REVOCATION_UNCONFIRMED") from None
