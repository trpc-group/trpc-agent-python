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
from trpc_agent_sdk.evaluation import EvalSet

from examples.optimization.eval_optimize_loop.pipeline.models import (
    CandidateProposal,
    CandidateRound,
    CostSource,
    Decision,
    OptimizationReport,
    Transition,
)
from examples.optimization.eval_optimize_loop.pipeline.backends import (
    DeterministicCandidateGenerator,
    FakeEvaluationBackend,
)
from examples.optimization.eval_optimize_loop.pipeline.artifacts import (
    AuditPersistenceError,
    AuditSink,
)
from examples.optimization.eval_optimize_loop.pipeline.costing import CostLedger
from examples.optimization.eval_optimize_loop.pipeline.evaluation import (
    dataset_fingerprint,
    dataset_fingerprint_payload,
)
from examples.optimization.eval_optimize_loop.pipeline.evaluation_runtime import create_evaluation_runtime
from examples.optimization.eval_optimize_loop.pipeline.models import Phase, Split
from examples.optimization.eval_optimize_loop.pipeline.orchestrator import run_pipeline
from examples.optimization.eval_optimize_loop.pipeline.preflight import preflight_run
from examples.optimization.eval_optimize_loop.pipeline.prompt_workspace import (
    PromptRestoreError,
)
from examples.optimization.eval_optimize_loop.pipeline import orchestrator as orchestrator_module
from examples.optimization.eval_optimize_loop.pipeline import reporting as reporting_module

SOURCE = Path(__file__).resolve().parents[3] / "examples" / "optimization" / "eval_optimize_loop"


async def importable_call_agent(query: str) -> str:
    return query


async def alternate_importable_call_agent(query: str) -> str:
    return query


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
async def test_fake_reject_with_hard_regression_is_a_completed_audit_run(tmp_path) -> None:
    root = _example(tmp_path)
    report = await run_pipeline(str(root), run_id="reject-run")
    assert report.status == Decision.REJECT
    assert [case.transition for case in report.delta.validation.cases] == [
        Transition.NEW_PASS,
        Transition.NEW_FAIL,
        Transition.UNCHANGED,
    ]
    assert "NEW_HARD_FAIL_BUDGET_EXCEEDED" in report.gate_decision.reasons
    assert "OVERFIT_TRAIN_UP_VALIDATION_DOWN" not in report.gate_decision.reasons
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
async def test_audit_dataset_copies_round_trip_to_reported_fingerprints(tmp_path) -> None:
    root = _example(tmp_path)
    report = await run_pipeline(str(root), run_id="dataset-contract")
    run_dir = root / "artifacts" / "dataset-contract"

    for source_name, audit_name in (
            ("train.evalset.json", "train.evalset.json"),
            ("val.evalset.json", "val.evalset.json"),
    ):
        source = EvalSet.model_validate(json.loads((root / source_name).read_text(encoding="utf-8")))
        audited = EvalSet.model_validate(json.loads((run_dir / audit_name).read_text(encoding="utf-8")))
        assert dataset_fingerprint(audited) == dataset_fingerprint(source)

    assert report.inputs["auditHashes"] == {
        split: report.inputs["hashes"][split] for split in ("train", "validation")
    }
    assert report.optimization is not None
    inner = report.optimization["innerSplit"]
    for split_name, hash_name, path_name in (
            ("train", "trainHash", "trainPath"),
            ("selection", "selectionHash", "selectionPath"),
    ):
        audited_payload = json.loads((run_dir / inner[path_name]).read_text(encoding="utf-8"))
        audited = EvalSet.model_validate(audited_payload)
        assert inner[hash_name] == dataset_fingerprint(audited)
        assert inner["auditHashes"][split_name] == dataset_fingerprint_payload(audited_payload)


@pytest.mark.asyncio
async def test_secret_bearing_dataset_has_distinct_source_and_audit_hashes(tmp_path) -> None:
    root = _example(tmp_path)
    train_path = root / "train.evalset.json"
    train_payload = json.loads(train_path.read_text(encoding="utf-8"))
    train_payload["evalCases"][0]["sessionInput"]["state"]["apiKey"] = "fixture-secret"
    train_path.write_text(json.dumps(train_payload), encoding="utf-8")

    source = EvalSet.model_validate(train_payload)
    report = await run_pipeline(str(root), run_id="redacted-dataset-contract")
    audited_payload = json.loads(
        (root / "artifacts" / "redacted-dataset-contract" / "train.evalset.json").read_text(encoding="utf-8"))

    assert audited_payload["evalCases"][0]["sessionInput"]["state"]["apiKey"] == "[REDACTED]"
    assert report.inputs["hashes"]["train"] == dataset_fingerprint(source)
    assert report.inputs["auditHashes"]["train"] == dataset_fingerprint_payload(audited_payload)
    assert report.inputs["hashes"]["train"] != report.inputs["auditHashes"]["train"]


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
async def test_apply_and_restore_double_failure_is_preserved_in_terminal_audit(tmp_path, monkeypatch) -> None:
    root = _example(tmp_path, accept=True, apply=True)
    original_write = orchestrator_module.PromptWorkspace._write_verified
    candidate_writes = 0

    async def fail_apply_and_restore(workspace, prompts):
        nonlocal candidate_writes
        if prompts != workspace.baseline:
            candidate_writes += 1
            if candidate_writes > 1:
                raise OSError("candidate write unavailable")
        elif candidate_writes > 1:
            raise OSError("restore unavailable: " + "r" * 5000)
        return await original_write(workspace, prompts)

    monkeypatch.setattr(
        orchestrator_module.PromptWorkspace,
        "_write_verified",
        fail_apply_and_restore,
    )
    with pytest.raises(PromptRestoreError):
        await run_pipeline(str(root), run_id="apply-restore-double-failure")

    report_path = root / "artifacts" / "apply-restore-double-failure" / "optimization_report.json"
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    message = payload["errors"][0]["message"]
    assert "candidate write unavailable" in message
    assert "restore unavailable" in message
    assert len(message) <= 4000


@pytest.mark.asyncio
async def test_error_notes_have_bounded_count_and_total_size(tmp_path) -> None:
    root = _example(tmp_path)

    class FailingGenerator:

        async def generate(self, **kwargs):
            error = RuntimeError("primary failure")
            for index in range(100):
                error.add_note(f"note-{index}: " + "x" * 10_000)
            raise error

    report = await run_pipeline(str(root), run_id="bounded-error-notes", candidate_generator=FailingGenerator())
    assert report.status == Decision.ERROR
    message = report.errors[0].message
    assert "primary failure" in message
    assert "diagnostics omitted" in message
    assert len(message) <= 4000


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
async def test_failed_optimizer_result_preserves_rounds_cost_and_duration(tmp_path) -> None:
    root = _example(tmp_path)

    class FailedGenerator:
        async def generate(self, **kwargs):
            baseline = dict(kwargs["baseline_prompts"])
            return CandidateProposal(
                status="FAILED",
                error_message="optimizer backend failed",
                algorithm="gepa_reflective",
                baseline_prompts=baseline,
                prompts=baseline,
                changed=False,
                stop_reason="error",
                rounds=(CandidateRound(
                    round=1,
                    candidate_prompts={},
                    accepted=False,
                    error_message="reflection request failed",
                    cost_usd=0.12,
                    duration_seconds=0.4,
                ), ),
                cost_sources=(CostSource(
                    name="optimizer_reported",
                    cost_usd=0.12,
                    model_calls=1,
                ), ),
                duration_seconds=0.4,
            )

    report = await run_pipeline(
        str(root),
        run_id="failed-optimizer-facts",
        candidate_generator=FailedGenerator(),
    )

    assert report.status == Decision.ERROR
    assert report.optimization["proposal"]["status"] == "FAILED"
    assert report.optimization["proposal"]["rounds"][0]["costUsd"] == 0.12
    assert report.optimization["proposal"]["durationSeconds"] == 0.4
    source = next(item for item in report.cost.sources if item.name.endswith("optimizer_reported"))
    assert source.cost_usd == 0.12
    assert all(item.name != "candidate_generation.unreported_failure" for item in report.cost.sources)


@pytest.mark.asyncio
async def test_adapter_rejected_success_is_audited_without_evaluation_or_apply(tmp_path) -> None:
    root = _example(tmp_path, accept=True, apply=True)
    prompt_path = root / "prompts" / "system.md"
    baseline_prompt = prompt_path.read_text(encoding="utf-8")

    class RejectedSuccessGenerator:
        async def generate(self, **kwargs):
            baseline = dict(kwargs["baseline_prompts"])
            return CandidateProposal(
                status="SUCCEEDED",
                adapter_error="optimizer baseline prompts differ from the workspace baseline",
                algorithm="gepa_reflective",
                baseline_prompts=baseline,
                prompts=baseline,
                changed=False,
                cost_sources=(CostSource(
                    name="optimizer_reported",
                    cost_usd=0.03,
                    model_calls=1,
                ), ),
                duration_seconds=0.2,
            )

    report = await run_pipeline(
        str(root),
        run_id="adapter-rejected-success",
        candidate_generator=RejectedSuccessGenerator(),
    )

    assert report.status == Decision.ERROR
    assert report.stage == "candidate_generation"
    assert report.optimization["proposal"]["status"] == "SUCCEEDED"
    assert report.optimization["proposal"]["adapterError"]
    assert report.candidate is None
    assert prompt_path.read_text(encoding="utf-8") == baseline_prompt


@pytest.mark.asyncio
async def test_terminal_audit_failure_is_raised_instead_of_silently_returned(tmp_path, monkeypatch) -> None:
    root = _example(tmp_path)

    class FailingGenerator:

        async def generate(self, **kwargs):
            raise RuntimeError("optimizer unavailable")

    def fail_persistence(*args, **kwargs):
        raise OSError("audit volume unavailable")

    monkeypatch.setattr(orchestrator_module, "persist_terminal_report", fail_persistence)
    with pytest.raises(AuditPersistenceError, match="audit volume unavailable") as captured:
        await run_pipeline(
            str(root),
            run_id="terminal-persistence-failure",
            candidate_generator=FailingGenerator(),
        )
    assert any("original pipeline error: RuntimeError" in note for note in captured.value.__notes__)


@pytest.mark.asyncio
async def test_live_evaluation_cost_bounds_are_accounted_conservatively(tmp_path) -> None:
    root = _example(tmp_path)
    config_path = root / "optimizer.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["pipeline"].update({
        "mode": "live",
        "liveAgentCallMaxCostUsd": 0.01,
        "liveMetricCallMaxCostUsd": 0.02,
    })
    config_path.write_text(json.dumps(config), encoding="utf-8")

    validated = preflight_run(
        str(root),
        config_path=None,
        train_path=None,
        validation_path=None,
        mode=None,
        run_id="live-cost-bounds",
        apply_candidate=False,
        call_agent=importable_call_agent,
        callback_spec=f"{__name__}:importable_call_agent",
        backend=None,
        candidate_generator=None,
    )
    sink = AuditSink(validated.artifact_root, validated.run_id)
    sink.create()
    ledger = CostLedger()
    runtime = create_evaluation_runtime(
        validated=validated,
        backend=FakeEvaluationBackend(),
        custom_backend=False,
        sink=sink,
        ledger=ledger,
    )
    await runtime.evaluate(
        eval_set=validated.train,
        prompts={"system": "baseline"},
        split=Split.TRAIN,
        phase=Phase.BASELINE,
    )
    source = ledger.summary().sources[0]
    invocations = sum(len(case.conversation or case.actual_conversation or []) for case in validated.train.eval_cases)
    expected = invocations * validated.config.evaluate.num_runs * 0.03
    assert source.cost_usd == pytest.approx(expected)
    assert source.upper_bound is True
    assert source.model_calls == invocations * validated.config.evaluate.num_runs


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


@pytest.mark.parametrize("object_id_length", (40, 64))
def test_reproducibility_command_contains_all_effective_cli_inputs(
        tmp_path,
        monkeypatch,
        object_id_length,
) -> None:
    repo = tmp_path / "repo with space"
    root = repo / "examples" / "optimization" / "eval_optimize_loop"
    root.mkdir(parents=True)

    def fake_run(args, **kwargs):
        if args[-1] == "HEAD":
            stdout = "a" * object_id_length + "\n"
        elif args[-1] == "--porcelain":
            stdout = ""
        elif args[-1] == "--show-toplevel":
            stdout = str(repo) + "\n"
        elif args[1] == "ls-files":
            stdout = args[-1] + "\n"
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
        input_paths=(
            str(root / "custom config.json"),
            str(root / "custom train.json"),
            str(root / "custom validation.json"),
        ),
    )
    args = shlex.split(result.command)
    assert result.reproducible is True
    assert result.git_commit == "a" * object_id_length
    assert args[1] == "examples/optimization/eval_optimize_loop/run_pipeline.py"
    assert args[args.index("--config") + 1].endswith("custom config.json")
    assert args[args.index("--train") + 1].endswith("custom train.json")
    assert args[args.index("--validation") + 1].endswith("custom validation.json")
    assert args[args.index("--run-id") + 1] == "exact-replay-replay"
    assert "--apply-candidate" in args
    assert args[args.index("--call-agent") + 1] == "package.agent:call_agent"


@pytest.mark.parametrize(
    ("input_path", "tracked", "expected_reason"),
    (
        ("outside/input.json", True, "input_outside_git"),
        ("repo/untracked.json", False, "input_not_tracked"),
    ),
)
def test_reproducibility_requires_all_inputs_in_the_pinned_commit(
    tmp_path,
    monkeypatch,
    input_path,
    tracked,
    expected_reason,
) -> None:
    repo = tmp_path / "repo"
    root = repo / "example"
    root.mkdir(parents=True)
    resolved_input = tmp_path / input_path

    def fake_run(args, **kwargs):
        if args[-1] == "HEAD":
            return subprocess.CompletedProcess(args, 0, stdout="a" * 40 + "\n", stderr="")
        if args[-1] == "--porcelain":
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
        if args[-1] == "--show-toplevel":
            return subprocess.CompletedProcess(args, 0, stdout=str(repo) + "\n", stderr="")
        if args[1] == "ls-files":
            return subprocess.CompletedProcess(args, 0 if tracked else 1, stdout="", stderr="")
        raise AssertionError(args)

    monkeypatch.setattr(reporting_module.subprocess, "run", fake_run)
    result = reporting_module.build_reproducibility(
        str(root),
        mode="fake",
        config_path=str(root / "optimizer.json"),
        train_path=str(root / "train.json"),
        validation_path=str(root / "validation.json"),
        run_id="replay",
        apply_candidate=False,
        input_paths=(str(resolved_input), ),
    )
    assert result.reproducible is False
    assert result.reason == expected_reason
    assert result.command is None


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

    first_callback = preflight_run(
        str(root),
        apply_candidate=False,
        **{
            **common,
            "mode": "live",
            "call_agent": importable_call_agent,
            "callback_spec": f"{__name__}:importable_call_agent",
        },
    )
    second_callback = preflight_run(
        str(root),
        apply_candidate=False,
        **{
            **common,
            "mode": "live",
            "call_agent": alternate_importable_call_agent,
            "callback_spec": f"{__name__}:alternate_importable_call_agent",
        },
    )
    assert first_callback.run_id != second_callback.run_id


def test_preflight_rejects_mismatched_live_callback_identity(tmp_path) -> None:
    root = _example(tmp_path)
    with pytest.raises(ValueError, match="must resolve to the same function"):
        preflight_run(
            str(root),
            config_path=None,
            train_path=None,
            validation_path=None,
            mode="live",
            run_id="mismatched-live-callback",
            apply_candidate=False,
            call_agent=importable_call_agent,
            callback_spec=f"{__name__}:alternate_importable_call_agent",
            backend=None,
            candidate_generator=None,
        )


@pytest.mark.asyncio
async def test_terminal_report_is_committed_once_with_gate_consistent_duration(tmp_path, monkeypatch) -> None:
    root = _example(tmp_path)
    original = reporting_module.persist_report
    complete_writes = 0

    def persist_with_delay(sink, report):
        nonlocal complete_writes
        if report.stage == "complete":
            complete_writes += 1
            time.sleep(0.05)
        return original(sink, report)

    monkeypatch.setattr(reporting_module, "persist_report", persist_with_delay)
    report = await run_pipeline(str(root), run_id="duration-includes-report")
    duration_check = next(check for check in report.gate_decision.checks if check.code == "DURATION_BUDGET")
    assert complete_writes == 1
    assert report.duration_seconds == pytest.approx(duration_check.observed, abs=1e-12)
    assert report.inputs["environment"]["sdk"]
