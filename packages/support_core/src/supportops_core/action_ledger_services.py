"""动作账本状态边界；未知结果不得被直接改成可重试或成功。"""

import hashlib
import json
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from supportops_core.action_contracts import ACTION_TRANSITIONS, ActionStatus, MockTicketParameters, frozen_parameters_digest
from supportops_core.models import ActionAttempt, ActionReceipt, BusinessAction, utc_now
from supportops_core.services import ResourceNotFoundError


class ActionStateConflict(ValueError):
    code = "ACTION_STATE_CONFLICT"


def business_logical_key(*, tenant_id: UUID, user_id: UUID, tool_id: str, resource_kind: str,
    resource_id: str, issue_key: str,
) -> str:
    """只使用业务意图和可信资源身份，不含 Run/attempt/模型调用 ID。"""
    return hashlib.sha256(json.dumps([str(tenant_id), str(user_id), tool_id, resource_kind, resource_id, issue_key],
        ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


async def lock_business_action(session: AsyncSession, *, action_id: UUID, tenant_id: UUID) -> BusinessAction:
    action = await session.scalar(select(BusinessAction).where(BusinessAction.id == action_id, BusinessAction.tenant_id == tenant_id)
        .execution_options(populate_existing=True).with_for_update())
    if action is None:
        raise ResourceNotFoundError("业务动作不存在")
    return action


async def transition_business_action(session: AsyncSession, *, action_id: UUID, tenant_id: UUID,
    expected_generation: int, target: ActionStatus, receipt_id: UUID | None = None,
) -> BusinessAction:
    """调用方仍需主体/策略与当前 owner 授权；此函数只负责不可绕过的状态事实。"""
    action = await lock_business_action(session, action_id=action_id, tenant_id=tenant_id)
    if action.generation != expected_generation or target not in ACTION_TRANSITIONS.get(action.status, frozenset()):
        raise ActionStateConflict("动作 generation 或状态已变化")
    if action.parameter_schema_version != "mock_ticket_v1":
        raise ActionStateConflict("动作参数 Schema 未获准，不能继续执行或对账")
    if frozen_parameters_digest(MockTicketParameters.model_validate(action.parameters)) != action.parameters_digest:
        raise ActionStateConflict("冻结参数不一致，不能继续执行或对账")
    if target == "queued" and action.approval_required:
        from supportops_core.models import ApprovalRequest
        approval = await session.scalar(select(ApprovalRequest).where(ApprovalRequest.action_id == action.id,
            ApprovalRequest.tenant_id == tenant_id, ApprovalRequest.status == "approved",
            ApprovalRequest.parameters_digest == action.parameters_digest, ApprovalRequest.expires_at > utc_now()))
        if action.status != "waiting_approval" or approval is None:
            raise ActionStateConflict("此动作缺少匹配的有效独立审批，不能排队执行")
    if target == "succeeded" or (action.status == "reconciling" and target in {"queued", "failed"}):
        proof = await session.scalar(select(ActionReceipt).where(ActionReceipt.id == receipt_id,
            ActionReceipt.tenant_id == tenant_id, ActionReceipt.action_id == action.id,
            ActionReceipt.attempt_id == action.current_attempt_id)) if receipt_id is not None else None
        attempt = await session.scalar(select(ActionAttempt).where(ActionAttempt.id == action.current_attempt_id,
            ActionAttempt.tenant_id == tenant_id, ActionAttempt.action_id == action.id, ActionAttempt.generation == action.generation))
        expected_proof = "verified" if target == "succeeded" else "not_executed"
        if (proof is None or attempt is None or proof.status != expected_proof or attempt.kind not in {"verify", "reconcile"}
            or attempt.parameters_digest != action.parameters_digest):
            raise ActionStateConflict("需要当前独立只读查询证明；未知结果不能直接重试或宣称成功")
        proof_digest = hashlib.sha256(json.dumps(proof.verified_fields, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        if (proof.result_digest != proof_digest or proof.verified_fields.get("logical_key") != action.logical_key
            or proof.verified_fields.get("parameters_digest") != action.parameters_digest
            or proof.verified_fields.get("status") != expected_proof):
            raise ActionStateConflict("复核回执摘要或冻结业务参数不一致")
        if target != "succeeded" and int(proof.verified_fields.get("execution_fence", -1)) < action.generation:
            raise ActionStateConflict("目标尚未封锁迟到执行请求，不能作为未执行证明")
        expires = attempt.lease_expires_at.replace(tzinfo=UTC) if attempt.lease_expires_at.tzinfo is None else attempt.lease_expires_at
        if attempt.status != "running" or expires <= datetime.now(UTC):
            raise ActionStateConflict("提供独立复核证明的尝试已经失效")
        if target == "succeeded":
            if proof.external_id is None:
                raise ActionStateConflict("业务成功缺少可信外部 ID")
            action.external_id = proof.external_id
    action.status = target
    if target in {"succeeded", "failed", "cancelled"}:
        action.finished_at = utc_now()
    await session.flush()
    return action
