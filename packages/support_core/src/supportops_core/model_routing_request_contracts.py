"""路由完整逻辑输入与实际Provider正文；正文仅内存使用，不表示发送或批准。"""

import hashlib
import json
from typing import Literal
from uuid import UUID, uuid5

from pydantic import AwareDatetime, Field, model_validator

from supportops_core.agent_config import SupportAnswer
from supportops_core.model_routing_contracts import Digest, RoutingFilterModel

ROUTING_REQUEST_BYTES_MAX = 524288
ROUTING_PRODUCT_EVENT_TYPES = ("text.delta", "text.replace", "result.ready", "stage.completed",
    "stage.failed", "run.completed", "run.failed", "run.cancelled")
ROUTING_PRODUCT_CONFLICT_EVENT_TYPES = (*ROUTING_PRODUCT_EVENT_TYPES,
    "run.recovery_blocked", "routing.execution_stopped", "routing.request_rejected")
ROUTING_OUTCOME_EVENT_TYPES = (*ROUTING_PRODUCT_CONFLICT_EVENT_TYPES, "routing.product_committed")


class RoutingExecutionOutcomeView(RoutingFilterModel):
    """原attempt的最小只读查证；不包含正文、owner或任何恢复/执行许可。"""

    schema_version: Literal["routing_execution_outcome_v1"] = "routing_execution_outcome_v1"
    run_id: UUID
    attempt_id: UUID
    generation: int = Field(ge=1, strict=True)
    status: Literal["completed", "failed", "cancelled", "quarantined", "unconfirmed"]
    code: str | None = Field(default=None, max_length=100)
    root_call_id: UUID | None = None
    observed_at: AwareDatetime
    execution_permitted: Literal[False] = False
    recovery_permitted: Literal[False] = False


class RoutingWorkerStart(RoutingFilterModel):
    """Worker一次入口；随机entry只区分本次领取调用，不授予模型或产品许可。"""

    schema_version: Literal["routing_worker_start_v1"] = "routing_worker_start_v1"
    operation_id: UUID
    entry_id: UUID
    tenant_id: UUID
    conversation_id: UUID
    run_id: UUID
    attempt_id: UUID
    generation: int = Field(ge=1, strict=True)
    agent_version_id: UUID
    config_digest: Digest
    created_at: AwareDatetime

    @model_validator(mode="after")
    def original_operation(self) -> "RoutingWorkerStart":
        if self.operation_id != uuid5(self.run_id, "routing-worker-start-v1"):
            raise ValueError("ROUTING_WORKER_START_INVALID")
        return self


class RoutingRuntimeStart(RoutingFilterModel):
    """原真实输入事务的最小一次入口；不包含正文、owner或执行批准。"""

    schema_version: Literal["routing_runtime_start_v1"] = "routing_runtime_start_v1"
    operation_id: UUID
    tenant_id: UUID
    conversation_id: UUID
    run_id: UUID
    attempt_id: UUID
    generation: int = Field(ge=1, strict=True)
    agent_version_id: UUID
    actor_user_id: UUID
    input_sequence: int = Field(ge=1, strict=True)
    root_call_id: UUID
    root_plan_digest: Digest
    manifest_digest: Digest
    model_input_digest: Digest
    context_snapshot_digest: Digest
    checkpoint_thread_id: str = Field(min_length=1, max_length=300)
    created_at: AwareDatetime
    expires_at: AwareDatetime

    @model_validator(mode="after")
    def original_window(self) -> "RoutingRuntimeStart":
        if (self.operation_id != uuid5(self.run_id, "routing-runtime-start-v1")
            or not 0 < (self.expires_at - self.created_at).total_seconds() <= 30):
            raise ValueError("ROUTING_RUNTIME_START_INVALID")
        return self


def bounded_request_json(content: str) -> dict[str, object]:
    if len(content.encode("utf-8")) > ROUTING_REQUEST_BYTES_MAX:
        raise ValueError("ROUTING_REQUEST_TOO_LARGE")
    def unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
        values: dict[str, object] = {}
        for key, value in pairs:
            if key in values:
                raise ValueError("ROUTING_REQUEST_JSON_INVALID")
            values[key] = value
        return values
    try:
        value = json.loads(content, object_pairs_hook=unique)
        json.dumps(value, allow_nan=False)
        pending: list[tuple[object, int]] = [(value, 0)]
        count = 0
        while pending:
            item, depth = pending.pop()
            count += 1
            if depth > 16 or count > 4096:
                raise ValueError()
            if isinstance(item, dict):
                pending.extend((child, depth + 1) for child in item.values())
            elif isinstance(item, list):
                pending.extend((child, depth + 1) for child in item)
        if not isinstance(value, dict):
            raise ValueError()
        return value
    except (ValueError, TypeError, RecursionError):
        raise ValueError("ROUTING_REQUEST_JSON_INVALID") from None


class RoutingLogicalInput(RoutingFilterModel):
    schema_version: Literal["routing_logical_input_v1"] = "routing_logical_input_v1"
    attempt_id: UUID
    generation: int = Field(ge=1, strict=True)
    context_snapshot_digest: Digest
    payload_json: str = Field(min_length=1, max_length=ROUTING_REQUEST_BYTES_MAX, repr=False)
    model_input_digest: Digest
    reserved_input_tokens: int = Field(ge=1, le=2_000_000, strict=True)

    @model_validator(mode="after")
    def original_input(self) -> "RoutingLogicalInput":
        payload = bounded_request_json(self.payload_json)
        messages = payload.get("messages")
        if (set(payload) != {"system", "messages", "tools", "response_schema", "model_settings"}
            or payload["tools"] != [] or payload["model_settings"] != {}
            or payload["response_schema"] != SupportAnswer.model_json_schema()
            or not isinstance(messages, list) or not 1 <= len(messages) <= 2003
            or not isinstance(payload["system"], dict)
            or json.dumps(payload, ensure_ascii=False, sort_keys=True, allow_nan=False) != self.payload_json
            or hashlib.sha256(self.payload_json.encode("utf-8")).hexdigest() != self.model_input_digest
            or self.reserved_input_tokens != len(self.payload_json.encode("utf-8")) + 256 * (len(messages) + 2)):
            raise ValueError("ROUTING_LOGICAL_INPUT_INVALID")
        system = payload["system"]
        if (not isinstance(system, dict) or system.get("type") != "system" or not isinstance(system.get("content"), str)
            or system.get("additional_kwargs") != {} or system.get("response_metadata") != {} or system.get("name") is not None):
            raise ValueError("ROUTING_LOGICAL_INPUT_UNSUPPORTED")
        for message in messages:
            if (not isinstance(message, dict) or message.get("type") not in {"human", "ai"}
                or not isinstance(message.get("content"), str) or message.get("additional_kwargs") != {}
                or message.get("tool_calls") or message.get("invalid_tool_calls") or message.get("name") is not None):
                raise ValueError("ROUTING_LOGICAL_INPUT_UNSUPPORTED")
        if messages[-1]["type"] != "human" or not messages[-1]["content"].strip():
            raise ValueError("ROUTING_LOGICAL_INPUT_UNSUPPORTED")
        return self


class RoutingPreparedProviderRequest(RoutingFilterModel):
    schema_version: Literal["routing_prepared_provider_v1"] = "routing_prepared_provider_v1"
    call_id: UUID
    reservation_digest: Digest
    logical_input_digest: Digest
    model_version_id: UUID
    credential_revision: int = Field(ge=1, strict=True)
    model_request_digest: Digest
    outbound_policy_digest: Digest
    target_digest: Digest
    path: Literal["responses", "chat/completions"]
    payload_json: str = Field(min_length=1, max_length=ROUTING_REQUEST_BYTES_MAX, repr=False)
    body_digest: Digest
    organization: str | None = Field(default=None, max_length=300)
    timeout_seconds: float = Field(gt=0, le=30, allow_inf_nan=False)
    streaming: bool = Field(strict=True)
    reserved_input_tokens: int = Field(ge=1, le=2_000_000, strict=True)
    reserved_output_tokens: int = Field(ge=1, le=128_000, strict=True)
    sdk_max_retries: Literal[0] = 0
    execution_permitted: Literal[False] = False

    @model_validator(mode="after")
    def original_body(self) -> "RoutingPreparedProviderRequest":
        body = bounded_request_json(self.payload_json)
        output_key = "max_output_tokens" if self.path == "responses" else "max_completion_tokens"
        if (json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False) != self.payload_json
            or hashlib.sha256(self.payload_json.encode("utf-8")).hexdigest() != self.body_digest
            or len(self.payload_json.encode("utf-8")) + 1024 > self.reserved_input_tokens
            or body.get(output_key) != self.reserved_output_tokens or type(body.get(output_key)) is not int
            or type(body.get("stream")) is not bool or body["stream"] != self.streaming
            or body.get("store") is not False):
            raise ValueError("ROUTING_PREPARED_BODY_INVALID")
        return self
