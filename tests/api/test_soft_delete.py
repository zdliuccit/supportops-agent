"""验证删除保留数据、查询隔离、撤权及同名资源重建。"""

from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker
from sqlalchemy.orm import aliased
from supportops_core.config import Settings
from supportops_core.db import create_session_factory
from supportops_core.enums import ModelTestStatus, RunStatus
from supportops_core.models import (
    AgentAccessGrant,
    AgentAuditEvent,
    AgentErrorEvent,
    AgentRun,
    AgentRunObservation,
    Conversation,
    ModelCredential,
    ModelCredentialRevision,
    ModelEndpoint,
    ModelEndpointModel,
    ModelEndpointTestRun,
    ModelEndpointVersion,
    OrganizationUnit,
    utc_now,
)
from supportops_core.services import claim_run

from tests.api.test_agent_management import endpoint_payload
from tests.conftest import auth_headers, provision_agent


@pytest.mark.parametrize("run_status", [RunStatus.QUEUED, RunStatus.RUNNING, RunStatus.FAILED])
async def test_delete_conversation_with_foreign_keys_and_diagnostics(
    client: AsyncClient, engine: AsyncEngine, settings: Settings, run_status: RunStatus
) -> None:
    # 显式开启真实外键校验，覆盖此前有观测或错误记录时的删除失败。
    async with engine.connect() as connection:
        await connection.execute(text("PRAGMA foreign_keys=ON"))
        assert await connection.scalar(text("PRAGMA foreign_keys")) == 1
    tenant_id = uuid4()
    agent_id = await provision_agent(engine, settings, tenant_id)
    headers = await auth_headers(engine, settings, tenant_id)
    created = await client.post(
        "/v1/conversations", headers=headers, json={"agent_id": str(agent_id)}
    )
    assert created.status_code == 201
    conversation_id = UUID(created.json()["id"])
    path = f"/v1/conversations/{conversation_id}"
    accepted = await client.post(
        f"{path}/messages",
        json={"content": "诊断历史"},
        headers={**headers, "Idempotency-Key": "soft-delete"},
    )
    assert accepted.status_code == 202
    run_id = UUID(accepted.json()["run_id"])
    factory = create_session_factory(engine)
    async with factory() as session, session.begin():
        run = await session.get(AgentRun, run_id)
        conversation = await session.get(Conversation, conversation_id)
        assert run is not None and conversation is not None
        owner_id = conversation.user_id
        run.status = run_status
        session.add(
            AgentRunObservation(
                tenant_id=tenant_id,
                run_id=run_id,
                trace_id="test",
                span_id="root",
                kind="agent",
                name="execute",
                status="failed",
                started_at=utc_now(),
            )
        )
        session.add(
            AgentErrorEvent(
                tenant_id=tenant_id,
                run_id=run_id,
                conversation_id=conversation_id,
                agent_id=agent_id,
                agent_version_id=run.agent_version_id,
                model_endpoint_version_id=run.model_endpoint_version_id,
                user_id=conversation.user_id,
                occurred_at=utc_now(),
                error_code="TEST_FAILURE",
            )
        )
    assert (await client.delete(path, headers=headers)).status_code == 204
    for method, suffix in (("get", ""), ("patch", ""), ("post", "/messages")):
        if method == "patch":
            response = await client.patch(path, headers=headers, json={"title": "隐藏记录"})
        elif method == "post":
            response = await client.post(
                path + suffix,
                json={"content": "不能继续发送"},
                headers={**headers, "Idempotency-Key": "after-delete"},
            )
        else:
            response = await client.get(path + suffix, headers=headers)
        assert response.status_code == 404
    listing = await client.get(
        "/v1/conversations", headers=headers, params={"agent_id": str(agent_id)}
    )
    assert listing.json()["items"] == [] and listing.json()["total"] == 0
    for suffix in ("", "/events"):
        assert (await client.get(f"/v1/runs/{run_id}{suffix}", headers=headers)).status_code == 404
    assert (await client.post(f"/v1/runs/{run_id}/cancel", headers=headers)).status_code == 404
    assert (await client.delete(path, headers=headers)).status_code == 404
    admin = await auth_headers(
        engine, settings, tenant_id, subject="auditor", roles=("platform_admin",)
    )
    for params in ({}, {"user_id": str(owner_id)}, {"agent_id": str(agent_id)}):
        diagnostics = await client.get("/v1/admin/dashboard/runs", headers=admin, params=params)
        assert diagnostics.status_code == 200
        assert diagnostics.json()["total"] == len(diagnostics.json()["items"]) == 1
        assert diagnostics.json()["items"][0]["id"] == str(run_id)
    async with factory() as session:
        assert await session.get(Conversation, conversation_id) is None
        retained = await session.scalar(
            select(Conversation)
            .where(Conversation.id == conversation_id)
            .execution_options(include_deleted=True)
        )
        assert retained is not None and retained.deleted_at is not None
        run = await session.get(AgentRun, run_id)
        assert run is not None
        expected = {
            RunStatus.QUEUED: RunStatus.CANCELLED,
            RunStatus.RUNNING: RunStatus.CANCELLING,
            RunStatus.FAILED: RunStatus.FAILED,
        }[run_status]
        assert run.status == expected
        assert await claim_run(session, run_id) is None
        for resource in (AgentErrorEvent, AgentRunObservation):
            assert (
                await session.scalar(
                    select(func.count()).select_from(resource).where(resource.run_id == run_id)
                )
                == 1
            )


async def test_model_delete_preserves_all_dependencies_and_allows_name_reuse(
    client: AsyncClient, engine: AsyncEngine, settings: Settings
) -> None:
    tenant_id = uuid4()
    headers = await auth_headers(engine, settings, tenant_id, roles=("platform_admin",))
    payload = endpoint_payload()
    created = await client.post("/v1/admin/model-endpoints", headers=headers, json=payload)
    assert created.status_code == 201
    endpoint_id = UUID(created.json()["id"])
    factory = create_session_factory(engine)
    async with factory() as session, session.begin():
        model = await session.scalar(
            select(ModelEndpointModel).where(ModelEndpointModel.endpoint_id == endpoint_id)
        )
        assert model is not None and model.current_version_id is not None
        credential = await session.scalar(
            select(ModelCredential).where(ModelCredential.endpoint_id == endpoint_id)
        )
        assert credential is not None
        endpoint = await session.get(ModelEndpoint, endpoint_id)
        assert endpoint is not None
        session.add(
            ModelEndpointTestRun(
                tenant_id=tenant_id,
                endpoint_id=endpoint_id,
                endpoint_model_id=model.id,
                model_version_id=model.current_version_id,
                credential_revision=credential.revision,
                config_digest="test",
                status=ModelTestStatus.FAILED,
                created_by=endpoint.created_by,
                correlation_id="test",
            )
        )
        version_id = model.current_version_id
        credential_id = credential.id
    path = f"/v1/admin/model-endpoints/{endpoint_id}"
    assert (await client.delete(path, headers=headers)).status_code == 204
    assert (await client.get(path, headers=headers)).status_code == 404
    listing = await client.get("/v1/admin/model-endpoints", headers=headers)
    assert listing.json()["total"] == 0
    replacement = await client.post("/v1/admin/model-endpoints", headers=headers, json=payload)
    assert replacement.status_code == 201 and replacement.json()["id"] != str(endpoint_id)
    async with factory() as session:
        for resource, condition in (
            (ModelEndpoint, ModelEndpoint.id == endpoint_id),
            (ModelEndpointModel, ModelEndpointModel.endpoint_id == endpoint_id),
            (ModelEndpointVersion, ModelEndpointVersion.id == version_id),
            (ModelEndpointTestRun, ModelEndpointTestRun.endpoint_id == endpoint_id),
            (ModelCredential, ModelCredential.id == credential_id),
            (ModelCredentialRevision, ModelCredentialRevision.credential_id == credential_id),
        ):
            assert await session.scalar(select(resource).where(condition)) is None
            history = list(
                (
                    await session.scalars(
                        select(resource).where(condition).execution_options(include_deleted=True)
                    )
                ).all()
            )
            assert history and all(record.deleted_at is not None for record in history)
        old_model = await session.scalar(
            select(ModelEndpointModel)
            .where(ModelEndpointModel.endpoint_id == endpoint_id)
            .execution_options(include_deleted=True)
        )
        assert old_model is not None and old_model.current_version_id == version_id
        assert (
            await session.scalar(
                select(func.count())
                .select_from(AgentAuditEvent)
                .where(AgentAuditEvent.model_endpoint_id == endpoint_id)
            )
            > 0
        )


async def test_department_delete_filters_tree_parent_checks_and_unique_constraints(
    client: AsyncClient, engine: AsyncEngine, settings: Settings
) -> None:
    tenant_id = uuid4()
    headers = await auth_headers(engine, settings, tenant_id, roles=("platform_admin",))
    path = "/v1/admin/organization-units"
    parent = await client.post(path, headers=headers, json={"name": "上级"})
    assert parent.status_code == 201
    payload = {"name": "可重新创建", "parent_id": parent.json()["id"]}
    child = await client.post(path, headers=headers, json=payload)
    assert child.status_code == 201
    child_id = UUID(child.json()["id"])
    assert (await client.delete(f"{path}/{child_id}", headers=headers)).status_code == 204
    tree = (await client.get(path, headers=headers)).json()
    assert tree["items"][0]["children"] == []
    replacement = await client.post(path, headers=headers, json=payload)
    assert replacement.status_code == 201
    assert replacement.json()["id"] != str(child_id)
    assert (
        await client.post(
            path, headers=headers, json={"name": "非法上级", "parent_id": str(child_id)}
        )
    ).status_code == 404
    assert (
        await client.delete(f"{path}/{replacement.json()['id']}", headers=headers)
    ).status_code == 204
    assert (
        await client.delete(f"{path}/{parent.json()['id']}", headers=headers)
    ).status_code == 204
    factory = create_session_factory(engine)
    async with factory() as session:
        assert await session.scalar(select(func.count()).select_from(OrganizationUnit)) == 0
        alias = aliased(OrganizationUnit)
        assert await session.scalar(select(func.count()).select_from(alias)) == 0
        subquery = select(OrganizationUnit.id).subquery()
        assert await session.scalar(select(func.count()).select_from(subquery)) == 0
        assert (
            await session.scalar(
                select(func.count())
                .select_from(OrganizationUnit)
                .execution_options(include_deleted=True)
            )
            == 3
        )


async def test_revoked_agent_grants_are_retained_and_no_longer_authorize(
    client: AsyncClient, engine: AsyncEngine, settings: Settings
) -> None:
    tenant_id = uuid4()
    agent_id = await provision_agent(engine, settings, tenant_id)
    employee = await auth_headers(engine, settings, tenant_id)
    admin = await auth_headers(
        engine, settings, tenant_id, subject="admin", roles=("platform_admin",)
    )
    path = f"/v1/admin/agents/{agent_id}/grants"
    assert (await client.get(f"/v1/agents/{agent_id}", headers=employee)).status_code == 200
    assert (await client.put(path, headers=admin, json={"grants": []})).status_code == 200
    assert (await client.get(f"/v1/agents/{agent_id}", headers=employee)).status_code == 404
    assert (await client.get(path, headers=admin)).json()["total"] == 0
    for _ in range(2):
        response = await client.put(
            path,
            headers=admin,
            json={"grants": [{"subject_type": "role", "subject_id": "customer"}]},
        )
        assert response.status_code == 200 and response.json()["total"] == 1
    assert (await client.get(f"/v1/agents/{agent_id}", headers=employee)).status_code == 200
    factory = create_session_factory(engine)
    async with factory() as session:
        assert await session.scalar(select(func.count()).select_from(AgentAccessGrant)) == 1
        assert (
            await session.scalar(
                select(func.count())
                .select_from(AgentAccessGrant)
                .execution_options(include_deleted=True)
            )
            == 3
        )


async def test_physical_business_deletion_is_rejected(
    engine: AsyncEngine, settings: Settings
) -> None:
    tenant_id = uuid4()
    await auth_headers(engine, settings, tenant_id)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session, session.begin():
        unit = OrganizationUnit(tenant_id=tenant_id, name="保留记录")
        session.add(unit)
        await session.flush()
        unit_id = unit.id
    async with factory() as session:
        unit = await session.get(OrganizationUnit, unit_id)
        assert unit is not None
        await session.delete(unit)
        with pytest.raises(ValueError, match="禁止物理删除"):
            await session.flush()
        await session.rollback()
        with pytest.raises(ValueError, match="禁止物理删除"):
            await session.execute(delete(OrganizationUnit).where(OrganizationUnit.id == unit_id))
    async with factory() as session:
        assert await session.get(OrganizationUnit, unit_id) is not None
