"""真实认证/知识领域服务与数据库下的 ACL、正文边界及拒绝审计。"""

import hashlib
import json
from dataclasses import dataclass
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import pytest
from httpx import AsyncClient
from sqlalchemy import event, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine
from supportops_core import knowledge_access_services
from supportops_core.auth import IdentityContext, Principal
from supportops_core.config import Settings
from supportops_core.db import create_session_factory
from supportops_core.enums import (
    AuditAction,
    GrantSubjectType,
    KnowledgeAclSubjectType,
    KnowledgeSourceEnvironment,
)
from supportops_core.knowledge_index_services import consume_knowledge_events
from supportops_core.knowledge_retrieval import search_keyword_knowledge
from supportops_core.knowledge_services import (
    create_knowledge_document,
    create_knowledge_source,
    decide_knowledge_review,
    get_published_knowledge_content,
    publish_knowledge_version,
    replace_knowledge_acl,
    submit_knowledge_version_for_review,
)
from supportops_core.models import (
    AgentAccessGrant,
    AgentAuditEvent,
    KnowledgeDocument,
    KnowledgeIndexGeneration,
    KnowledgeVersion,
    OrganizationUnit,
    ToolInvocation,
    User,
)
from supportops_core.run_attempt_services import claim_run_attempt

from tests.conftest import auth_headers, provision_agent

BODY = "# RestrictedSection\n\nPrivateBody-429-budget; do not publish raw content."


@dataclass
class KnowledgeCase:
    client: AsyncClient
    engine: AsyncEngine
    settings: Settings
    tenant_id: UUID
    owner_id: UUID
    reader_id: UUID
    department_id: UUID
    parent_department_id: UUID
    document_id: UUID
    version_id: UUID
    owner_headers: dict[str, str]
    reader_headers: dict[str, str]

    async def acl(self, entries: list[tuple[KnowledgeAclSubjectType, str]]) -> None:
        async with create_session_factory(self.engine)() as session, session.begin():
            await replace_knowledge_acl(
                session,
                document_id=self.document_id,
                tenant_id=self.tenant_id,
                actor_user_id=self.owner_id,
                entries=entries,
                correlation_id="test-acl",
            )


@pytest.fixture
async def knowledge_case(client: AsyncClient, engine: AsyncEngine, settings: Settings):
    tenant_id = uuid4()
    owner_headers = await auth_headers(
        engine, settings, tenant_id, "knowledge-owner", roles=("platform_admin",)
    )
    reader_headers = await auth_headers(
        engine, settings, tenant_id, "knowledge-reader", roles=("employee",)
    )
    await auth_headers(engine, settings, tenant_id, "knowledge-reviewer", roles=("platform_admin",))
    owner_id, reader_id, reviewer_id = [
        uuid5(NAMESPACE_URL, f"{tenant_id}:{name}")
        for name in (
            "knowledge-owner",
            "knowledge-reader",
            "knowledge-reviewer",
        )
    ]
    async with create_session_factory(engine)() as session, session.begin():
        parent = OrganizationUnit(tenant_id=tenant_id, name="父部门")
        session.add(parent)
        await session.flush()
        department = OrganizationUnit(tenant_id=tenant_id, name="直属部门", parent_id=parent.id)
        session.add(department)
        await session.flush()
        reader = await session.get(User, reader_id)
        assert reader is not None
        reader.organization_unit_id = department.id
        source = await create_knowledge_source(
            session,
            tenant_id=tenant_id,
            actor_user_id=owner_id,
            external_key="security-source",
            name="安全测试来源",
            source_type="manual",
            environment=KnowledgeSourceEnvironment.DEV,
            owner_user_id=owner_id,
            correlation_id="source-security",
        )
        document = await create_knowledge_document(
            session,
            tenant_id=tenant_id,
            actor_user_id=owner_id,
            source_id=source.id,
            external_key="restricted",
            owner_user_id=owner_id,
            title="RestrictedTitle",
            content_markdown=BODY,
            correlation_id="document-security",
        )
        await replace_knowledge_acl(
            session,
            tenant_id=tenant_id,
            document_id=document.id,
            actor_user_id=owner_id,
            entries=[(KnowledgeAclSubjectType.USER, str(reader_id))],
            correlation_id="initial-acl",
        )
        version = await session.scalar(
            select(KnowledgeVersion).where(KnowledgeVersion.document_id == document.id)
        )
        assert version is not None
        await submit_knowledge_version_for_review(
            session,
            version_id=version.id,
            tenant_id=tenant_id,
            actor_user_id=owner_id,
            correlation_id="submit",
        )
        await decide_knowledge_review(
            session,
            version_id=version.id,
            tenant_id=tenant_id,
            actor_user_id=reviewer_id,
            decision="approved",
            expected_review_revision=version.review_revision,
            expected_content_digest=version.content_digest,
            comment="ReviewerNote-private",
            correlation_id="review",
        )
        await publish_knowledge_version(
            session,
            version_id=version.id,
            tenant_id=tenant_id,
            actor_user_id=owner_id,
            runtime_environment="test",
            correlation_id="publish",
        )
    return KnowledgeCase(
        client,
        engine,
        settings,
        tenant_id,
        owner_id,
        reader_id,
        department.id,
        parent.id,
        document.id,
        version.id,
        owner_headers,
        reader_headers,
    )


async def denials(case: KnowledgeCase, tenant_id: UUID | None = None) -> list[AgentAuditEvent]:
    async with create_session_factory(case.engine)() as session:
        return list(
            (
                await session.scalars(
                    select(AgentAuditEvent).where(
                        AgentAuditEvent.action == AuditAction.KNOWLEDGE_ACCESS_DENIED,
                        AgentAuditEvent.tenant_id == (tenant_id or case.tenant_id),
                    )
                )
            ).all()
        )


@pytest.mark.parametrize("mask", range(8))
async def test_snapshot_acl_uses_any_matching_role_user_or_direct_department(
    knowledge_case: KnowledgeCase, mask: int
):
    case = knowledge_case
    entries = [
        (KnowledgeAclSubjectType.ROLE, "employee" if mask & 1 else "customer"),
        (KnowledgeAclSubjectType.USER, str(case.reader_id if mask & 2 else case.owner_id)),
        (
            KnowledgeAclSubjectType.ORGANIZATION_UNIT,
            str(case.department_id if mask & 4 else case.parent_department_id),
        ),
    ]
    await case.acl(entries)
    response = await case.client.get(
        f"/v1/admin/knowledge/documents/{case.document_id}/snapshot", headers=case.reader_headers
    )
    assert response.status_code == (200 if mask else 403)
    assert response.headers["cache-control"] == "no-store"
    if mask:
        assert response.json()["version_id"] == str(case.version_id)
        async with create_session_factory(case.engine)() as session, session.begin():
            user = await session.get(User, case.reader_id)
            identity = IdentityContext(
                Principal(user.id, case.tenant_id, tuple(user.roles), user.display_name), user
            )
            version = await get_published_knowledge_content(
                session, document_id=case.document_id, tenant_id=case.tenant_id, identity=identity
            )
            assert version.content_markdown == BODY
    else:
        assert "RestrictedSection" not in response.text
        assert len(await denials(case)) == 1


async def test_admin_governance_metadata_does_not_grant_version_or_published_body(
    knowledge_case: KnowledgeCase,
):
    case = knowledge_case
    path = f"/v1/admin/knowledge/documents/{case.document_id}"
    metadata = await case.client.get(path, headers=case.owner_headers)
    assert metadata.status_code == 200 and "content_markdown" not in metadata.json()
    denied = await case.client.get(path + "/snapshot", headers=case.owner_headers)
    assert denied.status_code == 403
    history = await case.client.get(path + "/versions", headers=case.owner_headers)
    assert history.status_code == 200
    item = history.json()["items"][0]
    assert item["content_markdown"] is None
    assert item["section_anchors"] == []
    assert item["review_comment"] is None
    assert "PrivateBody" not in history.text and "RestrictedSection" not in history.text
    await case.acl([(KnowledgeAclSubjectType.USER, str(case.owner_id))])
    permitted = await case.client.get(path + "/versions", headers=case.owner_headers)
    assert permitted.json()["items"][0]["content_markdown"] == BODY


@pytest.mark.parametrize("target", ["foreign", "missing", "revoked"])
async def test_denial_audit_records_only_tenant_scoped_digests(
    knowledge_case: KnowledgeCase, target: str
):
    case = knowledge_case
    headers, tenant_id, actor_id, requested = (
        case.reader_headers,
        case.tenant_id,
        case.reader_id,
        case.document_id,
    )
    if target == "foreign":
        tenant_id = uuid4()
        headers = await auth_headers(
            case.engine, case.settings, tenant_id, "foreign-admin", roles=("platform_admin",)
        )
        actor_id = uuid5(NAMESPACE_URL, f"{tenant_id}:foreign-admin")
    elif target == "missing":
        requested = uuid4()
    else:
        await case.acl([])
    correlation = "private-query-and-token-marker"
    response = await case.client.get(
        f"/v1/admin/knowledge/documents/{requested}/snapshot",
        headers={**headers, "X-Correlation-ID": correlation},
    )
    assert response.status_code == (403 if target == "revoked" else 404)
    assert response.headers["cache-control"] == "no-store"
    events = await denials(case, tenant_id)
    assert len(events) == 1
    event = events[0]
    assert event.tenant_id == tenant_id and event.actor_user_id == actor_id
    assert event.version_id is None and event.agent_id is None
    assert event.metadata_payload == {
        "schema_version": "knowledge_access_denial_v1",
        "operation": "snapshot_read",
        "outcome": "not_accessible",
        "target_observation": "requested_only",
        "requested_resource_digest": hashlib.sha256(
            b"knowledge-denial-target-v1\0" + tenant_id.bytes + b"snapshot_read\0" + requested.bytes
        ).hexdigest(),
    }
    assert len(event.correlation_id) == 64
    serialized = json.dumps(event.metadata_payload) + event.correlation_id
    for secret in (
        str(requested),
        str(case.version_id),
        BODY,
        "RestrictedTitle",
        correlation,
        headers["Authorization"],
    ):
        assert secret not in serialized


async def create_evidence(case: KnowledgeCase) -> tuple[UUID, UUID]:
    agent_id = await provision_agent(
        case.engine,
        case.settings,
        case.tenant_id,
        subject="knowledge-reader",
        tool_ids=("search_knowledge",),
    )
    async with create_session_factory(case.engine)() as session, session.begin():
        session.add(
            AgentAccessGrant(
                tenant_id=case.tenant_id,
                agent_id=agent_id,
                subject_type=GrantSubjectType.USER,
                subject_id=str(case.reader_id),
                created_by=case.owner_id,
            )
        )
    created = await case.client.post(
        "/v1/conversations", headers=case.reader_headers, json={"agent_id": str(agent_id)}
    )
    assert created.status_code == 201
    accepted = await case.client.post(
        f"/v1/conversations/{created.json()['id']}/messages",
        headers={**case.reader_headers, "Idempotency-Key": "evidence-security"},
        json={"content": "429 budget"},
    )
    assert accepted.status_code == 202
    run_id = UUID(accepted.json()["run_id"])
    factory = create_session_factory(case.engine)
    # PG 多用例共库时保留先前文档的 Outbox；按真实批次推进至本例索引就绪。
    for _ in range(20):
        await consume_knowledge_events(factory, settings=case.settings)
        async with factory() as session:
            ready = await session.scalar(
                select(KnowledgeIndexGeneration.id).where(
                    KnowledgeIndexGeneration.document_id == case.document_id,
                    KnowledgeIndexGeneration.status == "ready",
                )
            )
        if ready is not None:
            break
    else:
        raise AssertionError("本例知识索引未在有界消费轮次内就绪")
    async with factory() as session, session.begin():
        claim = await claim_run_attempt(
            session, run_id, owner_id="knowledge-test", lease_seconds=60
        )
        assert claim is not None
        invocation = ToolInvocation(
            tenant_id=case.tenant_id,
            run_id=run_id,
            attempt_id=claim.attempt.id,
            tool_call_id="search-1",
            tool_id="search_knowledge",
            implementation_version=1,
            catalog_version=1,
            risk_level="R0",
            arguments_digest="a" * 64,
            status="succeeded",
        )
        session.add(invocation)
        await session.flush()
        user = await session.get(User, case.reader_id)
        identity = IdentityContext(
            Principal(user.id, case.tenant_id, tuple(user.roles), user.display_name), user
        )
        result = await search_keyword_knowledge(
            session,
            identity=identity,
            attempt=claim.identity,
            query="429 budget",
            invocation_id=invocation.id,
        )
        assert result["status"] == "ok" and result["evidence"]
        return run_id, UUID(result["evidence"][0]["evidence_id"])


@pytest.mark.parametrize("change", ["acl", "role", "department", "other_user", "other_tenant"])
async def test_evidence_rechecks_current_acl_and_original_owner(
    knowledge_case: KnowledgeCase, change: str
):
    case = knowledge_case
    subject_type, subject_id = {
        "role": (KnowledgeAclSubjectType.ROLE, "employee"),
        "department": (KnowledgeAclSubjectType.ORGANIZATION_UNIT, str(case.department_id)),
    }.get(change, (KnowledgeAclSubjectType.USER, str(case.reader_id)))
    await case.acl([(subject_type, subject_id)])
    run_id, evidence_id = await create_evidence(case)
    path = f"/v1/runs/{run_id}/evidence/{evidence_id}"
    response = await case.client.get(path, headers=case.reader_headers)
    assert response.status_code == 200 and "PrivateBody" in response.json()["content"]
    assert response.headers["cache-control"] == "no-store"
    headers = case.reader_headers
    audit_tenant_id = case.tenant_id
    if change == "acl":
        await case.acl([])
    elif change in {"role", "department"}:
        async with create_session_factory(case.engine)() as session, session.begin():
            user = await session.get(User, case.reader_id)
            if change == "role":
                user.roles = ["customer"]
            else:
                user.organization_unit_id = case.parent_department_id
    elif change == "other_user":
        headers = case.owner_headers
    else:
        audit_tenant_id = uuid4()
        headers = await auth_headers(
            case.engine, case.settings, audit_tenant_id, "foreign", roles=("platform_admin",)
        )
    denied = await case.client.get(path, headers=headers)
    assert denied.status_code == 404
    assert "PrivateBody" not in denied.text and "RestrictedTitle" not in denied.text
    if denied.status_code == 404:
        events = await denials(case, audit_tenant_id)
        assert len(events) == 1
        event = events[0]
        assert event.metadata_payload["operation"] == "evidence_read"
        assert len(event.metadata_payload["requested_run_digest"]) == 64
        assert str(run_id) not in json.dumps(event.metadata_payload)


@pytest.mark.parametrize("failure", [RuntimeError, TimeoutError, SQLAlchemyError])
async def test_audit_failure_keeps_denial_and_logs_no_exception(
    knowledge_case: KnowledgeCase, monkeypatch: pytest.MonkeyPatch, capsys, failure
):
    case = knowledge_case
    append_audit = knowledge_access_services.append_knowledge_audit

    async def fail_audit(*args, **kwargs):
        await append_audit(*args, **kwargs)
        raise failure("private SQL body token secret")

    monkeypatch.setattr(knowledge_access_services, "append_knowledge_audit", fail_audit)
    await case.acl([])
    response = await case.client.get(
        f"/v1/admin/knowledge/documents/{case.document_id}/snapshot", headers=case.reader_headers
    )
    assert response.status_code == 403
    assert not await denials(case)
    captured = capsys.readouterr()
    assert "private SQL body token secret" not in captured.out + captured.err + response.text
    assert "KNOWLEDGE_DENIAL_AUDIT_UNAVAILABLE" in captured.out


async def test_restricted_history_sql_does_not_load_body_or_reviewer_text(
    knowledge_case: KnowledgeCase,
):
    case = knowledge_case
    statements = []

    def observe(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(case.engine.sync_engine, "before_cursor_execute", observe)
    try:
        response = await case.client.get(
            f"/v1/admin/knowledge/documents/{case.document_id}/versions", headers=case.owner_headers
        )
    finally:
        event.remove(case.engine.sync_engine, "before_cursor_execute", observe)
    assert response.status_code == 200
    selected = "\n".join(sql for sql in statements if sql.lstrip().upper().startswith("SELECT"))
    for column in (
        "knowledge_versions.content_markdown",
        "knowledge_versions.section_anchors",
        "knowledge_versions.review_comment",
    ):
        assert column not in selected


async def test_version_write_receipts_also_hide_unapproved_body(knowledge_case: KnowledgeCase):
    case = knowledge_case
    created = await case.client.post(
        f"/v1/admin/knowledge/documents/{case.document_id}/versions",
        headers=case.owner_headers,
        json={
            "title": "新版本",
            "content_markdown": "# NewSection\nNewPrivateBody",
            "expected_revision": 1,
        },
    )
    assert created.status_code == 201
    version_id = created.json()["id"]
    assert created.json()["content_markdown"] is None
    submitted = await case.client.post(
        f"/v1/admin/knowledge/versions/{version_id}/submit-review", headers=case.owner_headers
    )
    assert submitted.status_code == 200 and submitted.json()["content_markdown"] is None
    reviewer_headers = await auth_headers(
        case.engine, case.settings, case.tenant_id, "knowledge-reviewer", roles=("platform_admin",)
    )
    approved = await case.client.post(
        f"/v1/admin/knowledge/versions/{version_id}/review",
        headers=reviewer_headers,
        json={
            "decision": "approved",
            "expected_review_revision": submitted.json()["review_revision"],
            "expected_content_digest": submitted.json()["content_digest"],
            "comment": "ReviewerSecretMarker",
        },
    )
    assert approved.status_code == 200 and approved.json()["review_comment"] is None
    published = await case.client.post(
        f"/v1/admin/knowledge/versions/{version_id}/publish", headers=case.owner_headers
    )
    assert published.status_code == 200 and published.json()["content_markdown"] is None
    async with create_session_factory(case.engine)() as session:
        events = (
            await session.scalars(
                select(AgentAuditEvent).where(AgentAuditEvent.tenant_id == case.tenant_id)
            )
        ).all()
        payload = json.dumps([row.metadata_payload for row in events])
        for secret in (
            BODY,
            "NewPrivateBody",
            "ReviewerSecretMarker",
            case.owner_headers["Authorization"],
        ):
            assert secret not in payload


async def test_denial_audit_commits_independently_after_request_rollback(
    knowledge_case: KnowledgeCase, monkeypatch: pytest.MonkeyPatch
):
    from supportops_api.routes import admin_knowledge
    from supportops_core.knowledge_services import KnowledgeAccessDeniedError

    case = knowledge_case
    original = admin_knowledge.get_effective_knowledge_snapshot

    async def refuse_after_read(session, **kwargs):
        await original(session, **kwargs)
        document = await session.get(KnowledgeDocument, case.document_id)
        document.external_key = "must-rollback"
        await session.flush()
        raise KnowledgeAccessDeniedError("当前权限拒绝")

    monkeypatch.setattr(admin_knowledge, "get_effective_knowledge_snapshot", refuse_after_read)
    response = await case.client.get(
        f"/v1/admin/knowledge/documents/{case.document_id}/snapshot", headers=case.reader_headers
    )
    assert response.status_code == 403
    async with create_session_factory(case.engine)() as session:
        document = await session.get(KnowledgeDocument, case.document_id)
        assert document.external_key == "restricted"
    assert len(await denials(case)) == 1


async def test_read_infrastructure_failure_is_not_acl_denial(
    knowledge_case: KnowledgeCase, monkeypatch: pytest.MonkeyPatch
):
    from supportops_api.routes import admin_knowledge

    async def unavailable(*args, **kwargs):
        raise SQLAlchemyError("private database SQL token")

    monkeypatch.setattr(admin_knowledge, "get_effective_knowledge_snapshot", unavailable)
    case = knowledge_case
    response = await case.client.get(
        f"/v1/admin/knowledge/documents/{case.document_id}/snapshot", headers=case.reader_headers
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "KNOWLEDGE_CONFLICT"
    assert "private database" not in response.text
    assert not await denials(case)


async def test_acl_configuration_read_is_complete_tenant_scoped_and_no_store(knowledge_case):
    case = knowledge_case
    entries = [
        (KnowledgeAclSubjectType.ROLE, "employee"),
        (KnowledgeAclSubjectType.USER, str(case.reader_id)),
        (KnowledgeAclSubjectType.ORGANIZATION_UNIT, str(case.department_id)),
    ]
    await case.acl(entries)
    response = await case.client.get(
        f"/v1/admin/knowledge/documents/{case.document_id}/acl", headers=case.owner_headers
    )
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == [
        {"subject_type": kind.value, "subject_id": value}
        for kind, value in sorted(entries, key=lambda entry: (entry[0].value, entry[1]))
    ]
    # 配置读取不隐式授予管理员正文权限。
    versions = await case.client.get(
        f"/v1/admin/knowledge/documents/{case.document_id}/versions", headers=case.owner_headers
    )
    assert all(item["content_markdown"] is None for item in versions.json()["items"])
    await case.acl([])
    empty = await case.client.get(
        f"/v1/admin/knowledge/documents/{case.document_id}/acl", headers=case.owner_headers
    )
    assert empty.json() == []


@pytest.mark.parametrize("target", ["other_tenant", "missing", "archived"])
async def test_acl_configuration_read_hides_unavailable_documents(knowledge_case, target):
    case = knowledge_case
    document_id = case.document_id
    headers = case.owner_headers
    if target == "other_tenant":
        headers = await auth_headers(
            case.engine, case.settings, uuid4(), "other-admin", roles=("platform_admin",)
        )
    elif target == "missing":
        document_id = uuid4()
    else:
        result = await case.client.delete(
            f"/v1/admin/knowledge/documents/{document_id}", headers=headers
        )
        assert result.status_code == 200
    response = await case.client.get(
        f"/v1/admin/knowledge/documents/{document_id}/acl", headers=headers
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"
    assert response.headers["cache-control"] == "no-store"


async def test_acl_configuration_read_requires_admin_and_authentication(knowledge_case):
    case = knowledge_case
    url = f"/v1/admin/knowledge/documents/{case.document_id}/acl"
    assert (await case.client.get(url, headers=case.reader_headers)).status_code == 403
    assert (await case.client.get(url)).status_code == 401
