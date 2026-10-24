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
    ToolDiscoveryRevision,
    ToolConnectionEvent,
    ToolConnectionProbeJob,
    HttpMcpDispatch,
    KnowledgeIngestionExecutionRequest,
    RetentionCleanupEvent,
    RetentionPropagationEvent,
    RestoreKnowledgeEvent,
    RestoreCredentialEvent,
    RestoreSessionEvent,
    ModelRoutingPolicyVersion,
    ModelRoutingPolicyRevocation,
    ModelRoutingCircuitEvent,
    SpecialistExecutionObservation,
    AgentVersion,
    DeletionTombstone,
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
    """检查已加载实体列变化；批量或原始SQL由对应PG历史保护迁移约束。"""
    for resource in session.new:
        if isinstance(resource, AgentVersion) and resource.schema_version == "3":
            from supportops_core.release_manifest_services import project_release_manifest
            if project_release_manifest(resource)[0] != "recorded" or resource.withdrawn_at is not None or resource.withdrawn_by is not None:
                raise ReferenceHistoryConflict("v3候选必须首次保存完整清单，不能补造历史或初始撤回")
    if any(isinstance(resource, AgentVersion) and resource.schema_version == "3" for resource in session.deleted):
        raise ReferenceHistoryConflict("v3候选版本历史不能删除")
    if any(isinstance(resource, (ToolDiscoveryRevision, ToolConnectionEvent, HttpMcpDispatch,
        RetentionCleanupEvent, RetentionPropagationEvent, RestoreKnowledgeEvent, RestoreCredentialEvent, RestoreSessionEvent,
        ModelRoutingPolicyVersion, ModelRoutingPolicyRevocation, ModelRoutingCircuitEvent, SpecialistExecutionObservation, DeletionTombstone)) for resource in session.deleted):
        raise ReferenceHistoryConflict("受保护的追加历史不能删除")
    for resource in session.dirty:
        if not isinstance(resource, (
            AgentVersion, ModelCredentialRevision, KnowledgeEvidence, KnowledgeChunk,
            KnowledgeVersion, KnowledgeSnapshot, KnowledgeIndexGeneration, ToolDiscoveryRevision, ToolConnectionEvent, ToolConnectionProbeJob, HttpMcpDispatch, KnowledgeIngestionExecutionRequest, RetentionCleanupEvent, RetentionPropagationEvent, RestoreKnowledgeEvent, RestoreCredentialEvent, RestoreSessionEvent, ModelRoutingPolicyVersion, ModelRoutingPolicyRevocation, ModelRoutingCircuitEvent, SpecialistExecutionObservation, DeletionTombstone,
        )):
            continue
        state = inspect(resource)
        changed = {
            column.key for column in state.mapper.column_attrs
            if state.attrs[column.key].history.has_changes()
        }
        if not changed:
            continue
        if isinstance(resource, AgentVersion):
            original_schema = _previous_value(state.attrs.schema_version)
            if original_schema != "3":
                if resource.schema_version == "3":
                    raise ReferenceHistoryConflict("v3必须保存新候选，不能改写旧版本")
                continue
            if changed - {"withdrawn_at", "withdrawn_by"}:
                raise ReferenceHistoryConflict("v3候选配置、清单和提交事实不可改写")
            if (changed & {"withdrawn_at", "withdrawn_by"} and (
                _previous_value(state.attrs.withdrawn_at) is not None
                or _previous_value(state.attrs.withdrawn_by) is not None
                or resource.withdrawn_at is None or resource.withdrawn_by is None
            )):
                raise ReferenceHistoryConflict("v3撤回事实必须完整且只能记录一次")
            continue
        if isinstance(resource, (ModelRoutingPolicyVersion, ModelRoutingPolicyRevocation, ModelRoutingCircuitEvent)):
            raise ReferenceHistoryConflict("路由策略版本和永久撤销事实不能改写")
        if isinstance(resource, SpecialistExecutionObservation):
            raise ReferenceHistoryConflict("子任务原完整执行回执和密封回答不能改写")
        if isinstance(resource, RestoreSessionEvent):
            raise ReferenceHistoryConflict("恢复登录会话审计历史不能改写")
        if isinstance(resource, RestoreCredentialEvent):
            raise ReferenceHistoryConflict("恢复凭据隔离审计是追加历史，不能原地改写")
        if isinstance(resource, RestoreKnowledgeEvent):
            raise ReferenceHistoryConflict("恢复知识隔离审计是追加历史，不能原地改写")
        if isinstance(resource, RetentionPropagationEvent):
            raise ReferenceHistoryConflict("删除传播核对审计是追加历史，不能原地改写")
        if isinstance(resource, (RetentionCleanupEvent, DeletionTombstone)):
            raise ReferenceHistoryConflict("清理批次与删除事实是追加历史，不能原地改写")
        if isinstance(resource, KnowledgeIngestionExecutionRequest):
            if changed - {"status", "revision", "claim_id", "claimed_at", "finished_at", "error_code"}:
                raise ReferenceHistoryConflict("人工导入执行意图固定范围不可改写")
            previous = _previous_value(state.attrs.status)
            transitions = {"queued": {"claimed", "rejected"}, "claimed": {"completed", "unconfirmed"}}
            if resource.status not in transitions.get(previous, set()):
                raise ReferenceHistoryConflict("人工导入执行意图不能重领或复活")
            if resource.revision != _previous_value(state.attrs.revision) + 1:
                raise ReferenceHistoryConflict("人工导入执行意图修订无效")
            if previous == "claimed" and changed & {"claim_id", "claimed_at"}:
                raise ReferenceHistoryConflict("人工导入领取事实不可替换")
            continue
        if isinstance(resource, HttpMcpDispatch):
            if changed - {"status", "revision", "claim_id", "claimed_at", "token_state", "token_state_digest", "lease_id", "revoked_at", "updated_at"}:
                raise ReferenceHistoryConflict("HTTP MCP派发固定范围不可改写")
            previous = _previous_value(state.attrs.status)
            allowed = {"authorized": {"claimed"}, "claimed": {"bound", "blocked"}, "bound": {"revoked"}}
            if not isinstance(previous, str) or resource.status not in allowed.get(previous, set()):
                raise ReferenceHistoryConflict("HTTP MCP派发不能重领或复活")
            revision = _previous_value(state.attrs.revision)
            updated_at = _previous_value(state.attrs.updated_at)
            if (type(revision) is not int or resource.revision != revision + 1
                or not isinstance(updated_at, datetime)
                or resource.updated_at.replace(tzinfo=resource.updated_at.tzinfo or UTC)
                    < updated_at.replace(tzinfo=updated_at.tzinfo or UTC)):
                raise ReferenceHistoryConflict("HTTP MCP派发修订或状态时间无效")
            if previous != "authorized" and changed & {"claim_id", "claimed_at"}:
                raise ReferenceHistoryConflict("HTTP MCP领取事实不可替换")
            if previous == "bound" and changed & {"token_state", "token_state_digest", "lease_id"}:
                raise ReferenceHistoryConflict("HTTP MCP令牌元数据不可替换")
            continue
        if isinstance(resource, ToolConnectionProbeJob):
            if changed - {"status", "revision", "generation", "owner_id", "lease_until", "result_digest", "error_code", "updated_at"}:
                raise ReferenceHistoryConflict("连接探测固定范围不可改写")
            continue
        if isinstance(resource, (ToolDiscoveryRevision, ToolConnectionEvent)):
            raise ReferenceHistoryConflict("工具发现修订为追加历史，不能原地改写")
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
