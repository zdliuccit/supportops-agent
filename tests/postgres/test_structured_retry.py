"""真实 Agent 图结构校验重试、产品提交及 PG 事件恢复契约。"""

import json
import os
from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessageChunk, BaseMessage, ToolMessage
from langchain_core.outputs import ChatGenerationChunk, ChatResult
from sqlalchemy import select
from supportops_core import agent_runtime, worker_runtime
from supportops_core.db import create_session_factory
from supportops_core.enums import MessageRole, RunStatus
from supportops_core.models import AgentRun, Message, ModelCallLedger, RunEvent

from tests.postgres.test_conversation_ordering import ApiPair, submit
from tests.postgres.test_conversation_ordering import apis as apis
from tests.postgres.test_run_fencing import workers as workers

pytestmark = pytest.mark.skipif(
    os.getenv("SUPPORTOPS_RUN_POSTGRES_TESTS") != "1",
    reason="需显式启用隔离 PostgreSQL 测试库",
)


async def test_real_schema_retry_replaces_draft_and_commits_only_valid_result(
    apis: ApiPair,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[BaseMessage]] = []

    class SchemaRetryModel(BaseChatModel):
        @property
        def _llm_type(self) -> str:
            return "isolated-schema-retry"

        def bind_tools(self, tools: Any, **kwargs: Any) -> Any:
            return self

        def _generate(self, messages: list[BaseMessage], **kwargs: Any) -> ChatResult:
            raise AssertionError("测试只允许本地流式模型")

        async def _astream(
            self, messages: list[BaseMessage], **kwargs: Any
        ) -> AsyncIterator[ChatGenerationChunk]:
            calls.append(messages)
            assert len(calls) <= 2, "结构校验重试必须收敛"
            value = (
                {"answer": "不可提交的初稿", "unapproved_field": True}
                if len(calls) == 1
                else {
                    "answer": "通过校验的修正回答🙂",
                    "summary": "有效摘要",
                    "suggested_actions": ["建议联系支持人员"],
                    "escalation_recommended": True,
                }
            )
            yield ChatGenerationChunk(
                message=AIMessageChunk(
                    id=f"schema-attempt-{len(calls)}",
                    content="",
                    tool_call_chunks=[
                        {
                            "index": 0,
                            "name": "SupportAnswer",
                            "args": json.dumps(value, ensure_ascii=False),
                            "id": f"answer-{len(calls)}",
                        }
                    ],
                )
            )

    async def model_adapter(*args: object, **kwargs: object) -> BaseChatModel:
        return SchemaRetryModel()

    monkeypatch.setattr(agent_runtime.ModelAdapter, "build", model_adapter)
    monkeypatch.setattr(worker_runtime, "invoke_agent", agent_runtime.invoke_agent)
    accepted = await submit(apis, 0, content="结构校验重试", key="schema-retry")
    assert accepted.status_code == 202
    run_id = UUID(accepted.json()["run_id"])
    factory = create_session_factory(apis.workers.engines[0])
    assert await worker_runtime.execute_agent_run(factory, run_id, settings=apis.workers.settings)
    assert len(calls) == 2
    assert any(
        isinstance(message, ToolMessage) and message.name == "SupportAnswer" for message in calls[1]
    )
    async with factory() as session:
        run = await session.get(AgentRun, run_id)
        assert run is not None and run.status == RunStatus.COMPLETED
        messages = (
            await session.scalars(
                select(Message).where(
                    Message.conversation_id == run.conversation_id,
                    Message.role == MessageRole.ASSISTANT,
                )
            )
        ).all()
        assert len(messages) == 1 and messages[0].content == "通过校验的修正回答🙂"
        result = messages[0].support_result
        assert result["summary"] == "有效摘要"
        assert result["suggested_actions"] == [
            {"description": "建议联系支持人员", "status": "proposed"}
        ]
        assert result["escalation_status"] == "recommended" and result["business_actions"] == []
        assert "unapproved_field" not in result
        ledger = (
            await session.scalars(select(ModelCallLedger).where(ModelCallLedger.run_id == run_id))
        ).all()
        assert len(ledger) == 2 and run.model_call_count == 2
        events = (
            await session.scalars(
                select(RunEvent).where(RunEvent.run_id == run_id).order_by(RunEvent.sequence)
            )
        ).all()
        assert sum(event.event_type == "result.ready" for event in events) == 1
        assert sum(event.event_type == "run.completed" for event in events) == 1
        assert any(
            event.event_type == "text.replace" and event.data.get("content") == result["answer"]
            for event in events
        )
    refreshed = await apis.clients[1].get(
        f"/v1/conversations/{apis.conversation_id}", headers=apis.headers
    )
    assert refreshed.status_code == 200
    assert refreshed.json()["messages"][-1]["support_result"] == result
    assert refreshed.json()["latest_run"]["content"] == result["answer"]
