"""本人导入创建请求停止事实及迟到创建围栏；不读取来源或变更已有Job。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261017_0048"
down_revision: str | None = "20261015_0047"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table("knowledge_ingestion_creation_stops",
        sa.Column("id", sa.Uuid(), primary_key=True, comment="停止事实唯一标识。"),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False, comment="当前可信租户。"),
        sa.Column("requested_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False, comment="当前本人，不允许管理员代办。"),
        sa.Column("request_key", sa.String(200), nullable=False, comment="本人原创建请求键，无来源路径或正文。"),
        sa.Column("record_digest", sa.String(64), nullable=False, comment="停止身份/请求/时间的完整性摘要。"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, comment="服务器实际保存停止事实的时间。"),
        sa.UniqueConstraint("tenant_id", "requested_by", "request_key", name="uq_ingestion_creation_stop_request"),
        sa.CheckConstraint("length(request_key) BETWEEN 1 AND 200", name="ck_ingestion_creation_stop_key"),
        comment="追加式创建请求停止事实，与当前主体锁串行，禁止迟到请求重新建任务。")
    op.execute("""CREATE FUNCTION supportops_guard_ingestion_creation_stop() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
        IF TG_OP <> 'INSERT' THEN RAISE EXCEPTION 'ingestion creation stop is append only'; END IF;
        IF current_setting('transaction_isolation') <> 'read committed' THEN
            RAISE EXCEPTION 'ingestion creation fencing requires read committed'; END IF;
        PERFORM 1 FROM users WHERE id=NEW.requested_by AND tenant_id=NEW.tenant_id FOR UPDATE;
        IF NOT FOUND THEN RAISE EXCEPTION 'invalid ingestion creation stop subject'; END IF;
        IF EXISTS (SELECT 1 FROM knowledge_ingestion_jobs WHERE tenant_id=NEW.tenant_id
            AND requested_by=NEW.requested_by AND idempotency_key=NEW.request_key) THEN
            RAISE EXCEPTION 'ingestion creation already has a job'; END IF;
        RETURN NEW; END; $$""")
    op.execute("""CREATE TRIGGER ingestion_creation_stop_guard BEFORE INSERT OR UPDATE OR DELETE
        ON knowledge_ingestion_creation_stops FOR EACH ROW EXECUTE FUNCTION supportops_guard_ingestion_creation_stop()""")
    op.execute("""CREATE FUNCTION supportops_fence_ingestion_creation() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
        IF current_setting('transaction_isolation') <> 'read committed' THEN
            RAISE EXCEPTION 'ingestion creation fencing requires read committed'; END IF;
        PERFORM 1 FROM users WHERE id=NEW.requested_by AND tenant_id=NEW.tenant_id FOR UPDATE;
        IF NOT FOUND OR EXISTS (SELECT 1 FROM knowledge_ingestion_creation_stops WHERE tenant_id=NEW.tenant_id
            AND requested_by=NEW.requested_by AND request_key=NEW.idempotency_key) THEN
            RAISE EXCEPTION 'ingestion creation request is stopped or invalid'; END IF;
        RETURN NEW; END; $$""")
    op.execute("""CREATE TRIGGER ingestion_creation_fence BEFORE INSERT ON knowledge_ingestion_jobs
        FOR EACH ROW EXECUTE FUNCTION supportops_fence_ingestion_creation()""")


def downgrade() -> None:
    op.execute("""DO $$ BEGIN IF EXISTS (SELECT 1 FROM knowledge_ingestion_creation_stops) THEN
        RAISE EXCEPTION 'creation stop facts must be preserved before downgrade'; END IF; END $$""")
    op.execute("DROP TRIGGER ingestion_creation_fence ON knowledge_ingestion_jobs")
    op.execute("DROP FUNCTION supportops_fence_ingestion_creation()")
    op.drop_table("knowledge_ingestion_creation_stops")
    op.execute("DROP FUNCTION supportops_guard_ingestion_creation_stop()")
