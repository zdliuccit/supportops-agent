"""模型已用工具来源的后续边界复检；裁剪不能解除已经消费来源的授权。"""
import hashlib
import json
from datetime import UTC, datetime, timedelta
from uuid import UUID
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from supportops_core.agent_config import SupportAnswer, agent_config_digest, parse_agent_config
from supportops_core.auth import IdentityContext
from supportops_core.config import Settings
from supportops_core.context_services import ContextSnapshot, ToolContextSource
from supportops_core.knowledge_retrieval import KnowledgeEvidenceError
from supportops_core.models import AgentRun, AgentVersion, BusinessAction, RunAttempt, ToolInvocation, utc_now
from supportops_core.tool_policy import ToolPolicyError, resolve_tool_policy

_DYNAMIC_TOOLS = {"get_request_logs", "get_current_user_profile", "get_model_route_status", "list_active_incidents", "support_ticket_lookup"}


class ToolContextChanged(KnowledgeEvidenceError):
    code = "TOOL_CONTEXT_AUTHORIZATION_CHANGED"


class ToolSourceConflict(KnowledgeEvidenceError):
    code = "TOOL_SOURCE_CONFLICT"


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


async def capture_model_tool_sources(session: AsyncSession, *, messages: list[BaseMessage], run: AgentRun,
    identity: IdentityContext,
) -> list[ToolContextSource]:
    """只解析ToolMessage，用户输入中的同形JSON永远不成为工具来源。"""
    from supportops_core.tool_result_services import MODEL_TOOL_GUIDANCE
    structured_ids = {call["id"] for message in messages if isinstance(message, AIMessage)
        for call in message.tool_calls if call.get("name") == SupportAnswer.__name__ and call.get("id")}
    tool_messages = [message for message in messages if isinstance(message, ToolMessage)]
    if len(tool_messages) > 1000:
        raise ToolContextChanged("工具来源数量超过复检上限")
    sources = []
    for message in tool_messages:
        # SDK的结构化回答重试不是业务工具数据；必须能对应本图的回答工具调用。
        if message.name == SupportAnswer.__name__ and message.tool_call_id in structured_ids:
            continue
        try:
            if not isinstance(message.content, str) or len(message.content.encode()) > 1_000_000:
                raise ValueError("unsupported tool data")
            payload = json.loads(message.content)
            if (not isinstance(payload, dict) or payload.get("schema_version") != "model_tool_data_v1"
                or payload.get("data_trust") != "untrusted_tool_data" or payload.get("execution_permit") is not False
                or set(payload) != {"schema_version", "data_trust", "source", "conflict_status", "validity_rule", "execution_permit", "guidance", "data"}
                or payload.get("validity_rule") != "observation_only_recheck_current_sources"
                or payload.get("guidance") != MODEL_TOOL_GUIDANCE
                or not isinstance(payload.get("source"), dict) or not isinstance(payload.get("data"), dict)):
                raise ValueError("missing provenance envelope")
            metadata, data = payload["source"], payload["data"]
            if (set(metadata) != {"tool_id", "invocation_id", "implementation_version", "catalog_version", "received_at"}
                or type(metadata.get("implementation_version")) is not int or type(metadata.get("catalog_version")) is not int
                or payload.get("conflict_status") != ("detected" if data.get("status") == "conflict" else "not_assessed")):
                raise ValueError("tool envelope metadata mismatch")
            invocation = await session.scalar(select(ToolInvocation).where(ToolInvocation.id == UUID(metadata["invocation_id"]),
                ToolInvocation.tenant_id == run.tenant_id, ToolInvocation.run_id == run.id,
                ToolInvocation.attempt_id == run.current_attempt_id, ToolInvocation.tool_call_id == message.tool_call_id))
            if (invocation is None or invocation.finished_at is None or invocation.status not in {"succeeded", "failed"}
                or invocation.tool_id != metadata.get("tool_id") or message.name != invocation.tool_id
                or invocation.implementation_version != metadata.get("implementation_version")
                or invocation.catalog_version != metadata.get("catalog_version") or _digest(data) != invocation.result_digest):
                raise ValueError("tool source mismatch")
            # 失败响应不携带已确认事实，但同样校验真实调用/摘要，不能伪造成功来源。
            if invocation.status != "succeeded":
                continue
            assert invocation.result_digest is not None
            roles_digest = None
            if invocation.tool_id == "current_identity_summary":
                expected = f"当前用户 {identity.user.id} 属于租户 {identity.principal.tenant_id}，可信角色：{', '.join(identity.principal.roles) or '无'}。"
                if data.get("summary") != expected:
                    raise ValueError("identity observation changed")
                roles_digest = _digest(sorted(identity.principal.roles))
            expires = _utc(invocation.finished_at) + timedelta(minutes=5) if invocation.tool_id in _DYNAMIC_TOOLS else None
            # 接收时间不延长来源观察寿命，诊断按原始observed_at计算。
            if invocation.tool_id in _DYNAMIC_TOOLS and isinstance(data.get("observed_at"), str):
                observed = datetime.fromisoformat(data["observed_at"])
                if observed.tzinfo is None or _utc(observed) > _utc(invocation.finished_at):
                    raise ValueError("invalid observation time")
                expires = _utc(observed) + timedelta(minutes=5)
            sources.append(ToolContextSource(invocation_id=invocation.id, tool_id=invocation.tool_id,
                implementation_version=invocation.implementation_version, catalog_version=invocation.catalog_version,
                result_digest=invocation.result_digest, expires_at=expires,
                ticket_id=data.get("external_id") if invocation.tool_id == "support_ticket_lookup" else None,
                ticket_version=data.get("version") if invocation.tool_id == "support_ticket_lookup" else None,
                identity_roles_digest=roles_digest))
        except (KeyError, ValueError, TypeError, AttributeError) as exc:
            raise ToolContextChanged("模型工具消息缺少匹配的本轮来源证明，不能复用旧正文") from exc
    return sources


async def assert_tool_context_authorized(session: AsyncSession, *, snapshot: ContextSnapshot | None,
    identity: IdentityContext, run: AgentRun, settings: Settings,
) -> None:
    if snapshot is None or not snapshot.used_tool_sources:
        return
    if "TOOL_SOURCE_CONFLICT" in snapshot.conflicts:
        raise ToolSourceConflict("工具来源存在冲突，不能选择其中一方确认根因，请人工复核或补充当前证据")
    version = await session.scalar(select(AgentVersion).where(AgentVersion.id == run.agent_version_id,
        AgentVersion.tenant_id == run.tenant_id, AgentVersion.agent_id == run.agent_id))
    if version is None or identity.principal.tenant_id != run.tenant_id:
        raise ToolContextChanged("工具来源固定版本或租户已变化")
    try:
        config = parse_agent_config(version.config)
    except ValueError as exc:
        raise ToolContextChanged("工具来源固定版本配置损坏") from exc
    if agent_config_digest(config) != version.config_digest:
        raise ToolContextChanged("固定版本配置摘要已变化")
    for source in sorted(snapshot.used_tool_sources, key=lambda item: str(item.invocation_id)):
        invocation = await session.scalar(select(ToolInvocation).where(ToolInvocation.id == source.invocation_id,
            ToolInvocation.tenant_id == run.tenant_id, ToolInvocation.run_id == run.id,
            ToolInvocation.attempt_id == run.current_attempt_id))
        if (invocation is None or invocation.status != "succeeded" or invocation.result_digest != source.result_digest
            or invocation.tool_id != source.tool_id or invocation.implementation_version != source.implementation_version
            or invocation.catalog_version != source.catalog_version or invocation.finished_at is None
            or (source.expires_at is not None and utc_now() >= _utc(source.expires_at))):
            raise ToolContextChanged("已使用工具观察已失效或超过五分钟，请重新获取当前证据")
        try:
            policy = await resolve_tool_policy(session, identity=identity, config=config,
                allowed_tool_ids=version.resolved_tool_ids, tool_id=source.tool_id, arguments={},
                implementation_versions=version.resolved_tool_versions)
            if (policy.catalog_version != source.catalog_version
                or policy.risk_number >= 2 and not policy.action_proposal_only
                or policy.approval_required and not policy.action_proposal_only):
                raise ToolContextChanged("已使用工具的目录或执行策略已变化")
        except ToolPolicyError as exc:
            raise ToolContextChanged("已使用工具的当前许可已失效") from exc
        if source.tool_id in _DYNAMIC_TOOLS - {"support_ticket_lookup"}:
            if not settings.diagnostic_mock_enabled or settings.environment == "production":
                raise ToolContextChanged("已使用的隔离诊断当前不可用")
        if source.tool_id in _DYNAMIC_TOOLS and (source.expires_at is None
            or _utc(source.expires_at) > _utc(invocation.finished_at) + timedelta(minutes=5)):
            raise ToolContextChanged("动态工具观察缺少合法的有界时效证明")
        if source.tool_id == "current_identity_summary" and source.identity_roles_digest != _digest(sorted(identity.principal.roles)):
            raise ToolContextChanged("身份摘要中的角色已变化")
        if source.tool_id == "support_ticket_lookup" and source.ticket_id is not None:
            from supportops_core.ticket_services import query_mock_support_ticket
            current = await query_mock_support_ticket(session, identity=identity, settings=settings, ticket_id=source.ticket_id)
            if current.status != "available" or current.version != source.ticket_version:
                raise ToolContextChanged("工单观察的授权或目标版本已变化")
        if source.tool_id == "create_mock_ticket":
            if not settings.business_action_mock_enabled or settings.environment == "production":
                raise ToolContextChanged("动作提案来源当前不可用")
            action = await session.scalar(select(BusinessAction.id).where(BusinessAction.id == invocation.business_action_id,
                BusinessAction.tenant_id == run.tenant_id, BusinessAction.proposed_by == identity.user.id))
            if action is None:
                raise ToolContextChanged("动作提案来源当前不可读取")


async def mark_diagnostic_tool_conflicts(session: AsyncSession, *, invocation: ToolInvocation,
    attempt: RunAttempt,
) -> None:
    """只比较同Run/attempt、同请求的已声明公共标量字段，不声称通用语义冲突识别。"""
    from supportops_core.diagnostic_mock_services import MOCK_DIAGNOSTIC_TOOLS, MockDiagnosticResponse
    current = MockDiagnosticResponse.model_validate(invocation.diagnostic_result)
    previous = (await session.execute(select(ToolInvocation.diagnostic_result, ToolInvocation.result_digest).where(
        ToolInvocation.tenant_id == invocation.tenant_id, ToolInvocation.run_id == invocation.run_id,
        ToolInvocation.attempt_id == invocation.attempt_id, ToolInvocation.id != invocation.id,
        ToolInvocation.tool_id.in_(MOCK_DIAGNOSTIC_TOOLS), ToolInvocation.status == "succeeded",
        ToolInvocation.diagnostic_result.is_not(None))
        .order_by(ToolInvocation.id).limit(1001))).all()
    if len(previous) > 1000:
        raise ToolContextChanged("诊断来源数量超过冲突复检上限")
    conflict = current.status == "conflict"
    for data, result_digest in previous:
        other = MockDiagnosticResponse.model_validate(data)
        if _digest(other.model_dump(mode="json")) != result_digest:
            raise ToolContextChanged("诊断冲突比较的旧来源摘要已失效")
        if other.request_id != current.request_id:
            continue
        conflict = conflict or other.status == "conflict"
        if current.status == other.status == "ok":
            conflict = conflict or any(_digest(current.facts[key]) != _digest(other.facts[key])
                for key in current.facts.keys() & other.facts.keys())
    if conflict and attempt.context_snapshot is not None:
        snapshot = ContextSnapshot.model_validate(attempt.context_snapshot)
        attempt.context_snapshot = snapshot.model_copy(update={
            "conflicts": list(dict.fromkeys([*snapshot.conflicts, "TOOL_SOURCE_CONFLICT"])),
            "unresolved_issues": list(dict.fromkeys([*snapshot.unresolved_issues, "TOOL_SOURCE_REQUIRES_REVIEW"])),
        }).model_dump(mode="json")
