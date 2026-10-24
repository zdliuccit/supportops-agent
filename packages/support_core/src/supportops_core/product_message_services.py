"""调用者产品事务中的助手消息、最终正文与结果事件保存。"""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from supportops_core.agent_runtime import AgentReferenceError
from supportops_core.enums import MessageRole
from supportops_core.models import Message
from supportops_core.run_attempt_services import OwnedRun
from supportops_core.services import append_event, content_digest
from supportops_core.support_result_services import SupportResult


async def persist_support_message(
    session: AsyncSession, *, owned: OwnedRun, result: SupportResult,
    published_answer: str | None, message_id: UUID | None = None,
) -> Message:
    """调用者持当前Run锁并负责授权/提交；None表示完整缓存路径必须发送最终replace。"""
    result = SupportResult.model_validate_json(result.model_dump_json())
    run, attempt, conversation = owned.run, owned.attempt, owned.conversation
    original = await session.get(Message, run.input_message_id)
    if (original is None or original.tenant_id != run.tenant_id
        or original.conversation_id != conversation.id or original.user_id != conversation.user_id
        or original.role != MessageRole.USER or original.turn_sequence != run.input_sequence
        or result.run_id != run.id or result.attempt_id != attempt.id):
        raise AgentReferenceError("Run 输入消息或产品结果边界无效")
    message = Message(
        tenant_id=run.tenant_id, conversation_id=run.conversation_id, user_id=original.user_id,
        role=MessageRole.ASSISTANT, turn_sequence=run.input_sequence,
        content=result.answer, content_hash=content_digest(result.answer),
        idempotency_key=f"run:{run.id}:assistant", correlation_id=run.correlation_id,
        support_result=result.model_dump(mode="json"),
    )
    if message_id is not None:
        message.id = message_id
    session.add(message)
    await session.flush()
    if published_answer is None or result.answer != published_answer:
        await append_event(session, run_id=run.id, event_type="text.replace", attempt_id=attempt.id,
            data={"content": result.answer})
    await append_event(session, run_id=run.id, event_type="result.ready", attempt_id=attempt.id,
        data={"message_id": str(message.id), "schema_version": "1"})
    return message
