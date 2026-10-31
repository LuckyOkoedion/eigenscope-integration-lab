"""Release prerequisites retain their cause without publishing raw exceptions."""

import json
from types import SimpleNamespace

import pytest

from eigenscope_lab import cli, runner
from eigenscope_lab.reporting import outcome, report


def test_missing_git_is_blocked_before_coding_execution(monkeypatch, capsys):
    monkeypatch.setattr(runner.shutil, "which", lambda _name: None)
    monkeypatch.setattr(
        runner.importlib, "import_module", lambda _name: pytest.fail("No engine should start")
    )
    row = runner.run_scenario("coding", 7)
    assert outcome(row) == "BLOCKED"
    assert row["dependency"] == "git"
    assert cli._emit(report([row], "release-prerequisite-test", 7), None) == 2
    assert json.loads(capsys.readouterr().out)["summary"]["BLOCKED"] == 1


@pytest.mark.parametrize("wrapped", [False, True])
def test_missing_optional_engine_remains_blocked_through_setup_wrapper(monkeypatch, wrapped):
    def unavailable(**_kwargs):
        try:
            raise ModuleNotFoundError("SYNTHETIC_PRIVATE_IMPORT_DETAIL", name="mujoco")
        except ModuleNotFoundError as missing:
            if wrapped:
                raise RuntimeError("SYNTHETIC_PRIVATE_SETUP_DETAIL") from missing
            raise

    monkeypatch.setattr(
        runner.importlib, "import_module", lambda _name: SimpleNamespace(run_robotics=unavailable)
    )
    row = runner.run_scenario("robotics", 7)
    assert outcome(row) == "BLOCKED"
    assert row["dependency"] == "mujoco"
    assert "--extra robotics" in row["install"]
    assert "SYNTHETIC_PRIVATE" not in json.dumps(row)


def test_unrelated_engine_runtime_error_stays_a_harness_error(monkeypatch):
    def broken(**_kwargs):
        raise RuntimeError("SYNTHETIC_PRIVATE_RUNTIME_DETAIL")

    monkeypatch.setattr(
        runner.importlib, "import_module", lambda _name: SimpleNamespace(run_robotics=broken)
    )
    row = runner.run_scenario("robotics", 7)
    assert outcome(row) == "ERROR"
    assert row["reason"] == "RuntimeError"
    assert "SYNTHETIC_PRIVATE" not in json.dumps(row)
