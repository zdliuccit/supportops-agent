"""运维管理的单一来源批准配置；每个边界重新读取，不接受HTTP或知识内容自批。"""

import asyncio
import json
import os
import stat
import threading
from concurrent.futures import Future
from typing import Literal
from uuid import UUID

from pydantic import Field, ValidationError, model_validator
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from supportops_core.agent_services import PermissionDeniedError
from supportops_core.auth import AuthenticationError, IdentityContext
from supportops_core.config import Settings
from supportops_core.knowledge_ingestion_contracts import (
    INGESTION_MANUAL_WINDOW_SECONDS, INGESTION_TRANSACTION_TIMEOUT_SECONDS,
    IngestionContract, IngestionJobView, IngestionScope, IngestionSourceReadiness,
    ingestion_object_key, ingestion_scope_digest,
)
from supportops_core.knowledge_ingestion_runtime import ApprovedLocalMarkdownSource, IngestionExecutionUncertain
from supportops_core.knowledge_ingestion_services import (
    IngestionConflict, IngestionNotApproved, IngestionWritePolicy, check_ingestion_source,
    create_ingestion_job, get_ingestion_job, ingestion_job_view,
)
from supportops_core.knowledge_markdown_source import (
    LOCAL_MARKDOWN_ADAPTER_MANIFEST_DIGEST, LOCAL_MARKDOWN_IDENTITY_POLICY_DIGEST,
    LOCAL_MARKDOWN_PARSER_MANIFEST_DIGEST, LocalMarkdownError, LocalMarkdownSelection, local_markdown_selection_digest,
)
from supportops_core.models import utc_now
from supportops_core.services import ResourceNotFoundError

_APPROVAL_FILE_READ_SLOTS = threading.BoundedSemaphore(2)


class LocalMarkdownApprovalFile(IngestionContract):
    """仅运维受控文件使用；完整策略须经独立批准，解析成功不代表已通过验收。"""

    schema_version: Literal["local_markdown_approval_v1"]
    root_directory: str = Field(min_length=1, max_length=4096, repr=False)
    relative_paths: tuple[str, ...] = Field(min_length=1, max_length=100, repr=False)
    write_policy: IngestionWritePolicy = Field(repr=False)

    def plan(self) -> ApprovedLocalMarkdownSource:
        return ApprovedLocalMarkdownSource(selection=LocalMarkdownSelection(
            root_directory=self.root_directory, relative_paths=self.relative_paths), write_policy=self.write_policy)

    @model_validator(mode="after")
    def validate_binding(self) -> "LocalMarkdownApprovalFile":
        plan, scope = self.plan(), self.write_policy.scope
        if (local_markdown_selection_digest(plan.selection) != scope.source_contract_digest
            or scope.identity_policy_digest != LOCAL_MARKDOWN_IDENTITY_POLICY_DIGEST
            or scope.adapter_manifest_digest != LOCAL_MARKDOWN_ADAPTER_MANIFEST_DIGEST
            or scope.parser_manifest_digest != LOCAL_MARKDOWN_PARSER_MANIFEST_DIGEST
            or {ingestion_object_key(scope, path) for path in self.relative_paths}
                != set(self.write_policy.allowed_object_keys)):
            raise ValueError("批准配置与固定读取契约或完整对象集合不一致")
        return self


def _unique_json(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("批准配置不能含重复字段")
        result[key] = value
    return result


def _file_facts(value: os.stat_result) -> tuple[int, ...]:
    return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns,
        value.st_ctime_ns, value.st_nlink, value.st_uid, value.st_mode)


class ApprovalFileError(Exception):
    """仅保留固定分类，不将路径、原文或解析错误输入传播给维护命令。"""

    def __init__(self, code: Literal[
        "APPROVAL_FILE_UNAVAILABLE", "APPROVAL_JSON_INVALID", "APPROVAL_CONTRACT_INVALID",
    ]) -> None:
        super().__init__(code)
        self.code = code


def _read_approval_bytes(path: str) -> bytes:
    # 只读运维指定的单个配置文件，不访问其中声明的任何知识目录/文件。
    if (not path.startswith("/") or len(path) > 4096 or "\\" in path
        or any(ord(char) < 32 for char in path)):
        raise ValueError("批准配置路径无效")
    parts = path.split("/")[1:]
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise ValueError("批准配置路径无效")
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
    directory = os.open("/", flags | os.O_DIRECTORY)
    try:
        for part in parts[:-1]:
            following = os.open(part, flags | os.O_DIRECTORY, dir_fd=directory)
            os.close(directory)
            directory = following
            info = os.fstat(directory)
            if info.st_uid not in {0, os.geteuid()} or info.st_mode & 0o022:
                raise ValueError("批准配置父目录不受当前服务身份保护")
        descriptor = os.open(parts[-1], flags, dir_fd=directory)
        try:
            before = os.fstat(descriptor)
            if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                or before.st_uid not in {0, os.geteuid()} or before.st_mode & 0o077
                or not 0 < before.st_size <= 131072):
                raise ValueError("批准配置必须是受保护的有限普通文件")
            chunks: list[bytes] = []
            remaining = 131073
            while remaining:
                chunk = os.read(descriptor, min(remaining, 16384))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            raw, after = b"".join(chunks), os.fstat(descriptor)
            if not raw or len(raw) > 131072 or len(raw) != before.st_size or _file_facts(before) != _file_facts(after):
                raise ValueError("批准配置在读取期间变化或超过限制")
        finally:
            os.close(descriptor)
    finally:
        os.close(directory)
    return raw


def read_local_markdown_approval_file(path: str) -> LocalMarkdownApprovalFile:
    """运行时和离线配置预检共用；仅读指定配置，不读取来源、环境或数据库。"""
    try:
        raw = _read_approval_bytes(path)
    except (OSError, ValueError):
        raise ApprovalFileError("APPROVAL_FILE_UNAVAILABLE") from None
    # JSON模式保留严格UUID/带时区日期与tuple契约；先排除重复键，避免不同解析器选择不同范围。
    try:
        json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_json)
    except (UnicodeError, ValueError, RecursionError):
        raise ApprovalFileError("APPROVAL_JSON_INVALID") from None
    try:
        return LocalMarkdownApprovalFile.model_validate_json(raw)
    except (ValidationError, LocalMarkdownError, ValueError, RecursionError):
        raise ApprovalFileError("APPROVAL_CONTRACT_INVALID") from None


async def _read_current_approval_file(path: str) -> LocalMarkdownApprovalFile:
    """进程内最多两个未结束的配置读取；不排队，超时不释放实际IO占用。"""
    result: Future[LocalMarkdownApprovalFile | None] = Future()
    if not _APPROVAL_FILE_READ_SLOTS.acquire(blocking=False):
        raise IngestionNotApproved("当前来源批准配置无法确认，请联系维护人员核对")

    def read() -> None:
        try:
            # 调用方已取消且线程尚未读取时，不再访问配置。
            if not result.set_running_or_notify_cancel():
                return
            try:
                record = read_local_markdown_approval_file(path)
            except Exception:
                # 跨线程只传固定失败，不保存或上抛包含路径/原文的异常。
                record = None
            result.set_result(record)
        finally:
            # 必须由实际读取者释放；请求超时/取消不能放出新额度继续堆积IO。
            _APPROVAL_FILE_READ_SLOTS.release()

    async with asyncio.timeout(1):
        try:
            # 独立daemon线程不占默认executor；内核IO未返回不阻塞解释器等待executor退出。
            threading.Thread(target=read, name="supportops-approval-read", daemon=True).start()
        except Exception:
            _APPROVAL_FILE_READ_SLOTS.release()
            raise IngestionNotApproved("当前来源批准配置无法确认，请联系维护人员核对") from None
        record = await asyncio.wrap_future(result)
    if record is None:
        raise IngestionNotApproved("当前来源批准配置无法确认，请联系维护人员核对")
    return record


class FileLocalMarkdownApprovalProvider:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def current(self, *, tenant_id: UUID, requested_by: UUID) -> ApprovedLocalMarkdownSource | None:
        settings = self._settings
        if (not settings.knowledge_ingestion_enabled or not settings.knowledge_ingestion_approval_file
            or settings.environment not in {"local", "dev", "test"}
            or settings.database_url.split(":", 1)[0].split("+", 1)[0] != "postgresql"):
            return None
        try:
            record = await _read_current_approval_file(settings.knowledge_ingestion_approval_file)
            policy = record.write_policy
            if (policy.scope.tenant_id != tenant_id or policy.requested_by != requested_by
                or policy.valid_until <= utc_now()):
                return None
            return record.plan()
        except Exception:
            # 配置路径、原文及Pydantic错误输入均不能进入HTTP错误或日志。
            raise IngestionNotApproved("当前来源批准配置无法确认，请联系维护人员核对") from None

    async def __call__(self, *, scope: IngestionScope, requested_by: UUID) -> ApprovedLocalMarkdownSource | None:
        plan = await self.current(tenant_id=scope.tenant_id, requested_by=requested_by)
        return plan if plan is not None and plan.write_policy.scope == scope else None


async def read_configured_ingestion_source(factory: async_sessionmaker[AsyncSession], *,
    identity: IdentityContext, provider: FileLocalMarkdownApprovalProvider,
) -> IngestionSourceReadiness:
    plan = await provider.current(tenant_id=identity.principal.tenant_id, requested_by=identity.principal.user_id)
    if plan is None:
        return IngestionSourceReadiness(available=False, observed_at=utc_now())
    try:
        async with (
            asyncio.timeout(INGESTION_TRANSACTION_TIMEOUT_SECONDS), factory() as session, session.begin(),
        ):
            source = await check_ingestion_source(session, identity=identity, policy=plan.write_policy)
            if await provider(scope=plan.write_policy.scope, requested_by=identity.principal.user_id) != plan:
                raise IngestionNotApproved("来源批准已变化，请重新读取")
            if plan.write_policy.valid_until <= utc_now():
                raise IngestionNotApproved("来源批准已到期")
            return IngestionSourceReadiness(available=True, source_id=source.id, source_name=source.name,
                scope_digest=ingestion_scope_digest(plan.write_policy.scope),
                object_count=len(plan.selection.relative_paths), valid_until=plan.write_policy.valid_until,
                create_available=(plan.write_policy.valid_until - utc_now()).total_seconds() >= INGESTION_MANUAL_WINDOW_SECONDS,
                observed_at=utc_now())
    except TimeoutError:
        raise IngestionConflict("来源许可核对超过固定时限，请刷新后重新核对") from None


async def create_configured_ingestion_job(factory: async_sessionmaker[AsyncSession], *,
    identity: IdentityContext, provider: FileLocalMarkdownApprovalProvider,
    expected_scope_digest: str, request_key: UUID,
) -> IngestionJobView:
    plan = await provider.current(tenant_id=identity.principal.tenant_id, requested_by=identity.principal.user_id)
    if plan is None:
        raise IngestionNotApproved("当前没有本人获准的来源配置")
    if ingestion_scope_digest(plan.write_policy.scope) != expected_scope_digest:
        raise IngestionConflict("来源批准范围已变化，请重新读取后明确创建")
    # 创建请求使用固定预算；靠近批准截止时不缩短预算，避免相同请求键重放变成异参。
    try:
        async with (
            asyncio.timeout(INGESTION_TRANSACTION_TIMEOUT_SECONDS), factory() as session, session.begin(),
        ):
            row = await create_ingestion_job(session, identity=identity, scope=plan.write_policy.scope,
                idempotency_key=str(request_key), policy=plan.write_policy, correlation_id=f"ingestion-create:{request_key}")
            if await provider(scope=plan.write_policy.scope, requested_by=identity.principal.user_id) != plan:
                raise IngestionNotApproved("来源批准已变化，未创建新任务")
            row = await get_ingestion_job(session, identity=identity, job_id=row.id)
            if plan.write_policy.valid_until <= utc_now():
                raise IngestionNotApproved("来源批准已到期")
            result = ingestion_job_view(row)
    except (IngestionConflict, PermissionDeniedError, AuthenticationError, ResourceNotFoundError):
        raise
    except Exception:
        raise IngestionExecutionUncertain("创建结果无法确认，请按原请求标识查询；勿自动重发创建请求") from None
    return result
