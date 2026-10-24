"""在完整迁移的隔离 PostgreSQL 中运行三层状态契约。"""

import os

import pytest

from tests.api import test_dashboard as baseline
from tests.api import test_dashboard_states as contract
from tests.api.test_dashboard_states import state_case as state_case
from tests.postgres.test_knowledge_access import engine as engine
from tests.postgres.test_knowledge_access import settings as settings

test_three_states = contract.test_three_states_are_independent_and_waits_are_not_model_execution
test_lifecycle = (
    contract.test_non_active_lifecycle_preserves_inflight_execution_without_claiming_healthy
)
test_idle = contract.test_active_without_recent_work_explains_idle_or_unavailable_service
test_all_time_activity = (
    contract.test_current_activity_is_not_cut_off_by_historical_dashboard_window
)
test_attempt_dedup = (
    contract.test_old_attempts_and_multiple_runs_never_duplicate_business_or_agent_counts
)
test_unproven_attempt = (
    contract.test_unproven_current_attempt_cannot_be_reported_as_active_execution
)
test_read_only = contract.test_status_get_is_read_only_and_never_invents_health_without_snapshot
test_empty = baseline.test_dashboard_endpoints_return_empty_tenant_state
test_error_scope = baseline.test_error_event_list_and_detail_are_tenant_scoped
test_metric_scope = baseline.test_dashboard_metrics_keep_agent_scope_and_zero_usage_available

pytestmark = pytest.mark.skipif(
    os.getenv("SUPPORTOPS_RUN_POSTGRES_TESTS") != "1", reason="需显式启用隔离 PostgreSQL 测试库"
)
