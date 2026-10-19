"""HTTP MCP凭据broker的封闭契约；摘要和类型不构成调用许可。"""

from typing import Annotated, Literal
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import AwareDatetime, Field, StrictInt, field_validator, model_validator

from supportops_core.tool_discovery_contracts import Digest, DiscoveryContract, SourceKey, discovery_digest

Scope = Annotated[str, Field(strict=True, min_length=1, max_length=128, pattern=r'^[\x21\x23-\x5b\x5d-\x7e]+$')]
Scopes = Annotated[tuple[Scope, ...], Field(min_length=1, max_length=16)]
HttpsIdentifier = Annotated[str, Field(strict=True, min_length=9, max_length=2048)]
HttpMcpAuditEvent = Literal["credential_acquired", "call_confirmed", "call_unconfirmed", "authorization_rejected", "revocation_confirmed", "revocation_unconfirmed"]
HttpMcpAuthorizationCode = Literal[
    "MCP_HTTP_CREDENTIAL_SCOPE_REJECTED", "MCP_HTTP_AUTHORIZATION_EXPIRED", "MCP_HTTP_CREDENTIAL_REVOKED",
    "MCP_HTTP_CREDENTIAL_EXPIRED", "MCP_HTTP_PLATFORM_TOKEN_REJECTED", "MCP_HTTP_CREDENTIAL_REJECTED",
    "MCP_HTTP_AUTHORIZATION_CHANGED", "MCP_HTTP_AUDIT_UNAVAILABLE", "MCP_HTTP_PROVIDER_NOT_CONFIGURED",
    "MCP_HTTP_PROVIDER_REJECTED", "MCP_HTTP_AUTHORIZATION_UNCONFIRMED", "MCP_HTTP_REVOCATION_UNCONFIRMED",
    "MCP_HTTP_CREDENTIAL_ACQUIRED", "MCP_HTTP_CALL_CONFIRMED", "MCP_HTTP_REVOCATION_CONFIRMED",
    "MCP_HTTP_PROTOCOL_UNAVAILABLE", "MCP_HTTP_DESCRIPTOR_REJECTED", "MCP_HTTP_REQUEST_REJECTED",
    "MCP_HTTP_PROTOCOL_REJECTED", "MCP_HTTP_TOOL_RESULT_REJECTED", "MCP_HTTP_REMOTE_REJECTED",
    "MCP_HTTP_TRANSPORT_UNCONFIRMED",
    "MCP_HTTP_DISPATCH_UNAVAILABLE", "MCP_HTTP_DISPATCH_ALREADY_CLAIMED", "MCP_HTTP_DISPATCH_SCOPE_REJECTED",
    "MCP_HTTP_IPC_UNCONFIRMED", "MCP_HTTP_IPC_PEER_REJECTED",
    "MCP_HTTP_EXECUTOR_RUNTIME_REQUIRED", "MCP_HTTP_EXECUTOR_POLICY_REJECTED", "MCP_HTTP_EXECUTOR_REQUEST_REJECTED",
    "MCP_HTTP_EXECUTOR_UNCONFIRMED", "MCP_HTTP_EXECUTOR_INTERRUPTED",
]


def require_https_identifier(value: str) -> str:
    """只接受维护者明确固定的HTTPS标识，不改写受众或从远端挑战推断目标。"""
    if any(ord(char) <= 32 or ord(char) >= 127 for char in value) or any(char in value for char in "\\%?#"):
        raise ValueError("HTTP MCP标识必须为精确HTTPS地址")
    try:
        parsed = urlsplit(value)
        host, port = parsed.hostname, parsed.port
    except ValueError:
        raise ValueError("HTTP MCP标识不合法") from None
    if (parsed.scheme != "https" or not host or parsed.username is not None or parsed.password is not None
        or host != host.lower() or host.endswith(".") or parsed.query or parsed.fragment
        or any(part in {".", ".."} for part in parsed.path.split("/")) or "//" in parsed.path):
        raise ValueError("HTTP MCP标识不合法")
    authority = f"[{host}]" if ":" in host else host
    if port is not None:
        if not 1 <= port <= 65535 or port == 443:
            raise ValueError("HTTP MCP标识须省略默认端口")
        authority += f":{port}"
    if parsed.netloc != authority or value != "https://" + authority + parsed.path:
        raise ValueError("HTTP MCP标识须明确规范形式")
    return value


class HttpMcpAuthorizationPolicy(DiscoveryContract):
    """可信部署配置供给；来源管理页面/模型不能批准OAuth目标和凭据。"""

    schema_version: Literal["http_mcp_authorization_policy_v1"] = "http_mcp_authorization_policy_v1"
    protocol_version: Literal["2025-11-25"] = "2025-11-25"
    tenant_id: UUID
    source_key: SourceKey
    source_record_id: UUID
    source_policy_digest: Digest
    provider_id: SourceKey
    credential_id: UUID
    credential_revision: Annotated[StrictInt, Field(ge=1)]
    issuer: HttpsIdentifier = Field(repr=False)
    resource: HttpsIdentifier = Field(repr=False)
    server_name: Annotated[str, Field(strict=True, min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.-]+$")]
    server_version: Annotated[str, Field(strict=True, min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")]
    transport_profile: Literal["stateless_json_v1"] = "stateless_json_v1"
    scopes: Scopes = Field(repr=False)
    valid_until: AwareDatetime
    max_token_lifetime_seconds: Annotated[StrictInt, Field(ge=10, le=300)] = 60

    @field_validator("issuer", "resource")
    @classmethod
    def identifiers(cls, value: str) -> str:
        return require_https_identifier(value)

    @field_validator("scopes")
    @classmethod
    def unique_scopes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("scope不能重复")
        return tuple(sorted(value))


class HttpMcpCallBinding(DiscoveryContract):
    """统一网关持久化后给出的固定引用，broker仍须独立核对当前许可。"""

    tenant_id: UUID
    actor_user_id: UUID
    run_id: UUID
    attempt_id: UUID
    generation: Annotated[StrictInt, Field(ge=1)]
    invocation_id: UUID
    dispatch_id: UUID
    tool_id: Annotated[str, Field(strict=True, min_length=1, max_length=128, pattern=r"^[A-Za-z][A-Za-z0-9_.-]*$")]
    implementation_version: Annotated[StrictInt, Field(ge=1)]
    descriptor_digest: Digest
    arguments_digest: Digest
    expires_at: AwareDatetime


class HttpMcpCredentialRequest(DiscoveryContract):
    schema_version: Literal["http_mcp_credential_request_v1"] = "http_mcp_credential_request_v1"
    policy: HttpMcpAuthorizationPolicy = Field(repr=False)
    binding: HttpMcpCallBinding = Field(repr=False)

    @model_validator(mode="after")
    def same_tenant(self) -> "HttpMcpCredentialRequest":
        if self.policy.tenant_id != self.binding.tenant_id or self.binding.expires_at > self.policy.valid_until:
            raise ValueError("调用主体或期限超出批准范围")
        return self

    @property
    def request_digest(self) -> str:
        return discovery_digest(self.model_dump(mode="json"))


class HttpMcpTokenState(DiscoveryContract):
    """由获准broker验证令牌签名/内省后给出，不接受客户端自报claims。"""

    schema_version: Literal["http_mcp_token_state_v1"] = "http_mcp_token_state_v1"
    lease_id: UUID
    request_digest: Digest
    provider_id: SourceKey
    credential_id: UUID
    credential_revision: Annotated[StrictInt, Field(ge=1)]
    tenant_id: UUID
    actor_user_id: UUID
    issuer: HttpsIdentifier = Field(repr=False)
    audiences: Annotated[tuple[HttpsIdentifier, ...], Field(min_length=1, max_length=1, repr=False)]
    scopes: Scopes = Field(repr=False)
    token_digest: Digest = Field(repr=False)
    issued_at: AwareDatetime
    expires_at: AwareDatetime
    revoked_at: AwareDatetime | None = None

    @field_validator("issuer")
    @classmethod
    def issuer_identifier(cls, value: str) -> str:
        return require_https_identifier(value)

    @field_validator("audiences")
    @classmethod
    def audience_identifiers(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(require_https_identifier(item) for item in value)

    @field_validator("scopes")
    @classmethod
    def unique_scopes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return HttpMcpAuthorizationPolicy.unique_scopes(value)

    @model_validator(mode="after")
    def time_range(self) -> "HttpMcpTokenState":
        if self.expires_at <= self.issued_at or self.revoked_at is not None and self.revoked_at < self.issued_at:
            raise ValueError("凭据时间范围无效")
        return self


class HttpMcpAuthorizationAudit(DiscoveryContract):
    """审计仅固定关联和结果码，不含令牌、目标地址、scope原文或上游异常。"""

    schema_version: Literal["http_mcp_authorization_audit_v1"] = "http_mcp_authorization_audit_v1"
    request_digest: Digest
    dispatch_id: UUID
    lease_id: UUID | None = None
    event: HttpMcpAuditEvent
    code: HttpMcpAuthorizationCode
    observed_at: AwareDatetime

    @model_validator(mode="after")
    def consistent_phase(self) -> "HttpMcpAuthorizationAudit":
        successes = {"credential_acquired": "MCP_HTTP_CREDENTIAL_ACQUIRED", "call_confirmed": "MCP_HTTP_CALL_CONFIRMED",
            "revocation_confirmed": "MCP_HTTP_REVOCATION_CONFIRMED"}
        if self.event != "authorization_rejected" and self.lease_id is None:
            raise ValueError("此审计阶段必须定位原lease")
        if self.event in successes and self.code != successes[self.event] or self.event not in successes and self.code in successes.values():
            raise ValueError("审计阶段与结果不一致")
        if self.event == "revocation_unconfirmed" and self.code != "MCP_HTTP_REVOCATION_UNCONFIRMED":
            raise ValueError("撤销未知阶段不匹配")
        return self
