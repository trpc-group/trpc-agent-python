from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from examples.optimization.eval_optimize_loop.run_pipeline import run_fake_pipeline


PROMPT_DIR = Path(__file__).resolve().parents[4] / "examples" / "optimization" / "eval_optimize_loop" / "agent" / "prompts"


@pytest.mark.asyncio
async def test_fake_closed_loop_uses_three_fixture_candidates_and_restores_prompts(tmp_path: Path) -> None:
    baseline_prompts = {name: (PROMPT_DIR / name).read_text(encoding="utf-8") for name in ("system.md", "router.md")}
    report = await run_fake_pipeline(output_dir=tmp_path)
    assert report.selected_candidate_id == "candidate_general_fix"
    assert {candidate.candidate_id: candidate.accepted for candidate in report.candidates} == {
        "candidate_general_fix": True,
        "candidate_noop": False,
        "candidate_overfit": False,
    }
    assert (tmp_path / "optimization_report.json").is_file()
    assert (tmp_path / "optimization_report.md").is_file()
    assert {name: (PROMPT_DIR / name).read_text(encoding="utf-8") for name in baseline_prompts} == baseline_prompts


@pytest.mark.asyncio
async def test_fake_loop_does_not_require_live_model_key(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("TRPC_AGENT_API_KEY", raising=False)
    report = await run_fake_pipeline(output_dir=tmp_path)
    assert report.mode == "fake"


@pytest.mark.asyncio
async def test_fake_mode_keeps_parsed_json_tool_calls_when_no_intermediate_trace(tmp_path: Path) -> None:
    report = await run_fake_pipeline(output_dir=tmp_path)

    assert report.baseline_train is not None
    case = next(item for item in report.baseline_train.cases if item.eval_id == "train_tool_argument")
    assert [(tool.name, tool.arguments) for tool in case.tool_calls] == [("lookup_order", {})]
    assert [(tool.name, tool.arguments) for tool in case.expected_tool_calls] == [
        ("lookup_order", {"order_id": "A100"})
    ]


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
