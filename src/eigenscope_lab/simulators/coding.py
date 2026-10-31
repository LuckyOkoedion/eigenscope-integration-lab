"""A real, isolated Git worktree and Python test workflow for coding agents."""

from __future__ import annotations

import ast
import os
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

ORIGINAL = '''"""Synthetic retry service used only by the integration lab."""
def retry_budget(attempts: int) -> int:
    if not isinstance(attempts, int) or isinstance(attempts, bool) or attempts < 0:
        raise ValueError("attempts must be a nonnegative integer")
    return attempts + 1
'''
TEST_SOURCE = """import unittest
from budget import retry_budget

class RetryContract(unittest.TestCase):
    def test_budget(self):
        for attempts in (0, 1, 3, 10):
            with self.subTest(attempts=attempts):
                self.assertEqual(retry_budget(attempts), attempts)
    def test_invalid(self):
        for attempts in (-1, True, 1.5, "3"):
            with self.subTest(attempts=attempts), self.assertRaises(ValueError):
                retry_budget(attempts)
"""


def apply_approved_patch(worktree: Path, relative_path: str, old: str, new: str) -> None:
    """Apply one exact patch to the single permitted source file.

    A proposed tool action never becomes a shell command. Paths, occurrence count
    and syntax are validated before writing. This is a small explicit action
    boundary, not a general-purpose sandbox for arbitrary generated Python.
    """
    if relative_path != "budget.py" or not isinstance(old, str) or not old:
        raise ValueError("Only the declared budget.py patch action is allowed")
    path = worktree / relative_path
    if path.is_symlink() or not path.is_file() or path.resolve().parent != worktree.resolve():
        raise ValueError("Source must be a regular file inside the worktree")
    current = path.read_text()
    if current.count(old) != 1:
        raise ValueError("Patch must match exactly one source occurrence")
    updated = current.replace(old, new, 1)
    ast.parse(updated)
    path.write_text(updated)


def run_coding(
    seed: int = 7, on_frame: Callable[[dict[str, Any]], None] | None = None
) -> dict[str, Any]:
    """Show a failing contract, patch it in a worktree, and run real tests."""
    git = shutil.which("git")
    if git is None:
        raise RuntimeError("Install Git to run the coding scenario")
    start = time.perf_counter()
    checks: list[dict[str, Any]] = []

    def record(check: dict[str, Any]) -> None:
        checks.append(check)
        if on_frame:
            on_frame(
                {
                    "suite": "coding",
                    "phase": check["name"],
                    "passed": check["passed"],
                    "completed_checks": len(checks),
                }
            )

    env = {
        **{key: value for key, value in os.environ.items() if not key.startswith("GIT_")},
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_AUTHOR_NAME": "EigenScope Lab",
        "GIT_AUTHOR_EMAIL": "lab@example.invalid",
        "GIT_COMMITTER_NAME": "EigenScope Lab",
        "GIT_COMMITTER_EMAIL": "lab@example.invalid",
    }

    def command(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            args, cwd=cwd, env=env, capture_output=True, text=True, timeout=15, check=False
        )

    def git_command(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        return command(
            [git, "-c", f"core.hooksPath={os.devnull}", "-c", "commit.gpgsign=false", *args], cwd
        )

    with tempfile.TemporaryDirectory(prefix="eigenscope-coding-") as temporary:
        root = Path(temporary)
        repository, worktree = root / "repository", root / "agent-worktree"
        repository.mkdir()
        initialized = git_command(["init", "--initial-branch=main"], repository)
        if initialized.returncode:
            raise RuntimeError("Could not initialize isolated Git repository")
        (repository / "budget.py").write_text(ORIGINAL)
        (repository / "test_budget.py").write_text(TEST_SOURCE)
        if (
            git_command(["add", "budget.py", "test_budget.py"], repository).returncode
            or git_command(["commit", "-m", "Synthetic retry contract"], repository).returncode
        ):
            raise RuntimeError("Could not commit the synthetic baseline")
        baseline = command(
            [sys.executable, "-I", "-m", "unittest", "discover", "-s", ".", "-p", "test_*.py"],
            repository,
        )
        record(
            {
                "name": "baseline_test_exposes_real_bug",
                "passed": baseline.returncode == 1 and "FAILED" in baseline.stderr,
            }
        )
        if git_command(["worktree", "add", "-b", "lab-fix", str(worktree)], repository).returncode:
            raise RuntimeError("Could not create isolated Git worktree")
        try:
            try:
                apply_approved_patch(
                    worktree, "../repository/budget.py", "return attempts + 1", "return 0"
                )
                rejected = False
            except ValueError:
                rejected = True
            record({"name": "path_escape_action_rejected", "passed": rejected})
            try:
                apply_approved_patch(worktree, "budget.py", "not present", "return 0")
                stale_rejected = False
            except ValueError:
                stale_rejected = True
            record({"name": "stale_patch_rejected", "passed": stale_rejected})
            apply_approved_patch(worktree, "budget.py", "return attempts + 1", "return attempts")
            fixed = command(
                [sys.executable, "-I", "-m", "unittest", "discover", "-s", ".", "-p", "test_*.py"],
                worktree,
            )
            record(
                {
                    "name": "patched_worktree_passes_contract",
                    "passed": fixed.returncode == 0 and "OK" in fixed.stderr,
                }
            )
            diff = git_command(["diff", "--name-only"], worktree)
            record(
                {
                    "name": "only_approved_source_changed",
                    "passed": diff.stdout.strip() == "budget.py",
                }
            )
            record(
                {
                    "name": "original_checkout_unchanged",
                    "passed": (repository / "budget.py").read_text() == ORIGINAL,
                }
            )
            status = git_command(["status", "--porcelain", "--untracked-files=no"], repository)
            record(
                {
                    "name": "original_tracked_files_clean",
                    "passed": status.returncode == 0 and status.stdout == "",
                }
            )
        finally:
            removed = git_command(["worktree", "remove", "--force", str(worktree)], repository)
            record(
                {
                    "name": "worktree_cleanup",
                    "passed": removed.returncode == 0 and not worktree.exists(),
                }
            )
        version = command([git, "--version"], repository).stdout.strip()
    record({"name": "temporary_repository_removed", "passed": not root.exists()})
    return {
        "suite": "coding",
        "engine": "Git worktree + Python unittest",
        "engine_version": version,
        "seed": seed,
        "checks": checks,
        "metrics": {"wall_ms": round((time.perf_counter() - start) * 1000, 3)},
        "scope": "Actual Git branches/worktree, syntax validation and subprocess tests on source-owned synthetic code. A deterministic approved patch stands in for model reasoning; no IDE or vendor proxy is claimed.",
    }
