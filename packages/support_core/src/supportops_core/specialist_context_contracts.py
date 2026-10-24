"""子任务知识上下文投影；只表示一次读取观察，不授予执行或预算许可。"""

import hashlib
import json
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from supportops_core.specialist_task_contracts import (
    Digest, SpecialistContract, SpecialistEvidence, SpecialistParent,
    SpecialistResource, SpecialistVersion,
)


class SpecialistKnowledgeScope(SpecialistContract):
    schema_version: Literal["specialist_knowledge_scope_v1"] = "specialist_knowledge_scope_v1"
    parent: SpecialistParent
    child_version: SpecialistVersion
    resources: tuple[SpecialistResource, ...] = Field(min_length=1, max_length=32)
    evidence: tuple[SpecialistEvidence, ...] = Field(min_length=1, max_length=64, repr=False)
    checked_at: AwareDatetime
    expires_at: AwareDatetime
    execution_permit: Literal[False] = False
    budget_reserved: Literal[False] = False

    @model_validator(mode="after")
    def bounded_scope(self) -> "SpecialistKnowledgeScope":
        if (not self.checked_at < self.expires_at <= self.parent.authorized_until
            or (self.expires_at - self.checked_at).total_seconds() > 30
            or len({item.resource_id for item in self.resources}) != len(self.resources)
            or len({item.evidence_id for item in self.evidence}) != len(self.evidence)):
            raise ValueError("SPECIALIST_CONTEXT_SCOPE_INVALID")
        for resource in self.resources:
            if (resource.resource_kind != "knowledge_document"
                or resource.tenant_id != self.parent.tenant_id
                or resource.authorization_scope_digest != self.parent.authorization_scope_digest):
                raise ValueError("SPECIALIST_CONTEXT_SCOPE_INVALID")
        for evidence in self.evidence:
            if (evidence.resource not in self.resources or evidence.observed_at > self.checked_at
                or evidence.valid_until != self.expires_at):
                raise ValueError("SPECIALIST_CONTEXT_SCOPE_INVALID")
        if {item.resource for item in self.evidence} != set(self.resources):
            raise ValueError("SPECIALIST_CONTEXT_SCOPE_INVALID")
        return self


class SpecialistKnowledgeExcerpt(SpecialistContract):
    evidence_id: UUID
    resource: SpecialistResource
    snapshot_id: UUID
    version_id: UUID
    original_content_digest: Digest
    rendered_content_digest: Digest
    title: str = Field(max_length=500, repr=False)
    section_id: str = Field(max_length=500)
    section_title: str = Field(max_length=500, repr=False)
    content: str = Field(min_length=1, max_length=16384, repr=False)
    content_is_redacted: bool
    data_is_untrusted: Literal[True] = True

    @model_validator(mode="after")
    def bound_rendering(self) -> "SpecialistKnowledgeExcerpt":
        if (self.resource.resource_kind != "knowledge_document"
            or len(self.content.encode("utf-8")) > 16384
            or hashlib.sha256(self.content.encode("utf-8")).hexdigest() != self.rendered_content_digest
            or self.content_is_redacted != (self.original_content_digest != self.rendered_content_digest)):
            raise ValueError("SPECIALIST_CONTEXT_RENDERING_INVALID")
        return self


def knowledge_payload_bytes(question: str, excerpts: tuple[SpecialistKnowledgeExcerpt, ...]) -> int:
    return len(json.dumps({"question": question,
        "excerpts": [item.model_dump(mode="json") for item in excerpts]},
        ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8"))


class SpecialistKnowledgeContext(SpecialistContract):
    schema_version: Literal["specialist_knowledge_context_v1"] = "specialist_knowledge_context_v1"
    task_id: UUID
    input_digest: Digest
    scope_digest: Digest
    budget_scope_digest: Digest
    checked_at: AwareDatetime
    expires_at: AwareDatetime
    question: str = Field(min_length=1, max_length=8192, repr=False)
    question_is_redacted: bool
    excerpts: tuple[SpecialistKnowledgeExcerpt, ...] = Field(min_length=1, max_length=64, repr=False)
    # 仅计序列化后的question/excerpts字节，不能代替完整模型请求token/费用计量。
    payload_bytes: int = Field(ge=1, le=65536)
    data_is_untrusted: Literal[True] = True
    execution_permit: Literal[False] = False
    budget_reserved: Literal[False] = False

    @model_validator(mode="after")
    def bounded_context(self) -> "SpecialistKnowledgeContext":
        if (not self.checked_at < self.expires_at
            or len({item.evidence_id for item in self.excerpts}) != len(self.excerpts)
            or len(self.question.encode("utf-8")) > 8192 or not self.question.strip()
            or self.payload_bytes != knowledge_payload_bytes(self.question, self.excerpts)):
            raise ValueError("SPECIALIST_CONTEXT_INVALID")
        return self


def specialist_context_digest(context: SpecialistKnowledgeContext) -> str:
    """原正文/范围/窗口不变时，复检时间变化不制造新的模型输入。"""
    payload = json.dumps(context.model_dump(mode="json", exclude={"checked_at"}),
        ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
