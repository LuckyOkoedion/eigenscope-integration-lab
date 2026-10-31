"""Lazy scenario loading keeps the base CLI usable without heavy extras."""

from __future__ import annotations

import importlib
import shutil
from typing import Any

SCENARIOS = {
    "provider": ("provider", "run_provider", "provider"),
    "coding": ("coding", "run_coding", None),
    "tools": ("tools", "run_tools", "tools"),
    "telecom": ("telecom", "run_telecom", "telecom"),
    "robotics": ("robotics", "run_robotics", "robotics"),
}


def run_scenario(name: str, seed: int) -> dict[str, Any]:
    module, function, extra = SCENARIOS[name]
    if name == "coding" and shutil.which("git") is None:
        return {
            "suite": name,
            "status": "BLOCKED",
            "checks": [],
            "reason": "missing_runtime_prerequisite",
            "dependency": "git",
            "install": "Install Git 2.28 or newer and add it to PATH",
        }
    try:
        implementation = importlib.import_module(f"eigenscope_lab.simulators.{module}")
        return getattr(implementation, function)(seed=seed)
    except Exception as exc:
        # Adapters may wrap a lazy import failure with setup guidance. Preserve
        # prerequisite attribution while keeping unrelated bugs as harness errors.
        current: BaseException | None = exc
        seen: set[int] = set()
        while current is not None and id(current) not in seen and len(seen) < 8:
            seen.add(id(current))
            if isinstance(current, ImportError):
                return {
                    "suite": name,
                    "status": "BLOCKED",
                    "checks": [],
                    "reason": "missing_dependency",
                    "dependency": current.name,
                    "install": f"uv sync --extra {extra}" if extra else "Reinstall the lab package",
                }
            current = current.__cause__ or current.__context__
        return {"suite": name, "status": "ERROR", "checks": [], "reason": type(exc).__name__}
