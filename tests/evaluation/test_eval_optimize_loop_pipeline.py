"""Integration tests for the fake optimization loop."""

import asyncio
import json
from pathlib import Path

from examples.optimization.eval_optimize_loop.loop.models import InputPaths
from examples.optimization.eval_optimize_loop.loop.models import PipelineOptions
from examples.optimization.eval_optimize_loop.loop.pipeline import run_pipeline

ROOT = Path("examples/optimization/eval_optimize_loop")


def _options(tmp_path: Path, **overrides) -> PipelineOptions:
    values = {
        "paths":
        InputPaths(
            prompt_path=ROOT / "agent/prompts/system.md",
            train_path=ROOT / "data/train.evalset.json",
            validation_path=ROOT / "data/val.evalset.json",
            optimizer_path=ROOT / "optimizer.json",
            gate_path=ROOT / "gate.json",
        ),
        "output_dir":
        tmp_path,
        "mode":
        "fake-model",
    }
    values.update(overrides)
    return PipelineOptions(**values)


def test_fake_pipeline_writes_auditable_json_and_markdown(tmp_path):
    result = asyncio.run(run_pipeline(_options(tmp_path, fake_judge=True)))

    assert result.json_path.is_file()
    assert result.markdown_path.is_file()
    payload = json.loads(result.json_path.read_text(encoding="utf-8"))
    assert payload["gate"]["checks"]
    assert payload["audit"]["input_hashes"]
    assert "Optimization" in result.markdown_path.read_text(encoding="utf-8")


def test_candidate_prompt_is_restored_after_replay(tmp_path):
    source = (ROOT / "agent/prompts/system.md").read_text(encoding="utf-8")

    asyncio.run(run_pipeline(_options(tmp_path)))

    assert (ROOT / "agent/prompts/system.md").read_text(encoding="utf-8") == source


def test_trace_mode_replays_recorded_cases(tmp_path):
    options = _options(
        tmp_path,
        mode="trace",
        trace_file=ROOT / "data/fake_trace.json",
    )

    result = asyncio.run(run_pipeline(options))

    assert result.report.baseline
    assert result.report.candidate
    assert (tmp_path / "work/trace/baseline/train.evalset.json").is_file()
    assert (tmp_path / "work/trace/baseline/validation.evalset.json").is_file()
    assert (tmp_path / "work/trace/candidate/train.evalset.json").is_file()
    assert (tmp_path / "work/trace/candidate/validation.evalset.json").is_file()


def test_pipeline_failure_is_reported_without_prompt_write(tmp_path):
    options = _options(tmp_path,
                       paths=InputPaths(
                           prompt_path=ROOT / "agent/prompts/system.md",
                           train_path=ROOT / "missing.evalset.json",
                           validation_path=ROOT / "data/val.evalset.json",
                           optimizer_path=ROOT / "optimizer.json",
                           gate_path=ROOT / "gate.json",
                       ))

    result = asyncio.run(run_pipeline(options))

    assert result.report.status == "REJECTED"
    assert result.report.failures
    assert result.json_path.is_file()
