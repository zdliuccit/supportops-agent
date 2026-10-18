"""显式离线配置预检；不加载应用配置、不访问来源或数据库、不授予执行许可。"""

import argparse
import json
import re
from datetime import UTC, datetime, timedelta
from typing import Literal, NoReturn, Sequence

from supportops_core.knowledge_ingestion_contracts import (
    INGESTION_MANUAL_WINDOW_SECONDS, Digest, IngestionContract, ingestion_scope_digest,
)

PreflightCode = Literal[
    "CONFIGURATION_MATCHES", "ARGUMENTS_INVALID", "APPROVAL_FILE_UNAVAILABLE",
    "APPROVAL_JSON_INVALID", "APPROVAL_CONTRACT_INVALID", "SCOPE_MISMATCH",
    "APPROVAL_EXPIRED", "CREATION_WINDOW_INSUFFICIENT", "PREFLIGHT_UNAVAILABLE",
    "PREFLIGHT_INTERRUPTED",
]


class IngestionPreflightReport(IngestionContract):
    """只输出固定状态及必要元数据，不回显配置路径、清单、人员或原始错误。"""

    schema_version: Literal["knowledge_ingestion_preflight_v1"] = "knowledge_ingestion_preflight_v1"
    status: Literal["configuration_matches", "blocked"]
    code: PreflightCode
    observed_at: datetime
    scope_digest: Digest | None = None
    object_count: int | None = None
    valid_until: datetime | None = None
    minimum_creation_window_seconds: int = INGESTION_MANUAL_WINDOW_SECONDS
    new_job_window_sufficient: bool = False
    runtime_authorization_checked: Literal[False] = False
    source_files_read: Literal[False] = False
    ingestion_started: Literal[False] = False


class _ArgumentError(Exception):
    pass


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        # argparse默认会回显错误值和路径；维护命令只返回固定码。
        raise _ArgumentError from None


class _SingleValue(argparse.Action):
    def __call__(self, parser: argparse.ArgumentParser, namespace: argparse.Namespace,
        values: object, option_string: str | None = None,
    ) -> None:
        if getattr(namespace, self.dest, None) is not None:
            # 重复路径/摘要不能悄悄采用最后一项，参数错误时不进入文件读取。
            raise _ArgumentError from None
        setattr(namespace, self.dest, values)


def preflight_approval_file(*, approval_file: str, expected_scope_digest: str) -> IngestionPreflightReport:
    """仅读一个受保护配置；调用方提供独立核对的范围摘要，不自动发现或生成批准。"""
    if re.fullmatch(r"[0-9a-f]{64}", expected_scope_digest) is None:
        return IngestionPreflightReport(status="blocked", code="ARGUMENTS_INVALID", observed_at=datetime.now(UTC))
    # 参数通过后才加载完整运行时解析器；包内源码不可用也由命令统一隐藏异常。
    from supportops_core.knowledge_ingestion_approval import (
        ApprovalFileError, read_local_markdown_approval_file,
    )

    try:
        record = read_local_markdown_approval_file(approval_file)
    except ApprovalFileError as error:
        return IngestionPreflightReport(status="blocked", code=error.code, observed_at=datetime.now(UTC))

    observed_at = datetime.now(UTC)
    scope_digest = ingestion_scope_digest(record.write_policy.scope)
    if scope_digest != expected_scope_digest:
        # 不把配置中另一范围的摘要、人员、文件数或期限返回给错误调用。
        return IngestionPreflightReport(status="blocked", code="SCOPE_MISMATCH", observed_at=observed_at)
    valid_until = record.write_policy.valid_until
    remaining = valid_until - observed_at
    window_sufficient = remaining >= timedelta(seconds=INGESTION_MANUAL_WINDOW_SECONDS)
    code: PreflightCode = "CONFIGURATION_MATCHES"
    if remaining <= timedelta(0):
        code = "APPROVAL_EXPIRED"
    elif not window_sufficient:
        code = "CREATION_WINDOW_INSUFFICIENT"
    return IngestionPreflightReport(
        status="configuration_matches" if window_sufficient else "blocked", code=code,
        observed_at=observed_at, scope_digest=scope_digest, object_count=len(record.relative_paths),
        valid_until=valid_until, new_job_window_sufficient=window_sufficient,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = _ArgumentParser(
        prog="supportops-knowledge-ingestion-preflight", allow_abbrev=False,
        description="离线核对一个运维批准配置；不读取知识文件、连接数据库或启用导入。",
    )
    parser.add_argument("--approval-file", action=_SingleValue, required=True, help="受保护批准配置的绝对路径。")
    parser.add_argument("--expected-scope-digest", action=_SingleValue, required=True,
        help="独立核对的64位小写SHA-256范围摘要。")
    try:
        args = parser.parse_args(argv)
        report = preflight_approval_file(
            approval_file=args.approval_file, expected_scope_digest=args.expected_scope_digest,
        )
    except _ArgumentError:
        report = IngestionPreflightReport(status="blocked", code="ARGUMENTS_INVALID", observed_at=datetime.now(UTC))
    except KeyboardInterrupt:
        report = IngestionPreflightReport(status="blocked", code="PREFLIGHT_INTERRUPTED", observed_at=datetime.now(UTC))
    except Exception:
        # 不输出异常链、配置原文或操作系统错误；没有隐式重试/备用配置。
        report = IngestionPreflightReport(status="blocked", code="PREFLIGHT_UNAVAILABLE", observed_at=datetime.now(UTC))
    print(json.dumps(report.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":")))
    if report.code == "ARGUMENTS_INVALID":
        return 2
    if report.code == "PREFLIGHT_INTERRUPTED":
        return 130
    if report.code == "PREFLIGHT_UNAVAILABLE":
        return 1
    return 0 if report.status == "configuration_matches" else 3


if __name__ == "__main__":
    raise SystemExit(main())
