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


class ModelTestStatus(StrEnum):
    """单个远端模型一次连通性测试的执行状态。"""

    QUEUED = "queued"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    CANCELLED = "cancelled"


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
    KNOWLEDGE_SOURCE = "knowledge_source"
    KNOWLEDGE_DOCUMENT = "knowledge_document"
    KNOWLEDGE_VERSION = "knowledge_version"


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
    KNOWLEDGE_CREATED = "knowledge_created"
    KNOWLEDGE_UPDATED = "knowledge_updated"
    KNOWLEDGE_REVIEW_SUBMITTED = "knowledge_review_submitted"
    KNOWLEDGE_PUBLISHED = "knowledge_published"
    KNOWLEDGE_RETIRED = "knowledge_retired"
    KNOWLEDGE_ROLLED_BACK = "knowledge_rolled_back"
    KNOWLEDGE_ACL_UPDATED = "knowledge_acl_updated"
    KNOWLEDGE_ACCESS_DENIED = "knowledge_access_denied"


class KnowledgeSourceEnvironment(StrEnum):
    MOCK = "mock"
    DEV = "dev"
    STAGING = "staging"
    PRODUCTION = "production"


class KnowledgeDocumentStatus(StrEnum):
    DRAFT = "draft"
    PUBLISHED = "published"
    EXPIRED = "expired"
    ARCHIVED = "archived"


class KnowledgeVersionStatus(StrEnum):
    DRAFT = "draft"
    IN_REVIEW = "in_review"
    PUBLISHED = "published"
    SUPERSEDED = "superseded"
    RETIRED = "retired"


class KnowledgeAclSubjectType(StrEnum):
    ROLE = "role"
    ORGANIZATION_UNIT = "organization_unit"
    USER = "user"


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
