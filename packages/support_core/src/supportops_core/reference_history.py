"""固定引用历史的ORM更新保护；不提供擦除许可或查询秘密内容。"""

from datetime import UTC, datetime

from sqlalchemy import inspect
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import NO_VALUE
from sqlalchemy.orm.state import AttributeState

from supportops_core.enums import KnowledgeVersionStatus
from supportops_core.models import (
    KnowledgeChunk,
    KnowledgeEvidence,
    KnowledgeIndexGeneration,
    KnowledgeSnapshot,
    KnowledgeVersion,
    ModelCredentialRevision,
)

_REVOCATION_FIELDS = frozenset({"revoked_at", "revoked_by", "revocation_reason"})
_CREDENTIAL_MUTABLE_FIELDS = _REVOCATION_FIELDS | {"deleted_at"}
_VERSION_MUTABLE_FIELDS = frozenset({
    "status", "effective_until", "deleted_at", "submitted_by", "submitted_at",
    "reviewed_by", "reviewed_at", "review_decision", "review_revision",
    "review_scope_digest", "review_comment", "published_by", "published_at",
})
_GENERATION_MUTABLE_FIELDS = frozenset({
    "status", "chunk_count", "error_code", "ready_at",
})


class ReferenceHistoryConflict(ValueError):
    """仅固定提示，不附带历史内容、字段值或凭据。"""


def _previous_value(attribute: AttributeState) -> object:
    """只用已加载旧值；不为保护检查发起延迟查询。"""
    history = attribute.history
    if history.has_changes():
        if len(history.deleted) == 1:
            return history.deleted[0]
    elif attribute.loaded_value is not NO_VALUE:
        return attribute.loaded_value
    raise ReferenceHistoryConflict("历史状态未加载，不能确认本次变更")


def _guard_knowledge_version(resource: KnowledgeVersion, changed: set[str]) -> None:
    if changed - _VERSION_MUTABLE_FIELDS:
        raise ReferenceHistoryConflict("知识版本原文与定位不可改写，请创建新版本")
    state = inspect(resource)
    if changed & {"published_at", "published_by"}:
        if (
            _previous_value(state.attrs.published_at) is not None
            or _previous_value(state.attrs.published_by) is not None
            or state.attrs.published_at.loaded_value in (None, NO_VALUE)
            or state.attrs.published_by.loaded_value in (None, NO_VALUE)
            or state.attrs.status.loaded_value != KnowledgeVersionStatus.PUBLISHED
        ):
            raise ReferenceHistoryConflict("知识版本发布事实须完整且只能记录一次")
    if "effective_until" in changed:
        previous = _previous_value(state.attrs.effective_until)
        current = state.attrs.effective_until.loaded_value
        published_at = _previous_value(state.attrs.published_at)
        if (
            not isinstance(current, datetime) or not isinstance(published_at, datetime)
            or state.attrs.status.loaded_value != KnowledgeVersionStatus.RETIRED
            or (previous is not None and not isinstance(previous, datetime))
        ):
            raise ReferenceHistoryConflict("知识有效期只能在已发布版本退役时缩短")
        if isinstance(previous, datetime) and (
            current.replace(tzinfo=current.tzinfo or UTC)
            > previous.replace(tzinfo=previous.tzinfo or UTC)
        ):
            raise ReferenceHistoryConflict("知识退役不能延长或清除原有效期")


def guard_reference_history_updates(session: Session) -> None:
    """检查已加载实体列变化；批量或原始SQL由0049/0050的PG触发器保护。"""
    for resource in session.dirty:
        if not isinstance(resource, (
            ModelCredentialRevision, KnowledgeEvidence, KnowledgeChunk,
            KnowledgeVersion, KnowledgeSnapshot, KnowledgeIndexGeneration,
        )):
            continue
        state = inspect(resource)
        changed = {
            column.key for column in state.mapper.column_attrs
            if state.attrs[column.key].history.has_changes()
        }
        if not changed:
            continue
        if isinstance(resource, KnowledgeVersion):
            _guard_knowledge_version(resource, changed)
        elif isinstance(resource, KnowledgeSnapshot):
            if changed - {"deleted_at"}:
                raise ReferenceHistoryConflict("知识发布快照不可改写，请创建新版本")
        elif isinstance(resource, KnowledgeIndexGeneration):
            if changed - _GENERATION_MUTABLE_FIELDS:
                raise ReferenceHistoryConflict("知识索引代次定位不可改写，请创建新代次")
        elif not isinstance(resource, ModelCredentialRevision):
            raise ReferenceHistoryConflict("知识证据及分块为追加历史，不能原地改写")
        elif changed - _CREDENTIAL_MUTABLE_FIELDS:
            raise ReferenceHistoryConflict("历史凭据内容与定位不可改写，请创建新修订")
        if "deleted_at" in changed:
            previous = state.attrs.deleted_at.history.deleted
            if (
                len(previous) != 1 or previous[0] is not None
                or state.attrs.deleted_at.loaded_value in (None, NO_VALUE)
            ):
                raise ReferenceHistoryConflict("历史软删除事实不能覆盖或撤回")
        if isinstance(resource, ModelCredentialRevision) and changed & _REVOCATION_FIELDS:
            previous = state.attrs.revoked_at.history.deleted
            if (
                len(previous) != 1 or previous[0] is not None
                or resource.revoked_at is None or resource.revoked_by is None
                or not isinstance(resource.revocation_reason, str)
                or not resource.revocation_reason.strip()
            ):
                raise ReferenceHistoryConflict("历史凭据撤销须完整且只能记录一次")
