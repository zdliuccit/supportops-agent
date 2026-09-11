from uuid import uuid4

import httpx
import pytest
from supportops_core import model_services
from supportops_core.enums import ModelApiProtocol, ModelProviderKind
from supportops_core.model_services import (
    _capability_probe_payload,
    _send_bounded_probe,
    discover_remote_models,
)
from supportops_core.models import ModelEndpointVersion


def endpoint_version(protocol: ModelApiProtocol) -> ModelEndpointVersion:
    return ModelEndpointVersion(
        tenant_id=uuid4(),
        endpoint_id=uuid4(),
        version_number=1,
        provider_kind=(
            ModelProviderKind.OPENAI_OFFICIAL
            if protocol == ModelApiProtocol.RESPONSES
            else ModelProviderKind.OPENAI_COMPATIBLE
        ),
        api_protocol=protocol,
        base_url="https://relay.example/v1",
        remote_model_name="test-model",
        capabilities={},
        defaults={},
        request_metadata={},
        pricing={},
        credential_id=uuid4(),
        created_by=uuid4(),
    )


def test_capability_probe_payloads_match_each_protocol() -> None:
    responses = endpoint_version(ModelApiProtocol.RESPONSES)
    chat = endpoint_version(ModelApiProtocol.CHAT_COMPLETIONS)
    chat.defaults = {"extension_options": {"service_tier": "auto", "model": "ignored"}}

    responses_tool = _capability_probe_payload(responses, "tool_calling")
    chat_tool = _capability_probe_payload(chat, "tool_calling")
    responses_structured = _capability_probe_payload(responses, "structured_output")
    chat_structured = _capability_probe_payload(chat, "structured_output")

    assert responses_tool["tools"][0]["name"] == "supportops_probe"  # type: ignore[index]
    assert chat_tool["tools"][0]["function"]["name"] == "supportops_probe"  # type: ignore[index]
    assert "text" in responses_structured
    assert "response_format" in chat_structured
    assert _capability_probe_payload(chat, "streaming")["stream"] is True
    assert chat_tool["service_tier"] == "auto"
    assert chat_tool["model"] == "test-model"


async def test_capability_probe_rejects_oversized_response() -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=b"x" * 128))
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(ValueError, match="超过平台大小限制"):
            await _send_bounded_probe(
                client,
                url="https://relay.example/v1/chat/completions",
                headers={},
                payload={"model": "test"},
                max_response_bytes=64,
            )


@pytest.mark.parametrize(
    "response_or_error",
    [
        httpx.Response(302, headers={"Location": "http://127.0.0.1/private"}),
        httpx.ReadTimeout("probe timeout"),
    ],
)
async def test_capability_probe_does_not_follow_redirects_and_propagates_timeout(
    response_or_error: httpx.Response | Exception,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if isinstance(response_or_error, Exception):
            raise response_or_error
        return response_or_error

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(httpx.HTTPError):
            await _send_bounded_probe(
                client,
                url="https://relay.example/v1/chat/completions",
                headers={},
                payload={"model": "test"},
                max_response_bytes=1024,
            )


async def test_explicit_model_discovery_reads_standard_catalog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """显式模型发现只读取标准目录并返回去重后的模型 ID。"""

    async def allow_test_endpoint(base_url: str, *, allow_private_networks: bool) -> list[str]:
        del base_url, allow_private_networks
        return ["203.0.113.10"]

    monkeypatch.setattr(model_services, "resolve_and_validate_endpoint", allow_test_endpoint)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url == "https://relay.example/v1/models"
        assert request.headers["authorization"] == "Bearer sk-discovery-test"
        return httpx.Response(
            200,
            json={"data": [{"id": "support-b"}, {"id": "support-a"}, {"id": "support-b"}]},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await discover_remote_models(
            base_url="https://relay.example/v1",
            api_key="sk-discovery-test",
            allow_private_networks=False,
            allowed_hosts=[],
            timeout_seconds=5,
            max_response_bytes=4096,
            client=client,
        )

    assert result == ["support-a", "support-b"]
