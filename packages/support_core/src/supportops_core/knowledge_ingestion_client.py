"""本机人工导入API客户端；不直连数据库、读取知识文件或自动重试写入。"""

import asyncio
import json
import math
import re
from dataclasses import dataclass, field
from typing import Literal, NoReturn, TypeVar
from urllib.parse import urlsplit
from uuid import UUID

import httpx
from pydantic import SecretStr

from supportops_core.knowledge_ingestion_contracts import (
    INGESTION_MANUAL_WINDOW_SECONDS,
    IngestionContract,
    IngestionCreateCommand,
    IngestionCreationStatus,
    IngestionCreationStopCommand,
    IngestionJobView,
    IngestionRevisionCommand,
    IngestionSourceReadiness,
)

_API_PATH = "/v1/admin/knowledge/ingestion-jobs"
_MAX_RESPONSE_BYTES = 65536
_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\Z")
ResponseModel = TypeVar("ResponseModel", bound=IngestionContract)
IngestionClientCode = Literal[
    "LOCAL_API_ORIGIN_REQUIRED", "TOKEN_INVALID", "API_UNAVAILABLE", "API_RESPONSE_INVALID",
    "AUTHENTICATION_REQUIRED", "FORBIDDEN", "RESOURCE_UNAVAILABLE", "REQUEST_CONFLICT",
    "RATE_LIMITED", "WRITE_ALREADY_ATTEMPTED", "SOURCE_SCOPE_MISMATCH",
    "SOURCE_UNAVAILABLE", "CREATION_NOT_AVAILABLE", "REQUEST_STOPPED",
    "JOB_INTEGRITY_UNCONFIRMED", "JOB_REVISION_CHANGED", "JOB_NOT_EXECUTABLE",
    "JOB_TRANSITION_UNAVAILABLE",
]


class IngestionClientError(Exception):
    """只携带固定失败码，不保存URL、令牌或响应原文。"""

    def __init__(self, code: IngestionClientCode) -> None:
        super().__init__(code)
        self.code = code


def local_api_origin(value: str) -> str:
    """本入口只接受字面回环HTTP来源，不接受DNS主机名、代理或任意路径。"""
    if (
        not value or len(value) > 256 or any(char in value for char in "\\?#@%")
        or any(ord(char) <= 32 or ord(char) == 127 for char in value)
    ):
        raise IngestionClientError("LOCAL_API_ORIGIN_REQUIRED")
    try:
        url = httpx.URL(value)
        port = urlsplit(value).port
        if (
            url.scheme != "http" or url.raw_host not in {b"127.0.0.1", b"::1"}
            or url.userinfo or url.raw_path != b"/" or port is None or not 1 <= port <= 65535
            or (url.port if url.port is not None else 80) != port
        ):
            raise IngestionClientError("LOCAL_API_ORIGIN_REQUIRED")
        # HTTPX会省略默认80端口；规范结果仍保留显式端口，使重复核对保持同一契约。
        host = "[::1]" if url.raw_host == b"::1" else "127.0.0.1"
        return f"http://{host}:{port}"
    except (ValueError, httpx.InvalidURL):
        raise IngestionClientError("LOCAL_API_ORIGIN_REQUIRED") from None


def validate_api_token(value: str) -> SecretStr:
    # 这里只检查头值形状；签名、期限、会话、租户与当前身份均由API独立验证。
    if not 1 <= len(value) <= 8192 or _TOKEN_PATTERN.fullmatch(value) is None:
        raise IngestionClientError("TOKEN_INVALID")
    return SecretStr(value)


def _unique_fields(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate response field")
        result[key] = value
    return result


def _reject_constant(_value: str) -> NoReturn:
    raise ValueError("nonfinite response number")


def _decode_response(raw: bytes, model: type[ResponseModel], version: str) -> ResponseModel:
    try:
        payload: object = json.loads(
            raw.decode("utf-8"), object_pairs_hook=_unique_fields, parse_constant=_reject_constant,
        )
        if not isinstance(payload, dict) or payload.get("schema_version") != version:
            raise ValueError("response version required")
        nested_job = payload.get("job")
        if isinstance(nested_job, dict) and (
            nested_job.get("schema_version") != "knowledge_ingestion_job_view_v1"
        ):
            raise ValueError("nested job version required")
        stack: list[tuple[object, int]] = [(payload, 0)]
        nodes = 0
        while stack:
            value, depth = stack.pop()
            nodes += 1
            if nodes > 4096 or depth > 16:
                raise ValueError("response structure limit")
            if isinstance(value, float) and not math.isfinite(value):
                raise ValueError("nonfinite response number")
            if isinstance(value, dict):
                stack.extend((item, depth + 1) for item in value.values())
            elif isinstance(value, list):
                stack.extend((item, depth + 1) for item in value)
            if len(stack) + nodes > 4096:
                raise ValueError("response structure limit")
        return model.model_validate_json(raw)
    except Exception:
        # Pydantic原错误可能附带输入；不跨客户端边界传播原文或异常链。
        raise IngestionClientError("API_RESPONSE_INVALID") from None


def require_job_scope(job: IngestionJobView, *, source_id: UUID, scope_digest: str) -> None:
    if job.integrity_status != "recorded" or job.status == "unknown":
        raise IngestionClientError("JOB_INTEGRITY_UNCONFIRMED")
    if job.source_id != source_id or job.scope_digest != scope_digest:
        raise IngestionClientError("SOURCE_SCOPE_MISMATCH")


def require_readiness(
    readiness: IngestionSourceReadiness, *, source_id: UUID, scope_digest: str, creating: bool,
) -> None:
    if not readiness.available:
        raise IngestionClientError("SOURCE_UNAVAILABLE")
    if readiness.source_id != source_id or readiness.scope_digest != scope_digest:
        raise IngestionClientError("SOURCE_SCOPE_MISMATCH")
    if (
        readiness.observed_at.tzinfo is None or readiness.valid_until is None
        or readiness.valid_until.tzinfo is None
        or readiness.valid_until <= readiness.observed_at or readiness.object_count is None
    ):
        raise IngestionClientError("API_RESPONSE_INVALID")
    if creating and (
        not readiness.create_available
        or (readiness.valid_until - readiness.observed_at).total_seconds()
        < INGESTION_MANUAL_WINDOW_SECONDS
    ):
        raise IngestionClientError("CREATION_NOT_AVAILABLE")


def _require_creation_status(status: IngestionCreationStatus, request_key: UUID) -> None:
    if status.request_key != request_key or status.observed_at.tzinfo is None:
        raise IngestionClientError("API_RESPONSE_INVALID")
    if status.stopped_at is not None and status.stopped_at.tzinfo is None:
        raise IngestionClientError("API_RESPONSE_INVALID")
    if status.job is not None:
        if status.job.integrity_status != "recorded" or status.job.status == "unknown":
            raise IngestionClientError("JOB_INTEGRITY_UNCONFIRMED")
        if status.job.creation_request_key != request_key:
            raise IngestionClientError("API_RESPONSE_INVALID")


def require_executable_job(
    job: IngestionJobView, readiness: IngestionSourceReadiness, *, expected_revision: int,
) -> None:
    if job.revision != expected_revision:
        raise IngestionClientError("JOB_REVISION_CHANGED")
    # 使用同一API返回的时间观察，不跨本地/服务端时钟推断许可；服务器提交时仍复检。
    if (
        job.status not in {"queued", "retry_wait"} or job.counts is None
        or job.attempts is None or job.max_attempts is None or job.attempts >= job.max_attempts
        or job.deadline_at is None or job.deadline_at.tzinfo is None
        or job.deadline_at <= readiness.observed_at or job.checkpoint_kind != "initial"
        or job.committed_pages != 0 or job.max_pages != 1
        or (job.retry_at is not None and (
            job.retry_at.tzinfo is None or job.retry_at > readiness.observed_at
        ))
        or (job.status == "retry_wait" and job.retry_at is None)
    ):
        raise IngestionClientError("JOB_NOT_EXECUTABLE")


@dataclass(slots=True)
class LocalIngestionClient:
    """单命令最多一次POST；属性不代表服务器接收、提交或业务执行事实。"""

    origin: str = field(repr=False)
    token: SecretStr = field(repr=False)
    write_attempted: bool = field(default=False, init=False)
    mutation_result_confirmed: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        self.origin = local_api_origin(self.origin)
        self.token = validate_api_token(self.token.get_secret_value())

    async def _request(
        self, method: Literal["GET", "POST"], suffix: str, *,
        payload: IngestionContract | None = None, request_key: UUID | None = None,
        executing: bool = False,
    ) -> bytes:
        origin = local_api_origin(self.origin)
        token = validate_api_token(self.token.get_secret_value())
        if method == "POST" and self.write_attempted:
            raise IngestionClientError("WRITE_ALREADY_ATTEMPTED")
        headers = {
            "Authorization": "Bearer " + token.get_secret_value(),
            "Accept": "application/json", "Accept-Encoding": "identity",
        }
        if request_key is not None:
            headers["Idempotency-Key"] = str(request_key)
        content = payload.model_dump_json().encode("utf-8") if payload is not None else None
        if content is not None:
            headers["Content-Type"] = "application/json"
        budget = 60.0 if executing else 15.0
        transport = httpx.AsyncHTTPTransport(
            retries=0, trust_env=False, http2=False,
            limits=httpx.Limits(max_connections=1, max_keepalive_connections=0),
        )
        try:
            async with asyncio.timeout(budget), httpx.AsyncClient(
                transport=transport, trust_env=False, follow_redirects=False,
                timeout=httpx.Timeout(budget, connect=3, write=3, pool=1),
            ) as client:
                if method == "POST":
                    # 客户端上下文进入包含await；同实例的并发调用也须在最后边界拒绝第二次写。
                    if self.write_attempted:
                        raise IngestionClientError("WRITE_ALREADY_ATTEMPTED")
                    # 从此处开始，任何失败都不能当作未提交；不自动补发或换键。
                    self.write_attempted = True
                async with client.stream(
                    method, origin + _API_PATH + suffix, headers=headers, content=content,
                ) as response:
                    if response.status_code != 200:
                        codes: dict[int, IngestionClientCode] = {
                            401: "AUTHENTICATION_REQUIRED", 403: "FORBIDDEN",
                            404: "RESOURCE_UNAVAILABLE", 409: "REQUEST_CONFLICT",
                            429: "RATE_LIMITED",
                        }
                        raise IngestionClientError(codes.get(response.status_code, "API_UNAVAILABLE"))
                    if (
                        response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                        != "application/json"
                        or response.headers.get("content-encoding", "identity").lower() != "identity"
                    ):
                        raise IngestionClientError("API_RESPONSE_INVALID")
                    length = response.headers.get("content-length")
                    if length is not None and (
                        not length.isascii() or not length.isdecimal()
                        or len(length) > 10 or int(length) > _MAX_RESPONSE_BYTES
                    ):
                        raise IngestionClientError("API_RESPONSE_INVALID")
                    data = bytearray()
                    async for chunk in response.aiter_raw(chunk_size=4096):
                        if len(data) + len(chunk) > _MAX_RESPONSE_BYTES:
                            raise IngestionClientError("API_RESPONSE_INVALID")
                        data.extend(chunk)
                    return bytes(data)
        except IngestionClientError:
            raise
        except (OSError, ValueError, TimeoutError, httpx.HTTPError):
            raise IngestionClientError("API_UNAVAILABLE") from None

    async def readiness(self) -> IngestionSourceReadiness:
        return _decode_response(
            await self._request("GET", "/source-readiness"), IngestionSourceReadiness,
            "knowledge_ingestion_source_readiness_v1",
        )

    async def request_status(self, request_key: UUID) -> IngestionCreationStatus:
        status = _decode_response(
            await self._request("GET", f"/creation-requests/{request_key}"), IngestionCreationStatus,
            "knowledge_ingestion_creation_status_v1",
        )
        _require_creation_status(status, request_key)
        return status

    async def job(self, job_id: UUID) -> IngestionJobView:
        job = _decode_response(
            await self._request("GET", f"/{job_id}"), IngestionJobView,
            "knowledge_ingestion_job_view_v1",
        )
        if job.id != job_id:
            raise IngestionClientError("API_RESPONSE_INVALID")
        return job

    async def create(
        self, *, request_key: UUID, source_id: UUID, scope_digest: str,
    ) -> IngestionJobView:
        job = _decode_response(
            await self._request(
                "POST", "", request_key=request_key,
                payload=IngestionCreateCommand(expected_scope_digest=scope_digest),
            ), IngestionJobView, "knowledge_ingestion_job_view_v1",
        )
        require_job_scope(job, source_id=source_id, scope_digest=scope_digest)
        if job.creation_request_key != request_key:
            raise IngestionClientError("API_RESPONSE_INVALID")
        self.mutation_result_confirmed = True
        return job

    async def execute(
        self, *, job_id: UUID, source_id: UUID, scope_digest: str, expected_revision: int,
    ) -> IngestionJobView:
        job = _decode_response(
            await self._request(
                "POST", f"/{job_id}/execute", executing=True,
                payload=IngestionRevisionCommand(expected_revision=expected_revision),
            ), IngestionJobView, "knowledge_ingestion_job_view_v1",
        )
        require_job_scope(job, source_id=source_id, scope_digest=scope_digest)
        if job.id != job_id or job.revision is None or job.revision <= expected_revision:
            raise IngestionClientError("API_RESPONSE_INVALID")
        self.mutation_result_confirmed = True
        return job

    async def stop_creation(self, request_key: UUID) -> IngestionCreationStatus:
        status = _decode_response(
            await self._request(
                "POST", f"/creation-requests/{request_key}/stop",
                payload=IngestionCreationStopCommand(confirm_stop=True),
            ), IngestionCreationStatus, "knowledge_ingestion_creation_status_v1",
        )
        _require_creation_status(status, request_key)
        if status.status == "unobserved":
            raise IngestionClientError("API_RESPONSE_INVALID")
        self.mutation_result_confirmed = True
        return status

    async def transition_job(
        self, job: IngestionJobView, *, transition: Literal["cancel", "expire"],
        expected_revision: int,
    ) -> IngestionJobView:
        """只使用当前Job事实；来源不可用不阻止本人停止或记录服务器确认的到期。"""
        if job.integrity_status != "recorded" or job.status == "unknown":
            raise IngestionClientError("JOB_INTEGRITY_UNCONFIRMED")
        if job.revision != expected_revision:
            raise IngestionClientError("JOB_REVISION_CHANGED")
        if job.source_id is None or job.scope_digest is None or job.counts is None:
            raise IngestionClientError("API_RESPONSE_INVALID")
        if transition == "cancel":
            available, expected_status = job.cancel_available, "cancelled"
        elif transition == "expire":
            available, expected_status = job.expire_available, "failed"
        else:
            raise IngestionClientError("JOB_TRANSITION_UNAVAILABLE")
        if not available or job.status not in {"queued", "running", "retry_wait"}:
            raise IngestionClientError("JOB_TRANSITION_UNAVAILABLE")
        result = _decode_response(
            await self._request(
                "POST", f"/{job.id}/{transition}",
                payload=IngestionRevisionCommand(expected_revision=expected_revision),
            ), IngestionJobView, "knowledge_ingestion_job_view_v1",
        )
        require_job_scope(result, source_id=job.source_id, scope_digest=job.scope_digest)
        if (
            result.id != job.id or result.revision is None or result.revision <= expected_revision
            or result.status != expected_status or result.counts != job.counts
            or result.creation_request_key != job.creation_request_key
            or (transition == "expire" and result.failure_code not in {
                "budget_exhausted", "recovery_required",
            })
        ):
            raise IngestionClientError("API_RESPONSE_INVALID")
        self.mutation_result_confirmed = True
        return result
