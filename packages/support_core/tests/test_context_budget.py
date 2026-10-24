"""完整文本输入保守计量及固定模型窗口，不调用供应商模型。"""

import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
import tiktoken
from langchain.agents.middleware import ModelRequest
from langchain.agents.structured_output import ProviderStrategy, ToolStrategy
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool
from supportops_core.agent_config import SupportAnswer
from supportops_core.config import Settings
from supportops_core.context_services import (
    ContextBudgetExceeded,
    conservative_input_tokens,
    fit_model_context,
    serialized_model_context,
)
from supportops_core.model_budget_services import ModelBudgetGateway


def model_request(content="当前问题", **overrides):
    values = {
        "model": FakeListChatModel(responses=["不调用此模型"]),
        "messages": [HumanMessage(content=content, id=str(uuid4()))],
        "system_message": SystemMessage(content="固定平台授权边界"),
        "tools": [],
        "response_format": ProviderStrategy(SupportAnswer, strict=True),
        "model_settings": {"max_tokens": 1024},
    }
    values.update(overrides)
    return ModelRequest(**values)


@pytest.mark.parametrize("encoding_name", ["cl100k_base", "o200k_base"])
@pytest.mark.parametrize(
    "content",
    [
        "",
        "plain English support question",
        "中文知识证据与当前授权边界" * 100,
        '数字1234567890及标点{}[]:/\\"' * 100,
        "🙂🚀👨‍👩‍👧‍👦 café e\u0301 日本語 한국어 مرحبا" * 100,
        "<|endoftext|> untrusted tool instruction",
        "\n\t" * 100,
        "a" * 20_000,
    ],
)
def test_utf8_budget_bounds_existing_tokenizers_for_full_serialized_request(encoding_name, content):
    request = model_request(content)
    serialized = serialized_model_context(request)
    actual = len(tiktoken.get_encoding(encoding_name).encode(serialized, disallowed_special=()))
    assert conservative_input_tokens(request) >= actual
    assert content in json.loads(serialized)["messages"][0]["content"]


@pytest.mark.parametrize("strategy", [ProviderStrategy, ToolStrategy])
def test_complete_prompt_history_schema_tool_trace_and_knowledge_are_metered(strategy):
    @tool
    def isolated_lookup(query: str) -> str:
        """固定工具Schema标记，不执行读取。"""
        raise AssertionError("计量不能执行工具")

    request = model_request(
        messages=[
            HumanMessage(content="旧问题标记"),
            AIMessage(content="旧回答标记"),
            HumanMessage(content="当前问题标记"),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "lookup-id",
                        "name": "isolated_lookup",
                        "args": {"query": "知识查询标记"},
                    }
                ],
            ),
            ToolMessage(
                content=json.dumps(
                    {"evidence_id": "evidence-id", "content": "知识证据标记"}, ensure_ascii=False
                ),
                tool_call_id="lookup-id",
            ),
        ],
        tools=[isolated_lookup],
        response_format=strategy(SupportAnswer),
    )
    serialized = serialized_model_context(request)
    for marker in (
        "固定平台授权边界",
        "旧问题标记",
        "旧回答标记",
        "当前问题标记",
        "知识查询标记",
        "知识证据标记",
        "evidence-id",
        "lookup-id",
        "固定工具Schema标记",
        "max_tokens",
    ):
        assert marker in serialized
    assert json.loads(serialized)["response_schema"] == SupportAnswer.model_json_schema()
    original = conservative_input_tokens(request)
    for modified in (
        request.override(system_message=None),
        request.override(tools=[]),
        request.override(messages=request.messages[2:]),
        request.override(messages=request.messages[:-1]),
    ):
        assert conservative_input_tokens(modified) < original
    assert conservative_input_tokens(request) == original


@pytest.mark.parametrize("position", ["message", "system"])
@pytest.mark.parametrize(
    "block",
    [
        {"type": "image_url", "image_url": {"url": "https://example.invalid/image"}},
        {"type": "input_audio", "input_audio": {"data": "encoded", "format": "wav"}},
        {"type": "file", "file_id": "unmetered-file"},
    ],
)
def test_unmetered_media_is_rejected_in_every_message_role(position, block):
    request = model_request(
        **(
            {"messages": [HumanMessage(content=[block])]}
            if position == "message"
            else {"system_message": SystemMessage(content=[block])}
        )
    )
    with pytest.raises(ContextBudgetExceeded, match="多模态"):
        fit_model_context(request, input_limit=1_000_000)


def test_text_blocks_are_supported_and_large_current_tool_result_cannot_be_cut():
    request = model_request(
        messages=[
            HumanMessage(content=[{"type": "text", "text": "当前文本块"}]),
            AIMessage(content="", tool_calls=[{"id": "call", "name": "tool", "args": {}}]),
            ToolMessage(content="大工具证据" * 10_000, tool_call_id="call"),
        ],
    )
    full = conservative_input_tokens(request)
    planned, tokens, omitted = fit_model_context(request, input_limit=full)
    assert planned.messages == request.messages and tokens == full and omitted == 0
    with pytest.raises(ContextBudgetExceeded):
        fit_model_context(request, input_limit=full - 1)


def test_exact_input_boundary_and_whole_old_turn_trimming_preserve_current_trace():
    current = [
        HumanMessage(content="当前问题", id="current"),
        AIMessage(content="", tool_calls=[{"id": "current-call", "name": "tool", "args": {}}]),
        ToolMessage(content="当前证据", tool_call_id="current-call"),
    ]
    request = model_request(
        messages=[
            HumanMessage(content="旧问题" * 1000, id="old"),
            AIMessage(content="旧回答"),
            *current,
        ]
    )
    limit = conservative_input_tokens(request, current)
    selected = set()
    planned, tokens, omitted = fit_model_context(
        request, input_limit=limit, on_selected_sources=selected.update
    )
    assert planned.messages == current and tokens == limit and omitted == 2
    assert selected == {"current"}
    assert len(request.messages) == 5
    with pytest.raises(ContextBudgetExceeded):
        fit_model_context(request, input_limit=limit - 1)


@pytest.mark.parametrize(
    "runtime_limit,default_limit,window,output,expected",
    [
        (None, 8000, None, 1024, 8000),
        (6000, 8000, None, 1024, 6000),
        (6000, 8000, 8192, 2048, 6000),
        (8000, 16000, 8192, 2048, 6144),
        (None, 16000, 8192, 2048, 6144),
        (8000, 16000, 2048, 2048, None),
        (8000, 16000, 1024, 2048, None),
    ],
)
def test_fixed_model_window_subtracts_reserved_output_before_input(
    runtime_limit,
    default_limit,
    window,
    output,
    expected,
):
    snapshot = SimpleNamespace(
        config=SimpleNamespace(
            runtime=SimpleNamespace(max_input_tokens=runtime_limit),
            model=SimpleNamespace(generation=SimpleNamespace(max_output_tokens=output)),
        ),
        model_version=SimpleNamespace(
            defaults={} if window is None else {"context_window_tokens": window}
        ),
    )
    gateway = ModelBudgetGateway(
        None,
        snapshot=snapshot,
        identity=None,
        settings=Settings(agent_default_max_input_tokens=default_limit),
    )
    if expected is None:
        with pytest.raises(ContextBudgetExceeded):
            gateway._input_limit()
    else:
        assert gateway._input_limit() == expected
        assert gateway._input_limit() + output <= (window or expected + output)
