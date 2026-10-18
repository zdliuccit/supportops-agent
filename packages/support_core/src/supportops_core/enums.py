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
    TOOL = "tool"
    RUNTIME_ALERT = "runtime_alert"
    AUTH_SESSION = "auth_session"


class AuditAction(StrEnum):
    CREATED = "created"
    PROFILE_UPDATED = "profile_updated"
    DRAFT_UPDATED = "draft_updated"
    VALIDATION_FAILED = "validation_failed"
    VERSION_PUBLISHED = "version_published"
    VERSION_ACTIVATED = "version_activated"
    VERSION_ROLLED_BACK = "version_rolled_back"
    RELEASE_ACTIVATION_REJECTED = "release_activation_rejected"
    VERSION_WITHDRAWN = "version_withdrawn"
    DISABLED = "disabled"
    GRANTS_REPLACED = "grants_replaced"
    CREDENTIAL_ROTATED = "credential_rotated"
    CREDENTIAL_REVOKED = "credential_revoked"
    CREDENTIAL_RESOLVED = "credential_resolved"
    CREDENTIAL_ACCESS_DENIED = "credential_access_denied"
    RUNTIME_ALERT_ACKNOWLEDGED = "runtime_alert_acknowledged"
    AUTH_SESSION_CREATED = "auth_session_created"
    AUTH_SESSION_REVOKED = "auth_session_revoked"
    AUTH_SESSIONS_REVOKED = "auth_sessions_revoked"
    CONNECTION_TESTED = "connection_tested"
    KNOWLEDGE_CREATED = "knowledge_created"
    KNOWLEDGE_UPDATED = "knowledge_updated"
    KNOWLEDGE_REVIEW_SUBMITTED = "knowledge_review_submitted"
    KNOWLEDGE_REVIEW_APPROVED = "knowledge_review_approved"
    KNOWLEDGE_REVIEW_REJECTED = "knowledge_review_rejected"
    BUSINESS_ACTION_PROPOSED = "business_action_proposed"
    BUSINESS_ACTION_COMPENSATION_PROPOSED = "business_action_compensation_proposed"
    BUSINESS_ACTION_QUEUED = "business_action_queued"
    BUSINESS_ACTION_CANCELLED = "business_action_cancelled"
    APPROVAL_DENIED = "approval_denied"
    APPROVAL_REQUESTED = "approval_requested"
    APPROVAL_DECIDED = "approval_decided"
    APPROVAL_CONSUMED = "approval_consumed"
    MOCK_TICKET_COMMAND = "mock_ticket_command"
    KNOWLEDGE_PUBLISHED = "knowledge_published"
    KNOWLEDGE_RETIRED = "knowledge_retired"
    KNOWLEDGE_ROLLED_BACK = "knowledge_rolled_back"
    KNOWLEDGE_ACL_UPDATED = "knowledge_acl_updated"
    KNOWLEDGE_ACCESS_DENIED = "knowledge_access_denied"
    KNOWLEDGE_INGESTION_CREATED = "knowledge_ingestion_created"
    KNOWLEDGE_INGESTION_UPDATED = "knowledge_ingestion_updated"
    KNOWLEDGE_INGESTION_PAGE_COMMITTED = "knowledge_ingestion_page_committed"
    KNOWLEDGE_INGESTION_REQUEST_STOPPED = "knowledge_ingestion_request_stopped"
    TOOL_UPDATED = "tool_updated"
    TOOL_REVOKED = "tool_revoked"
    TOOL_REVOCATION_CLEARED = "tool_revocation_cleared"


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
    WAITING_APPROVAL = "waiting_approval"
    WAITING_ACTION = "waiting_action"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_RUN_STATUSES = {
    RunStatus.COMPLETED,
    RunStatus.FAILED,
    RunStatus.CANCELLED,
}

ALLOWED_RUN_TRANSITIONS: dict[RunStatus, set[RunStatus]] = {
    RunStatus.QUEUED: {RunStatus.RUNNING, RunStatus.CANCELLED, RunStatus.FAILED},
    RunStatus.RUNNING: {
        RunStatus.WAITING_APPROVAL,
        RunStatus.QUEUED,
        RunStatus.COMPLETED,
        RunStatus.FAILED,
        RunStatus.CANCELLING,
    },
    RunStatus.CANCELLING: {RunStatus.CANCELLED, RunStatus.FAILED},
    RunStatus.WAITING_APPROVAL: {RunStatus.WAITING_ACTION, RunStatus.COMPLETED, RunStatus.CANCELLED, RunStatus.FAILED},
    RunStatus.WAITING_ACTION: {RunStatus.WAITING_APPROVAL, RunStatus.COMPLETED, RunStatus.CANCELLED, RunStatus.FAILED},
    RunStatus.COMPLETED: set(),
    RunStatus.FAILED: set(),
    RunStatus.CANCELLED: set(),
}
