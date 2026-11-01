"""CLI selection and exit-code controls without model downloads or service calls."""

from __future__ import annotations

import json

import pytest

from eigenscope_lab import cli, model_integration
from eigenscope_lab.reporting import report


@pytest.mark.parametrize(
    "arguments",
    [
        ["models"],
        ["models", "--matrix", "matrix.json", "--engine-smoke"],
        ["models", "--engine-smoke", "--connector", "example:connect"],
        ["models", "--engine-smoke", "--allow-downloads"],
        ["models", "--engine-smoke", "--max-projection-p95-ms", "15"],
    ],
)
def test_incompatible_modes_are_rejected_before_loading_models(monkeypatch, arguments):
    def forbidden(*_args, **_kwargs):
        pytest.fail("Invalid CLI choices must not start inference")

    monkeypatch.setattr(model_integration, "run_engine_smoke", forbidden)
    monkeypatch.setattr(model_integration, "run_model_matrix", forbidden)
    with pytest.raises(SystemExit) as failure:
        cli.main(arguments)
    assert failure.value.code == 2


def test_cli_preserves_blocked_integration_alongside_successful_baseline(
    monkeypatch, capsys, tmp_path
):
    path = tmp_path / "matrix.json"
    path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(model_integration, "load_matrix", lambda _path: [{"id": "example"}])
    captured = {}

    def run(cases, **kwargs):
        captured.update(kwargs)
        return report(
            [
                {"suite": "model:example:baseline", "checks": [{"passed": True}]},
                {"suite": "model:example:integration", "status": "BLOCKED", "checks": []},
            ],
            "model-integration",
            7,
        )

    monkeypatch.setattr(model_integration, "run_model_matrix", run)
    assert cli.main(["models", "--matrix", str(path)]) == 2
    assert captured["connector"] is None
    assert captured["allow_downloads"] is False
    result = json.loads(capsys.readouterr().out)
    assert result["summary"] == {"PASS": 1, "FAIL": 0, "BLOCKED": 1, "ERROR": 0}


@pytest.mark.parametrize("failure_type", [ValueError, ImportError, AttributeError, RuntimeError])
def test_matrix_or_connector_configuration_errors_do_not_print_private_details(
    monkeypatch, capsys, failure_type
):
    def invalid(_path):
        raise failure_type("SYNTHETIC_PRIVATE_CONFIGURATION_DETAIL")

    monkeypatch.setattr(model_integration, "load_matrix", invalid)
    with pytest.raises(SystemExit) as failure:
        cli.main(["models", "--matrix", "private-checkpoint-config.json"])
    assert failure.value.code == 2
    captured = capsys.readouterr()
    assert "SYNTHETIC_PRIVATE_CONFIGURATION_DETAIL" not in captured.out + captured.err
    assert "docs/model-integration.md" in captured.err


def test_engine_smoke_does_not_receive_a_product_connector(monkeypatch, capsys):
    def smoke(**kwargs):
        assert kwargs == {"repeat": 2, "warmup": 0, "seed": 11, "device": "cpu"}
        value = report(
            [{"suite": "model:example:baseline", "checks": [{"passed": True}]}],
            "model-engine-smoke",
            11,
        )
        value["eigenscope_projection_tested"] = False
        return value

    monkeypatch.setattr(model_integration, "run_engine_smoke", smoke)
    assert (
        cli.main(["models", "--engine-smoke", "--repeat", "2", "--warmup", "0", "--seed", "11"])
        == 0
    )
    assert json.loads(capsys.readouterr().out)["eigenscope_projection_tested"] is False
