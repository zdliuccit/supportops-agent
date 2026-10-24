"""固定子请求复用共享一次POST桥接；保留原准备字段、响应与错误域。"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from pydantic import SecretStr

from supportops_core.outbound_http import OutboundHttpPolicy
from supportops_core.provider_answer_requests import provider_answer_request
from supportops_core.provider_sdk_transport import assert_provider_sdk_supported, invoke_provider_sdk_once
from supportops_core.specialist_request_contracts import SpecialistPreparedRequest
from supportops_core.specialist_sdk_response import SpecialistSDKReply, parse_specialist_sdk_response


def assert_specialist_sdk_supported() -> None:
    assert_provider_sdk_supported("SPECIALIST_SDK")


@dataclass(frozen=True, slots=True)
class SpecialistSDKMaterial:
    prepared: SpecialistPreparedRequest = field(repr=False)
    base_url: str = field(repr=False)
    api_key: SecretStr = field(repr=False)


@dataclass(frozen=True, slots=True)
class SpecialistSDKObservation:
    reply: SpecialistSDKReply
    started_at: datetime | None
    finished_at: datetime | None


def _request(prepared: SpecialistPreparedRequest) -> tuple[Literal["responses", "chat/completions"], bytes, bool, bool, str, str | None]:
    import json

    payload = json.loads(prepared.payload_json)
    settings = payload["model_settings"]
    responses = payload["api_protocol"] == "responses"
    streaming = settings["streaming"]
    try:
        path, wire = provider_answer_request(api_protocol=payload["api_protocol"], model=payload["model"],
            system=payload["system"], messages=tuple((row["role"], row["content"]) for row in payload["messages"]),
            temperature=settings["temperature"], max_output_tokens=settings["max_completion_tokens"],
            reasoning_effort=settings["reasoning_effort"], verbosity=settings["verbosity"], streaming=streaming,
            reserved_input_tokens=prepared.reserved_input_tokens)
    except ValueError:
        raise ValueError("SPECIALIST_SDK_INPUT_BOUND_EXCEEDED") from None
    return path, wire, responses, streaming, payload["model"], settings["organization"]


async def _invoke_specialist_sdk_once(*, material: SpecialistSDKMaterial, policy: OutboundHttpPolicy,
    fence: Callable[[], Awaitable[None]], expires_at: datetime,
) -> SpecialistSDKObservation:
    """仅当前消费成功后调用；共享传输不开启新的子任务围栏或重试行为。"""
    assert_specialist_sdk_supported()
    path, body, responses, streaming, model, organization = _request(material.prepared)
    observed = await invoke_provider_sdk_once(base_url=material.base_url, path=path, body=body, key=material.api_key,
        organization=organization, streaming=streaming, policy=policy, fence=fence, expires_at=expires_at,
        prefix="SPECIALIST_SDK")
    reply = SpecialistSDKReply("unknown") if observed.raw_body is None else parse_specialist_sdk_response(
        observed.raw_body, responses=responses, streaming=streaming, model=model)
    return SpecialistSDKObservation(reply=reply, started_at=observed.started_at, finished_at=observed.finished_at)
