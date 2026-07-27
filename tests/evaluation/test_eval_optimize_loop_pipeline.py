"""Integration tests for the fake optimization loop."""

import asyncio
import json
import time
from pathlib import Path

from trpc_agent_sdk.evaluation._target_prompt import _RollbackError

from examples.optimization.eval_optimize_loop.loop.models import InputPaths
from examples.optimization.eval_optimize_loop.loop.models import PipelineOptions
from examples.optimization.eval_optimize_loop.loop.pipeline import _write_back_and_report
from examples.optimization.eval_optimize_loop.loop.pipeline import _failure_result
from examples.optimization.eval_optimize_loop.loop.pipeline import run_pipeline
from examples.optimization.eval_optimize_loop.loop import pipeline as pipeline_module
from examples.optimization.eval_optimize_loop.loop.evaluation import validate_inputs

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
    work_prompt = tmp_path / "work/prompts/system.md"
    source = (ROOT / "agent/prompts/system.md").read_text(encoding="utf-8")

    asyncio.run(run_pipeline(_options(tmp_path)))

    assert work_prompt.read_text(encoding="utf-8") == source


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


def test_failure_report_keeps_validated_audit_context(tmp_path, monkeypatch):

    async def fail_after_validation(*args, **kwargs):
        raise RuntimeError("evaluation failed")

    monkeypatch.setattr(pipeline_module, "_evaluate_pair", fail_after_validation)
    optimizer = json.loads((ROOT / "optimizer.json").read_text(encoding="utf-8"))
    optimizer["evaluate"]["num_runs"] = 2
    optimizer_path = tmp_path / "optimizer.json"
    optimizer_path.write_text(json.dumps(optimizer), encoding="utf-8")
    options = _options(
        tmp_path,
        paths=_options(tmp_path).paths.model_copy(update={"optimizer_path": optimizer_path}),
    )

    result = asyncio.run(run_pipeline(options))

    assert result.report.status == "REJECTED"
    assert result.report.audit.input_hashes
    assert result.report.audit.num_runs == 2
    assert result.report.audit.case_parallelism == 1


def test_rollback_failure_details_are_audited(tmp_path):
    error = _RollbackError([("system_prompt", RuntimeError("rollback failed"))])

    result = _failure_result(_options(tmp_path), time.monotonic(), error)

    assert "rollback failed" in result.report.failures[0]


def test_write_back_report_matches_updated_prompt(tmp_path):
    prompt_path = tmp_path / "system.md"
    original = (ROOT / "agent/prompts/system.md").read_text(encoding="utf-8")
    prompt_path.write_text(original, encoding="utf-8")
    options = _options(
        tmp_path / "output",
        paths=_options(tmp_path).paths.model_copy(update={"prompt_path": prompt_path}),
    )

    result = asyncio.run(run_pipeline(options))
    bundle, _, _ = validate_inputs(options.paths)
    asyncio.run(
        _write_back_and_report(
            result.report,
            bundle,
            {"system_prompt": original + "\n\nOPTIMIZED_CANDIDATE\n"},
            options.model_copy(update={
                "write_back": True,
                "mode": "real"
            }),
        ))
    payload = json.loads(result.json_path.read_text(encoding="utf-8"))

    assert result.report.gate.accepted is True
    assert payload["source_updated"] is True
    assert "OPTIMIZED_CANDIDATE" in prompt_path.read_text(encoding="utf-8")


def test_fake_mode_write_back_does_not_touch_prompt(tmp_path):
    prompt_path = tmp_path / "system.md"
    original = (ROOT / "agent/prompts/system.md").read_text(encoding="utf-8")
    prompt_path.write_text(original, encoding="utf-8")
    options = _options(
        tmp_path / "output",
        paths=_options(tmp_path).paths.model_copy(update={"prompt_path": prompt_path}),
        write_back=True,
    )

    result = asyncio.run(run_pipeline(options))

    assert result.report.status == "REJECTED"
    assert result.report.failures[0].startswith("ValueError:")
    assert prompt_path.read_text(encoding="utf-8") == original


def test_report_audit_uses_configured_num_runs(tmp_path):
    optimizer = json.loads((ROOT / "optimizer.json").read_text(encoding="utf-8"))
    optimizer["evaluate"]["num_runs"] = 2
    optimizer_path = tmp_path / "optimizer.json"
    optimizer_path.write_text(json.dumps(optimizer), encoding="utf-8")
    options = _options(
        tmp_path / "output",
        paths=_options(tmp_path).paths.model_copy(update={"optimizer_path": optimizer_path}),
    )

    result = asyncio.run(run_pipeline(options))

    assert result.report.audit.num_runs == 2
