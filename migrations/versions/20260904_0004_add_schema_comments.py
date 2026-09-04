"""为业务表和字段补充 PostgreSQL 说明注释。

Revision ID: 20260904_0004
Revises: 8f4d8fe16359
Create Date: 2026-09-04 14:00:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260904_0004"
down_revision: str | None = "8f4d8fe16359"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 注释同时定义在 SQLAlchemy 模型中；本迁移负责把说明补到已经存在的 PostgreSQL。
TABLE_COMMENTS = {
    "tenants": "企业租户，作为用户、Agent、模型和会话的数据隔离边界。",
    "users": "租户内用户身份及其可信角色。",
    "model_credentials": "模型端点当前凭据；密钥只以加密密文保存且不可通过 API 读回。",
    "model_credential_revisions": "模型凭据不可变历史版本，确保 Run 可解析其固定 revision。",
    "model_endpoints": "租户内稳定模型端点，资料与不可变调用版本分离管理。",
    "model_endpoint_versions": "模型端点不可变调用配置版本，供 AgentVersion 固定引用。",
    "agents": "企业 Agent 稳定身份、展示资料、生命周期状态及当前活动版本。",
    "agent_drafts": "Agent 当前可编辑配置，使用 revision 实现乐观并发控制。",
    "agent_versions": "不可变 Agent 运行配置版本，供 Conversation 和 Run 固定引用。",
    "agent_access_grants": "租户内 Agent 使用授权；未命中任何授权时默认拒绝。",
    "agent_audit_events": "Agent 与模型控制面追加写审计，不保存密钥或完整 Prompt。",
    "conversations": "用户可见会话；创建时固定 AgentVersion，产品消息是历史事实源。",
    "messages": "用户可见会话消息；通过幂等键防止重复提交。",
    "agent_runs": "Agent 单轮执行记录，固定身份、配置、模型和凭据 revision。",
    "run_events": "Run 生命周期和流式输出的追加写事件流。",
}

COLUMN_COMMENTS = {
    "tenants": {
        "id": "租户唯一标识。",
        "name": "租户显示名称。",
        "status": "租户状态。",
        "created_at": "租户创建时间。",
    },
    "users": {
        "id": "用户唯一标识。",
        "tenant_id": "用户所属租户。",
        "external_subject": "外部身份系统中的稳定主体标识。",
        "display_name": "用户显示名称。",
        "roles": "来自可信身份上下文的角色列表。",
        "status": "用户状态。",
        "created_at": "用户首次建立映射的时间。",
    },
    "model_credentials": {
        "id": "凭据唯一标识。",
        "tenant_id": "凭据所属租户。",
        "endpoint_id": "凭据所属的稳定模型端点。",
        "provider": "Secret Provider 类型。",
        "encrypted_secret": "由服务端主密钥封装后的模型 API Key 密文。",
        "masked_hint": "供管理员识别密钥的脱敏提示。",
        "revision": "当前凭据修订号。",
        "created_at": "凭据创建时间。",
        "rotated_at": "最近一次密钥轮换时间。",
    },
    "model_credential_revisions": {
        "id": "历史凭据唯一标识。",
        "tenant_id": "历史凭据所属租户。",
        "credential_id": "稳定凭据标识。",
        "revision": "不可变凭据修订号。",
        "encrypted_secret": "该 revision 对应的加密 API Key 密文。",
        "created_at": "历史凭据创建时间。",
    },
    "model_endpoints": {
        "id": "模型端点唯一标识。",
        "tenant_id": "模型端点所属租户。",
        "name": "模型端点显示名称。",
        "logo_url": "模型端点 Logo 地址。",
        "status": "模型端点生命周期状态。",
        "read_only": "是否为仅用于历史引用的只读迁移记录。",
        "active_version_id": "当前启用的不可变模型端点版本。",
        "created_by": "创建该端点的用户。",
        "updated_by": "最近修改端点资料的用户。",
        "created_at": "模型端点创建时间。",
        "updated_at": "模型端点最近更新时间。",
    },
    "model_endpoint_versions": {
        "id": "模型版本唯一标识。",
        "tenant_id": "模型版本所属租户。",
        "endpoint_id": "所属稳定模型端点。",
        "version_number": "端点内单调递增的版本号。",
        "provider_kind": "模型供应商类型。",
        "api_protocol": "调用模型使用的 API 协议。",
        "base_url": "通过安全策略校验的 API Base URL。",
        "remote_model_name": "供应商端点识别的远端模型名称。",
        "capabilities": "声明并验证的模型能力。",
        "defaults": "默认参数及模型限制。",
        "request_metadata": "允许随请求发送的非秘密组织或项目元数据。",
        "pricing": "用于成本预算的价格元数据。",
        "credential_id": "运行时解析的稳定凭据标识。",
        "verification_status": "最近一次连接及能力验证状态。",
        "verification_result": "不含秘密和业务数据的分项验证结果。",
        "verified_at": "最近一次验证完成时间。",
        "created_by": "创建该不可变版本的用户。",
        "created_at": "模型端点版本创建时间。",
    },
    "agents": {
        "id": "Agent 唯一标识。",
        "tenant_id": "Agent 所属租户。",
        "slug": "租户内唯一、适合 URL 和审计的稳定短名。",
        "name": "员工界面展示的 Agent 名称。",
        "logo_url": "员工界面展示的 Agent Logo 地址。",
        "description": "Agent 能力描述。",
        "welcome_message": "新建对话时展示的欢迎语。",
        "suggested_prompts": "新建对话建议问题列表。",
        "status": "Agent 生命周期状态。",
        "read_only": "是否为仅用于历史会话的只读迁移 Agent。",
        "active_version_id": "新会话使用的当前活动 AgentVersion。",
        "created_by": "创建 Agent 的用户。",
        "updated_by": "最近更新 Agent 资料或状态的用户。",
        "created_at": "Agent 创建时间。",
        "updated_at": "Agent 最近更新时间。",
    },
    "agent_drafts": {
        "agent_id": "草稿所属 Agent，同时作为主键。",
        "tenant_id": "草稿所属租户。",
        "revision": "每次成功保存后单调递增的乐观锁版本。",
        "schema_version": "Agent 配置 JSON Schema 版本。",
        "config": "待校验和发布的声明式 Agent 配置。",
        "updated_by": "最近保存草稿的管理员。",
        "updated_at": "草稿最近保存时间。",
    },
    "agent_versions": {
        "id": "Agent 版本唯一标识。",
        "tenant_id": "Agent 版本所属租户。",
        "agent_id": "所属稳定 Agent。",
        "version_number": "Agent 内单调递增的版本号。",
        "schema_version": "配置快照的 Schema 版本。",
        "config": "发布时规范化后的完整运行配置快照。",
        "config_digest": "规范化配置的 SHA-256 摘要，用于一致性校验。",
        "model_endpoint_version_id": "发布时固定的不可变模型端点版本。",
        "resolved_tool_ids": "发布时解析并允许执行的工具标识列表。",
        "release_notes": "管理员填写的版本变更说明。",
        "published_by": "发布该版本的管理员。",
        "published_at": "版本发布时间。",
    },
    "agent_access_grants": {
        "id": "授权记录唯一标识。",
        "tenant_id": "授权所属租户。",
        "agent_id": "被授权使用的 Agent。",
        "subject_type": "授权主体类型：用户或角色。",
        "subject_id": "用户 UUID 或规范角色标识。",
        "created_by": "创建授权的管理员。",
        "created_at": "授权创建时间。",
    },
    "agent_audit_events": {
        "id": "审计事件递增标识。",
        "tenant_id": "审计事件所属租户。",
        "resource_type": "被操作资源类型。",
        "agent_id": "相关 Agent；模型事件可为空。",
        "model_endpoint_id": "相关模型端点；Agent 事件可为空。",
        "action": "规范化控制面动作。",
        "actor_user_id": "执行操作的可信用户。",
        "version_id": "相关 AgentVersion 或 ModelEndpointVersion 标识。",
        "metadata": "不含秘密和完整 Prompt 的审计摘要。",
        "correlation_id": "串联请求、审计与运行日志的关联标识。",
        "created_at": "审计事件发生时间。",
    },
    "conversations": {
        "id": "会话唯一标识。",
        "tenant_id": "会话所属租户。",
        "user_id": "会话所属用户。",
        "agent_id": "会话使用的稳定 Agent。",
        "agent_version_id": "会话创建时固定的 AgentVersion。",
        "title": "用户可修改的会话标题。",
        "is_pinned": "是否在最近会话中置顶。",
        "status": "会话生命周期状态。",
        "created_at": "会话创建时间。",
        "updated_at": "会话最近活动时间。",
    },
    "messages": {
        "id": "消息唯一标识。",
        "tenant_id": "消息所属租户。",
        "conversation_id": "消息所属会话。",
        "user_id": "消息归属用户；助手消息沿用会话用户。",
        "role": "消息角色。",
        "content": "用户可见消息正文。",
        "content_hash": "消息正文 SHA-256 摘要，用于幂等冲突检测。",
        "idempotency_key": "客户端或 Worker 提供的幂等键。",
        "correlation_id": "关联消息请求、Run 和日志的标识。",
        "created_at": "消息创建时间。",
    },
    "agent_runs": {
        "id": "Run 唯一标识。",
        "tenant_id": "Run 所属租户。",
        "conversation_id": "触发 Run 的会话。",
        "agent_id": "执行使用的稳定 Agent。",
        "agent_version_id": "执行固定的 AgentVersion。",
        "model_endpoint_version_id": "执行固定的模型端点版本。",
        "credential_revision": "执行固定使用的模型凭据 revision。",
        "config_digest": "执行配置摘要，用于检测版本引用漂移。",
        "identity_roles": "创建 Run 时固定的可信身份角色。",
        "input_message_id": "触发本次 Run 的唯一用户消息。",
        "status": "Run 生命周期状态。",
        "correlation_id": "关联请求、事件和日志的标识。",
        "error_code": "失败终态的稳定脱敏错误码。",
        "created_at": "Run 创建并入队的时间。",
        "started_at": "Worker 成功领取 Run 的时间。",
        "finished_at": "Run 进入终态的时间。",
        "updated_at": "Run 最近更新时间。",
    },
    "run_events": {
        "id": "事件全局递增标识，兼作 SSE event ID。",
        "tenant_id": "事件所属租户。",
        "run_id": "事件所属 Run。",
        "sequence": "Run 内从 1 开始单调递增的事件序号。",
        "event_type": "事件类型。",
        "data": "脱敏后的事件负载。",
        "correlation_id": "关联请求、Run 与日志的标识。",
        "created_at": "事件创建时间。",
    },
}


def _identifier(value: str) -> str:
    """引用由本迁移常量定义的 PostgreSQL 标识符。"""
    if not value.replace("_", "").isalnum():
        raise ValueError(f"非法数据库标识符：{value}")
    return f'"{value}"'


def _literal(value: str | None) -> str:
    """将固定注释文本转换为 PostgreSQL 字符串字面量。"""
    return "NULL" if value is None else "'" + value.replace("'", "''") + "'"


def _apply_comments(*, enabled: bool) -> None:
    """添加或清除本迁移管理的全部表与字段注释。"""
    for table_name, table_comment in TABLE_COMMENTS.items():
        effective_table_comment = table_comment if enabled else None
        op.execute(
            f"COMMENT ON TABLE {_identifier(table_name)} IS {_literal(effective_table_comment)}"
        )
        for column_name, column_comment in COLUMN_COMMENTS[table_name].items():
            effective_column_comment = column_comment if enabled else None
            op.execute(
                "COMMENT ON COLUMN "
                f"{_identifier(table_name)}.{_identifier(column_name)} "
                f"IS {_literal(effective_column_comment)}"
            )


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(
        "COMMENT ON SCHEMA supportops_checkpoints IS "
        "'LangGraph 持久化 checkpoint、内部消息和工具轨迹的独立 Schema。'"
    )
    _apply_comments(enabled=True)


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    _apply_comments(enabled=False)
    op.execute("COMMENT ON SCHEMA supportops_checkpoints IS NULL")
