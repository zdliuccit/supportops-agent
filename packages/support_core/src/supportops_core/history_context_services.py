"""历史正文/摘要的当前授权与来源证明；模型摘要不成为已确认事实。"""
from __future__ import annotations

import hashlib
import json
from uuid import UUID
from typing import TYPE_CHECKING
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from supportops_core.auth import IdentityContext
from supportops_core.context_services import ContextSnapshot, HistoryExcerpt, HistorySource
from supportops_core.enums import MessageRole
from supportops_core.knowledge_retrieval import KnowledgeEvidenceError, lock_evidence_documents
from supportops_core.models import AgentRun, Message
from supportops_core.redaction import redact_sensitive_text
if TYPE_CHECKING:
    from supportops_core.support_result_services import SupportResult


class HistoryAuthorizationChanged(KnowledgeEvidenceError):
    code = "HISTORY_CONTEXT_AUTHORIZATION_CHANGED"


def history_source(message: Message) -> HistorySource:
    if message.turn_sequence is None or message.role not in {MessageRole.USER, MessageRole.ASSISTANT}:
        raise HistoryAuthorizationChanged("历史来源缺少确定输入边界")
    digest = hashlib.sha256(json.dumps({"role": message.role.value, "content": message.content,
        "input_sequence": message.turn_sequence, "support_result": message.support_result},
        ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    return HistorySource(message_id=message.id, role="user" if message.role == MessageRole.USER else "assistant",
        input_sequence=message.turn_sequence, source_digest=digest)


async def readable_history_result(session: AsyncSession, *, message: Message, identity: IdentityContext,
    conversation_id: UUID,
) -> tuple[bool, SupportResult | None]:
    if message.role != MessageRole.ASSISTANT or message.support_result is None:
        return True, None
    try:
        from supportops_core.support_result_services import SupportResult, project_support_result
        result = await project_support_result(session, stored=message.support_result, identity=identity)
        if result is None or any(citation.status != "available" for citation in result.citations):
            return False, None
        source_run = await session.scalar(select(AgentRun).where(AgentRun.id == result.run_id,
            AgentRun.tenant_id == identity.principal.tenant_id, AgentRun.conversation_id == conversation_id,
            AgentRun.input_sequence == message.turn_sequence))
        if source_run is None or result.attempt_id != source_run.current_attempt_id:
            return False, None
        if result.diagnostic is not None and result.diagnostic.status == "unavailable":
            return False, None
        if result.diagnostic_data_environment == "mock" and result.diagnostic is None and not result.business_actions:
            # 旧自由工具回答没有可复检的诊断/动作来源，不把模拟事实固化进后续历史摘要。
            return False, None
        if any(action.availability != "available" for action in result.business_actions):
            return False, None
        return True, result
    except ValueError:
        # 历史结构损坏也不能退回未校验的旧正文。
        return False, None


def history_excerpt(message: Message, result: SupportResult | None) -> HistoryExcerpt:
    source = history_source(message)
    text = result.summary if result is not None and result.summary else message.content
    limit = 400 if message.role == MessageRole.USER else 1000
    cleaned = redact_sensitive_text(text)
    return HistoryExcerpt(source=source, excerpt=cleaned[:limit],
        source_kind="user_excerpt" if message.role == MessageRole.USER else "answer_summary" if result is not None and result.summary else "answer_excerpt",
        is_truncated=len(cleaned) > limit, evidence_ids=[citation.evidence_id for citation in result.citations[:6]] if result else [],
        unresolved=result is not None and result.escalation_status == "recommended",
        has_conflicts=bool(result and result.evidence_conflicts))


async def assert_history_context_authorized(session: AsyncSession, *, snapshot: ContextSnapshot | None,
    identity: IdentityContext, conversation_id: UUID, run: AgentRun,
    extra_evidence_ids: list[UUID] | None = None,
) -> None:
    if snapshot is None:
        return
    if (snapshot.current_input_id != run.input_message_id or snapshot.through_input_sequence != run.input_sequence
        or run.tenant_id != identity.principal.tenant_id or run.conversation_id != conversation_id):
        raise HistoryAuthorizationChanged("历史上下文与本轮固定输入边界不一致")
    if not snapshot.history_sources:
        return
    known_ids = {source.message_id for source in snapshot.history_sources}
    if (snapshot.current_input_id not in known_ids or len(known_ids) != len(snapshot.history_sources)
        or any(source.input_sequence > snapshot.through_input_sequence for source in snapshot.history_sources)
        or snapshot.used_history_source_ids is not None and not set(snapshot.used_history_source_ids) <= known_ids):
        raise HistoryAuthorizationChanged("历史来源证明无效或包含未来输入")
    used_ids = set(snapshot.used_history_source_ids) if snapshot.used_history_source_ids is not None else None
    sources = [source for source in snapshot.history_sources if used_ids is None or source.message_id in used_ids]
    if not sources:
        return
    source_ids = [source.message_id for source in sources]
    messages = {message.id: message for message in (await session.scalars(select(Message).where(
        Message.id.in_(source_ids), Message.tenant_id == identity.principal.tenant_id,
        Message.conversation_id == conversation_id, Message.user_id == identity.user.id,
        Message.turn_sequence <= snapshot.through_input_sequence))).all()}
    from supportops_core.support_result_services import SupportResult
    evidence_ids = [*list(extra_evidence_ids or []), *(reference.evidence_id for reference in snapshot.evidence)]
    for message in messages.values():
        if message.support_result is not None:
            try:
                stored = SupportResult.model_validate(message.support_result)
                evidence_ids.extend(citation.evidence_id for citation in stored.citations)
            except ValueError as exc:
                raise HistoryAuthorizationChanged("历史结构已变化，不能继续复用") from exc
    # 历史和本轮证据统一按文档排序加锁，不能按消息顺序形成相反锁序。
    try:
        await lock_evidence_documents(session, evidence_ids=list(dict.fromkeys(evidence_ids)), identity=identity)
    except KnowledgeEvidenceError as exc:
        raise HistoryAuthorizationChanged("历史证据来源已失效，不能继续复用") from exc
    for source in sources:
        message = messages.get(source.message_id)
        if message is None or history_source(message) != source:
            raise HistoryAuthorizationChanged("本轮历史来源已变化，不能继续复用旧上下文")
        allowed, _ = await readable_history_result(session, message=message, identity=identity, conversation_id=conversation_id)
        if not allowed:
            raise HistoryAuthorizationChanged("历史摘要依赖的证据、诊断或动作范围已失效，请重新获取当前证据")
