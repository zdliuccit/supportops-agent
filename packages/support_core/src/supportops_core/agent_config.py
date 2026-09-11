"""Agent Schema v2、可信运行上下文和结构化回答契约。"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from supportops_core.enums import ApprovalPolicy, ResponseStrategy

AGENT_CONFIG_SCHEMA_VERSION = "2"
AGENT_RUNTIME_ENGINE = "langchain_create_agent_v1"
SUPPORT_CONTEXT_SCHEMA = "support_context_v1"
SUPPORT_RESPONSE_SCHEMA = "support_answer_v1"

SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"(?i)\b(?:api[_-]?key|authorization|bearer)\s*[:=]\s*\S+"),
)


class StrictModel(BaseModel):
    """拒绝未知字段的配置基类，防止 Worker 静默接受未建模行为。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class AgentPromptConfig(StrictModel):
    """进入每轮 Agent 调用的稳定系统指令。"""

    system_prompt: str = Field(min_length=1, max_length=50_000)

    @field_validator("system_prompt")
    @classmethod
    def reject_secrets(cls, value: str) -> str:
        if any(pattern.search(value) for pattern in SECRET_PATTERNS):
            raise ValueError("system prompt 不能包含疑似凭据")
        return value


class GenerationConfig(StrictModel):
    """管理员可在平台边界内调整的模型生成参数。"""

    temperature: float = Field(default=0.2, ge=0, le=2)
    max_output_tokens: int = Field(default=4096, ge=1, le=128_000)
    reasoning_effort: Literal["none", "low", "medium", "high", "xhigh", "max"] | None = Field(
        default=None,
        description="推理强度；仅在固定模型支持时发送。",
    )
    verbosity: Literal["low", "medium", "high"] | None = Field(
        default=None,
        description="回答详细程度；仅在固定协议支持时发送。",
    )
    timeout_seconds: int = Field(default=60, ge=1, le=600)
    max_retries: int = Field(default=2, ge=0, le=10)


class AgentModelBinding(StrictModel):
    """发布时解析并固定具体模型端点版本的绑定。"""

    model_endpoint_id: UUID
    model_endpoint_model_id: UUID | None = None
    fallback_model_endpoint_ids: list[UUID] = Field(default_factory=list, max_length=0)
    generation: GenerationConfig = Field(default_factory=GenerationConfig)


class AgentToolBinding(StrictModel):
    """从受控 Tool Registry 选择的单项工具配置。"""

    tool_id: str = Field(pattern=r"^[a-z][a-z0-9_.-]{1,99}$")
    enabled: bool = True
    max_calls_per_run: int = Field(default=4, ge=1, le=20)
    approval_policy: ApprovalPolicy = ApprovalPolicy.NONE


class AgentRuntimeConfig(StrictModel):
    """模型、工具、并行、超时、Token 和成本的硬运行边界。"""

    engine: Literal["langchain_create_agent_v1"] = "langchain_create_agent_v1"
    context_schema: Literal["support_context_v1"] = "support_context_v1"
    response_schema: Literal["support_answer_v1"] = "support_answer_v1"
    response_strategy: ResponseStrategy = ResponseStrategy.TOOL
    checkpointer: Literal["postgres"] = "postgres"
    model_call_limit: int = Field(default=6, ge=1, le=12)
    tool_call_limit: int = Field(default=8, ge=1, le=20)
    run_timeout_seconds: int = Field(default=120, ge=10, le=600)
    max_parallel_tools: int = Field(default=2, ge=1, le=8)
    max_input_tokens: int | None = Field(default=None, ge=256, le=2_000_000)
    max_cost_usd: float | None = Field(default=None, gt=0, le=10_000)


class AgentKnowledgeConfig(StrictModel):
    """后续知识检索能力的版本化接入点；首版保持为空。"""

    knowledge_base_ids: list[UUID] = Field(default_factory=list, max_length=0)


class AgentMemoryConfig(StrictModel):
    """后续长期记忆策略的版本化接入点；首版保持为空。"""

    long_term_policy_id: None = None


class AgentConfigV2(StrictModel):
    """可规范化、摘要、发布和复现的完整 Agent 配置。"""

    schema_version: Literal["2"] = "2"
    prompt: AgentPromptConfig
    model: AgentModelBinding
    tools: list[AgentToolBinding] = Field(default_factory=list, max_length=30)
    runtime: AgentRuntimeConfig = Field(default_factory=AgentRuntimeConfig)
    knowledge: AgentKnowledgeConfig = Field(default_factory=AgentKnowledgeConfig)
    memory: AgentMemoryConfig = Field(default_factory=AgentMemoryConfig)

    @model_validator(mode="after")
    def unique_tools(self) -> AgentConfigV2:
        tool_ids = [binding.tool_id for binding in self.tools]
        if len(tool_ids) != len(set(tool_ids)):
            raise ValueError("工具绑定不能重复")
        return self


class SupportCitation(StrictModel):
    """结构化回答中的安全引用。"""

    title: str = Field(min_length=1, max_length=500)
    url: str | None = Field(default=None, max_length=2000)


class SupportAnswer(StrictModel):
    """所有模型供应商最终必须满足的统一回答结构。"""

    answer: str = Field(min_length=1, max_length=100_000)
    summary: str | None = Field(default=None, max_length=1000)
    citations: list[SupportCitation] = Field(default_factory=list, max_length=50)
    suggested_actions: list[str] = Field(default_factory=list, max_length=20)
    escalation_recommended: bool = False


@dataclass(frozen=True, slots=True)
class SupportContext:
    """由服务端注入工具运行时、模型不可伪造的可信身份上下文。"""

    tenant_id: UUID
    user_id: UUID
    roles: tuple[str, ...]
    conversation_id: UUID
    run_id: UUID
    correlation_id: str


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    """平台注册工具的静态能力和授权元数据。"""

    id: str
    name: str
    description: str
    required_roles: frozenset[str] = frozenset()
    risk_level: Literal["low", "medium", "high"] = "low"
    version: int = 1


TOOL_REGISTRY: dict[str, ToolDefinition] = {
    "support_ticket_lookup": ToolDefinition(
        id="support_ticket_lookup",
        name="查询支持工单",
        description="根据工单编号读取当前租户内的工单摘要。",
        required_roles=frozenset({"employee", "agent_user", "platform_admin"}),
    ),
    "current_identity_summary": ToolDefinition(
        id="current_identity_summary",
        name="读取当前身份摘要",
        description="读取当前请求的租户、用户和角色摘要，不访问外部系统。",
        required_roles=frozenset(),
    ),
}


def normalize_agent_config(config: AgentConfigV2) -> dict[str, object]:
    """生成适合 JSONB 保存和稳定摘要计算的规范化配置。"""
    return config.model_dump(mode="json", exclude_none=False)


def agent_config_digest(config: AgentConfigV2) -> str:
    """计算跨进程稳定的规范化配置 SHA-256 摘要。"""
    encoded = json.dumps(
        normalize_agent_config(config),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def parse_agent_config(value: object) -> AgentConfigV2:
    """严格解析任意持久化值，未知字段或 Schema 会被拒绝。"""
    return AgentConfigV2.model_validate(value)


def default_agent_config(model_endpoint_id: UUID) -> AgentConfigV2:
    """为新 Agent 创建安全、最小且可发布的初始草稿。"""
    return AgentConfigV2(
        prompt=AgentPromptConfig(
            system_prompt=(
                "你是企业 AI/API 技术支持 Agent。基于已提供的事实回答；"
                "信息不足时明确说明，不得编造工单、日志或配置。"
            )
        ),
        model=AgentModelBinding(model_endpoint_id=model_endpoint_id),
    )
