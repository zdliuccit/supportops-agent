"""恢复前只读比较独立删除账本与恢复库快照；不执行恢复、重施、擦除或开放访问。"""

import argparse
import hashlib
import json
import os
import re
import stat
import sys
import time
from contextlib import ExitStack
from datetime import UTC, datetime
from typing import NoReturn
from uuid import UUID

MAX_BYTES = 4 * 1024 * 1024
MAX_FACTS = 4096
CATEGORIES = frozenset({"identity", "auth_sessions", "conversations", "messages", "runs", "events",
    "observations", "knowledge", "credentials", "actions", "approvals", "checkpoints", "memory",
    "backups", "external_copies", "governance_audit"})
FACT_FIELDS = frozenset({"schema_version", "id", "tenant_id", "subject_user_id", "erasure_request_id",
    "category", "resource_key_digest", "scope_digest", "action", "evidence_digest",
    "maintenance_identity", "record_digest", "recorded_at"})


class GateError(Exception):
    """仅保存本模块固定错误码，不携带清单、路径或底层异常。"""


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise GateError("RESTORE_GATE_ARGUMENTS_INVALID") from None


class _Once(argparse.Action):
    def __call__(self, parser: argparse.ArgumentParser, namespace: argparse.Namespace,
        values: object, option_string: str | None = None,
    ) -> None:
        if getattr(namespace, self.dest, None) is not None:
            raise GateError("RESTORE_GATE_ARGUMENTS_INVALID")
        setattr(namespace, self.dest, values)


def _uuid(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return str(UUID(value)) == value
    except ValueError:
        return False


def _digest(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[a-f0-9]{64}", value) is not None


def _arguments(argv: list[str] | None) -> argparse.Namespace:
    parser = _Parser(description="只读核对删除事实快照；匹配也不授予恢复环境访问许可。", allow_abbrev=False)
    for name in ("directory", "expected-ledger-id", "expected-ledger-generation", "expected-restore-id",
        "expected-backup-id", "expected-target-alias", "expected-ledger-sha256", "expected-restored-sha256",
        "max-age-seconds", "timeout-seconds"):
        parser.add_argument("--" + name, action=_Once)
    args = parser.parse_args(argv)
    if (args.directory is None or not args.directory.startswith("/") or len(args.directory) > 4096
        or "\\" in args.directory or any(ord(char) < 32 or ord(char) == 127 for char in args.directory)
        or any(part in {"", ".", ".."} for part in args.directory.split("/")[1:])
        or not _uuid(args.expected_ledger_id) or not _uuid(args.expected_restore_id)
        or not _digest(args.expected_ledger_sha256) or not _digest(args.expected_restored_sha256)
        or args.expected_backup_id is None
        or re.fullmatch(r"supportops-backup\.[A-Za-z0-9]{12}", args.expected_backup_id) is None
        or args.expected_target_alias is None
        or re.fullmatch(r"[A-Za-z0-9_-]{1,64}", args.expected_target_alias) is None):
        raise GateError("RESTORE_GATE_ARGUMENTS_INVALID")
    for name, default, maximum in (("expected_ledger_generation", None, 2**63 - 1),
        ("max_age_seconds", 300, 3600), ("timeout_seconds", 10, 60)):
        value = getattr(args, name)
        if value is None:
            if default is None:
                raise GateError("RESTORE_GATE_ARGUMENTS_INVALID")
            setattr(args, name, default)
        elif re.fullmatch(r"[1-9][0-9]{0,18}", value) is None or int(value) > maximum:
            raise GateError("RESTORE_GATE_ARGUMENTS_INVALID")
        else:
            setattr(args, name, int(value))
    return args


def _deadline(expires_at: float) -> None:
    if time.monotonic() >= expires_at:
        raise GateError("RESTORE_GATE_TIMEOUT")


def _fingerprint(value: os.stat_result) -> tuple[int, ...]:
    return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns,
        value.st_uid, value.st_gid, value.st_mode, value.st_nlink)


def _read(path: str, name: str, expires_at: float) -> tuple[bytes, tuple[int, ...]]:
    """逐级NOFOLLOW并保留目录FD；只读本人0400单链接文件，有限分段读取。"""
    try:
        with ExitStack() as stack:
            flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW
            directory = os.open("/", flags | os.O_DIRECTORY)
            stack.callback(os.close, directory)
            parts = path.split("/")[1:]
            if len(parts) > 128:
                raise ValueError()
            for part in [None, *parts]:
                _deadline(expires_at)
                if part is not None:
                    directory = os.open(part, flags | os.O_DIRECTORY, dir_fd=directory)
                    stack.callback(os.close, directory)
                info = os.fstat(directory)
                if info.st_uid not in {0, os.geteuid()} or stat.S_IMODE(info.st_mode) & 0o022:
                    raise ValueError()
            names: set[str] = set()
            with os.scandir(directory) as entries:
                for entry in entries:
                    _deadline(expires_at)
                    names.add(entry.name)
                    if len(names) > 2:
                        raise ValueError()
            if names != {"deletion-ledger.json", "restored-deletions.json"}:
                raise ValueError()
            descriptor = os.open(name, flags | os.O_NONBLOCK, dir_fd=directory)
            stack.callback(os.close, descriptor)
            before = os.fstat(descriptor)
            if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.geteuid()
                or stat.S_IMODE(before.st_mode) != 0o400 or before.st_nlink != 1
                or not 0 < before.st_size <= MAX_BYTES):
                raise ValueError()
            chunks: list[bytes] = []
            size = 0
            while True:
                _deadline(expires_at)
                chunk = os.read(descriptor, min(65536, MAX_BYTES + 1 - size))
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_BYTES:
                    raise ValueError()
                chunks.append(chunk)
            after = os.fstat(descriptor)
            current = os.stat(name, dir_fd=directory, follow_symlinks=False)
            if (_fingerprint(before) != _fingerprint(after) or _fingerprint(after) != _fingerprint(current)
                or size != after.st_size):
                raise ValueError()
            return b"".join(chunks), _fingerprint(after)
    except GateError:
        raise
    except Exception:
        raise GateError("RESTORE_GATE_FILE_UNAVAILABLE") from None


def _pairs(items: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in items:
        if key in result:
            raise ValueError()
        result[key] = value
    return result


def _constant(value: str) -> NoReturn:
    raise ValueError()


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _object(raw: bytes, expires_at: float) -> dict[str, object]:
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_constant)
        pending = [(value, 0)]
        nodes = 0
        while pending:
            _deadline(expires_at)
            item, depth = pending.pop()
            nodes += 1
            if depth > 8 or nodes > 100_000:
                raise ValueError()
            if isinstance(item, dict):
                pending.extend((child, depth + 1) for child in item.values())
            elif isinstance(item, list):
                pending.extend((child, depth + 1) for child in item)
        if not isinstance(value, dict):
            raise ValueError()
        return value
    except GateError:
        raise
    except Exception:
        raise GateError("RESTORE_GATE_JSON_INVALID") from None


def _time(value: object) -> datetime:
    if not isinstance(value, str) or len(value) > 32:
        raise ValueError()
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(parsed) or parsed.isoformat() != value:
        raise ValueError()
    return parsed


def _facts(snapshot: dict[str, object], now: datetime, args: argparse.Namespace,
    expires_at: float, *, restored: bool,
) -> dict[str, dict[str, object]]:
    try:
        fields = {"schema_version", "snapshot_id", "captured_at", "coverage", "facts"}
        fields |= {"restore_id", "backup_id", "target_alias"} if restored else {"ledger_id", "generation"}
        schema = "supportops_restored_deletion_snapshot_v1" if restored else "supportops_deletion_ledger_snapshot_v1"
        coverage = "all_restored_tombstones" if restored else "all_recorded_tombstones"
        if (set(snapshot) != fields or snapshot["schema_version"] != schema
            or snapshot["coverage"] != coverage or not _uuid(snapshot["snapshot_id"])):
            raise ValueError()
        if restored:
            if (snapshot["restore_id"] != args.expected_restore_id or snapshot["backup_id"] != args.expected_backup_id
                or snapshot["target_alias"] != args.expected_target_alias):
                raise ValueError()
        elif (snapshot["ledger_id"] != args.expected_ledger_id or type(snapshot["generation"]) is not int
            or snapshot["generation"] != args.expected_ledger_generation):
            raise ValueError()
        captured_at = _time(snapshot["captured_at"])
        age = (now - captured_at).total_seconds()
        if age < 0 or age > args.max_age_seconds:
            raise GateError("RESTORE_GATE_SNAPSHOT_STALE")
        entries = snapshot["facts"]
        if not isinstance(entries, list) or len(entries) > MAX_FACTS:
            raise ValueError()
        result: dict[str, dict[str, object]] = {}
        keys: set[tuple[str, ...]] = set()
        for fact in entries:
            _deadline(expires_at)
            if not isinstance(fact, dict) or set(fact) != FACT_FIELDS or fact["schema_version"] != "supportops_deletion_fact_v1":
                raise ValueError()
            if not all(_uuid(fact[name]) for name in ("id", "tenant_id", "subject_user_id", "erasure_request_id")):
                raise ValueError()
            if not all(_digest(fact[name]) for name in ("resource_key_digest", "scope_digest", "evidence_digest", "record_digest")):
                raise ValueError()
            if (not isinstance(fact["category"], str) or fact["category"] not in CATEGORIES
                or fact["action"] not in ("quarantined", "erased")
                or not isinstance(fact["maintenance_identity"], str)
                or re.fullmatch(r"[A-Za-z][A-Za-z0-9_.:-]{0,99}", fact["maintenance_identity"]) is None
                or _time(fact["recorded_at"]) > captured_at):
                raise ValueError()
            body = {key: value for key, value in fact.items() if key != "record_digest"}
            if hashlib.sha256(_canonical(body)).hexdigest() != fact["record_digest"]:
                raise GateError("RESTORE_GATE_FACT_INTEGRITY_FAILED")
            key = tuple(fact[name] for name in ("tenant_id", "erasure_request_id", "category", "resource_key_digest", "action"))
            if fact["id"] in result or key in keys:
                raise ValueError()
            keys.add(key)
            result[fact["id"]] = fact
        return result
    except GateError:
        raise
    except Exception:
        raise GateError("RESTORE_GATE_CONTRACT_INVALID") from None


def _report(status: str, code: str | None = None) -> dict[str, object]:
    return {"schema_version": "supportops_restore_tombstone_gate_v1", "status": status, "code": code,
        "access_allowed": False, "restore_verified": False, "reapplication_verified": False,
        "ledger_authenticity_verified": False, "ledger_currentness_verified": False,
        "database_connected": False, "deletion_performed": False}


def inspect_snapshots(args: argparse.Namespace) -> tuple[dict[str, object], int]:
    """只比较两份显式预期快照，不能发现没有被导出到清单的历史或资源。"""
    expires_at = time.monotonic() + args.timeout_seconds
    ledger_raw, ledger_stat = _read(args.directory, "deletion-ledger.json", expires_at)
    if hashlib.sha256(ledger_raw).hexdigest() != args.expected_ledger_sha256:
        raise GateError("RESTORE_GATE_LEDGER_DIGEST_MISMATCH")
    restored_raw, restored_stat = _read(args.directory, "restored-deletions.json", expires_at)
    if hashlib.sha256(restored_raw).hexdigest() != args.expected_restored_sha256:
        raise GateError("RESTORE_GATE_RESTORED_DIGEST_MISMATCH")
    now = datetime.now(UTC)
    ledger_payload = _object(ledger_raw, expires_at)
    restored_payload = _object(restored_raw, expires_at)
    ledger = _facts(ledger_payload, now, args, expires_at, restored=False)
    restored = _facts(restored_payload, now, args, expires_at, restored=True)
    # 第二次读取仍是顺序观察，不声称跨文件原子快照。
    for name, original, original_stat in (("deletion-ledger.json", ledger_raw, ledger_stat),
        ("restored-deletions.json", restored_raw, restored_stat)):
        repeated, current_stat = _read(args.directory, name, expires_at)
        if repeated != original or current_stat != original_stat:
            raise GateError("RESTORE_GATE_SNAPSHOTS_CHANGED")
    now = datetime.now(UTC)
    for payload in (ledger_payload, restored_payload):
        age = (now - _time(payload["captured_at"])).total_seconds()
        if age < 0 or age > args.max_age_seconds:
            raise GateError("RESTORE_GATE_SNAPSHOT_STALE")
    missing = ledger.keys() - restored.keys()
    unexpected = restored.keys() - ledger.keys()
    conflicting = {key for key in ledger.keys() & restored.keys() if ledger[key] != restored[key]}
    blocked = bool(missing or unexpected or conflicting)
    report = _report("quarantine_required" if blocked else "facts_matched",
        "RESTORE_GATE_REAPPLICATION_REQUIRED" if blocked else None)
    report.update({"observed_at": now.isoformat(), "ledger_sha256": args.expected_ledger_sha256,
        "restored_sha256": args.expected_restored_sha256, "ledger_fact_count": len(ledger),
        "restored_fact_count": len(restored), "missing_fact_count": len(missing),
        "unexpected_fact_count": len(unexpected), "conflicting_fact_count": len(conflicting),
        "source_resources_checked": False, "external_copies_checked": False})
    _deadline(expires_at)
    return report, 3 if blocked else 0


def main(argv: list[str] | None = None) -> int:
    try:
        args = _arguments(argv)
        report, status = inspect_snapshots(args)
    except GateError as exc:
        code = str(exc)
        report, status = _report("unconfirmed", code), 2 if code == "RESTORE_GATE_ARGUMENTS_INVALID" else 1
    except KeyboardInterrupt:
        report, status = _report("unconfirmed", "RESTORE_GATE_INTERRUPTED"), 130
    except Exception:
        report, status = _report("unconfirmed", "RESTORE_GATE_UNCONFIRMED"), 1
    try:
        sys.stdout.write(json.dumps(report, ensure_ascii=False, sort_keys=True) + "\n")
        sys.stdout.flush()
    except (OSError, ValueError):
        return 1
    return status


if __name__ == "__main__":
    raise SystemExit(main())
