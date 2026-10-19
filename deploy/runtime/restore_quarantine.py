"""显式创建一次恢复隔离标记；不连接数据库或执行恢复，没有解除/覆盖/删除命令。"""

import argparse
import hashlib
import json
import os
import re
import stat
import sys
from contextlib import ExitStack
from datetime import UTC, datetime
from typing import NoReturn
from uuid import UUID


class InitializationError(Exception):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise InitializationError("RESTORE_INITIALIZATION_ARGUMENTS_INVALID") from None


class _Once(argparse.Action):
    def __call__(self, parser: argparse.ArgumentParser, namespace: argparse.Namespace,
        values: object, option_string: str | None = None,
    ) -> None:
        if getattr(namespace, self.dest, None) is not None:
            raise InitializationError("RESTORE_INITIALIZATION_ARGUMENTS_INVALID")
        setattr(namespace, self.dest, values)


def _uuid(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return str(UUID(value)) == value
    except ValueError:
        return False


def _arguments(argv: list[str]) -> argparse.Namespace:
    parser = _Parser(description="仅创建阻止服务启动的恢复隔离标记，不执行恢复或提供解除入口。", allow_abbrev=False)
    parser.add_argument("--initialize", action="store_true")
    for name in ("restore-id", "backup-id", "target-alias", "ledger-id", "ledger-generation", "ledger-snapshot-sha256"):
        parser.add_argument("--" + name, action=_Once)
    args = parser.parse_args(argv)
    if (not args.initialize or not _uuid(args.restore_id) or not _uuid(args.ledger_id)
        or args.backup_id is None or re.fullmatch(r"supportops-backup\.[A-Za-z0-9]{12}", args.backup_id) is None
        or args.target_alias is None or re.fullmatch(r"[A-Za-z0-9_-]{1,64}", args.target_alias) is None
        or args.ledger_snapshot_sha256 is None or re.fullmatch(r"[a-f0-9]{64}", args.ledger_snapshot_sha256) is None
        or args.ledger_generation is None or re.fullmatch(r"[1-9][0-9]{0,18}", args.ledger_generation) is None
        or int(args.ledger_generation) > 2**63 - 1):
        raise InitializationError("RESTORE_INITIALIZATION_ARGUMENTS_INVALID")
    args.ledger_generation = int(args.ledger_generation)
    return args


def initialize(args: argparse.Namespace) -> str:
    """只写固定/state/operation.json；O_EXCL保留旧/部分标记，不对未知写入自动重试。"""
    if os.geteuid() != 0 or os.getegid() != 0:
        raise InitializationError("RESTORE_INITIALIZATION_IDENTITY_REJECTED")
    payload = {"schema_version": "supportops_restore_operation_v1", "state": "quarantined",
        "restore_id": args.restore_id, "backup_id": args.backup_id, "target_alias": args.target_alias,
        "ledger_id": args.ledger_id, "ledger_generation": args.ledger_generation,
        "ledger_snapshot_sha256": args.ledger_snapshot_sha256, "quarantined_at": datetime.now(UTC).isoformat()}
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
    try:
        with ExitStack() as stack:
            flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_DIRECTORY
            root = os.open("/", flags)
            stack.callback(os.close, root)
            directory = os.open("state", flags, dir_fd=root)
            stack.callback(os.close, directory)
            for descriptor in (root, directory):
                info = os.fstat(descriptor)
                if info.st_uid != 0 or stat.S_IMODE(info.st_mode) & 0o022:
                    raise InitializationError("RESTORE_INITIALIZATION_DIRECTORY_REJECTED")
            if stat.S_IMODE(os.fstat(directory).st_mode) != 0o755:
                raise InitializationError("RESTORE_INITIALIZATION_DIRECTORY_REJECTED")
            descriptor = os.open("operation.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                0o400, dir_fd=directory)
            stack.callback(os.close, descriptor)
            view = memoryview(raw)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:
                    raise OSError()
                view = view[written:]
            os.fsync(descriptor)
            os.fchmod(descriptor, 0o444)
            os.fsync(descriptor)
            os.fsync(directory)
            info = os.fstat(descriptor)
            current = os.stat("operation.json", dir_fd=directory, follow_symlinks=False)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_gid != 0
                or stat.S_IMODE(info.st_mode) != 0o444 or info.st_nlink != 1 or info.st_size != len(raw)
                or (info.st_dev, info.st_ino) != (current.st_dev, current.st_ino)):
                raise OSError()
    except InitializationError:
        raise
    except FileExistsError:
        raise InitializationError("RESTORE_INITIALIZATION_MARKER_EXISTS") from None
    except Exception:
        # 创建后失败保留标记，包括部分标记；它仍须阻止服务启动。
        raise InitializationError("RESTORE_INITIALIZATION_UNCONFIRMED") from None
    return hashlib.sha256(raw).hexdigest()


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments:
        arguments = ["--help"]
    try:
        args = _arguments(arguments)
        digest = initialize(args)
        report = {"schema_version": "supportops_restore_quarantine_initialization_v1", "status": "marker_created",
            "context_sha256": digest, "access_allowed": False, "restore_performed": False, "release_supported": False}
        status = 0
    except InitializationError as exc:
        code = str(exc)
        report = {"schema_version": "supportops_restore_quarantine_initialization_v1", "status": "unconfirmed",
            "code": code, "access_allowed": False, "restore_performed": False, "release_supported": False}
        status = 2 if code == "RESTORE_INITIALIZATION_ARGUMENTS_INVALID" else 3 if code == "RESTORE_INITIALIZATION_MARKER_EXISTS" else 1
    except KeyboardInterrupt:
        report = {"schema_version": "supportops_restore_quarantine_initialization_v1", "status": "unconfirmed",
            "code": "RESTORE_INITIALIZATION_INTERRUPTED", "access_allowed": False,
            "restore_performed": False, "release_supported": False}
        status = 130
    except Exception:
        report = {"schema_version": "supportops_restore_quarantine_initialization_v1", "status": "unconfirmed",
            "code": "RESTORE_INITIALIZATION_UNCONFIRMED", "access_allowed": False,
            "restore_performed": False, "release_supported": False}
        status = 1
    try:
        sys.stdout.write(json.dumps(report, ensure_ascii=False, sort_keys=True) + "\n")
        sys.stdout.flush()
    except (OSError, ValueError):
        return 1
    return status


if __name__ == "__main__":
    raise SystemExit(main())
