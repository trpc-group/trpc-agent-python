"""CLI smoke tests for the offline optimization loop."""

import subprocess
import sys


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
