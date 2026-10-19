"""显式本地文件清单的只读预览；不扫描目录、不推断权限、不写知识版本。"""

import asyncio
import base64
import hashlib
import json
import math
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from supportops_core.knowledge_ingestion_contracts import (
    IngestionScope,
    ingestion_object_key,
    ingestion_scope_digest,
)
from supportops_core.knowledge_parser_isolated import (
    KnowledgeParserError, isolated_parser_manifest_digest, parse_markdown_isolated,
    require_isolated_parser_endpoint,
)
from supportops_core.knowledge_reader_isolated import (
    SOURCE_ROOT, KnowledgeReaderError, isolated_reader_manifest_digest, read_markdown_isolated,
    require_isolated_reader_endpoint, selection_digest as _selection_digest,
)

MAX_LOCAL_MARKDOWN_FILES = 100
MAX_LOCAL_MARKDOWN_FILE_BYTES = 1_048_576
MAX_LOCAL_MARKDOWN_BATCH_BYTES = 4_194_304
MAX_LOCAL_MARKDOWN_ANCHORS = 1000
LOCAL_MARKDOWN_IDENTITY_POLICY_DIGEST = hashlib.sha256(
    b"local_markdown_identity_v1:exact_relative_posix_path"
).hexdigest()
MAX_LOCAL_MARKDOWN_PARSED_BYTES = 8_388_608
_PROGRAM_SLOTS = threading.BoundedSemaphore(2)
_ACTIVE_PROGRAMS: set[asyncio.Task[bytes]] = set()

# 两个固定子进程共用限制代码；不能提高继承的CPU或句柄上限。
_PROCESS_LIMITS_PROGRAM = r"""
def limit_process():
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    cpu_soft, cpu_hard = resource.getrlimit(resource.RLIMIT_CPU)
    cpu_hard = 3 if cpu_hard == resource.RLIM_INFINITY else min(3, cpu_hard)
    cpu_soft = min(2, cpu_hard) if cpu_soft == resource.RLIM_INFINITY else min(2, cpu_hard, cpu_soft)
    resource.setrlimit(resource.RLIMIT_CPU, (cpu_soft, cpu_hard))
    fd_soft, fd_hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    fd_hard = 32 if fd_hard == resource.RLIM_INFINITY else min(32, fd_hard)
    fd_soft = fd_hard if fd_soft == resource.RLIM_INFINITY else min(fd_hard, fd_soft)
    resource.setrlimit(resource.RLIMIT_NOFILE, (fd_soft, fd_hard))
"""

# 固定程序只加载标准库，不继承应用环境，不解释正文或访问外链。
# openat/NOFOLLOW固定每一级目录描述符；不是独立UID、挂载或网络沙箱。
_READ_PROGRAM = (
    "import base64, json, os, resource, stat, sys\n" + _PROCESS_LIMITS_PROGRAM + r"""

def read_batch(request):
    limit_process()
    if not all(hasattr(os, name) for name in ('O_NOFOLLOW', 'O_DIRECTORY', 'O_NONBLOCK')):
        raise ValueError('MARKDOWN_PLATFORM_UNSUPPORTED')
    if os.open not in os.supports_dir_fd:
        raise ValueError('MARKDOWN_PLATFORM_UNSUPPORTED')
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    root_fd = os.open('/', directory_flags)
    try:
        for part in request['root'].split('/')[1:]:
            next_fd = os.open(part, directory_flags, dir_fd=root_fd)
            os.close(root_fd)
            root_fd = next_fd
        records, total, identities = [], 0, set()
        for path in request['paths']:
            parent_fd = os.dup(root_fd)
            try:
                parts = path.split('/')
                for part in parts[:-1]:
                    next_fd = os.open(part, directory_flags, dir_fd=parent_fd)
                    os.close(parent_fd)
                    parent_fd = next_fd
                file_fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                    dir_fd=parent_fd)
                try:
                    before = os.fstat(file_fd)
                    identity = (before.st_dev, before.st_ino)
                    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
                        raise ValueError('MARKDOWN_FILE_TYPE_DENIED')
                    if identity in identities:
                        raise ValueError('MARKDOWN_OBJECT_ALIAS_DENIED')
                    identities.add(identity)
                    if before.st_size > 1048576 or total + before.st_size > 4194304:
                        raise ValueError('MARKDOWN_SIZE_EXCEEDED')
                    chunks, size = [], 0
                    while True:
                        chunk = os.read(file_fd, min(65536, 1048577 - size))
                        if not chunk:
                            break
                        size += len(chunk)
                        if size > 1048576 or total + size > 4194304:
                            raise ValueError('MARKDOWN_SIZE_EXCEEDED')
                        chunks.append(chunk)
                    after = os.fstat(file_fd)
                    signature = lambda value: (value.st_dev, value.st_ino, value.st_size,
                        value.st_mtime_ns, value.st_ctime_ns, value.st_nlink)
                    if signature(before) != signature(after) or size != before.st_size:
                        raise ValueError('MARKDOWN_SOURCE_CHANGED')
                    total += size
                    records.append(base64.b64encode(b''.join(chunks)).decode('ascii'))
                finally:
                    os.close(file_fd)
            finally:
                os.close(parent_fd)
        return {'contents': records}
    finally:
        os.close(root_fd)

try:
    result = read_batch(json.loads(sys.stdin.buffer.read(500001)))
except ValueError as error:
    code = str(error)
    allowed = {'MARKDOWN_PLATFORM_UNSUPPORTED', 'MARKDOWN_FILE_TYPE_DENIED',
        'MARKDOWN_OBJECT_ALIAS_DENIED', 'MARKDOWN_SIZE_EXCEEDED', 'MARKDOWN_SOURCE_CHANGED'}
    result = {'error': code if code in allowed else 'MARKDOWN_READ_FAILED'}
except Exception:
    result = {'error': 'MARKDOWN_READ_FAILED'}
sys.stdout.write(json.dumps(result, separators=(',', ':')))
"""
)
LOCAL_MARKDOWN_ADAPTER_MANIFEST_DIGEST = hashlib.sha256(
    ("local_markdown_reader_v2:cpu2_3:fd32:core0:explicit_selection:no_retry:max100:1MiB:4MiB:" + _READ_PROGRAM).encode(
        "utf-8"
    )
).hexdigest()

# 仅加载部署包内固定knowledge_content.py；路径由服务器提供，不来自来源/HTTP/模型。
# 同一规范化/锚点实现继续被版本写入复用，进程不解释Markdown代码或访问内容中的路径。
_PARSER_MODULE_PATH = Path(__file__).absolute().with_name("knowledge_content.py")
_PARSER_MODULE_DIGEST = hashlib.sha256(_PARSER_MODULE_PATH.read_bytes()).hexdigest()
_PARSE_PROGRAM = (
    "import base64, hashlib, importlib.machinery, importlib.util, json, resource, sys\n"
    + _PROCESS_LIMITS_PROGRAM + r"""

def parse_batch(request):
    limit_process()
    path = request['parser_module']
    with open(path, 'rb') as source:
        parser_source = source.read(1048577)
        if hashlib.sha256(parser_source).hexdigest() != request['parser_digest']:
            raise ValueError('MARKDOWN_MANIFEST_MISMATCH')
    class VerifiedContentLoader(importlib.machinery.SourceFileLoader):
        def get_data(self, file_path):
            # 只执行刚核验的部署源码字节，不再读取源码或接受旧pyc缓存。
            if file_path != path:
                raise OSError('unavailable')
            return parser_source
    name = 'supportops_ingestion_content'
    spec = importlib.util.spec_from_file_location(name, path, loader=VerifiedContentLoader(name, path))
    if spec is None or spec.loader is None:
        raise ValueError('MARKDOWN_PARSER_UNAVAILABLE')
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    sys.dont_write_bytecode = True
    spec.loader.exec_module(module)
    records, total = [], 0
    contents = request['contents']
    if not isinstance(contents, list) or not 0 < len(contents) <= 100:
        raise ValueError('MARKDOWN_READER_RESPONSE_INVALID')
    for encoded in contents:
        raw = base64.b64decode(encoded, validate=True)
        total += len(raw)
        if len(raw) > 1048576 or total > 4194304:
            raise ValueError('MARKDOWN_SIZE_EXCEEDED')
        content = raw.decode('utf-8')
        if any(ord(char) < 32 and char not in '\t\r\n' for char in content):
            raise ValueError('MARKDOWN_CONTENT_INVALID')
        normalized = module.normalize_knowledge_markdown(content)
        if sum(line.startswith('#') for line in normalized.splitlines()) > 1000:
            raise ValueError('MARKDOWN_ANCHOR_LIMIT_EXCEEDED')
        records.append({'raw_content_digest': hashlib.sha256(raw).hexdigest(),
            'parsed_content_digest': module.knowledge_content_digest(normalized),
            'raw_bytes': len(raw), 'content_markdown': normalized,
            'section_anchors': module.parse_section_anchors(normalized)})
    return {'objects': records}

try:
    result = parse_batch(json.loads(sys.stdin.buffer.read(6000001)))
except ValueError as error:
    code = str(error)
    allowed = {'MARKDOWN_SIZE_EXCEEDED', 'MARKDOWN_CONTENT_INVALID', 'MARKDOWN_ANCHOR_LIMIT_EXCEEDED',
        'MARKDOWN_MANIFEST_MISMATCH', 'MARKDOWN_PARSER_UNAVAILABLE', 'MARKDOWN_READER_RESPONSE_INVALID'}
    result = {'error': code if code in allowed else 'MARKDOWN_CONTENT_INVALID'}
except Exception:
    result = {'error': 'MARKDOWN_PARSE_FAILED'}
payload = json.dumps(result, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
if len(payload) > 8388608:
    payload = b'{"error":"MARKDOWN_PARSE_OUTPUT_TOO_LARGE"}'
sys.stdout.buffer.write(payload)
"""
)
LOCAL_MARKDOWN_PARSER_MANIFEST_DIGEST = hashlib.sha256(
    ("local_markdown_plaintext_v3:shared_process_limits:separate_process:shared_deadline:max1000:8MiB:"
        + _PARSER_MODULE_DIGEST + ":" + _PARSE_PROGRAM).encode("utf-8")
).hexdigest()
LOCAL_MARKDOWN_ISOLATED_PARSER_MANIFEST_DIGEST = isolated_parser_manifest_digest()
LOCAL_MARKDOWN_ISOLATED_ADAPTER_MANIFEST_DIGEST = isolated_reader_manifest_digest()


def supports_local_markdown_parser(manifest_digest: str) -> bool:
    """两种明确实现各自批准，不静默替换旧任务或从隔离路径回退。"""
    return manifest_digest in {
        LOCAL_MARKDOWN_PARSER_MANIFEST_DIGEST, LOCAL_MARKDOWN_ISOLATED_PARSER_MANIFEST_DIGEST,
    }


def supports_local_markdown_implementation(adapter_digest: str, parser_digest: str) -> bool:
    """独立读取只能配独立解析；原读取器的两个已知批准组合保持不变。"""
    return ((adapter_digest == LOCAL_MARKDOWN_ADAPTER_MANIFEST_DIGEST
        and supports_local_markdown_parser(parser_digest))
        or (adapter_digest == LOCAL_MARKDOWN_ISOLATED_ADAPTER_MANIFEST_DIGEST
            and parser_digest == LOCAL_MARKDOWN_ISOLATED_PARSER_MANIFEST_DIGEST))


class LocalMarkdownError(Exception):
    """错误仅携带固定码，不暴露路径、正文或原始操作系统异常。"""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class LocalMarkdownSelection:
    """可信调用方提供的精确范围；创建此对象不代表来源或文件已经获准。"""

    root_directory: str = field(repr=False)
    relative_paths: tuple[str, ...] = field(repr=False)
    source_root_device: int | None = field(default=None, repr=False)
    source_root_inode: int | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True)
class MarkdownAnchor:
    id: str = field(repr=False)
    title: str = field(repr=False)
    level: int


@dataclass(frozen=True, slots=True)
class LocalMarkdownPreview:
    object_key: str
    raw_content_digest: str
    parsed_content_digest: str
    raw_bytes: int
    content_markdown: str = field(repr=False)
    section_anchors: tuple[MarkdownAnchor, ...] = field(repr=False)


@dataclass(frozen=True, slots=True)
class LocalMarkdownBatch:
    scope_digest: str
    selection_digest: str
    objects: tuple[LocalMarkdownPreview, ...] = field(repr=False)


def local_markdown_selection_digest(selection: LocalMarkdownSelection) -> str:
    """冻结目录与精确清单，不自动resolve/大小写折叠/展开glob；摘要不是授权。"""
    if not isinstance(selection, LocalMarkdownSelection):
        raise LocalMarkdownError("MARKDOWN_SELECTION_INVALID")
    try:
        return _selection_digest(selection.root_directory, selection.relative_paths,
            source_root_device=selection.source_root_device, source_root_inode=selection.source_root_inode)
    except KnowledgeReaderError as error:
        raise LocalMarkdownError(error.code) from None


async def _exchange_program(process: asyncio.subprocess.Process, payload: bytes, *,
    stage: Literal["READ", "PARSE"], maximum_output: int,
) -> bytes:
    """并行发送有限输入和接收有限输出；超限即停止，不先累积完整stdout。"""
    stdin, stdout = process.stdin, process.stdout
    if stdin is None or stdout is None:
        raise LocalMarkdownError(f"MARKDOWN_{stage}_FAILED")

    async def send_input() -> None:
        try:
            stdin.write(payload)
            await stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            # 与communicate相同，子进程提前拒绝输入时仍允许读取其固定错误回执。
            pass
        finally:
            stdin.close()
        try:
            await stdin.wait_closed()
        except (BrokenPipeError, ConnectionResetError):
            pass

    async def receive_output() -> bytes:
        chunks, total = [], 0
        while True:
            chunk = await stdout.read(min(65_536, maximum_output - total + 1))
            if not chunk:
                return b"".join(chunks)
            total += len(chunk)
            if total > maximum_output:
                raise LocalMarkdownError(f"MARKDOWN_{stage}_OUTPUT_TOO_LARGE")
            chunks.append(chunk)

    writer = asyncio.create_task(send_input())
    reader = asyncio.create_task(receive_output())
    waiter = asyncio.create_task(process.wait())
    tasks = (writer, reader, waiter)
    try:
        await asyncio.gather(*tasks)
        return reader.result()
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


@dataclass(slots=True)
class _ProgramControl:
    stop_requested: asyncio.Event = field(default_factory=asyncio.Event)
    reaped: bool = False


async def _supervise_program(program: str, payload: bytes, *, deadline: float,
    stage: Literal["READ", "PARSE"], maximum_output: int, control: _ProgramControl,
) -> bytes:
    """仅监督任务拥有进程；调用方取消通过信号请求停止，不打断回收。"""
    loop = asyncio.get_running_loop()
    if control.stop_requested.is_set():
        control.reaped = True
        raise asyncio.CancelledError
    if loop.time() >= deadline:
        control.reaped = True
        raise LocalMarkdownError(f"MARKDOWN_{stage}_TIMEOUT")
    try:
        process = await asyncio.create_subprocess_exec(
            sys.executable, "-I", "-S", "-B", "-c", program, env={}, close_fds=True,
            cwd="/", stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except OSError:
        control.reaped = True
        raise LocalMarkdownError(f"MARKDOWN_{'READER' if stage == 'READ' else 'PARSER'}_UNAVAILABLE") from None
    exchange: asyncio.Task[bytes] | None = None
    stop_waiter: asyncio.Task[bool] | None = None
    try:
        try:
            if control.stop_requested.is_set():
                raise asyncio.CancelledError
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise LocalMarkdownError(f"MARKDOWN_{stage}_TIMEOUT")
            exchange = asyncio.create_task(_exchange_program(process, payload,
                stage=stage, maximum_output=maximum_output))
            stop_waiter = asyncio.create_task(control.stop_requested.wait())
            completed, _ = await asyncio.wait((exchange, stop_waiter), timeout=remaining,
                return_when=asyncio.FIRST_COMPLETED)
            if control.stop_requested.is_set():
                raise asyncio.CancelledError
            if exchange not in completed:
                raise LocalMarkdownError(f"MARKDOWN_{stage}_TIMEOUT")
            stdout = exchange.result()
        except (OSError, ValueError):
            raise LocalMarkdownError(f"MARKDOWN_{stage}_FAILED") from None
    finally:
        if process.stdin is not None:
            try:
                process.stdin.close()
            except (OSError, ValueError):
                pass
        if process.returncode is None:
            try:
                process.kill()
            except OSError:
                # 仍等待实际退出；无法确认回收时不能归还额度或继续启动进程。
                pass
        try:
            operations = [operation for operation in (exchange, stop_waiter) if operation is not None]
            for operation in operations:
                if not operation.done():
                    operation.cancel()
            await asyncio.gather(*operations, return_exceptions=True)
            # 接收协程可能因超限/取消停止；杀进程后丢弃剩余管道字节，
            # 避免stdout暂停读取时只wait导致退出通知无法完成。此处不保留正文。
            if process.stdout is not None:
                while await process.stdout.read(65_536):
                    pass
        except (OSError, ValueError):
            pass
        finally:
            await process.wait()
            control.reaped = True
    if process.returncode != 0:
        raise LocalMarkdownError(f"MARKDOWN_{stage}_FAILED")
    if loop.time() >= deadline:
        raise LocalMarkdownError(f"MARKDOWN_{stage}_TIMEOUT")
    return stdout


async def _run_program(program: str, payload: bytes, *, deadline: float,
    stage: Literal["READ", "PARSE"], maximum_output: int,
) -> bytes:
    """每进程最多两个未回收程序；无排队，重复取消不提前归还额度。"""
    if asyncio.get_running_loop().time() >= deadline:
        raise LocalMarkdownError(f"MARKDOWN_{stage}_TIMEOUT")
    control = _ProgramControl()
    if not _PROGRAM_SLOTS.acquire(blocking=False):
        raise LocalMarkdownError("MARKDOWN_PROGRAM_LIMIT_REACHED")
    operation = _supervise_program(program, payload, deadline=deadline, stage=stage,
        maximum_output=maximum_output, control=control)
    try:
        task = asyncio.create_task(operation)
    except Exception:
        operation.close()
        _PROGRAM_SLOTS.release()
        raise LocalMarkdownError(f"MARKDOWN_{'READER' if stage == 'READ' else 'PARSER'}_UNAVAILABLE") from None
    _ACTIVE_PROGRAMS.add(task)

    def finished(completed: asyncio.Task[bytes]) -> None:
        _ACTIVE_PROGRAMS.discard(completed)
        if control.reaped:
            _PROGRAM_SLOTS.release()
        # 强制停机等情况无法确认启动/回收时保留占用，不能假定子进程已停止。
        if not completed.cancelled():
            completed.exception()

    task.add_done_callback(finished)
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        control.stop_requested.set()
        try:
            await asyncio.shield(task)
        except BaseException:
            # 保留调用方取消；再次取消也不传入监督任务，强引用/额度持续到实际回收。
            pass
        raise


def _response_error(result: object, allowed: set[str], fallback: str) -> None:
    if isinstance(result, dict) and set(result) == {"error"}:
        code = result["error"]
        raise LocalMarkdownError(code if isinstance(code, str) and code in allowed else fallback)


async def preview_local_markdown(
    *, selection: LocalMarkdownSelection, scope: IngestionScope,
    expected_selection_digest: str, timeout_seconds: float = 5,
) -> LocalMarkdownBatch:
    """读取和正文解析共用有限期限；不自动重试、不推断知识ACL。"""
    selection_digest = local_markdown_selection_digest(selection)
    if (not isinstance(scope, IngestionScope)
        or selection_digest != expected_selection_digest
        or selection_digest != scope.source_contract_digest):
        raise LocalMarkdownError("MARKDOWN_SELECTION_CHANGED")
    if (scope.identity_policy_digest != LOCAL_MARKDOWN_IDENTITY_POLICY_DIGEST
        or not supports_local_markdown_implementation(scope.adapter_manifest_digest, scope.parser_manifest_digest)):
        raise LocalMarkdownError("MARKDOWN_MANIFEST_MISMATCH")
    if (type(timeout_seconds) not in {int, float} or not math.isfinite(timeout_seconds)
        or not 0 < timeout_seconds <= 30):
        raise LocalMarkdownError("MARKDOWN_TIMEOUT_INVALID")
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    use_isolated_reader = scope.adapter_manifest_digest == LOCAL_MARKDOWN_ISOLATED_ADAPTER_MANIFEST_DIGEST
    use_isolated_parser = scope.parser_manifest_digest == LOCAL_MARKDOWN_ISOLATED_PARSER_MANIFEST_DIGEST
    if use_isolated_reader:
        if (selection.root_directory != SOURCE_ROOT or selection.source_root_device is None
            or selection.source_root_inode is None):
            raise LocalMarkdownError("MARKDOWN_SELECTION_CHANGED")
        try:
            require_isolated_reader_endpoint(expected_manifest_digest=scope.adapter_manifest_digest)
        except KnowledgeReaderError as error:
            raise LocalMarkdownError(error.code) from None
    elif selection.source_root_device is not None or selection.source_root_inode is not None:
        raise LocalMarkdownError("MARKDOWN_SELECTION_CHANGED")
    if use_isolated_parser:
        try:
            require_isolated_parser_endpoint(expected_manifest_digest=scope.parser_manifest_digest)
        except KnowledgeParserError as error:
            raise LocalMarkdownError(error.code) from None
    paths = sorted(selection.relative_paths)
    payload = json.dumps({"root": selection.root_directory, "paths": paths},
        ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(payload) > 500_000:
        raise LocalMarkdownError("MARKDOWN_SELECTION_INVALID")
    try:
        if use_isolated_reader:
            try:
                result = await read_markdown_isolated(scope_digest=ingestion_scope_digest(scope),
                    expected_selection_digest=selection_digest, expected_manifest_digest=scope.adapter_manifest_digest,
                    expected_parser_manifest_digest=scope.parser_manifest_digest, deadline=deadline)
            except KnowledgeReaderError as error:
                raise LocalMarkdownError(error.code) from None
        else:
            stdout = await _run_program(_READ_PROGRAM, payload, deadline=deadline, stage="READ", maximum_output=6_000_000)
            result = json.loads(stdout)
        _response_error(result, {"MARKDOWN_PLATFORM_UNSUPPORTED", "MARKDOWN_FILE_TYPE_DENIED",
            "MARKDOWN_OBJECT_ALIAS_DENIED", "MARKDOWN_SIZE_EXCEEDED",
            "MARKDOWN_SOURCE_CHANGED", "MARKDOWN_READ_FAILED"}, "MARKDOWN_READ_FAILED")
        if (not isinstance(result, dict) or set(result) != {"contents"}
            or not isinstance(result["contents"], list) or len(result["contents"]) != len(paths)):
            raise LocalMarkdownError("MARKDOWN_READER_RESPONSE_INVALID")
        expected, total = [], 0
        for encoded in result["contents"]:
            if not isinstance(encoded, str):
                raise LocalMarkdownError("MARKDOWN_READER_RESPONSE_INVALID")
            raw = base64.b64decode(encoded, validate=True)
            total += len(raw)
            if len(raw) > MAX_LOCAL_MARKDOWN_FILE_BYTES or total > MAX_LOCAL_MARKDOWN_BATCH_BYTES:
                raise LocalMarkdownError("MARKDOWN_SIZE_EXCEEDED")
            expected.append((hashlib.sha256(raw).hexdigest(), len(raw)))
        if use_isolated_parser:
            try:
                parsed = await parse_markdown_isolated(result["contents"], deadline=deadline,
                    expected_manifest_digest=scope.parser_manifest_digest)
            except KnowledgeParserError as error:
                raise LocalMarkdownError(error.code) from None
        else:
            parser_payload = json.dumps({"parser_module": str(_PARSER_MODULE_PATH),
                "parser_digest": _PARSER_MODULE_DIGEST, "contents": result["contents"]},
                separators=(",", ":")).encode("utf-8")
            if len(parser_payload) > 6_000_000:
                raise LocalMarkdownError("MARKDOWN_SIZE_EXCEEDED")
            parsed = json.loads(await _run_program(_PARSE_PROGRAM, parser_payload,
                deadline=deadline, stage="PARSE", maximum_output=MAX_LOCAL_MARKDOWN_PARSED_BYTES))
        _response_error(parsed, {"MARKDOWN_SIZE_EXCEEDED", "MARKDOWN_CONTENT_INVALID",
            "MARKDOWN_ANCHOR_LIMIT_EXCEEDED", "MARKDOWN_MANIFEST_MISMATCH", "MARKDOWN_PARSER_UNAVAILABLE",
            "MARKDOWN_READER_RESPONSE_INVALID", "MARKDOWN_PARSE_FAILED", "MARKDOWN_PARSE_OUTPUT_TOO_LARGE"},
            "MARKDOWN_PARSE_FAILED")
        if (not isinstance(parsed, dict) or set(parsed) != {"objects"}
            or not isinstance(parsed["objects"], list) or len(parsed["objects"]) != len(paths)):
            raise LocalMarkdownError("MARKDOWN_READER_RESPONSE_INVALID")
        objects, normalized_total = [], 0
        for path, item, (raw_digest, raw_bytes) in zip(paths, parsed["objects"], expected, strict=True):
            if (not isinstance(item, dict) or set(item) != {"raw_content_digest", "parsed_content_digest",
                "raw_bytes", "content_markdown", "section_anchors"}
                or item["raw_content_digest"] != raw_digest or type(item["raw_bytes"]) is not int
                or item["raw_bytes"] != raw_bytes or not isinstance(item["content_markdown"], str)
                or not isinstance(item["section_anchors"], list)
                or len(item["section_anchors"]) > MAX_LOCAL_MARKDOWN_ANCHORS):
                raise LocalMarkdownError("MARKDOWN_READER_RESPONSE_INVALID")
            normalized = item["content_markdown"]
            normalized_bytes = normalized.encode("utf-8")
            normalized_total += len(normalized_bytes)
            if (not normalized_bytes or len(normalized_bytes) > MAX_LOCAL_MARKDOWN_FILE_BYTES
                or normalized_total > MAX_LOCAL_MARKDOWN_BATCH_BYTES
                or hashlib.sha256(normalized_bytes).hexdigest() != item["parsed_content_digest"]):
                raise LocalMarkdownError("MARKDOWN_READER_RESPONSE_INVALID")
            anchors = []
            for anchor in item["section_anchors"]:
                if (not isinstance(anchor, dict) or set(anchor) != {"id", "title", "level"}
                    or not isinstance(anchor["id"], str) or not isinstance(anchor["title"], str)
                    or type(anchor["level"]) is not str or anchor["level"] not in {"1", "2", "3", "4", "5", "6"}):
                    raise LocalMarkdownError("MARKDOWN_READER_RESPONSE_INVALID")
                anchors.append(MarkdownAnchor(id=anchor["id"], title=anchor["title"], level=int(anchor["level"])))
            objects.append(LocalMarkdownPreview(
                object_key=ingestion_object_key(scope, path),
                raw_content_digest=raw_digest, parsed_content_digest=item["parsed_content_digest"],
                raw_bytes=raw_bytes, content_markdown=normalized, section_anchors=tuple(anchors),
            ))
    except (ValueError, TypeError):
        raise LocalMarkdownError("MARKDOWN_CONTENT_INVALID") from None
    if asyncio.get_running_loop().time() >= deadline:
        raise LocalMarkdownError("MARKDOWN_PARSE_TIMEOUT")
    return LocalMarkdownBatch(
        scope_digest=ingestion_scope_digest(scope), selection_digest=selection_digest,
        objects=tuple(objects),
    )
