from __future__ import annotations

import os
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from langgraph.checkpoint.base import BaseCheckpointSaver, empty_checkpoint
from supportops_core.agent_config import SupportContext
from supportops_core.agent_runtime import checkpoint_saver, checkpoint_thread_id
from supportops_core.config import Settings


def _config(thread_id: str) -> dict[str, Any]:
    return {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}


def _context(*, tenant_id: UUID, conversation_id: UUID) -> SupportContext:
    return SupportContext(
        tenant_id=tenant_id,
        user_id=uuid4(),
        roles=("employee",),
        conversation_id=conversation_id,
        run_id=uuid4(),
        correlation_id="postgres-checkpoint-test",
    )


@pytest.mark.skipif(
    os.getenv("SUPPORTOPS_RUN_POSTGRES_TESTS") != "1",
    reason="设置 SUPPORTOPS_RUN_POSTGRES_TESTS=1 后执行真实 PostgreSQL 测试",
)
async def test_postgres_checkpoint_survives_reconnect_and_isolates_threads() -> None:
    settings = Settings()
    if not settings.database_url.startswith("postgresql"):
        pytest.skip("当前 SUPPORTOPS_DATABASE_URL 不是 PostgreSQL")

    tenant_id = uuid4()
    conversation_id = uuid4()
    primary = _context(tenant_id=tenant_id, conversation_id=conversation_id)
    another_tenant = _context(tenant_id=uuid4(), conversation_id=conversation_id)
    another_conversation = _context(tenant_id=tenant_id, conversation_id=uuid4())
    primary_config = _config(checkpoint_thread_id(primary))
    marker = f"checkpoint-{uuid4()}"

    async with checkpoint_saver(settings) as raw_saver:
        saver = cast(BaseCheckpointSaver[Any], raw_saver)
        checkpoint = empty_checkpoint()
        version = saver.get_next_version(None, None)
        checkpoint["channel_values"] = {"supportops_marker": marker}
        checkpoint["channel_versions"] = {"supportops_marker": version}
        checkpoint["updated_channels"] = ["supportops_marker"]
        saved_config = await saver.aput(
            primary_config,
            checkpoint,
            {"source": "input", "step": 0, "parents": {}},
            {"supportops_marker": version},
        )

    # 新建连接模拟 Worker 重启；同一 thread 恢复，其他租户/会话均不可见。
    async with checkpoint_saver(settings) as raw_saver:
        saver = cast(BaseCheckpointSaver[Any], raw_saver)
        recovered = await saver.aget_tuple(saved_config)
        cross_tenant = await saver.aget_tuple(_config(checkpoint_thread_id(another_tenant)))
        cross_conversation = await saver.aget_tuple(
            _config(checkpoint_thread_id(another_conversation))
        )

    assert recovered is not None
    assert recovered.checkpoint["channel_values"]["supportops_marker"] == marker
    assert cross_tenant is None
    assert cross_conversation is None
