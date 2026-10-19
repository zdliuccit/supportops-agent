"""独立HTTP执行进程的封闭非秘密策略、单次请求及不可信观察回执。"""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from supportops_core.mcp_http_authorization_contracts import (
    HttpMcpAuthorizationCode, HttpMcpAuthorizationPolicy, HttpMcpCredentialRequest,
)
from supportops_core.mcp_http_client import HttpMcpToolObservation
from supportops_core.tool_discovery_contracts import Digest, DiscoveredTool, DiscoveryContract, discovery_digest

MAX_INPUT_BYTES = 196_608
MAX_OUTPUT_BYTES = 131_072
IpLiteral = Annotated[str, Field(strict=True, min_length=2, max_length=45)]


class HttpMcpExecutorPolicy(DiscoveryContract):
    """维护者只读挂载的固定目标批准；不是凭据、网关许可或OS网络策略证明。"""

    schema_version: Literal["http_mcp_executor_policy_v1"] = "http_mcp_executor_policy_v1"
    authorization_policy: HttpMcpAuthorizationPolicy = Field(repr=False)
    platform_issuer: str = Field(strict=True, min_length=1, max_length=256, repr=False)
    platform_audience: str = Field(strict=True, min_length=1, max_length=256, repr=False)
    allowed_ips: Annotated[tuple[IpLiteral, ...], Field(min_length=1, max_length=32)] = Field(repr=False)
    private_ips: Annotated[tuple[IpLiteral, ...], Field(max_length=32)] = Field(default=(), repr=False)

    @field_validator("platform_issuer", "platform_audience")
    @classmethod
    def fixed_identifier(cls, value: str) -> str:
        if any(ord(char) <= 32 or ord(char) >= 127 for char in value):
            raise ValueError()
        return value

    @model_validator(mode="after")
    def fixed_scope(self) -> "HttpMcpExecutorPolicy":
        if (self.authorization_policy.issuer == self.platform_issuer
            or self.authorization_policy.resource == self.platform_audience
            or len(set(self.allowed_ips)) != len(self.allowed_ips) or len(set(self.private_ips)) != len(self.private_ips)
            or not set(self.private_ips) <= set(self.allowed_ips)):
            raise ValueError()
        return self

    @property
    def policy_digest(self) -> str:
        return discovery_digest(self.model_dump(mode="json"))


class HttpMcpExecutorRequest(DiscoveryContract):
    """由可信网关构造；无token/命令/凭据路径/任意网络策略字段。"""

    schema_version: Literal["http_mcp_executor_request_v1"] = "http_mcp_executor_request_v1"
    nonce: UUID
    deployment_policy_digest: Digest
    request_digest: Digest
    request: HttpMcpCredentialRequest = Field(repr=False)
    descriptor: DiscoveredTool = Field(repr=False)
    arguments: dict[str, object] = Field(repr=False)

    @model_validator(mode="after")
    def bound_request(self) -> "HttpMcpExecutorRequest":
        if (self.request_digest != self.request.request_digest
            or discovery_digest(self.descriptor.model_dump(mode="json")) != self.request.binding.descriptor_digest
            or discovery_digest(self.arguments) != self.request.binding.arguments_digest):
            raise ValueError()
        return self


class HttpMcpExecutorReport(DiscoveryContract):
    schema_version: Literal["http_mcp_executor_report_v1"] = "http_mcp_executor_report_v1"
    nonce: UUID | None = None
    dispatch_id: UUID | None = None
    request_digest: Digest | None = None
    status: Literal["observed", "unconfirmed"]
    error_code: HttpMcpAuthorizationCode | None = None
    observation: HttpMcpToolObservation | None = Field(default=None, repr=False)
    invocation_attempted: bool = Field(strict=True)
    result_confirmed: bool = Field(strict=True)
    business_status_confirmed: Literal[False] = False
    execution_permit: Literal[False] = False
    automatic_retries_enabled: Literal[False] = False

    @model_validator(mode="after")
    def consistent_report(self) -> "HttpMcpExecutorReport":
        correlation = (self.nonce, self.dispatch_id, self.request_digest)
        if any(item is None for item in correlation) and any(item is not None for item in correlation):
            raise ValueError()
        if self.invocation_attempted and self.nonce is None:
            raise ValueError()
        if self.status == "observed":
            if (not self.result_confirmed or not self.invocation_attempted or self.error_code is not None
                or self.observation is None or self.nonce is None or self.observation.request_digest != self.request_digest):
                raise ValueError()
        elif (self.result_confirmed or self.error_code is None or self.observation is not None
            or self.error_code in {"MCP_HTTP_CREDENTIAL_ACQUIRED", "MCP_HTTP_CALL_CONFIRMED", "MCP_HTTP_REVOCATION_CONFIRMED"}):
            raise ValueError()
        return self


def unconfirmed_executor_report(code: HttpMcpAuthorizationCode, request: HttpMcpExecutorRequest | None,
    attempted: bool,
) -> HttpMcpExecutorReport:
    """stdout与认证socket共用失败投影，不推断远端是否执行。"""
    return HttpMcpExecutorReport(nonce=request.nonce if request else None,
        dispatch_id=request.request.binding.dispatch_id if request else None,
        request_digest=request.request_digest if request else None, status="unconfirmed", error_code=code,
        invocation_attempted=attempted, result_confirmed=False)
