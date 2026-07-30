"""End-to-end lifecycle tests for the fake orchestration path."""

from __future__ import annotations

import asyncio
import hashlib
import json
import shlex
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from examples.optimization.eval_optimize_loop.pipeline.models import (
    CandidateProposal,
    CandidateRound,
    Decision,
    OptimizationReport,
    Transition,
)
from examples.optimization.eval_optimize_loop.pipeline.backends import (
    DeterministicCandidateGenerator,
    FakeEvaluationBackend,
)
from examples.optimization.eval_optimize_loop.pipeline.orchestrator import run_pipeline
from examples.optimization.eval_optimize_loop.pipeline.preflight import preflight_run
from examples.optimization.eval_optimize_loop.pipeline import reporting as reporting_module

SOURCE = Path(__file__).resolve().parents[3] / "examples" / "optimization" / "eval_optimize_loop"


def _example(tmp_path: Path, *, accept: bool = False, apply: bool = False) -> Path:
    root = tmp_path / "example"
    (root / "prompts").mkdir(parents=True)
    (root / "traces").mkdir()
    for name in ("optimizer.json", "train.evalset.json", "val.evalset.json"):
        shutil.copyfile(SOURCE / name, root / name)
    shutil.copyfile(SOURCE / "prompts" / "system.md", root / "prompts" / "system.md")
    shutil.copyfile(SOURCE / "traces" / "trace_cases.json", root / "traces" / "trace_cases.json")
    config = json.loads((root / "optimizer.json").read_text(encoding="utf-8"))
    if accept:
        config["pipeline"]["gate"]["minValidationScoreDelta"] = 0
        config["pipeline"]["gate"]["maxNewHardFailures"] = 1
    config["pipeline"]["applyCandidate"] = apply
    (root / "optimizer.json").write_text(json.dumps(config), encoding="utf-8")
    return root


@pytest.mark.asyncio
async def test_fake_reject_overfit_is_a_completed_audit_run(tmp_path) -> None:
    root = _example(tmp_path)
    report = await run_pipeline(str(root), run_id="reject-run")
    assert report.status == Decision.REJECT
    assert [case.transition for case in report.delta.validation.cases] == [
        Transition.NEW_PASS,
        Transition.NEW_FAIL,
        Transition.UNCHANGED,
    ]
    assert "OVERFIT_TRAIN_UP_VALIDATION_DOWN" in report.gate_decision.reasons
    assert report.source_application.applied is False
    actual_prompt = (root / "prompts" / "system.md").read_text(encoding="utf-8")
    expected_prompt = (SOURCE / "prompts" / "system.md").read_text(encoding="utf-8")
    assert actual_prompt == expected_prompt
    assert report.duration_seconds < 180
    artifact_paths = {record.path for record in report.artifacts}
    assert "optimization_report.json" not in artifact_paths
    assert "optimization_report.md" not in artifact_paths
    manifest = json.loads((root / "artifacts" / "reject-run" / "manifest.json").read_text(encoding="utf-8"))
    records = {record["path"]: record for record in manifest["files"]}
    for name in ("optimization_report.json", "optimization_report.md"):
        content = (root / "artifacts" / "reject-run" / name).read_bytes()
        assert records[name]["sha256"] == hashlib.sha256(content).hexdigest()


@pytest.mark.asyncio
async def test_accept_without_apply_leaves_source_unchanged(tmp_path) -> None:
    root = _example(tmp_path, accept=True)
    baseline = (root / "prompts" / "system.md").read_text(encoding="utf-8")
    report = await run_pipeline(str(root), run_id="accept-no-apply")
    assert report.status == Decision.ACCEPT
    assert report.source_application.requested is False
    assert report.source_application.applied is False
    assert (root / "prompts" / "system.md").read_text(encoding="utf-8") == baseline


@pytest.mark.asyncio
async def test_accept_apply_writes_and_verifies_candidate(tmp_path) -> None:
    root = _example(tmp_path, accept=True, apply=True)
    report = await run_pipeline(str(root), run_id="accept-apply")
    prompt = (root / "prompts" / "system.md").read_text(encoding="utf-8")
    assert report.status == Decision.ACCEPT
    assert report.source_application.applied is True
    assert "BEHAVIOR_PROFILE=precision-v2" in prompt
    assert report.source_application.final_hashes["system"] == hashlib.sha256(prompt.encode("utf-8")).hexdigest()


@pytest.mark.asyncio
async def test_final_report_failure_after_apply_rolls_back(tmp_path) -> None:
    root = _example(tmp_path, accept=True, apply=True)
    baseline = (root / "prompts" / "system.md").read_text(encoding="utf-8")

    def fail_final_report(stage: str) -> None:
        if stage == "final_report":
            raise OSError("injected final report failure")

    report = await run_pipeline(
        str(root),
        run_id="failed-final-report",
        fault_injector=fail_final_report,
    )
    assert report.status == Decision.ERROR
    assert report.stage == "final_report"
    assert report.source_application.applied is False
    assert (root / "prompts" / "system.md").read_text(encoding="utf-8") == baseline


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failed_stage",
    (
        "audit_config",
        "baseline_train",
        "baseline_validation",
        "inner_split",
        "candidate_generation",
        "candidate_train",
        "candidate_validation",
        "comparison",
        "gate",
        "pre_apply_report",
        "apply",
        "final_report",
    ),
)
async def test_every_orchestration_stage_fails_to_an_error_audit_and_restores_prompt(
    tmp_path,
    failed_stage,
) -> None:
    root = _example(tmp_path, accept=True, apply=True)
    baseline = (root / "prompts" / "system.md").read_text(encoding="utf-8")

    def inject(stage: str) -> None:
        if stage == failed_stage:
            raise RuntimeError(f"injected {stage} failure")

    report = await run_pipeline(
        str(root),
        run_id=f"stage-{failed_stage}",
        fault_injector=inject,
    )

    assert report.status == Decision.ERROR
    assert report.stage == failed_stage
    assert report.errors[0].stage == failed_stage
    assert report.source_application.applied is False
    assert (root / "prompts" / "system.md").read_text(encoding="utf-8") == baseline
    persisted = OptimizationReport.model_validate_json(
        (root / "artifacts" / f"stage-{failed_stage}" / "optimization_report.json").read_text(encoding="utf-8"))
    assert persisted.status == Decision.ERROR


@pytest.mark.asyncio
async def test_run_directory_is_never_reused(tmp_path) -> None:
    root = _example(tmp_path)
    await run_pipeline(str(root), run_id="immutable")
    report_path = root / "artifacts" / "immutable" / "optimization_report.json"
    before = report_path.read_bytes()
    with pytest.raises(FileExistsError):
        await run_pipeline(str(root), run_id="immutable")
    assert report_path.read_bytes() == before


@pytest.mark.asyncio
async def test_trace_drift_fails_before_artifact_creation(tmp_path) -> None:
    root = _example(tmp_path)
    fixture_path = root / "traces" / "trace_cases.json"
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    fixture["datasetHashes"]["train"] = "0" * 64
    fixture_path.write_text(json.dumps(fixture), encoding="utf-8")
    with pytest.raises(ValueError, match="hashes"):
        await run_pipeline(str(root), mode="trace", run_id="trace-drift")
    assert not (root / "artifacts").exists()


@pytest.mark.asyncio
async def test_source_trace_fixture_completes_offline_pipeline(tmp_path) -> None:
    root = _example(tmp_path)

    report = await run_pipeline(
        str(root),
        mode="trace",
        run_id="source-trace-complete",
    )

    assert report.status == Decision.REJECT
    assert report.stage == "complete"
    assert report.duration_seconds < 180


@pytest.mark.asyncio
async def test_cancellation_restores_prompt_and_propagates(tmp_path) -> None:
    root = _example(tmp_path)
    baseline = (root / "prompts" / "system.md").read_text(encoding="utf-8")

    def cancel_candidate_validation(stage: str) -> None:
        if stage == "candidate_validation":
            raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await run_pipeline(
            str(root),
            run_id="cancelled",
            fault_injector=cancel_candidate_validation,
        )
    assert (root / "prompts" / "system.md").read_text(encoding="utf-8") == baseline
    report = json.loads((root / "artifacts" / "cancelled" / "optimization_report.json").read_text(encoding="utf-8"))
    assert report["status"] == "ERROR"
    assert report["stage"] == "candidate_validation"
    assert report["candidate"]["train"] is not None
    assert report["candidate"]["validation"] is None


@pytest.mark.asyncio
async def test_error_report_preserves_completed_baseline_train(tmp_path) -> None:
    root = _example(tmp_path)

    def fail_baseline_validation(stage: str) -> None:
        if stage == "baseline_validation":
            raise RuntimeError("validation unavailable")

    report = await run_pipeline(
        str(root),
        run_id="partial-baseline",
        fault_injector=fail_baseline_validation,
    )
    assert report.status == Decision.ERROR
    assert report.baseline is not None
    assert report.baseline.train is not None
    assert report.baseline.validation is None
    assert report.failure_attribution is not None
    assert report.failure_attribution.train is not None
    assert report.failure_attribution.validation is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure_call", "failed_stage", "candidate_completed"),
    ((2, "baseline_validation", False), (4, "candidate_validation", True)),
)
async def test_backend_failure_preserves_partial_facts_and_unknown_failed_call_cost(
    tmp_path,
    failure_call,
    failed_stage,
    candidate_completed,
) -> None:
    root = _example(tmp_path)
    delegate = FakeEvaluationBackend()

    class FailingBackend:

        def __init__(self) -> None:
            self.calls = 0

        async def evaluate(self, **kwargs):
            self.calls += 1
            if self.calls == failure_call:
                raise RuntimeError("backend unavailable")
            return await delegate.evaluate(**kwargs)

    report = await run_pipeline(
        str(root),
        run_id=f"backend-failure-{failure_call}",
        backend=FailingBackend(),
    )

    assert report.status == Decision.ERROR
    assert report.stage == failed_stage
    failed_source = next(source for source in report.cost.sources if source.name == failed_stage)
    assert failed_source.cost_usd is None
    assert failed_source.model_calls is None
    assert failed_source.metric_calls is None
    if candidate_completed:
        assert report.candidate.train is not None
        assert report.candidate.validation is None
        assert report.candidate_failure_attribution.train is not None
        assert report.candidate_failure_attribution.validation is None
    else:
        assert report.baseline.train is not None
        assert report.baseline.validation is None
        assert report.failure_attribution.train is not None
        assert report.failure_attribution.validation is None


@pytest.mark.asyncio
async def test_generator_failure_is_recorded_as_unknown_cost_and_redacted(tmp_path) -> None:
    root = _example(tmp_path)

    class FailingGenerator:

        async def generate(self, **kwargs):
            raise RuntimeError('optimizer failed with {"apiKey":"candidate-secret"}')

    report = await run_pipeline(
        str(root),
        run_id="generator-failure-cost",
        candidate_generator=FailingGenerator(),
    )

    assert report.status == Decision.ERROR
    assert report.stage == "candidate_generation"
    source = next(item for item in report.cost.sources if item.name == "candidate_generation.unreported_failure")
    assert source.cost_usd is None
    assert source.model_calls is None
    assert source.metric_calls is None
    assert "candidate-secret" not in report.errors[0].message
    assert "[REDACTED]" in report.errors[0].message


@pytest.mark.asyncio
async def test_generator_receives_only_inner_train_attribution(tmp_path) -> None:
    root = _example(tmp_path)

    class CapturingGenerator:

        def __init__(self) -> None:
            self.checked = False

        async def generate(self, **kwargs):
            inner = json.loads(Path(kwargs["inner_train_path"]).read_text(encoding="utf-8"))
            inner_ids = {case["evalId"] for case in inner["evalCases"]}
            failure_ids = {failure.case_id for failure in kwargs["train_attribution"].failures}
            assert failure_ids <= inner_ids
            output = Path(kwargs["output_dir"])
            output.mkdir(parents=True)
            (output / "config.snapshot.json").write_text(
                json.dumps({"apiKey": "optimizer-secret"}),
                encoding="utf-8",
            )
            self.checked = True
            return await DeterministicCandidateGenerator().generate(**kwargs)

    generator = CapturingGenerator()
    report = await run_pipeline(
        str(root),
        run_id="inner-attribution",
        candidate_generator=generator,
    )
    assert report.status != Decision.ERROR
    assert generator.checked is True
    audit_text = "\n".join(
        path.read_text(encoding="utf-8") for path in (root / "artifacts" / "inner-attribution").rglob("*")
        if path.is_file())
    assert "optimizer-secret" not in audit_text
    assert "[REDACTED]" in audit_text
    assert not list((root / "artifacts" / "inner-attribution").rglob("prompt_sandbox"))


@pytest.mark.asyncio
async def test_custom_backend_is_isolated_and_has_unknown_accounting(tmp_path) -> None:
    root = _example(tmp_path)
    delegate = FakeEvaluationBackend()

    class MutatingBackend:

        async def evaluate(self, **kwargs):
            raw = await delegate.evaluate(**kwargs)
            kwargs["eval_set"].eval_cases.clear()
            kwargs["eval_config"].num_runs = 9
            kwargs["prompts"].clear()
            return raw

    report = await run_pipeline(
        str(root),
        run_id="isolated-custom-backend",
        backend=MutatingBackend(),
    )
    assert report.status != Decision.ERROR
    evaluation_sources = [
        source for source in report.cost.sources if not source.name.startswith("candidate_generation.")
    ]
    assert len(evaluation_sources) == 4
    assert all(source.cost_usd is None for source in evaluation_sources)
    assert all(source.model_calls is None for source in evaluation_sources)
    assert report.baseline.train.case_ids == (
        "train_improve_alpha",
        "train_improve_beta",
        "train_stable",
    )


@pytest.mark.asyncio
async def test_custom_generator_unknown_cost_fails_closed_when_budget_enabled(tmp_path) -> None:
    root = _example(tmp_path)
    config_path = root / "optimizer.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["pipeline"]["gate"]["maxCostUsd"] = 1
    config_path.write_text(json.dumps(config), encoding="utf-8")

    class UnmeteredGenerator:

        async def generate(self, **kwargs):
            known = await DeterministicCandidateGenerator().generate(**kwargs)
            payload = known.model_dump(mode="python")
            payload.pop("cost_sources")
            return CandidateProposal.model_validate(payload)

    report = await run_pipeline(
        str(root),
        run_id="unknown-candidate-cost",
        candidate_generator=UnmeteredGenerator(),
    )
    source = next(item for item in report.cost.sources if item.name == "candidate_generation.unreported")
    assert source.cost_usd is None
    assert source.model_calls is None
    assert report.status == Decision.REJECT
    assert "COST_UNAVAILABLE" in report.gate_decision.reasons


def test_reproducibility_command_contains_all_effective_cli_inputs(tmp_path, monkeypatch) -> None:
    repo = tmp_path / "repo with space"
    root = repo / "examples" / "optimization" / "eval_optimize_loop"
    root.mkdir(parents=True)

    def fake_run(args, **kwargs):
        if args[-1] == "HEAD":
            stdout = "a" * 40 + "\n"
        elif args[-1] == "--porcelain":
            stdout = ""
        elif args[-1] == "--show-toplevel":
            stdout = str(repo) + "\n"
        else:
            raise AssertionError(args)
        return subprocess.CompletedProcess(args, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(reporting_module.subprocess, "run", fake_run)
    result = reporting_module.build_reproducibility(
        str(root),
        mode="live",
        config_path=str(root / "custom config.json"),
        train_path=str(root / "custom train.json"),
        validation_path=str(root / "custom validation.json"),
        run_id="exact-replay",
        apply_candidate=True,
        callback_spec="package.agent:call_agent",
    )
    args = shlex.split(result.command)
    assert result.reproducible is True
    assert args[1] == "examples/optimization/eval_optimize_loop/run_pipeline.py"
    assert args[args.index("--config") + 1].endswith("custom config.json")
    assert args[args.index("--train") + 1].endswith("custom train.json")
    assert args[args.index("--validation") + 1].endswith("custom validation.json")
    assert args[args.index("--run-id") + 1] == "exact-replay-replay"
    assert "--apply-candidate" in args
    assert args[args.index("--call-agent") + 1] == "package.agent:call_agent"


@pytest.mark.asyncio
async def test_generator_baseline_drift_is_rejected_at_stage_boundary(tmp_path) -> None:
    root = _example(tmp_path)

    class DriftingGenerator:

        async def generate(self, **kwargs):
            candidate = {"system": "candidate"}
            return CandidateProposal(
                algorithm="drifting-test-generator",
                baseline_prompts={"system": "stale baseline"},
                prompts=candidate,
                changed=True,
                rounds=(CandidateRound(
                    round=1,
                    candidate_prompts=candidate,
                    accepted=True,
                    score=1,
                ), ),
            )

    report = await run_pipeline(
        str(root),
        run_id="candidate-baseline-drift",
        candidate_generator=DriftingGenerator(),
    )
    assert report.status == Decision.ERROR
    assert report.stage == "candidate_generation"
    assert "baseline prompts differ" in report.errors[0].message


def test_default_run_id_includes_effective_apply_setting(tmp_path) -> None:
    root = _example(tmp_path)
    common = {
        "config_path": None,
        "train_path": None,
        "validation_path": None,
        "mode": None,
        "run_id": None,
        "call_agent": None,
        "callback_spec": None,
        "backend": None,
        "candidate_generator": None,
    }
    dry_run = preflight_run(str(root), apply_candidate=False, **common)
    applying_run = preflight_run(str(root), apply_candidate=True, **common)
    assert dry_run.run_id != applying_run.run_id

    async def call_agent(query: str) -> str:
        return query

    first_callback = preflight_run(
        str(root),
        apply_candidate=False,
        **{
            **common,
            "mode": "live",
            "call_agent": call_agent,
            "callback_spec": "package.first:call_agent",
        },
    )
    second_callback = preflight_run(
        str(root),
        apply_candidate=False,
        **{
            **common,
            "mode": "live",
            "call_agent": call_agent,
            "callback_spec": "package.second:call_agent",
        },
    )
    assert first_callback.run_id != second_callback.run_id


@pytest.mark.asyncio
async def test_final_duration_includes_terminal_report_write(tmp_path, monkeypatch) -> None:
    root = _example(tmp_path)
    original = reporting_module.persist_report
    delayed = False

    def persist_with_delay(sink, report):
        nonlocal delayed
        if report.stage == "complete" and not delayed:
            delayed = True
            time.sleep(0.05)
        return original(sink, report)

    monkeypatch.setattr(reporting_module, "persist_report", persist_with_delay)
    report = await run_pipeline(str(root), run_id="duration-includes-report")
    assert delayed is True
    assert report.duration_seconds >= 0.05
    assert report.inputs["environment"]["sdk"]
