"""统一目录、静态最低权限、Agent 绑定与参数风险的服务端裁决。"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from supportops_core.agent_config import TOOL_REGISTRY, AgentConfigV2, pinned_tool_version
from supportops_core.auth import IdentityContext
from supportops_core.enums import ApprovalPolicy
from supportops_core.models import ToolCatalogEntry


class ToolPolicyError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def legacy_risk_number(value: str) -> int:
    """保守兼容旧风险：low→R1、medium→R3、high/未知→R4。"""
    return {"low": 1, "medium": 3, "high": 4}.get(value, 4)


def argument_risk_number(base: int, arguments: Mapping[str, object]) -> int:
    if arguments.get("export") is True or arguments.get("bulk") is True or arguments.get("scope") == "all":
        base = max(base, 3)
    operation = arguments.get("operation")
    if (isinstance(operation, str) and operation in {"delete", "refund", "disable", "reset"}) or arguments.get("force") is True:
        base = max(base, 4)
    return base


@dataclass(frozen=True, slots=True)
class ResolvedToolPolicy:
    tool_id: str
    implementation_version: int
    catalog_version: int
    risk_number: int
    max_calls_per_run: int
    approval_required: bool
    action_proposal_only: bool = False


async def resolve_tool_policy(
    session: AsyncSession, *, identity: IdentityContext, config: AgentConfigV2,
    allowed_tool_ids: Sequence[str], tool_id: str, arguments: Mapping[str, object],
    implementation_versions: Mapping[str, int] | None = None,
) -> ResolvedToolPolicy:
    definition = TOOL_REGISTRY.get(tool_id)
    binding = next((item for item in config.tools if item.tool_id == tool_id and item.enabled), None)
    if definition is None or binding is None or tool_id not in allowed_tool_ids:
        raise ToolPolicyError("TOOL_NOT_BOUND", "工具未注册或不在固定 Agent 版本的允许范围")
    if pinned_tool_version(implementation_versions, tool_id) != definition.version:
        raise ToolPolicyError("TOOL_IMPLEMENTATION_VERSION_UNAVAILABLE", "固定工具实现版本当前不可用，不能静默换用新版")
    catalog = await session.scalar(select(ToolCatalogEntry).where(
        ToolCatalogEntry.tenant_id == identity.principal.tenant_id, ToolCatalogEntry.tool_id == tool_id,
    ))
    if catalog is None or not catalog.is_enabled or catalog.is_revoked:
        raise ToolPolicyError("TOOL_DISABLED_OR_REVOKED", "工具已停用或被紧急撤销")
    if catalog.implementation_key != definition.id:
        raise ToolPolicyError("TOOL_IMPLEMENTATION_MISMATCH", "工具目录实现与服务端注册不一致")
    roles = frozenset(identity.principal.roles)
    if ((definition.required_roles and not roles.intersection(definition.required_roles))
        or (catalog.required_roles and not roles.intersection(catalog.required_roles))):
        raise ToolPolicyError("TOOL_ACCESS_DENIED", "当前角色不允许调用此工具")
    risk = argument_risk_number(max(definition.minimum_risk_number, legacy_risk_number(definition.risk_level), legacy_risk_number(catalog.risk_level)), arguments)
    return ResolvedToolPolicy(tool_id, definition.version, catalog.version, risk,
                              binding.max_calls_per_run, risk >= 3 or binding.approval_policy == ApprovalPolicy.REQUIRED,
                              definition.action_proposal_only)
