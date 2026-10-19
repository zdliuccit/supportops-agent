"""工具发现治理契约；仅管理目录元数据，不建立连接或授予执行权限。"""

import hashlib
import json
import math
from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictBool, StrictInt, field_validator, model_validator

Digest = Annotated[str, Field(strict=True, pattern=r"^[0-9a-f]{64}$")]
SourceKey = Annotated[str, Field(strict=True, pattern=r"^[a-z][a-z0-9_-]{0,63}$")]
RemoteToolName = Annotated[str, Field(strict=True, pattern=r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")]
MAX_CATALOG_BYTES = 131_072


class DiscoveryContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ApprovedDiscoveryTool(DiscoveryContract):
    name: RemoteToolName
    descriptor_digest: Digest


class ToolSourcePolicy(DiscoveryContract):
    """管理员显式批准的来源指纹及精确工具描述摘要；不接受URL/凭据。"""

    schema_version: Literal["tool_source_policy_v1"] = "tool_source_policy_v1"
    source_fingerprint: Digest
    enabled: StrictBool = False
    valid_until: AwareDatetime
    allowed_tools: list[ApprovedDiscoveryTool] = Field(max_length=50)

    @field_validator("valid_until", mode="before")
    @classmethod
    def require_explicit_time(cls, value: object) -> object:
        if not isinstance(value, (str, datetime)):
            raise ValueError("批准期限须为带时区时间，不接受数字时间戳")
        return value

    @model_validator(mode="after")
    def check_scope(self) -> "ToolSourcePolicy":
        names = [tool.name for tool in self.allowed_tools]
        if len(names) != len(set(names)) or self.enabled and not names:
            raise ValueError("工具批准集合不完整或重复")
        return self


class DiscoveredTool(DiscoveryContract):
    """适配器从远端目录投影的封闭描述，不透传annotations等任意元数据。"""

    name: RemoteToolName
    description: str = Field(strict=True, max_length=2000)
    input_schema: dict[str, object]
    output_schema: dict[str, object]

    @model_validator(mode="after")
    def check_schemas(self) -> "DiscoveredTool":
        for schema in (self.input_schema, self.output_schema):
            if schema.get("type") != "object" or schema.get("additionalProperties") is not False:
                raise ValueError("发现工具必须提供封闭对象输入/输出Schema")
        return self


class DiscoveryCatalog(DiscoveryContract):
    schema_version: Literal["tool_discovery_catalog_v1"] = "tool_discovery_catalog_v1"
    tools: list[DiscoveredTool] = Field(max_length=100)


class DiscoveryPolicyCommand(DiscoveryContract):
    expected_revision: Annotated[StrictInt, Field(ge=0)]
    policy: ToolSourcePolicy


class DiscoveryInvalidationCommand(DiscoveryContract):
    expected_policy_digest: Digest
    source_fingerprint: Digest
    reason: Literal["list_changed", "manual_refresh"]


class DiscoveryCatalogCommand(DiscoveryContract):
    expected_revision: Annotated[StrictInt, Field(ge=1)]
    expected_policy_digest: Digest
    source_fingerprint: Digest
    catalog_json: str = Field(strict=True, min_length=1, max_length=MAX_CATALOG_BYTES)


class DiscoveryToolBinding(DiscoveryContract):
    tool_id: str
    remote_name: RemoteToolName
    descriptor_digest: Digest
    input_schema_digest: Digest
    output_schema_digest: Digest


class DiscoverySnapshotTool(DiscoveryToolBinding):
    descriptor: DiscoveredTool


class DiscoverySnapshot(DiscoveryContract):
    schema_version: Literal["tool_discovery_snapshot_v1"] = "tool_discovery_snapshot_v1"
    tenant_id: UUID
    source_key: SourceKey
    policy_digest: Digest
    base_revision: Annotated[StrictInt, Field(ge=1)]
    tools: list[DiscoverySnapshotTool] = Field(max_length=50)


class DiscoveryView(DiscoveryContract):
    schema_version: Literal["tool_discovery_view_v1"] = "tool_discovery_view_v1"
    record_id: UUID
    source_key: SourceKey
    revision: int
    status: Literal["pending", "ready", "disabled", "rejected", "expired"]
    policy_digest: Digest
    policy: ToolSourcePolicy
    source_fingerprint: Digest
    valid_until: datetime
    catalog_digest: Digest | None = None
    tools: list[DiscoveryToolBinding] = Field(default_factory=list)
    failure_code: str | None = None
    observed_count: int = 0
    created_at: datetime
    execution_enabled: Literal[False] = False
    connection_status: Literal["not_connected"] = "not_connected"


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def discovery_digest(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def policy_payload(policy: ToolSourcePolicy) -> dict[str, object]:
    value = policy.model_dump(mode="json")
    value["valid_until"] = policy.valid_until.astimezone(UTC).isoformat()
    value["allowed_tools"] = [tool.model_dump() for tool in sorted(policy.allowed_tools, key=lambda tool: tool.name)]
    # 返回新JSON树，不把调用方可变列表作为持久化对象共享。
    return json.loads(canonical_json(value))


def parse_discovery_catalog(raw: str) -> DiscoveryCatalog:
    """限制运输、重复键和JSON树；不解析引用、获取外链或执行Schema。"""
    if len(raw.encode("utf-8")) > MAX_CATALOG_BYTES:
        raise ValueError("目录超限")

    def pairs(items: list[tuple[str, object]]) -> dict[str, object]:
        value: dict[str, object] = {}
        for key, item in items:
            key.encode("utf-8")
            if "\x00" in key:
                raise ValueError("目录字段名无效")
            if key in value:
                raise ValueError("目录包含重复键")
            value[key] = item
        return value

    def invalid_constant(_value: str) -> object:
        raise ValueError("目录数值无效")

    value = json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid_constant)
    stack: list[tuple[object, int]] = [(value, 0)]
    nodes = 0
    while stack:
        item, depth = stack.pop()
        nodes += 1
        if nodes > 4096 or depth > 16:
            raise ValueError("目录结构超限")
        if isinstance(item, dict):
            if any(key in item for key in ("$ref", "$dynamicRef", "$id")):
                raise ValueError("目录Schema引用未获支持")
            stack.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            stack.extend((child, depth + 1) for child in item)
        elif isinstance(item, str):
            item.encode("utf-8")
            if any(ord(char) < 32 and char not in "\n\r\t" for char in item):
                raise ValueError("目录文本无效")
        elif isinstance(item, float) and not math.isfinite(item):
            raise ValueError("目录数值无效")
    catalog = DiscoveryCatalog.model_validate(value)
    names = [tool.name for tool in catalog.tools]
    if len(names) != len(set(names)):
        raise ValueError("远端工具名重复")
    return catalog
