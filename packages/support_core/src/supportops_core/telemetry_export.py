"""显式运行的遥测维护命令；默认只预览，不输出数据正文或自动调度。"""

import argparse
import asyncio
import hashlib
import json
import sys
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import NoReturn
from uuid import UUID

import httpx

from supportops_core.config import Settings
from supportops_core.db import create_engine, create_session_factory
from supportops_core.outbound_http import (
    OutboundHttpError,
    OutboundHttpPolicy,
    request_fixed_json,
)
from supportops_core.telemetry_services import (
    CONVENTION_VERSION,
    TelemetryBatch,
    TelemetryCursor,
    TelemetryExportError,
    build_telemetry_batch,
    require_export_scope,
)


@dataclass(frozen=True, slots=True)
class _Receipt:
    rejected_spans: int
    warning_present: bool


@dataclass(slots=True)
class _ExportState:
    batch: TelemetryBatch | None = None
    # 仅表示进入唯一发送路径，不证明字节已发出或接收端已持久化。
    send_attempted: bool = False
    receipt: _Receipt | None = None


def _unique_fields(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def _reject_constant(_value: str) -> NoReturn:
    raise ValueError


def _rejected_count(value: object) -> int:
    # ProtoJSON允许字符串/数字及指数形式，null等同未设置；不能经float丢失精度。
    if value is None:
        return 0
    if isinstance(value, bool) or not isinstance(value, (int, str, Decimal)):
        raise ValueError
    raw = str(value)
    if not 0 < len(raw) <= 128 or not raw.isascii() or raw != raw.strip() or "_" in raw:
        raise ValueError
    number = Decimal(raw)
    if (not number.is_finite() or not 0 <= number <= 2**63 - 1
        or number != number.to_integral_value()):
        raise ValueError
    return int(number)


def _receipt(raw: bytes) -> _Receipt:
    """只提取计数和警告存在性；不保留接收端自由文本或原解析异常。"""
    try:
        result: object = json.loads(raw, object_pairs_hook=_unique_fields,
            parse_float=Decimal, parse_constant=_reject_constant)
        if not isinstance(result, dict):
            raise ValueError
        partial = result.get("partialSuccess")
        if partial is None:
            return _Receipt(0, False)
        if not isinstance(partial, dict):
            raise ValueError
        count = _rejected_count(partial.get("rejectedSpans"))
        warning = partial.get("errorMessage")
        if warning is not None and not isinstance(warning, str):
            raise ValueError
        return _Receipt(count, bool(warning))
    except Exception:
        raise TelemetryExportError("TELEMETRY_RESPONSE_INVALID") from None


async def _send_payload(settings: Settings, payload: bytes) -> _Receipt:
    """单次 HTTPS 请求，固定解析 IP 与 TLS 主机；没有代理、重定向或自动重试。"""
    try:
        endpoint = httpx.URL(settings.telemetry_otlp_endpoint or "")
        if (endpoint.scheme != "https" or not endpoint.host or endpoint.userinfo
            or endpoint.query or endpoint.fragment or not endpoint.path.endswith("/v1/traces")):
            raise TelemetryExportError("TELEMETRY_ENDPOINT_INVALID")
        if not settings.telemetry_allowed_ips:
            raise TelemetryExportError("TELEMETRY_IP_ALLOWLIST_INVALID")
        response = await request_fixed_json(
            url=str(endpoint),
            policy=OutboundHttpPolicy(
                allowed_hosts=frozenset({endpoint.raw_host.decode("ascii")}),
                allowed_paths=frozenset({endpoint.raw_path.decode("ascii")}),
                allowed_ports=frozenset({endpoint.port if endpoint.port is not None else 443}),
                allowed_ips=frozenset(settings.telemetry_allowed_ips),
                private_ips=frozenset(settings.telemetry_allowed_ips),
            ),
            method="POST", payload=payload, bearer_token=settings.telemetry_bearer_token,
            timeout_seconds=settings.telemetry_timeout_seconds,
            max_request_bytes=settings.telemetry_max_payload_bytes,
        )
        # 部分成功绝不自动重发；命令报告数量，不输出接收端自由文本。
        return _receipt(response.body)
    except OutboundHttpError as exc:
        code = {
            "OUTBOUND_IP_POLICY_INVALID": "TELEMETRY_IP_ALLOWLIST_INVALID",
            "OUTBOUND_ADDRESS_DENIED": "TELEMETRY_ADDRESS_DENIED",
            "OUTBOUND_REDIRECT_DENIED": "TELEMETRY_RECEIVER_REJECTED",
            "OUTBOUND_HTTP_REJECTED": "TELEMETRY_RECEIVER_REJECTED",
            "OUTBOUND_RESPONSE_TYPE_INVALID": "TELEMETRY_RESPONSE_INVALID",
            "OUTBOUND_RESPONSE_ENCODING_DENIED": "TELEMETRY_RESPONSE_INVALID",
            "OUTBOUND_RESPONSE_TOO_LARGE": "TELEMETRY_RESPONSE_TOO_LARGE",
            "OUTBOUND_TOKEN_INVALID": "TELEMETRY_TOKEN_INVALID",
            "OUTBOUND_REQUEST_TOO_LARGE": "TELEMETRY_PAYLOAD_TOO_LARGE",
        }.get(exc.code, "TELEMETRY_TRANSPORT_FAILED")
        raise TelemetryExportError(code) from None
    except TelemetryExportError:
        raise
    except (ValueError, OSError, httpx.HTTPError):
        raise TelemetryExportError("TELEMETRY_TRANSPORT_FAILED") from None


def _time_argument(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            raise ValueError
        return parsed
    except ValueError:
        raise argparse.ArgumentTypeError("时间必须为带时区的 ISO 8601 格式") from None


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="预览单租户 OTLP 批次；仅 --send 发出网络请求")
    parser.add_argument("--tenant-id", type=UUID, required=True)
    parser.add_argument("--start", type=_time_argument, required=True)
    parser.add_argument("--end", type=_time_argument, required=True)
    parser.add_argument("--after-finished-at", type=_time_argument)
    parser.add_argument("--after-id", type=UUID)
    parser.add_argument("--send", action="store_true")
    args = parser.parse_args()
    if (args.after_finished_at is None) != (args.after_id is None):
        parser.error("分页需同时提供 --after-finished-at 和 --after-id")
    return args


async def _run(args: argparse.Namespace, state: _ExportState) -> None:
    settings = Settings()
    require_export_scope(settings, args.tenant_id)
    after = TelemetryCursor(args.after_finished_at, args.after_id) if args.after_id is not None else None
    engine = create_engine(settings)
    try:
        async with create_session_factory(engine)() as session, session.begin():
            state.batch = await build_telemetry_batch(session, settings=settings, tenant_id=args.tenant_id,
                start=args.start, end=args.end, after=after)
    finally:
        await engine.dispose()
    batch = state.batch
    if args.send and batch is not None and batch.exported:
        # DNS、TLS、读取与响应解析共同受整体时限约束，数据库事务已结束。
        state.send_attempted = True
        async with asyncio.timeout(settings.telemetry_timeout_seconds):
            receipt = await _send_payload(settings, batch.payload)
        if receipt.rejected_spans > batch.exported:
            raise TelemetryExportError("TELEMETRY_RESPONSE_INVALID")
        state.receipt = receipt


def _cursor(cursor: TelemetryCursor | None) -> dict[str, str] | None:
    return {"finished_at": cursor.finished_at.isoformat(), "observation_id": str(cursor.observation_id)} if cursor else None


def _report(args: argparse.Namespace, state: _ExportState, error: str | None) -> dict[str, object]:
    batch, receipt = state.batch, state.receipt
    if error is not None:
        status = "outcome_uncertain" if state.send_attempted else "blocked"
    elif not args.send:
        status = "preview"
    elif receipt is None:
        status = "not_sent"
    else:
        status = "partially_acknowledged" if receipt.rejected_spans else "acknowledged"
    # 部分成功/未知只有扫描定位，不产生可用于跳过未确认批次的成功游标。
    advance = error is None and (not args.send or not state.send_attempted or (
        receipt is not None and receipt.rejected_spans == 0
    ))
    return {
        "schema_version": "telemetry_export_report_v1",
        "mode": "send" if args.send else "preview", "delivery_status": status,
        "error": error, "convention_version": CONVENTION_VERSION,
        "send_attempted": state.send_attempted, "receiver_response_confirmed": receipt is not None,
        "receiver_warning_present": receipt.warning_present if receipt is not None else None,
        "automatic_retries_enabled": False, "reliable_delivery_guaranteed": False,
        "cursor_advance_allowed": advance,
        "requested_after": {"finished_at": args.after_finished_at.isoformat(), "observation_id": str(args.after_id)}
            if args.after_id is not None else None,
        "scanned": batch.scanned if batch is not None else None,
        "mapped_spans": batch.exported if batch is not None else None,
        "skipped": batch.skipped if batch is not None else None,
        "accepted_spans": batch.exported - receipt.rejected_spans if batch is not None and receipt is not None else None,
        "rejected_spans": receipt.rejected_spans if receipt is not None else None,
        "payload_bytes": len(batch.payload) if batch is not None else None,
        "payload_sha256": hashlib.sha256(batch.payload).hexdigest() if batch is not None else None,
        "has_more": batch.has_more if batch is not None else None,
        "batch_last_cursor": _cursor(batch.next_cursor) if batch is not None else None,
        "next_cursor": _cursor(batch.next_cursor) if advance and batch is not None else None,
    }


def main() -> int:
    args = _arguments()
    state = _ExportState()
    error: str | None = None
    exit_code = 0
    try:
        asyncio.run(_run(args, state))
        if state.receipt is not None and state.receipt.rejected_spans:
            exit_code = 2
    except TelemetryExportError as exc:
        error, exit_code = str(exc), 4 if state.send_attempted else 1
    except KeyboardInterrupt:
        error, exit_code = "TELEMETRY_INTERRUPTED", 4 if state.send_attempted else 130
    except Exception:
        # 配置校验/数据库/网络异常可能包含连接串、秘密或对端数据，禁止原样输出。
        error, exit_code = "TELEMETRY_EXPORT_UNAVAILABLE", 4 if state.send_attempted else 1
    try:
        print(json.dumps(_report(args, state, error), ensure_ascii=False),
            file=sys.stderr if error is not None else sys.stdout, flush=True)
    except (OSError, ValueError):
        # 无法交付报告也不能触发重发或把进入过发送的批次当作未发送。
        return 4 if state.send_attempted else 1
    except KeyboardInterrupt:
        return 4 if state.send_attempted else 130
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
