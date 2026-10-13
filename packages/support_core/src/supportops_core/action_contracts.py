"""业务动作状态与冻结参数契约；执行与模型调用计数分开。"""

import hashlib
import json
from typing import Literal

from pydantic import Field, field_validator

from supportops_core.agent_config import SECRET_PATTERNS, StrictModel

ActionStatus = Literal["proposed", "waiting_approval", "queued", "running", "unknown", "reconciling", "succeeded", "failed", "needs_attention", "cancelled"]


class MockTicketProposalInput(StrictModel):
    """模型不能覆盖主体/问题范围/参数；全部从可信 Run 事实派生。"""


class MockTicketParameters(StrictModel):
    """仅冻结建单所需字段，禁止复制日志/Prompt/密钥。"""
    schema_version: Literal["1"] = "1"
    title: str = Field(min_length=1, max_length=200)
    summary: str = Field(min_length=1, max_length=2000)
    issue_key: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")

    @field_validator("title", "summary", "issue_key")
    @classmethod
    def reject_credentials(cls, value: str) -> str:
        if any(pattern.search(value) for pattern in SECRET_PATTERNS):
            raise ValueError("动作参数不能包含疑似凭据")
        return value


def frozen_parameters_digest(parameters: MockTicketParameters) -> str:
    return hashlib.sha256(json.dumps(parameters.model_dump(mode="json"), ensure_ascii=False,
        sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


ACTION_TRANSITIONS: dict[str, frozenset[str]] = {
    "proposed": frozenset({"waiting_approval", "queued", "cancelled"}),
    "waiting_approval": frozenset({"queued", "cancelled"}),
    "queued": frozenset({"running", "cancelled", "failed"}),
    "running": frozenset({"unknown", "succeeded"}),
    "unknown": frozenset({"reconciling", "needs_attention"}),
    "reconciling": frozenset({"unknown", "succeeded", "queued", "failed", "needs_attention"}),
    "needs_attention": frozenset({"reconciling", "unknown"}),
    "succeeded": frozenset(), "failed": frozenset(), "cancelled": frozenset(),
}
