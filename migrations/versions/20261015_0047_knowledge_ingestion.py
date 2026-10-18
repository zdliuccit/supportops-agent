"""有界知识导入任务、稳定绑定与追加式页/物化事实；不导入或回填来源。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20261015_0047"
down_revision: str | None = "20261015_0046"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table("knowledge_ingestion_jobs",
        sa.Column("id", sa.Uuid(), primary_key=True, comment="服务器生成的任务ID。"),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False, comment="可信申请租户。"),
        sa.Column("source_id", sa.Uuid(), sa.ForeignKey("knowledge_sources.id"), nullable=False, comment="固定知识来源。"),
        sa.Column("requested_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False, comment="实际申请人，其他管理员不能隐式代办。"),
        sa.Column("idempotency_key", sa.String(200), nullable=False, comment="同租户本人创建请求幂等键。"),
        sa.Column("request_digest", sa.String(64), nullable=False, comment="固定范围和预算请求摘要，不含随机ID或创建时间。"),
        sa.Column("definition", postgresql.JSONB(), nullable=False, comment="严格任务定义，仅摘要/引用/预算，无正文或原始游标。"),
        sa.Column("definition_digest", sa.String(64), nullable=False, comment="不可变完整任务定义摘要。"),
        sa.Column("state", postgresql.JSONB(), nullable=False, comment="严格执行状态、检查点和累计计数。"),
        sa.Column("state_digest", sa.String(64), nullable=False, comment="当前状态完整性摘要，不能授予执行许可。"),
        sa.Column("status", sa.String(32), nullable=False, comment="状态索引列，与严格状态JSON绑定。"),
        sa.Column("revision", sa.Integer(), nullable=False, comment="每次状态变化递增的并发版本。"),
        sa.Column("generation", sa.Integer(), nullable=False, comment="领取或取消递增的围栏代次。"),
        sa.Column("attempts", sa.Integer(), nullable=False, comment="累计实际领取次数，重试不清零。"),
        sa.Column("owner_id", sa.Uuid(), nullable=True, comment="当前执行者随机ID，非用户授权令牌。"),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True, comment="当前执行租约截止时间。"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, comment="服务器固定创建时间。"),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=False, comment="服务器固定任务截止时间。"),
        sa.UniqueConstraint("tenant_id", "requested_by", "idempotency_key", name="uq_ingestion_job_request"),
        sa.CheckConstraint("revision >= 1 AND generation >= attempts AND attempts BETWEEN 0 AND 3", name="ck_ingestion_job_counters"),
        sa.CheckConstraint("status IN ('queued','running','retry_wait','completed','failed','cancelled')", name="ck_ingestion_job_status"),
        sa.CheckConstraint("(status = 'running' AND owner_id IS NOT NULL AND lease_expires_at IS NOT NULL) OR (status <> 'running' AND owner_id IS NULL AND lease_expires_at IS NULL)", name="ck_ingestion_job_owner"),
        sa.CheckConstraint("deadline_at > created_at", name="ck_ingestion_job_deadline"),
        comment="显式批准范围的有限导入任务；没有默认来源或后台领取入口。")
    op.create_index("ix_ingestion_job_owner", "knowledge_ingestion_jobs",
        ["tenant_id", "requested_by", "created_at", "id"])
    op.create_table("knowledge_ingestion_objects",
        sa.Column("id", sa.Uuid(), primary_key=True, comment="绑定ID。"),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False, comment="固定租户。"),
        sa.Column("source_id", sa.Uuid(), sa.ForeignKey("knowledge_sources.id"), nullable=False, comment="固定来源。"),
        sa.Column("identity_policy_digest", sa.String(64), nullable=False, comment="来源身份规范化策略摘要。"),
        sa.Column("object_key", sa.String(64), nullable=False, comment="来源对象域隔离摘要，不保存路径或外部ID。"),
        sa.Column("document_id", sa.Uuid(), sa.ForeignKey("knowledge_documents.id"), nullable=False, comment="唯一绑定的同租户同来源文档。"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, comment="首次绑定时间。"),
        sa.UniqueConstraint("tenant_id", "source_id", "identity_policy_digest", "object_key", name="uq_ingestion_object_identity"),
        sa.UniqueConstraint("document_id", name="uq_ingestion_object_document"),
        comment="稳定来源对象绑定；不可删除或改绑，避免重复拉取创建第二文档。")
    op.create_table("knowledge_ingestion_materializations",
        sa.Column("id", sa.Uuid(), primary_key=True, comment="物化事实ID。"),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False, comment="固定租户。"),
        sa.Column("source_id", sa.Uuid(), sa.ForeignKey("knowledge_sources.id"), nullable=False, comment="固定来源。"),
        sa.Column("object_id", sa.Uuid(), sa.ForeignKey("knowledge_ingestion_objects.id"), nullable=False, comment="稳定对象绑定。"),
        sa.Column("job_id", sa.Uuid(), sa.ForeignKey("knowledge_ingestion_jobs.id"), nullable=False, comment="首次物化任务。"),
        sa.Column("draft_key", sa.String(64), nullable=False, comment="内容/元数据/权限/解析策略的草稿意向键。"),
        sa.Column("version_id", sa.Uuid(), sa.ForeignKey("knowledge_versions.id"), nullable=False, comment="首次创建的不可变候选知识版本。"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, comment="首次物化时间。"),
        sa.UniqueConstraint("tenant_id", "source_id", "draft_key", name="uq_ingestion_materialization_draft"),
        sa.UniqueConstraint("version_id", name="uq_ingestion_materialization_version"),
        comment="追加式草稿物化事实，不自动发布、审核或更改文档ACL。")
    op.create_table("knowledge_ingestion_pages",
        sa.Column("id", sa.Uuid(), primary_key=True, comment="页面回执ID。"),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False, comment="固定租户。"),
        sa.Column("job_id", sa.Uuid(), sa.ForeignKey("knowledge_ingestion_jobs.id"), nullable=False, comment="固定任务。"),
        sa.Column("page_key", sa.String(64), nullable=False, comment="同Job输入检查点的确定性键。"),
        sa.Column("request_digest", sa.String(64), nullable=False, comment="规范候选输入摘要，不含owner/revision/generation。"),
        sa.Column("result_digest", sa.String(64), nullable=False, comment="不可变完整页面结果摘要。"),
        sa.Column("receipt", postgresql.JSONB(), nullable=False, comment="严格页面回执，仅目标ID/摘要/状态，无正文。"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, comment="服务器提交时间。"),
        sa.UniqueConstraint("job_id", "page_key", name="uq_ingestion_page_key"),
        comment="与草稿、物化、审计和检查点同事务提交的只含元数据页面回执。")
    op.execute("""CREATE FUNCTION supportops_guard_ingestion_fact() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'ingestion facts are append only'; END; $$""")
    for table in ("knowledge_ingestion_objects", "knowledge_ingestion_materializations", "knowledge_ingestion_pages"):
        op.execute(f"CREATE TRIGGER ingestion_fact_immutable BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION supportops_guard_ingestion_fact()")
    op.execute("""CREATE FUNCTION supportops_guard_ingestion_job() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
        IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'ingestion job history cannot be deleted'; END IF;
        IF ROW(NEW.id,NEW.tenant_id,NEW.source_id,NEW.requested_by,NEW.idempotency_key,NEW.request_digest,
            NEW.definition,NEW.definition_digest,NEW.created_at,NEW.deadline_at) IS DISTINCT FROM
            ROW(OLD.id,OLD.tenant_id,OLD.source_id,OLD.requested_by,OLD.idempotency_key,OLD.request_digest,
            OLD.definition,OLD.definition_digest,OLD.created_at,OLD.deadline_at) THEN
            RAISE EXCEPTION 'ingestion definition is immutable'; END IF;
        IF NEW IS NOT DISTINCT FROM OLD THEN RETURN NEW; END IF;
        IF OLD.status IN ('completed','failed','cancelled') OR NEW.revision <> OLD.revision + 1
            OR NEW.generation < OLD.generation OR NEW.attempts < OLD.attempts THEN
            RAISE EXCEPTION 'invalid ingestion history transition'; END IF;
        IF NOT ((OLD.status IN ('queued','retry_wait') AND NEW.status IN ('running','failed','cancelled'))
            OR (OLD.status = 'running' AND NEW.status IN ('running','retry_wait','completed','failed','cancelled')))
            THEN RAISE EXCEPTION 'invalid ingestion state transition'; END IF;
        RETURN NEW; END; $$""")
    op.execute("CREATE TRIGGER ingestion_job_guard BEFORE UPDATE OR DELETE ON knowledge_ingestion_jobs "
        "FOR EACH ROW EXECUTE FUNCTION supportops_guard_ingestion_job()")
    op.execute("""CREATE FUNCTION supportops_check_ingestion_binding() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
        IF TG_TABLE_NAME = 'knowledge_ingestion_jobs' THEN
            IF NOT EXISTS (SELECT 1 FROM knowledge_sources WHERE id=NEW.source_id AND tenant_id=NEW.tenant_id)
                OR NOT EXISTS (SELECT 1 FROM users WHERE id=NEW.requested_by AND tenant_id=NEW.tenant_id) THEN
                RAISE EXCEPTION 'invalid ingestion job binding'; END IF;
        ELSIF TG_TABLE_NAME = 'knowledge_ingestion_objects' THEN
            PERFORM 1 FROM knowledge_sources WHERE id=NEW.source_id AND tenant_id=NEW.tenant_id FOR UPDATE;
            IF NOT FOUND THEN RAISE EXCEPTION 'invalid ingestion source binding'; END IF;
            IF NOT EXISTS (SELECT 1 FROM knowledge_documents WHERE id=NEW.document_id
                AND tenant_id=NEW.tenant_id AND source_id=NEW.source_id) OR EXISTS
                (SELECT 1 FROM knowledge_ingestion_objects WHERE tenant_id=NEW.tenant_id AND source_id=NEW.source_id
                AND identity_policy_digest<>NEW.identity_policy_digest) THEN
                RAISE EXCEPTION 'invalid ingestion object binding'; END IF;
        ELSIF TG_TABLE_NAME = 'knowledge_ingestion_materializations' THEN
            IF NOT EXISTS (SELECT 1 FROM knowledge_ingestion_objects o JOIN knowledge_versions v
                ON v.document_id=o.document_id JOIN knowledge_ingestion_jobs j ON j.id=NEW.job_id
                WHERE o.id=NEW.object_id AND o.tenant_id=NEW.tenant_id AND o.source_id=NEW.source_id
                AND v.id=NEW.version_id AND v.tenant_id=NEW.tenant_id
                AND j.tenant_id=NEW.tenant_id AND j.source_id=NEW.source_id) THEN
                RAISE EXCEPTION 'invalid ingestion materialization binding'; END IF;
        ELSIF NOT EXISTS (SELECT 1 FROM knowledge_ingestion_jobs WHERE id=NEW.job_id AND tenant_id=NEW.tenant_id) THEN
            RAISE EXCEPTION 'invalid ingestion page binding'; END IF;
        RETURN NEW; END; $$""")
    for table in ("knowledge_ingestion_jobs", "knowledge_ingestion_objects", "knowledge_ingestion_materializations", "knowledge_ingestion_pages"):
        op.execute(f"CREATE TRIGGER ingestion_binding_guard BEFORE INSERT ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION supportops_check_ingestion_binding()")


def downgrade() -> None:
    op.execute("""DO $$ BEGIN IF EXISTS (SELECT 1 FROM knowledge_ingestion_jobs)
        OR EXISTS (SELECT 1 FROM knowledge_ingestion_objects)
        OR EXISTS (SELECT 1 FROM knowledge_ingestion_materializations)
        OR EXISTS (SELECT 1 FROM knowledge_ingestion_pages) THEN
        RAISE EXCEPTION 'ingestion history must be preserved before downgrade'; END IF; END $$""")
    for table in ("knowledge_ingestion_pages", "knowledge_ingestion_materializations", "knowledge_ingestion_objects"):
        op.execute(f"DROP TRIGGER ingestion_binding_guard ON {table}")
        op.execute(f"DROP TRIGGER ingestion_fact_immutable ON {table}")
        op.drop_table(table)
    op.execute("DROP TRIGGER ingestion_job_guard ON knowledge_ingestion_jobs")
    op.execute("DROP TRIGGER ingestion_binding_guard ON knowledge_ingestion_jobs")
    op.drop_table("knowledge_ingestion_jobs")
    op.execute("DROP FUNCTION supportops_check_ingestion_binding()")
    op.execute("DROP FUNCTION supportops_guard_ingestion_fact()")
    op.execute("DROP FUNCTION supportops_guard_ingestion_job()")
