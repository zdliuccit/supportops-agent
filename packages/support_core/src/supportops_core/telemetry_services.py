"""数据库观测的受限 OTLP/HTTP JSON 投影；不参与业务提交或调用许可。"""

import asyncio
import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import and_, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from supportops_core.config import Settings
from supportops_core.models import AgentRun, AgentRunObservation

CONVENTION_VERSION = "supportops_otel_v1"
_KINDS = frozenset({"agent", "llm", "tool", "retrieval", "api", "client"})
_STATUSES = frozenset({"completed", "failed", "cancelled", "succeeded", "denied", "unknown"})
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_MAX_INT64 = 2**63 - 1


class TelemetryExportError(Exception):
    """只携带固定错误码；外部错误、配置和响应正文不得进入日志。"""


@dataclass(frozen=True, slots=True)
class TelemetryCursor:
    finished_at: datetime
    observation_id: UUID


@dataclass(frozen=True, slots=True)
class TelemetryBatch:
    payload: bytes
    scanned: int
    exported: int
    skipped: int
    has_more: bool
    next_cursor: TelemetryCursor | None


def require_export_scope(settings: Settings, tenant_id: UUID) -> bytes:
    """维护身份须由服务端配置显式批准租户；平台角色不扩大范围。"""
    if not settings.telemetry_export_enabled:
        raise TelemetryExportError("TELEMETRY_DISABLED")
    if tenant_id not in settings.telemetry_allowed_tenants:
        raise TelemetryExportError("TELEMETRY_TENANT_DENIED")
    if settings.telemetry_convention_version != CONVENTION_VERSION:
        raise TelemetryExportError("TELEMETRY_CONVENTION_UNSUPPORTED")
    secret = settings.telemetry_hmac_key
    if secret is None or len(secret.get_secret_value().encode("utf-8")) < 32:
        raise TelemetryExportError("TELEMETRY_HMAC_KEY_REQUIRED")
    key = secret.get_secret_value().encode("utf-8")
    other_secrets = [settings.jwt_secret, settings.secret_encryption_key]
    if settings.telemetry_bearer_token is not None:
        other_secrets.append(settings.telemetry_bearer_token.get_secret_value())
    if any(hmac.compare_digest(key, value.encode("utf-8")) for value in other_secrets):
        raise TelemetryExportError("TELEMETRY_HMAC_KEY_REUSED")
    return key


def _opaque_id(key: bytes, tenant_id: UUID, kind: str, value: UUID, length: int = 32) -> str:
    # 不使用客户端 correlation_id、工具名称或原始 provider request ID 生成公开标识。
    raw = f"{CONVENTION_VERSION}:{tenant_id.hex}:{kind}:{value.hex}".encode("ascii")
    result = hmac.new(key, raw, hashlib.sha256).hexdigest()[:length]
    return result if int(result, 16) else "1" + result[1:]


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _nanoseconds(value: datetime) -> str:
    delta = _utc(value) - _EPOCH
    return str((delta.days * 86400 + delta.seconds) * 1_000_000_000 + delta.microseconds * 1000)


def _attribute(name: str, value: str | int | bool) -> dict[str, object]:
    encoded: dict[str, object]
    if isinstance(value, bool):
        encoded = {"boolValue": value}
    elif isinstance(value, int):
        encoded = {"intValue": str(value)}
    else:
        encoded = {"stringValue": value}
    return {"key": name, "value": encoded}


def _valid_metric(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= _MAX_INT64


def _metric(attributes: list[dict[str, object]], name: str, value: object) -> None:
    if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= _MAX_INT64:
        attributes.append(_attribute(name, value))


def _span(
    observation: AgentRunObservation,
    *,
    key: bytes,
    tenant_id: UUID,
    agent_version_id: UUID,
    parent_id: UUID | None,
) -> dict[str, object] | None:
    """封闭字段投影；自由文本即使通过脱敏也不转发。"""
    row = observation
    if row.kind not in _KINDS or row.status not in _STATUSES or row.finished_at is None:
        return None
    start, finish = _utc(row.started_at), _utc(row.finished_at)
    if start < _EPOCH or finish < start or finish > datetime.now(UTC) + timedelta(seconds=5):
        return None
    attributes = [
        _attribute("supportops.observation.kind", row.kind),
        _attribute("supportops.observation.status", row.status),
        _attribute("supportops.run.id", _opaque_id(key, tenant_id, "run", row.run_id)),
        _attribute("supportops.agent.version", _opaque_id(key, tenant_id, "agent_version", agent_version_id)),
    ]
    if row.attempt_id is not None:
        attributes.append(_attribute("supportops.attempt.id", _opaque_id(key, tenant_id, "attempt", row.attempt_id)))
    if row.model_version_id is not None:
        attributes.append(_attribute("supportops.model.version", _opaque_id(key, tenant_id, "model_version", row.model_version_id)))
    # 仅模型叶子观测携带用量，父级聚合不再导出，避免接收端重复求和。
    if row.kind == "llm":
        _metric(attributes, "supportops.model.input_tokens", row.input_tokens)
        _metric(attributes, "supportops.model.output_tokens", row.output_tokens)
        _metric(attributes, "supportops.model.cost_microusd", row.total_cost_microusd)
        attributes.append(_attribute("supportops.model.usage_available", _valid_metric(row.input_tokens) and _valid_metric(row.output_tokens)))
        attributes.append(_attribute("supportops.model.cost_available", _valid_metric(row.total_cost_microusd)))
    if row.first_output_at is not None and start <= _utc(row.first_output_at) <= finish:
        _metric(attributes, "supportops.first_output.elapsed_ms", int((_utc(row.first_output_at) - start).total_seconds() * 1000))
    metadata = row.metadata_payload if isinstance(row.metadata_payload, dict) else {}
    if row.kind in {"tool", "retrieval"}:
        _metric(attributes, "supportops.tool.implementation_version", metadata.get("implementation_version"))
        _metric(attributes, "supportops.tool.catalog_version", metadata.get("catalog_version"))
        # 工具 ID 仅作租户内不可逆关联，未知/恶意目录名称不能变成外传文本。
        if isinstance(row.tool_id, str):
            tool_digest = hmac.new(key, f"{tenant_id.hex}:tool:{row.tool_id}".encode(), hashlib.sha256).hexdigest()[:32]
            attributes.append(_attribute("supportops.tool.id", tool_digest))
    if row.kind == "client":
        _metric(attributes, "supportops.client.elapsed_ms", metadata.get("client_elapsed_ms"))
        attributes.extend([_attribute("supportops.measurement.trusted", False),
            _attribute("supportops.measurement.boundary", "local_submit_to_react_commit")])
    elif row.kind == "api":
        attributes.extend([_attribute("supportops.measurement.boundary", "run_created_to_first_sse_read_of_committed_text"),
            _attribute("supportops.measurement.upper_bound", True)])
    span: dict[str, object] = {
        "traceId": _opaque_id(key, tenant_id, "trace", row.run_id),
        "spanId": _opaque_id(key, tenant_id, "span", row.id, 16),
        "name": f"supportops.{row.kind}",
        "kind": 1,
        "startTimeUnixNano": _nanoseconds(start),
        "endTimeUnixNano": _nanoseconds(finish),
        "attributes": attributes,
        "status": {"code": 2 if row.status in {"failed", "denied"} else 0},
    }
    if parent_id is not None and parent_id != row.id:
        span["parentSpanId"] = _opaque_id(key, tenant_id, "span", parent_id, 16)
    return span


async def build_telemetry_batch(
    session: AsyncSession,
    *,
    settings: Settings,
    tenant_id: UUID,
    start: datetime,
    end: datetime,
    after: TelemetryCursor | None = None,
) -> TelemetryBatch:
    """只读有限批次；完成时间左闭右开。游标不是可靠投递/快照保证。"""
    key = require_export_scope(settings, tenant_id)
    if start.tzinfo is None or end.tzinfo is None:
        raise TelemetryExportError("TELEMETRY_TIMEZONE_REQUIRED")
    start, end = _utc(start), _utc(end)
    if start < _EPOCH or not start < end or end - start > timedelta(hours=24) or end > datetime.now(UTC):
        raise TelemetryExportError("TELEMETRY_WINDOW_INVALID")
    filters = [AgentRunObservation.tenant_id == tenant_id, AgentRun.tenant_id == tenant_id,
        AgentRunObservation.finished_at >= start, AgentRunObservation.finished_at < end]
    if after is not None:
        if after.finished_at.tzinfo is None or not start <= _utc(after.finished_at) < end:
            raise TelemetryExportError("TELEMETRY_CURSOR_INVALID")
        filters.append(or_(AgentRunObservation.finished_at > after.finished_at,
            and_(AgentRunObservation.finished_at == after.finished_at, AgentRunObservation.id > after.observation_id)))
    async with asyncio.timeout(settings.telemetry_timeout_seconds):
        if session.bind is not None and session.bind.dialect.name == "postgresql":
            # 当前命令独立事务，只读标记在第一条查询前设置，不与产品事务共用。
            await session.execute(text("SET TRANSACTION READ ONLY"))
            await session.execute(text("SET LOCAL statement_timeout = '5000ms'"))
        rows = (await session.execute(select(AgentRunObservation, AgentRun.agent_version_id)
            .join(AgentRun, AgentRun.id == AgentRunObservation.run_id).where(*filters)
            .order_by(AgentRunObservation.finished_at, AgentRunObservation.id)
            .limit(settings.telemetry_batch_size + 1))).all()
        has_more = len(rows) > settings.telemetry_batch_size
        rows = rows[:settings.telemetry_batch_size]
        # 批外父观测也校验同租户/Run/attempt，避免损坏关联连到其他主体。
        parent_ids = {row.parent_observation_id for row, _ in rows if row.parent_observation_id is not None}
        parents = (await session.execute(select(AgentRunObservation.id, AgentRunObservation.run_id,
            AgentRunObservation.attempt_id).where(AgentRunObservation.tenant_id == tenant_id,
                AgentRunObservation.id.in_(parent_ids)))).all() if parent_ids else []
    parent_scopes = {identifier: (run_id, attempt_id) for identifier, run_id, attempt_id in parents}
    spans: list[dict[str, object]] = []
    for row, version_id in rows:
        parent_id = row.parent_observation_id
        if parent_scopes.get(parent_id) != (row.run_id, row.attempt_id):
            parent_id = None
        span = _span(row, key=key, tenant_id=tenant_id, agent_version_id=version_id, parent_id=parent_id)
        if span is not None:
            spans.append(span)
    payload = json.dumps({"resourceSpans": [{"resource": {"attributes": [
        _attribute("service.name", "supportops-runtime"),
        _attribute("supportops.tenant.id", _opaque_id(key, tenant_id, "tenant", tenant_id)),
        _attribute("supportops.convention.version", CONVENTION_VERSION),
    ]}, "scopeSpans": [{"scope": {"name": "supportops.database_observations", "version": CONVENTION_VERSION},
        "spans": spans}]}]}, ensure_ascii=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    if len(payload) > settings.telemetry_max_payload_bytes:
        raise TelemetryExportError("TELEMETRY_PAYLOAD_TOO_LARGE")
    last = rows[-1][0] if rows else None
    cursor = TelemetryCursor(_utc(last.finished_at), last.id) if last is not None and last.finished_at is not None else None
    return TelemetryBatch(payload, len(rows), len(spans), len(rows) - len(spans), has_more, cursor)
