from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine
from supportops_core.bootstrap import bootstrap_enterprise_identity
from supportops_core.config import Settings, get_settings
from supportops_core.db import create_engine, create_session_factory
from supportops_core.logging import configure_logging
from supportops_core.queue import RunQueue
from supportops_core.restore_startup_barrier import assert_service_start_allowed

from supportops_api.errors import register_error_handlers
from supportops_api.middleware import CorrelationIdMiddleware
from supportops_api.schemas import ErrorResponse
from supportops_api.routes import (
    actions,
    admin_agents,
    admin_dashboard,
    admin_identity,
    admin_knowledge,
    admin_knowledge_index,
    admin_knowledge_ingestion,
    admin_models,
    admin_run_attempts,
    admin_run_notifications,
    admin_runtime_alerts,
    admin_tools,
    admin_tool_discovery,
    agents,
    approvals,
    auth,
    conversations,
    data_governance,
    health,
    mock_tickets,
    runs,
)


def create_app(
    settings: Settings | None = None,
    *,
    engine: AsyncEngine | None = None,
    redis: Redis | None = None,
) -> FastAPI:
    app_settings = settings or get_settings()
    assert_service_start_allowed(mode=app_settings.restore_mode, expected_restore_id=app_settings.restore_operation_id,
        expected_context_digest=app_settings.restore_context_digest)
    configure_logging(service=app_settings.service_name, level=app_settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # 工厂创建与实际ASGI启动之间可能有间隔，启动副作用前再次检查独立标记。
        assert_service_start_allowed(mode=app_settings.restore_mode, expected_restore_id=app_settings.restore_operation_id,
            expected_context_digest=app_settings.restore_context_digest)
        app.state.settings = app_settings
        app.state.engine = engine or create_engine(app_settings)
        app.state.session_factory = create_session_factory(app.state.engine)
        await bootstrap_enterprise_identity(app.state.session_factory, app_settings)
        app.state.redis = redis or Redis.from_url(app_settings.redis_url, decode_responses=False)
        app.state.run_queue = RunQueue(app.state.redis, app_settings.redis_queue_name)
        yield
        if redis is None:
            await app.state.redis.aclose()
        if engine is None:
            await app.state.engine.dispose()

    app = FastAPI(
        title="SupportOps Agent API",
        version="0.1.0",
        lifespan=lifespan,
        responses={code: {"model": ErrorResponse, "description": description} for code, description in {
            401: "认证缺失或登录会话失效", 403: "当前策略或操作权限拒绝", 404: "资源不存在或当前不可访问",
            409: "版本、状态、固定范围或恢复许可冲突", 422: "请求或配置校验失败",
            429: "租户或运行额度耗尽", 500: "内部错误，使用关联 ID 排查", 503: "依赖或已批准连接器不可用",
        }.items()},
    )
    app.add_middleware(CorrelationIdMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=app_settings.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "Last-Event-ID"],
        expose_headers=["X-Correlation-ID"],
    )
    register_error_handlers(app)
    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(data_governance.admin_router)
    app.include_router(data_governance.account_router)
    app.include_router(admin_identity.router)
    app.include_router(admin_knowledge.router)
    app.include_router(admin_knowledge_index.router)
    app.include_router(admin_knowledge_ingestion.router)
    app.include_router(admin_models.router)
    app.include_router(admin_run_attempts.router)
    app.include_router(admin_run_notifications.router)
    app.include_router(admin_runtime_alerts.router)
    app.include_router(admin_tools.router)
    app.include_router(admin_tool_discovery.router)
    app.include_router(admin_agents.router)
    app.include_router(admin_dashboard.router)
    app.include_router(agents.router)
    app.include_router(conversations.router)
    app.include_router(runs.router)
    app.include_router(actions.router)
    app.include_router(approvals.router)
    app.include_router(mock_tickets.router)
    return app


app = create_app()


def run() -> None:
    uvicorn.run("supportops_api.main:app", host="0.0.0.0", port=8000, reload=False)
