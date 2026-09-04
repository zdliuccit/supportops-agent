from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from supportops_core.db import check_database

from supportops_api.schemas import HealthResponse

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live", response_model=HealthResponse)
async def live(request: Request) -> HealthResponse:
    return HealthResponse(status="ok", service=request.app.state.settings.service_name)


@router.get("/ready", response_model=HealthResponse)
async def ready(request: Request) -> HealthResponse | JSONResponse:
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
