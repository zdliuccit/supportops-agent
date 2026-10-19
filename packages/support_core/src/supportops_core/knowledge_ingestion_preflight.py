"""显式离线配置预检；不加载应用配置、不访问来源或数据库、不授予执行许可。"""

import argparse
import json
import re
from datetime import UTC, datetime, timedelta
from typing import Literal, NoReturn, Sequence

from pydantic import Field

from supportops_core.knowledge_ingestion_contracts import (
    INGESTION_MANUAL_WINDOW_SECONDS, Digest, IngestionContract, ingestion_scope_digest,
)

PreflightCode = Literal[
    "CONFIGURATION_MATCHES", "ARGUMENTS_INVALID", "APPROVAL_FILE_UNAVAILABLE",
    "APPROVAL_JSON_INVALID", "APPROVAL_CONTRACT_INVALID", "SCOPE_MISMATCH",
    "APPROVAL_EXPIRED", "CREATION_WINDOW_INSUFFICIENT", "PREFLIGHT_UNAVAILABLE",
    "PREFLIGHT_INTERRUPTED",
    "READER_POLICY_REQUIRED", "READER_POLICY_NOT_APPLICABLE", "READER_POLICY_FILE_UNAVAILABLE",
    "READER_POLICY_JSON_INVALID", "READER_POLICY_CONTRACT_INVALID", "READER_POLICY_SCOPE_MISMATCH",
    "READER_POLICY_EXPIRED", "CONFIGURATION_CHANGED",
]


class IngestionPreflightReport(IngestionContract):
    """只输出固定状态及必要元数据，不回显配置路径、清单、人员或原始错误。"""

    schema_version: Literal["knowledge_ingestion_preflight_v2"] = "knowledge_ingestion_preflight_v2"
    status: Literal["configuration_matches", "blocked"]
    code: PreflightCode
    observed_at: datetime
    scope_digest: Digest | None = None
    object_count: int | None = None
    valid_until: datetime | None = None
    reader_policy_checked: bool = Field(default=False, description="两份配置范围及物理根等已匹配；不表示读取器可用或获准执行。")
    reader_policy_digest: Digest | None = Field(default=None, description="本次匹配的只读策略规范摘要；不是新的批准或签名。")
    reader_policy_valid_until: datetime | None = Field(default=None, description="已匹配读取器策略的独立截止时间。")
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


def _config_path(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return (value.startswith("/") and 0 < len(value.encode("utf-8")) <= 4096
            and "\\" not in value and not any(ord(char) < 32 or ord(char) == 127 for char in value)
            and all(part not in {"", ".", ".."} for part in value.split("/")[1:]))
    except UnicodeError:
        return False


def preflight_approval_file(*, approval_file: str, expected_scope_digest: str,
    reader_policy_file: str | None = None,
) -> IngestionPreflightReport:
    """核对显式批准及新隔离模式所需的读取策略；不读取来源或连接执行器。"""
    if (not isinstance(expected_scope_digest, str) or re.fullmatch(r"[0-9a-f]{64}", expected_scope_digest) is None
        or not _config_path(approval_file) or (reader_policy_file is not None and not _config_path(reader_policy_file))):
        return IngestionPreflightReport(status="blocked", code="ARGUMENTS_INVALID", observed_at=datetime.now(UTC))
    # 参数通过后才加载完整运行时解析器；包内源码不可用也由命令统一隐藏异常。
    from supportops_core.knowledge_ingestion_approval import (
        ApprovalFileError, read_local_markdown_approval_file,
    )
    from supportops_core.knowledge_markdown_source import LOCAL_MARKDOWN_ISOLATED_ADAPTER_MANIFEST_DIGEST
    from supportops_core.knowledge_reader_isolated import (
        KnowledgeReaderPolicyError, isolated_reader_manifest_digest, read_isolated_reader_policy_file,
    )
    from supportops_core.knowledge_parser_isolated import isolated_parser_manifest_digest

    try:
        record = read_local_markdown_approval_file(approval_file)
    except ApprovalFileError as error:
        return IngestionPreflightReport(status="blocked", code=error.code, observed_at=datetime.now(UTC))

    observed_at = datetime.now(UTC)
    scope_digest = ingestion_scope_digest(record.write_policy.scope)
    if scope_digest != expected_scope_digest:
        # 不把配置中另一范围的摘要、人员、文件数或期限返回给错误调用。
        return IngestionPreflightReport(status="blocked", code="SCOPE_MISMATCH", observed_at=observed_at)
    isolated_reader = record.write_policy.scope.adapter_manifest_digest == LOCAL_MARKDOWN_ISOLATED_ADAPTER_MANIFEST_DIGEST
    if isolated_reader and reader_policy_file is None:
        return IngestionPreflightReport(status="blocked", code="READER_POLICY_REQUIRED", observed_at=observed_at)
    if not isolated_reader and reader_policy_file is not None:
        return IngestionPreflightReport(status="blocked", code="READER_POLICY_NOT_APPLICABLE", observed_at=observed_at)

    reader_policy = None
    if reader_policy_file is not None:
        try:
            reader_policy = read_isolated_reader_policy_file(reader_policy_file,
                adapter_digest=record.write_policy.scope.adapter_manifest_digest,
                parser_digest=record.write_policy.scope.parser_manifest_digest)
        except KnowledgeReaderPolicyError as error:
            code_by_error: dict[str, PreflightCode] = {
                "READER_POLICY_FILE_UNAVAILABLE": "READER_POLICY_FILE_UNAVAILABLE",
                "READER_POLICY_JSON_INVALID": "READER_POLICY_JSON_INVALID",
                "READER_POLICY_CONTRACT_INVALID": "READER_POLICY_CONTRACT_INVALID",
            }
            return IngestionPreflightReport(status="blocked", code=code_by_error.get(error.code, "PREFLIGHT_UNAVAILABLE"),
                observed_at=datetime.now(UTC))
        if (reader_policy.scope_digest != scope_digest
            or reader_policy.selection_digest != record.write_policy.scope.source_contract_digest
            or reader_policy.paths != tuple(sorted(record.relative_paths))
            or reader_policy.root_device != record.source_root_device or reader_policy.root_inode != record.source_root_inode):
            return IngestionPreflightReport(status="blocked", code="READER_POLICY_SCOPE_MISMATCH", observed_at=datetime.now(UTC))
        # 再读各自当前文件；不把两份独立配置的顺序检查说成原子授权快照。
        try:
            current_record = read_local_markdown_approval_file(approval_file)
            current_policy = read_isolated_reader_policy_file(reader_policy_file,
                adapter_digest=current_record.write_policy.scope.adapter_manifest_digest,
                parser_digest=current_record.write_policy.scope.parser_manifest_digest)
        except (ApprovalFileError, KnowledgeReaderPolicyError):
            return IngestionPreflightReport(status="blocked", code="CONFIGURATION_CHANGED", observed_at=datetime.now(UTC))
        if (current_record != record or current_policy != reader_policy
            or isolated_reader_manifest_digest() != record.write_policy.scope.adapter_manifest_digest
            or isolated_parser_manifest_digest() != record.write_policy.scope.parser_manifest_digest):
            return IngestionPreflightReport(status="blocked", code="CONFIGURATION_CHANGED", observed_at=datetime.now(UTC))

    observed_at = datetime.now(UTC)
    valid_until = min(record.write_policy.valid_until, reader_policy.valid_until) if reader_policy else record.write_policy.valid_until
    remaining = valid_until - observed_at
    window_sufficient = remaining >= timedelta(seconds=INGESTION_MANUAL_WINDOW_SECONDS)
    code: PreflightCode = "CONFIGURATION_MATCHES"
    if record.write_policy.valid_until <= observed_at:
        code = "APPROVAL_EXPIRED"
    elif reader_policy is not None and reader_policy.valid_until <= observed_at:
        code = "READER_POLICY_EXPIRED"
    elif not window_sufficient:
        code = "CREATION_WINDOW_INSUFFICIENT"
    return IngestionPreflightReport(
        status="configuration_matches" if window_sufficient else "blocked", code=code,
        observed_at=observed_at, scope_digest=scope_digest, object_count=len(record.relative_paths),
        valid_until=valid_until, new_job_window_sufficient=window_sufficient,
        reader_policy_checked=reader_policy is not None,
        reader_policy_digest=reader_policy.policy_digest if reader_policy else None,
        reader_policy_valid_until=reader_policy.valid_until if reader_policy else None,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = _ArgumentParser(
        prog="supportops-knowledge-ingestion-preflight", allow_abbrev=False,
        description="离线核对运维批准及可选独立读取策略；不读取知识文件、连接数据库或启用导入。",
    )
    parser.add_argument("--approval-file", action=_SingleValue, required=True, help="受保护批准配置的绝对路径。")
    parser.add_argument("--expected-scope-digest", action=_SingleValue, required=True,
        help="独立核对的64位小写SHA-256范围摘要。")
    parser.add_argument("--reader-policy-file", action=_SingleValue,
        help="新隔离读取模式必需的受保护读取策略绝对路径；旧读取模式不适用。")
    try:
        args = parser.parse_args(argv)
        report = preflight_approval_file(
            approval_file=args.approval_file, expected_scope_digest=args.expected_scope_digest,
            reader_policy_file=args.reader_policy_file,
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
