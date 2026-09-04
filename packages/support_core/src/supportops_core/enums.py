from enum import StrEnum

ROLE_PLATFORM_ADMIN = "platform_admin"
ROLE_EMPLOYEE = "employee"
ROLE_AGENT_USER = "agent_user"
ROLE_CUSTOMER = "customer"

AGENT_USE_ROLES = {ROLE_PLATFORM_ADMIN, ROLE_EMPLOYEE, ROLE_AGENT_USER, ROLE_CUSTOMER}


class AgentStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    DISABLED = "disabled"


class ModelEndpointStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    DISABLED = "disabled"


class ModelProviderKind(StrEnum):
    OPENAI_OFFICIAL = "openai_official"
    OPENAI_COMPATIBLE = "openai_compatible"


class ModelApiProtocol(StrEnum):
    RESPONSES = "responses"
    CHAT_COMPLETIONS = "chat_completions"


class ModelVerificationStatus(StrEnum):
    UNTESTED = "untested"
    VERIFIED = "verified"
    PARTIAL = "partial"
    FAILED = "failed"


class GrantSubjectType(StrEnum):
    USER = "user"
    ROLE = "role"


class ResponseStrategy(StrEnum):
    TOOL = "tool"
    PROVIDER = "provider"


class ApprovalPolicy(StrEnum):
    NONE = "none"
    REQUIRED = "required"


class AuditResourceType(StrEnum):
    AGENT = "agent"
    MODEL_ENDPOINT = "model_endpoint"


class AuditAction(StrEnum):
    CREATED = "created"
    PROFILE_UPDATED = "profile_updated"
    DRAFT_UPDATED = "draft_updated"
    VALIDATION_FAILED = "validation_failed"
    VERSION_PUBLISHED = "version_published"
    VERSION_ACTIVATED = "version_activated"
    VERSION_ROLLED_BACK = "version_rolled_back"
    DISABLED = "disabled"
    GRANTS_REPLACED = "grants_replaced"
    CREDENTIAL_ROTATED = "credential_rotated"
    CONNECTION_TESTED = "connection_tested"


class ConversationStatus(StrEnum):
    ACTIVE = "active"
    CLOSED = "closed"


class MessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    CANCELLING = "cancelling"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_RUN_STATUSES = {
    RunStatus.COMPLETED,
    RunStatus.FAILED,
    RunStatus.CANCELLED,
}

ALLOWED_RUN_TRANSITIONS: dict[RunStatus, set[RunStatus]] = {
    RunStatus.QUEUED: {RunStatus.RUNNING, RunStatus.CANCELLED},
    RunStatus.RUNNING: {
        RunStatus.COMPLETED,
        RunStatus.FAILED,
        RunStatus.CANCELLING,
    },
    RunStatus.CANCELLING: {RunStatus.CANCELLED, RunStatus.FAILED},
    RunStatus.COMPLETED: set(),
    RunStatus.FAILED: set(),
    RunStatus.CANCELLED: set(),
}
