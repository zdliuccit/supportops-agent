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

from supportops_api.errors import register_error_handlers
from supportops_api.middleware import CorrelationIdMiddleware
from supportops_api.routes import (
    admin_agents,
    admin_identity,
    admin_models,
    admin_tools,
    agents,
    auth,
    conversations,
    health,
    runs,
)


def create_app(
    settings: Settings | None = None,
    *,
    engine: AsyncEngine | None = None,
    redis: Redis | None = None,
) -> FastAPI:
    app_settings = settings or get_settings()
    configure_logging(service=app_settings.service_name, level=app_settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
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
    app.include_router(admin_identity.router)
    app.include_router(admin_models.router)
    app.include_router(admin_tools.router)
    app.include_router(admin_agents.router)
    app.include_router(agents.router)
    app.include_router(conversations.router)
    app.include_router(runs.router)
    return app


app = create_app()


def run() -> None:
    uvicorn.run("supportops_api.main:app", host="0.0.0.0", port=8000, reload=False)
