"""真实 Agent 图与 PostgreSQL saver 的历史重建、长窗口及恢复隔离。"""

import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessageChunk, BaseMessage, HumanMessage
from langchain_core.outputs import ChatGenerationChunk, ChatResult
from sqlalchemy import select
from supportops_core import agent_runtime, worker_runtime
from supportops_core.agent_runtime import checkpoint_saver
from supportops_core.db import create_session_factory
from supportops_core.enums import MessageRole, RunStatus
from supportops_core.models import AgentRun, Conversation, Message, ModelCallLedger, RunAttempt
from supportops_core.run_attempt_services import claim_run_attempt
from supportops_core.services import content_digest

from tests.postgres.test_conversation_ordering import ApiPair, submit
from tests.postgres.test_conversation_ordering import apis as apis
from tests.postgres.test_run_fencing import save_checkpoint
from tests.postgres.test_run_fencing import workers as workers

pytestmark = pytest.mark.skipif(
    os.getenv("SUPPORTOPS_RUN_POSTGRES_TESTS") != "1",
    reason="需显式启用隔离 PostgreSQL 测试库",
)


def bind_recording_model(monkeypatch: pytest.MonkeyPatch) -> list[list[BaseMessage]]:
    requests: list[list[BaseMessage]] = []

    class RecordingModel(BaseChatModel):
        @property
        def _llm_type(self) -> str:
            return "isolated-history-model"

        def bind_tools(self, tools: Any, **kwargs: Any) -> Any:
            return self

        def _generate(self, messages: list[BaseMessage], **kwargs: Any) -> ChatResult:
            raise AssertionError("只允许隔离模型的流式接口")

        async def _astream(
            self,
            messages: list[BaseMessage],
            **kwargs: Any,
        ) -> AsyncIterator[ChatGenerationChunk]:
            requests.append(messages)
            yield ChatGenerationChunk(
                message=AIMessageChunk(
                    id=f"recording-model-{len(requests)}",
                    content="",
                    tool_call_chunks=[
                        {
                            "index": 0,
                            "name": "SupportAnswer",
                            "args": f'{{"answer":"隔离回答{len(requests)}"}}',
                            "id": f"answer-{len(requests)}",
                        }
                    ],
                )
            )

    async def model_adapter(*args: object, **kwargs: object) -> BaseChatModel:
        return RecordingModel()

    monkeypatch.setattr(agent_runtime.ModelAdapter, "build", model_adapter)
    # 保留正式 invoke_agent、AgentFactory、预算middleware及真实PostgreSQL saver。
    monkeypatch.setattr(worker_runtime, "invoke_agent", agent_runtime.invoke_agent)
    return requests


async def read_checkpoint(apis: ApiPair, attempt: RunAttempt) -> list[BaseMessage]:
    async with checkpoint_saver(apis.workers.settings) as saver:
        config = {"configurable": {"thread_id": attempt.checkpoint_thread_id}}
        if attempt.checkpoint_id is not None:
            config["configurable"]["checkpoint_id"] = attempt.checkpoint_id
        saved = await saver.aget_tuple(config)
        assert saved is not None
        return saved.checkpoint["channel_values"]["messages"]


async def test_next_turn_rebuilds_committed_products_without_future_or_duplicate_input(
    apis: ApiPair,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests = bind_recording_model(monkeypatch)
    responses = [
        await submit(apis, index, content=f"输入{index}", key=f"history-{index}")
        for index in range(3)
    ]
    assert all(response.status_code == 202 for response in responses)
    run_ids = [UUID(response.json()["run_id"]) for response in responses]
    message_ids = [response.json()["message_id"] for response in responses]
    factory = create_session_factory(apis.workers.engines[0])
    for index in range(2):
        assert await worker_runtime.execute_agent_run(
            factory, run_ids[index], settings=apis.workers.settings
        )
        assert [
            message.content for message in requests[index] if isinstance(message, HumanMessage)
        ] == (["输入0"] if index == 0 else ["输入0", "输入1"])
        ids = [message.id for message in requests[index] if isinstance(message, HumanMessage)]
        assert ids == message_ids[: index + 1] and len(ids) == len(set(ids))
        assert all("输入2" not in str(message.content) for message in requests[index])
        async with factory() as session:
            run = await session.get(AgentRun, run_ids[index])
            assert run is not None and run.status == RunStatus.COMPLETED
            attempt = await session.get(RunAttempt, run.current_attempt_id)
            assert attempt is not None
        graph_messages = await read_checkpoint(apis, attempt)
        assert [
            message.id for message in graph_messages if isinstance(message, HumanMessage)
        ] == ids
        assert not await worker_runtime.execute_agent_run(
            factory, run_ids[index], settings=apis.workers.settings
        )
    assert len(requests) == 2
    assert any(message.type == "ai" and message.content == "隔离回答1" for message in requests[1])
    async with factory() as session:
        future = await session.get(AgentRun, run_ids[2])
        conversation = await session.get(Conversation, apis.conversation_id)
        assert future is not None and future.status == RunStatus.QUEUED
        assert conversation is not None and conversation.checkpoint_input_sequence == 2
        assert conversation.active_run_id is None


async def test_product_failure_recovers_into_new_graph_without_uncommitted_answer(
    apis: ApiPair,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests = bind_recording_model(monkeypatch)
    response = await submit(apis, 0, content="必须只输入一次", key="recovery-current")
    future = await submit(apis, 1, content="未来输入禁止读取", key="recovery-future")
    assert response.status_code == future.status_code == 202
    run_id = UUID(response.json()["run_id"])
    factory = create_session_factory(apis.workers.engines[0])
    persist = worker_runtime.persist_support_message

    async def fail_product(*args, **kwargs):
        await persist(*args, **kwargs)
        raise OSError("isolated failure after graph save and product flush")

    monkeypatch.setattr(worker_runtime, "persist_support_message", fail_product)
    assert not await worker_runtime.execute_agent_run(
        factory, run_id, settings=apis.workers.settings
    )
    async with factory() as session:
        run = await session.get(AgentRun, run_id)
        assert run is not None and run.status == RunStatus.QUEUED
        old = await session.scalar(select(RunAttempt).where(RunAttempt.run_id == run_id))
        assert old is not None and old.status == "quarantined"
        assert old.checkpoint_id is None
        conversation = await session.get(Conversation, apis.conversation_id)
        assert conversation is not None and conversation.checkpoint_id is None
    assert any("隔离回答1" in str(message) for message in await read_checkpoint(apis, old))
    monkeypatch.setattr(worker_runtime, "persist_support_message", persist)
    assert await worker_runtime.execute_agent_run(
        create_session_factory(apis.workers.engines[1]),
        run_id,
        settings=apis.workers.settings,
    )
    assert len(requests) == 2
    for request in requests:
        humans = [message for message in request if isinstance(message, HumanMessage)]
        assert len(humans) == 1 and humans[0].id == response.json()["message_id"]
        assert humans[0].content == "必须只输入一次"
        assert not any(
            "隔离回答1" in str(message.content) or "未来输入" in str(message.content)
            for message in request
        )
    async with factory() as session:
        run = await session.get(AgentRun, run_id)
        assert run is not None and run.status == RunStatus.COMPLETED
        new = await session.get(RunAttempt, run.current_attempt_id)
        assert new is not None and new.id != old.id
        assert new.checkpoint_thread_id != old.checkpoint_thread_id
        messages = (
            await session.scalars(
                select(Message).where(
                    Message.conversation_id == apis.conversation_id,
                    Message.role == MessageRole.ASSISTANT,
                )
            )
        ).all()
        assert len(messages) == 1 and messages[0].content == "隔离回答2"
    assert not any("隔离回答1" in str(message) for message in await read_checkpoint(apis, new))


async def test_existing_uncommitted_attempt_checkpoint_is_rejected_before_model_request(
    apis: ApiPair,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests = bind_recording_model(monkeypatch)
    response = await submit(apis, 0, content="不复用旧图", key="existing-graph")
    assert response.status_code == 202
    run_id = UUID(response.json()["run_id"])
    factory = create_session_factory(apis.workers.engines[0])
    async with factory() as session, session.begin():
        claim = await claim_run_attempt(session, run_id, owner_id="same-attempt", lease_seconds=90)
        assert claim is not None
    await save_checkpoint(apis.workers.settings, claim.attempt.checkpoint_thread_id, "uncommitted")
    assert not await worker_runtime.execute_agent_run(
        factory,
        run_id,
        settings=apis.workers.settings,
        owner_id=claim.identity.owner_id,
        preclaimed=claim,
    )
    assert requests == []
    async with factory() as session:
        run = await session.get(AgentRun, run_id)
        assert run is not None and run.status == RunStatus.QUEUED
        assert run.recovery_reason == "AGENT_CHECKPOINT_FAILED" and run.recovery_count == 1
        conversation = await session.get(Conversation, apis.conversation_id)
        assert conversation is not None and conversation.checkpoint_id is None
    assert await worker_runtime.execute_agent_run(factory, run_id, settings=apis.workers.settings)
    assert len(requests) == 1
    assert [message.id for message in requests[0] if isinstance(message, HumanMessage)] == [
        response.json()["message_id"]
    ]


@pytest.mark.parametrize("window,input_limit", [(3, 100_000), (30, 100_000), (30, 20_000)])
async def test_long_history_keeps_anchor_recent_order_and_current_input_once(
    apis: ApiPair,
    monkeypatch: pytest.MonkeyPatch,
    window: int,
    input_limit: int,
) -> None:
    """预置75轮旧产品历史，逆序插入/同时间戳，使用真实图检查实际模型输入。"""
    requests = bind_recording_model(monkeypatch)
    factory = create_session_factory(apis.workers.engines[0])
    fixed_time = datetime(2026, 10, 1, tzinfo=UTC)
    rows: dict[tuple[int, MessageRole], Message] = {}
    async with factory() as session, session.begin():
        conversation = await session.get(Conversation, apis.conversation_id)
        assert conversation is not None
        for turn in range(75, 0, -1):
            for role in (MessageRole.ASSISTANT, MessageRole.USER):
                content = f"旧{role.value}-{turn}" + "历史内容" * 30
                message = Message(
                    id=uuid4(),
                    tenant_id=conversation.tenant_id,
                    user_id=conversation.user_id,
                    conversation_id=conversation.id,
                    role=role,
                    turn_sequence=turn,
                    content=content,
                    content_hash=content_digest(content),
                    idempotency_key=f"legacy-{turn}-{role.value}",
                    correlation_id="legacy-history",
                    created_at=fixed_time,
                )
                rows[(turn, role)] = message
                session.add(message)
        conversation.next_input_sequence = 76
    response = await submit(apis, 0, content="第76轮当前问题", key="long-current")
    future = await submit(apis, 1, content="第77轮未来问题", key="long-future")
    assert response.status_code == future.status_code == 202
    run_id = UUID(response.json()["run_id"])
    settings = apis.workers.settings.model_copy(
        update={
            "agent_context_history_turns": window,
            "agent_default_max_input_tokens": input_limit,
        }
    )
    assert await worker_runtime.execute_agent_run(factory, run_id, settings=settings)
    assert len(requests) == 1
    expected_ids = [str(rows[(1, role)].id) for role in (MessageRole.USER, MessageRole.ASSISTANT)]
    for turn in range(76 - window, 76):
        expected_ids.extend(
            str(rows[(turn, role)].id) for role in (MessageRole.USER, MessageRole.ASSISTANT)
        )
    expected_ids.append(response.json()["message_id"])
    actual = [message for message in requests[0] if message.type != "system"]
    if input_limit == 100_000:
        assert [message.id for message in actual] == expected_ids
    else:
        selected_ids = [
            message.id for message in actual if message.id != "supportops-history-excerpts-v1"
        ]
        assert len(selected_ids) < len(expected_ids)
        assert selected_ids == [
            message_id for message_id in expected_ids if message_id in selected_ids
        ]
        assert selected_ids.count(response.json()["message_id"]) == 1
        assert selected_ids[-1] == response.json()["message_id"]
        assert all(
            message.id == response.json()["message_id"] or "第76轮" not in str(message.content)
            for message in actual
        )
    assert len(expected_ids) == len(set(expected_ids)) == 2 * window + 3
    assert all("第77轮" not in str(message.content) for message in requests[0])
    async with factory() as session:
        run = await session.get(AgentRun, run_id)
        assert run is not None and run.status == RunStatus.COMPLETED
        attempt = await session.get(RunAttempt, run.current_attempt_id)
        assert attempt is not None and attempt.context_snapshot is not None
        context = attempt.context_snapshot
        assert context["through_input_sequence"] == 76
        assert context["selected_message_ids"] == expected_ids
        assert context["omitted_message_count"] == 151 - len(expected_ids)
        assert all(source["input_sequence"] <= 76 for source in context["history_sources"])
        assert all(
            entry["source"]["message_id"] != response.json()["message_id"]
            for entry in context["history_excerpts"]
        )
        ledger = await session.scalar(
            select(ModelCallLedger).where(ModelCallLedger.run_id == run_id)
        )
        assert ledger is not None and ledger.estimated_input_tokens <= input_limit
        assert (ledger.omitted_message_count > 0) is (input_limit < 100_000)
        assert set(context["used_history_source_ids"]) <= set(expected_ids)
        assert response.json()["message_id"] in context["used_history_source_ids"]
    graph_messages = await read_checkpoint(apis, attempt)
    user_ids = {str(message.id) for message in rows.values() if message.role == MessageRole.USER}
    user_ids.add(response.json()["message_id"])
    assert [message.id for message in graph_messages if isinstance(message, HumanMessage)] == [
        message_id for message_id in expected_ids if message_id in user_ids
    ]
