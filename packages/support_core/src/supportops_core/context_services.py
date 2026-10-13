"""产品历史与模型上下文分离，保留当前输入并给出保守完整计量。"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Literal
from uuid import UUID

from langchain.agents.middleware import ModelRequest
from langchain_core.messages import BaseMessage
from langchain_core.tools import BaseTool
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import Field

from supportops_core.agent_config import StrictModel, SupportAnswer, SupportContext
from supportops_core.diagnostic_contracts import DiagnosticReport


class ContextBudgetExceeded(ValueError):
    code = "AGENT_CONTEXT_BUDGET_EXCEEDED"


class ContextEvidence(StrictModel):
    """只有服务器证据身份，不将模型摘要自动升级为确认事实。"""
    evidence_id: UUID
    snapshot_id: UUID
    version_id: UUID
    section_id: str
    content_digest: str
    retrieved_at: datetime


class ConfirmedFact(StrictModel):
    description: str
    evidence_ids: list[UUID] = Field(min_length=1)
    verified_at: datetime


class ContextSnapshot(StrictModel):
    schema_version: Literal["1"] = "1"
    strategy: Literal["bounded_product_history_v1"] = "bounded_product_history_v1"
    current_input_id: UUID
    question_digest: str
    through_input_sequence: int
    selected_message_ids: list[UUID]
    omitted_message_count: int = Field(ge=0)
    authorization_boundary: Literal["current_database_at_each_boundary"] = "current_database_at_each_boundary"
    confirmed_facts: list[ConfirmedFact] = Field(default_factory=list)
    evidence: list[ContextEvidence] = Field(default_factory=list)
    unresolved_issues: list[str] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)
    diagnostic: DiagnosticReport | None = None


def question_digest(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()


def conservative_input_tokens(request: ModelRequest[SupportContext], messages: list[BaseMessage] | None = None) -> int:
    """计量完整文本/工具 Schema/结构化输出，使用 UTF-8 字节保守估算。

    不声称适配任意供应商的精确 tokenizer；预留每项协议开销。未知多模态
    内容拒绝，避免 URL/媒体的字节数被当作实际模型 Token。
    """
    actual_messages = request.messages if messages is None else messages
    for message in actual_messages:
        if isinstance(message.content, list) and any(
            not isinstance(block, str) and (not isinstance(block, dict) or block.get("type") != "text")
            for block in message.content
        ):
            raise ContextBudgetExceeded("当前预算策略不支持未知多模态输入计量")
    tools = [convert_to_openai_tool(tool) if isinstance(tool, BaseTool) else tool for tool in request.tools]
    payload = {"system": request.system_message.model_dump(mode="json") if request.system_message else None,
               "messages": [message.model_dump(mode="json") for message in actual_messages],
               "tools": tools, "response_schema": SupportAnswer.model_json_schema(), "model_settings": request.model_settings}
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, allow_nan=False)
    return len(encoded.encode()) + 256 * (len(actual_messages) + len(tools) + 2)


def fit_model_context(request: ModelRequest[SupportContext], *, input_limit: int) -> tuple[ModelRequest[SupportContext], int, int]:
    """只裁掉完整旧用户轮次；当前问题与本轮完整工具轨迹必须保留。"""
    messages = list(request.messages)
    removed = 0
    tokens = conservative_input_tokens(request, messages)
    while tokens > input_limit:
        human_positions = [index for index, message in enumerate(messages) if message.type == "human"]
        if len(human_positions) < 2:
            raise ContextBudgetExceeded("当前问题、系统指令、工具证据与输出预留超过上下文预算")
        next_turn = human_positions[1]
        removed += next_turn
        messages = messages[next_turn:]
        tokens = conservative_input_tokens(request, messages)
    return request.override(messages=messages), tokens, removed
