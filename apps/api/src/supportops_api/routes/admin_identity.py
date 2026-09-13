"""公司、组织架构和租户用户管理 API。"""

from __future__ import annotations

import re
from collections import defaultdict
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from supportops_core.auth import IdentityContext
from supportops_core.enums import ROLE_EMPLOYEE, ROLE_PLATFORM_ADMIN
from supportops_core.models import OrganizationUnit, Tenant, User, utc_now
from supportops_core.passwords import PasswordPolicyError, hash_password

from supportops_api.dependencies import current_identity, database_session
from supportops_api.pagination import PaginationParams, pagination_metadata, pagination_params
from supportops_api.schemas import (
    AdminPasswordReset,
    AdminUserCreate,
    AdminUserListResponse,
    AdminUserResponse,
    AdminUserUpdate,
    CompanyResponse,
    CompanyUpdate,
    OrganizationTreeResponse,
    OrganizationUnitCreate,
    OrganizationUnitResponse,
    OrganizationUnitUpdate,
)

router = APIRouter(prefix="/v1/admin", tags=["admin-identity"])
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
ALLOWED_ROLES = {ROLE_PLATFORM_ADMIN, ROLE_EMPLOYEE}


def _require_admin(identity: IdentityContext) -> None:
    if ROLE_PLATFORM_ADMIN not in identity.principal.roles:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="需要平台管理员权限")


def _normalize_email(email: str) -> str:
    value = email.strip().lower()
    if not EMAIL_PATTERN.fullmatch(value):
        raise HTTPException(status_code=422, detail="邮箱格式不正确")
    return value


async def _tenant_department(
    session: AsyncSession, tenant_id: UUID, unit_id: UUID | None
) -> OrganizationUnit | None:
    if unit_id is None:
        return None
    unit = await session.scalar(
        select(OrganizationUnit).where(
            OrganizationUnit.id == unit_id, OrganizationUnit.tenant_id == tenant_id
        )
    )
    if unit is None:
        raise HTTPException(status_code=404, detail="部门不存在")
    return unit


async def _ensure_department_name_available(
    session: AsyncSession,
    tenant_id: UUID,
    parent_id: UUID | None,
    name: str,
    *,
    exclude_id: UUID | None = None,
) -> None:
    """补足 NULL 上级不参与普通唯一约束时的同级部门名称校验。"""
    query = select(OrganizationUnit.id).where(
        OrganizationUnit.tenant_id == tenant_id,
        OrganizationUnit.parent_id == parent_id,
        OrganizationUnit.name == name,
    )
    if exclude_id is not None:
        query = query.where(OrganizationUnit.id != exclude_id)
    if await session.scalar(query) is not None:
        raise HTTPException(status_code=409, detail="同一上级部门下已存在同名部门")


async def _department_user_count(
    session: AsyncSession, tenant_id: UUID, unit_id: UUID
) -> int:
    """统计部门自身及全部后代部门的用户数量。"""
    descendants = (
        select(OrganizationUnit.id)
        .where(OrganizationUnit.id == unit_id, OrganizationUnit.tenant_id == tenant_id)
        .cte(name="department_descendants", recursive=True)
    )
    descendants = descendants.union_all(
        select(OrganizationUnit.id).where(
            OrganizationUnit.parent_id == descendants.c.id,
            OrganizationUnit.tenant_id == tenant_id,
        )
    )
    count = await session.scalar(
        select(func.count(User.id)).where(
            User.tenant_id == tenant_id,
            User.organization_unit_id.in_(select(descendants.c.id)),
        )
    )
    return int(count or 0)


async def _user_response(session: AsyncSession, user: User) -> AdminUserResponse:
    unit = await _tenant_department(session, user.tenant_id, user.organization_unit_id)
    return AdminUserResponse(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        organization_unit_id=user.organization_unit_id,
        organization_unit_name=unit.name if unit else None,
        job_title=user.job_title,
        phone=user.phone,
        roles=list(user.roles),
        status=user.status,
        last_login_at=user.last_login_at,
        created_at=user.created_at,
        updated_at=user.updated_at,
    )


@router.get("/company", response_model=CompanyResponse)
async def get_company(
    identity: IdentityContext = Depends(current_identity),
    session: AsyncSession = Depends(database_session),
) -> CompanyResponse:
    _require_admin(identity)
    tenant = await session.get(Tenant, identity.principal.tenant_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail="公司不存在")
    return CompanyResponse.model_validate(tenant, from_attributes=True)


@router.patch("/company", response_model=CompanyResponse)
async def update_company(
    payload: CompanyUpdate,
    identity: IdentityContext = Depends(current_identity),
    session: AsyncSession = Depends(database_session),
) -> CompanyResponse:
    _require_admin(identity)
    tenant = await session.get(Tenant, identity.principal.tenant_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail="公司不存在")
    tenant.name = payload.name
    tenant.slug = payload.slug
    tenant.logo_url = payload.logo_url
    tenant.contact_email = (
        _normalize_email(payload.contact_email) if payload.contact_email else None
    )
    tenant.updated_at = utc_now()
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=409, detail="公司简称已被使用") from exc
    return CompanyResponse.model_validate(tenant, from_attributes=True)


@router.get("/organization-units", response_model=OrganizationTreeResponse)
async def list_organization_units(
    identity: IdentityContext = Depends(current_identity),
    session: AsyncSession = Depends(database_session),
) -> OrganizationTreeResponse:
    _require_admin(identity)
    units = list(
        (
            await session.scalars(
                select(OrganizationUnit)
                .where(OrganizationUnit.tenant_id == identity.principal.tenant_id)
                .order_by(OrganizationUnit.name)
            )
        ).all()
    )
    count_rows = (
        await session.execute(
            select(User.organization_unit_id, func.count(User.id))
            .where(User.tenant_id == identity.principal.tenant_id)
            .group_by(User.organization_unit_id)
        )
    ).all()
    counts: dict[UUID | None, int] = {
        organization_unit_id: int(count)
        for organization_unit_id, count in count_rows
    }
    children: dict[UUID | None, list[OrganizationUnit]] = defaultdict(list)
    for unit in units:
        children[unit.parent_id].append(unit)

    def build(unit: OrganizationUnit) -> OrganizationUnitResponse:
        child_nodes = [build(child) for child in children[unit.id]]
        direct_user_count = int(counts.get(unit.id, 0))
        return OrganizationUnitResponse(
            id=unit.id,
            parent_id=unit.parent_id,
            name=unit.name,
            direct_user_count=direct_user_count,
            user_count=direct_user_count + sum(child.user_count for child in child_nodes),
            children=child_nodes,
        )

    return OrganizationTreeResponse(items=[build(unit) for unit in children[None]])


@router.post(
    "/organization-units",
    response_model=OrganizationUnitResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_organization_unit(
    payload: OrganizationUnitCreate,
    identity: IdentityContext = Depends(current_identity),
    session: AsyncSession = Depends(database_session),
) -> OrganizationUnitResponse:
    _require_admin(identity)
    await _tenant_department(session, identity.principal.tenant_id, payload.parent_id)
    await _ensure_department_name_available(
        session,
        identity.principal.tenant_id,
        payload.parent_id,
        payload.name,
    )
    unit = OrganizationUnit(
        tenant_id=identity.principal.tenant_id,
        parent_id=payload.parent_id,
        name=payload.name,
    )
    session.add(unit)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=409, detail="同一上级部门下已存在同名部门") from exc
    return OrganizationUnitResponse(
        id=unit.id,
        parent_id=unit.parent_id,
        name=unit.name,
        direct_user_count=0,
        user_count=0,
        children=[],
    )


@router.patch("/organization-units/{unit_id}", response_model=OrganizationUnitResponse)
async def update_organization_unit(
    unit_id: UUID,
    payload: OrganizationUnitUpdate,
    identity: IdentityContext = Depends(current_identity),
    session: AsyncSession = Depends(database_session),
) -> OrganizationUnitResponse:
    _require_admin(identity)
    unit = await _tenant_department(session, identity.principal.tenant_id, unit_id)
    assert unit is not None
    if payload.parent_id == unit.id:
        raise HTTPException(status_code=422, detail="部门不能成为自己的上级")
    parent = await _tenant_department(session, identity.principal.tenant_id, payload.parent_id)
    cursor = parent
    while cursor is not None:
        if cursor.id == unit.id:
            raise HTTPException(status_code=422, detail="部门层级不能形成循环")
        cursor = await _tenant_department(
            session, identity.principal.tenant_id, cursor.parent_id
        )
    await _ensure_department_name_available(
        session,
        identity.principal.tenant_id,
        payload.parent_id,
        payload.name,
        exclude_id=unit.id,
    )
    unit.parent_id = payload.parent_id
    unit.name = payload.name
    unit.updated_at = utc_now()
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=409, detail="同一上级部门下已存在同名部门") from exc
    count = await session.scalar(
        select(func.count(User.id)).where(User.organization_unit_id == unit.id)
    )
    user_count = await _department_user_count(session, identity.principal.tenant_id, unit.id)
    return OrganizationUnitResponse(
        id=unit.id,
        parent_id=unit.parent_id,
        name=unit.name,
        direct_user_count=int(count or 0),
        user_count=user_count,
        children=[],
    )


@router.delete("/organization-units/{unit_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_organization_unit(
    unit_id: UUID,
    identity: IdentityContext = Depends(current_identity),
    session: AsyncSession = Depends(database_session),
) -> Response:
    _require_admin(identity)
    unit = await _tenant_department(session, identity.principal.tenant_id, unit_id)
    assert unit is not None
    child_count = await session.scalar(
        select(func.count(OrganizationUnit.id)).where(OrganizationUnit.parent_id == unit.id)
    )
    user_count = await session.scalar(
        select(func.count(User.id)).where(User.organization_unit_id == unit.id)
    )
    if child_count or user_count:
        raise HTTPException(status_code=409, detail="部门仍包含下级部门或用户，不能删除")
    await session.delete(unit)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/users", response_model=AdminUserListResponse)
async def list_users(
    keywords: str | None = Query(default=None, description="按姓名或邮箱搜索"),
    organization_unit_id: UUID | None = Query(default=None, description="按直属部门筛选"),
    pagination: PaginationParams = Depends(pagination_params),
    identity: IdentityContext = Depends(current_identity),
    session: AsyncSession = Depends(database_session),
) -> AdminUserListResponse:
    _require_admin(identity)
    conditions = [User.tenant_id == identity.principal.tenant_id]
    if organization_unit_id is not None:
        conditions.append(User.organization_unit_id == organization_unit_id)
    if keywords and keywords.strip():
        normalized_keywords = keywords.strip()
        pattern = f"%{normalized_keywords}%"
        matching_departments = select(OrganizationUnit.id).where(
            OrganizationUnit.tenant_id == identity.principal.tenant_id,
            OrganizationUnit.name.ilike(pattern),
        )
        department_id_match: UUID | None = None
        try:
            department_id_match = UUID(normalized_keywords)
        except ValueError:
            # 普通文本搜索不应因为不是 UUID 而返回 422。
            pass
        department_id_condition = (
            User.organization_unit_id == department_id_match
            if department_id_match is not None
            else User.organization_unit_id.in_(matching_departments)
        )
        conditions.append(
            or_(
                User.display_name.ilike(pattern),
                User.email.ilike(pattern),
                User.phone.ilike(pattern),
                User.job_title.ilike(pattern),
                department_id_condition,
            )
        )
    total = int(
        await session.scalar(
            select(func.count())
            .select_from(User)
            .where(*conditions)
        )
        or 0
    )
    users = (
        await session.scalars(
            select(User)
            .where(*conditions)
            .order_by(User.display_name, User.email, User.id)
            .limit(pagination.page_size)
            .offset(pagination.offset)
        )
    ).all()
    metadata = pagination_metadata(total, pagination)
    return AdminUserListResponse(
        items=[await _user_response(session, user) for user in users],
        total=metadata.total,
        page=metadata.page,
        page_size=metadata.page_size,
        pages=metadata.pages,
    )


@router.post("/users", response_model=AdminUserResponse, status_code=status.HTTP_201_CREATED)
async def create_user(
    payload: AdminUserCreate,
    identity: IdentityContext = Depends(current_identity),
    session: AsyncSession = Depends(database_session),
) -> AdminUserResponse:
    _require_admin(identity)
    await _tenant_department(session, identity.principal.tenant_id, payload.organization_unit_id)
    roles = set(payload.roles)
    if not roles or not roles <= ALLOWED_ROLES:
        raise HTTPException(status_code=422, detail="用户角色不合法")
    try:
        password_hash = hash_password(payload.password)
    except PasswordPolicyError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    user = User(
        tenant_id=identity.principal.tenant_id,
        email=_normalize_email(payload.email),
        password_hash=password_hash,
        display_name=payload.display_name,
        organization_unit_id=payload.organization_unit_id,
        job_title=payload.job_title,
        phone=payload.phone,
        roles=sorted(roles),
        password_changed_at=utc_now(),
    )
    session.add(user)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=409, detail="该邮箱已存在") from exc
    return await _user_response(session, user)


async def _managed_user(
    session: AsyncSession, tenant_id: UUID, user_id: UUID
) -> User:
    user = await session.scalar(
        select(User).where(User.id == user_id, User.tenant_id == tenant_id)
    )
    if user is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    return user


@router.get("/users/{user_id}", response_model=AdminUserResponse)
async def get_user(
    user_id: UUID,
    identity: IdentityContext = Depends(current_identity),
    session: AsyncSession = Depends(database_session),
) -> AdminUserResponse:
    _require_admin(identity)
    user = await _managed_user(session, identity.principal.tenant_id, user_id)
    return await _user_response(session, user)


@router.patch("/users/{user_id}", response_model=AdminUserResponse)
async def update_user(
    user_id: UUID,
    payload: AdminUserUpdate,
    identity: IdentityContext = Depends(current_identity),
    session: AsyncSession = Depends(database_session),
) -> AdminUserResponse:
    _require_admin(identity)
    user = await _managed_user(session, identity.principal.tenant_id, user_id)
    await _tenant_department(session, identity.principal.tenant_id, payload.organization_unit_id)
    roles = set(payload.roles)
    if not roles or not roles <= ALLOWED_ROLES:
        raise HTTPException(status_code=422, detail="用户角色不合法")
    losing_admin = ROLE_PLATFORM_ADMIN in user.roles and ROLE_PLATFORM_ADMIN not in roles
    disabling = user.status == "active" and payload.status == "disabled"
    if user.id == identity.user.id and (losing_admin or disabling):
        raise HTTPException(status_code=409, detail="不能停用当前管理员或移除自己的管理员角色")
    removes_active_admin = losing_admin or (
        disabling and ROLE_PLATFORM_ADMIN in user.roles
    )
    if removes_active_admin:
        active_roles = (
            await session.scalars(
                select(User.roles).where(
                    User.tenant_id == identity.principal.tenant_id,
                    User.status == "active",
                )
            )
        ).all()
        admin_count = sum(ROLE_PLATFORM_ADMIN in roles for roles in active_roles)
        if admin_count <= 1:
            raise HTTPException(status_code=409, detail="必须保留至少一个可用平台管理员")
    user.display_name = payload.display_name
    user.organization_unit_id = payload.organization_unit_id
    user.job_title = payload.job_title
    user.phone = payload.phone
    user.roles = sorted(roles)
    user.status = payload.status
    user.updated_at = utc_now()
    await session.commit()
    return await _user_response(session, user)


@router.put("/users/{user_id}/password", status_code=status.HTTP_204_NO_CONTENT)
async def reset_user_password(
    user_id: UUID,
    payload: AdminPasswordReset,
    identity: IdentityContext = Depends(current_identity),
    session: AsyncSession = Depends(database_session),
) -> Response:
    _require_admin(identity)
    user = await _managed_user(session, identity.principal.tenant_id, user_id)
    try:
        user.password_hash = hash_password(payload.password)
    except PasswordPolicyError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    user.password_changed_at = utc_now()
    user.updated_at = utc_now()
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
