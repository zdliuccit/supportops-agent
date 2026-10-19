"""固定目标的无会话JSON MCP调用；可选SDK只提供协议类型，不使用其通用网络传输。"""

import json
import math
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from typing import Literal
from uuid import UUID, uuid4

from pydantic import AwareDatetime, Field, SecretStr

from supportops_core.mcp_http_authorization import (
    HttpMcpAuditSink, HttpMcpAuthorizationError, HttpMcpCredentialBroker, run_authorized_http_mcp_call,
)
from supportops_core.mcp_http_authorization_contracts import HttpMcpAuthorizationCode, HttpMcpCredentialRequest
from supportops_core.mcp_isolated_contracts import PROTOCOL_VERSION, SDK_VERSION
from supportops_core.outbound_http import OutboundHttpError, OutboundHttpPolicy, request_fixed_json
from supportops_core.redaction import redact_sensitive_text
from supportops_core.tool_discovery_contracts import (
    Digest, DiscoveredTool, DiscoveryContract, canonical_json, discovery_digest, parse_discovery_catalog,
)


class HttpMcpToolObservation(DiscoveryContract):
    """未受信业务观察；未来网关仍需固定输出契约、当前授权和独立业务复核。"""

    schema_version: Literal["http_mcp_tool_observation_v1"] = "http_mcp_tool_observation_v1"
    request_digest: Digest
    source_record_id: UUID
    descriptor_digest: Digest
    observed_catalog_digest: Digest
    received_at: AwareDatetime
    data: dict[str, object] = Field(repr=False)
    data_is_redacted: bool
    is_untrusted_observation: Literal[True] = True
    execution_permit: Literal[False] = False
    business_status_confirmed: Literal[False] = False


def _tree(value: object, *, max_bytes: int) -> None:
    stack: list[tuple[object, int]] = [(value, 0)]
    nodes, text_bytes = 0, 0
    while stack:
        item, depth = stack.pop()
        nodes += 1
        if nodes > 4096 or depth > 16:
            raise ValueError()
        if type(item) is dict:
            if nodes+len(stack)+len(item) > 4096:
                raise ValueError()
            for key, child in item.items():
                if not isinstance(key, str) or len(key) > max_bytes:
                    raise ValueError()
                text_bytes += len(key.encode("utf-8"))
                if any(ord(char) < 32 for char in key):
                    raise ValueError()
                stack.append((child, depth+1))
        elif type(item) is list:
            if nodes+len(stack)+len(item) > 4096:
                raise ValueError()
            stack.extend((child, depth+1) for child in item)
        elif type(item) is str:
            if len(item) > max_bytes or any(ord(char) < 32 and char not in "\r\n\t" for char in item):
                raise ValueError()
            text_bytes += len(item.encode("utf-8"))
        elif type(item) is float:
            if not math.isfinite(item):
                raise ValueError()
        elif item is not None and type(item) not in {int, bool}:
            raise ValueError()
        if text_bytes > max_bytes:
            raise ValueError()


def _object(raw: bytes, *, max_bytes: int) -> dict[str, object]:
    if len(raw) > max_bytes:
        raise ValueError()
    def pairs(items: list[tuple[str, object]]) -> dict[str, object]:
        value: dict[str, object] = {}
        for key, item in items:
            if key in value:
                raise ValueError()
            value[key] = item
        return value
    def invalid(_value: str) -> object:
        raise ValueError()
    value = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs, parse_constant=invalid)
    if not isinstance(value, dict):
        raise ValueError()
    _tree(value, max_bytes=max_bytes)
    return value


def _freeze(value: dict[str, object], *, max_bytes: int) -> dict[str, object]:
    _tree(value, max_bytes=max_bytes)
    return _object(canonical_json(value).encode("utf-8"), max_bytes=max_bytes)


def _schema(schema: dict[str, object]) -> None:
    # 已批准Schema只能在本地解释；任何层次引用均拒绝，不解析网络或文件引用。
    stack: list[object] = [schema]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            if any(key in item for key in ("$ref", "$dynamicRef", "$id")):
                raise ValueError()
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)
    from jsonschema import Draft202012Validator
    Draft202012Validator.check_schema(schema)


def _validate(schema: dict[str, object], value: dict[str, object]) -> None:
    _schema(schema)
    from jsonschema import Draft202012Validator
    Draft202012Validator(schema).validate(value)


def _redact(value: object, secret: str, key: str = "") -> object:
    if isinstance(value, str):
        redacted = redact_sensitive_text(value, secrets=(secret,))
        if redacted != value and key.endswith(("_id", "_digest", "_key")):
            raise ValueError()
        return redacted
    if isinstance(value, dict):
        if any(redact_sensitive_text(name, secrets=(secret,)) != name for name in value):
            raise ValueError()
        return {name: _redact(item, secret, name) for name, item in value.items()}
    if isinstance(value, list):
        return [_redact(item, secret, key) for item in value]
    return value


async def call_http_mcp_tool(request: HttpMcpCredentialRequest, *, descriptor: DiscoveredTool,
    arguments: dict[str, object], outbound_policy: OutboundHttpPolicy,
    broker: HttpMcpCredentialBroker | None = None, audit_sink: HttpMcpAuditSink | None = None,
    platform_issuer: str, platform_audience: str,
) -> HttpMcpToolObservation:
    """最多四次固定POST：初始化、通知、单页发现、一次调用；无会话/SSE/自动重试。

    没有默认URL、凭据或网关注册。未来只能由获准独立执行器在真实broker许可下调用。
    """
    if broker is None or audit_sink is None:
        raise HttpMcpAuthorizationError("MCP_HTTP_PROVIDER_NOT_CONFIGURED")
    try:
        if version("mcp") != SDK_VERSION:
            raise ValueError()
        from mcp import types
        import jsonschema
        if not callable(getattr(jsonschema, "Draft202012Validator", None)):
            raise ValueError()
    except (ImportError, PackageNotFoundError, ValueError):
        raise HttpMcpAuthorizationError("MCP_HTTP_PROTOCOL_UNAVAILABLE") from None
    try:
        # 在首次await前冻结可变Schema和参数；调用者不能在取密期间修改派发内容。
        fixed = DiscoveredTool.model_validate(_freeze(descriptor.model_dump(mode="json"), max_bytes=131_072))
        params = _freeze(arguments, max_bytes=32_768)
        binding, policy = request.binding, request.policy
        expected_id = "mcp_"+discovery_digest([str(binding.tenant_id), policy.source_key, fixed.name])
        if (discovery_digest(fixed.model_dump(mode="json")) != binding.descriptor_digest
            or expected_id != binding.tool_id or discovery_digest(params) != binding.arguments_digest):
            raise ValueError()
        _schema(fixed.output_schema)
        _validate(fixed.input_schema, params)
    except Exception:
        raise HttpMcpAuthorizationError("MCP_HTTP_REQUEST_REJECTED") from None

    async def invoke(token: SecretStr, recheck: Callable[[], Awaitable[None]]) -> HttpMcpToolObservation:
        async def exchange(method: str, values: dict[str, object] | None = None, *, notification: bool = False) -> dict[str, object]:
            request_id = str(uuid4())
            frame: types.JSONRPCNotification | types.JSONRPCRequest
            if notification:
                frame = types.JSONRPCNotification(jsonrpc="2.0", method=method)
            else:
                frame = types.JSONRPCRequest(jsonrpc="2.0", id=request_id, method=method, params=values)
            wire = canonical_json(frame.model_dump(mode="json", by_alias=True, exclude_none=True)).encode("utf-8")
            await recheck()
            try:
                response = await request_fixed_json(url=policy.resource, policy=outbound_policy, method="POST",
                    payload=wire, bearer_token=token, timeout_seconds=3, max_request_bytes=65_536,
                    max_response_bytes=131_072, accepted_statuses=frozenset({202} if notification else {200}),
                    mcp_json_profile=True, before_send=recheck)
            except OutboundHttpError as exc:
                code: HttpMcpAuthorizationCode = "MCP_HTTP_REMOTE_REJECTED" if exc.http_status in {401, 403} else "MCP_HTTP_TRANSPORT_UNCONFIRMED"
                raise HttpMcpAuthorizationError(code) from None
            await recheck()
            if notification:
                return {}
            try:
                result = _object(response.body, max_bytes=131_072)
                if result.get("jsonrpc") != "2.0" or result.get("id") != request_id:
                    raise ValueError()
                if "error" in result:
                    raise HttpMcpAuthorizationError("MCP_HTTP_REMOTE_REJECTED")
                if set(result) != {"jsonrpc", "id", "result"} or not isinstance(result["result"], dict):
                    raise ValueError()
                return result["result"]
            except HttpMcpAuthorizationError:
                raise
            except Exception:
                raise HttpMcpAuthorizationError("MCP_HTTP_PROTOCOL_REJECTED") from None

        try:
            initialized = await exchange("initialize", types.InitializeRequestParams(protocolVersion=PROTOCOL_VERSION,
                capabilities=types.ClientCapabilities(), clientInfo=types.Implementation(name="supportops-http-json", version="1")
            ).model_dump(mode="json", by_alias=True, exclude_none=True))
            parsed = types.InitializeResult.model_validate(initialized)
            raw_capabilities, server_info = initialized.get("capabilities"), initialized.get("serverInfo")
            if not isinstance(raw_capabilities, dict) or not isinstance(server_info, dict):
                raise ValueError()
            capabilities = dict(raw_capabilities)
            if capabilities.get("experimental") == {}:
                capabilities.pop("experimental")
            if (set(initialized) != {"protocolVersion", "capabilities", "serverInfo"}
                or set(server_info) != {"name", "version"}
                or parsed.protocolVersion != PROTOCOL_VERSION or parsed.serverInfo.name != policy.server_name
                or parsed.serverInfo.version != policy.server_version):
                raise ValueError()
            tool_capability = capabilities.get("tools")
            if (set(capabilities) != {"tools"} or not isinstance(tool_capability, dict)
                or set(tool_capability)-{"listChanged"}
                or "listChanged" in tool_capability and tool_capability["listChanged"] is not False):
                raise ValueError()
            await exchange("notifications/initialized", notification=True)
            listing = await exchange("tools/list")
            listed = types.ListToolsResult.model_validate(listing)
            if set(listing)-{"tools", "nextCursor"} or listed.nextCursor is not None or not 0 < len(listed.tools) <= 100:
                raise ValueError()
            tools: list[dict[str, object]] = []
            for tool in listed.tools:
                wire = tool.model_dump(mode="json", by_alias=True, exclude_none=True)
                if set(wire) != {"name", "description", "inputSchema", "outputSchema"}:
                    raise ValueError()
                tools.append({"name": tool.name, "description": tool.description, "input_schema": tool.inputSchema,
                    "output_schema": tool.outputSchema})
            catalog = parse_discovery_catalog(canonical_json({"schema_version": "tool_discovery_catalog_v1", "tools": tools}))
            selected = [tool for tool in catalog.tools if tool.name == fixed.name]
            if len(selected) != 1 or canonical_json(selected[0].model_dump(mode="json")) != canonical_json(fixed.model_dump(mode="json")):
                raise HttpMcpAuthorizationError("MCP_HTTP_DESCRIPTOR_REJECTED")
            await recheck()
            result = await exchange("tools/call", {"name": fixed.name, "arguments": params})
            structured = result.get("structuredContent")
            if (not {"content", "structuredContent"} <= set(result)
                or set(result)-{"content", "structuredContent", "isError"} or type(result.get("isError", False)) is not bool
                or result.get("isError", False) or not isinstance(structured, dict)):
                raise ValueError()
            call = types.CallToolResult.model_validate(result)
            data = _freeze(structured, max_bytes=65_536)
            _validate(fixed.output_schema, data)
            if len(call.content) > 1:
                raise ValueError()
            if call.content:
                mirror = call.content[0].model_dump(mode="json", by_alias=True, exclude_none=True)
                if (set(mirror) != {"type", "text"} or mirror["type"] != "text"
                    or not isinstance(mirror["text"], str)
                    or canonical_json(_object(mirror["text"].encode("utf-8"), max_bytes=65_536)) != canonical_json(data)):
                    raise ValueError()
            redacted = _redact(data, token.get_secret_value())
            if not isinstance(redacted, dict):
                raise ValueError()
            filtered = _freeze(redacted, max_bytes=65_536)
            _validate(fixed.output_schema, filtered)
            return HttpMcpToolObservation(request_digest=request.request_digest, source_record_id=policy.source_record_id,
                descriptor_digest=binding.descriptor_digest, observed_catalog_digest=discovery_digest(catalog.model_dump(mode="json")),
                received_at=datetime.now(UTC), data=filtered, data_is_redacted=filtered != data)
        except HttpMcpAuthorizationError:
            raise
        except Exception:
            raise HttpMcpAuthorizationError("MCP_HTTP_PROTOCOL_REJECTED") from None

    return await run_authorized_http_mcp_call(request, operation=invoke, broker=broker, audit_sink=audit_sink,
        platform_issuer=platform_issuer, platform_audience=platform_audience)
