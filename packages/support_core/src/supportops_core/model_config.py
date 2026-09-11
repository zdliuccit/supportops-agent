"""模型端点配置 Schema、URL/DNS 安全策略和错误脱敏工具。"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from typing import Any, Literal
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from supportops_core.enums import ModelApiProtocol, ModelProviderKind

OPENAI_OFFICIAL_BASE_URL = "https://api.openai.com/v1"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ModelCapabilities(StrictModel):
    """连接测试需要逐项确认的模型增强能力。"""

    streaming: bool = True
    tool_calling: bool = False
    structured_output: bool = False
    parallel_tool_calls: bool = False
    vision: bool = False


class ModelParameterDefaults(StrictModel):
    """模型默认生成参数和上下文/并发限制。"""

    temperature: float = Field(default=0.2, ge=0, le=2)
    max_output_tokens: int = Field(default=4096, ge=1, le=128_000)
    reasoning_effort: Literal["none", "low", "medium", "high", "xhigh", "max"] | None = Field(
        default=None,
        description="推理模型默认推理强度；不支持时保持为空。",
    )
    verbosity: Literal["low", "medium", "high"] | None = Field(
        default=None,
        description="Responses 模型默认回答详细程度；不支持时保持为空。",
    )
    timeout_seconds: int = Field(default=60, ge=1, le=600)
    max_retries: int = Field(default=2, ge=0, le=10)
    context_window_tokens: int | None = Field(default=None, ge=1024, le=2_000_000)
    requests_per_minute: int | None = Field(default=None, ge=1, le=1_000_000)
    tokens_per_minute: int | None = Field(default=None, ge=1, le=1_000_000_000)
    max_concurrency: int = Field(default=8, ge=1, le=10_000)
    extension_options: dict[str, Any] = Field(
        default_factory=dict,
        description="随模型请求发送的 JSON 扩展对象；默认空对象。",
    )


class ModelRequestMetadata(StrictModel):
    """允许发送给供应商的非秘密组织与项目元数据。"""

    organization_id: str | None = Field(default=None, max_length=200)
    project_id: str | None = Field(default=None, max_length=200)


class ModelPricing(StrictModel):
    """执行成本预算使用的每百万 Token 价格。"""

    currency: str = Field(default="USD", pattern=r"^[A-Z]{3}$")
    input_per_million_tokens: float | None = Field(default=None, ge=0)
    output_per_million_tokens: float | None = Field(default=None, ge=0)


class ModelEndpointVersionConfig(StrictModel):
    """发布后不可变的模型、能力和参数配置。"""

    provider_kind: ModelProviderKind
    api_protocol: ModelApiProtocol = ModelApiProtocol.CHAT_COMPLETIONS
    base_url: str = Field(max_length=2000)
    remote_model_name: str = Field(min_length=1, max_length=300)
    capabilities: ModelCapabilities = Field(default_factory=ModelCapabilities)
    defaults: ModelParameterDefaults = Field(default_factory=ModelParameterDefaults)
    request_metadata: ModelRequestMetadata = Field(default_factory=ModelRequestMetadata)
    pricing: ModelPricing = Field(default_factory=ModelPricing)

    @field_validator("base_url")
    @classmethod
    def normalize_url_shape(cls, value: str) -> str:
        return normalize_base_url_shape(value)

    @model_validator(mode="after")
    def apply_provider_rules(self) -> ModelEndpointVersionConfig:
        if self.provider_kind == ModelProviderKind.OPENAI_OFFICIAL:
            self.base_url = OPENAI_OFFICIAL_BASE_URL
        return self


def normalize_base_url_shape(value: str) -> str:
    """规范化 Base URL，拒绝查询、片段和内嵌凭据。"""
    parsed = urlsplit(value.strip())
    if parsed.scheme not in {"https", "http"} or not parsed.hostname:
        raise ValueError("API Base URL 必须是有效的 HTTP(S) URL")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("API Base URL 不能包含用户信息")
    if parsed.query or parsed.fragment:
        raise ValueError("API Base URL 不能包含 query 或 fragment")
    path = parsed.path.rstrip("/")
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), path, "", ""))


def validate_endpoint_url_policy(
    base_url: str,
    *,
    allow_private_networks: bool,
    allowed_hosts: list[str],
) -> str:
    """在 DNS 解析前检查 HTTPS、localhost 和域名允许列表。"""
    normalized = normalize_base_url_shape(base_url)
    parsed = urlsplit(normalized)
    hostname = parsed.hostname
    if hostname is None:
        raise ValueError("API Base URL 缺少主机名")
    hostname = hostname.lower().rstrip(".")
    if allowed_hosts and hostname not in {host.lower().rstrip(".") for host in allowed_hosts}:
        raise ValueError("模型端点域名不在允许列表中")
    is_localhost = hostname == "localhost" or hostname.endswith(".localhost")
    if parsed.scheme != "https" and not (allow_private_networks and is_localhost):
        raise ValueError("模型端点必须使用 HTTPS")
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        return normalized
    validate_ip_addresses([address], allow_private_networks=allow_private_networks)
    return normalized


def validate_ip_addresses(
    addresses: list[ipaddress.IPv4Address | ipaddress.IPv6Address],
    *,
    allow_private_networks: bool,
) -> None:
    """拒绝解析到回环、私网、链路本地或保留地址。"""
    if allow_private_networks:
        return
    for address in addresses:
        if not address.is_global:
            raise ValueError("模型端点不能解析到私网、回环、链路本地或保留地址")


async def resolve_and_validate_endpoint(
    base_url: str,
    *,
    allow_private_networks: bool,
) -> list[str]:
    """解析并复检全部目标 IP，防止域名绕过出站网络策略。"""
    hostname = urlsplit(base_url).hostname
    if hostname is None:
        raise ValueError("API Base URL 缺少主机名")
    infos = await asyncio.to_thread(socket.getaddrinfo, hostname, None, type=socket.SOCK_STREAM)
    addresses = sorted({str(info[4][0]) for info in infos})
    parsed_addresses = [ipaddress.ip_address(address) for address in addresses]
    validate_ip_addresses(parsed_addresses, allow_private_networks=allow_private_networks)
    return addresses


def redact_provider_error(value: str, secrets: tuple[str, ...] = ()) -> str:
    """清除供应商错误中的密钥并限制可记录长度。"""
    redacted = value
    for secret in secrets:
        if secret:
            redacted = redacted.replace(secret, "[REDACTED]")
    return redacted[:500]
