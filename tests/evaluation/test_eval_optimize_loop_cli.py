"""CLI smoke tests for the offline optimization loop."""

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

from examples.optimization.eval_optimize_loop import run_pipeline


def test_cli_fake_mode_runs_without_api_key(tmp_path):
    command = [
        sys.executable,
        "examples/optimization/eval_optimize_loop/run_pipeline.py",
        "--output",
        str(tmp_path),
        "--fake-judge",
    ]

    completed = subprocess.run(command, capture_output=True, text=True, check=True)

    assert "optimization_report.json" in completed.stdout
    assert (tmp_path / "optimization_report.json").is_file()


def test_real_model_name_uses_environment(monkeypatch):
    monkeypatch.setenv("TRPC_AGENT_MODEL_NAME", "deepseek-chat")

    assert run_pipeline._model_name("real", None) == "deepseek-chat"
    assert run_pipeline._model_name("real", "explicit-model") == "explicit-model"


def test_main_accepts_completed_report(monkeypatch, tmp_path, capsys):
    report = SimpleNamespace(
        gate=SimpleNamespace(accepted=True),
        failures=[],
        status="ACCEPTED",
    )
    result = SimpleNamespace(
        report=report,
        json_path=tmp_path / "optimization_report.json",
        markdown_path=tmp_path / "optimization_report.md",
    )

    async def fake_run(options):
        assert options.model_name == "fake-model"
        return result

    monkeypatch.setattr(sys, "argv", ["run_pipeline.py", "--output", str(tmp_path)])
    monkeypatch.setattr(run_pipeline, "run_pipeline", fake_run)

    assert run_pipeline.main() == 0
    assert "ACCEPTED" in capsys.readouterr().out


def test_main_rejects_gate_failure(monkeypatch, tmp_path):
    report = SimpleNamespace(
        gate=SimpleNamespace(accepted=False),
        failures=[],
        status="REJECTED",
    )
    result = SimpleNamespace(
        report=report,
        json_path=Path(tmp_path) / "optimization_report.json",
        markdown_path=Path(tmp_path) / "optimization_report.md",
    )

    async def fake_run(options):
        return result

    monkeypatch.setattr(sys, "argv", ["run_pipeline.py"])
    monkeypatch.setattr(run_pipeline, "run_pipeline", fake_run)

    assert run_pipeline.main() == 1


def test_cli_pipeline_failure_returns_nonzero(tmp_path):
    command = [
        sys.executable,
        "examples/optimization/eval_optimize_loop/run_pipeline.py",
        "--output",
        str(tmp_path),
        "--train",
        str(tmp_path / "missing.evalset.json"),
    ]

    completed = subprocess.run(command, capture_output=True, text=True)

    assert completed.returncode != 0


def test_cli_gate_rejection_returns_nonzero(tmp_path):
    gate = tmp_path / "gate.json"
    gate.write_text('{"primary_metric":"final_response_avg_score","min_score_delta":2.0}', encoding="utf-8")
    command = [
        sys.executable,
        "examples/optimization/eval_optimize_loop/run_pipeline.py",
        "--output",
        str(tmp_path),
        "--gate",
        str(gate),
    ]

    completed = subprocess.run(command, capture_output=True, text=True)

    assert completed.returncode != 0
