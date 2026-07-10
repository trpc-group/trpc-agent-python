from __future__ import annotations

import hashlib
import json
import math
import os
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from examples.optimization.eval_optimize_loop.eval_loop import pipeline as pipeline_module
from examples.optimization.eval_optimize_loop.eval_loop import report as report_module
from examples.optimization.eval_optimize_loop.eval_loop.backends import FakeBackend
from examples.optimization.eval_optimize_loop.eval_loop.pipeline import PipelineRequest
from examples.optimization.eval_optimize_loop.eval_loop.pipeline import execute_pipeline
from examples.optimization.eval_optimize_loop.eval_loop.schemas import CandidatePrompt
from examples.optimization.eval_optimize_loop.eval_loop.schemas import CaseResult
from examples.optimization.eval_optimize_loop.eval_loop.schemas import CostSummary
from examples.optimization.eval_optimize_loop.eval_loop.schemas import EvalResult
from examples.optimization.eval_optimize_loop.eval_loop.schemas import OptimizationResult
from examples.optimization.eval_optimize_loop.eval_loop.schemas import OptimizationRound
from examples.optimization.eval_optimize_loop.eval_loop.schemas import WritebackResult
from examples.optimization.eval_optimize_loop.eval_loop.writeback import ConcurrentPromptUpdateError
from examples.optimization.eval_optimize_loop.eval_loop.writeback import PromptRestorationError
from examples.optimization.eval_optimize_loop.run_pipeline import build_pipeline_request_and_backend


class RecordingBackend:
    def __init__(
        self,
        *,
        candidates: list[CandidatePrompt],
        results: dict[tuple[str, str], EvalResult],
        optimization_cost: CostSummary | None = None,
        rounds: list[OptimizationRound] | None = None,
        raw_summary: dict[str, object] | None = None,
    ) -> None:
        self.candidates = candidates
        self.results = results
        self.optimization_cost = optimization_cost or CostSummary()
        self.rounds = list(rounds or [])
        self.raw_summary = raw_summary or {"status": "SUCCEEDED"}
        self.calls: list[tuple[Any, ...]] = []
        self.failure_summary: dict[str, object] | None = None

    async def evaluate(
        self,
        *,
        prompt_id: str,
        prompts: dict[str, str],
        dataset_path: str | Path,
        split: str,
        trace: bool,
        artifact_dir: str | Path,
    ) -> EvalResult:
        self.calls.append(("evaluate", prompt_id, split, dict(prompts), Path(artifact_dir), trace))
        return self.results[(prompt_id, split)]

    async def optimize_candidates(
        self,
        *,
        baseline_prompts: dict[str, str],
        baseline_train: EvalResult,
        failure_summary: dict[str, object],
        train_path: str | Path,
        validation_path: str | Path,
        config_path: str | Path,
        artifact_dir: str | Path,
    ) -> OptimizationResult:
        self.calls.append(("optimize", dict(baseline_prompts), Path(artifact_dir)))
        self.failure_summary = failure_summary
        return OptimizationResult(
            candidates=self.candidates,
            rounds=self.rounds,
            cost=self.optimization_cost,
            raw_summary=self.raw_summary,
        )


class MutatingBundleBackend(RecordingBackend):
    async def evaluate(self, **kwargs: Any) -> EvalResult:
        result = await super().evaluate(**kwargs)
        kwargs["prompts"]["system_prompt"] = "backend-mutated prompt"
        return result

    async def optimize_candidates(self, **kwargs: Any) -> OptimizationResult:
        result = await super().optimize_candidates(**kwargs)
        kwargs["baseline_prompts"]["system_prompt"] = "optimizer-mutated baseline"
        return result


class FailingCandidateBackend(RecordingBackend):
    def __init__(
        self,
        *,
        fail_on: tuple[str, str],
        failure: Exception,
        delegate: RecordingBackend,
    ) -> None:
        super().__init__(
            candidates=delegate.candidates,
            results=delegate.results,
            optimization_cost=delegate.optimization_cost,
        )
        self.fail_on = fail_on
        self.failure = failure

    async def evaluate(self, **kwargs: Any) -> EvalResult:
        if (kwargs["prompt_id"], kwargs["split"]) == self.fail_on:
            self.calls.append(
                (
                    "evaluate",
                    kwargs["prompt_id"],
                    kwargs["split"],
                    dict(kwargs["prompts"]),
                    Path(kwargs["artifact_dir"]),
                    kwargs["trace"],
                )
            )
            raise self.failure
        return await super().evaluate(**kwargs)


class AllCandidatesFailingBackend(RecordingBackend):
    async def evaluate(self, **kwargs: Any) -> EvalResult:
        if kwargs["prompt_id"] != "baseline":
            self.calls.append(
                (
                    "evaluate",
                    kwargs["prompt_id"],
                    kwargs["split"],
                    dict(kwargs["prompts"]),
                    Path(kwargs["artifact_dir"]),
                    kwargs["trace"],
                )
            )
            raise RuntimeError(f"{kwargs['prompt_id']} evaluation unavailable")
        return await super().evaluate(**kwargs)


@pytest.mark.asyncio
async def test_execute_pipeline_uses_train_only_evidence_and_full_candidate_reevaluation(tmp_path: Path):
    request = _request(tmp_path, update_source=False)
    backend = _safe_backend()

    report = await execute_pipeline(request, evaluator=backend, optimizer=backend)

    assert [(call[0], *call[1:3]) for call in backend.calls] == [
        ("evaluate", "baseline", "train"),
        ("evaluate", "baseline", "validation"),
        ("optimize", {"system_prompt": "baseline\n"}, tmp_path / "out" / "runs" / "ordered_run" / "optimizer"),
        ("evaluate", "candidate_a", "train"),
        ("evaluate", "candidate_a", "validation"),
        ("evaluate", "candidate_b", "train"),
        ("evaluate", "candidate_b", "validation"),
    ]
    assert backend.failure_summary is not None
    assert backend.failure_summary["total_failed_cases"] == 1
    assert backend.failure_summary["by_category"] == {"train_only": 1}
    assert "validation_only" not in str(backend.failure_summary)
    assert report.selected_candidate == "candidate_a"
    assert report.audit["duration_seconds"] > 0
    assert report.audit["config_snapshot"] == {"seed": 91}
    assert report.audit["input_hashes"]["target_prompts"]["system_prompt"] == hashlib.sha256(b"baseline\n").hexdigest()
    artifact_dirs = [call[4] for call in backend.calls if call[0] == "evaluate"]
    assert len(artifact_dirs) == len(set(artifact_dirs)) == 6
    assert report.audit["sdk_result_availability"]["full_train_eval_result"] is True
    assert report.audit["sdk_result_availability"]["full_per_case_validation_delta"] is True


@pytest.mark.asyncio
async def test_backend_cannot_mutate_canonical_baseline_or_candidate_bundles(tmp_path: Path):
    request = _request(tmp_path, update_source=True)
    delegate = _safe_backend(candidate_count=1)
    backend = MutatingBundleBackend(
        candidates=delegate.candidates,
        results=delegate.results,
        optimization_cost=delegate.optimization_cost,
    )

    report = await execute_pipeline(request, evaluator=backend, optimizer=backend)

    prompt_calls = [
        (call[1], call[2], call[3]["system_prompt"])
        for call in backend.calls
        if call[0] == "evaluate"
    ]
    assert prompt_calls == [
        ("baseline", "train", "baseline\n"),
        ("baseline", "validation", "baseline\n"),
        ("candidate_a", "train", "safe prompt"),
        ("candidate_a", "validation", "safe prompt"),
    ]
    optimize_call = next(call for call in backend.calls if call[0] == "optimize")
    assert optimize_call[1] == {"system_prompt": "baseline\n"}
    assert report.candidates[0]["candidate"].bundle() == {"system_prompt": "safe prompt"}
    assert report.candidates[0]["prompt_bundle"] == {"system_prompt": "safe prompt"}
    assert report.audit["candidate_prompts"]["candidate_a"] == {
        "system_prompt": "safe prompt"
    }
    assert report.audit["candidate_prompt_hashes"]["candidate_a"] == {
        "system_prompt": hashlib.sha256(b"safe prompt").hexdigest()
    }
    candidate_artifact = report.audit["candidate_artifacts"]["candidate_a"]
    artifact_prompt = (
        Path(request.output_dir)
        / "runs"
        / request.run_id
        / "candidate_prompts"
        / candidate_artifact
        / "system_prompt.txt"
    )
    assert artifact_prompt.read_text(encoding="utf-8") == "safe prompt"
    assert Path(request.target_prompt_paths["system_prompt"]).read_text(encoding="utf-8") == "safe prompt"


@pytest.mark.asyncio
@pytest.mark.parametrize("failed_split", ["train", "validation"])
async def test_candidate_evaluation_error_rejects_candidate_and_continues(
    tmp_path: Path,
    failed_split: str,
):
    request = _request(tmp_path, update_source=True)
    backend = FailingCandidateBackend(
        fail_on=("candidate_a", failed_split),
        failure=RuntimeError(f"synthetic {failed_split} evaluation failure"),
        delegate=_safe_backend(candidate_count=2),
    )

    report = await execute_pipeline(request, evaluator=backend, optimizer=backend)

    candidate_calls = [
        (call[1], call[2]) for call in backend.calls if call[0] == "evaluate" and call[1] != "baseline"
    ]
    expected_a_calls = [("candidate_a", "train")]
    if failed_split == "validation":
        expected_a_calls.append(("candidate_a", "validation"))
    assert candidate_calls == expected_a_calls + [
        ("candidate_b", "train"),
        ("candidate_b", "validation"),
    ]
    assert report.selected_candidate == "candidate_b"
    assert report.writeback.status == "applied"
    assert Path(request.target_prompt_paths["system_prompt"]).read_text(encoding="utf-8") == "other prompt"

    failed_record = report.candidates[0]
    assert failed_record["candidate"].candidate_id == "candidate_a"
    assert failed_record["evaluation_error"]["stage"] == failed_split
    assert failed_record["evaluation_error"]["type"] == "RuntimeError"
    assert failed_record["evaluation_error"]["cost_complete"] is False
    assert ("train_result" in failed_record) is (failed_split == "validation")
    assert "validation_result" not in failed_record

    failed_decision = report.gate_decisions[0]
    assert failed_decision.accepted is False
    assert failed_decision.gate_status == "not_applied"
    assert failed_decision.gate_not_applied_reason == "evaluation_error"
    assert failed_decision.train_score_delta is None
    assert failed_decision.validation_score_delta is None
    assert any("evaluation_error" in reason and failed_split in reason for reason in failed_decision.reasons)
    assert failed_decision.not_applied_checks

    audit_failure = report.audit["candidate_evaluation_failures"]["candidate_a"]
    assert audit_failure == failed_record["evaluation_error"]
    assert report.cost_summary.complete is False
    expected_known_cost = 0.05 if failed_split == "validation" else 0.04
    assert report.cost_summary.total == expected_known_cost
    assert report.audit["cost"]["total"] is None
    assert report.audit["cost"]["known_run_cost"] == expected_known_cost
    markdown = report_module.render_markdown(report)
    assert "evaluation_error" in markdown
    assert "n/a" in markdown


@pytest.mark.asyncio
async def test_candidate_evaluation_error_makes_later_budget_gate_fail_closed(
    tmp_path: Path,
):
    request = _request(
        tmp_path,
        update_source=True,
        gate_config={
            "min_val_score_improvement": 0.0,
            "max_score_drop_per_case": 1.0,
            "max_total_cost": 10.0,
        },
    )
    backend = FailingCandidateBackend(
        fail_on=("candidate_a", "train"),
        failure=RuntimeError("unknown failed evaluation cost"),
        delegate=_safe_backend(candidate_count=2),
    )

    report = await execute_pipeline(request, evaluator=backend, optimizer=backend)

    assert report.selected_candidate is None
    assert report.writeback.status == "rejected"
    assert report.gate_decisions[0].gate_not_applied_reason == "evaluation_error"
    assert any("cost_unavailable" in reason for reason in report.gate_decisions[1].reasons)
    assert report.cost_summary.complete is False
    assert Path(request.target_prompt_paths["system_prompt"]).read_bytes() == b"baseline\n"


@pytest.mark.asyncio
async def test_all_candidate_evaluation_errors_finalize_rejected_audit_without_writeback(
    tmp_path: Path,
):
    request = _request(tmp_path, update_source=True)
    delegate = _safe_backend(candidate_count=2)
    backend = AllCandidatesFailingBackend(
        candidates=delegate.candidates,
        results=delegate.results,
        optimization_cost=delegate.optimization_cost,
    )

    report = await execute_pipeline(request, evaluator=backend, optimizer=backend)

    assert report.selected_candidate is None
    assert report.writeback.status == "rejected"
    assert report.audit["writeback_journal"]["state"] == "rejected"
    assert report.audit["writeback_journal"]["report_phase"] == "final"
    assert set(report.audit["candidate_evaluation_failures"]) == {
        "candidate_a",
        "candidate_b",
    }
    assert all("evaluation_error" in record for record in report.candidates)
    assert all("train_result" not in record for record in report.candidates)
    assert all(decision.gate_status == "not_applied" for decision in report.gate_decisions)
    assert report.cost_summary.complete is False
    assert report.cost_summary.total == 0.02
    assert Path(request.target_prompt_paths["system_prompt"]).read_bytes() == b"baseline\n"
    run_dir = Path(request.output_dir) / "runs" / request.run_id
    assert (run_dir / "optimization_report.json").is_file()
    assert (run_dir / "audit.json").is_file()
    assert (run_dir / "writeback.json").is_file()


@pytest.mark.asyncio
async def test_candidate_evaluation_error_does_not_persist_backend_secret_text(
    tmp_path: Path,
):
    request = _request(tmp_path)
    secret = "api_key=sk-private-value token=github-private-value"
    backend = FailingCandidateBackend(
        fail_on=("candidate_a", "train"),
        failure=RuntimeError(secret),
        delegate=_safe_backend(candidate_count=2),
    )

    report = await execute_pipeline(request, evaluator=backend, optimizer=backend)

    failure = report.candidates[0]["evaluation_error"]
    assert failure["message"] == "candidate evaluation failed; backend details withheld"
    assert failure["message_sha256"] == hashlib.sha256(secret.encode("utf-8")).hexdigest()
    serialized = report_module.report_to_json(report) + report_module.render_markdown(report)
    serialized += (Path(request.output_dir) / "runs" / request.run_id / "audit.json").read_text(
        encoding="utf-8"
    )
    assert "sk-private-value" not in serialized
    assert "github-private-value" not in serialized


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_kind", ["cas", "direct_restore", "chained_restore"])
async def test_candidate_prompt_integrity_failures_abort_run(
    tmp_path: Path,
    failure_kind: str,
):
    request = _request(tmp_path, update_source=True)
    if failure_kind == "cas":
        failure: Exception = ConcurrentPromptUpdateError("source changed")
    elif failure_kind == "direct_restore":
        failure = PromptRestorationError("restore failed")
    else:
        failure = RuntimeError("candidate evaluation failed")
        failure.__cause__ = PromptRestorationError("restore failed")
    backend = FailingCandidateBackend(
        fail_on=("candidate_a", "train"),
        failure=failure,
        delegate=_safe_backend(candidate_count=2),
    )

    with pytest.raises(Exception) as error_info:
        await execute_pipeline(request, evaluator=backend, optimizer=backend)

    assert error_info.value is failure
    assert not any(call[0] == "evaluate" and call[1] == "candidate_b" for call in backend.calls)
    assert Path(request.target_prompt_paths["system_prompt"]).read_bytes() == b"baseline\n"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mutation_target", "error_pattern"),
    [
        ("prompt", "source prompt integrity changed"),
        ("input", "immutable input snapshot.*train"),
    ],
)
async def test_candidate_failure_with_state_drift_aborts_run(
    tmp_path: Path,
    mutation_target: str,
    error_pattern: str,
):
    request = _request(tmp_path, update_source=True)

    class StateDriftBackend(FailingCandidateBackend):
        async def evaluate(self, **kwargs: Any) -> EvalResult:
            if (kwargs["prompt_id"], kwargs["split"]) == self.fail_on:
                if mutation_target == "prompt":
                    Path(request.target_prompt_paths["system_prompt"]).write_text(
                        "external prompt update",
                        encoding="utf-8",
                    )
                else:
                    Path(kwargs["dataset_path"]).write_text("{}", encoding="utf-8")
            return await super().evaluate(**kwargs)

    backend = StateDriftBackend(
        fail_on=("candidate_a", "train"),
        failure=RuntimeError("ordinary evaluation failure"),
        delegate=_safe_backend(candidate_count=2),
    )

    with pytest.raises((ConcurrentPromptUpdateError, ValueError), match=error_pattern):
        await execute_pipeline(request, evaluator=backend, optimizer=backend)

    assert not any(call[0] == "evaluate" and call[1] == "candidate_b" for call in backend.calls)


@pytest.mark.asyncio
async def test_rejected_candidate_never_changes_source_when_writeback_requested(tmp_path: Path):
    request = _request(
        tmp_path,
        update_source=True,
        gate_config={"min_val_score_improvement": 0.5, "max_total_cost": None},
    )
    backend = _safe_backend(candidate_validation_score=0.0)
    before = (tmp_path / "prompt.txt").read_bytes()

    report = await execute_pipeline(request, evaluator=backend, optimizer=backend)

    assert report.selected_candidate is None
    assert report.writeback.status == "rejected"
    assert report.writeback.before_hashes == {
        "system_prompt": hashlib.sha256(before).hexdigest(),
    }
    assert report.audit["writeback_journal"]["report_phase"] == "final"
    assert (tmp_path / "prompt.txt").read_bytes() == before


@pytest.mark.asyncio
async def test_accepted_candidate_is_not_written_when_writeback_not_requested(tmp_path: Path):
    request = _request(tmp_path, update_source=False)
    backend = _safe_backend()
    before = (tmp_path / "prompt.txt").read_bytes()

    report = await execute_pipeline(request, evaluator=backend, optimizer=backend)

    assert report.selected_candidate == "candidate_a"
    assert report.writeback.status == "not_requested"
    assert report.writeback.before_hashes == {
        "system_prompt": hashlib.sha256(before).hexdigest(),
    }
    assert (tmp_path / "prompt.txt").read_bytes() == before


@pytest.mark.asyncio
async def test_writeback_happens_only_after_prewrite_audit_succeeds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    request = _request(tmp_path, update_source=True)
    backend = _safe_backend()
    events: list[str] = []
    artifact_paths = object()

    def prepare(report, output_dir):
        events.append("prepare")
        assert report.selected_candidate == "candidate_a"
        assert Path(output_dir) == tmp_path / "out"
        return artifact_paths

    def commit(snapshot, prompts):
        events.append("commit")
        assert prompts == {"system_prompt": "safe prompt"}
        return WritebackResult(
            status="applied",
            before_hashes=snapshot.hashes(),
            after_hashes=snapshot.hashes(),
        )

    def finalize(report, paths):
        events.append("finalize")
        assert paths is artifact_paths
        assert report.writeback.status == "applied"

    def persist_journal(paths, journal):
        events.append(f"journal:{journal['state']}")
        assert paths is artifact_paths

    def persist_outcome(report, paths):
        events.append(f"outcome:{report.audit['writeback_journal']['state']}")
        assert paths is artifact_paths

    monkeypatch.setattr(pipeline_module, "prepare_run_artifacts", prepare)
    monkeypatch.setattr(pipeline_module, "commit_prompt_bundle", commit)
    monkeypatch.setattr(pipeline_module, "finalize_run_artifacts", finalize)
    monkeypatch.setattr(pipeline_module, "persist_writeback_journal", persist_journal)
    monkeypatch.setattr(pipeline_module, "persist_writeback_outcome", persist_outcome)

    report = await execute_pipeline(request, evaluator=backend, optimizer=backend)

    assert events == [
        "prepare",
        "journal:committing",
        "commit",
        "journal:applied",
        "outcome:applied",
        "finalize",
    ]
    assert report.writeback.status == "applied"


@pytest.mark.asyncio
async def test_prepare_failure_prevents_source_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    request = _request(tmp_path, update_source=True)
    backend = _safe_backend()
    before = (tmp_path / "prompt.txt").read_bytes()
    commit_called = False

    def fail_prepare(report, output_dir):
        raise OSError("audit unavailable")

    def forbidden_commit(snapshot, prompts):
        nonlocal commit_called
        commit_called = True
        raise AssertionError("commit must not run")

    monkeypatch.setattr(pipeline_module, "prepare_run_artifacts", fail_prepare)
    monkeypatch.setattr(pipeline_module, "commit_prompt_bundle", forbidden_commit)

    with pytest.raises(OSError, match="audit unavailable"):
        await execute_pipeline(request, evaluator=backend, optimizer=backend)

    assert commit_called is False
    assert (tmp_path / "prompt.txt").read_bytes() == before


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("max_total_cost", "accepted", "reason_fragment"),
    [
        (1.0, False, "cost_unavailable"),
        (None, True, None),
    ],
)
async def test_incomplete_optimizer_cost_is_fail_closed_only_when_budget_configured(
    tmp_path: Path,
    max_total_cost: float | None,
    accepted: bool,
    reason_fragment: str | None,
):
    request = _request(
        tmp_path,
        gate_config={
            "min_val_score_improvement": 0.0,
            "max_score_drop_per_case": 1.0,
            "max_total_cost": max_total_cost,
        },
    )
    backend = _safe_backend(
        candidate_count=1,
        optimization_cost=CostSummary(optimizer=0.2, total=0.2, complete=False),
    )

    report = await execute_pipeline(request, evaluator=backend, optimizer=backend)

    assert report.gate_decisions[0].accepted is accepted
    if reason_fragment is not None:
        assert any(reason_fragment in reason for reason in report.gate_decisions[0].reasons)
    else:
        assert all("cost_unavailable" not in reason for reason in report.gate_decisions[0].reasons)
        payload = json.loads(report_module.report_to_json(report))
        assert payload["cost_summary"]["complete"] is False
        assert payload["cost_summary"]["reported_optimizer_cost"] == 0.2
        assert report.audit["cost"]["complete"] is False
        assert report.audit["cost"]["total"] is None
        assert report.audit["cost"]["reported_optimizer_cost"] == 0.2
        markdown = report_module.render_markdown(report)
        assert "Reported optimizer cost (incomplete; not total run cost): 0.200" in markdown
        assert "Total cost:" not in markdown


@pytest.mark.asyncio
async def test_optimizer_cost_is_counted_once_across_candidates(tmp_path: Path):
    request = _request(tmp_path)
    backend = _safe_backend(
        baseline_train_cost=0.1,
        baseline_validation_cost=0.2,
        candidate_train_cost=0.04,
        candidate_validation_cost=0.05,
        optimization_cost=CostSummary(optimizer=0.3, total=0.3, complete=True),
    )

    report = await execute_pipeline(request, evaluator=backend, optimizer=backend)

    assert [decision.total_run_cost for decision in report.gate_decisions] == [0.69, 0.78]
    assert report.cost_summary == CostSummary(
        optimizer=0.3,
        evaluator=0.48,
        agent=0.0,
        total=0.78,
        complete=True,
    )
    assert report.audit["total_run_cost"] == 0.78
    assert report.audit["known_run_cost"] is None
    assert report.audit["total_run_cost_complete"] is True
    assert report.audit["cost"]["total"] == 0.78
    assert report.audit["cost"]["reported_optimizer_cost"] is None
    markdown = report_module.render_markdown(report)
    assert "Total cost: 0.780" in markdown
    assert "Reported optimizer cost" not in markdown


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("baseline_case_ids", "candidate_case_ids", "reason_fragment"),
    [
        (["a"], ["a", "a"], "duplicate case IDs"),
        (["a", "b"], ["a"], "case ID set mismatch"),
        (["a"], ["a", "extra"], "case ID set mismatch"),
    ],
)
async def test_incomparable_case_results_are_rejected_without_delta_error(
    tmp_path: Path,
    baseline_case_ids: list[str],
    candidate_case_ids: list[str],
    reason_fragment: str,
):
    request = _request(tmp_path)
    for dataset_path in (Path(request.train_path), Path(request.validation_path)):
        dataset_path.write_text(
            json.dumps(
                {
                    "eval_cases": [
                        {"eval_id": case_id, "session_input": {"state": {}}}
                        for case_id in dict.fromkeys(baseline_case_ids)
                    ]
                }
            ),
            encoding="utf-8",
        )
    candidate = CandidatePrompt("candidate_a", "safe prompt", "safe", "diff")
    results = {
        ("baseline", "train"): _eval("baseline", "train", baseline_case_ids, 0.0),
        ("baseline", "validation"): _eval("baseline", "validation", baseline_case_ids, 0.0),
        ("candidate_a", "train"): _eval("candidate_a", "train", candidate_case_ids, 1.0),
        ("candidate_a", "validation"): _eval("candidate_a", "validation", candidate_case_ids, 1.0),
    }
    backend = RecordingBackend(candidates=[candidate], results=results)

    report = await execute_pipeline(request, evaluator=backend, optimizer=backend)

    assert report.selected_candidate is None
    assert report.per_case_deltas == []
    assert report.gate_decisions[0].accepted is False
    assert any(reason_fragment in reason for reason in report.gate_decisions[0].reasons)


@pytest.mark.asyncio
async def test_empty_evalsets_are_rejected_before_backend_calls(tmp_path: Path):
    request = _request(tmp_path)
    Path(request.train_path).write_text('{"eval_cases": []}', encoding="utf-8")
    backend = _safe_backend(candidate_count=1)

    with pytest.raises(ValueError, match="train evalset.*must not be empty"):
        await execute_pipeline(request, evaluator=backend, optimizer=backend)

    assert backend.calls == []


@pytest.mark.asyncio
async def test_duplicate_candidate_ids_are_rejected(tmp_path: Path):
    request = _request(tmp_path)
    backend = _safe_backend(candidate_count=1)
    backend.candidates = [backend.candidates[0], backend.candidates[0]]

    with pytest.raises(ValueError, match="duplicate candidate_id"):
        await execute_pipeline(request, evaluator=backend, optimizer=backend)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("prompt_fields", "message"),
    [
        ({"other_prompt": "candidate"}, "bundle fields"),
        ({"system_prompt": ""}, "non-empty string"),
    ],
)
async def test_candidate_bundle_must_match_target_snapshot(
    tmp_path: Path,
    prompt_fields: dict[str, str],
    message: str,
):
    request = _request(tmp_path)
    candidate = CandidatePrompt(
        candidate_id="candidate_a",
        prompt="candidate",
        rationale="test",
        prompt_diff="diff",
        prompt_fields=prompt_fields,
    )
    backend = _safe_backend(candidate_count=0)
    backend.candidates = [candidate]

    with pytest.raises(ValueError, match=message):
        await execute_pipeline(request, evaluator=backend, optimizer=backend)


@pytest.mark.asyncio
async def test_target_prompt_hashes_cannot_overwrite_dataset_hashes(tmp_path: Path):
    request = _request(tmp_path)
    prompt_path = tmp_path / "reserved_name_prompt.txt"
    prompt_path.write_text("baseline", encoding="utf-8")
    request = replace(request, target_prompt_paths={"train": prompt_path})
    backend = _safe_backend(candidate_count=1)
    backend.candidates = [
        CandidatePrompt(
            candidate_id="candidate_a",
            prompt="safe prompt",
            rationale="safe",
            prompt_diff="diff",
            prompt_fields={"train": "safe prompt"},
        )
    ]

    report = await execute_pipeline(request, evaluator=backend, optimizer=backend)

    expected_dataset_hash = hashlib.sha256(Path(request.train_path).read_bytes()).hexdigest()
    expected_prompt_hash = hashlib.sha256(prompt_path.read_bytes()).hexdigest()
    assert report.audit["input_hashes"]["train"] == expected_dataset_hash
    assert report.audit["input_hashes"]["target_prompts"]["train"] == expected_prompt_hash


@pytest.mark.asyncio
async def test_finalize_failure_leaves_explicit_prepared_writeback_journal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    request = _request(tmp_path, update_source=True)
    backend = _safe_backend(candidate_count=1)

    def fail_finalize(report, paths):
        raise OSError("final report unavailable")

    monkeypatch.setattr(pipeline_module, "finalize_run_artifacts", fail_finalize)

    with pytest.raises(OSError, match="final report unavailable"):
        await execute_pipeline(request, evaluator=backend, optimizer=backend)

    run_dir = Path(request.output_dir) / "runs" / request.run_id
    prewrite = json.loads((run_dir / "pre_write_report.json").read_text(encoding="utf-8"))
    journal = json.loads((run_dir / "writeback_journal.json").read_text(encoding="utf-8"))
    assert prewrite["audit"]["writeback_journal"]["state"] == "pending"
    assert "pending" in prewrite["writeback"]["error"]
    assert journal["state"] == "applied"
    assert (tmp_path / "prompt.txt").read_bytes() == b"safe prompt"
    assert journal["after_hashes"] == {
        "system_prompt": hashlib.sha256(b"safe prompt").hexdigest(),
    }


@pytest.mark.asyncio
async def test_writeback_conflict_is_caught_and_durably_reported(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    request = _request(tmp_path, update_source=True)
    backend = _safe_backend(candidate_count=1)

    def conflict(snapshot, prompts):
        raise ConcurrentPromptUpdateError("source changed")

    monkeypatch.setattr(pipeline_module, "commit_prompt_bundle", conflict)

    report = await execute_pipeline(request, evaluator=backend, optimizer=backend)

    journal_path = Path(request.output_dir) / "runs" / request.run_id / "writeback_journal.json"
    journal = json.loads(journal_path.read_text(encoding="utf-8"))
    assert report.writeback.status == "rejected"
    assert "source changed" in (report.writeback.error or "")
    assert journal["state"] == "conflict"
    assert journal["error"] == report.writeback.error


@pytest.mark.asyncio
async def test_real_source_drift_between_prepare_and_commit_is_terminal_conflict(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    request = _request(tmp_path, update_source=True)
    backend = _safe_backend(candidate_count=1)
    original_prepare = pipeline_module.prepare_run_artifacts

    def prepare_then_external_change(report, output_dir):
        paths = original_prepare(report, output_dir)
        (tmp_path / "prompt.txt").write_bytes(b"external writer\n")
        return paths

    monkeypatch.setattr(pipeline_module, "prepare_run_artifacts", prepare_then_external_change)

    report = await execute_pipeline(request, evaluator=backend, optimizer=backend)

    journal_path = Path(request.output_dir) / "runs" / request.run_id / "writeback_journal.json"
    journal = json.loads(journal_path.read_text(encoding="utf-8"))
    assert report.writeback.status == "rejected"
    assert report.writeback.after_hashes == {
        "system_prompt": hashlib.sha256(b"external writer\n").hexdigest(),
    }
    assert journal["state"] == "conflict"
    assert journal["after_hashes"] == report.writeback.after_hashes
    assert (tmp_path / "prompt.txt").read_bytes() == b"external writer\n"


@pytest.mark.asyncio
async def test_terminal_journal_survives_nonessential_writeback_artifact_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    request = _request(tmp_path, update_source=True)
    backend = _safe_backend(candidate_count=1)

    def fail_outcome(report, paths):
        raise OSError("writeback convenience artifact unavailable")

    monkeypatch.setattr(pipeline_module, "persist_writeback_outcome", fail_outcome)

    with pytest.raises(OSError, match="convenience artifact"):
        await execute_pipeline(request, evaluator=backend, optimizer=backend)

    journal_path = Path(request.output_dir) / "runs" / request.run_id / "writeback_journal.json"
    journal = json.loads(journal_path.read_text(encoding="utf-8"))
    assert (tmp_path / "prompt.txt").read_bytes() == b"safe prompt"
    assert journal["state"] == "applied"
    assert journal["after_hashes"] == {
        "system_prompt": hashlib.sha256(b"safe prompt").hexdigest(),
    }


@pytest.mark.asyncio
async def test_writeback_journal_is_committing_before_source_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    request = _request(tmp_path, update_source=True)
    backend = _safe_backend(candidate_count=1)

    def commit(snapshot, prompts):
        journal_path = Path(request.output_dir) / "runs" / request.run_id / "writeback_journal.json"
        journal = json.loads(journal_path.read_text(encoding="utf-8"))
        assert journal["state"] == "committing"
        return WritebackResult(
            status="applied",
            before_hashes=snapshot.hashes(),
            after_hashes={name: hashlib.sha256(prompts[name].encode("utf-8")).hexdigest() for name in prompts},
        )

    monkeypatch.setattr(pipeline_module, "commit_prompt_bundle", commit)

    report = await execute_pipeline(request, evaluator=backend, optimizer=backend)

    assert report.writeback.status == "applied"


@pytest.mark.asyncio
@pytest.mark.parametrize("writeback_status", ["rolled_back", "rollback_failed"])
async def test_failed_writeback_is_persisted_in_final_report_and_journal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    writeback_status: str,
):
    request = _request(tmp_path, update_source=True)
    backend = _safe_backend(candidate_count=1)
    monkeypatch.setattr(
        pipeline_module,
        "commit_prompt_bundle",
        lambda snapshot, prompts: WritebackResult(
            status=writeback_status,
            before_hashes=snapshot.hashes(),
            after_hashes=snapshot.hashes(),
            error="simulated commit failure",
        ),
    )

    report = await execute_pipeline(request, evaluator=backend, optimizer=backend)

    run_dir = Path(request.output_dir) / "runs" / request.run_id
    final_payload = json.loads((run_dir / "optimization_report.json").read_text(encoding="utf-8"))
    writeback_payload = json.loads((run_dir / "writeback.json").read_text(encoding="utf-8"))
    journal = json.loads((run_dir / "writeback_journal.json").read_text(encoding="utf-8"))
    assert report.writeback.status == writeback_status
    assert final_payload["writeback"]["status"] == writeback_status
    assert writeback_payload["status"] == writeback_status
    assert journal["state"] == writeback_status


@pytest.mark.asyncio
async def test_standard_evalset_protected_metadata_is_applied_to_gate(tmp_path: Path):
    request = _request(tmp_path)
    Path(request.train_path).write_text(
        json.dumps({"eval_cases": [{"eval_id": "train", "session_input": {"state": {}}}]}),
        encoding="utf-8",
    )
    Path(request.validation_path).write_text(
        """{
  "eval_set_id": "validation",
  "eval_cases": [
    {
      "eval_id": "protected",
      "session_input": {"state": {"eval_optimize_protected": true}}
    },
    {
      "eval_id": "improved",
      "session_input": {"state": {"eval_optimize_protected": false}}
    }
  ]
}
""",
        encoding="utf-8",
    )
    candidate = CandidatePrompt("candidate_a", "safe prompt", "safe", "diff")
    results = {
        ("baseline", "train"): _eval_scores("baseline", "train", [("train", 1.0)]),
        ("baseline", "validation"): _eval_scores(
            "baseline",
            "validation",
            [("protected", 0.8), ("improved", 0.0)],
        ),
        ("candidate_a", "train"): _eval_scores("candidate_a", "train", [("train", 1.0)]),
        ("candidate_a", "validation"): _eval_scores(
            "candidate_a",
            "validation",
            [("protected", 0.7), ("improved", 1.0)],
        ),
    }
    backend = RecordingBackend(candidates=[candidate], results=results)

    report = await execute_pipeline(request, evaluator=backend, optimizer=backend)

    assert report.selected_candidate is None
    assert report.gate_decisions[0].protected_regressions == ["protected"]
    assert report.audit["gate_config_snapshot"]["protected_case_ids"] == ["protected"]


@pytest.mark.asyncio
async def test_baseline_results_must_cover_every_snapshotted_dataset_case(tmp_path: Path):
    request = _request(tmp_path, update_source=True)
    Path(request.validation_path).write_text(
        json.dumps(
            {
                "eval_cases": [
                    {"eval_id": "validation_case", "session_input": {"state": {}}},
                    {
                        "eval_id": "protected_missing",
                        "session_input": {
                            "state": {"eval_optimize_protected": True}
                        },
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    backend = _safe_backend(candidate_count=1)

    with pytest.raises(ValueError, match="baseline.*validation.*dataset case IDs"):
        await execute_pipeline(request, evaluator=backend, optimizer=backend)

    assert (tmp_path / "prompt.txt").read_bytes() == b"baseline\n"


@pytest.mark.asyncio
async def test_backend_neutral_core_rejects_same_resolved_train_and_validation_path(tmp_path: Path):
    request = _request(tmp_path)
    request = replace(request, validation_path=request.train_path)
    backend = _safe_backend(candidate_count=1)

    with pytest.raises(ValueError, match="train and validation.*different"):
        await execute_pipeline(request, evaluator=backend, optimizer=backend)

    assert backend.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("field_names", [["Foo", "foo"], ["CON"]])
async def test_backend_neutral_core_rejects_unsafe_or_colliding_target_fields(
    tmp_path: Path,
    field_names: list[str],
):
    request = _request(tmp_path)
    target_paths: dict[str, Path] = {}
    for index, field_name in enumerate(field_names):
        prompt_path = tmp_path / f"prompt-{index}.txt"
        prompt_path.write_text(f"prompt {index}", encoding="utf-8")
        target_paths[field_name] = prompt_path
    request = replace(request, target_prompt_paths=target_paths)
    backend = _safe_backend(candidate_count=1)

    with pytest.raises(ValueError, match="target prompt field.*unsafe|case-insensitively unique"):
        await execute_pipeline(request, evaluator=backend, optimizer=backend)

    assert backend.calls == []


@pytest.mark.asyncio
async def test_backend_neutral_core_rejects_target_fields_for_same_resolved_file(
    tmp_path: Path,
):
    request = _request(tmp_path)
    prompt_path = request.target_prompt_paths["system_prompt"]
    request = replace(
        request,
        target_prompt_paths={"system_prompt": prompt_path, "router_prompt": prompt_path},
    )
    backend = _safe_backend(candidate_count=1)

    with pytest.raises(ValueError, match="target prompt fields.*same resolved file"):
        await execute_pipeline(request, evaluator=backend, optimizer=backend)

    assert backend.calls == []


@pytest.mark.asyncio
async def test_backend_neutral_core_rejects_fake_backend_seed_mismatch(tmp_path: Path):
    request = _request(tmp_path)
    backend = FakeBackend(seed=7)

    with pytest.raises(ValueError, match="backend seed.*effective seed"):
        await execute_pipeline(request, evaluator=backend, optimizer=backend)


@pytest.mark.asyncio
async def test_fake_gate_is_derived_from_immutable_optimizer_snapshot(tmp_path: Path):
    base_request = _request(tmp_path)
    backend = _safe_backend(candidate_count=1)
    Path(base_request.optimizer_config_path).write_text(
        json.dumps({"seed": 91, "gate": {"max_total_cost": None}}),
        encoding="utf-8",
    )
    request, selected_backend = build_pipeline_request_and_backend(
        train_path=base_request.train_path,
        val_path=base_request.validation_path,
        optimizer_config_path=base_request.optimizer_config_path,
        prompt_path=base_request.target_prompt_paths["system_prompt"],
        output_dir=base_request.output_dir,
        mode="fake",
        run_id="gate_snapshot_run",
        backend=backend,
    )
    Path(base_request.optimizer_config_path).write_text(
        json.dumps({"seed": 91, "gate": {"max_total_cost": 0.0}}),
        encoding="utf-8",
    )

    report = await execute_pipeline(
        request,
        evaluator=selected_backend,
        optimizer=selected_backend,
    )

    assert report.selected_candidate is None
    assert report.audit["gate_config_snapshot"]["max_total_cost"] == 0.0


@pytest.mark.asyncio
async def test_configured_protected_case_ids_must_exist_in_validation_metadata(tmp_path: Path):
    request = _request(
        tmp_path,
        gate_config={
            "min_val_score_improvement": 0.0,
            "protected_case_ids": ["missing"],
            "max_score_drop_per_case": 1.0,
            "max_total_cost": None,
        },
    )
    Path(request.validation_path).write_text(
        json.dumps({"eval_cases": [{"eval_id": "present", "session_input": {"state": {}}}]}),
        encoding="utf-8",
    )
    backend = _safe_backend(candidate_count=1)

    with pytest.raises(ValueError, match="missing validation cases.*missing"):
        await execute_pipeline(request, evaluator=backend, optimizer=backend)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"eval_cases": [{"eval_id": 7, "session_input": {"state": {}}}]},
        {"eval_cases": [{"eval_id": "", "session_input": {"state": {}}}]},
        {"eval_cases": [{"eval_id": "case", "session_input": {"state": {"eval_optimize_protected": "yes"}}}]},
        {"eval_cases": [{"eval_id": "case", "session_input": {"state": {"eval_optimize_protected": None}}}]},
        {"cases": [{"case_id": 7, "protected": False}]},
        {"cases": [{"case_id": "case", "protected": "yes"}]},
        {"cases": [{"case_id": "case", "protected": 1}]},
    ],
)
async def test_validation_metadata_rejects_coerced_ids_and_protected_flags(
    tmp_path: Path,
    payload: dict[str, object],
):
    request = _request(tmp_path)
    Path(request.validation_path).write_text(json.dumps(payload), encoding="utf-8")
    backend = _safe_backend(candidate_count=1)

    with pytest.raises(ValueError, match="case ID|protected.*boolean"):
        await execute_pipeline(request, evaluator=backend, optimizer=backend)


@pytest.mark.asyncio
async def test_validation_metadata_rejects_ambiguous_dual_schema(tmp_path: Path):
    request = _request(tmp_path)
    Path(request.validation_path).write_text(
        json.dumps({"eval_cases": [], "cases": []}),
        encoding="utf-8",
    )
    backend = _safe_backend(candidate_count=1)

    with pytest.raises(ValueError, match="exactly one.*eval_cases.*cases"):
        await execute_pipeline(request, evaluator=backend, optimizer=backend)


@pytest.mark.asyncio
@pytest.mark.parametrize("candidate_ids", [["CON"], ["candidate."], ["Candidate", "candidate"], ["bad/name"]])
async def test_candidate_ids_are_artifact_safe_and_casefold_unique(
    tmp_path: Path,
    candidate_ids: list[str],
):
    request = _request(tmp_path)
    backend = _safe_backend(candidate_count=0)
    backend.candidates = [
        CandidatePrompt(candidate_id, "safe prompt", "safe", "diff") for candidate_id in candidate_ids
    ]

    with pytest.raises(ValueError, match="candidate_id"):
        await execute_pipeline(request, evaluator=backend, optimizer=backend)


@pytest.mark.asyncio
async def test_candidate_prompts_must_be_valid_utf8_before_evaluation(tmp_path: Path):
    request = _request(tmp_path)
    backend = _safe_backend(candidate_count=0)
    backend.candidates = [
        CandidatePrompt("candidate", "bad\ud800prompt", "safe", "diff")
    ]

    with pytest.raises(ValueError, match="valid UTF-8"):
        await execute_pipeline(request, evaluator=backend, optimizer=backend)

    assert [call[:3] for call in backend.calls] == [
        ("evaluate", "baseline", "train"),
        ("evaluate", "baseline", "validation"),
        ("optimize", {"system_prompt": "baseline\n"}, Path(request.output_dir) / "runs" / request.run_id / "optimizer"),
    ]


@pytest.mark.asyncio
async def test_eval_results_reject_non_finite_scores_and_negative_costs(tmp_path: Path):
    request = _request(tmp_path)
    backend = _safe_backend(candidate_count=1)
    baseline = backend.results[("baseline", "train")]
    backend.results[("baseline", "train")] = replace(
        baseline,
        cost=-0.01,
        cases=[replace(baseline.cases[0], score=math.nan)],
    )

    with pytest.raises(ValueError, match="finite|non-negative"):
        await execute_pipeline(request, evaluator=backend, optimizer=backend)


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["result_score", "result_cost", "case_metric"])
async def test_eval_results_reject_boolean_numeric_values(tmp_path: Path, field: str):
    request = _request(tmp_path)
    backend = _safe_backend(candidate_count=1)
    baseline = backend.results[("baseline", "train")]
    if field == "result_score":
        malformed = replace(baseline, score=True)
    elif field == "result_cost":
        malformed = replace(baseline, cost=True)
    else:
        malformed = replace(
            baseline,
            cases=[replace(baseline.cases[0], metrics={"quality": True})],
        )
    backend.results[("baseline", "train")] = malformed

    with pytest.raises(ValueError, match="finite number"):
        await execute_pipeline(request, evaluator=backend, optimizer=backend)


@pytest.mark.asyncio
async def test_complete_cost_summary_must_equal_components(tmp_path: Path):
    request = _request(tmp_path)
    backend = _safe_backend(
        candidate_count=1,
        optimization_cost=CostSummary(optimizer=0.1, evaluator=0.2, agent=0.3, total=9.0, complete=True),
    )

    with pytest.raises(ValueError, match="total.*components"):
        await execute_pipeline(request, evaluator=backend, optimizer=backend)


@pytest.mark.asyncio
async def test_optimizer_rounds_reject_invalid_numeric_values(tmp_path: Path):
    request = _request(tmp_path)
    invalid_round = OptimizationRound(
        round_id=1,
        candidate_id="candidate_a",
        prompts={"system_prompt": "safe prompt"},
        rationale="bad duration",
        metrics={"quality": math.inf},
        cost=CostSummary(),
        duration_seconds=-1.0,
    )
    backend = _safe_backend(candidate_count=1)
    backend.rounds = [invalid_round]

    with pytest.raises(ValueError, match="round.*finite|duration.*non-negative"):
        await execute_pipeline(request, evaluator=backend, optimizer=backend)


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_value", [Path("private.txt"), {"not-hashable"}, object()])
async def test_optimizer_raw_summary_must_be_json_compatible(
    tmp_path: Path,
    bad_value: object,
):
    request = _request(tmp_path, update_source=True)
    delegate = _safe_backend(candidate_count=1)
    backend = RecordingBackend(
        candidates=delegate.candidates,
        results=delegate.results,
        optimization_cost=delegate.optimization_cost,
        raw_summary={"bad": bad_value},
    )

    with pytest.raises(ValueError, match="JSON-compatible"):
        await execute_pipeline(request, evaluator=backend, optimizer=backend)

    assert (tmp_path / "prompt.txt").read_bytes() == b"baseline\n"


class SnapshotMutationBackend(RecordingBackend):
    def __init__(self, *, request: PipelineRequest, delegate: RecordingBackend) -> None:
        super().__init__(
            candidates=delegate.candidates,
            results=delegate.results,
            optimization_cost=delegate.optimization_cost,
        )
        self.request = request
        self.dataset_paths: list[Path] = []
        self.optimizer_paths: list[tuple[Path, Path, Path]] = []
        self._mutated = False

    async def evaluate(self, **kwargs: Any) -> EvalResult:
        dataset_path = Path(kwargs["dataset_path"])
        self.dataset_paths.append(dataset_path)
        expected_id = "train_case" if kwargs["split"] == "train" else "validation_case"
        payload = json.loads(dataset_path.read_text(encoding="utf-8"))
        assert [case["eval_id"] for case in payload["eval_cases"]] == [expected_id]
        result = await super().evaluate(**kwargs)
        if not self._mutated:
            Path(self.request.train_path).write_text('{"eval_cases": [{"eval_id": "changed"}]}', encoding="utf-8")
            Path(self.request.optimizer_config_path).write_text('{"seed": 999}', encoding="utf-8")
            if self.request.gate_config_path is not None:
                Path(self.request.gate_config_path).write_text('{"gate": {"max_total_cost": 0}}', encoding="utf-8")
            self._mutated = True
        return result

    async def optimize_candidates(self, **kwargs: Any) -> OptimizationResult:
        paths = (
            Path(kwargs["train_path"]),
            Path(kwargs["validation_path"]),
            Path(kwargs["config_path"]),
        )
        self.optimizer_paths.append(paths)
        assert json.loads(paths[0].read_text(encoding="utf-8"))["eval_cases"][0]["eval_id"] == "train_case"
        assert json.loads(paths[1].read_text(encoding="utf-8"))["eval_cases"][0]["eval_id"] == "validation_case"
        assert json.loads(paths[2].read_text(encoding="utf-8"))["seed"] == 91
        return await super().optimize_candidates(**kwargs)


class SnapshotTamperingBackend(RecordingBackend):
    async def evaluate(self, **kwargs: Any) -> EvalResult:
        result = await super().evaluate(**kwargs)
        if kwargs["prompt_id"] == "baseline" and kwargs["split"] == "train":
            Path(kwargs["dataset_path"]).write_text(
                '{"eval_cases": [{"eval_id": "tampered"}]}',
                encoding="utf-8",
            )
        return result


@pytest.mark.asyncio
async def test_backend_cannot_mutate_immutable_input_snapshot(tmp_path: Path):
    request = _request(tmp_path, update_source=True)
    delegate = _safe_backend(candidate_count=1)
    backend = SnapshotTamperingBackend(
        candidates=delegate.candidates,
        results=delegate.results,
        optimization_cost=delegate.optimization_cost,
    )

    with pytest.raises(ValueError, match="immutable input snapshot.*train"):
        await execute_pipeline(request, evaluator=backend, optimizer=backend)

    assert (tmp_path / "prompt.txt").read_bytes() == b"baseline\n"
    assert list(tmp_path.glob(".*.snapshot-*")) == []


@pytest.mark.asyncio
async def test_pipeline_uses_immutable_entry_snapshots_and_rejects_original_input_drift(tmp_path: Path):
    request = _request(tmp_path, update_source=True)
    gate_path = tmp_path / "gate.json"
    gate_path.write_text('{"gate": {"max_total_cost": null}}', encoding="utf-8")
    request = replace(request, gate_config_path=gate_path)
    backend = SnapshotMutationBackend(request=request, delegate=_safe_backend(candidate_count=1))

    report = await execute_pipeline(request, evaluator=backend, optimizer=backend)

    original_paths = {
        Path(request.train_path).resolve(),
        Path(request.validation_path).resolve(),
        Path(request.optimizer_config_path).resolve(),
    }
    used_paths = {path.resolve() for path in backend.dataset_paths}
    used_paths.update(path.resolve() for triple in backend.optimizer_paths for path in triple)
    assert used_paths.isdisjoint(original_paths)
    assert all(".snapshot-" in path.name for path in used_paths)
    assert all(
        path.parent
        in {
            Path(request.train_path).resolve().parent,
            Path(request.validation_path).resolve().parent,
            Path(request.optimizer_config_path).resolve().parent,
        }
        for path in used_paths
    )
    assert report.writeback.status == "rejected"
    assert report.audit["writeback_journal"]["state"] == "conflict"
    assert "input changed" in (report.writeback.error or "")
    assert (tmp_path / "prompt.txt").read_bytes() == b"baseline\n"
    assert list(tmp_path.glob(".*.snapshot-*")) == []


@pytest.mark.asyncio
async def test_public_report_redacts_secrets_and_absolute_personal_paths(tmp_path: Path):
    request = _request(tmp_path)
    config_payload = {
        "seed": 91,
        "api_key": "plain-api-key",
        "nested": {
            "access_token": "plain-token",
            "credentials": {"password": "plain-password"},
            "github_token": "plain-github-token",
            "private_key": "plain-private-key",
            "cache_path": str(tmp_path / "private-cache"),
        },
    }
    Path(request.optimizer_config_path).write_text(json.dumps(config_payload), encoding="utf-8")
    backend = _safe_backend(candidate_count=1)

    report = await execute_pipeline(request, evaluator=backend, optimizer=backend)
    payload = json.dumps(report, default=lambda value: value.__dict__, sort_keys=True)

    assert "plain-api-key" not in payload
    assert "plain-token" not in payload
    assert "plain-password" not in payload
    assert "plain-github-token" not in payload
    assert "plain-private-key" not in payload
    assert str(tmp_path.resolve()) not in payload
    assert report.run["reproducibility_shell"] == "powershell"
    assert "$EXTERNAL" in report.run["reproducibility_command"]
    assert (
        report.audit["config_hash"]
        == hashlib.sha256(
            json.dumps(config_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
    )
    persisted = (Path(request.output_dir) / "optimization_report.json").read_text(encoding="utf-8")
    persisted_markdown = (Path(request.output_dir) / "optimization_report.md").read_text(encoding="utf-8")
    config_snapshot = (Path(request.output_dir) / "runs" / request.run_id / "config.snapshot.json").read_text(
        encoding="utf-8"
    )
    assert "plain-api-key" not in persisted + config_snapshot
    assert "plain-token" not in persisted + config_snapshot
    assert "plain-password" not in persisted + config_snapshot
    assert "plain-github-token" not in persisted + config_snapshot
    assert "plain-private-key" not in persisted + config_snapshot
    assert "```powershell" in persisted_markdown
    assert str(tmp_path.resolve()) not in persisted_markdown
    assert hashlib.sha256(config_snapshot.encode("utf-8")).hexdigest() == report.audit[
        "redacted_config_snapshot_sha256"
    ]
    assert report.audit["config_file_sha256"] == hashlib.sha256(
        Path(request.optimizer_config_path).read_bytes()
    ).hexdigest()


def test_atomic_artifact_write_fsyncs_before_durable_replace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    path = tmp_path / "artifact.json"
    events: list[tuple[str, object]] = []
    real_fsync = os.fsync
    real_durable_replace = report_module._durable_replace

    def tracked_fsync(fd: int) -> None:
        events.append(("fsync", fd))
        real_fsync(fd)

    def tracked_durable_replace(source: Path, target: Path) -> None:
        events.append(("durable_replace", Path(target)))
        real_durable_replace(source, target)

    monkeypatch.setattr(report_module.os, "fsync", tracked_fsync)
    monkeypatch.setattr(report_module, "_durable_replace", tracked_durable_replace)

    report_module._atomic_write_text(path, "durable\n")

    replace_index = next(index for index, event in enumerate(events) if event[0] == "durable_replace")
    assert any(event[0] == "fsync" for event in events[:replace_index])
    assert path.read_text(encoding="utf-8") == "durable\n"
    assert not list(tmp_path.glob("*.tmp"))


@pytest.mark.asyncio
async def test_prepare_writes_completion_journal_after_all_other_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    request = _request(tmp_path)
    backend = _safe_backend(candidate_count=1)
    report = await execute_pipeline(request, evaluator=backend, optimizer=backend)
    writes: list[str] = []
    original = report_module._atomic_write_text

    def tracked(path: Path, content: str) -> None:
        writes.append(Path(path).name)
        original(Path(path), content)

    monkeypatch.setattr(report_module, "_atomic_write_text", tracked)

    report_module.prepare_run_artifacts(report, tmp_path / "prepare-order")

    assert writes[-1] == "writeback_journal.json"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("identity_error", "message"),
    [
        ("prompt_id", "prompt_id"),
        ("result_split", "EvalResult split"),
        ("case_split", "CaseResult.*split"),
    ],
)
async def test_backend_result_identity_must_match_requested_evaluation(
    tmp_path: Path,
    identity_error: str,
    message: str,
):
    request = _request(tmp_path)
    backend = _safe_backend(candidate_count=1)
    if identity_error == "prompt_id":
        baseline_train = backend.results[("baseline", "train")]
        backend.results[("baseline", "train")] = replace(
            baseline_train,
            prompt_id="wrong_prompt",
        )
    elif identity_error == "result_split":
        candidate_validation = backend.results[("candidate_a", "validation")]
        backend.results[("candidate_a", "validation")] = replace(
            candidate_validation,
            split="train",
        )
    else:
        candidate_validation = backend.results[("candidate_a", "validation")]
        backend.results[("candidate_a", "validation")] = replace(
            candidate_validation,
            cases=[replace(candidate_validation.cases[0], split="train")],
        )

    with pytest.raises(ValueError, match=message):
        await execute_pipeline(request, evaluator=backend, optimizer=backend)


@pytest.mark.asyncio
async def test_async_wrapper_runs_inside_active_event_loop_with_injected_backend(tmp_path: Path):
    from examples.optimization.eval_optimize_loop.run_pipeline import run_pipeline_async

    request = _request(tmp_path)
    backend = _safe_backend(candidate_count=1)

    report = await run_pipeline_async(
        train_path=request.train_path,
        val_path=request.validation_path,
        optimizer_config_path=request.optimizer_config_path,
        prompt_path=request.target_prompt_paths["system_prompt"],
        output_dir=request.output_dir,
        mode="fake",
        trace=True,
        run_id="async_wrapper_run",
        backend=backend,
    )

    assert report.selected_candidate == "candidate_a"
    assert report.run["run_id"] == "async_wrapper_run"


@pytest.mark.asyncio
async def test_sync_wrapper_rejects_active_event_loop_with_async_hint(tmp_path: Path):
    from examples.optimization.eval_optimize_loop.run_pipeline import run_pipeline

    request = _request(tmp_path)
    backend = _safe_backend(candidate_count=1)

    with pytest.raises(ValueError, match="await run_pipeline_async"):
        run_pipeline(
            train_path=request.train_path,
            val_path=request.validation_path,
            optimizer_config_path=request.optimizer_config_path,
            prompt_path=request.target_prompt_paths["system_prompt"],
            output_dir=request.output_dir,
            mode="fake",
            run_id="sync_wrapper_run",
            backend=backend,
        )


def _request(
    tmp_path: Path,
    *,
    update_source: bool = False,
    gate_config: dict[str, object] | None = None,
) -> PipelineRequest:
    train_path = tmp_path / "train.evalset.json"
    validation_path = tmp_path / "validation.evalset.json"
    config_path = tmp_path / "optimizer.json"
    prompt_path = tmp_path / "prompt.txt"
    train_path.write_text(
        '{"eval_cases": [{"eval_id": "train_case", "session_input": {"state": {}}}]}',
        encoding="utf-8",
    )
    validation_path.write_text(
        '{"eval_cases": [{"eval_id": "validation_case", "session_input": {"state": {}}}]}',
        encoding="utf-8",
    )
    config_path.write_text('{"seed": 91}', encoding="utf-8")
    prompt_path.write_bytes(b"baseline\n")
    return PipelineRequest(
        train_path=train_path,
        validation_path=validation_path,
        optimizer_config_path=config_path,
        output_dir=tmp_path / "out",
        target_prompt_paths={"system_prompt": prompt_path},
        gate_config=gate_config
        or {
            "min_val_score_improvement": 0.0,
            "allow_new_hard_fail": False,
            "protected_case_ids": [],
            "max_score_drop_per_case": 1.0,
            "max_total_cost": None,
        },
        trace=True,
        update_source=update_source,
        mode="fake",
        run_id="ordered_run",
    )


def _safe_backend(
    *,
    candidate_count: int = 2,
    candidate_validation_score: float = 1.0,
    baseline_train_cost: float = 0.01,
    baseline_validation_cost: float = 0.01,
    candidate_train_cost: float = 0.01,
    candidate_validation_cost: float = 0.01,
    optimization_cost: CostSummary | None = None,
) -> RecordingBackend:
    candidates = [
        CandidatePrompt("candidate_a", "safe prompt", "safe", "diff a"),
        CandidatePrompt("candidate_b", "other prompt", "other", "diff b"),
    ][:candidate_count]
    results = {
        ("baseline", "train"): _eval(
            "baseline",
            "train",
            ["train_case"],
            0.0,
            cost=baseline_train_cost,
            failure_category="train_only",
        ),
        ("baseline", "validation"): _eval(
            "baseline",
            "validation",
            ["validation_case"],
            0.0,
            cost=baseline_validation_cost,
            failure_category="validation_only",
        ),
    }
    for candidate in candidates:
        results[(candidate.candidate_id, "train")] = _eval(
            candidate.candidate_id,
            "train",
            ["train_case"],
            1.0,
            cost=candidate_train_cost,
        )
        results[(candidate.candidate_id, "validation")] = _eval(
            candidate.candidate_id,
            "validation",
            ["validation_case"],
            candidate_validation_score,
            cost=candidate_validation_cost,
        )
    return RecordingBackend(
        candidates=candidates,
        results=results,
        optimization_cost=optimization_cost,
    )


def _eval(
    prompt_id: str,
    split: str,
    case_ids: list[str],
    score: float,
    *,
    cost: float = 0.01,
    failure_category: str | None = None,
) -> EvalResult:
    cases = [
        CaseResult(
            case_id=case_id,
            split=split,
            score=score,
            passed=score >= 1.0,
            output="output",
            hard_failed=score <= 0.0,
            failure_category=failure_category if score < 1.0 else None,
        )
        for case_id in case_ids
    ]
    return EvalResult(
        prompt_id=prompt_id,
        split=split,
        score=score,
        passed=bool(cases) and all(case.passed for case in cases),
        cost=cost,
        cases=cases,
    )


def _eval_scores(
    prompt_id: str,
    split: str,
    scores: list[tuple[str, float]],
) -> EvalResult:
    cases = [
        CaseResult(
            case_id=case_id,
            split=split,
            score=score,
            passed=score >= 1.0,
            output="output",
            hard_failed=score <= 0.0,
        )
        for case_id, score in scores
    ]
    return EvalResult(
        prompt_id=prompt_id,
        split=split,
        score=round(sum(case.score for case in cases) / len(cases), 6),
        passed=all(case.passed for case in cases),
        cost=0.01,
        cases=cases,
    )
