"""独立UID的单次Markdown解析通道；仅依赖标准库和固定正文规范化源码。"""

import asyncio
import base64
import hashlib
import json
import os
import signal
import socket
import stat
import struct
import sys
from collections.abc import Callable
from pathlib import Path
from uuid import UUID, uuid4

from supportops_core.knowledge_content import (
    knowledge_content_digest, normalize_knowledge_markdown, parse_section_anchors,
)

PARSER_UID = 10005
APPLICATION_UID = 10001
SOCKET_DIR = Path("/run/supportops-knowledge-parser")
SOCKET_PATH = SOCKET_DIR / "parse.sock"
MAX_REQUEST_BYTES = 6_001_024
MAX_RESULT_BYTES = 8_388_608
MAX_RESPONSE_BYTES = MAX_RESULT_BYTES + 1024
PARSER_ENV = {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": "/nonexistent",
    "LANG": "C.UTF-8", "SUPPORTOPS_KNOWLEDGE_PARSER_MODE": "markdown-once-v1"}
_PARSE_ERRORS = frozenset({"MARKDOWN_SIZE_EXCEEDED", "MARKDOWN_CONTENT_INVALID",
    "MARKDOWN_ANCHOR_LIMIT_EXCEEDED", "MARKDOWN_PARSE_OUTPUT_TOO_LARGE"})


class KnowledgeParserError(Exception):
    """只保留固定失败码，不传播正文、路径、协议帧或解释器异常。"""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def isolated_parser_manifest_digest() -> str:
    """摘要绑定两端协议/隔离前置检查和实际解析源码，不授予来源批准。"""
    digests = {}
    for name in ("__init__", "knowledge_parser_isolated", "knowledge_content"):
        with Path(__file__).with_name(name + ".py").open("rb") as handle:
            raw = handle.read(65_537)
        if not raw or len(raw) > 65_536:
            raise KnowledgeParserError("MARKDOWN_PARSER_UNAVAILABLE")
        digests[name] = hashlib.sha256(raw).hexdigest()
    return hashlib.sha256(_encode({"implementation": "local_markdown_isolated_v1",
        "uid": PARSER_UID, "application_uid": APPLICATION_UID, "sources": digests})).hexdigest()


def _encode(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True,
        separators=(",", ":")).encode("utf-8")


def _object(raw: bytes) -> dict[str, object]:
    def pairs(items: list[tuple[str, object]]) -> dict[str, object]:
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError()
            result[key] = value
        return result

    def invalid(_value: str) -> object:
        raise ValueError()

    result = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs, parse_constant=invalid)
    if not isinstance(result, dict):
        raise ValueError()
    return result


def _directory(*, directory: Path = SOCKET_DIR, owner_uid: int = PARSER_UID) -> None:
    value = directory.lstat()
    if (not stat.S_ISDIR(value.st_mode) or value.st_uid != owner_uid
        or value.st_gid != APPLICATION_UID or stat.S_IMODE(value.st_mode) != 0o770):
        raise ValueError()


def _endpoint(*, directory: Path = SOCKET_DIR, path: Path = SOCKET_PATH,
    owner_uid: int = PARSER_UID,
) -> tuple[int, int]:
    _directory(directory=directory, owner_uid=owner_uid)
    value = path.lstat()
    if (not stat.S_ISSOCK(value.st_mode) or value.st_uid != owner_uid
        or value.st_gid != APPLICATION_UID or stat.S_IMODE(value.st_mode) != 0o660):
        raise ValueError()
    return value.st_dev, value.st_ino


def require_isolated_parser_endpoint(*, expected_manifest_digest: str) -> None:
    """来源读取前仅检查固定端点，不创建连接或消费单次解析器。"""
    try:
        if sys.platform != "linux" or os.geteuid() != APPLICATION_UID:
            raise ValueError()
        if isolated_parser_manifest_digest() != expected_manifest_digest:
            raise KnowledgeParserError("MARKDOWN_MANIFEST_MISMATCH")
        _endpoint()
    except KnowledgeParserError:
        raise
    except Exception:
        raise KnowledgeParserError("MARKDOWN_PARSER_UNAVAILABLE") from None


def _peer(writer: asyncio.StreamWriter, expected_uid: int) -> None:
    channel = writer.get_extra_info("socket")
    if channel is None or not hasattr(socket, "SO_PEERCRED"):
        raise ValueError()
    _pid, actual_uid, _gid = struct.unpack("3i", channel.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
    if actual_uid != expected_uid:
        raise ValueError()


async def _read_frame(reader: asyncio.StreamReader, maximum: int) -> bytes:
    size = struct.unpack("!I", await reader.readexactly(4))[0]
    if not 0 < size <= maximum:
        raise ValueError()
    return await reader.readexactly(size)


async def _write_frame(writer: asyncio.StreamWriter, raw: bytes, maximum: int) -> None:
    if not 0 < len(raw) <= maximum:
        raise ValueError()
    writer.write(struct.pack("!I", len(raw)))
    for offset in range(0, len(raw), 65_536):
        writer.write(raw[offset:offset + 65_536])
        await writer.drain()


async def _close(writer: asyncio.StreamWriter) -> None:
    try:
        writer.close()
        async with asyncio.timeout(0.25):
            await writer.wait_closed()
    except asyncio.CancelledError:
        writer.transport.abort()
        raise
    except Exception:
        writer.transport.abort()


async def parse_markdown_isolated(contents: list[str], *, deadline: float,
    expected_manifest_digest: str,
) -> dict[str, object]:
    """当前批准已选择隔离摘要后才调用；固定socket/一次连接/原共享deadline，无本地回退。"""
    writer = None
    try:
        if (sys.platform != "linux" or os.geteuid() != APPLICATION_UID
            or not isinstance(contents, list) or not 0 < len(contents) <= 100
            or any(not isinstance(value, str) or not value.isascii() or len(value) > 1_398_104 for value in contents)
            or sum(len(value) for value in contents) > 5_592_672):
            raise ValueError()
        if asyncio.get_running_loop().time() >= deadline:
            raise KnowledgeParserError("MARKDOWN_PARSE_TIMEOUT")
        manifest = isolated_parser_manifest_digest()
        if manifest != expected_manifest_digest:
            raise KnowledgeParserError("MARKDOWN_MANIFEST_MISMATCH")
        request_id = str(uuid4())
        payload = _encode({"schema_version": "isolated_markdown_request_v1", "request_id": request_id,
            "parser_manifest_digest": manifest, "contents": contents})
        if len(payload) > MAX_REQUEST_BYTES:
            raise KnowledgeParserError("MARKDOWN_SIZE_EXCEEDED")
        async with asyncio.timeout_at(deadline):
            endpoint = _endpoint()
            reader, writer = await asyncio.open_unix_connection(str(SOCKET_PATH), limit=65_536)
            _peer(writer, PARSER_UID)
            if isolated_parser_manifest_digest() != manifest:
                raise KnowledgeParserError("MARKDOWN_MANIFEST_MISMATCH")
            if endpoint != _endpoint():
                raise ValueError()
            await _write_frame(writer, payload, MAX_REQUEST_BYTES)
            result = _object(await _read_frame(reader, MAX_RESPONSE_BYTES))
            if await reader.read(1):
                raise ValueError()
            _peer(writer, PARSER_UID)
            if (set(result) != {"schema_version", "request_id", "request_digest", "parser_manifest_digest", "result"}
                or result["schema_version"] != "isolated_markdown_result_v1" or result["request_id"] != request_id
                or result["request_digest"] != hashlib.sha256(payload).hexdigest()
                or result["parser_manifest_digest"] != manifest or not isinstance(result["result"], dict)):
                raise ValueError()
            parsed = result["result"]
            if set(parsed) == {"error"}:
                code = parsed["error"]
                raise KnowledgeParserError(code if isinstance(code, str) and code in _PARSE_ERRORS else "MARKDOWN_PARSE_FAILED")
            if set(parsed) != {"objects"} or len(_encode(parsed)) > MAX_RESULT_BYTES:
                raise ValueError()
            if isolated_parser_manifest_digest() != manifest:
                raise KnowledgeParserError("MARKDOWN_MANIFEST_MISMATCH")
            return parsed
    except KnowledgeParserError:
        raise
    except TimeoutError:
        raise KnowledgeParserError("MARKDOWN_PARSE_TIMEOUT") from None
    except Exception:
        raise KnowledgeParserError("MARKDOWN_PARSER_UNAVAILABLE") from None
    finally:
        if writer is not None:
            await _close(writer)


def require_parser_runtime() -> None:
    """创建事件循环前核对；挂载内容/镜像批准与隔离效果仍需部署验收。"""
    _require_isolated_runtime(owner_uid=PARSER_UID, environment=PARSER_ENV)


def _require_isolated_runtime(*, owner_uid: int, environment: dict[str, str]) -> None:
    """两个固定知识IO进程共用身份、环境、资源与继承描述符检查。"""
    try:
        if (sys.platform != "linux" or os.geteuid() != owner_uid or os.getegid() != owner_uid
            or set(os.getgroups()) - {owner_uid, APPLICATION_UID} or APPLICATION_UID not in os.getgroups()
            or dict(os.environ) != environment or not sys.flags.isolated or not sys.flags.no_site
            or not sys.dont_write_bytecode or {name for _, name in socket.if_nameindex()} != {"lo"}):
            raise ValueError()
        import resource
        for kind, maximum in ((resource.RLIMIT_CORE, 0), (resource.RLIMIT_NOFILE, 32),
            (resource.RLIMIT_NPROC, 16), (resource.RLIMIT_AS, 268_435_456), (resource.RLIMIT_CPU, 3)):
            soft, hard = resource.getrlimit(kind)
            if soft < 0 or hard < 0 or soft > hard or hard > maximum:
                raise ValueError()
            if kind == resource.RLIMIT_CPU and soft > 2:
                raise ValueError()
        with Path("/proc/self/status").open("rb") as handle:
            raw = handle.read(65_537)
        if len(raw) > 65_536:
            raise ValueError()
        status = dict(line.split(":", 1) for line in raw.decode("ascii").splitlines() if ":" in line)
        if (status.get("NoNewPrivs", "").strip() != "1" or status.get("Seccomp", "").strip() != "2"
            or any(int(status.get(key, "1").strip(), 16) != 0 for key in ("CapEff", "CapPrm", "CapAmb"))):
            raise ValueError()
        for name in os.listdir("/proc/self/fd"):
            descriptor = int(name)
            if descriptor > 2:
                try:
                    os.fstat(descriptor)
                except OSError:
                    continue
                raise ValueError()
    except Exception:
        raise KnowledgeParserError("MARKDOWN_PARSER_UNAVAILABLE") from None


def _parse(contents: object) -> dict[str, object]:
    if not isinstance(contents, list) or not 0 < len(contents) <= 100:
        raise ValueError()
    records, total = [], 0
    for encoded in contents:
        if not isinstance(encoded, str):
            raise ValueError()
        raw = base64.b64decode(encoded, validate=True)
        total += len(raw)
        if len(raw) > 1_048_576 or total > 4_194_304:
            raise KnowledgeParserError("MARKDOWN_SIZE_EXCEEDED")
        content = raw.decode("utf-8")
        if any(ord(char) < 32 and char not in "\t\r\n" for char in content):
            raise KnowledgeParserError("MARKDOWN_CONTENT_INVALID")
        normalized = normalize_knowledge_markdown(content)
        if sum(line.startswith("#") for line in normalized.splitlines()) > 1000:
            raise KnowledgeParserError("MARKDOWN_ANCHOR_LIMIT_EXCEEDED")
        records.append({"raw_content_digest": hashlib.sha256(raw).hexdigest(),
            "parsed_content_digest": knowledge_content_digest(normalized), "raw_bytes": len(raw),
            "content_markdown": normalized, "section_anchors": parse_section_anchors(normalized)})
    result = {"objects": records}
    if len(_encode(result)) > MAX_RESULT_BYTES:
        raise KnowledgeParserError("MARKDOWN_PARSE_OUTPUT_TOO_LARGE")
    return result


async def serve_parser_once() -> None:
    """一次认证连接后关闭监听；未知旧socket不删除，只清理自身同inode端点。"""
    manifest = isolated_parser_manifest_digest()

    def respond(raw: bytes) -> bytes:
        request = _object(raw)
        if (set(request) != {"schema_version", "request_id", "parser_manifest_digest", "contents"}
            or request["schema_version"] != "isolated_markdown_request_v1"
            or request["parser_manifest_digest"] != manifest or not isinstance(request["request_id"], str)
            or str(UUID(request["request_id"])) != request["request_id"]):
            raise ValueError()
        try:
            result = _parse(request["contents"])
        except KnowledgeParserError as error:
            result = {"error": error.code if error.code in _PARSE_ERRORS else "MARKDOWN_PARSE_FAILED"}
        except Exception:
            result = {"error": "MARKDOWN_CONTENT_INVALID"}
        return _encode({"schema_version": "isolated_markdown_result_v1", "request_id": request["request_id"],
            "parser_manifest_digest": manifest, "request_digest": hashlib.sha256(raw).hexdigest(), "result": result})

    await _serve_once(directory=SOCKET_DIR, path=SOCKET_PATH, owner_uid=PARSER_UID,
        maximum_request=MAX_REQUEST_BYTES, maximum_response=MAX_RESPONSE_BYTES, respond=respond)


async def _serve_once(*, directory: Path, path: Path, owner_uid: int,
    maximum_request: int, maximum_response: int, respond: Callable[[bytes], bytes],
) -> None:
    """共用单次认证帧运输，不解释来源策略或选择读取/解析操作。"""
    _directory(directory=directory, owner_uid=owner_uid)
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    endpoint = None
    server = None
    finished = asyncio.Event()
    accepted = asyncio.Event()
    consumed = False
    tasks: set[asyncio.Task[None]] = set()

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        nonlocal consumed
        owner = False
        try:
            _peer(writer, APPLICATION_UID)
            if consumed:
                return
            consumed = True
            owner = True
            accepted.set()
            if server is not None:
                server.close()
            async with asyncio.timeout(8):
                raw = await _read_frame(reader, maximum_request)
                await _write_frame(writer, respond(raw), maximum_response)
        except Exception:
            writer.transport.abort()
        finally:
            await _close(writer)
            if owner:
                finished.set()

    def accept(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        if len(tasks) >= 2:
            writer.transport.abort()
            return
        task = asyncio.create_task(handle(reader, writer))
        tasks.add(task)
        def completed(operation: asyncio.Task[None]) -> None:
            tasks.discard(operation)
            if not operation.cancelled():
                operation.exception()
        task.add_done_callback(completed)

    try:
        # 手动bind不会像按path启动的高层server那样清理旧socket。
        listener.bind(str(path))
        os.chown(path, -1, APPLICATION_UID)
        os.chmod(path, 0o660)
        endpoint = _endpoint(directory=directory, path=path, owner_uid=owner_uid)
        listener.listen(1)
        listener.setblocking(False)
        server = await asyncio.start_unix_server(accept, sock=listener, start_serving=False, limit=65_536)
        async with server:
            await server.start_serving()
            async with asyncio.timeout(60):
                await accepted.wait()
            async with asyncio.timeout(10):
                await finished.wait()
    finally:
        if server is not None:
            server.close()
            await server.wait_closed()
        listener.close()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if endpoint is not None:
            try:
                if _endpoint(directory=directory, path=path, owner_uid=owner_uid) == endpoint:
                    path.unlink()
            except (OSError, ValueError):
                pass


def main() -> None:
    """独立镜像固定入口，不接受参数/环境中的来源或任意命令，不打印解析正文。"""
    alarm_enabled = False
    try:
        if len(sys.argv) != 1:
            raise ValueError()
        require_parser_runtime()
        signal.signal(signal.SIGALRM, lambda *_: os._exit(2))
        signal.alarm(75)
        alarm_enabled = True
        asyncio.run(serve_parser_once())
    except BaseException:
        raise SystemExit(2) from None
    finally:
        if alarm_enabled:
            signal.alarm(0)


if __name__ == "__main__":
    main()
