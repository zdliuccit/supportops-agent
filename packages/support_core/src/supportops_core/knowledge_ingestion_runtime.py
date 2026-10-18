"""可信来源提供者驱动的本地单页执行链；人工API默认关闭，无自动调度。"""

import asyncio
from dataclasses import dataclass, field
from typing import Protocol
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from supportops_core.agent_services import PermissionDeniedError
from supportops_core.auth import AuthenticationError, IdentityContext
from supportops_core.knowledge_ingestion_contracts import (
    INGESTION_TRANSACTION_TIMEOUT_SECONDS,
    IngestionFailure, IngestionJobDefinition, IngestionJobState, IngestionJobView,
    IngestionScope, ingestion_object_key, ingestion_scope_digest,
)
from supportops_core.knowledge_ingestion_services import (
    IngestionConflict, IngestionDraftCandidate, IngestionNotApproved, IngestionTargetChanged, IngestionWritePolicy,
    _job_contracts, check_ingestion_execution, claim_ingestion_job, commit_ingestion_page,
    fail_ingestion_job, get_ingestion_job, ingestion_job_view, invalidate_ingestion_execution,
)
from supportops_core.knowledge_markdown_source import (
    LocalMarkdownBatch, LocalMarkdownError, LocalMarkdownSelection,
    local_markdown_selection_digest, preview_local_markdown,
)
from supportops_core.models import utc_now
from supportops_core.services import ResourceNotFoundError


@dataclass(frozen=True, slots=True)
class ApprovedLocalMarkdownSource:
    """只能由可信配置/批准服务提供；构造此数据本身不产生来源批准。"""

    selection: LocalMarkdownSelection = field(repr=False)
    write_policy: IngestionWritePolicy = field(repr=False)


class LocalMarkdownApprovalProvider(Protocol):
    async def __call__(self, *, scope: IngestionScope, requested_by: UUID) -> ApprovedLocalMarkdownSource | None:
        """重新取得当前批准；不得从HTTP、模型参数或内容生成自批策略。"""
        ...


class IngestionExecutionUncertain(IngestionConflict):
    """数据库提交可能已发生，调用方须查询原任务/页回执，不能重新读取或重放执行。"""

    code = "KNOWLEDGE_INGESTION_EXECUTION_UNCERTAIN"


async def _approved_plan(provider: LocalMarkdownApprovalProvider, definition: IngestionJobDefinition,
    previous: ApprovedLocalMarkdownSource | None = None,
) -> ApprovedLocalMarkdownSource:
    try:
        async with asyncio.timeout(1):
            plan = await provider(scope=definition.scope, requested_by=definition.requested_by)
        if (not isinstance(plan, ApprovedLocalMarkdownSource)
            or not isinstance(plan.write_policy, IngestionWritePolicy)
            or plan.write_policy.scope != definition.scope
            or plan.write_policy.requested_by != definition.requested_by
            or plan.write_policy.valid_until <= utc_now()
            or local_markdown_selection_digest(plan.selection) != definition.scope.source_contract_digest
            or {ingestion_object_key(definition.scope, path) for path in plan.selection.relative_paths}
                != set(plan.write_policy.allowed_object_keys)
            or len(plan.selection.relative_paths) > definition.max_objects_per_page
            or (previous is not None and plan != previous)):
            raise IngestionNotApproved("当前来源批准或精确清单已变化")
        return plan
    except Exception:
        # 配置错误可能包含路径或提供者秘密；不传播原异常/超时详情。
        raise IngestionNotApproved("无法确认当前来源批准、精确清单与固定范围") from None


def _candidates(batch: LocalMarkdownBatch, definition: IngestionJobDefinition,
    plan: ApprovedLocalMarkdownSource,
) -> tuple[IngestionDraftCandidate, ...]:
    if (batch.scope_digest != ingestion_scope_digest(definition.scope)
        or batch.selection_digest != definition.scope.source_contract_digest
        or len(batch.objects) != len(plan.write_policy.allowed_object_keys)
        or {item.object_key for item in batch.objects} != set(plan.write_policy.allowed_object_keys)):
        raise LocalMarkdownError("MARKDOWN_READER_RESPONSE_INVALID")
    candidates = []
    for item in batch.objects:
        # 标题只取同一不可信正文的首个章节；无章节时用中性标题，不泄露文件名/路径。
        title = (item.section_anchors[0].title.strip() if item.section_anchors else "") or "导入知识（待复核）"
        if len(title) > 500:
            raise LocalMarkdownError("MARKDOWN_TITLE_INVALID")
        candidates.append(IngestionDraftCandidate(object_key=item.object_key,
            raw_content_digest=item.raw_content_digest, parsed_content_digest=item.parsed_content_digest,
            title=title, content_markdown=item.content_markdown))
    return tuple(candidates)


def _read_failure(error: LocalMarkdownError) -> IngestionFailure:
    if error.code == "MARKDOWN_PROGRAM_LIMIT_REACHED":
        return "rate_limited"
    if error.code == "MARKDOWN_MANIFEST_MISMATCH":
        return "source_unapproved"
    if error.code in {"MARKDOWN_SIZE_EXCEEDED", "MARKDOWN_ANCHOR_LIMIT_EXCEEDED",
        "MARKDOWN_READ_OUTPUT_TOO_LARGE", "MARKDOWN_PARSE_OUTPUT_TOO_LARGE"}:
        return "output_too_large"
    if error.code == "MARKDOWN_SOURCE_CHANGED":
        return "source_version_conflict"
    if error.code in {"MARKDOWN_READER_UNAVAILABLE", "MARKDOWN_PARSER_UNAVAILABLE", "MARKDOWN_READ_TIMEOUT", "MARKDOWN_READ_FAILED"}:
        return "source_unavailable"
    return "parse_rejected"


def _execution_phase_seconds(definition: IngestionJobDefinition, state: IngestionJobState,
    plan: ApprovedLocalMarkdownSource | None, *, maximum: float = INGESTION_TRANSACTION_TIMEOUT_SECONDS,
) -> float:
    """不延长原许可；关闭失效来源许可时可不采用旧批准期限。"""
    if state.lease_expires_at is None:
        raise IngestionConflict("当前执行没有有效租约")
    deadline = min(state.lease_expires_at, definition.deadline_at)
    if plan is not None:
        deadline = min(deadline, plan.write_policy.valid_until)
    seconds = min(maximum, (deadline - utc_now()).total_seconds())
    if seconds <= 0:
        raise IngestionConflict("当前执行或来源批准已到期")
    return seconds


async def _record_failure(factory: async_sessionmaker[AsyncSession], *, identity: IdentityContext,
    definition: IngestionJobDefinition, state: IngestionJobState, owner_id: UUID,
    provider: LocalMarkdownApprovalProvider, plan: ApprovedLocalMarkdownSource,
    failure: IngestionFailure, correlation_id: str,
) -> bool:
    try:
        try:
            await _approved_plan(provider, definition, previous=plan)
        except IngestionNotApproved:
            failure = "source_unapproved"
        stopping = failure in {"source_unapproved", "authorization_changed"}
        # 来源批准失效时仍可关闭本人当前执行，不要求旧批准继续有效。
        async with (
            asyncio.timeout(_execution_phase_seconds(definition, state, None if stopping else plan)),
            factory() as session, session.begin(),
        ):
            if stopping:
                await invalidate_ingestion_execution(session, identity=identity, job_id=definition.id,
                    owner_id=owner_id, generation=state.generation, expected_revision=state.revision,
                    failure_code="authorization_changed" if failure == "authorization_changed" else "source_unapproved",
                    correlation_id=correlation_id)
            else:
                await fail_ingestion_job(session, identity=identity, job_id=definition.id,
                    owner_id=owner_id, generation=state.generation, expected_revision=state.revision,
                    policy=plan.write_policy, failure_code=failure, correlation_id=correlation_id)
        return True
    except Exception:
        # 身份/租约/围栏变化或数据库不可用时不能伪造failed；保留数据库事实供本人查询/超时收敛。
        return False


async def execute_local_markdown_ingestion(factory: async_sessionmaker[AsyncSession], *,
    identity: IdentityContext, job_id: UUID, expected_revision: int,
    approval_provider: LocalMarkdownApprovalProvider | None = None,
) -> IngestionJobView:
    """只执行已存在的本人任务一次；不创建任务、不循环重试、不调度或发布。"""
    if approval_provider is None:
        raise IngestionNotApproved("未配置可信来源批准提供者，不允许读取来源")
    try:
        async with (
            asyncio.timeout(INGESTION_TRANSACTION_TIMEOUT_SECONDS), factory() as session, session.begin(),
        ):
            row = await get_ingestion_job(session, identity=identity, job_id=job_id)
            definition, initial_state = _job_contracts(row)
            if initial_state.status not in {"queued", "retry_wait"}:
                raise IngestionConflict("仅排队或明确等待重试的任务允许手动执行；处理中或未知结果请先查询")
    except (IngestionConflict, PermissionDeniedError, AuthenticationError, ResourceNotFoundError):
        raise
    except Exception:
        raise IngestionConflict("无法确认本人导入任务，尚未启动来源读取") from None
    plan = await _approved_plan(approval_provider, definition)
    owner_id, correlation_id = uuid4(), f"ingestion-exec:{uuid4()}"
    try:
        async with (
            asyncio.timeout(INGESTION_TRANSACTION_TIMEOUT_SECONDS), factory() as session, session.begin(),
        ):
            row = await claim_ingestion_job(session, identity=identity, job_id=job_id, owner_id=owner_id,
                expected_revision=expected_revision, policy=plan.write_policy, correlation_id=correlation_id)
            _, state = _job_contracts(row)
    except (IngestionConflict, PermissionDeniedError, AuthenticationError, ResourceNotFoundError):
        raise
    except Exception:
        # 包括commit连接丢失；不从失败响应推断未领取，也不接着读取或尝试再次领取。
        raise IngestionExecutionUncertain("领取结果无法确认，未读取来源；请先查询原任务") from None
    try:
        await _approved_plan(approval_provider, definition, previous=plan)
        async with (
            asyncio.timeout(_execution_phase_seconds(definition, state, plan)), factory() as session, session.begin(),
        ):
            state, target_digest = await check_ingestion_execution(session, identity=identity, job_id=job_id,
                owner_id=owner_id, generation=state.generation, expected_revision=state.revision,
                policy=plan.write_policy)
        # 来源IO期间没有活跃数据库会话/事务；不占用用户、来源、任务或文档锁。
        seconds = _execution_phase_seconds(definition, state, plan, maximum=5.0)
        batch = await preview_local_markdown(selection=plan.selection, scope=definition.scope,
            expected_selection_digest=definition.scope.source_contract_digest, timeout_seconds=seconds)
        candidates = _candidates(batch, definition, plan)
        await _approved_plan(approval_provider, definition, previous=plan)
    except IngestionNotApproved:
        recorded = await _record_failure(factory, identity=identity, definition=definition, state=state, owner_id=owner_id,
            provider=approval_provider, plan=plan, failure="source_unapproved", correlation_id=correlation_id)
        raise IngestionNotApproved("来源许可无法确认，未提交知识；" +
            ("已记录处理结果，请查询原任务" if recorded else "处理状态未确认，请查询原任务")) from None
    except LocalMarkdownError as error:
        recorded = await _record_failure(factory, identity=identity, definition=definition, state=state, owner_id=owner_id,
            provider=approval_provider, plan=plan, failure=_read_failure(error), correlation_id=correlation_id)
        raise IngestionConflict("来源读取或解析失败，未提交知识；" +
            ("已记录处理结果，请查询原任务" if recorded else "处理状态未确认，请查询原任务")) from None
    except (IngestionConflict, PermissionDeniedError, AuthenticationError, ResourceNotFoundError):
        raise
    except Exception:
        # 当前身份/取消/围栏错误、未知解析或DB错误一律停止，不覆盖可能更新的任务事实。
        raise IngestionConflict("执行许可或候选无法确认，未提交知识；请查询原任务状态") from None
    try:
        async with (
            asyncio.timeout(_execution_phase_seconds(definition, state, plan)), factory() as session, session.begin(),
        ):
            await _approved_plan(approval_provider, definition, previous=plan)
            await commit_ingestion_page(session, identity=identity, job_id=job_id,
                owner_id=owner_id, generation=state.generation, expected_revision=state.revision,
                candidates=candidates, expected_target_digest=target_digest,
                policy=plan.write_policy, correlation_id=correlation_id)
            await _approved_plan(approval_provider, definition, previous=plan)
            row = await get_ingestion_job(session, identity=identity, job_id=job_id)
            # 最后一次批准查询也消耗时间；不让其跨过原始执行/批准窗口后提交草稿。
            if (state.lease_expires_at is None or min(state.lease_expires_at,
                definition.deadline_at, plan.write_policy.valid_until) <= utc_now()):
                raise IngestionConflict("提交前原始执行或来源批准已到期")
            result = ingestion_job_view(row)
            if result.status != "completed":
                raise IngestionConflict("提交结果完整性无法确认")
    except IngestionTargetChanged:
        recorded = await _record_failure(factory, identity=identity, definition=definition, state=state, owner_id=owner_id,
            provider=approval_provider, plan=plan, failure="commit_conflict", correlation_id=correlation_id)
        raise IngestionConflict("来源读取期间目标版本或治理范围已变化，本页未生成草稿；" +
            ("已记录处理结果，请查询原任务" if recorded else "处理状态未确认，请查询原任务")) from None
    except Exception:
        # 整页提交可能已经发生；不记录失败、不重新读文件或重放提交，只允许查询原账本。
        raise IngestionExecutionUncertain("页面提交结果无法确认；请查询原任务和页回执，勿重放执行") from None
    return result
