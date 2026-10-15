"""租户管理员只读告警与确认；已阅不代表业务修复。"""
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from supportops_core.agent_services import require_platform_admin
from supportops_core.auth import IdentityContext
from supportops_core.models import RuntimeAlert, RuntimeAlertScanState
from supportops_core.runtime_alert_services import (ALERT_RULES, RuntimeAlertConflict,
    acknowledge_runtime_alert, aware, safe_alert_metadata)
from supportops_api.dependencies import current_identity, database_session, settings_from
from supportops_api.pagination import PaginationParams, pagination_metadata, pagination_params
from supportops_api.schemas import RuntimeAlertAcknowledgeRequest, RuntimeAlertResponse, RuntimeAlertListResponse

router = APIRouter(prefix="/v1/admin/runtime-alerts", tags=["runtime-alerts"])


async def admin_identity(identity: IdentityContext = Depends(current_identity)) -> IdentityContext:
    require_platform_admin(identity)
    return identity


def _response(alert: RuntimeAlert, *, now: datetime, stale_seconds: float) -> RuntimeAlertResponse:
    _, title, description = ALERT_RULES.get(alert.rule_code, ("info", "未知告警规则", "请确认观测约定版本。"))
    return RuntimeAlertResponse(id=alert.id, rule_code=alert.rule_code, title=title, description=description,
        scope_kind=alert.scope_kind, scope_id=alert.scope_id, run_id=alert.run_id, severity=alert.severity,
        is_active=alert.is_active, is_stale=(now-aware(alert.last_observed_at)).total_seconds() > stale_seconds,
        revision=alert.revision, source_generation=alert.source_generation, metadata=safe_alert_metadata(alert.metadata_payload),
        first_observed_at=alert.first_observed_at, last_observed_at=alert.last_observed_at, resolved_at=alert.resolved_at,
        acknowledged_at=alert.acknowledged_at, acknowledged_by=alert.acknowledged_by)


@router.get("", response_model=RuntimeAlertListResponse)
async def list_alerts(request: Request, state: Literal["unacknowledged", "acknowledged", "resolved", "all"] = Query(default="unacknowledged"),
    severity: Literal["info", "warning", "critical"] | None = Query(default=None),
    rule_code: str | None = Query(default=None, max_length=64), pagination: PaginationParams = Depends(pagination_params),
    identity: IdentityContext = Depends(admin_identity), session: AsyncSession = Depends(database_session),
) -> RuntimeAlertListResponse:
    if rule_code is not None and rule_code not in ALERT_RULES:
        raise HTTPException(status_code=422, detail="未知告警规则")
    filters = [RuntimeAlert.tenant_id == identity.principal.tenant_id]
    if state == "unacknowledged":
        filters.extend([RuntimeAlert.is_active.is_(True), RuntimeAlert.acknowledged_at.is_(None)])
    elif state == "acknowledged":
        filters.extend([RuntimeAlert.is_active.is_(True), RuntimeAlert.acknowledged_at.is_not(None)])
    elif state == "resolved":
        filters.append(RuntimeAlert.is_active.is_(False))
    if severity is not None:
        filters.append(RuntimeAlert.severity == severity)
    if rule_code is not None:
        filters.append(RuntimeAlert.rule_code == rule_code)
    total = int(await session.scalar(select(func.count()).select_from(RuntimeAlert).where(*filters)) or 0)
    rows = (await session.scalars(select(RuntimeAlert).where(*filters).order_by(RuntimeAlert.last_observed_at.desc(), RuntimeAlert.id.desc())
        .offset(pagination.offset).limit(pagination.page_size))).all()
    # 共享执行服务只显示最近扫描时间，不暴露其他租户游标、来源 ID 或业务计数。
    scan_times = (await session.scalars(select(RuntimeAlertScanState.updated_at))).all()
    observed_at = min((aware(value) for value in scan_times), default=None) if len(scan_times) == 2 else None
    now, settings = datetime.now(UTC), settings_from(request)
    page = pagination_metadata(total, pagination)
    return RuntimeAlertListResponse(items=[_response(row, now=now, stale_seconds=settings.runtime_alert_stale_seconds) for row in rows],
        total=page.total, page=page.page, page_size=page.page_size, pages=page.pages, scan_observed_at=observed_at,
        scan_stale=observed_at is None or (now-observed_at).total_seconds() > settings.runtime_alert_stale_seconds)


@router.post("/{alert_id}/acknowledge", response_model=RuntimeAlertResponse)
async def acknowledge_alert(alert_id: UUID, payload: RuntimeAlertAcknowledgeRequest, request: Request,
    identity: IdentityContext = Depends(admin_identity), session: AsyncSession = Depends(database_session),
) -> RuntimeAlertResponse:
    try:
        async with session.begin():
            alert = await acknowledge_runtime_alert(session, tenant_id=identity.principal.tenant_id, alert_id=alert_id,
                expected_revision=payload.expected_revision, actor_user_id=identity.user.id, correlation_id=request.state.correlation_id)
    except RuntimeAlertConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _response(alert, now=datetime.now(UTC), stale_seconds=settings_from(request).runtime_alert_stale_seconds)
