"""父级已取得的诊断/工单观察，只读隔离材料；不授予执行或预算许可。"""

import hashlib
import json
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from supportops_core.specialist_request_contracts import canonical_request_json
from supportops_core.specialist_task_contracts import (
    Digest, SpecialistContract, SpecialistEvidence, SpecialistParent, SpecialistResource, SpecialistVersion,
)
from supportops_core.tool_result_services import MODEL_TOOL_GUIDANCE, _check_json

BusinessSpecialist = Literal["diagnostic", "ticket_analysis"]
BusinessTool = Literal["get_request_logs", "get_current_user_profile", "get_model_route_status",
    "list_active_incidents", "support_ticket_lookup"]


class SpecialistBusinessMessageSource(SpecialistContract):
    tool_id: BusinessTool
    invocation_id: UUID
    implementation_version: int = Field(ge=1)
    catalog_version: int = Field(ge=1)
    received_at: AwareDatetime


class SpecialistBusinessObservation(SpecialistContract):
    """从父级原ToolMessage显式选择；正文不落事件，只能匹配真实原调用账本。"""

    tool_id: BusinessTool
    tool_call_id: str = Field(min_length=1, max_length=200)
    envelope_json: str = Field(min_length=1, max_length=32768, repr=False)

    @model_validator(mode="after")
    def original_envelope(self) -> "SpecialistBusinessObservation":
        try:
            if len(self.envelope_json.encode("utf-8")) > 32768:
                raise ValueError()
            _check_json(self.envelope_json)
            envelope = json.loads(self.envelope_json)
            if (canonical_request_json(envelope) != self.envelope_json
                or set(envelope) != {"schema_version", "data_trust", "source", "conflict_status", "validity_rule",
                    "execution_permit", "guidance", "data"}
                or envelope["schema_version"] != "model_tool_data_v1"
                or envelope["data_trust"] != "untrusted_tool_data"
                or envelope["execution_permit"] is not False
                or envelope["validity_rule"] != "observation_only_recheck_current_sources"
                or envelope["guidance"] != MODEL_TOOL_GUIDANCE or not isinstance(envelope["data"], dict)
                or envelope["conflict_status"] != ("detected" if envelope["data"].get("status") == "conflict" else "not_assessed")):
                raise ValueError()
            source = SpecialistBusinessMessageSource.model_validate_json(canonical_request_json(envelope["source"]))
            if source.tool_id != self.tool_id:
                raise ValueError()
        except (ValueError, TypeError, KeyError, RecursionError, UnicodeError):
            raise ValueError("SPECIALIST_BUSINESS_ENVELOPE_INVALID") from None
        return self

    @property
    def source(self) -> SpecialistBusinessMessageSource:
        return SpecialistBusinessMessageSource.model_validate_json(canonical_request_json(json.loads(self.envelope_json)["source"]))

    @property
    def payload_json(self) -> str:
        return canonical_request_json(json.loads(self.envelope_json)["data"])


class SpecialistBusinessSource(SpecialistContract):
    invocation_id: UUID
    tool_id: BusinessTool
    tool_call_id: str = Field(min_length=1, max_length=200)
    implementation_version: int = Field(ge=1)
    catalog_version: int = Field(ge=1)
    result_digest: Digest
    message_digest: Digest
    observed_at: AwareDatetime
    observation_until: AwareDatetime

    @model_validator(mode="after")
    def original_lifetime(self) -> "SpecialistBusinessSource":
        if (self.observation_until - self.observed_at).total_seconds() != 300:
            raise ValueError("SPECIALIST_BUSINESS_SOURCE_WINDOW_INVALID")
        return self


class SpecialistBusinessScope(SpecialistContract):
    schema_version: Literal["specialist_business_scope_v1"] = "specialist_business_scope_v1"
    specialist: BusinessSpecialist
    parent: SpecialistParent
    child_version: SpecialistVersion
    resources: tuple[SpecialistResource, ...] = Field(min_length=1, max_length=32)
    evidence: tuple[SpecialistEvidence, ...] = Field(min_length=1, max_length=32, repr=False)
    sources: tuple[SpecialistBusinessSource, ...] = Field(min_length=1, max_length=32)
    checked_at: AwareDatetime
    expires_at: AwareDatetime
    execution_permit: Literal[False] = False
    budget_reserved: Literal[False] = False

    @model_validator(mode="after")
    def source_binding(self) -> "SpecialistBusinessScope":
        sources = {item.invocation_id: item for item in self.sources}
        expected_kind = "diagnostic_request" if self.specialist == "diagnostic" else "support_ticket"
        if (not self.checked_at < self.expires_at <= self.parent.authorized_until
            or (self.expires_at - self.checked_at).total_seconds() > 30
            or len(sources) != len(self.sources) or len({item.resource_id for item in self.resources}) != len(self.resources)
            or len({item.evidence_id for item in self.evidence}) != len(self.evidence)
            or set(sources) != {item.evidence_id for item in self.evidence}
            or {item.resource for item in self.evidence} != set(self.resources)):
            raise ValueError("SPECIALIST_BUSINESS_SCOPE_INVALID")
        for item in self.evidence:
            source = sources[item.evidence_id]
            if (item.resource.resource_kind != expected_kind or item.resource.tenant_id != self.parent.tenant_id
                or item.resource.authorization_scope_digest != self.parent.authorization_scope_digest
                or item.content_digest != source.result_digest or item.observed_at != source.observed_at
                or item.valid_until != self.expires_at or not source.observed_at <= self.checked_at
                or self.expires_at > source.observation_until
                or (source.tool_id == "support_ticket_lookup") != (self.specialist == "ticket_analysis")):
                raise ValueError("SPECIALIST_BUSINESS_SCOPE_INVALID")
        return self


class SpecialistBusinessExcerpt(SpecialistContract):
    evidence_id: UUID
    resource: SpecialistResource
    tool_id: BusinessTool
    original_result_digest: Digest
    rendered_content_digest: Digest
    payload_json: str = Field(min_length=1, max_length=16384, repr=False)
    content_is_redacted: bool
    data_is_untrusted: Literal[True] = True

    @model_validator(mode="after")
    def rendered_binding(self) -> "SpecialistBusinessExcerpt":
        if (len(self.payload_json.encode("utf-8")) > 16384
            or hashlib.sha256(self.payload_json.encode("utf-8")).hexdigest() != self.rendered_content_digest
            or self.content_is_redacted != (self.original_result_digest != self.rendered_content_digest)):
            raise ValueError("SPECIALIST_BUSINESS_RENDERING_INVALID")
        return self


def business_payload_bytes(question: str, excerpts: tuple[SpecialistBusinessExcerpt, ...]) -> int:
    return len(canonical_request_json({"question": question,
        "excerpts": [item.model_dump(mode="json") for item in excerpts]}).encode("utf-8"))


class SpecialistBusinessContext(SpecialistContract):
    schema_version: Literal["specialist_business_context_v1"] = "specialist_business_context_v1"
    task_id: UUID
    specialist: BusinessSpecialist
    input_digest: Digest
    scope_digest: Digest
    budget_scope_digest: Digest
    checked_at: AwareDatetime
    expires_at: AwareDatetime
    question: str = Field(min_length=1, max_length=8192, repr=False)
    question_is_redacted: bool
    excerpts: tuple[SpecialistBusinessExcerpt, ...] = Field(min_length=1, max_length=32, repr=False)
    payload_bytes: int = Field(ge=1, le=65536)
    data_is_untrusted: Literal[True] = True
    execution_permit: Literal[False] = False
    budget_reserved: Literal[False] = False

    @model_validator(mode="after")
    def bounded_material(self) -> "SpecialistBusinessContext":
        expected = "diagnostic_request" if self.specialist == "diagnostic" else "support_ticket"
        if (not self.checked_at < self.expires_at or not self.question.strip()
            or len(self.question.encode("utf-8")) > 8192
            or len({item.evidence_id for item in self.excerpts}) != len(self.excerpts)
            or any(item.resource.resource_kind != expected for item in self.excerpts)
            or self.payload_bytes != business_payload_bytes(self.question, self.excerpts)):
            raise ValueError("SPECIALIST_BUSINESS_CONTEXT_INVALID")
        return self


def specialist_business_context_digest(context: SpecialistBusinessContext) -> str:
    return hashlib.sha256(canonical_request_json(context.model_dump(mode="json",
        exclude={"checked_at"})).encode("utf-8")).hexdigest()
