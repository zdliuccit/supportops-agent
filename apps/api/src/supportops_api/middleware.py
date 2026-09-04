import re
from uuid import uuid4

import structlog
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

CORRELATION_HEADER = "X-Correlation-ID"
VALID_CORRELATION_ID = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        supplied = request.headers.get(CORRELATION_HEADER, "")
        correlation_id = supplied if VALID_CORRELATION_ID.fullmatch(supplied) else str(uuid4())
        request.state.correlation_id = correlation_id
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(correlation_id=correlation_id)
        response = await call_next(request)
        response.headers[CORRELATION_HEADER] = correlation_id
        return response
