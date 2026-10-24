"""固定输入边界内的产品历史读取；固定模型与独立路由共用，不读取checkpoint或秘密。"""

from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from supportops_core.agent_runtime import AgentReferenceError
from supportops_core.auth import IdentityContext
from supportops_core.config import Settings
from supportops_core.context_services import ContextSnapshot, question_digest
from supportops_core.enums import MessageRole
from supportops_core.history_context_services import history_excerpt, history_source, readable_history_result
from supportops_core.models import AgentRun, Conversation, Message


@dataclass(frozen=True, slots=True)
class RunInputContext:
    product_messages: tuple[tuple[str, str], ...] = field(repr=False)
    product_message_ids: tuple[UUID, ...]
    snapshot: ContextSnapshot = field(repr=False)


async def load_run_input_context(session: AsyncSession, *, run: AgentRun, conversation: Conversation,
    input_message: Message, identity: IdentityContext, settings: Settings,
) -> RunInputContext:
    """调用者负责当前身份和固定引用；路由还须持Run锁，保持原窗口/锚点/摘录语义。"""
    if run.input_sequence is None:
        raise AgentReferenceError("Run 缺少固定输入边界")
    history_filters = [Message.tenant_id == run.tenant_id, Message.conversation_id == run.conversation_id,
                       Message.role.in_((MessageRole.USER, MessageRole.ASSISTANT)), Message.turn_sequence <= run.input_sequence]
    total_history = int(await session.scalar(select(func.count()).select_from(Message).where(*history_filters)) or 0)
    lower_sequence = max(1, run.input_sequence - settings.agent_context_history_turns)
    history = list((await session.scalars(select(Message).where(*history_filters,
        Message.turn_sequence >= lower_sequence)
        .order_by(Message.turn_sequence, Message.role.desc(), Message.created_at, Message.id)
        .limit(settings.agent_context_history_turns * 2 + 1))).all())
    if lower_sequence > 1:
        anchor = (await session.scalars(select(Message).where(*history_filters, Message.turn_sequence == 1)
            .order_by(Message.role.desc(), Message.created_at, Message.id).limit(2))).all()
        history = [*anchor, *history]
    selected_messages = []
    excerpts = []
    for message in history:
        allowed, projected = await readable_history_result(session, message=message, identity=identity, conversation_id=conversation.id)
        if not allowed:
            # 结构损坏、撤权及失效诊断/动作不能退回旧正文或摘要。
            continue
        selected_messages.append(message)
        if message.id != input_message.id:
            excerpts.append(history_excerpt(message, projected))
    if not any(message.id == input_message.id for message in selected_messages):
        raise AgentReferenceError("有界历史缺少当前输入，不能生成脱离当前问题的回答")
    excerpts = list({entry.source.message_id: entry for entry in [*excerpts[:2], *excerpts[-10:]]}.values())
    snapshot = ContextSnapshot(current_input_id=input_message.id, question_digest=question_digest(input_message.content),
        through_input_sequence=run.input_sequence, selected_message_ids=[message.id for message in selected_messages],
        omitted_message_count=max(0, total_history - len(selected_messages)),
        history_sources=[history_source(message) for message in selected_messages], history_excerpts=excerpts)
    return RunInputContext(product_messages=tuple((message.role.value, message.content) for message in selected_messages),
        product_message_ids=tuple(message.id for message in selected_messages), snapshot=snapshot)
