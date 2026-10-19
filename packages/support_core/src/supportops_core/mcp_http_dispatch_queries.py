"""本人原派发的只读投影；查询不取密、不联网，也不产生执行许可。"""

from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from supportops_core.models import AgentRun, Conversation, HttpMcpDispatch
from supportops_core.services import ResourceNotFoundError, get_run_for_user


class HttpMcpDispatchSummary(BaseModel):
    """只包含固定引用及本地生命周期，不代表远端业务状态。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: UUID = Field(description="原派发标识，不构成再次执行许可。")
    run_id: UUID = Field(description="本人会话的原 Run。")
    attempt_id: UUID = Field(description="原执行尝试。")
    invocation_id: UUID = Field(description="原工具调用许可。")
    source_record_id: UUID = Field(description="原固定来源修订，不表示当前来源有效。")
    generation: int = Field(ge=1, description="原执行围栏代次。")
    revision: int = Field(ge=1, description="派发状态修订。")
    status: Literal["authorized", "claimed", "bound", "blocked", "revoked"] = Field(
        description="本地授权/领取/凭据绑定/阻止/已确认撤销状态；不是远端执行结果。"
    )
    claimed_at: datetime | None = Field(description="取密前提交领取的时间，不证明已取得令牌。")
    revoked_at: datetime | None = Field(description="提供者已确认的真实撤销时间。")
    created_at: datetime = Field(description="原派发保存时间。")
    expires_at: datetime = Field(description="原派发截止时间，不自动延长。")
    updated_at: datetime = Field(description="最近本地状态更新时间。")
    observed_at: datetime = Field(description="本次数据库元数据查询时间，不是远端观察时间。")
    is_expired: bool = Field(description="查询时是否超过派发截止；过期不等于已撤销。")
    business_status_confirmed: Literal[False] = Field(default=False, description="本查询不确认业务结果。")
    execution_permit: Literal[False] = Field(default=False, description="本查询不授予执行或重发许可。")


def _scope(*, tenant_id: UUID, user_id: UUID, run_id: UUID):
    # 明确选择列，禁止ORM整行加载request_payload/token_state或其摘要/lease/领取nonce。
    return (
        select(
            HttpMcpDispatch.id, HttpMcpDispatch.run_id, HttpMcpDispatch.attempt_id,
            HttpMcpDispatch.invocation_id, HttpMcpDispatch.source_record_id,
            HttpMcpDispatch.generation, HttpMcpDispatch.revision, HttpMcpDispatch.status,
            HttpMcpDispatch.claimed_at, HttpMcpDispatch.revoked_at, HttpMcpDispatch.created_at,
            HttpMcpDispatch.expires_at, HttpMcpDispatch.updated_at,
        )
        .join(AgentRun, AgentRun.id == HttpMcpDispatch.run_id)
        .join(Conversation, Conversation.id == AgentRun.conversation_id)
        .where(
            HttpMcpDispatch.tenant_id == tenant_id, HttpMcpDispatch.actor_user_id == user_id,
            HttpMcpDispatch.run_id == run_id, AgentRun.tenant_id == tenant_id,
            Conversation.tenant_id == tenant_id, Conversation.user_id == user_id,
        )
    )


async def list_http_mcp_dispatches(
    session: AsyncSession, *, tenant_id: UUID, user_id: UUID, run_id: UUID,
    page: int, page_size: int,
) -> tuple[list[HttpMcpDispatchSummary], int]:
    """历史只读查询沿用本人Run边界，不要求原执行租约/Agent授权仍有效。"""
    if page < 1 or not 1 <= page_size <= 100:
        raise ValueError("派发查询分页范围无效")
    await get_run_for_user(session, run_id=run_id, tenant_id=tenant_id, user_id=user_id)
    query = _scope(tenant_id=tenant_id, user_id=user_id, run_id=run_id)
    total = int(await session.scalar(select(func.count()).select_from(query.subquery())) or 0)
    observed_at = datetime.now(UTC)
    rows = (await session.execute(
        query.add_columns((HttpMcpDispatch.expires_at <= observed_at).label("is_expired"))
        .order_by(HttpMcpDispatch.created_at.desc(), HttpMcpDispatch.id.desc())
        .offset((page - 1) * page_size).limit(page_size)
    )).mappings().all()
    return [HttpMcpDispatchSummary.model_validate({**row, "observed_at": observed_at}) for row in rows], total


async def read_http_mcp_dispatch(
    session: AsyncSession, *, tenant_id: UUID, user_id: UUID, run_id: UUID, dispatch_id: UUID,
) -> HttpMcpDispatchSummary:
    """单项详情与列表使用相同投影和主体范围，不允许按派发ID跨Run读取。"""
    await get_run_for_user(session, run_id=run_id, tenant_id=tenant_id, user_id=user_id)
    observed_at = datetime.now(UTC)
    row = (await session.execute(
        _scope(tenant_id=tenant_id, user_id=user_id, run_id=run_id)
        .add_columns((HttpMcpDispatch.expires_at <= observed_at).label("is_expired"))
        .where(HttpMcpDispatch.id == dispatch_id)
    )).mappings().one_or_none()
    if row is None:
        raise ResourceNotFoundError("派发记录不存在")
    return HttpMcpDispatchSummary.model_validate({**row, "observed_at": observed_at})
