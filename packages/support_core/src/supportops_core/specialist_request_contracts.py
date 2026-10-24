"""固定子Prompt/知识材料与无工具Provider参数；端点和凭据仅绑定摘要/revision。"""

import hashlib
import json
from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from supportops_core.agent_config import GenerationConfig, SupportAnswer
from supportops_core.model_config import ModelRequestMetadata
from supportops_core.specialist_task_contracts import Count, Digest, SpecialistContract, SpecialistVersion

# 平台现有每项256字节协议预留：system/user两条消息、输出Schema和生成控制各一项。
SPECIALIST_REQUEST_OVERHEAD = 4 * 256
SPECIALIST_REQUEST_BYTES_MAX = 524288


def canonical_request_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


class SpecialistPreparedRequest(SpecialistContract):
    schema_version: Literal["specialist_prepared_request_v1"] = "specialist_prepared_request_v1"
    task_id: UUID
    task_input_digest: Digest
    context_digest: Digest
    child_version: SpecialistVersion
    model_request_digest: Digest
    credential_revision: int = Field(ge=1, le=2**31 - 1)
    # 完整适配器输入用规范JSON字符串冻结，避免浅层frozen模型留下可变字典。
    payload_json: str = Field(min_length=1, max_length=SPECIALIST_REQUEST_BYTES_MAX, repr=False)
    model_input_digest: Digest
    reserved_input_tokens: Count = Field(ge=1)
    reserved_output_tokens: Count = Field(ge=1)
    token_strategy: Literal["utf8_upper_bound_v1"] = "utf8_upper_bound_v1"
    execution_permitted: Literal[False] = False

    @model_validator(mode="after")
    def full_payload_binding(self) -> "SpecialistPreparedRequest":
        encoded = self.payload_json.encode("utf-8")
        if (len(encoded) > SPECIALIST_REQUEST_BYTES_MAX
            or hashlib.sha256(encoded).hexdigest() != self.model_input_digest
            or self.reserved_input_tokens != len(encoded) + SPECIALIST_REQUEST_OVERHEAD):
            raise ValueError("SPECIALIST_PREPARED_REQUEST_INVALID")
        try:
            payload = json.loads(self.payload_json)
        except (ValueError, RecursionError):
            raise ValueError("SPECIALIST_PREPARED_REQUEST_INVALID") from None
        if not isinstance(payload, dict) or canonical_request_json(payload) != self.payload_json:
            raise ValueError("SPECIALIST_PREPARED_REQUEST_INVALID")
        if set(payload) != {"adapter", "api_protocol", "model", "model_request_digest", "credential_revision",
            "system", "messages", "tools", "response_strategy", "response_schema", "model_settings"}:
            raise ValueError("SPECIALIST_PREPARED_REQUEST_INVALID")
        settings = payload["model_settings"]
        messages = payload["messages"]
        if (payload["adapter"] != "specialist_provider_answer_v1"
            or payload["api_protocol"] not in ("responses", "chat_completions")
            or not isinstance(payload["model"], str) or not 1 <= len(payload["model"]) <= 300
            or payload["model_request_digest"] != self.model_request_digest
            or type(payload["credential_revision"]) is not int or payload["credential_revision"] != self.credential_revision
            or not isinstance(payload["system"], str) or not payload["system"].strip()
            or not isinstance(messages, list) or len(messages) != 1
            or not isinstance(messages[0], dict) or set(messages[0]) != {"role", "content"}
            or messages[0]["role"] != "user" or not isinstance(messages[0]["content"], str)
            or not messages[0]["content"].strip()
            or payload["tools"] != [] or payload["response_strategy"] != {"name": "provider", "strict": True}
            or type(payload["response_strategy"]["strict"]) is not bool
            or payload["response_schema"] != SupportAnswer.model_json_schema()
            or not isinstance(settings, dict)
            or set(settings) != {"temperature", "max_completion_tokens", "reasoning_effort", "verbosity",
                "timeout", "max_retries", "extra_body", "organization", "streaming", "disable_streaming"}
            or settings["max_completion_tokens"] != self.reserved_output_tokens
            or settings["extra_body"] != {}
            or type(settings["max_retries"]) is not int or settings["max_retries"] != 0):
            raise ValueError("SPECIALIST_PREPARED_REQUEST_INVALID")
        GenerationConfig.model_validate({"temperature": settings["temperature"],
            "max_output_tokens": settings["max_completion_tokens"], "reasoning_effort": settings["reasoning_effort"],
            "verbosity": settings["verbosity"], "max_retries": settings["max_retries"]}, strict=True)
        ModelRequestMetadata.model_validate({"organization_id": settings["organization"]}, strict=True)
        if (type(settings["timeout"]) not in (int, float) or not 0 < settings["timeout"] <= 600
            or type(settings["streaming"]) is not bool or type(settings["disable_streaming"]) is not bool
            or settings["disable_streaming"] != (settings["streaming"] is False)
            or payload["api_protocol"] == "chat_completions" and settings["verbosity"] is not None):
            raise ValueError("SPECIALIST_PREPARED_REQUEST_INVALID")
        return self
