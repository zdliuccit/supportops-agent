"""外部知识导入的版本化元数据契约；不拉取来源、不持久化游标或发布知识。"""

import hashlib
import json
from datetime import datetime, timedelta
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
INGESTION_MANUAL_WINDOW_SECONDS = 600
INGESTION_TRANSACTION_TIMEOUT_SECONDS = 10.0
IngestionStatus = Literal[
    "defined", "blocked", "queued", "running", "retry_wait", "completed",
    "completed_with_rejections", "failed", "cancelled",
]
IngestionFailure = Literal[
    "source_unapproved", "authorization_changed", "credential_unavailable",
    "source_unavailable", "rate_limited", "cursor_invalid", "source_version_conflict",
    "parse_rejected", "output_too_large", "budget_exhausted", "commit_conflict",
    "recovery_required",
]
PersistedIngestionStatus = Literal["queued", "running", "retry_wait", "completed", "failed", "cancelled"]


class IngestionContract(BaseModel):
    """契约及嵌套集合不可原地修改，禁止任意metadata或秘密字段。"""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class IngestionCounts(IngestionContract):
    draft_created: int = Field(ge=0, description="已提交的新草稿数。")
    unchanged: int = Field(ge=0, description="已提交的原版本复用数。")
    source_removed: int = Field(ge=0, description="已记录的来源移除观察数，不代表删除知识。")
    rejected: int = Field(ge=0, description="已提交的明确拒绝对象数。")
    quarantined: int = Field(ge=0, description="已提交的待隔离复核对象数。")


class IngestionJobView(IngestionContract):
    """仅本人可读的封闭任务投影；损坏记录隐藏范围和全部执行字段。"""

    schema_version: Literal["knowledge_ingestion_job_view_v1"] = "knowledge_ingestion_job_view_v1"
    id: UUID = Field(description="当前本人导入任务ID。")
    creation_request_key: UUID | None = Field(default=None, description="仅投影规范UUID格式的原创建键，非规范UUID的旧字符串不透出；不是执行许可。")
    integrity_status: Literal["recorded", "integrity_failed"] = "integrity_failed"
    status: Literal["queued", "running", "retry_wait", "completed", "failed", "cancelled", "unknown"] = "unknown"
    source_id: UUID | None = Field(default=None, description="通过固定范围校验的来源ID。")
    scope_digest: Digest | None = Field(default=None, description="任务创建时的固定范围摘要，仅用于与当前批准比对，不授予执行权限。")
    source_name: str | None = Field(default=None, max_length=200, description="当前同租户来源名称，缺失不补造。")
    revision: int | None = Field(default=None, ge=1, description="执行操作必须提交的当前修订。")
    generation: int | None = Field(default=None, ge=0, description="执行围栏代次，不是授权。")
    attempts: int | None = Field(default=None, ge=0, description="累计实际领取次数。")
    max_attempts: int | None = Field(default=None, ge=1, description="固定尝试上限。")
    created_at: datetime | None = Field(default=None, description="原始创建时间，不按读取补造。")
    deadline_at: datetime | None = Field(default=None, description="固定截止时间，不代表执行器可用。")
    lease_expires_at: datetime | None = Field(default=None, description="当前租约截止，过期不自动更改状态。")
    retry_at: datetime | None = Field(default=None, description="有限重试最早时间，不表示已有后台调度。")
    failure_code: IngestionFailure | None = Field(default=None, description="固定失败码，无原始错误。")
    checkpoint_kind: Literal["initial", "continuation", "complete"] | None = None
    committed_pages: int | None = Field(default=None, ge=0, description="已提交检查点序号。")
    max_pages: int | None = Field(default=None, ge=1, description="固定页面上限。")
    max_objects_per_page: int | None = Field(default=None, ge=1, description="固定单页对象上限。")
    counts: IngestionCounts | None = Field(default=None, description="已提交计数，损坏或未知时为空。")
    cancel_available: bool = Field(default=False, description="本人当前可显式取消，提交时仍会复检。")
    expire_available: bool = Field(default=False, description="已达到显式超时收敛边界，不是自动失败事实。")
    automatic_publish_enabled: Literal[False] = False


class IngestionRevisionCommand(IngestionContract):
    expected_revision: int = Field(ge=1, description="本人最后读取的任务修订；变化时拒绝操作。")


class IngestionCreateCommand(IngestionContract):
    expected_scope_digest: Digest = Field(description="本人读取的服务器批准范围摘要；不接受路径、ACL或批准参数。")


class IngestionSourceReadiness(IngestionContract):
    schema_version: Literal["knowledge_ingestion_source_readiness_v1"] = "knowledge_ingestion_source_readiness_v1"
    available: bool = Field(description="当前来源配置与本人控制面身份可用，提交时仍须重新检查。")
    create_available: bool = Field(default=False, description="批准剩余时间允许新建固定600秒窗口任务；提交时仍复检。")
    source_id: UUID | None = None
    source_name: str | None = Field(default=None, max_length=200)
    scope_digest: Digest | None = Field(default=None, description="当前批准范围摘要，不含路径或完整策略。")
    object_count: int | None = Field(default=None, ge=1, le=100)
    valid_until: datetime | None = None
    observed_at: datetime = Field(description="服务器本次检查时间，不表示来源读取事实。")
    automatic_publish_enabled: Literal[False] = False


class IngestionCreationStopCommand(IngestionContract):
    confirm_stop: Literal[True] = Field(description="本人明确确认永久封锁原创建请求；已存在任务不受此操作取消。")

    @model_validator(mode="before")
    @classmethod
    def require_explicit_confirmation(cls, value: object) -> object:
        if not isinstance(value, dict) or value.get("confirm_stop") is not True:
            raise ValueError("必须明确提交JSON布尔confirm_stop=true")
        return value


class IngestionCreationStatus(IngestionContract):
    schema_version: Literal["knowledge_ingestion_creation_status_v1"] = "knowledge_ingestion_creation_status_v1"
    request_key: UUID
    status: Literal["unobserved", "recorded", "stopped"]
    job: IngestionJobView | None = None
    stopped_at: datetime | None = Field(default=None, description="有完整停止事实时的原始记录时间，不按读取补造。")
    observed_at: datetime = Field(description="服务器本次查询时间，不是创建或停止事实。")

    @model_validator(mode="after")
    def validate_outcome(self) -> "IngestionCreationStatus":
        if self.status == "recorded":
            if self.job is None or self.stopped_at is not None:
                raise ValueError("已观察任务必须有原任务而不能伪造停止")
            if self.job.integrity_status == "recorded" and self.job.creation_request_key != self.request_key:
                raise ValueError("原任务与查询的创建请求标识不一致")
        elif self.status == "stopped":
            if self.job is not None or self.stopped_at is None:
                raise ValueError("明确停止不能夹带任务或缺少原始事实时间")
        elif self.job is not None or self.stopped_at is not None:
            raise ValueError("未观察到记录不能补造任务或停止事实")
        return self


class IngestionScope(IngestionContract):
    """由可信服务端绑定的来源和批准范围；摘要本身不是授权。"""

    tenant_id: UUID
    source_id: UUID
    identity_policy_digest: Digest
    source_contract_digest: Digest
    authorization_scope_digest: Digest
    adapter_manifest_digest: Digest
    parser_manifest_digest: Digest


class IngestionCursor(IngestionContract):
    """只记录受控存储引用与摘要，禁止把原始page token或URL写入任务元数据。"""

    kind: Literal["initial", "continuation", "complete"]
    page_sequence: int = Field(ge=0, le=10000)
    cursor_secret_ref: UUID | None = None
    cursor_digest: Digest | None = None

    @model_validator(mode="after")
    def validate_position(self) -> "IngestionCursor":
        paired = self.cursor_secret_ref is not None and self.cursor_digest is not None
        empty = self.cursor_secret_ref is None and self.cursor_digest is None
        if self.kind == "initial":
            valid = self.page_sequence == 0 and empty
        elif self.kind == "continuation":
            valid = self.page_sequence > 0 and paired
        else:
            valid = self.page_sequence > 0 and empty
        if not valid:
            raise ValueError("导入游标类型、序号与受控引用不一致")
        return self


class IngestionJobDefinition(IngestionContract):
    schema_version: Literal["knowledge_ingestion_job_v1"] = "knowledge_ingestion_job_v1"
    id: UUID
    scope: IngestionScope
    requested_by: UUID
    created_at: datetime
    deadline_at: datetime
    initial_cursor: IngestionCursor
    max_attempts: int = Field(default=1, ge=1, le=3)
    max_pages: int = Field(default=1, ge=1, le=10000)
    max_objects_per_page: int = Field(default=50, ge=1, le=100)
    mode: Literal["manual"] = "manual"
    automatic_publish_enabled: Literal[False] = False
    automatic_acl_update_enabled: Literal[False] = False

    @model_validator(mode="after")
    def validate_deadline(self) -> "IngestionJobDefinition":
        if self.created_at.tzinfo is None or self.deadline_at.tzinfo is None:
            raise ValueError("导入任务时间必须包含时区")
        if not timedelta(0) < self.deadline_at - self.created_at <= timedelta(hours=24):
            raise ValueError("导入任务必须有不超过24小时的固定执行窗口")
        if self.initial_cursor.kind != "initial":
            raise ValueError("新导入任务必须从显式初始位置开始，恢复沿用原任务检查点")
        return self


class IngestionObjectReceipt(IngestionContract):
    """单个来源观察的处理事实；原始内容、任意错误及来源ID不进入回执。"""

    object_key: Digest
    source_revision_digest: Digest | None = None
    raw_content_digest: Digest | None = None
    parsed_content_digest: Digest | None = None
    document_metadata_digest: Digest | None = None
    source_acl_digest: Digest | None = None
    change_kind: Literal["upsert", "removed"]
    outcome: Literal[
        "draft_created", "unchanged", "source_removed", "quarantined",
        "rejected", "retryable_failure",
    ]
    document_id: UUID | None = None
    knowledge_version_id: UUID | None = None
    failure_code: IngestionFailure | None = None

    @model_validator(mode="after")
    def validate_materialization(self) -> "IngestionObjectReceipt":
        materialized = self.outcome in {"draft_created", "unchanged"}
        if materialized:
            if (self.change_kind != "upsert" or self.document_id is None
                or self.knowledge_version_id is None or self.raw_content_digest is None
                or self.parsed_content_digest is None or self.document_metadata_digest is None
                or self.source_acl_digest is None or self.failure_code is not None):
                raise ValueError("草稿/未变化回执必须具有完整来源、解析和目标绑定")
        elif self.document_id is not None or self.knowledge_version_id is not None:
            raise ValueError("未物化回执不能携带成功目标引用")
        if self.outcome == "source_removed" and self.change_kind != "removed":
            raise ValueError("来源删除观察只能记录移除事实，不能自动删除或发布知识")
        failed = self.outcome in {"quarantined", "rejected", "retryable_failure"}
        if failed != (self.failure_code is not None):
            raise ValueError("失败/隔离回执必须提供固定失败码，成功不能携带失败事实")
        if self.source_revision_digest is None and self.raw_content_digest is None:
            raise ValueError("来源观察至少需要版本摘要或原始内容摘要，不能猜测相同版本")
        return self


class IngestionJobState(IngestionContract):
    """持久化状态快照；字段一致性不表示已取得有效执行许可。"""

    job_id: UUID
    scope_digest: Digest
    status: IngestionStatus
    revision: int = Field(ge=1)
    generation: int = Field(ge=0)
    attempts: int = Field(ge=0, le=3)
    cursor: IngestionCursor
    owner_id: UUID | None = None
    lease_expires_at: datetime | None = None
    retry_at: datetime | None = None
    failure_code: IngestionFailure | None = None
    draft_created_count: int = Field(default=0, ge=0, le=1_000_000)
    unchanged_count: int = Field(default=0, ge=0, le=1_000_000)
    source_removed_count: int = Field(default=0, ge=0, le=1_000_000)
    rejected_count: int = Field(default=0, ge=0, le=1_000_000)
    quarantined_count: int = Field(default=0, ge=0, le=1_000_000)

    @model_validator(mode="after")
    def validate_state(self) -> "IngestionJobState":
        if self.generation < self.attempts:
            raise ValueError("执行代次不能小于累计领取次数")
        if self.status == "running":
            if (self.owner_id is None or self.lease_expires_at is None
                or self.generation < 1 or self.attempts < 1):
                raise ValueError("运行状态必须有执行者、租约、代次与实际领取次数")
        elif self.owner_id is not None or self.lease_expires_at is not None:
            raise ValueError("非运行状态不能保留可执行租约")
        if self.lease_expires_at is not None and self.lease_expires_at.tzinfo is None:
            raise ValueError("租约时间必须包含时区，不按当前读取补造有效性")
        if self.status in {"blocked", "retry_wait", "failed"} and self.failure_code is None:
            raise ValueError("阻塞/失败状态必须有固定失败码")
        if self.status == "retry_wait" and (
            self.attempts < 1 or self.failure_code not in {
                "source_unavailable", "rate_limited", "commit_conflict", "recovery_required",
            }
        ):
            raise ValueError("仅实际尝试过的临时失败可以有限重试")
        if self.status == "retry_wait":
            if self.retry_at is None or self.retry_at.tzinfo is None:
                raise ValueError("重试等待必须有带时区的固定可重试时间")
        elif self.retry_at is not None:
            raise ValueError("非重试等待不能携带自动重试时间")
        rejected = self.rejected_count + self.quarantined_count
        if self.status in {"completed", "completed_with_rejections"}:
            if self.cursor.kind != "complete" or self.attempts < 1 or self.failure_code is not None:
                raise ValueError("未取得结束检查点不能标记完成")
            if (self.status == "completed_with_rejections") != (rejected > 0):
                raise ValueError("完成状态必须区分仍有拒绝或隔离对象")
        elif self.cursor.kind == "complete":
            raise ValueError("结束检查点必须与完成状态同事务提交")
        if self.status == "defined" and (
            self.attempts != 0 or self.generation != 0 or self.cursor.kind != "initial"
            or self.failure_code is not None or self.draft_created_count or self.unchanged_count
            or self.source_removed_count or rejected
        ):
            raise ValueError("仅已定义的任务不能携带执行或物化事实")
        return self


class IngestionPageReceipt(IngestionContract):
    """页提交元数据；有可重试失败时整页回滚，不保存最终页幂等回执。"""

    schema_version: Literal["knowledge_ingestion_page_v1"] = "knowledge_ingestion_page_v1"
    job_id: UUID
    scope: IngestionScope
    expected_revision: int = Field(ge=1)
    generation: int = Field(ge=1)
    input_cursor: IngestionCursor
    output_cursor: IngestionCursor
    objects: tuple[IngestionObjectReceipt, ...] = Field(max_length=100)

    @model_validator(mode="after")
    def validate_page(self) -> "IngestionPageReceipt":
        if self.input_cursor.kind == "complete":
            raise ValueError("已结束的来源不能继续领取页面")
        if len({item.object_key for item in self.objects}) != len(self.objects):
            raise ValueError("单页不能含重复来源对象；多个不透明版本不能自行择新覆盖")
        retrying = any(item.outcome == "retryable_failure" for item in self.objects)
        if retrying:
            if self.output_cursor != self.input_cursor:
                raise ValueError("存在可重试失败时不能推进检查点")
            return self
        if (self.output_cursor.kind == "initial"
            or self.output_cursor.page_sequence != self.input_cursor.page_sequence + 1):
            raise ValueError("成功提交页面必须推进恰好一个检查点")
        if (self.output_cursor.kind == "continuation"
            and self.output_cursor.cursor_digest == self.input_cursor.cursor_digest):
            raise ValueError("来源重复返回同一游标，不能循环拉取")
        return self


def _digest(domain: str, value: object) -> str:
    payload = json.dumps([domain, value], ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def ingestion_scope_digest(scope: IngestionScope) -> str:
    return _digest("knowledge_ingestion_scope_v1", scope.model_dump(mode="json"))


def ingestion_object_key(scope: IngestionScope, canonical_source_object_id: str) -> str:
    """适配器先按来源固定策略规范化ID；不在这里去空白、改大小写或解释URL。"""
    try:
        encoded = canonical_source_object_id.encode("utf-8")
    except UnicodeError:
        raise ValueError("规范来源对象ID必须为有效UTF-8") from None
    if not encoded or len(encoded) > 4096:
        raise ValueError("规范来源对象ID必须非空且不超过4096字节")
    # 身份键不含Job、页、Run或来源版本；重复拉取/新Job仍定位同一对象。
    return _digest("knowledge_ingestion_object_v1", [str(scope.tenant_id), str(scope.source_id),
        scope.identity_policy_digest, canonical_source_object_id])


def ingestion_observation_key(scope: IngestionScope, receipt: IngestionObjectReceipt) -> str:
    """来源版本是不透明值的摘要，不作为大小或时间顺序依据。"""
    return _digest("knowledge_ingestion_observation_v1", [ingestion_scope_digest(scope),
        receipt.object_key, receipt.change_kind, receipt.source_revision_digest,
        receipt.raw_content_digest])


def ingestion_draft_key(scope: IngestionScope, receipt: IngestionObjectReceipt) -> str:
    """同来源同内容/元数据/ACL/解析契约的草稿意向不随Job或ETag改变而改变。"""
    if receipt.outcome not in {"draft_created", "unchanged"}:
        raise ValueError("未物化观察没有草稿幂等键")
    return ingestion_draft_identity(scope, object_key=receipt.object_key,
        parsed_content_digest=receipt.parsed_content_digest,
        document_metadata_digest=receipt.document_metadata_digest, source_acl_digest=receipt.source_acl_digest)


def ingestion_draft_identity(scope: IngestionScope, *, object_key: str,
    parsed_content_digest: str | None, document_metadata_digest: str | None,
    source_acl_digest: str | None,
) -> str:
    """物化前计算意向键，不需要伪造尚未生成的目标ID。"""
    return _digest("knowledge_ingestion_draft_v1", [str(scope.tenant_id), str(scope.source_id),
        scope.identity_policy_digest, scope.parser_manifest_digest, object_key,
        parsed_content_digest, document_metadata_digest, source_acl_digest])


def validate_ingestion_page(job: IngestionJobDefinition, page: IngestionPageReceipt) -> None:
    """只核对静态契约；当前授权/owner/generation与原子唯一性由持久化服务落实。"""
    if page.job_id != job.id or page.scope != job.scope:
        raise ValueError("页面不能跨任务、租户、来源或批准范围提交")
    if len(page.objects) > job.max_objects_per_page:
        raise ValueError("页面对象数超过固定任务预算")
    if page.input_cursor.page_sequence >= job.max_pages:
        raise ValueError("页面数已达到固定任务预算")
    if page.output_cursor.kind == "continuation" and page.output_cursor.page_sequence >= job.max_pages:
        raise ValueError("达到页预算但来源未结束，不能伪称导入完成或继续推进")


def ingestion_page_key(page: IngestionPageReceipt) -> str:
    # 页面在同Job内幂等；新的扫描可看到新来源事实，但仍复用同一对象身份键。
    return _digest("knowledge_ingestion_page_key_v1", [str(page.job_id),
        ingestion_scope_digest(page.scope), page.input_cursor.kind,
        page.input_cursor.page_sequence, page.input_cursor.cursor_digest])


def ingestion_page_result_digest(page: IngestionPageReceipt) -> str:
    """同页改参冲突比较不含owner/revision/generation或游标秘密存储位置。"""
    return _digest("knowledge_ingestion_page_result_v1", {
        "page_key": ingestion_page_key(page),
        "output": [page.output_cursor.kind, page.output_cursor.page_sequence,
            page.output_cursor.cursor_digest],
        "objects": [item.model_dump(mode="json") for item in sorted(
            page.objects, key=lambda item: item.object_key,
        )],
    })


def validate_ingestion_state(job: IngestionJobDefinition, state: IngestionJobState) -> None:
    if state.job_id != job.id or state.scope_digest != ingestion_scope_digest(job.scope):
        raise ValueError("任务状态与固定定义或来源范围不一致")
    if state.attempts > job.max_attempts or state.cursor.page_sequence > job.max_pages:
        raise ValueError("实际尝试次数或检查点超过固定任务预算")
    if state.status == "retry_wait" and state.attempts >= job.max_attempts:
        raise ValueError("重试预算已耗尽，不能继续等待自动重试")
    count = (state.draft_created_count + state.unchanged_count + state.source_removed_count
        + state.rejected_count + state.quarantined_count)
    if count > state.cursor.page_sequence * job.max_objects_per_page:
        raise ValueError("已提交对象数不能超过已提交页面的固定预算")
    if state.lease_expires_at is not None and state.lease_expires_at > job.deadline_at:
        raise ValueError("执行租约不能延长任务固定截止时间")
    if state.retry_at is not None and state.retry_at >= job.deadline_at:
        raise ValueError("超过任务截止时间不能继续排定重试")
