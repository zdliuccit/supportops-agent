"""把不可变 Agent 快照映射为受限的 LangChain/LangGraph 运行。"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from urllib.parse import quote, urlsplit, urlunsplit

from langchain.agents import create_agent
from langchain.agents.middleware import ModelCallLimitMiddleware, ToolCallLimitMiddleware
from langchain.agents.structured_output import ProviderStrategy, ToolStrategy
from langchain.tools import ToolRuntime, tool
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg import Error as PsycopgError
from psycopg_pool import PoolTimeout
from pydantic import SecretStr, ValidationError

from supportops_core.agent_config import (
    TOOL_REGISTRY,
    AgentConfigV2,
    SupportAnswer,
    SupportContext,
)
from supportops_core.config import Settings
from supportops_core.enums import ModelApiProtocol, ResponseStrategy
from supportops_core.model_config import (
    resolve_and_validate_endpoint,
    validate_endpoint_url_policy,
)
from supportops_core.models import AgentVersion, ModelEndpointVersion

_REASONING_EFFORTS = frozenset({"none", "low", "medium", "high", "xhigh", "max"})
_VERBOSITY_LEVELS = frozenset({"low", "medium", "high"})
_MODEL_CLIENT_USER_AGENT = "supportops-agent"


class AgentRuntimeConfigurationError(RuntimeError):
    pass


class AgentReferenceError(AgentRuntimeConfigurationError):
    """Run 固定的版本、消息或用户引用不存在或不一致。"""


class AgentSecretResolutionError(AgentRuntimeConfigurationError):
    """固定凭据 revision 不存在，或 Secret Provider 无法解析。"""


class AgentCapabilityError(AgentRuntimeConfigurationError):
    """固定模型版本不满足已发布 Agent 的运行能力要求。"""


class AgentStructuredOutputError(AgentRuntimeConfigurationError):
    """模型结果无法校验为平台约定的 SupportAnswer。"""


class AgentCheckpointError(AgentRuntimeConfigurationError):
    """LangGraph checkpoint 无法建立、读取或持久化。"""


@dataclass(frozen=True, slots=True)
class AgentRuntimeSnapshot:
    """Worker 执行一次 Run 所需的全部固定版本和可信输入。"""

    agent_version: AgentVersion
    model_version: ModelEndpointVersion
    config: AgentConfigV2
    api_key: str
    context: SupportContext
    input_text: str
    product_messages: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class AgentExecutionResult:
    """Agent 回答以及可用于 Dashboard 的脱敏运行摘要。"""

    answer: SupportAnswer
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_input_tokens: int | None = None
    reasoning_tokens: int | None = None
    model_call_count: int | None = None
    tool_call_count: int | None = None
    retry_count: int | None = None
    finish_reason: str | None = None
    provider_request_id: str | None = None


class ModelAdapter:
    """将官方或兼容模型端点版本转换为统一 ChatOpenAI 客户端。"""

    @staticmethod
    async def build(
        snapshot: AgentRuntimeSnapshot,
        *,
        allow_private_networks: bool,
        allowed_hosts: list[str],
    ) -> BaseChatModel:
        version = snapshot.model_version
        validate_endpoint_url_policy(
            version.base_url,
            allow_private_networks=allow_private_networks,
            allowed_hosts=allowed_hosts,
        )
        await resolve_and_validate_endpoint(
            version.base_url, allow_private_networks=allow_private_networks
        )
        generation = snapshot.config.model.generation
        request_metadata = version.request_metadata
        raw_extension_options = version.defaults.get("extension_options", {})
        extension_options = (
            dict(raw_extension_options) if isinstance(raw_extension_options, dict) else {}
        )
        defaults = version.defaults
        reasoning_effort: str | None = generation.reasoning_effort
        if reasoning_effort is None:
            candidate = defaults.get("reasoning_effort")
            reasoning_effort = (
                candidate
                if isinstance(candidate, str) and candidate in _REASONING_EFFORTS
                else None
            )
        verbosity: str | None = generation.verbosity
        if verbosity is None:
            candidate = defaults.get("verbosity")
            verbosity = (
                candidate if isinstance(candidate, str) and candidate in _VERBOSITY_LEVELS else None
            )
        if version.api_protocol != ModelApiProtocol.RESPONSES:
            verbosity = None
        return ChatOpenAI(
            model=version.remote_model_name,
            api_key=SecretStr(snapshot.api_key),
            base_url=version.base_url,
            organization=request_metadata.get("organization_id"),
            temperature=generation.temperature,
            max_completion_tokens=generation.max_output_tokens,
            timeout=generation.timeout_seconds,
            max_retries=generation.max_retries,
            reasoning_effort=reasoning_effort,
            verbosity=verbosity,
            default_headers={"User-Agent": _MODEL_CLIENT_USER_AGENT},
            # 自定义供应商参数必须放进 extra_body；model_kwargs 会把未知字段
            # 提升为 OpenAI 客户端的顶层参数，导致 SDK 直接拒绝请求。
            extra_body=extension_options,
            streaming=bool(version.capabilities.get("streaming", False)),
            use_responses_api=version.api_protocol == ModelApiProtocol.RESPONSES,
        )


@tool
def support_ticket_lookup(ticket_id: str, runtime: ToolRuntime[SupportContext]) -> str:
    """查询当前租户中的支持工单摘要。"""
    return (
        f"租户 {runtime.context.tenant_id} 中未找到工单 {ticket_id}。当前版本尚未接入外部工单系统。"
    )


@tool
def current_identity_summary(runtime: ToolRuntime[SupportContext]) -> str:
    """返回由服务端注入的当前身份摘要。"""
    context = runtime.context
    return (
        f"当前用户 {context.user_id} 属于租户 {context.tenant_id}，"
        f"可信角色：{', '.join(context.roles) or '无'}。"
    )


RUNTIME_TOOLS: dict[str, BaseTool] = {
    "support_ticket_lookup": support_ticket_lookup,
    "current_identity_summary": current_identity_summary,
}


def filter_runtime_tools(snapshot: AgentRuntimeSnapshot) -> list[BaseTool]:
    """按发布绑定和可信角色取交集，绝不暴露未注册工具。"""
    roles = frozenset(snapshot.context.roles)
    tools: list[BaseTool] = []
    for binding in snapshot.config.tools:
        if not binding.enabled or binding.tool_id not in snapshot.agent_version.resolved_tool_ids:
            continue
        definition = TOOL_REGISTRY.get(binding.tool_id)
        runtime_tool = RUNTIME_TOOLS.get(binding.tool_id)
        if definition is None or runtime_tool is None:
            raise AgentRuntimeConfigurationError("已发布 Agent 引用了未知工具")
        if definition.required_roles and definition.required_roles.isdisjoint(roles):
            continue
        tools.append(runtime_tool)
    return tools


def validate_runtime_capabilities(snapshot: AgentRuntimeSnapshot) -> None:
    """再次校验不可变快照，防止历史脏数据绕过发布期校验。"""
    capabilities = snapshot.model_version.capabilities
    has_enabled_tools = any(binding.enabled for binding in snapshot.config.tools)
    if snapshot.config.runtime.response_strategy == ResponseStrategy.TOOL and not capabilities.get(
        "tool_calling", False
    ):
        raise AgentCapabilityError("ToolStrategy 需要固定模型版本支持工具调用")
    if has_enabled_tools and not capabilities.get("tool_calling", False):
        raise AgentCapabilityError("Agent 工具绑定需要固定模型版本支持工具调用")
    if (
        snapshot.config.runtime.response_strategy == ResponseStrategy.PROVIDER
        and not capabilities.get("structured_output", False)
    ):
        raise AgentCapabilityError("ProviderStrategy 需要固定模型版本支持结构化输出")


class AgentFactory:
    """集中维护 create_agent 模板，禁止草稿配置任意构造运行图。"""

    @staticmethod
    def build(
        snapshot: AgentRuntimeSnapshot,
        *,
        model: BaseChatModel,
        checkpointer: BaseCheckpointSaver[Any],
        tools: Sequence[BaseTool],
    ) -> Any:
        runtime = snapshot.config.runtime
        response_format: object
        if runtime.response_strategy == ResponseStrategy.PROVIDER:
            response_format = ProviderStrategy(SupportAnswer, strict=True)
        else:
            response_format = ToolStrategy(SupportAnswer)
        return create_agent(
            model=model,
            tools=tools,
            system_prompt=snapshot.config.prompt.system_prompt,
            context_schema=SupportContext,
            checkpointer=checkpointer,
            response_format=response_format,
            middleware=cast(
                Sequence[Any],
                [
                    ModelCallLimitMiddleware(
                        run_limit=runtime.model_call_limit,
                        exit_behavior="end",
                    ),
                    ToolCallLimitMiddleware(
                        run_limit=runtime.tool_call_limit,
                        exit_behavior="continue",
                    ),
                ],
            ),
            name="technical_support_agent",
        )


def checkpoint_thread_id(context: SupportContext) -> str:
    """生成同时包含租户与会话边界的 LangGraph thread ID。"""
    return f"tenant:{context.tenant_id}:conversation:{context.conversation_id}"


def checkpoint_input_messages(
    snapshot: AgentRuntimeSnapshot,
    *,
    has_checkpoint: bool,
) -> list[dict[str, str]]:
    """无 checkpoint 时从产品消息事实源重建上下文，否则只追加本轮输入。"""
    if has_checkpoint or not snapshot.product_messages:
        return [{"role": "user", "content": snapshot.input_text}]
    return [
        {"role": role, "content": content}
        for role, content in snapshot.product_messages
        if role in {"user", "assistant"}
    ]


def _psycopg_url(database_url: str, schema: str) -> str:
    """把应用异步连接串转换为 psycopg 且固定 checkpoint search_path。"""
    normalized = database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    parsed = urlsplit(normalized)
    query = parsed.query
    search_path = quote(f"-csearch_path={schema},public", safe="")
    query = f"{query}&options={search_path}" if query else f"options={search_path}"
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, query, parsed.fragment))


@asynccontextmanager
async def checkpoint_saver(settings: Settings) -> AsyncIterator[BaseCheckpointSaver[Any]]:
    """生产返回 PostgreSQL saver；非 PostgreSQL 测试环境使用内存实现。"""
    if not settings.database_url.startswith("postgresql"):
        yield InMemorySaver()
        return
    connection_url = _psycopg_url(
        settings.database_url,
        settings.langgraph_checkpoint_schema,
    )
    async with AsyncPostgresSaver.from_conn_string(connection_url) as saver:
        await saver.setup()
        yield cast(BaseCheckpointSaver[Any], saver)


async def discard_checkpoint_thread(
    settings: Settings,
    *,
    context: SupportContext,
) -> None:
    """删除未能提交到产品消息事实源的会话内部状态，避免下轮读取脏 checkpoint。"""
    if not settings.database_url.startswith("postgresql"):
        return
    async with checkpoint_saver(settings) as raw_saver:
        saver = cast(AsyncPostgresSaver, raw_saver)
        await saver.adelete_thread(checkpoint_thread_id(context))


async def prune_expired_checkpoints(
    settings: Settings,
    *,
    now: datetime | None = None,
) -> int:
    """按会话最后 checkpoint 时间清理过期内部状态，产品消息不受影响。"""
    if not settings.database_url.startswith("postgresql"):
        return 0
    cutoff = (now or datetime.now(UTC)) - timedelta(
        days=settings.langgraph_checkpoint_retention_days
    )
    async with checkpoint_saver(settings) as raw_saver:
        saver = cast(AsyncPostgresSaver, raw_saver)
        cursor = await cast(Any, saver.conn).execute(
            """
            SELECT thread_id
            FROM checkpoints
            GROUP BY thread_id
            HAVING MAX(NULLIF(checkpoint->>'ts', '')::timestamptz) < %s
            """,
            (cutoff,),
        )
        rows = await cursor.fetchall()
        thread_ids = [str(row["thread_id"]) for row in rows]
        for thread_id in thread_ids:
            await saver.adelete_thread(thread_id)
        return len(thread_ids)


def parse_structured_answer(result: dict[str, Any]) -> SupportAnswer:
    """把图结果严格收敛为 support_answer_v1。"""
    structured = result.get("structured_response")
    if isinstance(structured, SupportAnswer):
        return structured
    if isinstance(structured, dict):
        try:
            return SupportAnswer.model_validate(structured)
        except ValidationError as exc:
            raise AgentStructuredOutputError("模型返回的 support_answer_v1 校验失败") from exc
    raise AgentStructuredOutputError("模型未返回 support_answer_v1 结构")


async def invoke_agent(
    snapshot: AgentRuntimeSnapshot,
    *,
    settings: Settings,
    on_stage: Callable[[str], None] | None = None,
) -> AgentExecutionResult:
    """在预算、能力和 checkpoint 边界内执行一次 Agent Run。"""
    enforce_run_budgets(snapshot)
    validate_runtime_capabilities(snapshot)
    model = await ModelAdapter.build(
        snapshot,
        allow_private_networks=settings.model_endpoint_allow_private_networks,
        allowed_hosts=settings.model_endpoint_allowed_hosts,
    )
    tools = filter_runtime_tools(snapshot)
    if on_stage is not None:
        on_stage("agent.created")
    try:
        async with checkpoint_saver(settings) as saver:
            graph = AgentFactory.build(
                snapshot,
                model=model,
                checkpointer=saver,
                tools=tools,
            )
            checkpoint_config = {
                "configurable": {"thread_id": checkpoint_thread_id(snapshot.context)},
            }
            has_checkpoint = (
                await saver.aget_tuple(cast(RunnableConfig, checkpoint_config)) is not None
            )
            result = await graph.ainvoke(
                {
                    "messages": checkpoint_input_messages(
                        snapshot,
                        has_checkpoint=has_checkpoint,
                    )
                },
                config={
                    **checkpoint_config,
                    "max_concurrency": snapshot.config.runtime.max_parallel_tools,
                },
                context=snapshot.context,
            )
    except (PsycopgError, PoolTimeout) as exc:
        raise AgentCheckpointError("Agent checkpoint 读写失败") from exc
    if not isinstance(result, dict):
        raise AgentStructuredOutputError("Agent 返回结果类型无效")
    answer = parse_structured_answer(result)
    messages = result.get("messages", [])
    if not isinstance(messages, list):
        messages = []
    model_messages = [item for item in messages if getattr(item, "type", None) == "ai"]
    tool_messages = [item for item in messages if getattr(item, "type", None) == "tool"]
    input_tokens = output_tokens = cached_input_tokens = reasoning_tokens = None
    finish_reason = provider_request_id = None
    for message in model_messages:
        usage = getattr(message, "usage_metadata", None)
        if isinstance(usage, dict):
            input_tokens = (input_tokens or 0) + int(usage.get("input_tokens", 0))
            output_tokens = (output_tokens or 0) + int(usage.get("output_tokens", 0))
            details = usage.get("input_token_details")
            if isinstance(details, dict) and details.get("cache_read") is not None:
                cached_input_tokens = (cached_input_tokens or 0) + int(details["cache_read"])
            output_details = usage.get("output_token_details")
            if isinstance(output_details, dict) and output_details.get("reasoning") is not None:
                reasoning_tokens = (reasoning_tokens or 0) + int(output_details["reasoning"])
        metadata = getattr(message, "response_metadata", None)
        if isinstance(metadata, dict):
            finish_reason = finish_reason or metadata.get("finish_reason")
            provider_request_id = provider_request_id or metadata.get("id")
    return AgentExecutionResult(
        answer=answer,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_input_tokens=cached_input_tokens,
        reasoning_tokens=reasoning_tokens,
        model_call_count=len(model_messages) or None,
        tool_call_count=len(tool_messages) or None,
        finish_reason=str(finish_reason) if finish_reason is not None else None,
        provider_request_id=str(provider_request_id) if provider_request_id is not None else None,
    )


def enforce_run_budgets(snapshot: AgentRuntimeSnapshot) -> None:
    """在出站调用前执行可确定的 token 与成本上限，避免只依赖 Prompt。"""
    runtime = snapshot.config.runtime
    estimated_input_tokens = max(1, (len(snapshot.input_text.encode("utf-8")) + 3) // 4)
    if runtime.max_input_tokens is not None and estimated_input_tokens > runtime.max_input_tokens:
        raise AgentRuntimeConfigurationError("输入超过 Agent 的 token 上限")
    if runtime.max_cost_usd is None:
        return
    pricing = snapshot.model_version.pricing
    input_price = pricing.get("input_per_million_tokens")
    output_price = pricing.get("output_per_million_tokens")
    if not isinstance(input_price, int | float) or not isinstance(output_price, int | float):
        raise AgentRuntimeConfigurationError("模型缺少成本预算所需的定价信息")
    upper_bound = (
        estimated_input_tokens * float(input_price)
        + snapshot.config.model.generation.max_output_tokens * float(output_price)
    ) / 1_000_000
    if upper_bound > runtime.max_cost_usd:
        raise AgentRuntimeConfigurationError("本次运行的成本上界超过 Agent 预算")
