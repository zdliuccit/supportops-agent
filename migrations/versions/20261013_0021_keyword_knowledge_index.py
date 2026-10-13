"""关键词索引代次、分块、证据和知识 Outbox 死信；不启用向量。"""

from collections.abc import Sequence
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision: str = "20261013_0021"
down_revision: str | None = "20261012_0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("knowledge_outbox_events", sa.Column("dead_at", sa.DateTime(timezone=True), nullable=True, comment="有限消费耗尽进入死信时间。"))
    op.add_column("knowledge_outbox_events", sa.Column("error_code", sa.String(100), nullable=True, comment="索引消费稳定错误码，不保存正文或异常原文。"))
    op.create_index("ix_knowledge_outbox_consumption", "knowledge_outbox_events", ["processed_at", "dead_at", "available_at"])
    op.create_table(
        "knowledge_index_generations",
        sa.Column("id", sa.Uuid(), primary_key=True, comment="索引代次唯一标识。"),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False, comment="索引所属租户。"),
        sa.Column("document_id", sa.Uuid(), sa.ForeignKey("knowledge_documents.id"), nullable=False, comment="关联稳定文档。"),
        sa.Column("snapshot_id", sa.Uuid(), sa.ForeignKey("knowledge_snapshots.id"), nullable=False, comment="构建的不可变快照。"),
        sa.Column("generation_number", sa.Integer(), nullable=False, comment="文档内单调递增索引代次。"),
        sa.Column("algorithm_version", sa.String(64), nullable=False, comment="分块和关键词策略版本，不表示 embedding。"),
        sa.Column("content_digest", sa.String(64), nullable=False, comment="规范化原文摘要。"),
        sa.Column("status", sa.String(32), nullable=False, comment="pending/ready/invalidated/failed。"),
        sa.Column("chunk_count", sa.Integer(), nullable=False, comment="成功构建的分块数。"),
        sa.Column("error_code", sa.String(100), nullable=True, comment="构建失败稳定错误码。"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, comment="代次创建时间。"),
        sa.Column("ready_at", sa.DateTime(timezone=True), nullable=True, comment="构建并切换完成时间。"),
        sa.UniqueConstraint("document_id", "generation_number", name="uq_knowledge_index_generation"),
        comment="知识关键词索引代次及可追溯构建状态。",
    )
    op.create_index("ix_knowledge_index_tenant_status", "knowledge_index_generations", ["tenant_id", "status", "document_id"])
    op.create_table(
        "knowledge_chunks",
        sa.Column("id", sa.Uuid(), primary_key=True, comment="分块数据库唯一标识。"),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False, comment="分块所属租户。"),
        sa.Column("generation_id", sa.Uuid(), sa.ForeignKey("knowledge_index_generations.id"), nullable=False, comment="索引代次。"),
        sa.Column("ordinal", sa.Integer(), nullable=False, comment="原文内从零开始的分块序号。"),
        sa.Column("chunk_key", sa.String(64), nullable=False, comment="快照/算法/偏移/摘要导出的可复现分块键。"),
        sa.Column("section_id", sa.String(500), nullable=False, comment="章节锚点，无标题时使用 document。"),
        sa.Column("section_title", sa.String(500), nullable=False, comment="章节显示标题。"),
        sa.Column("start_offset", sa.Integer(), nullable=False, comment="规范化原文字符起点。"),
        sa.Column("end_offset", sa.Integer(), nullable=False, comment="规范化原文字符终点，不包含该字符。"),
        sa.Column("content_digest", sa.String(64), nullable=False, comment="该分块原文 SHA-256。"),
        sa.Column("content", sa.Text(), nullable=False, comment="获准后才允许返回的知识数据，不是系统指令。"),
        sa.UniqueConstraint("generation_id", "ordinal", name="uq_knowledge_chunk_ordinal"),
        comment="按索引代次保存知识原文分块和章节定位。",
    )
    op.create_index("ix_knowledge_chunk_tenant_generation", "knowledge_chunks", ["tenant_id", "generation_id"])
    op.create_table(
        "knowledge_evidence",
        sa.Column("id", sa.Uuid(), primary_key=True, comment="服务端生成的证据 ID。"),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False, comment="证据所属租户。"),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False, comment="检索时可信主体。"),
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("agent_runs.id"), nullable=False, comment="关联业务 Run。"),
        sa.Column("attempt_id", sa.Uuid(), sa.ForeignKey("run_attempts.id"), nullable=False, comment="实际检索执行尝试。"),
        sa.Column("tool_invocation_id", sa.Uuid(), sa.ForeignKey("tool_invocations.id"), nullable=False, comment="获准并成功返回模型的检索调用。"),
        sa.Column("snapshot_id", sa.Uuid(), sa.ForeignKey("knowledge_snapshots.id"), nullable=False, comment="引用的发布快照。"),
        sa.Column("chunk_id", sa.Uuid(), sa.ForeignKey("knowledge_chunks.id"), nullable=False, comment="引用的不可变分块。"),
        sa.Column("content_digest", sa.String(64), nullable=False, comment="检索时分块摘要。"),
        sa.Column("query_digest", sa.String(64), nullable=False, comment="查询摘要，不保存用户原始问题。"),
        sa.Column("score", sa.Float(), nullable=False, comment="keyword_v1 词项命中分数，不是语义相似度。"),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False, comment="实际检索时间。"),
        comment="绑定检索主体、执行尝试、快照与分块的证据记录。",
    )
    op.create_index("ix_knowledge_evidence_run_attempt", "knowledge_evidence", ["tenant_id", "run_id", "attempt_id"])
    # 为旧发布快照排重建事件，不把尚未构建的知识标记为 ready。
    connection = op.get_bind()
    snapshots = sa.table("knowledge_snapshots", sa.column("id", sa.Uuid()), sa.column("tenant_id", sa.Uuid()),
                         sa.column("document_id", sa.Uuid()), sa.column("version_id", sa.Uuid()), sa.column("deleted_at", sa.DateTime()))
    documents = sa.table("knowledge_documents", sa.column("id", sa.Uuid()), sa.column("current_version_id", sa.Uuid()), sa.column("deleted_at", sa.DateTime()))
    events = sa.table("knowledge_outbox_events", sa.column("id", sa.Uuid()), sa.column("tenant_id", sa.Uuid()),
                      sa.column("document_id", sa.Uuid()), sa.column("version_id", sa.Uuid()), sa.column("event_type", sa.String()),
                      sa.column("event_key", sa.String()), sa.column("payload", sa.JSON()), sa.column("attempts", sa.Integer()),
                      sa.column("available_at", sa.DateTime()), sa.column("created_at", sa.DateTime()))
    query = sa.select(snapshots.c.tenant_id, snapshots.c.document_id, snapshots.c.version_id, snapshots.c.id).join(
        documents, documents.c.id == snapshots.c.document_id).where(
        snapshots.c.deleted_at.is_(None), documents.c.deleted_at.is_(None), documents.c.current_version_id == snapshots.c.version_id)
    for row in connection.execute(query):
        connection.execute(events.insert().values(id=uuid4(), tenant_id=row.tenant_id, document_id=row.document_id,
            version_id=row.version_id, event_type="knowledge.rebuild", event_key=f"keyword_v1.initial:{row.id}",
            payload={}, attempts=0, available_at=sa.func.now(), created_at=sa.func.now()))


def downgrade() -> None:
    op.drop_table("knowledge_evidence")
    op.drop_table("knowledge_chunks")
    op.drop_table("knowledge_index_generations")
    op.drop_index("ix_knowledge_outbox_consumption", table_name="knowledge_outbox_events")
    op.drop_column("knowledge_outbox_events", "error_code")
    op.drop_column("knowledge_outbox_events", "dead_at")
