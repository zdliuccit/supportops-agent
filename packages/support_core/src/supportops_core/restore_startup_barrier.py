"""恢复环境的拒绝启动屏障；没有释放、批准或删除隔离标记的入口。"""

import hashlib
import json
import os
import re
import stat
from contextlib import ExitStack
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import UUID

CONTEXT_DIRECTORY = Path("/etc/supportops-restore")
MAX_CONTEXT_BYTES = 8192
RestoreBarrierCode = Literal["RESTORE_QUARANTINE_ACTIVE", "RESTORE_CONTEXT_REQUIRED",
    "RESTORE_CONTEXT_INVALID", "RESTORE_CONTEXT_UNAVAILABLE", "RESTORE_CONTEXT_MISMATCH"]


class RestoreStartupBlocked(RuntimeError):
    def __init__(self, code: RestoreBarrierCode):
        self.code = code
        super().__init__(code)


def _fingerprint(info: os.stat_result) -> tuple[int, ...]:
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns,
        info.st_uid, info.st_gid, info.st_mode, info.st_nlink)


def _pairs(items: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in items:
        if key in result:
            raise ValueError()
        result[key] = value
    return result


def _constant(value: str) -> object:
    raise ValueError()


def _uuid(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return str(UUID(value)) == value
    except ValueError:
        return False


def _read_context() -> bytes:
    try:
        with ExitStack() as stack:
            flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC
            directory = os.open("/", flags | os.O_DIRECTORY)
            stack.callback(os.close, directory)
            for part in (None, "etc", "supportops-restore"):
                if part is not None:
                    directory = os.open(part, flags | os.O_DIRECTORY, dir_fd=directory)
                    stack.callback(os.close, directory)
                info = os.fstat(directory)
                if (info.st_uid != 0 or stat.S_IMODE(info.st_mode) & 0o022
                    or part == "supportops-restore" and stat.S_IMODE(info.st_mode) != 0o755):
                    raise ValueError()
            descriptor = os.open("operation.json", flags | os.O_NONBLOCK, dir_fd=directory)
            stack.callback(os.close, descriptor)
            before = os.fstat(descriptor)
            if (not stat.S_ISREG(before.st_mode) or before.st_uid != 0 or before.st_gid != 0
                or before.st_nlink != 1 or stat.S_IMODE(before.st_mode) != 0o444
                or not 0 < before.st_size <= MAX_CONTEXT_BYTES):
                raise ValueError()
            parts: list[bytes] = []
            size = 0
            while True:
                chunk = os.read(descriptor, min(1024, MAX_CONTEXT_BYTES + 1 - size))
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_CONTEXT_BYTES:
                    raise ValueError()
                parts.append(chunk)
            after = os.fstat(descriptor)
            current = os.stat("operation.json", dir_fd=directory, follow_symlinks=False)
            if (_fingerprint(before) != _fingerprint(after) or _fingerprint(after) != _fingerprint(current)
                or size != after.st_size):
                raise ValueError()
            return b"".join(parts)
    except Exception:
        raise RestoreStartupBlocked("RESTORE_CONTEXT_UNAVAILABLE") from None


def assert_service_start_allowed(*, mode: str, expected_restore_id: UUID | None,
    expected_context_digest: str | None,
) -> None:
    """仅正常模式且固定标记目录不存在时放行；隔离永不因缺项或时间到期解除。"""
    if mode not in ("normal", "quarantined"):
        raise RestoreStartupBlocked("RESTORE_CONTEXT_INVALID")
    if mode == "normal" and (expected_restore_id is not None or expected_context_digest is not None):
        raise RestoreStartupBlocked("RESTORE_CONTEXT_INVALID")
    try:
        CONTEXT_DIRECTORY.lstat()
    except FileNotFoundError:
        if mode == "normal":
            return
        raise RestoreStartupBlocked("RESTORE_CONTEXT_REQUIRED") from None
    except OSError:
        raise RestoreStartupBlocked("RESTORE_CONTEXT_UNAVAILABLE") from None
    # 正常模式看到目录即拒绝，包括空目录/坏标记，不能仅去掉环境变量绕过。
    if mode == "normal":
        raise RestoreStartupBlocked("RESTORE_QUARANTINE_ACTIVE")
    if (not isinstance(expected_restore_id, UUID) or not isinstance(expected_context_digest, str)
        or re.fullmatch(r"[a-f0-9]{64}", expected_context_digest) is None):
        raise RestoreStartupBlocked("RESTORE_CONTEXT_REQUIRED")
    raw = _read_context()
    if hashlib.sha256(raw).hexdigest() != expected_context_digest:
        raise RestoreStartupBlocked("RESTORE_CONTEXT_MISMATCH")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_constant)
        if (not isinstance(value, dict) or set(value) != {"schema_version", "restore_id", "backup_id",
            "target_alias", "ledger_id", "ledger_generation", "ledger_snapshot_sha256", "quarantined_at", "state"}
            or value["schema_version"] != "supportops_restore_operation_v1" or value["state"] != "quarantined"
            or not _uuid(value["restore_id"]) or not _uuid(value["ledger_id"])
            or not isinstance(value["backup_id"], str)
            or re.fullmatch(r"supportops-backup\.[A-Za-z0-9]{12}", value["backup_id"]) is None
            or not isinstance(value["target_alias"], str)
            or re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value["target_alias"]) is None
            or type(value["ledger_generation"]) is not int or not 1 <= value["ledger_generation"] <= 2**63 - 1
            or not isinstance(value["ledger_snapshot_sha256"], str)
            or re.fullmatch(r"[a-f0-9]{64}", value["ledger_snapshot_sha256"]) is None
            or not isinstance(value["quarantined_at"], str) or len(value["quarantined_at"]) > 32):
            raise ValueError()
        quarantined_at = datetime.fromisoformat(value["quarantined_at"])
        if (quarantined_at.tzinfo is None or quarantined_at.utcoffset() != UTC.utcoffset(quarantined_at)
            or quarantined_at.isoformat() != value["quarantined_at"] or quarantined_at > datetime.now(UTC)):
            raise ValueError()
    except Exception:
        raise RestoreStartupBlocked("RESTORE_CONTEXT_INVALID") from None
    if value["restore_id"] != str(expected_restore_id):
        raise RestoreStartupBlocked("RESTORE_CONTEXT_MISMATCH")
    # 标记有效只证明当前应隔离；不存在released状态、TTL、通过报告或开发豁免。
    raise RestoreStartupBlocked("RESTORE_QUARANTINE_ACTIVE")
