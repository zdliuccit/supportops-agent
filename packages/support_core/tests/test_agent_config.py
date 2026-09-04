from uuid import uuid4

import pytest
from pydantic import ValidationError
from supportops_core.agent_config import (
    agent_config_digest,
    default_agent_config,
    normalize_agent_config,
    parse_agent_config,
)
from supportops_core.model_config import (
    ModelEndpointVersionConfig,
    resolve_and_validate_endpoint,
    validate_endpoint_url_policy,
)


def test_agent_config_normalization_and_digest_are_stable() -> None:
    config = default_agent_config(uuid4())
    normalized = normalize_agent_config(config)

    assert parse_agent_config(normalized) == config
    assert agent_config_digest(config) == agent_config_digest(parse_agent_config(normalized))


def test_agent_config_rejects_unknown_fields_and_secrets() -> None:
    config = normalize_agent_config(default_agent_config(uuid4()))
    config["unknown"] = True

    with pytest.raises(ValidationError):
        parse_agent_config(config)

    config.pop("unknown")
    prompt = config["prompt"]
    assert isinstance(prompt, dict)
    prompt["system_prompt"] = "Authorization: Bearer sk-abcdefghijklmnop"
    with pytest.raises(ValidationError):
        parse_agent_config(config)


def test_model_endpoint_provider_rules_and_ssrf_policy() -> None:
    official = ModelEndpointVersionConfig.model_validate(
        {
            "provider_kind": "openai_official",
            "api_protocol": "responses",
            "base_url": "https://evil.example/v1",
            "remote_model_name": "gpt-test",
        }
    )
    assert official.base_url == "https://api.openai.com/v1"

    with pytest.raises(ValueError, match="HTTPS"):
        validate_endpoint_url_policy(
            "http://relay.example/v1",
            allow_private_networks=False,
            allowed_hosts=[],
        )
    with pytest.raises(ValueError, match="私网"):
        validate_endpoint_url_policy(
            "https://127.0.0.1/v1",
            allow_private_networks=False,
            allowed_hosts=[],
        )
    with pytest.raises(ValueError, match="允许列表"):
        validate_endpoint_url_policy(
            "https://relay.example/v1",
            allow_private_networks=False,
            allowed_hosts=["approved.example"],
        )


async def test_model_endpoint_rechecks_resolved_addresses(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "supportops_core.model_config.socket.getaddrinfo",
        lambda *args, **kwargs: [(2, 1, 6, "", ("127.0.0.1", 0))],
    )

    with pytest.raises(ValueError, match="私网"):
        await resolve_and_validate_endpoint(
            "https://public-name.example/v1", allow_private_networks=False
        )
