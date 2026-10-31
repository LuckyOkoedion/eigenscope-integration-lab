"""Real Git worktrees and a bounded, validated coding action."""

import shutil
from pathlib import Path

import pytest

from eigenscope_lab.simulators.coding import ORIGINAL, apply_approved_patch, run_coding


@pytest.mark.skipif(shutil.which("git") is None, reason="Git is a scenario prerequisite")
def test_actual_git_worktree_contract(monkeypatch, tmp_path):
    redirected = tmp_path / "must-not-be-used.git"
    monkeypatch.setenv("GIT_DIR", str(redirected))
    monkeypatch.setenv("GIT_WORK_TREE", str(tmp_path))
    result = run_coding()
    assert len(result["checks"]) == 9
    assert all(check["passed"] for check in result["checks"]), result["checks"]
    assert not redirected.exists()


def test_invalid_syntax_does_not_change_source(tmp_path: Path):
    source = tmp_path / "budget.py"
    source.write_text(ORIGINAL)
    with pytest.raises(SyntaxError):
        apply_approved_patch(tmp_path, "budget.py", "return attempts + 1", "return ((")
    assert source.read_text() == ORIGINAL


def test_symlink_patch_cannot_write_other_file(tmp_path: Path):
    outside = tmp_path / "outside.py"
    outside.write_text(ORIGINAL)
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    try:
        (worktree / "budget.py").symlink_to(outside)
    except OSError:
        pytest.skip("Symlink creation is unavailable on this host")
    with pytest.raises(ValueError):
        apply_approved_patch(worktree, "budget.py", "return attempts + 1", "return attempts")
    assert outside.read_text() == ORIGINAL
