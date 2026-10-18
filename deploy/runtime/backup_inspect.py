"""只读核对备份产物或恢复引用配置，不加载应用配置、联网或恢复归档。"""

import argparse
import hashlib
import json
import os
import re
import stat
import sys
import time
from collections.abc import Iterator, Sequence
from contextlib import ExitStack, contextmanager
from datetime import UTC, datetime
from typing import NoReturn

_MAX_ARCHIVE_BYTES = 64 * 1024**3
_FILES = frozenset({"database.dump", "manifest.json", "COMPLETED"})
_FIXED_FIELDS: dict[str, object] = {
    "schema_version": "supportops_backup_manifest_v1",
    "archive": "database.dump", "scope": "single_database_all_schemas",
    "reference_contents_validated": False, "storage_approval": "operator_declared",
    "restore_verified": False, "pitr_available": False, "external_files_included": False,
    "redis_included": False, "cluster_globals_included": False,
}
_VARIABLE_FIELDS = frozenset({
    "backup_id", "target_alias", "started_at", "finished_at", "archive_bytes",
    "archive_sha256", "postgres_major", "checkpoint_schema", "recovery_references_sha256",
})
_REFERENCE_FILE = "recovery-references.json"
_REFERENCE_GROUPS = {
    "application_release": {
        "backend_image_digest", "web_image_digest", "source_revision",
    },
    "database_prerequisites": {
        "postgres_major", "roles_configuration_reference", "extensions_configuration_reference",
    },
    "secret_references": {
        "sealed_credential_key_revisions_reference", "identity_signing_key_revisions_reference",
        "provider_access_recovery_reference",
    },
    "external_files": {
        "coverage", "approved_source_backup_reference", "knowledge_ingestion_approval_reference",
    },
    "long_term_memory": {"enabled", "backup_reference"},
    "retention_and_storage": {
        "approved_policy_reference", "encrypted_storage_reference", "legal_hold_status",
        "legal_hold_reference", "rpo_seconds", "rto_seconds",
    },
}


class InspectionError(Exception):
    """只允许本模块固定码；不跨边界输出路径、清单或原异常。"""


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise InspectionError("BACKUP_INSPECTION_ARGUMENTS_INVALID") from None


class _SingleValue(argparse.Action):
    def __call__(
        self, parser: argparse.ArgumentParser, namespace: argparse.Namespace,
        values: object, option_string: str | None = None,
    ) -> None:
        if getattr(namespace, self.dest, None) is not None:
            raise InspectionError("BACKUP_INSPECTION_ARGUMENTS_INVALID")
        setattr(namespace, self.dest, values)


def _arguments(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = _Parser(
        description="只读核对备份字节或恢复引用配置，不读取凭据或连接数据库。",
        allow_abbrev=False,
    )
    parser.add_argument("--mode", action=_SingleValue, choices=("artifact", "references"))
    for name in (
        "directory", "expected-backup-id", "expected-target-alias",
        "expected-checkpoint-schema", "expected-references-sha256",
    ):
        parser.add_argument("--" + name, action=_SingleValue)
    parser.add_argument("--max-bytes", action=_SingleValue)
    parser.add_argument("--timeout-seconds", action=_SingleValue)
    args = parser.parse_args(argv)
    args.mode = args.mode or "artifact"
    if args.directory is None or args.expected_references_sha256 is None:
        raise InspectionError("BACKUP_INSPECTION_ARGUMENTS_INVALID")
    valid = (
        0 < len(args.directory) <= 4096 and args.directory.startswith("/")
        and not any(ord(char) < 32 or ord(char) == 127 for char in args.directory)
        and not {".", ".."}.intersection(args.directory.split("/"))
        and re.fullmatch(r"[a-f0-9]{64}", args.expected_references_sha256)
    )
    artifact_fields = (
        (args.expected_backup_id, r"supportops-backup\.[A-Za-z0-9]{12}"),
        (args.expected_target_alias, r"[A-Za-z0-9_-]{1,64}"),
        (args.expected_checkpoint_schema, r"[a-z_][a-z0-9_]{0,62}"),
    )
    if args.mode == "artifact":
        valid = valid and all(
            value is not None and re.fullmatch(pattern, value)
            for value, pattern in artifact_fields
        )
    else:
        valid = valid and all(value is None for value, _ in artifact_fields)
        valid = valid and args.max_bytes is None
    if not valid:
        raise InspectionError("BACKUP_INSPECTION_ARGUMENTS_INVALID")
    for field, default, upper in (
        ("max_bytes", _MAX_ARCHIVE_BYTES, _MAX_ARCHIVE_BYTES), ("timeout_seconds", 300, 3600),
    ):
        value = getattr(args, field)
        if value is not None and (
            re.fullmatch(r"[1-9][0-9]{0,11}", value) is None or not 1 <= int(value) <= upper
        ):
            raise InspectionError("BACKUP_INSPECTION_ARGUMENTS_INVALID")
        setattr(args, field, default if value is None else int(value))
    return args


def _deadline(expires_at: float) -> None:
    if time.monotonic() >= expires_at:
        raise InspectionError("BACKUP_INSPECTION_TIMEOUT")


@contextmanager
def _directory(path: str) -> Iterator[int]:
    # 逐级打开并保留目录句柄，不经符号链接定位；读到的是已打开对象，不是实时路径保证。
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    with ExitStack() as stack:
        descriptor = os.open("/", flags)
        stack.callback(os.close, descriptor)
        parts = [part for part in path.split("/") if part]
        if not parts or len(parts) > 128:
            raise InspectionError("BACKUP_DIRECTORY_UNSAFE")
        for part in parts:
            descriptor = os.open(part, flags, dir_fd=descriptor)
            stack.callback(os.close, descriptor)
        info = os.fstat(descriptor)
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise InspectionError("BACKUP_DIRECTORY_UNSAFE")
        yield descriptor


def _entries(directory: int, expected: frozenset[str] = _FILES) -> None:
    names: set[str] = set()
    with os.scandir(directory) as entries:
        for entry in entries:
            if entry.name not in expected or entry.name in names:
                raise InspectionError("BACKUP_ARTIFACT_INCOMPLETE")
            names.add(entry.name)
    if names != expected:
        raise InspectionError("BACKUP_ARTIFACT_INCOMPLETE")


def _fingerprint(info: os.stat_result) -> tuple[int, ...]:
    return (
        info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid,
        info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns,
    )


def _open_file(
    directory: int, name: str, upper: int, stack: ExitStack,
) -> tuple[int, os.stat_result]:
    descriptor = os.open(
        name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=directory,
    )
    stack.callback(os.close, descriptor)
    info = os.fstat(descriptor)
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_nlink != 1
        or stat.S_IMODE(info.st_mode) not in {0o400, 0o600} or not 0 < info.st_size <= upper):
        raise InspectionError("BACKUP_FILE_UNSAFE")
    return descriptor, info


def _read(descriptor: int, size: int, expires_at: float) -> bytes:
    data = bytearray()
    while len(data) < size:
        _deadline(expires_at)
        chunk = os.read(descriptor, min(65536, size - len(data)))
        if not chunk:
            raise InspectionError("BACKUP_ARTIFACT_CHANGED")
        data.extend(chunk)
    return bytes(data)


def _unique_fields(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def _reject_constant(value: str) -> NoReturn:
    raise ValueError


def _timestamp(value: object) -> datetime:
    if not isinstance(value, str) or re.fullmatch(
        r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", value,
    ) is None:
        raise ValueError
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


def _manifest(raw: bytes, args: argparse.Namespace, size: int) -> dict[str, object]:
    try:
        payload: object = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_fields,
            parse_constant=_reject_constant)
        if not isinstance(payload, dict) or set(payload) != set(_FIXED_FIELDS) | _VARIABLE_FIELDS:
            raise ValueError
        for key, expected in _FIXED_FIELDS.items():
            if type(payload[key]) is not type(expected) or payload[key] != expected:
                raise ValueError
        if (
            payload["backup_id"] != args.expected_backup_id
            or payload["target_alias"] != args.expected_target_alias
            or payload["checkpoint_schema"] != args.expected_checkpoint_schema
            or payload["recovery_references_sha256"] != args.expected_references_sha256
            or type(payload["archive_bytes"]) is not int or payload["archive_bytes"] != size
            or type(payload["postgres_major"]) is not int or not 10 <= payload["postgres_major"] <= 99
            or not isinstance(payload["archive_sha256"], str)
            or re.fullmatch(r"[a-f0-9]{64}", payload["archive_sha256"]) is None
            or _timestamp(payload["started_at"]) > _timestamp(payload["finished_at"])
        ):
            raise ValueError
        return payload
    except Exception:
        raise InspectionError("BACKUP_MANIFEST_MISMATCH") from None


def inspect_artifact(args: argparse.Namespace) -> dict[str, object]:
    if sys.platform not in {"linux", "darwin"} or os.geteuid() == 0:
        raise InspectionError("BACKUP_INSPECTION_IDENTITY_UNSUPPORTED")
    expires_at = time.monotonic() + args.timeout_seconds
    with _directory(args.directory) as directory, ExitStack() as files:
        _deadline(expires_at)
        _entries(directory)
        directory_state = os.fstat(directory)
        opened = {name: _open_file(directory, name, limit, files) for name, limit in (
            ("manifest.json", 16384), ("COMPLETED", 65), ("database.dump", args.max_bytes),
        )}
        manifest_fd, manifest_state = opened["manifest.json"]
        archive_fd, archive_state = opened["database.dump"]
        marker_fd, marker_state = opened["COMPLETED"]
        manifest_raw = _read(manifest_fd, manifest_state.st_size, expires_at)
        payload = _manifest(manifest_raw, args, archive_state.st_size)
        marker = _read(marker_fd, marker_state.st_size, expires_at)
        if marker != (str(payload["archive_sha256"]) + "\n").encode("ascii"):
            raise InspectionError("BACKUP_COMPLETION_MISMATCH")
        digest = hashlib.sha256()
        remaining = archive_state.st_size
        while remaining:
            _deadline(expires_at)
            chunk = os.read(archive_fd, min(1024 * 1024, remaining))
            if not chunk:
                raise InspectionError("BACKUP_ARTIFACT_CHANGED")
            digest.update(chunk)
            remaining -= len(chunk)
        if digest.hexdigest() != payload["archive_sha256"]:
            raise InspectionError("BACKUP_ARCHIVE_DIGEST_MISMATCH")
        _entries(directory)
        for name, (descriptor, before) in opened.items():
            current = os.stat(name, dir_fd=directory, follow_symlinks=False)
            if (_fingerprint(before) != _fingerprint(os.fstat(descriptor))
                or _fingerprint(before) != _fingerprint(current)):
                raise InspectionError("BACKUP_ARTIFACT_CHANGED")
        if _fingerprint(directory_state) != _fingerprint(os.fstat(directory)):
            raise InspectionError("BACKUP_ARTIFACT_CHANGED")
        _deadline(expires_at)
    return {
        "schema_version": "supportops_backup_inspection_v1", "status": "artifact_matched",
        "backup_id": args.expected_backup_id, "archive_bytes": archive_state.st_size,
        "archive_sha256": digest.hexdigest(),
        "manifest_sha256": hashlib.sha256(manifest_raw).hexdigest(),
        "expected_scope_matched": True, "restore_verified": False, "provenance_verified": False,
        "archive_readability_checked": False, "reference_contents_validated": False,
        "storage_encryption_verified": False, "external_files_checked": False,
    }


def _reference_configuration(raw: bytes) -> list[str]:
    # null表示尚未配置。引用只接受不透明登记号，不解引用、不输出其内容。
    try:
        payload: object = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_fields,
            parse_constant=_reject_constant)
        if (
            not isinstance(payload, dict)
            or set(payload) != {"schema_version", "approval_reference"} | set(_REFERENCE_GROUPS)
            or payload["schema_version"] != "supportops_backup_recovery_references_v1"
        ):
            raise ValueError
        for group, fields in _REFERENCE_GROUPS.items():
            if not isinstance(payload[group], dict) or set(payload[group]) != fields:
                raise ValueError

        missing: list[str] = []

        def require(value: object, field: str, pattern: str) -> None:
            if value is None:
                missing.append(field)
            elif not isinstance(value, str) or re.fullmatch(pattern, value) is None:
                raise ValueError

        reference_pattern = r"ref:[A-Za-z0-9][A-Za-z0-9._/-]{0,191}"
        require(payload["approval_reference"], "approval_reference", reference_pattern)
        for group in ("database_prerequisites", "secret_references", "retention_and_storage"):
            for field in sorted(_REFERENCE_GROUPS[group]):
                if field.endswith("_reference") and field != "legal_hold_reference":
                    require(payload[group][field], group + "." + field, reference_pattern)
        for field in sorted(_REFERENCE_GROUPS["application_release"]):
            pattern = r"[a-f0-9]{40}|[a-f0-9]{64}" if field == "source_revision" else (
                r"sha256:[a-f0-9]{64}"
            )
            require(payload["application_release"][field], "application_release." + field, pattern)
        for group, field, upper in (
            ("database_prerequisites", "postgres_major", 99),
            ("retention_and_storage", "rpo_seconds", 2147483647),
            ("retention_and_storage", "rto_seconds", 2147483647),
        ):
            value = payload[group][field]
            lower = 10 if field == "postgres_major" else 1
            if value is None:
                missing.append(group + "." + field)
            elif type(value) is not int or not lower <= value <= upper:
                raise ValueError

        external = payload["external_files"]
        coverage = external["coverage"]
        if coverage not in ("not_configured", "none", "independent_backup"):
            raise ValueError
        if coverage == "not_configured":
            missing.append("external_files.coverage")
        for field in ("approved_source_backup_reference", "knowledge_ingestion_approval_reference"):
            if coverage == "independent_backup":
                require(external[field], "external_files." + field, reference_pattern)
            elif external[field] is not None:
                raise ValueError

        memory = payload["long_term_memory"]
        if type(memory["enabled"]) is not bool:
            raise ValueError
        if memory["enabled"]:
            require(
                memory["backup_reference"], "long_term_memory.backup_reference", reference_pattern,
            )
        elif memory["backup_reference"] is not None:
            raise ValueError

        retention = payload["retention_and_storage"]
        if retention["legal_hold_status"] not in ("not_configured", "none", "active"):
            raise ValueError
        if retention["legal_hold_status"] == "not_configured":
            missing.append("retention_and_storage.legal_hold_status")
        if retention["legal_hold_status"] == "active":
            require(retention["legal_hold_reference"],
                "retention_and_storage.legal_hold_reference", reference_pattern)
        elif retention["legal_hold_reference"] is not None:
            raise ValueError
        return sorted(missing)
    except Exception:
        raise InspectionError("BACKUP_REFERENCE_CONFIGURATION_INVALID") from None


def inspect_references(args: argparse.Namespace) -> dict[str, object]:
    if sys.platform not in {"linux", "darwin"} or os.geteuid() == 0:
        raise InspectionError("BACKUP_INSPECTION_IDENTITY_UNSUPPORTED")
    expires_at = time.monotonic() + args.timeout_seconds
    with _directory(args.directory) as directory, ExitStack() as files:
        expected = frozenset({_REFERENCE_FILE})
        _entries(directory, expected)
        directory_state = os.fstat(directory)
        descriptor, before = _open_file(directory, _REFERENCE_FILE, 128 * 1024, files)
        raw = _read(descriptor, before.st_size, expires_at)
        digest = hashlib.sha256(raw).hexdigest()
        if digest != args.expected_references_sha256:
            raise InspectionError("BACKUP_REFERENCE_DIGEST_MISMATCH")
        missing = _reference_configuration(raw)
        _entries(directory, expected)
        current = os.stat(_REFERENCE_FILE, dir_fd=directory, follow_symlinks=False)
        if (
            _fingerprint(before) != _fingerprint(os.fstat(descriptor))
            or _fingerprint(before) != _fingerprint(current)
            or _fingerprint(directory_state) != _fingerprint(os.fstat(directory))
        ):
            raise InspectionError("BACKUP_ARTIFACT_CHANGED")
        _deadline(expires_at)
    return {
        "schema_version": "supportops_backup_reference_inspection_v1",
        "status": "configuration_incomplete" if missing else "configuration_complete",
        "references_sha256": digest, "missing_fields": missing,
        "approval_verified": False, "reference_targets_checked": False,
        "secret_recoverability_verified": False, "external_files_checked": False,
        "storage_encryption_verified": False, "restore_verified": False,
        "backup_authorized": False,
    }


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = _arguments(argv)
        result = inspect_references(args) if args.mode == "references" else inspect_artifact(args)
        exit_code = 1 if result["status"] == "configuration_incomplete" else 0
    except InspectionError as exc:
        result = {
            "schema_version": "supportops_backup_inspection_v1",
            "status": "unconfirmed", "code": str(exc),
        }
        exit_code = 2 if str(exc) == "BACKUP_INSPECTION_ARGUMENTS_INVALID" else 1
    except KeyboardInterrupt:
        result = {
            "schema_version": "supportops_backup_inspection_v1", "status": "unconfirmed",
            "code": "BACKUP_INSPECTION_INTERRUPTED",
        }
        exit_code = 130
    except Exception:
        result = {
            "schema_version": "supportops_backup_inspection_v1", "status": "unconfirmed",
            "code": "BACKUP_INSPECTION_UNAVAILABLE",
        }
        exit_code = 1
    try:
        print(json.dumps(result, ensure_ascii=False, separators=(",", ":")), flush=True)
    except (OSError, ValueError):
        return 1
    except KeyboardInterrupt:
        return 130
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
