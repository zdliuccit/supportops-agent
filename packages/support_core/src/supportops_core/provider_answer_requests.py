"""无工具Provider回答的确定性HTTP正文；不构造SDK、不联网或修改原回答Schema。"""

import json
from typing import Literal

from supportops_core.agent_config import GenerationConfig, SupportAnswer


def provider_answer_request(*, api_protocol: Literal["responses", "chat_completions"], model: str,
    system: str, messages: tuple[tuple[Literal["user", "assistant"], str], ...], temperature: float,
    max_output_tokens: int, reasoning_effort: str | None, verbosity: str | None, streaming: bool,
    reserved_input_tokens: int,
) -> tuple[Literal["responses", "chat/completions"], bytes]:
    """仅供已严格校验的固定配置/材料调用；完整正文必须落在原保守预留内。"""
    if (api_protocol not in {"responses", "chat_completions"} or not 1 <= len(model) <= 300 or not system.strip()
        or not messages or any(role not in {"user", "assistant"} or not isinstance(content, str) for role, content in messages)
        or type(streaming) is not bool or type(max_output_tokens) is not int or max_output_tokens < 1
        or type(reserved_input_tokens) is not int or reserved_input_tokens < 1):
        raise ValueError("PROVIDER_ANSWER_REQUEST_INVALID")
    GenerationConfig.model_validate({"temperature": temperature, "max_output_tokens": max_output_tokens,
        "reasoning_effort": reasoning_effort, "verbosity": verbosity, "max_retries": 0}, strict=True)
    body: dict[str, object] = {"model": model, "temperature": temperature, "stream": streaming}
    items = [{"role": role, "content": content} for role, content in messages]
    schema = SupportAnswer.model_json_schema()
    if api_protocol == "responses":
        text: dict[str, object] = {"format": {"type": "json_schema", "name": "support_answer_v1", "strict": True, "schema": schema}}
        if verbosity is not None:
            text["verbosity"] = verbosity
        body.update({"instructions": system, "input": items, "max_output_tokens": max_output_tokens,
            "tools": [], "store": False, "text": text})
        if reasoning_effort is not None:
            body["reasoning"] = {"effort": reasoning_effort}
        path: Literal["responses", "chat/completions"] = "responses"
    else:
        if verbosity is not None:
            raise ValueError("PROVIDER_ANSWER_REQUEST_INVALID")
        body.update({"messages": [{"role": "system", "content": system}, *items],
            "max_completion_tokens": max_output_tokens, "n": 1, "store": False,
            "response_format": {"type": "json_schema", "json_schema": {"name": "support_answer_v1", "strict": True, "schema": schema}}})
        if reasoning_effort is not None:
            body["reasoning_effort"] = reasoning_effort
        if streaming:
            body["stream_options"] = {"include_usage": True}
        path = "chat/completions"
    wire = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    if len(wire) + 1024 > reserved_input_tokens:
        raise ValueError("PROVIDER_ANSWER_INPUT_BOUND_EXCEEDED")
    return path, wire
