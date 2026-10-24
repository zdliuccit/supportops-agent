"""复查父尝试原工具观察，生成诊断/工单只读子上下文；不查询新工具或派发模型。"""

from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import NAMESPACE_URL, UUID, uuid5

from langchain_core.messages import ToolMessage
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from supportops_core.agent_config import parse_agent_config
from supportops_core.agent_services import _grant_filter
from supportops_core.approval_services import mock_project_id, require_mock_staff
from supportops_core.config import Settings
from supportops_core.context_services import ContextSnapshot
from supportops_core.diagnostic_mock_services import MOCK_DIAGNOSTIC_TOOLS, MOCK_FIXTURE_REQUESTS, MockDiagnosticResponse
from supportops_core.enums import MessageRole
from supportops_core.models import AgentAccessGrant, Message, MockSupportTicket, ToolCatalogEntry, ToolInvocation
from supportops_core.redaction import redact_sensitive_text
from supportops_core.release_manifest_services import manifest_digest
from supportops_core.run_attempt_services import RunAttemptIdentity
from supportops_core.specialist_business_context_contracts import (
    BusinessSpecialist, SpecialistBusinessContext, SpecialistBusinessExcerpt, SpecialistBusinessObservation,
    SpecialistBusinessScope, SpecialistBusinessSource, business_payload_bytes,
)
from supportops_core.specialist_context_services import _specialist_parent_versions, _utc, _version_ref
from supportops_core.specialist_request_contracts import canonical_request_json
from supportops_core.specialist_task_contracts import (
    SpecialistEvidence, SpecialistParent, SpecialistResource, SpecialistTaskInput, SpecialistVersion, contract_digest,
)
from supportops_core.ticket_services import MockTicketView, ticket_visibility
from supportops_core.tool_context_services import assert_tool_context_authorized, capture_model_tool_sources
from supportops_core.tool_policy import ResolvedToolPolicy, resolve_tool_policy
from supportops_core.tool_result_services import normalize_tool_result


class SpecialistBusinessContextDenied(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__("专业子任务的原业务观察当前不可读取")
        self.code = code


def _observations(value: tuple[SpecialistBusinessObservation, ...], specialist: BusinessSpecialist,
) -> tuple[SpecialistBusinessObservation, ...]:
    if specialist not in {"diagnostic", "ticket_analysis"} or not 1 <= len(value) <= 32:
        raise SpecialistBusinessContextDenied("SPECIALIST_BUSINESS_SELECTION_INVALID")
    copied = tuple(SpecialistBusinessObservation.model_validate_json(item.model_dump_json()) for item in value)
    if (len({item.source.invocation_id for item in copied}) != len(copied)
        or len({item.tool_call_id for item in copied}) != len(copied)
        or any((item.tool_id == "support_ticket_lookup") != (specialist == "ticket_analysis") for item in copied)):
        raise SpecialistBusinessContextDenied("SPECIALIST_BUSINESS_SELECTION_INVALID")
    return tuple(sorted(copied, key=lambda item: str(item.source.invocation_id)))


def _scope_digest(scope: SpecialistBusinessScope) -> str:
    return manifest_digest(scope.model_dump(mode="json", exclude={"checked_at"}))


async def _project(session: AsyncSession, *, attempt: RunAttemptIdentity, child_version: SpecialistVersion,
    specialist: BusinessSpecialist, observations: tuple[SpecialistBusinessObservation, ...], settings: Settings,
    expires_at: datetime | None,
) -> SpecialistBusinessScope:
    selected = _observations(observations, specialist)
    if (settings.environment == "production" or specialist == "diagnostic" and not settings.diagnostic_mock_enabled
        or specialist == "ticket_analysis" and not settings.business_action_mock_enabled):
        raise SpecialistBusinessContextDenied("SPECIALIST_BUSINESS_CONNECTOR_UNAVAILABLE")
    started = datetime.now(UTC)
    owned, identity, parent, child = await _specialist_parent_versions(session, attempt=attempt, child_version=child_version)
    original_input = await session.scalar(select(Message.id).where(Message.id == owned.run.input_message_id,
        Message.tenant_id == attempt.tenant_id, Message.conversation_id == attempt.conversation_id,
        Message.user_id == identity.user.id, Message.role == MessageRole.USER,
        Message.turn_sequence == owned.run.input_sequence))
    if original_input is None:
        raise SpecialistBusinessContextDenied("SPECIALIST_BUSINESS_PARENT_CHANGED")
    config = parse_agent_config(parent.config)
    # 清晰刷新本次选定目录记录，不能从调用者Session的旧ORM对象沿用撤销前策略。
    catalogs = (await session.scalars(select(ToolCatalogEntry).where(ToolCatalogEntry.tenant_id == attempt.tenant_id,
        ToolCatalogEntry.tool_id.in_({item.tool_id for item in selected})).execution_options(populate_existing=True))).all()
    if len(catalogs) != len({item.tool_id for item in selected}):
        raise SpecialistBusinessContextDenied("SPECIALIST_BUSINESS_AUTHORIZATION_CHANGED")
    policies: dict[str, ResolvedToolPolicy] = {}
    for tool_id in sorted({item.tool_id for item in selected}):
        policy = await resolve_tool_policy(session, identity=identity, config=config,
            allowed_tool_ids=parent.resolved_tool_ids, tool_id=tool_id, arguments={},
            implementation_versions=parent.resolved_tool_versions)
        if policy.approval_required or policy.risk_number > 1 or policy.action_proposal_only:
            raise SpecialistBusinessContextDenied("SPECIALIST_BUSINESS_AUTHORIZATION_CHANGED")
        policies[tool_id] = policy
    ids = tuple(item.source.invocation_id for item in selected)
    # 先核当前策略，再只取本父尝试的调用元数据；不扫描其他来源或会话正文。
    rows = (await session.execute(select(ToolInvocation.id, ToolInvocation.tool_id, ToolInvocation.tool_call_id,
        ToolInvocation.implementation_version, ToolInvocation.catalog_version, ToolInvocation.result_digest,
        ToolInvocation.created_at, ToolInvocation.finished_at).where(ToolInvocation.id.in_(ids),
        ToolInvocation.tenant_id == attempt.tenant_id, ToolInvocation.run_id == attempt.run_id,
        ToolInvocation.attempt_id == attempt.attempt_id, ToolInvocation.status == "succeeded")
        .order_by(ToolInvocation.id))).all()
    by_id = {row.id: row for row in rows}
    if set(by_id) != set(ids):
        raise SpecialistBusinessContextDenied("SPECIALIST_BUSINESS_SOURCE_UNAVAILABLE")
    for item in selected:
        row, message, policy = by_id[item.source.invocation_id], item.source, policies[item.tool_id]
        if (row.tool_id != item.tool_id or row.tool_call_id != item.tool_call_id
            or row.finished_at is None or row.result_digest is None
            or row.implementation_version != message.implementation_version
            or row.implementation_version != policy.implementation_version
            or row.catalog_version != message.catalog_version or row.catalog_version != policy.catalog_version
            or not _utc(row.created_at) <= _utc(row.finished_at) <= message.received_at <= started):
            raise SpecialistBusinessContextDenied("SPECIALIST_BUSINESS_SOURCE_CHANGED")
    sources = await capture_model_tool_sources(session, messages=[ToolMessage(content=item.envelope_json,
        name=item.tool_id, tool_call_id=item.tool_call_id) for item in selected], run=owned.run, identity=identity)
    if len(sources) != len(selected) or owned.attempt.context_snapshot is None:
        raise SpecialistBusinessContextDenied("SPECIALIST_BUSINESS_SOURCE_UNAVAILABLE")
    snapshot = ContextSnapshot.model_validate(owned.attempt.context_snapshot)
    if snapshot.current_input_id != owned.run.input_message_id or snapshot.through_input_sequence != owned.run.input_sequence:
        raise SpecialistBusinessContextDenied("SPECIALIST_BUSINESS_PARENT_CHANGED")
    await assert_tool_context_authorized(session, snapshot=snapshot.model_copy(update={"used_tool_sources": sources}),
        identity=identity, run=owned.run, settings=settings)
    captured = {item.invocation_id: item for item in sources}
    deadline = min(started + timedelta(seconds=30), _utc(owned.attempt.lease_expires_at))
    if expires_at is not None:
        if expires_at.tzinfo is None:
            raise SpecialistBusinessContextDenied("SPECIALIST_BUSINESS_WINDOW_INVALID")
        deadline = min(deadline, expires_at.astimezone(UTC))
    source_rows: list[SpecialistBusinessSource] = []
    resource_keys: dict[UUID, UUID] = {}
    resource_targets: dict[UUID, dict[str, object]] = {}
    resource_grants: dict[str, object] = {}
    diagnostic_facts: dict[tuple[UUID, str], str] = {}
    for item in selected:
        message, row, source = item.source, by_id[item.source.invocation_id], captured[item.source.invocation_id]
        if (source.result_digest != row.result_digest or source.tool_id != row.tool_id
            or source.implementation_version != row.implementation_version or source.catalog_version != row.catalog_version):
            raise SpecialistBusinessContextDenied("SPECIALIST_BUSINESS_SOURCE_CHANGED")
        if item.tool_id in MOCK_DIAGNOSTIC_TOOLS:
            diagnostic = MockDiagnosticResponse.model_validate_json(item.payload_json, strict=True)
            stored = await session.scalar(select(ToolInvocation.diagnostic_result).where(
                ToolInvocation.id == message.invocation_id, ToolInvocation.tenant_id == attempt.tenant_id,
                ToolInvocation.run_id == attempt.run_id, ToolInvocation.attempt_id == attempt.attempt_id,
                ToolInvocation.status == "succeeded"))
            if (diagnostic.status != "ok" or diagnostic.request_id not in MOCK_FIXTURE_REQUESTS
                or diagnostic.tool_id != item.tool_id or diagnostic.observed_at.tzinfo is None
                or canonical_request_json(diagnostic.model_dump(mode="json")) != item.payload_json
                or canonical_request_json(stored) != item.payload_json
                or diagnostic.facts.get("subject_user_id", str(identity.user.id)) != str(identity.user.id)
                or diagnostic.facts.get("project_id", str(mock_project_id(attempt.tenant_id, identity.user.id)))
                    != str(mock_project_id(attempt.tenant_id, identity.user.id))):
                raise SpecialistBusinessContextDenied("SPECIALIST_BUSINESS_DIAGNOSTIC_UNAVAILABLE")
            observed = diagnostic.observed_at.astimezone(UTC)
            resource_id = uuid5(NAMESPACE_URL,
                f"supportops:specialist-diagnostic:{attempt.tenant_id}:{identity.user.id}:{diagnostic.request_id}")
            resource_targets[resource_id] = {"kind": "diagnostic_request", "request_id": diagnostic.request_id}
            for key, value in diagnostic.facts.items():
                marker, digest = (resource_id, key), manifest_digest({"value": value})
                if marker in diagnostic_facts and diagnostic_facts[marker] != digest:
                    raise SpecialistBusinessContextDenied("SPECIALIST_BUSINESS_SOURCE_CONFLICT")
                diagnostic_facts[marker] = digest
        else:
            ticket = MockTicketView.model_validate_json(item.payload_json, strict=True)
            if (ticket.status != "available" or ticket.external_id is None or ticket.version is None
                or ticket.observed_at.tzinfo is None
                or canonical_request_json(ticket.model_dump(mode="json")) != item.payload_json):
                raise SpecialistBusinessContextDenied("SPECIALIST_BUSINESS_TICKET_UNAVAILABLE")
            current = (await session.execute(select(MockSupportTicket.id, MockSupportTicket.owner_user_id,
                MockSupportTicket.project_id, MockSupportTicket.version).where(
                MockSupportTicket.external_id == ticket.external_id, ticket_visibility(identity)))).one_or_none()
            if (current is None or current.version != ticket.version
                or current.project_id != mock_project_id(attempt.tenant_id, current.owner_user_id)):
                raise SpecialistBusinessContextDenied("SPECIALIST_BUSINESS_TICKET_UNAVAILABLE")
            staff_grants = []
            if current.owner_user_id != identity.user.id:
                # 子上下文读取不继承管理员角色或仅ticket_handle；另需显式context读取许可。
                for permission in ("ticket_handle", "support_context_read"):
                    grant = await require_mock_staff(session, tenant_id=attempt.tenant_id, owner_user_id=current.owner_user_id,
                        user_id=identity.user.id, permission=permission, lock=True)
                    staff_grants.append({"id": str(grant.id), "revision": grant.revision, "permission": grant.permission})
            resource_grants[str(current.id)] = staff_grants
            observed, resource_id = ticket.observed_at.astimezone(UTC), current.id
            resource_targets[resource_id] = {"kind": "support_ticket", "version": ticket.version,
                "owner_user_id": str(current.owner_user_id), "project_id": str(current.project_id)}
        if (source.expires_at is None or _utc(source.expires_at) != observed + timedelta(minutes=5)
            or not _utc(row.created_at) <= observed <= _utc(row.finished_at)
            or started >= _utc(source.expires_at)):
            raise SpecialistBusinessContextDenied("SPECIALIST_BUSINESS_SOURCE_EXPIRED")
        deadline = min(deadline, _utc(source.expires_at))
        resource_keys[message.invocation_id] = resource_id
        source_rows.append(SpecialistBusinessSource(invocation_id=message.invocation_id, tool_id=item.tool_id,
            tool_call_id=item.tool_call_id, implementation_version=source.implementation_version,
            catalog_version=source.catalog_version, result_digest=source.result_digest,
            message_digest=hashlib.sha256(item.envelope_json.encode("utf-8")).hexdigest(),
            observed_at=observed, observation_until=_utc(source.expires_at)))
    agent_grants = (await session.execute(select(AgentAccessGrant.id, AgentAccessGrant.agent_id,
        AgentAccessGrant.subject_type, AgentAccessGrant.subject_id).where(AgentAccessGrant.tenant_id == attempt.tenant_id,
        AgentAccessGrant.agent_id.in_({parent.agent_id, child.agent_id}), _grant_filter(identity))
        .order_by(AgentAccessGrant.id))).all()
    resource_versions = {resource_id: manifest_digest({"schema_version": "specialist_business_resource_v1",
        "target": target, "sources": [item.model_dump(mode="json") for item in source_rows
            if resource_keys[item.invocation_id] == resource_id]}) for resource_id, target in resource_targets.items()}
    scope_digest = manifest_digest({"schema_version": "specialist_business_authorization_v1", "specialist": specialist,
        "tenant_id": str(attempt.tenant_id), "subject_user_id": str(identity.user.id),
        "auth_generation": identity.user.auth_generation, "roles": sorted(identity.principal.roles),
        "organization_unit_id": str(identity.user.organization_unit_id) if identity.user.organization_unit_id else None,
        "run_id": str(attempt.run_id), "conversation_id": str(attempt.conversation_id),
        "attempt_id": str(attempt.attempt_id), "owner_id": attempt.owner_id, "generation": attempt.generation,
        "parent_config_digest": owned.run.config_digest, "parent_credential_revision": owned.run.credential_revision,
        "parent_version": _version_ref(parent).model_dump(mode="json"), "child_version": child_version.model_dump(mode="json"),
        "environment": settings.environment, "resource_grants": resource_grants,
        "agent_grants": [[str(row.id), str(row.agent_id), row.subject_type.value, row.subject_id] for row in agent_grants],
        "resources": {str(key): value for key, value in resource_versions.items()}})
    resource_kind: Literal["diagnostic_request", "support_ticket"] = (
        "diagnostic_request" if specialist == "diagnostic" else "support_ticket")
    resources = tuple(SpecialistResource(tenant_id=attempt.tenant_id, resource_kind=resource_kind,
        resource_id=key, resource_version_digest=resource_versions[key], authorization_scope_digest=scope_digest)
        for key in sorted(resource_versions, key=str))
    indexed = {item.resource_id: item for item in resources}
    if datetime.now(UTC) >= deadline:
        raise SpecialistBusinessContextDenied("SPECIALIST_BUSINESS_WINDOW_EXPIRED")
    return SpecialistBusinessScope(specialist=specialist, parent=SpecialistParent(tenant_id=attempt.tenant_id,
        subject_user_id=identity.user.id, conversation_id=attempt.conversation_id, run_id=attempt.run_id,
        attempt_id=attempt.attempt_id, attempt_generation=attempt.generation, version=_version_ref(parent),
        authorization_scope_digest=scope_digest, authorized_until=deadline), child_version=child_version, resources=resources,
        evidence=tuple(SpecialistEvidence(evidence_id=item.invocation_id, resource=indexed[resource_keys[item.invocation_id]],
            content_digest=item.result_digest, observed_at=item.observed_at, valid_until=deadline) for item in source_rows),
        sources=tuple(source_rows), checked_at=started, expires_at=deadline)


async def project_specialist_business_scope(session: AsyncSession, *, attempt: RunAttemptIdentity,
    child_version: SpecialistVersion, specialist: BusinessSpecialist,
    observations: tuple[SpecialistBusinessObservation, ...], settings: Settings, expires_at: datetime | None = None,
) -> SpecialistBusinessScope:
    """当前父工具信封+真实调用账本+当前授权共同核对；调用者管理短事务，不提交或派发。"""
    try:
        return await _project(session, attempt=attempt, child_version=child_version, specialist=specialist,
            observations=observations, settings=settings, expires_at=expires_at)
    except asyncio.CancelledError:
        raise
    except SpecialistBusinessContextDenied:
        raise
    except Exception:
        raise SpecialistBusinessContextDenied("SPECIALIST_BUSINESS_READ_UNCONFIRMED") from None


async def load_specialist_business_context(session: AsyncSession, *, attempt: RunAttemptIdentity,
    task: SpecialistTaskInput, scope: SpecialistBusinessScope, observations: tuple[SpecialistBusinessObservation, ...],
    settings: Settings, redaction_secrets: tuple[str, ...] = (),
) -> SpecialistBusinessContext:
    """仅渲染原任务证据子集，前后复查全部原范围；不补查历史、日志、工单活动或知识。"""
    try:
        selected = _observations(observations, scope.specialist)
        now = datetime.now(UTC)
        if (task.specialist != scope.specialist or task.parent != scope.parent or task.child_version != scope.child_version
            or not scope.checked_at <= task.created_at <= now < task.deadline <= scope.expires_at
            or not task.evidence or any(item not in scope.resources for item in task.resources)
            or any(item not in scope.evidence for item in task.evidence)
            or {item.resource for item in task.evidence} != set(task.resources)):
            raise SpecialistBusinessContextDenied("SPECIALIST_BUSINESS_TASK_MISMATCH")
        fresh = await project_specialist_business_scope(session, attempt=attempt, child_version=scope.child_version,
            specialist=scope.specialist, observations=selected, settings=settings, expires_at=scope.expires_at)
        if _scope_digest(fresh) != _scope_digest(scope):
            raise SpecialistBusinessContextDenied("SPECIALIST_BUSINESS_AUTHORIZATION_CHANGED")
        secrets = (settings.jwt_secret, settings.secret_encryption_key, *redaction_secrets)
        question = redact_sensitive_text(task.question, secrets=secrets)
        indexed = {item.source.invocation_id: item for item in selected}
        excerpts: list[SpecialistBusinessExcerpt] = []
        for evidence in sorted(task.evidence, key=lambda item: str(item.evidence_id)):
            original = indexed[evidence.evidence_id]
            result = normalize_tool_result(ToolMessage(content=original.payload_json, name=original.tool_id,
                tool_call_id=original.tool_call_id), tool_id=original.tool_id, call_id=original.tool_call_id,
                max_bytes=16384, redaction_secrets=secrets)
            if not isinstance(result.content, str):
                raise SpecialistBusinessContextDenied("SPECIALIST_BUSINESS_RENDERING_INVALID")
            rendered_digest = hashlib.sha256(result.content.encode("utf-8")).hexdigest()
            excerpts.append(SpecialistBusinessExcerpt(evidence_id=evidence.evidence_id, resource=evidence.resource,
                tool_id=original.tool_id, original_result_digest=evidence.content_digest,
                rendered_content_digest=rendered_digest, payload_json=result.content,
                content_is_redacted=result.content != original.payload_json))
        payload_bytes = business_payload_bytes(question, tuple(excerpts))
        if payload_bytes > 65536 or len(question.encode("utf-8")) > 8192:
            raise SpecialistBusinessContextDenied("SPECIALIST_BUSINESS_MATERIAL_TOO_LARGE")
        final = await project_specialist_business_scope(session, attempt=attempt, child_version=scope.child_version,
            specialist=scope.specialist, observations=selected, settings=settings, expires_at=scope.expires_at)
        if _scope_digest(final) != _scope_digest(scope) or datetime.now(UTC) >= task.deadline:
            raise SpecialistBusinessContextDenied("SPECIALIST_BUSINESS_AUTHORIZATION_CHANGED")
        return SpecialistBusinessContext(task_id=task.task_id, specialist=scope.specialist,
            input_digest=contract_digest(task), scope_digest=contract_digest(scope), budget_scope_digest=contract_digest(task.budget),
            checked_at=datetime.now(UTC), expires_at=task.deadline, question=question, question_is_redacted=question != task.question,
            excerpts=tuple(excerpts), payload_bytes=payload_bytes)
    except asyncio.CancelledError:
        raise
    except SpecialistBusinessContextDenied:
        raise
    except Exception:
        raise SpecialistBusinessContextDenied("SPECIALIST_BUSINESS_READ_UNCONFIRMED") from None
