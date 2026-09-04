from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from supportops_core.auth import IdentityContext, create_access_token
from supportops_core.models import Tenant, User, utc_now
from supportops_core.passwords import hash_password, verify_password

from supportops_api.dependencies import current_identity, database_session, settings_from
from supportops_api.schemas import IdentityResponse, LoginRequest, LoginResponse

router = APIRouter(prefix="/v1/auth", tags=["auth"])

# 未知邮箱也执行同成本校验，降低通过响应时间枚举账号的风险。
DUMMY_PASSWORD_HASH = hash_password("supportops-invalid-login-placeholder")


@router.get("/me", response_model=IdentityResponse)
async def read_current_identity(
    identity: IdentityContext = Depends(current_identity),
    session: AsyncSession = Depends(database_session),
) -> IdentityResponse:
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
        select(User).where(func.lower(User.email) == normalized_email)
    )
    password_matches = verify_password(
        payload.password, user.password_hash if user is not None else DUMMY_PASSWORD_HASH
    )
    if user is None or not password_matches or user.status != "active":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="邮箱或密码错误")
    lifetime = timedelta(minutes=settings.access_token_lifetime_minutes)
    user.last_login_at = utc_now()
    await session.commit()
    token = create_access_token(
        settings,
        user_id=user.id,
        tenant_id=user.tenant_id,
        roles=tuple(user.roles),
        display_name=user.display_name,
        expires_in=lifetime,
    )
    response.headers["Cache-Control"] = "no-store"
    return LoginResponse(
        access_token=token,
        expires_in=int(lifetime.total_seconds()),
    )
