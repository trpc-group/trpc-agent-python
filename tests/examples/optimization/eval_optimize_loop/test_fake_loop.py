from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from examples.optimization.eval_optimize_loop.run_pipeline import run_fake_pipeline


@pytest.mark.asyncio
async def test_fake_closed_loop_uses_three_fixture_candidates_and_restores_prompts(tmp_path: Path) -> None:
    report = await run_fake_pipeline(output_dir=tmp_path)
    assert report.selected_candidate_id == "candidate_general_fix"
    assert {candidate.candidate_id: candidate.accepted for candidate in report.candidates} == {
        "candidate_general_fix": True,
        "candidate_noop": False,
        "candidate_overfit": False,
    }
    assert (tmp_path / "optimization_report.json").is_file()
    assert (tmp_path / "optimization_report.md").is_file()


@pytest.mark.asyncio
async def test_fake_loop_does_not_require_live_model_key(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("TRPC_AGENT_API_KEY", raising=False)
    report = await run_fake_pipeline(output_dir=tmp_path)
    assert report.mode == "fake"


def test_fake_cli_runs_directly_from_repository_root(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[4]
    result = subprocess.run(
        [
            sys.executable,
            "examples/optimization/eval_optimize_loop/run_pipeline.py",
            "--mode",
            "fake",
            "--output-dir",
            str(tmp_path),
        ],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "Selected candidate: candidate_general_fix" in result.stdout
