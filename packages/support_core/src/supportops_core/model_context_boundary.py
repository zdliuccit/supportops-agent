"""登记本次实际模型输入使用的来源，保持跨裁剪/重试的依赖并集。"""

from langchain_core.messages import BaseMessage
from sqlalchemy.ext.asyncio import AsyncSession

from supportops_core.auth import IdentityContext
from supportops_core.config import Settings
from supportops_core.context_services import ContextSnapshot
from supportops_core.history_context_services import assert_history_context_authorized
from supportops_core.knowledge_retrieval import assert_attempt_knowledge_authorized
from supportops_core.run_attempt_services import OwnedRun, RunAttemptIdentity
from supportops_core.tool_context_services import (
    ToolContextChanged, assert_tool_context_authorized, capture_model_tool_sources,
)


async def record_model_input_context(session: AsyncSession, *, owned: OwnedRun,
    identity: IdentityContext, attempt: RunAttemptIdentity, settings: Settings,
    messages: list[BaseMessage], selected_source_ids: set[str],
    fallback_snapshot: ContextSnapshot | None = None,
) -> ContextSnapshot | None:
    """调用方持有原Run/attempt锁并提交；只保存来源元数据，不保存模型正文。"""
    snapshot = ContextSnapshot.model_validate(owned.attempt.context_snapshot) if owned.attempt.context_snapshot else fallback_snapshot
    tool_sources = await capture_model_tool_sources(session, messages=messages, run=owned.run, identity=identity)
    if tool_sources and snapshot is None:
        raise ToolContextChanged("当前模型调用缺少可持久化的工具来源上下文")
    if snapshot is not None:
        # 已用来源保留并集，不能通过后续裁剪绕过撤权。
        used_ids = set(snapshot.used_history_source_ids or [])
        if snapshot.history_sources and str(snapshot.current_input_id) not in selected_source_ids:
            used_ids.update(source.message_id for source in snapshot.history_sources)
        used_ids.update(source.message_id for source in snapshot.history_sources if str(source.message_id) in selected_source_ids)
        used_tools = {source.invocation_id: source for source in snapshot.used_tool_sources}
        for source in tool_sources:
            if source.invocation_id in used_tools and used_tools[source.invocation_id] != source:
                raise ToolContextChanged("同一工具调用的来源证明已变化")
            used_tools[source.invocation_id] = source
        if len(used_tools) > 1000:
            raise ToolContextChanged("模型使用的工具来源超过上限")
        snapshot = snapshot.model_copy(update={"used_history_source_ids": sorted(used_ids, key=str),
            "used_tool_sources": sorted(used_tools.values(), key=lambda source: str(source.invocation_id))})
        owned.attempt.context_snapshot = snapshot.model_dump(mode="json")
    await assert_history_context_authorized(session, snapshot=snapshot, identity=identity,
        conversation_id=owned.conversation.id, run=owned.run)
    await assert_attempt_knowledge_authorized(session, identity=identity, attempt=attempt)
    await assert_tool_context_authorized(session, snapshot=snapshot, identity=identity,
        run=owned.run, settings=settings)
    return snapshot
