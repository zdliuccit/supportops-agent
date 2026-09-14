from typing import Any, cast

import structlog
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from supportops_core.agent_services import (
    AgentConfigurationError,
    AgentConflictError,
    AgentRevisionConflictError,
    PermissionDeniedError,
)
from supportops_core.model_services import (
    ModelEndpointConflictError,
    ModelEndpointValidationError,
)
from supportops_core.knowledge_services import (
    KnowledgeAccessDeniedError,
    KnowledgeConflictError,
    KnowledgeRevisionConflictError,
    KnowledgeStateError,
)
from supportops_core.services import IdempotencyConflictError, ResourceNotFoundError

logger = structlog.get_logger()


def _payload(request: Request, code: str, message: str) -> dict[str, Any]:
    return {
        "error": {
            "code": code,
            "message": message,
            "correlation_id": request.state.correlation_id,
        }
    }


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(HTTPException)
    async def handle_http(request: Request, exc: HTTPException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=_payload(request, f"HTTP_{exc.status_code}", str(exc.detail)),
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=_payload(request, "VALIDATION_ERROR", "请求参数不合法"),
        )

    @app.exception_handler(ResourceNotFoundError)
    async def handle_not_found(request: Request, exc: ResourceNotFoundError) -> JSONResponse:
        return JSONResponse(status_code=404, content=_payload(request, "NOT_FOUND", str(exc)))

    @app.exception_handler(IdempotencyConflictError)
    async def handle_conflict(request: Request, exc: IdempotencyConflictError) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content=_payload(request, "IDEMPOTENCY_CONFLICT", str(exc)),
        )

    @app.exception_handler(PermissionDeniedError)
    async def handle_permission_denied(
        request: Request, exc: PermissionDeniedError
    ) -> JSONResponse:
        return JSONResponse(status_code=403, content=_payload(request, "FORBIDDEN", str(exc)))

    @app.exception_handler(AgentRevisionConflictError)
    async def handle_revision_conflict(
        request: Request, exc: AgentRevisionConflictError
    ) -> JSONResponse:
        content = _payload(request, "AGENT_DRAFT_REVISION_CONFLICT", str(exc))
        error = cast(dict[str, Any], content["error"])
        error["current_revision"] = exc.current_revision
        return JSONResponse(status_code=409, content=content)

    @app.exception_handler(AgentConfigurationError)
    async def handle_agent_configuration(
        request: Request, exc: AgentConfigurationError
    ) -> JSONResponse:
        content = _payload(request, "AGENT_CONFIG_INVALID", str(exc))
        error = cast(dict[str, Any], content["error"])
        error["issues"] = exc.issues
        return JSONResponse(status_code=422, content=content)

    @app.exception_handler(AgentConflictError)
    async def handle_agent_conflict(request: Request, exc: AgentConflictError) -> JSONResponse:
        return JSONResponse(status_code=409, content=_payload(request, "AGENT_CONFLICT", str(exc)))

    @app.exception_handler(ModelEndpointConflictError)
    async def handle_model_conflict(
        request: Request, exc: ModelEndpointConflictError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=409, content=_payload(request, "MODEL_ENDPOINT_CONFLICT", str(exc))
        )

    @app.exception_handler(ModelEndpointValidationError)
    async def handle_model_validation(
        request: Request, exc: ModelEndpointValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=_payload(request, "MODEL_ENDPOINT_INVALID", str(exc)),
        )

    @app.exception_handler(KnowledgeAccessDeniedError)
    async def handle_knowledge_access_denied(
        request: Request, exc: KnowledgeAccessDeniedError
    ) -> JSONResponse:
        return JSONResponse(status_code=403, content=_payload(request, "KNOWLEDGE_FORBIDDEN", str(exc)))

    @app.exception_handler(KnowledgeRevisionConflictError)
    async def handle_knowledge_revision_conflict(
        request: Request, exc: KnowledgeRevisionConflictError
    ) -> JSONResponse:
        return JSONResponse(status_code=409, content=_payload(request, "KNOWLEDGE_REVISION_CONFLICT", str(exc)))

    @app.exception_handler(KnowledgeStateError)
    async def handle_knowledge_state_error(
        request: Request, exc: KnowledgeStateError
    ) -> JSONResponse:
        return JSONResponse(status_code=409, content=_payload(request, "KNOWLEDGE_STATE_CONFLICT", str(exc)))

    @app.exception_handler(KnowledgeConflictError)
    async def handle_knowledge_conflict(
        request: Request, exc: KnowledgeConflictError
    ) -> JSONResponse:
        return JSONResponse(status_code=409, content=_payload(request, "KNOWLEDGE_CONFLICT", str(exc)))

    @app.exception_handler(Exception)
    async def handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled_request_error", error_type=type(exc).__name__)
        return JSONResponse(
            status_code=500,
            content=_payload(request, "INTERNAL_ERROR", "服务暂时不可用"),
        )
