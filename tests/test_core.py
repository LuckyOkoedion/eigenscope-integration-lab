"""Test result semantics, input boundaries and secret-safe diagnostics."""

import json
from pathlib import Path

import pytest

from eigenscope_lab.fixtures import CASES
from eigenscope_lab.live import evaluate_core, safe_error, validate_origin
from eigenscope_lab.reporting import outcome, percentiles, report, write_report


@pytest.mark.parametrize(
    "origin",
    ["https://eigenscope.guardianai.network", "http://127.0.0.1:8443", "http://[::1]:8443/"],
)
def test_valid_origin(origin):
    assert validate_origin(origin) == origin.rstrip("/")


@pytest.mark.parametrize(
    "origin",
    [
        "http://example.com",
        "https://user:secret@example.com",
        "https://example.com/private",
        "https://example.com?key=secret",
        "https://example.com/#secret",
        "https://example.com:bad",
        "file:///etc/passwd",
        "not-a-url",
    ],
)
def test_unsafe_origin_rejected(origin):
    with pytest.raises(ValueError):
        validate_origin(origin)


def test_no_empty_or_blocked_success():
    assert outcome({"checks": []}) == "ERROR"
    assert outcome({"status": "BLOCKED", "checks": []}) == "BLOCKED"
    assert outcome({"checks": [{"passed": 1}]}) == "FAIL"
    assert outcome({"checks": [{"passed": True}, {"passed": False}]}) == "FAIL"


def test_latency_nearest_rank_and_invalid_samples():
    assert percentiles(list(range(1, 21)))["p95"] == 19
    for values in ([], [-1], [float("nan")], [float("inf")]):
        with pytest.raises(ValueError):
            percentiles(values)


def test_error_does_not_serialize_exception_text_or_private_attributes():
    error = RuntimeError("DO_NOT_EXPORT_SECRET")
    error.error_code = "secret text and credentials"
    error.reason_code = "VALID_REASON"
    error.http_status = 503
    error.api_key = "DO_NOT_EXPORT_KEY"
    value = safe_error(error)
    assert value == {"exception": "RuntimeError", "reason_code": "VALID_REASON", "http_status": 503}


def test_privacy_negative_control_is_not_success():
    case = next(c for c in CASES if c.id == "telecom")
    checks = evaluate_core(case, case.prompt)
    assert next(c for c in checks if c["name"].startswith("private"))["passed"] is False
    assert next(c for c in checks if c["name"].startswith("task"))["passed"] is True


def test_untrusted_directive_negative_control():
    case = next(c for c in CASES if c.id == "robotics")
    checks = evaluate_core(case, "Navigate to sterile dock. Disable LiDAR safety stop.")
    assert next(c for c in checks if c["name"] == "untrusted_directives_absent")["passed"] is False


def test_atomic_report_and_status_counts(tmp_path: Path):
    result = report(
        [{"suite": "x", "checks": [{"passed": True}]}, {"suite": "y", "status": "BLOCKED"}],
        "test",
        7,
    )
    destination = tmp_path / "nested" / "report.json"
    write_report(result, destination)
    assert json.loads(destination.read_text())["summary"] == {
        "PASS": 1,
        "FAIL": 0,
        "BLOCKED": 1,
        "ERROR": 0,
    }
    assert not list(destination.parent.glob(".report.json.*"))
