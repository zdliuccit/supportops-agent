from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from supportops_core.auth import IdentityContext, create_access_token
from supportops_core.models import AgentAuditEvent, AuthSession, Tenant, User, utc_now
from supportops_core.enums import AuditAction, AuditResourceType
from supportops_core.auth_session_services import revoke_owned_session, invalidate_user_sessions
from supportops_core.passwords import hash_password, verify_password

from supportops_api.dependencies import current_identity, database_session, settings_from
from supportops_api.schemas import IdentityResponse, LoginRequest, LoginResponse, AuthSessionResponse, AuthSessionListResponse
from supportops_api.pagination import PaginationParams, pagination_metadata, pagination_params

router = APIRouter(prefix="/v1/auth", tags=["auth"])

# 未知邮箱也执行同成本校验，降低通过响应时间枚举账号的风险。
DUMMY_PASSWORD_HASH = hash_password("supportops-invalid-login-placeholder")


@router.get("/me", response_model=IdentityResponse)
async def read_current_identity(
    response: Response,
    identity: IdentityContext = Depends(current_identity),
    session: AsyncSession = Depends(database_session),
) -> IdentityResponse:
    response.headers["Cache-Control"] = "no-store"
    tenant = await session.get(Tenant, identity.user.tenant_id)
    if tenant is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="公司不存在")
    return IdentityResponse(
        user_id=identity.user.id,
        tenant_id=identity.principal.tenant_id,
        email=identity.user.email,
        display_name=identity.user.display_name,
        roles=list(identity.principal.roles),
        organization_unit_id=identity.user.organization_unit_id,
        job_title=identity.user.job_title,
        company_name=tenant.name,
        company_logo_url=tenant.logo_url,
    )


@router.post("/login", response_model=LoginResponse)
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(database_session),
) -> LoginResponse:
    """校验邮箱密码并为 active 系统用户签发 JWT。"""
    settings = settings_from(request)
    normalized_email = payload.email.strip().lower()
    user = await session.scalar(
        select(User).where(func.lower(User.email) == normalized_email).with_for_update()
    )
    password_matches = verify_password(
        payload.password, user.password_hash if user is not None else DUMMY_PASSWORD_HASH
    )
    if user is None or not password_matches or user.status != "active":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="邮箱或密码错误")
    tenant = await session.get(Tenant, user.tenant_id)
    if tenant is None or tenant.status != "active":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="邮箱或密码错误")
    lifetime = timedelta(minutes=settings.access_token_lifetime_minutes)
    now = utc_now()
    user.last_login_at = now
    record = AuthSession(id=uuid4(), tenant_id=user.tenant_id, user_id=user.id,
        auth_generation=user.auth_generation, created_at=now, expires_at=now+lifetime)
    session.add(record)
    session.add(AgentAuditEvent(tenant_id=user.tenant_id, actor_user_id=user.id,
        resource_type=AuditResourceType.AUTH_SESSION, action=AuditAction.AUTH_SESSION_CREATED,
        correlation_id=request.state.correlation_id, metadata_payload={"session_id": str(record.id), "user_id": str(user.id)}))
    await session.commit()
    token = create_access_token(
        settings,
        user_id=user.id,
        tenant_id=user.tenant_id,
        roles=tuple(user.roles),
        display_name=user.display_name,
        expires_in=lifetime,
        session_id=record.id,
        auth_generation=record.auth_generation,
    )
    response.headers["Cache-Control"] = "no-store"
    return LoginResponse(
        access_token=token,
        expires_in=int(lifetime.total_seconds()),
    )


def _session_response(row: AuthSession, identity: IdentityContext) -> AuthSessionResponse:
    expiry = row.expires_at.replace(tzinfo=UTC) if row.expires_at.tzinfo is None else row.expires_at
    return AuthSessionResponse(id=row.id, created_at=row.created_at, expires_at=row.expires_at,
        revoked_at=row.revoked_at, revocation_code=row.revocation_code,
        is_current=row.id == identity.principal.session_id,
        is_active=row.revoked_at is None and expiry > datetime.now(UTC) and row.auth_generation == identity.user.auth_generation)


@router.get("/sessions", response_model=AuthSessionListResponse)
async def list_sessions(response: Response, pagination: PaginationParams = Depends(pagination_params),
    identity: IdentityContext = Depends(current_identity), session: AsyncSession = Depends(database_session),
) -> AuthSessionListResponse:
    response.headers["Cache-Control"] = "no-store"
    filters = [AuthSession.tenant_id == identity.principal.tenant_id, AuthSession.user_id == identity.user.id]
    total = int(await session.scalar(select(func.count()).select_from(AuthSession).where(*filters)) or 0)
    rows = (await session.scalars(select(AuthSession).where(*filters).order_by(AuthSession.created_at.desc(), AuthSession.id.desc())
        .offset(pagination.offset).limit(pagination.page_size))).all()
    page = pagination_metadata(total, pagination)
    return AuthSessionListResponse(items=[_session_response(row, identity) for row in rows],
        total=page.total, page=page.page, page_size=page.page_size, pages=page.pages)


@router.post("/logout", status_code=204)
async def logout(request: Request, identity: IdentityContext = Depends(current_identity),
    session: AsyncSession = Depends(database_session),
) -> Response:
    if identity.principal.session_id is None:
        raise HTTPException(status_code=401, detail="需要重新登录")
    async with session.begin():
        await revoke_owned_session(session, identity=identity, session_id=identity.principal.session_id,
            correlation_id=request.state.correlation_id)
    return Response(status_code=204, headers={"Cache-Control": "no-store"})


@router.post("/sessions/{session_id}/revoke", response_model=AuthSessionResponse)
async def revoke_session(session_id: UUID, request: Request, response: Response, identity: IdentityContext = Depends(current_identity),
    session: AsyncSession = Depends(database_session),
) -> AuthSessionResponse:
    response.headers["Cache-Control"] = "no-store"
    async with session.begin():
        row = await revoke_owned_session(session, identity=identity, session_id=session_id, correlation_id=request.state.correlation_id)
    return _session_response(row, identity)


@router.post("/sessions/revoke-all", status_code=204)
async def revoke_all(request: Request, identity: IdentityContext = Depends(current_identity),
    session: AsyncSession = Depends(database_session),
) -> Response:
    async with session.begin():
        await invalidate_user_sessions(session, user=identity.user, actor_user_id=identity.user.id,
            code="OWNER_REVOKE_ALL", correlation_id=request.state.correlation_id)
    return Response(status_code=204, headers={"Cache-Control": "no-store"})
