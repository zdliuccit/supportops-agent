"""发布清单只记录服务器生成的版本/摘要；不授予运行权限，不替代评测门禁。"""

import hashlib
import json
from importlib.metadata import PackageNotFoundError, version as package_version
from pathlib import Path
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import Field, TypeAdapter, ValidationError, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from supportops_core.agent_config import AgentConfigV2, StrictModel, TOOL_REGISTRY, agent_config_digest
from supportops_core.config import Settings
from supportops_core.models import AgentAccessGrant, AgentVersion, ModelEndpointVersion, ToolCatalogEntry
from supportops_core.tool_policy import legacy_risk_number
from supportops_core.model_routing_config_v3 import (
    FixedModelBindingV3, RoutingCandidatePublishRequest, RoutingConfigResolution, RoutingConfigResolveRequest,
    parse_agent_config_v3,
)
from supportops_core.model_routing_policy_contracts import RoutingPolicyVersionRecord


class ReleaseManifestUnavailable(ValueError):
    """缺少已注册实现或源码时拒绝生成伪完整清单。"""


class ReleaseToolReference(StrictModel):
    tool_id: str
    implementation_version: int = Field(ge=1)
    schema_digest: str
    description_digest: str
    static_policy_digest: str
    catalog_version: int | None = None
    catalog_digest: str | None = None
    binding_digest: str
    minimum_risk: str
    connector: Literal["local_identity_v1", "keyword_v1", "mock_ticket_read_v2", "mock_429_query_v1", "mock_ticket_action_v1"]


class ReleaseModelReference(StrictModel):
    endpoint_id: UUID
    model_version_id: UUID
    model_record_id: UUID | None
    protocol: str
    settings_digest: str
    credential_reference_digest: str
    credential_revision_at_publish: int = Field(ge=1)
    credential_binding: Literal["repinned_at_run_creation"] = "repinned_at_run_creation"
    routing: Literal["fixed_model_v1"] = "fixed_model_v1"


class ReleaseKnowledgeReference(StrictModel):
    strategy: Literal["keyword_v1"] = "keyword_v1"
    enabled: bool
    binding_digest: str
    scope: Literal["current_authorized_snapshots_at_retrieval"] = "current_authorized_snapshots_at_retrieval"
    evidence_binding: Literal["run_attempt_snapshot_chunk_digest"] = "run_attempt_snapshot_chunk_digest"
    vector_enabled: Literal[False] = False
    corpus_version: None = None


class ReleaseAuthorizationReference(StrictModel):
    boundary: Literal["current_database_at_each_boundary"] = "current_database_at_each_boundary"
    grant_digest_at_publish: str
    grant_count_at_publish: int = Field(ge=0)
    approval_policy_digest: str
    governance_settings_digest: str
    snapshot_is_execution_permit: Literal[False] = False


class ReleaseEvaluationReference(StrictModel):
    status: Literal["not_assessed"] = "not_assessed"
    dataset_version: None = None
    result_id: None = None
    gate_passed: Literal[False] = False


class _ReleaseManifestFields(StrictModel):
    agent_id: UUID
    agent_version_id: UUID
    config_digest: str
    prompt_digest: str
    engine: Literal["langchain_create_agent_v1"] = "langchain_create_agent_v1"
    graph_policy_digest: str
    context_schema_digest: str
    response_schema_digest: str
    source_digests: dict[str, str]
    dependencies: dict[str, str | None]
    model: ReleaseModelReference
    tools: list[ReleaseToolReference] = Field(max_length=30)
    knowledge: ReleaseKnowledgeReference
    authorization: ReleaseAuthorizationReference
    evaluation: ReleaseEvaluationReference = Field(default_factory=ReleaseEvaluationReference)


class AgentReleaseManifest(_ReleaseManifestFields):
    schema_version: Literal["agent_release_manifest_v1"] = "agent_release_manifest_v1"


class RoutingCandidateOrigin(StrictModel):
    submission_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_draft_revision: int = Field(ge=1, strict=True)
    expected_latest_version_number: int = Field(ge=0, strict=True)
    candidate_only: Literal[True] = True
    runtime_supported_at_publication: Literal[False] = False


class ResolvedModelRoutingManifest(StrictModel):
    schema_version: Literal["resolved_model_routing_v1"] = "resolved_model_routing_v1"
    binding_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    primary_candidate_id: UUID | None
    policy_snapshot: RoutingPolicyVersionRecord | None
    resolution: RoutingConfigResolution

    @model_validator(mode="after")
    def complete_snapshot(self) -> "ResolvedModelRoutingManifest":
        resolution = self.resolution
        if resolution.status != "resolved" or resolution.resolved_models is None:
            raise ValueError("清单只能保存完整成功的引用解析观察")
        if resolution.mode == "fixed_model_v1":
            if self.primary_candidate_id is not None or self.policy_snapshot is not None:
                raise ValueError("固定模式不能包含路由政策快照")
        else:
            policy = self.policy_snapshot
            if (policy is None or policy.version_id != resolution.policy_version_id
                or policy.tenant_id != resolution.tenant_id
                or manifest_digest(policy.model_dump(mode="json")) != resolution.policy_digest
                or self.primary_candidate_id != policy.definition.primary_candidate_id
                or manifest_digest([row.model_dump(mode="json") for row in policy.definition.candidates])
                    != resolution.candidate_set_digest
                or [row.candidate_id for row in policy.definition.candidates]
                    != [row.candidate_id for row in resolution.resolved_models]):
                raise ValueError("路由政策及完整候选解析必须绑定原快照")
            for candidate, reference in zip(policy.definition.candidates, resolution.resolved_models, strict=True):
                if (candidate.endpoint_id != reference.endpoint_id or candidate.endpoint_model_id != reference.endpoint_model_id
                    or candidate.model_version_id != reference.model_version_id
                    or candidate.credential_revision != reference.credential_revision_at_resolution
                    or candidate.model_request_digest != reference.model_request_digest
                    or candidate.capability_digest != reference.capability_digest or candidate.pricing_digest != reference.pricing_digest):
                    raise ValueError("固定政策模型、修订和摘要不能被解析引用替换")
        return self


class AgentReleaseManifestV2(_ReleaseManifestFields):
    schema_version: Literal["agent_release_manifest_v2"] = "agent_release_manifest_v2"
    model_routing: ResolvedModelRoutingManifest
    candidate_origin: RoutingCandidateOrigin

    @model_validator(mode="after")
    def original_primary_and_tools(self) -> "AgentReleaseManifestV2":
        resolution = self.model_routing.resolution
        references = resolution.resolved_models or ()
        primary = next((row for row in references if row.candidate_id == self.model_routing.primary_candidate_id), None)
        if (resolution.agent_id != self.agent_id or resolution.config_digest != self.config_digest
            or primary is None or primary.model_version_id != self.model.model_version_id
            or primary.endpoint_id != self.model.endpoint_id or primary.endpoint_model_id != self.model.model_record_id
            or primary.credential_revision_at_resolution != self.model.credential_revision_at_publish
            or resolution.resolved_tool_versions != {row.tool_id: row.implementation_version for row in self.tools}
            or len(self.tools) != len(resolution.resolved_tool_versions)):
            raise ValueError("清单主模型、完整引用与工具实现不一致")
        return self


class ReleaseComponentDifference(StrictModel):
    component: str
    status: Literal["equal", "changed", "added", "removed", "unknown"]
    before: str | None = None
    after: str | None = None


class AgentReleaseComparison(StrictModel):
    schema_version: Literal["agent_release_comparison_v1"] = "agent_release_comparison_v1"
    agent_id: UUID
    version_id: UUID
    baseline_version_id: UUID | None
    observed_at: datetime
    status: Literal["available", "no_baseline"]
    version_manifest_status: Literal["recorded", "legacy_unrecorded", "integrity_failed"]
    baseline_manifest_status: Literal["recorded", "legacy_unrecorded", "integrity_failed"] | None
    differences: list[ReleaseComponentDifference] = Field(default_factory=list, max_length=512)
    changed_count: int = Field(default=0, ge=0)
    unknown_count: int = Field(default=0, ge=0)
    comparison_is_release_approval: Literal[False] = False


def compare_release_versions(version: AgentVersion, baseline: AgentVersion | None) -> AgentReleaseComparison:
    status, current = project_release_manifest(version)
    if baseline is None:
        return AgentReleaseComparison(agent_id=version.agent_id, version_id=version.id, baseline_version_id=None,
            observed_at=datetime.now(UTC), status="no_baseline", version_manifest_status=status, baseline_manifest_status=None)
    if baseline.tenant_id != version.tenant_id or baseline.agent_id != version.agent_id:
        raise ReleaseManifestUnavailable("对比版本不属于同一 Agent 和租户")
    baseline_status, previous = project_release_manifest(baseline)
    def components(row: AgentVersion, manifest: AgentReleaseManifest | AgentReleaseManifestV2 | None) -> dict[str, str | None]:
        values: dict[str, str | None] = {"config": row.config_digest, "model_version": str(row.model_endpoint_version_id),
            "tool_implementations": manifest_digest(row.resolved_tool_versions)}
        if manifest is not None:
            if isinstance(manifest, AgentReleaseManifestV2):
                values.update({"model_routing": manifest_digest(manifest.model_routing.model_dump(mode="json")),
                    "routing_candidate_set": manifest.model_routing.resolution.candidate_set_digest,
                    "routing_binding": manifest.model_routing.binding_digest})
            values.update({"prompt": manifest.prompt_digest, "graph_policy": manifest.graph_policy_digest,
                "context_schema": manifest.context_schema_digest, "response_schema": manifest.response_schema_digest,
                "model_settings": manifest.model.settings_digest, "knowledge_binding": manifest.knowledge.binding_digest,
                "knowledge_strategy": manifest.knowledge.strategy, "grant_snapshot": manifest.authorization.grant_digest_at_publish,
                "approval_policy": manifest.authorization.approval_policy_digest, "governance_settings": manifest.authorization.governance_settings_digest,
                "dependency_versions": manifest_digest(manifest.dependencies)})
            values.update({"source." + name: digest for name, digest in manifest.source_digests.items()})
            for tool in manifest.tools:
                values.update({f"tool.{tool.tool_id}.schema": tool.schema_digest,
                    f"tool.{tool.tool_id}.description": tool.description_digest,
                    f"tool.{tool.tool_id}.binding": tool.binding_digest,
                    f"tool.{tool.tool_id}.static_policy": tool.static_policy_digest,
                    f"tool.{tool.tool_id}.catalog": tool.catalog_digest})
        return values
    before, after = components(baseline, previous), components(version, current)
    differences = []
    for key in sorted(before.keys() | after.keys()):
        old, new = before.get(key), after.get(key)
        change: Literal["equal", "changed", "added", "removed", "unknown"] = "unknown" if old is None or new is None else "equal" if old == new else "changed"
        if previous is not None and current is not None:
            change = "added" if key not in before else "removed" if key not in after else change
        differences.append(ReleaseComponentDifference(component=key, status=change, before=old, after=new))
    # 工具是否存在可由旧固定绑定明确判断，不能因为旧 manifest 缺失而把新增/删除说成未知。
    old_tools, new_tools = set(baseline.resolved_tool_ids), set(version.resolved_tool_ids)
    for tool_id in sorted(old_tools ^ new_tools):
        differences.append(ReleaseComponentDifference(component="tool_binding." + tool_id,
            status="added" if tool_id in new_tools else "removed", before="bound" if tool_id in old_tools else None,
            after="bound" if tool_id in new_tools else None))
    return AgentReleaseComparison(agent_id=version.agent_id, version_id=version.id, baseline_version_id=baseline.id,
        observed_at=datetime.now(UTC), status="available", version_manifest_status=status, baseline_manifest_status=baseline_status,
        differences=differences, changed_count=sum(row.status in {"changed", "added", "removed"} for row in differences),
        unknown_count=sum(row.status == "unknown" for row in differences))


def manifest_digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def project_release_manifest(version: AgentVersion) -> tuple[Literal["recorded", "legacy_unrecorded", "integrity_failed"], AgentReleaseManifest | AgentReleaseManifestV2 | None]:
    """只投影已有历史清单，不在 GET 重建或修改发布事实。"""
    if version.release_manifest is None and version.release_manifest_digest is None:
        return "legacy_unrecorded", None
    try:
        manifest = (AgentReleaseManifestV2.model_validate(version.release_manifest) if version.schema_version == "3"
            else AgentReleaseManifest.model_validate(version.release_manifest))
        if isinstance(manifest, AgentReleaseManifestV2):
            config = parse_agent_config_v3(version.config)
            if (manifest_digest(config.model_dump(mode="json")) != version.config_digest
                or manifest_digest(config.model.model_dump(mode="json")) != manifest.model_routing.binding_digest
                or config.model.mode != manifest.model_routing.resolution.mode
                or manifest.model_routing.resolution.tenant_id != version.tenant_id
                or manifest.candidate_origin.expected_latest_version_number + 1 != version.version_number):
                raise ValueError()
            resolution = manifest.model_routing.resolution
            if isinstance(config.model, FixedModelBindingV3):
                if (config.model.model_endpoint_id != manifest.model.endpoint_id
                    or config.model.model_endpoint_model_id != manifest.model.model_record_id
                    or config.model.model_endpoint_version_id != manifest.model.model_version_id):
                    raise ValueError()
            elif (config.model.policy_version_id != resolution.policy_version_id
                or config.model.policy_digest != resolution.policy_digest):
                raise ValueError()
            else:
                policy = manifest.model_routing.policy_snapshot
                if (policy is None or config.model.purpose not in policy.definition.allowed_purposes
                    or not set(config.model.allowed_regions) <= set(policy.definition.allowed_regions)
                    or config.model.data_classification not in policy.definition.allowed_data_classifications):
                    raise ValueError()
            submitted = RoutingCandidatePublishRequest(version_id=version.id,
                expected_draft_revision=manifest.candidate_origin.expected_draft_revision,
                expected_latest_version_number=manifest.candidate_origin.expected_latest_version_number,
                request=RoutingConfigResolveRequest(agent_id=version.agent_id,
                    base_agent_version_id=resolution.base_agent_version_id,
                    expected_base_config_digest=resolution.base_config_digest, config=config),
                release_notes=version.release_notes)
            if manifest_digest(submitted.model_dump(mode="json")) != manifest.candidate_origin.submission_digest:
                raise ValueError()
        tool_versions = {tool.tool_id: tool.implementation_version for tool in manifest.tools}
        if (manifest_digest(manifest.model_dump(mode="json")) != version.release_manifest_digest
            or manifest.agent_id != version.agent_id or manifest.agent_version_id != version.id
            or manifest.config_digest != version.config_digest
            or manifest.model.model_version_id != version.model_endpoint_version_id
            or len(tool_versions) != len(manifest.tools)
            or sorted(tool_versions) != sorted(version.resolved_tool_ids)
            or tool_versions != version.resolved_tool_versions):
            return "integrity_failed", None
        return "recorded", manifest
    except (ValidationError, TypeError, ValueError):
        return "integrity_failed", None


def _source_digests() -> dict[str, str]:
    # 只读取包内明确的源码；不扫描工作区、环境变量、凭据或用户文件。
    modules = ("agent_runtime", "worker_runtime", "tool_gateway", "tool_policy", "resource_policy",
        "run_attempt_services", "model_budget_services", "context_services", "history_context_services", "diagnostic_workflow",
        "diagnostic_mock_services", "approval_services", "action_runtime", "knowledge_retrieval", "knowledge_index_services",
        "support_result_services", "release_manifest_services", "release_gate_services", "agent_services",
        "evaluation_runner", "evaluation_failure_contracts", "evaluation_failure_services",
        "model_routing_contracts", "model_routing_eligibility", "model_routing_policy_contracts",
        "model_routing_policy_services", "model_routing_reservation_contracts", "model_routing_reservations",
        "model_routing_config_v3", "model_routing_resolution",
        "model_routing_release_history", "model_routing_release_services",
        "model_routing_accounting_contracts", "model_routing_accounting_guards", "model_routing_settlements",
        "model_routing_failover_contracts", "model_routing_failover",
        "model_routing_circuit_contracts", "model_routing_circuit_history", "model_routing_circuit_store",
        "model_routing_failover_reservation_contracts", "model_routing_failover_reservations",
        "model_routing_dispatch_contracts", "model_routing_dispatch_history", "model_routing_dispatch_services",
        "provider_answer_requests", "model_routing_request_contracts", "model_routing_request_services",
        "model_context_boundary", "model_routing_runtime_input",
        "provider_sdk_transport", "model_routing_sdk_executor",
        "model_routing_coordination_contracts", "model_routing_coordinator",
        "model_routing_execution_contracts", "model_routing_execution_source",
        "model_routing_result_services",
        "product_message_services", "model_routing_product_contracts", "model_routing_product_services",
        "model_routing_checkpoint",
        "model_routing_product_runtime",
        "model_routing_run_lifecycle",
        "model_routing_run_request", "run_input_context",
        "model_routing_worker", "model_routing_worker_factory",
        "specialist_task_contracts", "specialist_scheduler_contracts", "specialist_scheduler",
        "specialist_context_contracts", "specialist_context_services",
        "specialist_business_context_contracts", "specialist_business_context_services",
        "specialist_business_request_services",
        "specialist_material_contracts", "specialist_material_services", "specialist_runtime",
        "specialist_budget_contracts", "specialist_budget_services",
        "specialist_request_contracts", "specialist_request_services",
        "specialist_dispatch_contracts", "specialist_dispatch_history", "specialist_dispatch_services",
        "specialist_admission_contracts", "specialist_admission_services",
        "specialist_response_contracts", "specialist_response_services",
        "specialist_execution_contracts", "specialist_execution_cipher", "specialist_execution_history", "specialist_execution_store",
        "specialist_sdk_response", "specialist_sdk_transport", "specialist_sdk_executor",
        "specialist_accounting_contracts", "specialist_accounting_guards", "specialist_accounting_services",
        "action_ledger_services", "mock_action_connector", "action_proposal_services", "credential_services",
        "release_rollout_contracts", "release_rollout_observations", "release_rollout_services", "tool_result_services", "tool_context_services", "ticket_services",
        "compensation_contracts", "compensation_services", "knowledge_ingestion_contracts",
        "knowledge_ingestion_services", "knowledge_ingestion_review", "knowledge_markdown_source", "knowledge_content", "knowledge_parser_isolated", "knowledge_reader_isolated",
        "knowledge_services", "knowledge_ingestion_governance", "knowledge_ingestion_runtime", "knowledge_ingestion_approval",
        "knowledge_ingestion_preflight", "knowledge_ingestion_client", "knowledge_ingestion_cli",
        "knowledge_ingestion_dispatch",
        "knowledge_access_services", "escalation_services", "db", "reference_history",
        "run_outbox_services", "reference_history_schema", "schema_structure", "retention_contracts",
        "retention_inventory", "retention_services", "retention_batch_contracts", "retention_batch_runtime",
        "retention_batch_events", "retention_batch_store", "retention_tombstone_snapshot",
        "retention_propagation_contracts", "retention_propagation", "retention_propagation_audit",
        "retention_knowledge_propagation", "retention_knowledge_batch",
        "knowledge_quarantine_policy", "retention_knowledge_executor", "retention_knowledge_runtime",
        "retention_job", "retention_knowledge_job",
        "retention_checkpoint_propagation",
        "retention_specialist_propagation",
        "retention_access_propagation",
        "retention_access_batch",
        "retention_dispatch", "retention_access_executor",
        "retention_access_runtime", "retention_access_job",
        "restore_startup_barrier",
        "restore_session_contracts", "restore_session_reapplication", "restore_session_history", "restore_session_audit",
        "restore_knowledge_contracts", "restore_knowledge_reapplication", "restore_knowledge_audit",
        "restore_credential_contracts", "restore_credential_reapplication", "restore_credential_audit",
        "restore_review_contracts", "restore_review",
        "restore_review_artifacts",
        "restore_review_capture",
        "models", "model_services", "secrets",
        "tool_discovery_contracts", "tool_discovery_services",
        "mcp_isolated_contracts", "mcp_isolated_runtime", "mcp_isolated_server", "mcp_isolated_client", "mcp_isolated_cli", "mcp_isolated_ipc",
        "tool_connection_contracts", "tool_connection_state", "tool_connection_services", "tool_connection_worker",
        "mcp_http_authorization_contracts", "mcp_http_authorization", "mcp_http_client", "mcp_http_audit", "mcp_http_broker", "mcp_http_broker_ipc",
        "mcp_http_executor_contracts", "mcp_http_executor_runtime", "mcp_http_executor_cli", "mcp_http_executor_ipc",
        "mcp_http_dispatch_services", "mcp_http_dispatch_queries", "mcp_http_jwt", "outbound_http", "enums")
    try:
        return {name: hashlib.sha256(Path(__file__).with_name(name + ".py").read_bytes()).hexdigest() for name in modules}
    except OSError as exc:
        raise ReleaseManifestUnavailable("发布清单所需源码不可用，不能伪造运行图版本") from exc


async def build_release_manifest(session: AsyncSession, *, agent_id: UUID, agent_version_id: UUID,
    tenant_id: UUID, config: AgentConfigV2, model: ModelEndpointVersion, credential_revision: int,
    tool_ids: list[str], settings: Settings,
) -> AgentReleaseManifest:
    # 延迟导入避免 agent_services / runtime 的既有依赖环；不创建模型客户端或联网。
    from supportops_core.agent_runtime import RUNTIME_TOOLS, _PLATFORM_DATA_POLICY
    from supportops_core.agent_config import SupportAnswer, SupportContext
    catalogs = {row.tool_id: row for row in (await session.scalars(select(ToolCatalogEntry)
        .where(ToolCatalogEntry.tenant_id == tenant_id, ToolCatalogEntry.tool_id.in_(tool_ids))
        .order_by(ToolCatalogEntry.tool_id).with_for_update(read=True))).all()}
    bindings = {binding.tool_id: binding for binding in config.tools if binding.enabled}
    tools: list[ReleaseToolReference] = []
    for tool_id in sorted(tool_ids):
        definition, runtime_tool, binding = TOOL_REGISTRY.get(tool_id), RUNTIME_TOOLS.get(tool_id), bindings.get(tool_id)
        if definition is None or runtime_tool is None or binding is None:
            raise ReleaseManifestUnavailable("发布清单存在未注册或未绑定工具")
        catalog = catalogs.get(tool_id)
        if catalog and (not catalog.is_enabled or catalog.is_revoked or catalog.implementation_key != definition.id):
            raise ReleaseManifestUnavailable("工具目录在发布期间变化，需重新校验")
        static = {"required_roles": sorted(definition.required_roles), "risk_level": definition.risk_level,
            "minimum_risk_number": definition.minimum_risk_number, "action_proposal_only": definition.action_proposal_only}
        current = {"required_roles": sorted(catalog.required_roles), "risk_level": catalog.risk_level,
            "is_enabled": catalog.is_enabled, "is_revoked": catalog.is_revoked, "implementation_key": catalog.implementation_key} if catalog else None
        risk = max(definition.minimum_risk_number, legacy_risk_number(definition.risk_level),
            legacy_risk_number(catalog.risk_level) if catalog else 0)
        if risk >= 3 and binding.approval_policy.value != "required":
            raise ReleaseManifestUnavailable("工具风险在发布期间变化，需重新配置审批")
        connector: Literal["local_identity_v1", "keyword_v1", "mock_ticket_read_v2", "mock_429_query_v1", "mock_ticket_action_v1"] = "local_identity_v1" if tool_id == "current_identity_summary" else "keyword_v1" if tool_id == "search_knowledge" else "mock_ticket_read_v2" if tool_id == "support_ticket_lookup" else "mock_ticket_action_v1" if tool_id == "create_mock_ticket" else "mock_429_query_v1"
        tools.append(ReleaseToolReference(tool_id=tool_id, implementation_version=definition.version,
            schema_digest=manifest_digest(convert_to_openai_tool(runtime_tool)),
            description_digest=manifest_digest({"runtime": runtime_tool.description, "static": definition.description}),
            static_policy_digest=manifest_digest(static), catalog_version=catalog.version if catalog else None,
            catalog_digest=manifest_digest(current) if current else None, binding_digest=manifest_digest(binding.model_dump(mode="json")),
            minimum_risk=f"R{risk}", connector=connector))
    grants = (await session.execute(select(AgentAccessGrant.subject_type, AgentAccessGrant.subject_id)
        .where(AgentAccessGrant.tenant_id == tenant_id, AgentAccessGrant.agent_id == agent_id)
        .order_by(AgentAccessGrant.subject_type, AgentAccessGrant.subject_id))).all()
    sources = _source_digests()
    dependencies: dict[str, str | None] = {}
    for package in ("supportops-core", "langchain", "langchain-core", "langchain-openai", "langgraph", "langgraph-checkpoint-postgres"):
        try:
            dependencies[package] = package_version(package)
        except PackageNotFoundError:
            dependencies[package] = None
    governance_names = ("diagnostic_mock_enabled", "business_action_mock_enabled", "business_action_mock_auto_create_enabled", "tool_connection_mock_enabled",
        "approval_ttl_seconds", "tenant_run_limit_per_day", "tenant_tool_limit_per_day", "tenant_max_active_runs",
        "tenant_model_call_limit_per_day", "tenant_model_cost_usd_per_day", "tool_argument_max_bytes", "tool_result_max_bytes",
        "agent_default_max_input_tokens", "agent_context_history_turns", "run_attempt_max_recoveries",
        "action_max_executions", "action_max_reconciliations", "action_max_manual_reconciliations")
    model_settings = {"protocol": model.api_protocol.value, "remote_model_name": model.remote_model_name,
        "capabilities": model.capabilities, "defaults": model.defaults, "pricing": model.pricing, "generation": config.model.generation.model_dump(mode="json")}
    return AgentReleaseManifest(agent_id=agent_id, agent_version_id=agent_version_id,
        config_digest=agent_config_digest(config), prompt_digest=manifest_digest({"platform": _PLATFORM_DATA_POLICY, "agent": config.prompt.system_prompt}),
        graph_policy_digest=manifest_digest({"engine": config.runtime.engine, "runtime": config.runtime.model_dump(mode="json"), "sources": sources}),
        context_schema_digest=manifest_digest(TypeAdapter(SupportContext).json_schema()), response_schema_digest=manifest_digest(SupportAnswer.model_json_schema()),
        source_digests=sources, dependencies=dependencies,
        model=ReleaseModelReference(endpoint_id=model.endpoint_id, model_version_id=model.id, model_record_id=model.endpoint_model_id,
            protocol=model.api_protocol.value, settings_digest=manifest_digest(model_settings),
            credential_reference_digest=manifest_digest(str(model.credential_id)), credential_revision_at_publish=credential_revision),
        tools=tools, knowledge=ReleaseKnowledgeReference(enabled="search_knowledge" in tool_ids,
            binding_digest=manifest_digest(config.knowledge.model_dump(mode="json"))),
        authorization=ReleaseAuthorizationReference(grant_digest_at_publish=manifest_digest([[str(kind), subject] for kind, subject in grants]),
            grant_count_at_publish=len(grants), approval_policy_digest=manifest_digest({"bindings": [tool.binding_digest for tool in tools], "policy": sources["approval_services"]}),
            governance_settings_digest=manifest_digest({name: getattr(settings, name) for name in governance_names})))
