from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from supportops_core.db import check_database

from supportops_api.schemas import HealthResponse

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live", response_model=HealthResponse)
async def live(request: Request) -> HealthResponse:
    """只报告 API 进程存活，不调用数据库、模型或业务连接器。"""
    return HealthResponse(status="ok", service=request.app.state.settings.service_name)


@router.get("/ready", response_model=HealthResponse, responses={503: {"model": HealthResponse,
    "description": "必需基础设施未就绪；健康响应不使用业务错误 envelope"}})
async def ready(request: Request) -> HealthResponse | JSONResponse:
    """只探测 API 必需的数据库与队列；业务依赖健康由独立状态接口呈现。"""
    dependencies: dict[str, str] = {}
    try:
        await check_database(request.app.state.engine)
        dependencies["postgresql"] = "ok"
    except Exception:
        dependencies["postgresql"] = "unavailable"
    try:
        await request.app.state.redis.ping()
        dependencies["redis"] = "ok"
    except Exception:
        dependencies["redis"] = "unavailable"
    if any(value != "ok" for value in dependencies.values()):
        return JSONResponse(
            status_code=503,
            content={
                "status": "unavailable",
                "service": request.app.state.settings.service_name,
                "dependencies": dependencies,
            },
        )
    return HealthResponse(
        status="ok",
        service=request.app.state.settings.service_name,
        dependencies=dependencies,
    )
