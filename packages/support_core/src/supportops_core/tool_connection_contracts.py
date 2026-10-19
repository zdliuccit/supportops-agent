"""隔离连接状态/一次探测契约；不提供业务执行或自报通过入口。"""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, StrictBool, StrictInt

from supportops_core.tool_discovery_contracts import DiscoveryContract, SourceKey


class ConnectionCommand(DiscoveryContract):
    expected_revision: Annotated[StrictInt, Field(ge=0)]
    expected_source_record_id: UUID
    confirm: StrictBool


class ProbeCancelCommand(DiscoveryContract):
    expected_revision: Annotated[StrictInt, Field(ge=1)]


class ConnectionView(DiscoveryContract):
    schema_version: Literal["tool_connection_view_v1"] = "tool_connection_view_v1"
    source_key: SourceKey
    source_record_id: UUID
    revision: int
    circuit_state: Literal["closed", "open", "half_open"]
    health_state: Literal["unknown", "healthy", "unhealthy", "stale"]
    failure_count: int
    checked_at: datetime | None = None
    valid_until: datetime | None = None
    retry_after: datetime | None = None
    latest_probe_id: UUID | None = None
    probe_available: bool = False
    unavailable_reason: str | None = None
    provider: Literal["isolated_mock_stdio_v1"] = "isolated_mock_stdio_v1"
    business_execution_enabled: Literal[False] = False


class ProbeJobView(DiscoveryContract):
    schema_version: Literal["tool_connection_probe_v1"] = "tool_connection_probe_v1"
    id: UUID
    source_key: SourceKey
    source_record_id: UUID
    status: Literal["queued", "running", "passed", "failed", "cancelled"]
    revision: int
    error_code: str | None
    created_at: datetime
    updated_at: datetime
    expires_at: datetime
    business_execution_enabled: Literal[False] = False
