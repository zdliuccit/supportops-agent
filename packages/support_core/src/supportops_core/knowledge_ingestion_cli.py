"""显式本机人工导入命令；令牌仅从stdin读取，不登录、不直连来源或数据库。"""

import argparse
import asyncio
import json
import re
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Literal, NoReturn
from uuid import UUID

from pydantic import Field, SecretStr

from supportops_core.knowledge_ingestion_client import (
    IngestionClientCode,
    IngestionClientError,
    LocalIngestionClient,
    local_api_origin,
    require_executable_job,
    require_job_scope,
    require_readiness,
    validate_api_token,
)
from supportops_core.knowledge_ingestion_contracts import (
    Digest,
    IngestionContract,
    IngestionCounts,
    IngestionCreationStatus,
    IngestionFailure,
    IngestionJobView,
    IngestionSourceReadiness,
)

Operation = Literal[
    "readiness", "request-status", "job", "create", "execute", "stop-creation", "cancel", "expire",
]
ReportCode = Literal[
    "READINESS_OBSERVED", "JOB_OBSERVED", "REQUEST_STATUS_OBSERVED",
    "CREATE_RESPONSE_CONFIRMED", "EXECUTION_RESPONSE_CONFIRMED", "STOP_RESPONSE_CONFIRMED",
    "CANCEL_RESPONSE_CONFIRMED", "EXPIRY_RESPONSE_CONFIRMED",
    "ARGUMENTS_INVALID", "TOKEN_STDIN_REQUIRED", "OUTCOME_UNCERTAIN",
    "CLIENT_INTERRUPTED", "CLIENT_UNAVAILABLE",
]
_WRITES = frozenset({"create", "execute", "stop-creation", "cancel", "expire"})


class IngestionCliReport(IngestionContract):
    """封闭元数据报告；不输出来源名称、路径、正文、URL、令牌或上游原文。"""

    schema_version: Literal["knowledge_ingestion_cli_v1"] = "knowledge_ingestion_cli_v1"
    operation: Operation | None = None
    status: Literal["observed", "blocked", "outcome_uncertain"]
    code: ReportCode | IngestionClientCode
    reported_at: datetime
    report_clock: Literal["client_utc"] = "client_utc"
    request_key: UUID | None = None
    job_id: UUID | None = None
    source_id: UUID | None = None
    scope_digest: Digest | None = None
    job_status: Literal[
        "queued", "running", "retry_wait", "completed", "failed", "cancelled", "unknown",
    ] | None = None
    revision: int | None = Field(default=None, ge=1)
    counts: IngestionCounts | None = None
    failure_code: IngestionFailure | None = None
    request_status: Literal["unobserved", "recorded", "stopped"] | None = None
    stopped_at: datetime | None = None
    source_available: bool | None = None
    creation_available: bool | None = None
    object_count: int | None = Field(default=None, ge=1, le=100)
    source_valid_until: datetime | None = None
    api_observed_at: datetime | None = None
    # attempted只表示客户端进入写调用，不证明字节已发送或服务器已提交。
    write_attempted: bool = False
    mutation_result_confirmed: bool = False
    client_error_code: IngestionClientCode | None = None
    recovery_operation: Literal["request-status", "job"] | None = None
    automatic_retries_enabled: Literal[False] = False
    automatic_publish_enabled: Literal[False] = False
    direct_source_access: Literal[False] = False


class _ArgumentError(Exception):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise _ArgumentError from None


class _SingleValue(argparse.Action):
    def __call__(
        self, parser: argparse.ArgumentParser, namespace: argparse.Namespace,
        values: object, option_string: str | None = None,
    ) -> None:
        if getattr(namespace, self.dest, None) is not None:
            raise _ArgumentError from None
        setattr(namespace, self.dest, True if self.nargs == 0 else values)


def _uuid(value: str) -> UUID:
    try:
        parsed = UUID(value)
    except ValueError:
        raise _ArgumentError from None
    if str(parsed) != value:
        raise _ArgumentError from None
    return parsed


def _digest(value: str) -> str:
    if re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise _ArgumentError from None
    return value


def _revision(value: str) -> int:
    if not value.isascii() or not value.isdecimal() or len(value) > 12 or int(value) < 1:
        raise _ArgumentError from None
    return int(value)


def _arguments(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = _Parser(
        prog="supportops-knowledge-ingestion", allow_abbrev=False,
        description="本机人工导入；有效会话令牌仅从非终端stdin读入，不登录或自动重试。",
    )
    commands = parser.add_subparsers(dest="operation", required=True, parser_class=_Parser)
    for operation in (
        "readiness", "request-status", "job", "create", "execute", "stop-creation", "cancel", "expire",
    ):
        command = commands.add_parser(operation, allow_abbrev=False)
        command.add_argument("--api-origin", action=_SingleValue, required=True,
            help="明确的本机HTTP来源，仅127.0.0.1或[::1]，不接受远程主机或路径。")
        if operation in {"request-status", "create", "stop-creation"}:
            command.add_argument("--request-key", action=_SingleValue, type=_uuid, required=True,
                help="本人已独立保存的规范小写UUID原创建键；命令不会自动生成新键。")
        if operation in {"job", "execute", "cancel", "expire"}:
            command.add_argument("--job-id", action=_SingleValue, type=_uuid, required=True)
        if operation in {"create", "execute"}:
            command.add_argument("--source-id", action=_SingleValue, type=_uuid, required=True)
            command.add_argument("--scope-digest", action=_SingleValue, type=_digest, required=True)
        if operation in {"execute", "cancel", "expire"}:
            command.add_argument("--expected-revision", action=_SingleValue, type=_revision, required=True)
        if operation in _WRITES:
            command.add_argument("--confirm", action=_SingleValue, nargs=0, const=True,
                help="明确确认本次唯一写意图；不批准来源、发布或更改ACL。")
    args = parser.parse_args(argv)
    if args.operation in _WRITES and getattr(args, "confirm", None) is None:
        raise _ArgumentError from None
    args.api_origin = local_api_origin(args.api_origin)
    return args


def _read_token() -> SecretStr:
    if sys.stdin.isatty():
        raise _ArgumentError from None
    raw = sys.stdin.buffer.read(8195)
    if raw.endswith(b"\r\n"):
        raw = raw[:-2]
    elif raw.endswith(b"\n"):
        raw = raw[:-1]
    try:
        return validate_api_token(raw.decode("ascii"))
    except UnicodeError:
        raise IngestionClientError("TOKEN_INVALID") from None


def _job_fields(job: IngestionJobView) -> dict[str, object]:
    if job.integrity_status != "recorded" or job.status == "unknown":
        raise IngestionClientError("JOB_INTEGRITY_UNCONFIRMED")
    if (
        job.source_id is None or job.scope_digest is None or job.revision is None
        or job.counts is None
    ):
        raise IngestionClientError("API_RESPONSE_INVALID")
    return {
        "job_id": job.id, "source_id": job.source_id, "scope_digest": job.scope_digest,
        "request_key": job.creation_request_key, "job_status": job.status,
        "revision": job.revision, "counts": job.counts, "failure_code": job.failure_code,
    }


def _creation_fields(status: IngestionCreationStatus) -> dict[str, object]:
    result: dict[str, object] = {
        "request_key": status.request_key, "request_status": status.status,
        "stopped_at": status.stopped_at, "api_observed_at": status.observed_at,
    }
    if status.job is not None:
        result.update(_job_fields(status.job))
    return result


def _readiness_fields(readiness: IngestionSourceReadiness) -> dict[str, object]:
    if readiness.observed_at.tzinfo is None:
        raise IngestionClientError("API_RESPONSE_INVALID")
    if readiness.available:
        if readiness.source_id is None or readiness.scope_digest is None:
            raise IngestionClientError("API_RESPONSE_INVALID")
        require_readiness(readiness, source_id=readiness.source_id,
            scope_digest=readiness.scope_digest, creating=False)
    elif readiness.create_available or any(value is not None for value in (
        readiness.source_id, readiness.source_name, readiness.scope_digest,
        readiness.object_count, readiness.valid_until,
    )):
        raise IngestionClientError("API_RESPONSE_INVALID")
    return {
        "source_available": readiness.available, "creation_available": readiness.create_available,
        "source_id": readiness.source_id, "scope_digest": readiness.scope_digest,
        "object_count": readiness.object_count, "source_valid_until": readiness.valid_until,
        "api_observed_at": readiness.observed_at,
    }


async def _operate(args: argparse.Namespace, client: LocalIngestionClient) -> IngestionCliReport:
    code: ReportCode
    fields: dict[str, object]
    if args.operation == "readiness":
        fields = _readiness_fields(await client.readiness())
        code = "READINESS_OBSERVED"
    elif args.operation == "job":
        fields = _job_fields(await client.job(args.job_id))
        code = "JOB_OBSERVED"
    elif args.operation in {"request-status", "stop-creation"}:
        status = await client.request_status(args.request_key)
        if args.operation == "stop-creation" and status.status == "unobserved":
            status = await client.stop_creation(args.request_key)
            code = "STOP_RESPONSE_CONFIRMED"
        else:
            code = "REQUEST_STATUS_OBSERVED"
        fields = _creation_fields(status)
    elif args.operation == "create":
        status = await client.request_status(args.request_key)
        if status.status == "stopped":
            raise IngestionClientError("REQUEST_STOPPED")
        if status.job is not None:
            require_job_scope(status.job, source_id=args.source_id, scope_digest=args.scope_digest)
            fields = _creation_fields(status)
            code = "REQUEST_STATUS_OBSERVED"
        else:
            readiness = await client.readiness()
            require_readiness(readiness, source_id=args.source_id,
                scope_digest=args.scope_digest, creating=True)
            fields = _job_fields(await client.create(
                request_key=args.request_key, source_id=args.source_id, scope_digest=args.scope_digest,
            ))
            code = "CREATE_RESPONSE_CONFIRMED"
    elif args.operation == "execute":
        job = await client.job(args.job_id)
        require_job_scope(job, source_id=args.source_id, scope_digest=args.scope_digest)
        readiness = await client.readiness()
        require_readiness(readiness, source_id=args.source_id,
            scope_digest=args.scope_digest, creating=False)
        require_executable_job(job, readiness, expected_revision=args.expected_revision)
        fields = _job_fields(await client.execute(
            job_id=args.job_id, source_id=args.source_id, scope_digest=args.scope_digest,
            expected_revision=args.expected_revision,
        ))
        code = "EXECUTION_RESPONSE_CONFIRMED"
    elif args.operation in {"cancel", "expire"}:
        job = await client.job(args.job_id)
        # 不查询新的来源readiness；实际服务器保持本人当前授权及任务围栏校验。
        fields = _job_fields(await client.transition_job(
            job, transition=args.operation, expected_revision=args.expected_revision,
        ))
        code = (
            "CANCEL_RESPONSE_CONFIRMED" if args.operation == "cancel" else "EXPIRY_RESPONSE_CONFIRMED"
        )
    else:
        raise _ArgumentError from None
    return IngestionCliReport.model_validate({
        **fields, "operation": args.operation, "status": "observed", "code": code,
        "reported_at": datetime.now(UTC), "write_attempted": client.write_attempted,
        "mutation_result_confirmed": client.mutation_result_confirmed,
    })


def main(argv: Sequence[str] | None = None) -> int:
    args: argparse.Namespace | None = None
    client: LocalIngestionClient | None = None
    exit_code = 1
    try:
        command_args = _arguments(argv)
        args = command_args
        if sys.stdin.isatty():
            report = IngestionCliReport(status="blocked", code="TOKEN_STDIN_REQUIRED",
                reported_at=datetime.now(UTC), operation=args.operation)
            exit_code = 2
        else:
            active_client = LocalIngestionClient(origin=command_args.api_origin, token=_read_token())
            client = active_client

            async def run() -> IngestionCliReport:
                async with asyncio.timeout(90):
                    return await _operate(command_args, active_client)

            report = asyncio.run(run())
            exit_code = 0
    except (Exception, KeyboardInterrupt) as error:
        # 包括请求取消/中断：只用客户端意图标识，不推断服务器未接收或事务已回滚。
        attempted = client is not None and client.write_attempted
        reason: ReportCode | IngestionClientCode
        if attempted:
            reason, exit_code = "OUTCOME_UNCERTAIN", 4
        elif isinstance(error, _ArgumentError):
            reason, exit_code = "ARGUMENTS_INVALID", 2
        elif isinstance(error, IngestionClientError):
            reason, exit_code = error.code, 3
        elif isinstance(error, KeyboardInterrupt):
            reason, exit_code = "CLIENT_INTERRUPTED", 130
        else:
            reason, exit_code = "CLIENT_UNAVAILABLE", 1
        report = IngestionCliReport(
            status="outcome_uncertain" if attempted else "blocked", code=reason,
            reported_at=datetime.now(UTC), operation=args.operation if args is not None else None,
            request_key=getattr(args, "request_key", None), job_id=getattr(args, "job_id", None),
            write_attempted=attempted,
            client_error_code=error.code if isinstance(error, IngestionClientError) else None,
            recovery_operation=(
                "request-status" if getattr(args, "request_key", None) is not None else "job"
            ) if attempted else None,
        )
    try:
        print(
            json.dumps(report.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":")),
            flush=True,
        )
    except (OSError, ValueError):
        # 输出管道丢失不能触发重发；原请求/Job始终由调用者独立保留。
        return 4 if client is not None and client.write_attempted else 1
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
