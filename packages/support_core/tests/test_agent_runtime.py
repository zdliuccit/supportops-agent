import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
from typing import Any, cast
from uuid import uuid4

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessageChunk, BaseMessage
from langchain_core.outputs import ChatGenerationChunk, ChatResult
from langgraph.checkpoint.memory import InMemorySaver
from psycopg import OperationalError
from pydantic import ValidationError
from structlog.testing import capture_logs
from supportops_core import agent_runtime
from supportops_core.agent_config import (
    TOOL_REGISTRY,
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
    StructuredAnswerStream,
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


def test_tool_stream_only_exposes_answer_and_handles_partial_unicode() -> None:
    stream = StructuredAnswerStream(ResponseStrategy.TOOL)
    assert (
        stream.feed(
            AIMessageChunk(
                id="lookup",
                content="内部思考",
                tool_call_chunks=[
                    {
                        "index": 0,
                        "name": "support_ticket_lookup",
                        "args": '{"answer":"工具参数不能展示"}',
                        "id": "lookup-call",
                    }
                ],
            )
        )
        is None
    )
    first = AIMessageChunk(
        id="answer",
        content="",
        tool_call_chunks=[
            {
                "index": 0,
                "name": "SupportAnswer",
                "args": '{"answer":"你好\\n\\u4f',
                "id": "answer-call",
            }
        ],
    )
    assert stream.feed(first) == "你好\n"
    assert (
        stream.feed(
            AIMessageChunk(
                id="answer",
                content="",
                tool_call_chunks=[
                    {
                        "index": 0,
                        "name": None,
                        "args": '60🙂","summary":"摘要不能展示"}',
                        "id": None,
                    }
                ],
            )
        )
        == "你好\n你🙂"
    )
    # 校验重试开启新的模型消息，应改写而不是拼接上一轮回答。
    assert (
        stream.feed(
            AIMessageChunk(
                id="retry",
                content="",
                tool_call_chunks=[
                    {
                        "index": 0,
                        "name": "SupportAnswer",
                        "args": '{"answer":"修正后的回答',
                        "id": "retry-call",
                    }
                ],
            )
        )
        == "修正后的回答"
    )


def test_provider_stream_extracts_json_text_and_ignores_reasoning() -> None:
    stream = StructuredAnswerStream(ResponseStrategy.PROVIDER)
    assert (
        stream.feed(
            AIMessageChunk(
                id="provider",
                content=[
                    {
                        "type": "reasoning",
                        "reasoning": "不应展示的推理",
                    }
                ],
            )
        )
        is None
    )
    assert (
        stream.feed(
            AIMessageChunk(
                id="provider",
                content=[
                    {
                        "type": "text",
                        "text": '{"answer":"渐进',
                        "index": 0,
                    }
                ],
            )
        )
        == "渐进"
    )
    assert (
        stream.feed(
            AIMessageChunk(
                id="provider",
                content=[
                    {
                        "type": "text",
                        "text": '输出","citations":[]}',
                        "index": 0,
                    }
                ],
            )
        )
        == "渐进输出"
    )


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
            resolved_tool_versions={
                "support_ticket_lookup": TOOL_REGISTRY["support_ticket_lookup"].version
            },
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
    active.agent_version.resolved_tool_versions = {"support_ticket_lookup": 1}
    with pytest.raises(AgentRuntimeConfigurationError, match="固定工具实现版本不可用"):
        filter_runtime_tools(active)


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
    assert captured["system_prompt"] == (
        agent_runtime._PLATFORM_DATA_POLICY + current.config.prompt.system_prompt
    )
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


@pytest.mark.parametrize("strategy", [ResponseStrategy.TOOL, ResponseStrategy.PROVIDER])
async def test_real_agent_graph_streams_structured_answer_before_completion(
    monkeypatch: pytest.MonkeyPatch,
    strategy: ResponseStrategy,
) -> None:
    """使用真实 LangGraph/ToolStrategy 验证增量回调，而不只模拟 SSE 输出。"""
    first_received = asyncio.Event()
    received: list[str] = []

    class StreamingModel(BaseChatModel):
        @property
        def _llm_type(self) -> str:
            return "stream-test"

        def bind_tools(self, tools: Any, **kwargs: Any) -> Any:
            return self

        def _generate(self, messages: list[BaseMessage], **kwargs: Any) -> ChatResult:
            raise AssertionError("必须使用模型流式接口")

        async def _astream(
            self, messages: list[BaseMessage], **kwargs: Any
        ) -> AsyncIterator[ChatGenerationChunk]:
            if strategy == ResponseStrategy.PROVIDER:
                yield ChatGenerationChunk(
                    message=AIMessageChunk(
                        id="stream-model",
                        content='{"answer":"第一段',
                    )
                )
                await asyncio.wait_for(first_received.wait(), timeout=2)
                yield ChatGenerationChunk(
                    message=AIMessageChunk(
                        id="stream-model",
                        content='第二段"}',
                    )
                )
                return
            yield ChatGenerationChunk(
                message=AIMessageChunk(
                    id="stream-model",
                    content="",
                    tool_call_chunks=[
                        {
                            "index": 0,
                            "name": "SupportAnswer",
                            "args": '{"answer":"第一段',
                            "id": "answer-call",
                        }
                    ],
                )
            )
            await asyncio.wait_for(first_received.wait(), timeout=2)
            yield ChatGenerationChunk(
                message=AIMessageChunk(
                    id="stream-model",
                    content="",
                    tool_call_chunks=[
                        {
                            "index": 0,
                            "name": None,
                            "args": '第二段"}',
                            "id": None,
                        }
                    ],
                )
            )

    async def fake_model(*args: object, **kwargs: object) -> BaseChatModel:
        return StreamingModel()

    @asynccontextmanager
    async def memory_checkpoint(*args: object, **kwargs: object) -> AsyncIterator[InMemorySaver]:
        yield InMemorySaver()

    async def on_answer(content: str) -> None:
        received.append(content)
        first_received.set()

    value = normalize_agent_config(default_agent_config(uuid4()))
    runtime = value["runtime"]
    assert isinstance(runtime, dict)
    runtime["response_strategy"] = strategy.value
    current = snapshot(config=AgentConfigV2.model_validate(value), input_text="hello")
    current.model_version.capabilities["structured_output"] = True
    monkeypatch.setattr(ModelAdapter, "build", fake_model)
    monkeypatch.setattr(agent_runtime, "checkpoint_saver", memory_checkpoint)
    with capture_logs() as logs:
        result = await invoke_agent(
            current,
            settings=agent_runtime.Settings(
                environment="test",
                llm_stream_log_enabled=True,
                database_url="postgresql+asyncpg://unused",
                jwt_secret="test-secret-with-at-least-thirty-two-characters",
            ),
            on_answer=on_answer,
        )
    assert received == ["第一段", "第一段第二段"]
    assert result.answer.answer == "第一段第二段"
    assert result.model_call_count == 1
    # ToolStrategy仅流出结构化参数，控制台不把工具参数当成正文输出。
    expected_logs = (
        ["LLM 开始", "LLM 输出中...", "LLM 结束"]
        if strategy == ResponseStrategy.PROVIDER
        else ["LLM 开始", "LLM 结束"]
    )
    assert [entry["event"] for entry in logs] == expected_logs
    assert {entry["call_index"] for entry in logs} == {1}
    assert all(set(entry) == {"event", "call_index", "log_level"} for entry in logs)
    assert "test-api-key" not in str(logs)
    assert "第一段" not in str(logs)
    assert "第一段" not in str(result.observations)


async def test_stream_console_logging_groups_calls_and_handles_failure() -> None:
    """输出中只打印一次，多次调用和失败仍有独立状态，日志关闭时保持静默。"""
    current = snapshot(config=default_agent_config(uuid4()), input_text="不应打印的输入")
    first_id, second_id = uuid4(), uuid4()
    with capture_logs() as logs:
        callback = agent_runtime._RuntimeObservationCallback(current, stream_logging=True)
        await callback.on_llm_start({}, [current.input_text], run_id=first_id)
        await callback.on_llm_new_token("", run_id=first_id)
        await callback.on_llm_new_token("后续正文不打印", run_id=first_id)
        await callback.on_llm_end(None, run_id=first_id)
        await callback.on_llm_start({}, [current.input_text], run_id=second_id)
        await callback.on_llm_error(RuntimeError("敏感异常原文"), run_id=second_id)
    assert [(entry["call_index"], entry["event"]) for entry in logs] == [
        (1, "LLM 开始"),
        (1, "LLM 输出中..."),
        (1, "LLM 结束"),
        (2, "LLM 开始"),
        (2, "LLM 结束（失败）"),
    ]
    assert callback.results()[-1].error_code == "MODEL_CALL_FAILED"
    assert current.input_text not in str(logs)
    assert "敏感异常原文" not in str(logs)
    assert "后续正文不打印" not in str(logs)
    with capture_logs() as disabled:
        callback = agent_runtime._RuntimeObservationCallback(current, stream_logging=False)
        await callback.on_llm_start({}, [], run_id=first_id)
        await callback.on_llm_new_token("", run_id=first_id)
        await callback.on_llm_end(None, run_id=first_id)
    assert disabled == []
