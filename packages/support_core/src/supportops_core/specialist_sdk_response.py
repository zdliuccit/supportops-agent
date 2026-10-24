"""一次SDK响应的有限JSON/SSE观察；不从正文推算usage，不重连或生成业务事实。"""

from dataclasses import dataclass, field
from typing import Literal

from supportops_core.model_routing_accounting_contracts import RoutingUsageTotals
from supportops_core.specialist_response_contracts import SPECIALIST_RESPONSE_BYTES_MAX
from supportops_core.specialist_response_services import _json_object

SDK_RESPONSE_BYTES_MAX = 2_097_152


@dataclass(frozen=True, slots=True)
class SpecialistSDKReply:
    outcome: Literal["completed", "failed", "unknown"]
    usage: RoutingUsageTotals | None = None
    answer_json: str | None = field(default=None, repr=False)


def _usage(value: object, *, responses: bool) -> RoutingUsageTotals | None:
    if not isinstance(value, dict):
        return None
    first, second = ("input_tokens", "output_tokens") if responses else ("prompt_tokens", "completion_tokens")
    incoming, outgoing = value.get(first), value.get(second)
    if type(incoming) is not int or type(outgoing) is not int:
        return None
    total = value.get("total_tokens")
    if total is not None and (type(total) is not int or total != incoming + outgoing):
        return None
    input_details, output_details = value.get(first + "_details"), value.get(second + "_details")
    if input_details is not None and not isinstance(input_details, dict) or output_details is not None and not isinstance(output_details, dict):
        return None
    try:
        return RoutingUsageTotals.model_validate({"input_tokens": incoming, "output_tokens": outgoing,
            "cached_input_tokens": input_details.get("cached_tokens") if isinstance(input_details, dict) else None,
            "reasoning_tokens": output_details.get("reasoning_tokens") if isinstance(output_details, dict) else None}, strict=True)
    except ValueError:
        return None


def _answer(value: object) -> str | None:
    if not isinstance(value, str) or not value or len(value) > SPECIALIST_RESPONSE_BYTES_MAX:
        return None
    return value if len(value.encode("utf-8")) <= SPECIALIST_RESPONSE_BYTES_MAX else None


def _response(value: dict[str, object], *, model: str, forbidden: bool = False) -> SpecialistSDKReply:
    if value.get("model") != model:
        # 不从别名推断实际模型，也不把其他模型用量按原冻结价格结算。
        return SpecialistSDKReply("unknown")
    usage = _usage(value.get("usage"), responses=True)
    if usage is None:
        return SpecialistSDKReply("unknown")
    output = value.get("output")
    texts: list[str] = []
    if not isinstance(output, list) or len(output) > 32:
        forbidden = True
    else:
        for item in output:
            if not isinstance(item, dict):
                forbidden = True
            elif item.get("type") == "reasoning":
                continue  # 推理正文不复制到父级/账本；仍受总运输大小限制。
            elif item.get("type") != "message" or item.get("role") != "assistant" or item.get("status") != "completed":
                forbidden = True
            else:
                content = item.get("content")
                if not isinstance(content, list) or len(content) != 1:
                    forbidden = True
                    continue
                part = content[0]
                if not isinstance(part, dict) or part.get("type") != "output_text" or part.get("annotations") not in (None, []):
                    forbidden = True
                    continue
                text = _answer(part.get("text"))
                if text is None:
                    forbidden = True
                else:
                    texts.append(text)
    completed = (not forbidden and value.get("status") == "completed" and value.get("error") is None
        and value.get("incomplete_details") is None and value.get("model") == model and len(texts) == 1)
    return SpecialistSDKReply("completed" if completed else "failed", usage, texts[0] if completed else None)


def _chat(value: dict[str, object], *, model: str) -> SpecialistSDKReply:
    if value.get("model") != model:
        return SpecialistSDKReply("unknown")
    usage = _usage(value.get("usage"), responses=False)
    if usage is None:
        return SpecialistSDKReply("unknown")
    choices = value.get("choices")
    if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
        return SpecialistSDKReply("failed", usage)
    choice = choices[0]
    message = choice.get("message")
    if (choice.get("index") != 0 or type(choice.get("index")) is not int or choice.get("finish_reason") != "stop"
        or value.get("model") != model or not isinstance(message, dict) or message.get("role") != "assistant"
        or message.get("tool_calls") not in (None, []) or message.get("function_call") is not None
        or message.get("refusal") not in (None, "") or message.get("audio") is not None):
        return SpecialistSDKReply("failed", usage)
    answer = _answer(message.get("content"))
    return SpecialistSDKReply("completed" if answer else "failed", usage, answer)


def _frames(body: bytes) -> list[tuple[str, str]]:
    text = body.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
    frames: list[tuple[str, str]] = []
    data: list[str] = []
    event = ""
    for line in (*text.split("\n"), ""):
        if not line:
            if data:
                frames.append((event, "\n".join(data)))
                if len(frames) > 4096:
                    raise ValueError()
            data, event = [], ""
        elif line.startswith(":"):
            continue
        elif line.startswith("data:"):
            data.append(line[5:].removeprefix(" "))
        elif line.startswith("event:"):
            if event:
                raise ValueError()
            event = line[6:].removeprefix(" ")
        elif not line.startswith(("id:", "retry:")):
            raise ValueError()
    return frames


def _stream(body: bytes, *, responses: bool, model: str) -> SpecialistSDKReply:
    terminal: dict[str, object] | None = None
    chunks: list[str] = []
    tokens: RoutingUsageTotals | None = None
    identifier: object = None
    finish: str | None = None
    done, forbidden = False, False
    for event, text in _frames(body):
        if done:
            raise ValueError()
        if text == "[DONE]":
            done = True
            continue
        value = _json_object(text, bytes_limit=SDK_RESPONSE_BYTES_MAX)
        if responses:
            kind = value.get("type")
            if event and event != kind:
                forbidden = True
            if kind in {"response.completed", "response.failed", "response.incomplete"}:
                if terminal is not None or not isinstance(value.get("response"), dict):
                    raise ValueError()
                terminal = value["response"]
                if kind != "response." + str(terminal.get("status")):
                    forbidden = True
            elif terminal is not None:
                forbidden = True
            elif kind not in {"response.created", "response.queued", "response.in_progress", "response.output_item.added",
                "response.output_item.done", "response.content_part.added", "response.content_part.done",
                "response.output_text.delta", "response.output_text.done", "response.reasoning_summary_part.added",
                "response.reasoning_summary_part.done", "response.reasoning_summary_text.delta", "response.reasoning_summary_text.done"}:
                forbidden = True
            if kind in {"response.output_item.added", "response.output_item.done"}:
                item = value.get("item")
                if not isinstance(item, dict) or item.get("type") not in {"message", "reasoning"}:
                    forbidden = True
        else:
            if value.get("model") != model:
                return SpecialistSDKReply("unknown")
            if event not in ("", "message"):
                forbidden = True
            if identifier is None:
                identifier = value.get("id")
            if not isinstance(identifier, str) or value.get("id") != identifier:
                forbidden = True
            choices = value.get("choices")
            if not isinstance(choices, list) or len(choices) > 1:
                raise ValueError()
            if value.get("usage") is not None:
                if tokens is not None or choices or finish is None:
                    raise ValueError()
                tokens = _usage(value["usage"], responses=False)
                if tokens is None:
                    raise ValueError()
            if choices:
                choice = choices[0]
                if not isinstance(choice, dict) or type(choice.get("index")) is not int or choice.get("index") != 0:
                    raise ValueError()
                delta = choice.get("delta")
                if not isinstance(delta, dict) or finish is not None or tokens is not None:
                    raise ValueError()
                if (delta.get("role") not in (None, "assistant") or delta.get("tool_calls") not in (None, [])
                    or delta.get("function_call") is not None or delta.get("refusal") not in (None, "")
                    or set(delta) - {"role", "content", "tool_calls", "function_call", "refusal"}):
                    forbidden = True
                content = delta.get("content")
                if content is not None:
                    if not isinstance(content, str):
                        raise ValueError()
                    chunks.append(content)
                marker = choice.get("finish_reason")
                if marker is not None:
                    if not isinstance(marker, str):
                        raise ValueError()
                    finish = marker
    if responses:
        return _response(terminal, model=model, forbidden=forbidden) if terminal is not None else SpecialistSDKReply("unknown")
    if tokens is None:
        return SpecialistSDKReply("unknown")
    answer = _answer("".join(chunks))
    completed = not forbidden and done and finish == "stop" and answer is not None
    return SpecialistSDKReply("completed" if completed else "failed", tokens, answer if completed else None)


def parse_specialist_sdk_response(body: bytes, *, responses: bool, streaming: bool, model: str) -> SpecialistSDKReply:
    try:
        if len(body) > SDK_RESPONSE_BYTES_MAX:
            raise ValueError()
        if streaming:
            return _stream(body, responses=responses, model=model)
        value = _json_object(body.decode("utf-8"), bytes_limit=SDK_RESPONSE_BYTES_MAX)
        return _response(value, model=model) if responses else _chat(value, model=model)
    except (ValueError, TypeError, UnicodeError, RecursionError):
        return SpecialistSDKReply("unknown")
