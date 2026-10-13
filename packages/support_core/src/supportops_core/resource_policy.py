"""可信资源事实上的 RBAC/ABAC；模型参数不能声明自己的权限或资源归属。"""

from dataclasses import dataclass, field
from typing import Literal
from uuid import UUID

from supportops_core.auth import IdentityContext

ResourceKind = Literal["identity", "user", "department", "project", "ticket", "knowledge", "log", "quota", "route", "incident"]
ResourceAction = Literal["read", "write", "export", "propose"]


class ResourceAccessDenied(PermissionError):
    """资源不存在与无权限使用相同拒绝语义。"""


@dataclass(frozen=True, slots=True)
class TrustedResource:
    """只能由业务库/受控连接器构造，不能直接从工具参数反序列化。"""

    kind: ResourceKind
    tenant_id: UUID
    resource_id: str
    owner_user_id: UUID | None = None
    organization_unit_id: UUID | None = None
    department_visible: bool = False
    allowed_user_ids: frozenset[UUID] = field(default_factory=frozenset)
    allowed_roles: frozenset[str] = field(default_factory=frozenset)
    allowed_actions: frozenset[ResourceAction] = field(default_factory=lambda: frozenset({"read"}))
    visible_fields: frozenset[str] = field(default_factory=frozenset)


def authorize_resource(
    identity: IdentityContext, resource: TrustedResource | None, *, action: ResourceAction,
    fields: frozenset[str] = frozenset(),
) -> frozenset[str]:
    """同租户、主体范围、动作与字段必须同时成立；管理员不自动扩权。"""
    if resource is None or identity.user.status != "active" or resource.tenant_id != identity.principal.tenant_id:
        raise ResourceAccessDenied("资源不可访问")
    subject_allowed = (
        resource.owner_user_id == identity.user.id
        or identity.user.id in resource.allowed_user_ids
        or bool(resource.allowed_roles.intersection(identity.principal.roles))
        or (resource.department_visible and resource.organization_unit_id is not None
            and resource.organization_unit_id == identity.user.organization_unit_id)
    )
    if not subject_allowed or action not in resource.allowed_actions or not fields.issubset(resource.visible_fields):
        raise ResourceAccessDenied("资源不可访问")
    return resource.visible_fields if not fields else fields
