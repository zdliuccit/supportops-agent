import asyncio
import json
import socket
from collections.abc import Awaitable, Callable
from typing import cast
from uuid import uuid4

import pytest
import uvicorn
from httpx import AsyncClient
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine
from supportops_api.main import create_app
from supportops_core import worker_runtime
from supportops_core.agent_config import SupportAnswer
from supportops_core.config import Settings
from supportops_core.db import create_session_factory
from supportops_core.enums import RunStatus
from supportops_core.models import AgentRun, RunEvent

from tests.api.test_runs import create_run
from tests.conftest import FakeRedis, auth_headers


@pytest.mark.parametrize("outcome", ["completed", "failed", "cancelled"])
async def test_durable_stream_snapshot_resume_and_terminal_boundaries(
    client: AsyncClient,
    settings: Settings,
    engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
    outcome: str,
) -> None:
    headers, run_id = await create_run(client, settings, engine)
    factory = create_session_factory(engine)
    release = asyncio.Event()
    invoked = asyncio.Event()

    async def generate(*args: object, **kwargs: object) -> SupportAnswer:
        callback = cast(Callable[[str], Awaitable[None]], kwargs["on_answer"])
        await callback("初始正文🙂")
        invoked.set()
        await release.wait()
        if outcome == "failed":
            raise RuntimeError("供应商中断")
        # 校验重试改写正文，后续事件必须采用替换语义。
        await callback("修正后的正文🙂与后续内容")
        return SupportAnswer(answer="修正后的正文🙂与后续内容")

    monkeypatch.setattr(worker_runtime, "invoke_agent", generate)
    task = asyncio.create_task(worker_runtime.execute_agent_run(factory, run_id, settings=settings))
    try:
        await asyncio.wait_for(invoked.wait(), timeout=2)
        async with asyncio.timeout(3):
            while True:
                async with factory() as session:
                    run = await session.get(AgentRun, run_id)
                    event = await session.scalar(
                        select(RunEvent).where(
                            RunEvent.run_id == run_id,
                            RunEvent.event_type == "text.delta",
                        )
                    )
                if event is not None:
                    break
                await asyncio.sleep(0.01)
        assert run is not None and run.status == RunStatus.RUNNING
        assert not task.done(), "正文必须在模型执行完成之前持久化"
        conversation_id = run.conversation_id
        response = await client.get(f"/v1/conversations/{conversation_id}", headers=headers)
        state = response.json()["latest_run"]
        assert state == {
            "id": str(run_id),
            "input_message_id": str(run.input_message_id),
            "status": "running",
            "last_event_id": event.sequence,
            "content": "初始正文🙂",
            "input_sequence": run.input_sequence,
            "current_attempt_id": str(run.current_attempt_id),
            "execution_generation": run.execution_generation,
            "recovery_count": 0,
            "recovery_reason": None,
            "error_code": None,
            "error_message": None,
            "diagnostic_stages": [],
        }
        assert len(response.json()["messages"]) == 1
        # 详情和正文续传共享原有租户/用户边界。
        foreign = await auth_headers(engine, settings, uuid4())
        assert (
            await client.get(f"/v1/conversations/{conversation_id}", headers=foreign)
        ).status_code == 404
        assert (await client.get(f"/v1/runs/{run_id}/events", headers=foreign)).status_code == 404
        if outcome == "cancelled":
            assert (
                await client.post(f"/v1/runs/{run_id}/cancel", headers=headers)
            ).status_code == 200
        release.set()
        assert await asyncio.wait_for(task, timeout=5) is (outcome == "completed")
        final = await client.get(f"/v1/conversations/{conversation_id}", headers=headers)
        final_state = final.json()["latest_run"]
        assert final_state["status"] == outcome
        expected = "修正后的正文🙂与后续内容" if outcome == "completed" else "初始正文🙂"
        assert final_state["content"] == expected
        replay = await client.get(
            f"/v1/runs/{run_id}/events",
            headers={**headers, "Last-Event-ID": str(state["last_event_id"])},
        )
        assert "初始正文" not in replay.text
        assert f"event: run.{outcome}" in replay.text
        ids = [int(line[4:]) for line in replay.text.splitlines() if line.startswith("id: ")]
        assert all(sequence > state["last_event_id"] for sequence in ids)
        assert ids[-1] == final_state["last_event_id"]
        if outcome == "completed":
            assert "event: text.replace" in replay.text
            assert final.json()["messages"][-1]["content"] == expected
        else:
            assert "修正后的正文" not in replay.text
            assert len(final.json()["messages"]) == 1
    finally:
        release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_sse_delivers_over_http_before_generation_finishes(
    client: AsyncClient,
    settings: Settings,
    engine: AsyncEngine,
    fake_redis: FakeRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """真实 HTTP 连接必须及时收到正文，不能等模型结束后整份返回。"""
    headers, run_id = await create_run(client, settings, engine)
    factory = create_session_factory(engine)
    continue_generation = asyncio.Event()
    finish_generation = asyncio.Event()

    async def generate(*args: object, **kwargs: object) -> SupportAnswer:
        callback = cast(Callable[[str], Awaitable[None]], kwargs["on_answer"])
        await callback("第一段")
        await continue_generation.wait()
        await callback("第一段第二段")
        await finish_generation.wait()
        return SupportAnswer(answer="第一段第二段")

    monkeypatch.setattr(worker_runtime, "invoke_agent", generate)
    app = create_app(settings, engine=engine, redis=cast(Redis, fake_redis))
    server = uvicorn.Server(
        uvicorn.Config(app, lifespan="off", log_level="error", timeout_graceful_shutdown=2)
    )
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        listener.listen()
        listener.setblocking(False)
        async with app.router.lifespan_context(app):
            serving = asyncio.create_task(server.serve(sockets=[listener]))
            execution = None
            try:
                async with asyncio.timeout(3):
                    while not server.started:
                        if serving.done():
                            serving.result()
                        await asyncio.sleep(0.01)
                execution = asyncio.create_task(
                    worker_runtime.execute_agent_run(factory, run_id, settings=settings)
                )
                async with (
                    AsyncClient(base_url=f"http://127.0.0.1:{port}", headers=headers) as http,
                    asyncio.timeout(5),
                    http.stream("GET", f"/v1/runs/{run_id}/events") as response,
                ):
                    assert response.status_code == 200
                    assert response.headers["content-type"].startswith("text/event-stream")
                    assert "content-length" not in response.headers
                    event_type = ""
                    deltas = []
                    async for line in response.aiter_lines():
                        if line.startswith("event: "):
                            event_type = line.removeprefix("event: ")
                        elif line.startswith("data: ") and event_type == "text.delta":
                            deltas.append(json.loads(line.removeprefix("data: "))["delta"])
                            assert not execution.done(), "收到正文时模型仍应等待继续生成"
                            state = await http.get(f"/v1/runs/{run_id}")
                            assert state.json()["status"] == "running"
                            if len(deltas) == 1:
                                assert deltas == ["第一段"]
                                continue_generation.set()
                            else:
                                assert deltas == ["第一段", "第二段"]
                                finish_generation.set()
                        elif event_type == "run.completed":
                            break
                    assert deltas == ["第一段", "第二段"]
                assert await execution is True
            finally:
                continue_generation.set()
                finish_generation.set()
                if execution is not None:
                    if not execution.done():
                        execution.cancel()
                    await asyncio.gather(execution, return_exceptions=True)
                server.should_exit = True
                await asyncio.wait_for(serving, timeout=5)
