"""在显式隔离、完整迁移的 PostgreSQL 测试库重跑同一知识安全契约。"""

import os
from collections.abc import AsyncIterator

import pytest
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine
from supportops_core.config import Settings
from supportops_core.db import create_engine

from tests.api import test_knowledge_access as contract
from tests.api.test_knowledge_access import knowledge_case as knowledge_case

test_admin_projection = (
    contract.test_admin_governance_metadata_does_not_grant_version_or_published_body
)
test_audit_failure = contract.test_audit_failure_keeps_denial_and_logs_no_exception
test_audit_rollback = contract.test_denial_audit_commits_independently_after_request_rollback
test_audit_digests = contract.test_denial_audit_records_only_tenant_scoped_digests
test_evidence_acl = contract.test_evidence_rechecks_current_acl_and_original_owner
test_history_sql = contract.test_restricted_history_sql_does_not_load_body_or_reviewer_text
test_snapshot_acl = contract.test_snapshot_acl_uses_any_matching_role_user_or_direct_department
test_write_receipts = contract.test_version_write_receipts_also_hide_unapproved_body
test_read_failure = contract.test_read_infrastructure_failure_is_not_acl_denial
test_acl_configuration_read = (
    contract.test_acl_configuration_read_is_complete_tenant_scoped_and_no_store
)
test_acl_configuration_hidden = contract.test_acl_configuration_read_hides_unavailable_documents
test_acl_configuration_admin = (
    contract.test_acl_configuration_read_requires_admin_and_authentication
)

pytestmark = pytest.mark.skipif(
    os.getenv("SUPPORTOPS_RUN_POSTGRES_TESTS") != "1", reason="需显式启用隔离 PostgreSQL 测试库"
)


@pytest.fixture
def settings() -> Settings:
    value = Settings()
    url = make_url(value.database_url)
    assert value.environment == "test" and url.drivername == "postgresql+asyncpg"
    assert url.database and url.database.endswith("_test")
    return value


@pytest.fixture
async def engine(settings: Settings) -> AsyncIterator[AsyncEngine]:
    value = create_engine(settings)
    try:
        yield value
    finally:
        await value.dispose()
