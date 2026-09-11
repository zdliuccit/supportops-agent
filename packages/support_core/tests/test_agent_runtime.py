from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
from typing import Any, cast
from uuid import uuid4

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from psycopg import OperationalError
from pydantic import ValidationError
from supportops_core import agent_runtime
from supportops_core.agent_config import (
    AgentConfigV2,
    SupportContext,
    default_agent_config,
    normalize_agent_config,
)
from supportops_core.agent_runtime import (
    AgentCapabilityError,
    AgentCheckpointError,
    AgentFactory,
    AgentRuntimeConfigurationError,
    AgentRuntimeSnapshot,
    AgentStructuredOutputError,
    ModelAdapter,
    checkpoint_input_messages,
    checkpoint_thread_id,
    enforce_run_budgets,
    filter_runtime_tools,
    invoke_agent,
    parse_structured_answer,
    validate_runtime_capabilities,
)
from supportops_core.enums import ModelApiProtocol, ModelProviderKind, ResponseStrategy
from supportops_core.models import AgentVersion, ModelEndpointVersion


def snapshot(*, config: AgentConfigV2, input_text: str) -> AgentRuntimeSnapshot:
    agent_id = uuid4()
    model_version_id = uuid4()
    context = SupportContext(
        tenant_id=uuid4(),
        user_id=uuid4(),
        roles=("employee",),
        conversation_id=uuid4(),
        run_id=uuid4(),
        correlation_id="runtime-test",
    )
    return AgentRuntimeSnapshot(
        agent_version=AgentVersion(
            id=uuid4(),
            tenant_id=context.tenant_id,
            agent_id=agent_id,
            version_number=1,
            schema_version="2",
            config=normalize_agent_config(config),
            config_digest="a" * 64,
            model_endpoint_version_id=model_version_id,
            resolved_tool_ids=["support_ticket_lookup"],
            published_by=context.user_id,
        ),
        model_version=ModelEndpointVersion(
            id=model_version_id,
            tenant_id=context.tenant_id,
            endpoint_id=uuid4(),
            version_number=1,
            provider_kind=ModelProviderKind.OPENAI_OFFICIAL,
            api_protocol=ModelApiProtocol.RESPONSES,
            base_url="https://api.openai.com/v1",
            remote_model_name="test-model",
            capabilities={"tool_calling": True},
            defaults={},
            request_metadata={},
            pricing={"input_per_million_tokens": 10, "output_per_million_tokens": 30},
            credential_id=uuid4(),
            created_by=context.user_id,
        ),
        config=config,
        api_key="test-key",
        context=context,
        input_text=input_text,
    )


def test_runtime_rejects_input_and_cost_budget_before_model_call() -> None:
    model_endpoint_id = uuid4()
    value = normalize_agent_config(default_agent_config(model_endpoint_id))
    runtime = value["runtime"]
    assert isinstance(runtime, dict)
    runtime["max_input_tokens"] = 256
    input_limited = snapshot(config=AgentConfigV2.model_validate(value), input_text="x" * 1100)

    with pytest.raises(AgentRuntimeConfigurationError, match="token 上限"):
        enforce_run_budgets(input_limited)

    runtime["max_input_tokens"] = None
    runtime["max_cost_usd"] = 0.001
    cost_limited = snapshot(config=AgentConfigV2.model_validate(value), input_text="hello")
    with pytest.raises(AgentRuntimeConfigurationError, match="成本上界"):
        enforce_run_budgets(cost_limited)


def test_runtime_tool_filter_and_checkpoint_thread_are_tenant_scoped() -> None:
    value = normalize_agent_config(default_agent_config(uuid4()))
    value["tools"] = [
        {
            "tool_id": "support_ticket_lookup",
            "enabled": True,
            "max_calls_per_run": 4,
            "approval_policy": "none",
        }
    ]
    active = snapshot(config=AgentConfigV2.model_validate(value), input_text="ticket")

    assert [tool.name for tool in filter_runtime_tools(active)] == ["support_ticket_lookup"]
    thread_id = checkpoint_thread_id(active.context)
    assert str(active.context.tenant_id) in thread_id
    assert str(active.context.conversation_id) in thread_id


def test_checkpoint_rehydrates_product_history_only_without_existing_checkpoint() -> None:
    value = normalize_agent_config(default_agent_config(uuid4()))
    current = snapshot(config=AgentConfigV2.model_validate(value), input_text="第二问")
    current = replace(
        current,
        product_messages=(("user", "第一问"), ("assistant", "第一答"), ("user", "第二问")),
    )

    assert checkpoint_input_messages(current, has_checkpoint=False) == [
        {"role": "user", "content": "第一问"},
        {"role": "assistant", "content": "第一答"},
        {"role": "user", "content": "第二问"},
    ]
    assert checkpoint_input_messages(current, has_checkpoint=True) == [
        {"role": "user", "content": "第二问"}
    ]


def test_structured_answer_rejects_missing_or_invalid_payload() -> None:
    answer = parse_structured_answer({"structured_response": {"answer": "已处理"}})
    assert answer.answer == "已处理"

    with pytest.raises(AgentStructuredOutputError, match="校验失败") as invalid:
        parse_structured_answer({"structured_response": {"answer": ""}})
    assert isinstance(invalid.value.__cause__, ValidationError)

    with pytest.raises(AgentStructuredOutputError, match="未返回"):
        parse_structured_answer({"messages": []})


def test_runtime_revalidates_fixed_model_capabilities() -> None:
    value = normalize_agent_config(default_agent_config(uuid4()))
    current = snapshot(config=AgentConfigV2.model_validate(value), input_text="hello")
    current.model_version.capabilities = {"tool_calling": False, "structured_output": False}

    with pytest.raises(AgentCapabilityError, match="ToolStrategy"):
        validate_runtime_capabilities(current)

    value["runtime"]["response_strategy"] = ResponseStrategy.PROVIDER.value  # type: ignore[index]
    provider = snapshot(config=AgentConfigV2.model_validate(value), input_text="hello")
    provider.model_version.capabilities = {"tool_calling": True, "structured_output": False}
    with pytest.raises(AgentCapabilityError, match="ProviderStrategy"):
        validate_runtime_capabilities(provider)


def test_agent_factory_maps_limits_context_and_response_strategy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    value = normalize_agent_config(default_agent_config(uuid4()))
    value["runtime"]["model_call_limit"] = 5  # type: ignore[index]
    value["runtime"]["tool_call_limit"] = 7  # type: ignore[index]
    current = snapshot(config=AgentConfigV2.model_validate(value), input_text="hello")
    captured: dict[str, Any] = {}

    def fake_create_agent(**kwargs: Any) -> str:
        captured.update(kwargs)
        return "graph"

    monkeypatch.setattr(agent_runtime, "create_agent", fake_create_agent)
    graph = AgentFactory.build(
        current,
        model=cast(Any, object()),
        checkpointer=InMemorySaver(),
        tools=[],
    )

    assert graph == "graph"
    assert captured["context_schema"] is SupportContext
    assert captured["system_prompt"] == current.config.prompt.system_prompt
    assert captured["name"] == "technical_support_agent"
    assert len(captured["middleware"]) == 2
    assert type(captured["response_format"]).__name__ == "ToolStrategy"


async def test_model_adapter_maps_fixed_openai_compatible_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    value = normalize_agent_config(default_agent_config(uuid4()))
    current = snapshot(config=AgentConfigV2.model_validate(value), input_text="hello")
    current.model_version.provider_kind = ModelProviderKind.OPENAI_COMPATIBLE
    current.model_version.api_protocol = ModelApiProtocol.CHAT_COMPLETIONS
    current.model_version.base_url = "https://gateway.example.com/v1"
    current.model_version.remote_model_name = "enterprise-model"
    current.model_version.defaults = {
        "extension_options": {"provider_extension": "enabled"},
        "reasoning_effort": "medium",
        "verbosity": "low",
    }

    async def no_op_resolution(*args: object, **kwargs: object) -> tuple[str, ...]:
        return ("203.0.113.10",)

    monkeypatch.setattr(agent_runtime, "resolve_and_validate_endpoint", no_op_resolution)
    model = await ModelAdapter.build(
        current,
        allow_private_networks=False,
        allowed_hosts=["gateway.example.com"],
    )

    assert model.model_name == "enterprise-model"
    assert str(model.openai_api_base) == "https://gateway.example.com/v1"
    assert model.extra_body == {"provider_extension": "enabled"}
    assert model.default_headers == {"User-Agent": "supportops-agent"}
    assert model.use_responses_api is False
    assert model.reasoning_effort == "medium"
    # Chat Completions 不发送 Responses 专属的 verbosity 参数。
    assert model.verbosity is None


async def test_model_adapter_prefers_agent_generation_over_endpoint_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    value = normalize_agent_config(default_agent_config(uuid4()))
    model_config = value["model"]
    assert isinstance(model_config, dict)
    generation = model_config["generation"]
    assert isinstance(generation, dict)
    generation["reasoning_effort"] = "high"
    generation["verbosity"] = "high"
    current = snapshot(config=AgentConfigV2.model_validate(value), input_text="hello")
    current.model_version.defaults = {"reasoning_effort": "low", "verbosity": "low"}

    async def no_op_resolution(*args: object, **kwargs: object) -> tuple[str, ...]:
        return ("203.0.113.10",)

    monkeypatch.setattr(agent_runtime, "resolve_and_validate_endpoint", no_op_resolution)
    model = await ModelAdapter.build(
        current,
        allow_private_networks=False,
        allowed_hosts=["api.openai.com"],
    )

    assert model.reasoning_effort == "high"
    assert model.verbosity == "high"


async def test_invoke_agent_classifies_postgres_checkpoint_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    value = normalize_agent_config(default_agent_config(uuid4()))
    current = snapshot(config=AgentConfigV2.model_validate(value), input_text="hello")

    async def fake_model(*args: object, **kwargs: object) -> Any:
        return object()

    @asynccontextmanager
    async def broken_checkpoint(*args: object, **kwargs: object) -> AsyncIterator[Any]:
        raise OperationalError("checkpoint unavailable")
        yield  # pragma: no cover - 保持 async context manager 生成器签名

    monkeypatch.setattr(ModelAdapter, "build", fake_model)
    monkeypatch.setattr(agent_runtime, "checkpoint_saver", broken_checkpoint)

    with pytest.raises(AgentCheckpointError, match="checkpoint 读写失败"):
        await invoke_agent(
            current,
            settings=agent_runtime.Settings(
                environment="test",
                database_url="postgresql+asyncpg://unused",
                jwt_secret="test-secret-with-at-least-thirty-two-characters",
            ),
        )
