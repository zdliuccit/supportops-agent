"""独立身份的一次来源读取；固定只读挂载和独立批准清单，不接受请求指定路径。"""

import asyncio
import base64
import hashlib
import json
import math
import os
import re
import signal
import stat
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

from supportops_core.knowledge_parser_isolated import (
    APPLICATION_UID, KnowledgeParserError, _close, _encode, _endpoint,
    _object, _peer, _read_frame, _require_isolated_runtime, _serve_once, _write_frame,
    isolated_parser_manifest_digest,
)

READER_UID = 10006
SOURCE_ROOT = "/source"
SOCKET_DIR = Path("/run/supportops-knowledge-reader")
SOCKET_PATH = SOCKET_DIR / "read.sock"
POLICY_DIR = Path("/etc/supportops-knowledge")
POLICY_PATH = POLICY_DIR / "reader-policy.json"
MAX_RESPONSE_BYTES = 6_001_024
READER_ENV = {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": "/nonexistent",
    "LANG": "C.UTF-8", "SUPPORTOPS_KNOWLEDGE_READER_MODE": "markdown-once-v1"}
_ERRORS = frozenset({"MARKDOWN_FILE_TYPE_DENIED", "MARKDOWN_OBJECT_ALIAS_DENIED",
    "MARKDOWN_SIZE_EXCEEDED", "MARKDOWN_SOURCE_CHANGED", "MARKDOWN_READ_FAILED",
    "MARKDOWN_READ_TIMEOUT", "MARKDOWN_MANIFEST_MISMATCH"})


class KnowledgeReaderError(KnowledgeParserError):
    """只携带固定码；不输出清单、挂载、文件名、正文或操作系统异常。"""


class KnowledgeReaderPolicyError(KnowledgeReaderError):
    """配置读取/JSON/契约的独立固定错误域，不含输入或原异常。"""


def selection_digest(root_directory: str, relative_paths: tuple[str, ...], *,
    source_root_device: int | None = None, source_root_inode: int | None = None,
) -> str:
    """原v1字节兼容；新隔离选择另绑定物理根身份，防止旧Job静默改用其他来源。"""
    def parts(value: str, *, absolute: bool) -> list[str]:
        if not isinstance(value, str):
            raise KnowledgeReaderError("MARKDOWN_PATH_INVALID")
        try:
            encoded = value.encode("utf-8")
        except UnicodeError:
            raise KnowledgeReaderError("MARKDOWN_PATH_INVALID") from None
        if (not encoded or len(encoded) > 4096 or value.startswith("/") != absolute
            or any(ord(char) < 32 or ord(char) == 127 for char in value)
            or any(char in value for char in "\\:*?[]")):
            raise KnowledgeReaderError("MARKDOWN_PATH_INVALID")
        result = value.split("/")[1:] if absolute else value.split("/")
        if not result or any(not part or part in {".", ".."} for part in result):
            raise KnowledgeReaderError("MARKDOWN_PATH_INVALID")
        return result

    parts(root_directory, absolute=True)
    if type(relative_paths) is not tuple or not 0 < len(relative_paths) <= 100:
        raise KnowledgeReaderError("MARKDOWN_SELECTION_INVALID")
    for path in relative_paths:
        components = parts(path, absolute=False)
        if any(part.startswith(".") for part in components) or not components[-1].endswith(".md"):
            raise KnowledgeReaderError("MARKDOWN_FILE_TYPE_DENIED")
    if len(set(relative_paths)) != len(relative_paths):
        raise KnowledgeReaderError("MARKDOWN_SELECTION_INVALID")
    payload: list[object] = ["local_markdown_selection_v1", root_directory, sorted(relative_paths)]
    if source_root_device is not None or source_root_inode is not None:
        if (root_directory != SOURCE_ROOT or type(source_root_device) is not int
            or not 0 <= source_root_device < 2**64 or type(source_root_inode) is not int
            or not 0 < source_root_inode < 2**64):
            raise KnowledgeReaderError("MARKDOWN_SELECTION_INVALID")
        payload = ["local_markdown_isolated_selection_v1", root_directory, sorted(relative_paths),
            source_root_device, source_root_inode]
    raw = json.dumps(payload,
        ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def isolated_reader_manifest_digest() -> str:
    digests = {}
    for name in ("__init__", "knowledge_reader_isolated", "knowledge_parser_isolated", "knowledge_content"):
        with Path(__file__).with_name(name + ".py").open("rb") as handle:
            raw = handle.read(65_537)
        if not raw or len(raw) > 65_536:
            raise KnowledgeReaderError("MARKDOWN_READER_UNAVAILABLE")
        digests[name] = hashlib.sha256(raw).hexdigest()
    return hashlib.sha256(_encode({"implementation": "local_markdown_isolated_reader_v1",
        "uid": READER_UID, "application_uid": APPLICATION_UID, "source_root": SOURCE_ROOT,
        "sources": digests})).hexdigest()


def require_isolated_reader_endpoint(*, expected_manifest_digest: str) -> None:
    try:
        if sys.platform != "linux" or os.geteuid() != APPLICATION_UID:
            raise ValueError()
        if isolated_reader_manifest_digest() != expected_manifest_digest:
            raise KnowledgeReaderError("MARKDOWN_MANIFEST_MISMATCH")
        _endpoint(directory=SOCKET_DIR, path=SOCKET_PATH, owner_uid=READER_UID)
    except KnowledgeReaderError:
        raise
    except Exception:
        raise KnowledgeReaderError("MARKDOWN_READER_UNAVAILABLE") from None


def _digest(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch("[0-9a-f]{64}", value) is not None


def _time(value: object) -> datetime:
    if not isinstance(value, str) or len(value) > 64 or "T" not in value:
        raise ValueError()
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError()
    return result.astimezone(UTC)


async def read_markdown_isolated(*, scope_digest: str, expected_selection_digest: str,
    expected_manifest_digest: str, expected_parser_manifest_digest: str, deadline: float,
) -> dict[str, object]:
    """只发送固定范围和期限；路径来自读取器受保护策略，单次连接且不回退。"""
    writer = None
    try:
        require_isolated_reader_endpoint(expected_manifest_digest=expected_manifest_digest)
        if (not all(_digest(value) for value in (scope_digest, expected_selection_digest,
                expected_manifest_digest, expected_parser_manifest_digest))
            or isolated_parser_manifest_digest() != expected_parser_manifest_digest):
            raise KnowledgeReaderError("MARKDOWN_MANIFEST_MISMATCH")
        seconds = deadline - asyncio.get_running_loop().time()
        if not math.isfinite(seconds) or not 0 < seconds <= 30:
            raise KnowledgeReaderError("MARKDOWN_READ_TIMEOUT")
        request_id = str(uuid4())
        fixed = {"scope_digest": scope_digest, "selection_digest": expected_selection_digest,
            "adapter_manifest_digest": expected_manifest_digest, "parser_manifest_digest": expected_parser_manifest_digest}
        raw = _encode({"schema_version": "isolated_markdown_read_request_v1", "request_id": request_id,
            **fixed, "expires_at": (datetime.now(UTC) + timedelta(seconds=seconds)).isoformat()})
        async with asyncio.timeout_at(deadline):
            endpoint = _endpoint(directory=SOCKET_DIR, path=SOCKET_PATH, owner_uid=READER_UID)
            reader, writer = await asyncio.open_unix_connection(str(SOCKET_PATH), limit=65_536)
            _peer(writer, READER_UID)
            if endpoint != _endpoint(directory=SOCKET_DIR, path=SOCKET_PATH, owner_uid=READER_UID):
                raise ValueError()
            await _write_frame(writer, raw, 4096)
            reply = _object(await _read_frame(reader, MAX_RESPONSE_BYTES))
            if await reader.read(1):
                raise ValueError()
            _peer(writer, READER_UID)
            if (set(reply) != {"schema_version", "request_id", "request_digest", *fixed, "result"}
                or reply["schema_version"] != "isolated_markdown_read_result_v1" or reply["request_id"] != request_id
                or reply["request_digest"] != hashlib.sha256(raw).hexdigest()
                or any(reply[key] != value for key, value in fixed.items()) or not isinstance(reply["result"], dict)):
                raise ValueError()
            if (isolated_reader_manifest_digest() != expected_manifest_digest
                or isolated_parser_manifest_digest() != expected_parser_manifest_digest):
                raise KnowledgeReaderError("MARKDOWN_MANIFEST_MISMATCH")
            result = reply["result"]
            if set(result) == {"error"}:
                code = result["error"]
                raise KnowledgeReaderError(code if isinstance(code, str) and code in _ERRORS else "MARKDOWN_READ_FAILED")
            if set(result) != {"contents"}:
                raise ValueError()
            return result
    except KnowledgeReaderError:
        raise
    except TimeoutError:
        raise KnowledgeReaderError("MARKDOWN_READ_TIMEOUT") from None
    except Exception:
        raise KnowledgeReaderError("MARKDOWN_READER_UNAVAILABLE") from None
    finally:
        if writer is not None:
            await _close(writer)


@dataclass(frozen=True, slots=True)
class _ReaderPolicy:
    scope_digest: str
    selection_digest: str
    paths: tuple[str, ...] = field(repr=False)
    root_device: int
    root_inode: int
    valid_until: datetime
    policy_digest: str


def read_isolated_reader_policy_file(path: str, *, adapter_digest: str, parser_digest: str) -> _ReaderPolicy:
    """运行时/离线预检复用受保护读取，仅打开显式配置，不读取来源或应用环境。"""
    directory = None
    descriptor = None
    try:
        if (not isinstance(path, str) or not path.startswith("/") or len(path.encode("utf-8")) > 4096
            or "\\" in path or any(ord(char) < 32 or ord(char) == 127 for char in path)):
            raise ValueError()
        parts = path.split("/")[1:]
        if not parts or any(part in {"", ".", ".."} for part in parts):
            raise ValueError()
        flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK
        directory = os.open("/", flags | os.O_DIRECTORY)
        for part in parts[:-1]:
            following = os.open(part, flags | os.O_DIRECTORY, dir_fd=directory)
            os.close(directory)
            directory = following
            info = os.fstat(directory)
            if info.st_uid != 0 or info.st_mode & 0o022:
                raise ValueError()
        descriptor = os.open(parts[-1], flags, dir_fd=directory)
        before = os.fstat(descriptor)
        if (not stat.S_ISREG(before.st_mode) or before.st_uid != 0 or before.st_nlink != 1
            or stat.S_IMODE(before.st_mode) != 0o444 or not 0 < before.st_size <= 131072):
            raise ValueError()
        chunks, remaining = [], 131073
        while remaining:
            chunk = os.read(descriptor, min(remaining, 16384))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        after = os.fstat(descriptor)
        facts = lambda value: (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns,
            value.st_ctime_ns, value.st_uid, value.st_gid, value.st_mode, value.st_nlink)
        if facts(before) != facts(after) or len(raw) != before.st_size or len(raw) > 131072:
            raise ValueError()
    except Exception:
        raise KnowledgeReaderPolicyError("READER_POLICY_FILE_UNAVAILABLE") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if directory is not None:
            os.close(directory)
    return parse_isolated_reader_policy(raw, adapter_digest=adapter_digest, parser_digest=parser_digest)


def parse_isolated_reader_policy(raw: bytes, *, adapter_digest: str, parser_digest: str) -> _ReaderPolicy:
    """严格配置结构与摘要校验；到期由调用方以同一次观察时间单独判定。"""
    try:
        value = _object(raw)
    except Exception:
        raise KnowledgeReaderPolicyError("READER_POLICY_JSON_INVALID") from None
    try:
        if (set(value) != {"schema_version", "scope_digest", "selection_digest", "relative_paths",
                "adapter_manifest_digest", "parser_manifest_digest", "source_root_device", "source_root_inode", "valid_until"}
            or value["schema_version"] != "isolated_markdown_reader_policy_v1"
            or not _digest(value["scope_digest"]) or not _digest(value["selection_digest"])
            or value["adapter_manifest_digest"] != adapter_digest or value["parser_manifest_digest"] != parser_digest
            or type(value["source_root_device"]) is not int or not 0 <= value["source_root_device"] < 2**64
            or type(value["source_root_inode"]) is not int or not 0 < value["source_root_inode"] < 2**64
            or not isinstance(value["relative_paths"], list)):
            raise ValueError()
        paths = tuple(value["relative_paths"])
        digest = selection_digest(SOURCE_ROOT, paths, source_root_device=value["source_root_device"],
            source_root_inode=value["source_root_inode"])
        if value["relative_paths"] != sorted(paths) or value["selection_digest"] != digest:
            raise ValueError()
        expires = _time(value["valid_until"])
        return _ReaderPolicy(scope_digest=value["scope_digest"], selection_digest=digest,
            paths=paths, root_device=value["source_root_device"], root_inode=value["source_root_inode"],
            valid_until=expires, policy_digest=hashlib.sha256(_encode(value)).hexdigest())
    except Exception:
        raise KnowledgeReaderPolicyError("READER_POLICY_CONTRACT_INVALID") from None


def _policy(*, adapter_digest: str, parser_digest: str) -> _ReaderPolicy:
    try:
        directory = POLICY_DIR.lstat()
        if (not stat.S_ISDIR(directory.st_mode) or directory.st_uid != 0
            or stat.S_IMODE(directory.st_mode) != 0o755):
            raise ValueError()
        policy = read_isolated_reader_policy_file(str(POLICY_PATH), adapter_digest=adapter_digest, parser_digest=parser_digest)
        if policy.valid_until <= datetime.now(UTC):
            raise ValueError()
        return policy
    except Exception:
        raise KnowledgeReaderError("MARKDOWN_READER_UNAVAILABLE") from None


def _source_mount() -> None:
    """固定来源必须是单独只读挂载，不接纳其下额外的秘密/文件挂载。"""
    with Path("/proc/self/mountinfo").open("rb") as handle:
        raw = handle.read(1_048_577)
    if len(raw) > 1_048_576:
        raise ValueError()
    matches = 0
    for line in raw.decode("utf-8").splitlines():
        fields = line.split()
        if len(fields) < 7:
            raise ValueError()
        if fields[4] == SOURCE_ROOT:
            matches += 1
            if "ro" not in fields[5].split(","):
                raise ValueError()
        elif fields[4].startswith(SOURCE_ROOT + "/"):
            raise ValueError()
    if matches != 1:
        raise ValueError()


def _read_files(policy: _ReaderPolicy, *, expires_at: datetime) -> dict[str, object]:
    _source_mount()
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
    root = os.open(SOURCE_ROOT, flags)
    try:
        root_facts = os.fstat(root)
        if ((root_facts.st_dev, root_facts.st_ino) != (policy.root_device, policy.root_inode)
            or not os.fstatvfs(root).f_flag & os.ST_RDONLY):
            raise ValueError()
        records, total, identities = [], 0, set()
        for path in policy.paths:
            if datetime.now(UTC) >= min(expires_at, policy.valid_until):
                raise KnowledgeReaderError("MARKDOWN_READ_TIMEOUT")
            parent = os.dup(root)
            try:
                for part in path.split("/")[:-1]:
                    following = os.open(part, flags, dir_fd=parent)
                    os.close(parent)
                    parent = following
                descriptor = os.open(path.split("/")[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                    dir_fd=parent)
                try:
                    before = os.fstat(descriptor)
                    identity = (before.st_dev, before.st_ino)
                    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
                        raise KnowledgeReaderError("MARKDOWN_FILE_TYPE_DENIED")
                    if identity in identities:
                        raise KnowledgeReaderError("MARKDOWN_OBJECT_ALIAS_DENIED")
                    identities.add(identity)
                    if before.st_size > 1_048_576 or total + before.st_size > 4_194_304:
                        raise KnowledgeReaderError("MARKDOWN_SIZE_EXCEEDED")
                    chunks, size = [], 0
                    while True:
                        if datetime.now(UTC) >= min(expires_at, policy.valid_until):
                            raise KnowledgeReaderError("MARKDOWN_READ_TIMEOUT")
                        chunk = os.read(descriptor, min(65_536, 1_048_577 - size))
                        if not chunk:
                            break
                        size += len(chunk)
                        if size > 1_048_576 or total + size > 4_194_304:
                            raise KnowledgeReaderError("MARKDOWN_SIZE_EXCEEDED")
                        chunks.append(chunk)
                    after = os.fstat(descriptor)
                    facts = lambda value: (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns,
                        value.st_ctime_ns, value.st_nlink)
                    if facts(before) != facts(after) or size != before.st_size:
                        raise KnowledgeReaderError("MARKDOWN_SOURCE_CHANGED")
                    records.append(base64.b64encode(b"".join(chunks)).decode("ascii"))
                    total += size
                finally:
                    os.close(descriptor)
            finally:
                os.close(parent)
        return {"contents": records}
    finally:
        os.close(root)


async def serve_reader_once() -> None:
    adapter, parser = isolated_reader_manifest_digest(), isolated_parser_manifest_digest()
    original = _policy(adapter_digest=adapter, parser_digest=parser)
    _source_mount()

    def respond(raw: bytes) -> bytes:
        request = _object(raw)
        if (set(request) != {"schema_version", "request_id", "scope_digest", "selection_digest",
                "adapter_manifest_digest", "parser_manifest_digest", "expires_at"}
            or request["schema_version"] != "isolated_markdown_read_request_v1"
            or not isinstance(request["request_id"], str) or str(UUID(request["request_id"])) != request["request_id"]):
            raise ValueError()
        try:
            if (request["scope_digest"] != original.scope_digest or request["selection_digest"] != original.selection_digest
                or request["adapter_manifest_digest"] != adapter or request["parser_manifest_digest"] != parser
                or _policy(adapter_digest=adapter, parser_digest=parser) != original):
                raise KnowledgeReaderError("MARKDOWN_MANIFEST_MISMATCH")
            expires = _time(request["expires_at"])
            now = datetime.now(UTC)
            if not now < expires <= now + timedelta(seconds=30):
                raise KnowledgeReaderError("MARKDOWN_READ_TIMEOUT")
            result = _read_files(original, expires_at=expires)
            if (_policy(adapter_digest=adapter, parser_digest=parser) != original
                or datetime.now(UTC) >= min(expires, original.valid_until)):
                raise KnowledgeReaderError("MARKDOWN_MANIFEST_MISMATCH")
        except KnowledgeReaderError as error:
            result = {"error": error.code if error.code in _ERRORS else "MARKDOWN_READ_FAILED"}
        except Exception:
            result = {"error": "MARKDOWN_READ_FAILED"}
        return _encode({"schema_version": "isolated_markdown_read_result_v1", "request_id": request["request_id"],
            "request_digest": hashlib.sha256(raw).hexdigest(), "scope_digest": original.scope_digest,
            "selection_digest": original.selection_digest, "adapter_manifest_digest": adapter,
            "parser_manifest_digest": parser, "result": result})

    await _serve_once(directory=SOCKET_DIR, path=SOCKET_PATH, owner_uid=READER_UID,
        maximum_request=4096, maximum_response=MAX_RESPONSE_BYTES, respond=respond)


def main() -> None:
    alarm_enabled = False
    try:
        if len(sys.argv) != 1:
            raise ValueError()
        _require_isolated_runtime(owner_uid=READER_UID, environment=READER_ENV)
        signal.signal(signal.SIGALRM, lambda *_: os._exit(2))
        signal.alarm(75)
        alarm_enabled = True
        asyncio.run(serve_reader_once())
    except BaseException:
        raise SystemExit(2) from None
    finally:
        if alarm_enabled:
            signal.alarm(0)


if __name__ == "__main__":
    main()
