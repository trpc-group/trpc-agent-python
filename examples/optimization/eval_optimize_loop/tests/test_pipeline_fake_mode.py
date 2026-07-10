from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

from examples.optimization.eval_optimize_loop.run_pipeline import DEFAULT_OPTIMIZER_CONFIG
from examples.optimization.eval_optimize_loop.run_pipeline import DEFAULT_PROMPT
from examples.optimization.eval_optimize_loop.run_pipeline import DEFAULT_TRAIN
from examples.optimization.eval_optimize_loop.run_pipeline import DEFAULT_VAL
from examples.optimization.eval_optimize_loop.run_pipeline import run_pipeline
from examples.optimization.eval_optimize_loop.eval_loop.report import report_to_json


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
        "baseline",
        "candidates",
        "per_case_deltas",
        "delta",
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
    assert payload["failure_attribution_summary"]["attribution_accuracy"] == 1.0
    assert set(payload["audit"]) >= {
        "seed",
        "config_hash",
        "cost",
        "duration_seconds",
        "prompt_hash",
        "candidate_prompts",
        "prompt_diffs",
        "input_hashes",
        "candidate_prompt_hashes",
        "total_run_cost",
    }
    assert payload["audit"]["seed"] == 91
    assert payload["audit"]["duration_seconds"] > 0
    assert payload["run"]["trace"] is True
    assert payload["run"]["reproducibility_command"].startswith("python examples/optimization")
    assert payload["run"]["run_id"].startswith("eval_optimize_loop_fake_")
    assert payload["audit"]["total_run_cost"] == payload["audit"]["cost"]["total"]
    assert "candidate_001_overfit" in payload["audit"]["prompt_diffs"]
    assert "candidate_002_safe" in payload["audit"]["prompt_diffs"]
    assert payload["candidates"][0]["candidate"]["prompt_diff"].startswith("--- baseline_system_prompt.txt")
    assert all(len(record["train_result"]["cases"]) == 3 for record in payload["candidates"])
    assert all(len(record["validation_result"]["cases"]) == 3 for record in payload["candidates"])
    assert payload["writeback"]["status"] == "not_requested"

    decisions = {item["candidate_id"]: item for item in payload["gate_decisions"]}
    assert not decisions["candidate_001_overfit"]["accepted"]
    assert decisions["candidate_002_safe"]["accepted"]
    assert decisions["candidate_001_overfit"]["total_run_cost"] > decisions["candidate_001_overfit"]["candidate_cost"]
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
    assert "Cost And Audit" in markdown

    run_dir = output_dir / "runs" / payload["run"]["run_id"]
    overfit_artifact = payload["audit"]["candidate_artifacts"]["candidate_001_overfit"]
    safe_artifact = payload["audit"]["candidate_artifacts"]["candidate_002_safe"]
    assert (run_dir / "config.snapshot.json").is_file()
    assert (run_dir / "input_hashes.json").is_file()
    assert (run_dir / "candidate_prompts" / overfit_artifact / "system_prompt.txt").is_file()
    assert (run_dir / "case_results" / f"{safe_artifact}_validation.json").is_file()
    assert (run_dir / "prompt_diffs" / f"{safe_artifact}.diff").is_file()
    assert (run_dir / "prompt_diffs" / f"{safe_artifact}.diff").read_text(encoding="utf-8") == (
        payload["candidates"][1]["candidate"]["prompt_diff"]
    )


def test_pipeline_is_deterministic_with_same_seed(tmp_path: Path):
    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"
    run_pipeline(output_dir=first_dir, fake_model=True, fake_judge=True, trace=True)
    run_pipeline(output_dir=second_dir, fake_model=True, fake_judge=True, trace=True)

    first_payload = json.loads((first_dir / "optimization_report.json").read_text(encoding="utf-8"))
    second_payload = json.loads((second_dir / "optimization_report.json").read_text(encoding="utf-8"))
    first_run_id = first_payload["run"]["run_id"]
    second_run_id = second_payload["run"]["run_id"]

    assert _normalized_payload(first_payload) == _normalized_payload(second_payload)
    first_markdown = (first_dir / "optimization_report.md").read_text(encoding="utf-8")
    second_markdown = (second_dir / "optimization_report.md").read_text(encoding="utf-8")
    normalized_first_markdown = first_markdown.replace(first_run_id, "<run_id>").replace(
        str(first_dir),
        "<output_dir>",
    )
    normalized_second_markdown = second_markdown.replace(second_run_id, "<run_id>").replace(
        str(second_dir),
        "<output_dir>",
    )
    assert normalized_first_markdown == normalized_second_markdown


def test_pipeline_accepts_mode_fake_without_legacy_flags(tmp_path: Path):
    report = run_pipeline(output_dir=tmp_path / "run", mode="fake", trace=True)
    assert report.run["mode"] == "fake"
    assert report.selected_candidate == "candidate_002_safe"


def test_pipeline_selected_candidate_is_null_when_all_candidates_rejected(tmp_path: Path):
    config_path = tmp_path / "optimizer.json"
    config = json.loads(Path(DEFAULT_OPTIMIZER_CONFIG).read_text(encoding="utf-8"))
    config["gate"] = {
        "allow_new_hard_fail": False,
        "protected_case_ids": ["val_protected_yes_no"],
        "max_score_drop_per_case": 0.0,
        "max_total_cost": 1.0,
    }
    config["gate"]["min_val_score_improvement"] = 1.0
    config_path.write_text(json.dumps(config), encoding="utf-8")

    report = run_pipeline(
        optimizer_config_path=config_path,
        output_dir=tmp_path / "run",
        mode="fake",
        trace=True,
    )

    assert report.selected_candidate is None


def test_cli_mode_fake_and_legacy_fake_flags_both_run(tmp_path: Path):
    script = Path("examples/optimization/eval_optimize_loop/run_pipeline.py")
    first = tmp_path / "first"
    second = tmp_path / "second"

    subprocess.run(
        [sys.executable, str(script), "--mode", "fake", "--trace", "--output-dir", str(first)],
        check=True,
    )
    subprocess.run(
        [sys.executable, str(script), "--fake-model", "--fake-judge", "--trace", "--output-dir", str(second)],
        check=True,
    )

    assert (first / "optimization_report.json").is_file()
    assert (second / "optimization_report.md").is_file()


def test_report_to_json_rejects_nan_values(tmp_path: Path):
    report = run_pipeline(output_dir=tmp_path / "run", mode="fake", trace=True)
    report.audit["bad_float"] = float("nan")

    try:
        report_to_json(report)
    except ValueError as exc:
        assert "Out of range float values are not JSON compliant" in str(exc)
    else:
        raise AssertionError("report_to_json should reject NaN")


def test_fake_report_json_remains_strict_json(tmp_path: Path):
    report = run_pipeline(output_dir=tmp_path / "run", mode="fake", trace=True)
    payload = report_to_json(report)

    assert "NaN" not in payload
    assert "Infinity" not in payload
    assert json.loads(payload)["selected_candidate"] == "candidate_002_safe"


def _normalized_payload(payload: dict) -> dict:
    normalized = json.loads(json.dumps(payload))
    normalized["run"].pop("run_id", None)
    normalized["run"].pop("reproducibility_command", None)
    normalized["audit"].pop("duration_seconds", None)
    normalized["audit"].pop("reproducibility_command", None)
    normalized["audit"].get("writeback_journal", {}).pop("run_id", None)
    return normalized
