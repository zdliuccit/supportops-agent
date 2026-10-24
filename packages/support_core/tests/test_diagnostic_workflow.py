"""诊断数值边界与多源冲突不产生确定根因。"""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from supportops_core.diagnostic_contracts import DiagnosticReport
from supportops_core.diagnostic_mock_services import MockDiagnosticResponse, mock_request_ids
from supportops_core.diagnostic_workflow import answer_from_report, evaluate_facts


def responses(
    *,
    code="unknown",
    rpm=10,
    input_tokens=1000,
    output_tokens=2000,
    concurrency=1,
    remaining=100000,
    upstream=False,
):
    counts = {
        "rpm_count": rpm,
        "rpm_limit": 60,
        "input_tokens": input_tokens,
        "max_output_reserved_tokens": output_tokens,
        "tpm_limit": 40000,
        "concurrency": concurrency,
        "concurrency_limit": 4,
    }
    data = {
        "get_request_logs": {
            **counts,
            "http_status": 429,
            "error_class": code,
            "model_name": "model",
            "region": "region",
        },
        "get_current_user_profile": {
            **counts,
            "account_status": "active",
            "plan_remaining_tokens": remaining,
        },
        "get_model_route_status": {
            "model_name": "model",
            "region": "region",
            "route_status": "rate_limited" if upstream else "healthy",
            "incident_id": "incident" if upstream else None,
        },
        "list_active_incidents": {
            "model_name": "model",
            "region": "region",
            "incident_count": int(upstream),
            "incident_id": "incident" if upstream else None,
        },
    }
    return {
        tool: MockDiagnosticResponse(
            tool_id=tool,
            request_id="test-request",
            status="ok",
            observed_at=datetime.now(UTC),
            facts=facts,
        )
        for tool, facts in data.items()
    }


@pytest.mark.parametrize(
    "inputs,candidate",
    [
        ({"code": "rpm_exceeded", "rpm": 60}, None),
        ({"code": "rpm_exceeded", "rpm": 61}, "rpm_exceeded"),
        ({"code": "tpm_reservation_exceeded", "input_tokens": 10000, "output_tokens": 30000}, None),
        (
            {"code": "tpm_reservation_exceeded", "input_tokens": 10000, "output_tokens": 30001},
            "tpm_reservation_exceeded",
        ),
        ({"code": "project_concurrency_exceeded", "concurrency": 4}, None),
        (
            {"code": "project_concurrency_exceeded", "concurrency": 5},
            "project_concurrency_exceeded",
        ),
        ({"code": "plan_quota_exhausted", "remaining": 1}, None),
        ({"code": "plan_quota_exhausted", "remaining": 0}, "plan_quota_exhausted"),
        ({"code": "upstream_rate_limited", "upstream": True}, "upstream_rate_limited"),
    ],
)
def test_exact_limits_only_produce_unconfirmed_candidate_until_rule_is_authorized(
    inputs, candidate
):
    report = DiagnosticReport(run_id=uuid4(), attempt_id=uuid4())
    evaluate_facts(report, responses(**inputs))
    assert report.status == "unconfirmed"
    assert [item.code for item in report.candidates] == ([] if candidate is None else [candidate])
    assert all(item.confirmation == "unconfirmed" for item in report.candidates)
    assert "尚不能确认" in answer_from_report(report).answer


@pytest.mark.parametrize(
    "damage,status",
    [
        ("class", "unconfirmed"),
        ("counts", "conflict"),
        ("account", "conflict"),
        ("region", "conflict"),
        ("multiple", "conflict"),
        ("incident_id", "unconfirmed"),
        ("incident_count", "unconfirmed"),
        ("http_status", "unconfirmed"),
    ],
)
def test_inconsistent_or_ambiguous_facts_never_select_one_confirmed_root(damage, status):
    upstream = damage in {"incident_id", "incident_count"}
    data = responses(
        code="upstream_rate_limited" if upstream else "rpm_exceeded",
        upstream=upstream,
        rpm=10 if upstream else 75,
    )
    if damage == "class":
        data["get_request_logs"].facts["error_class"] = "unknown"
    elif damage == "counts":
        data["get_current_user_profile"].facts["rpm_count"] = 10
    elif damage == "account":
        data["get_current_user_profile"].facts["account_status"] = "suspended"
    elif damage == "region":
        data["get_model_route_status"].facts["region"] = "other-region"
    elif damage == "multiple":
        data["get_current_user_profile"].facts["plan_remaining_tokens"] = 0
    elif damage == "incident_id":
        data["list_active_incidents"].facts["incident_id"] = "other-incident"
    elif damage == "incident_count":
        data["list_active_incidents"].facts["incident_count"] = 0
    else:
        data["get_request_logs"].facts["http_status"] = 200
    report = DiagnosticReport(run_id=uuid4(), attempt_id=uuid4())
    evaluate_facts(report, data)
    assert report.status == status and report.candidates == []
    assert report.reason_codes and report.unresolved_items


@pytest.mark.parametrize(
    "content,expected",
    [
        ("request_id:mock-s01-rpm", {"mock-s01-rpm"}),
        ("请求ID：mock-s01-rpm", {"mock-s01-rpm"}),
        ("request id=MOCK-S01-RPM", {"MOCK-S01-RPM"}),
        ("request_id:another:mock-s01-rpm", {"another:mock-s01-rpm"}),
        ("request_id:" + "r" * 128, {"r" * 128}),
        ("request_id:" + "r" * 129, set()),
        ("mock-s01-" + "r" * 119, {"mock-s01-" + "r" * 119}),
        ("mock-s01-" + "r" * 120, set()),
    ],
)
def test_request_parser_preserves_full_identifiers_without_accepting_truncated_prefix(
    content, expected
):
    assert mock_request_ids(content) == expected
