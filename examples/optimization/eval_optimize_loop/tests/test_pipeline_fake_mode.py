from __future__ import annotations

import json
from pathlib import Path

from examples.optimization.eval_optimize_loop.run_pipeline import DEFAULT_OPTIMIZER_CONFIG
from examples.optimization.eval_optimize_loop.run_pipeline import DEFAULT_PROMPT
from examples.optimization.eval_optimize_loop.run_pipeline import DEFAULT_TRAIN
from examples.optimization.eval_optimize_loop.run_pipeline import DEFAULT_VAL
from examples.optimization.eval_optimize_loop.run_pipeline import run_pipeline


def test_fake_mode_pipeline_generates_json_and_markdown_reports(tmp_path: Path):
    output_dir = tmp_path / "run"
    report = run_pipeline(
        train_path=DEFAULT_TRAIN,
        val_path=DEFAULT_VAL,
        optimizer_config_path=DEFAULT_OPTIMIZER_CONFIG,
        prompt_path=DEFAULT_PROMPT,
        output_dir=output_dir,
        fake_model=True,
        fake_judge=True,
        trace=True,
    )

    json_path = output_dir / "optimization_report.json"
    md_path = output_dir / "optimization_report.md"
    assert json_path.is_file()
    assert md_path.is_file()
    assert report.selected_candidate == "candidate_002_safe"

    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert set(payload) >= {
        "schema_version",
        "run",
        "baseline_train",
        "baseline_validation",
        "candidates",
        "per_case_deltas",
        "failure_attribution_summary",
        "gate_decisions",
        "selected_candidate",
        "audit",
    }
    assert payload["baseline_train"]["score"] == 0.333333
    assert payload["baseline_validation"]["score"] == 0.666667
    assert payload["selected_candidate"] == "candidate_002_safe"
    assert [record["candidate"]["candidate_id"] for record in payload["candidates"]] == [
        "candidate_001_overfit",
        "candidate_002_safe",
    ]
    assert len(payload["per_case_deltas"]) == 12
    assert payload["failure_attribution_summary"]["by_category"]["format_violation"] >= 1
    assert set(payload["audit"]) >= {
        "seed",
        "config_hash",
        "cost",
        "duration_seconds",
        "candidate_prompts",
        "prompt_diffs",
    }
    assert "candidate_001_overfit" in payload["audit"]["prompt_diffs"]
    assert "candidate_002_safe" in payload["audit"]["prompt_diffs"]

    decisions = {item["candidate_id"]: item for item in payload["gate_decisions"]}
    assert not decisions["candidate_001_overfit"]["accepted"]
    assert decisions["candidate_002_safe"]["accepted"]
    assert any(
        "train score improved but validation score regressed" in reason
        for reason in decisions["candidate_001_overfit"]["reasons"]
    )

    markdown = md_path.read_text(encoding="utf-8")
    assert "Selected candidate: `candidate_002_safe`." in markdown
    assert "candidate_001_overfit (rejected)" in markdown
    assert "candidate_002_safe (accepted)" in markdown
    assert "Baseline vs Candidate Scores" in markdown
    assert "Per-Case Delta" in markdown
    assert "Failure Attribution Summary" in markdown
    assert "Prompt Diff" in markdown
    assert "Reproducibility" in markdown


def test_pipeline_is_deterministic_with_same_seed(tmp_path: Path):
    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"
    run_pipeline(output_dir=first_dir, fake_model=True, fake_judge=True, trace=True)
    run_pipeline(output_dir=second_dir, fake_model=True, fake_judge=True, trace=True)

    assert (first_dir / "optimization_report.json").read_text(encoding="utf-8") == (
        second_dir / "optimization_report.json"
    ).read_text(encoding="utf-8")
    assert (first_dir / "optimization_report.md").read_text(encoding="utf-8") == (
        second_dir / "optimization_report.md"
    ).read_text(encoding="utf-8")
