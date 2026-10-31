"""Portable machine-readable results without credentials, tickets or raw exceptions."""

from __future__ import annotations

import datetime as dt
import importlib.metadata
import json
import math
import os
import platform
import statistics
import tempfile
from pathlib import Path
from typing import Any


def distribution_version(name: str) -> str:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return "not-installed"


def percentiles(samples: list[float]) -> dict[str, float | int | str]:
    """Nearest-rank p95, with population/sample boundaries carried by the caller."""
    if not samples or any(not math.isfinite(x) or x < 0 for x in samples):
        raise ValueError("Timing samples must be nonempty finite nonnegative milliseconds")
    ordered = sorted(samples)
    return {
        "n": len(samples),
        "unit": "ms",
        "median": round(statistics.median(ordered), 3),
        "p95": round(ordered[math.ceil(0.95 * len(ordered)) - 1], 3),
        "min": round(ordered[0], 3),
        "max": round(ordered[-1], 3),
    }


def outcome(suite: dict[str, Any]) -> str:
    if suite.get("status") in {"BLOCKED", "ERROR"}:
        return str(suite["status"])
    checks = suite.get("checks", [])
    if not checks:
        return "ERROR"
    return "PASS" if all(check.get("passed") is True for check in checks) else "FAIL"


def report(suites: list[dict[str, Any]], mode: str, seed: int) -> dict[str, Any]:
    normalized = [{**suite, "status": outcome(suite)} for suite in suites]
    return {
        "schema_version": "1.0",
        "generated_at_utc": dt.datetime.now(dt.UTC).isoformat(),
        "lab_version": distribution_version("eigenscope-integration-lab"),
        "environment": {
            "python": platform.python_version(),
            "platform": platform.system(),
            "machine": platform.machine(),
        },
        "mode": mode,
        "seed": seed,
        "summary": {
            status: sum(row["status"] == status for row in normalized)
            for status in ("PASS", "FAIL", "BLOCKED", "ERROR")
        },
        "suites": normalized,
    }


def write_report(value: dict[str, Any], destination: str | Path) -> None:
    """Atomic replacement; reports may contain synthetic fixtures, never live credentials."""
    target = Path(destination)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    fd, temporary = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(payload)
        os.replace(temporary, target)
    finally:
        Path(temporary).unlink(missing_ok=True)
