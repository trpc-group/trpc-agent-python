from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from examples.optimization.eval_optimize_loop.eval_loop import pipeline as pipeline_module
from examples.optimization.eval_optimize_loop.eval_loop.pipeline import PipelineRequest
from examples.optimization.eval_optimize_loop.eval_loop.pipeline import execute_pipeline
from examples.optimization.eval_optimize_loop.eval_loop.schemas import CandidatePrompt
from examples.optimization.eval_optimize_loop.eval_loop.schemas import CaseResult
from examples.optimization.eval_optimize_loop.eval_loop.schemas import CostSummary
from examples.optimization.eval_optimize_loop.eval_loop.schemas import EvalResult
from examples.optimization.eval_optimize_loop.eval_loop.schemas import OptimizationResult
from examples.optimization.eval_optimize_loop.eval_loop.schemas import WritebackResult


class RecordingBackend:
    def __init__(
        self,
        *,
        candidates: list[CandidatePrompt],
        results: dict[tuple[str, str], EvalResult],
        optimization_cost: CostSummary | None = None,
    ) -> None:
        self.candidates = candidates
        self.results = results
        self.optimization_cost = optimization_cost or CostSummary()
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
            rounds=[],
            cost=self.optimization_cost,
            raw_summary={"status": "SUCCEEDED"},
        )


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
    assert report.audit["input_hashes"]["target_prompts"]["system_prompt"] == hashlib.sha256(
        b"baseline\n"
    ).hexdigest()
    artifact_dirs = [call[4] for call in backend.calls if call[0] == "evaluate"]
    assert len(artifact_dirs) == len(set(artifact_dirs)) == 6
    assert report.audit["sdk_result_availability"]["full_train_eval_result"] is True
    assert report.audit["sdk_result_availability"]["full_per_case_validation_delta"] is True


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

    monkeypatch.setattr(pipeline_module, "prepare_run_artifacts", prepare)
    monkeypatch.setattr(pipeline_module, "commit_prompt_bundle", commit)
    monkeypatch.setattr(pipeline_module, "finalize_run_artifacts", finalize)

    report = await execute_pipeline(request, evaluator=backend, optimizer=backend)

    assert events == ["prepare", "commit", "finalize"]
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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("baseline_case_ids", "candidate_case_ids", "reason_fragment"),
    [
        (["a", "a"], ["a"], "duplicate case IDs"),
        (["a"], ["a", "a"], "duplicate case IDs"),
        (["a", "b"], ["a"], "case ID set mismatch"),
        (["a"], ["a", "extra"], "case ID set mismatch"),
        ([], [], "empty case results"),
    ],
)
async def test_incomparable_case_results_are_rejected_without_delta_error(
    tmp_path: Path,
    baseline_case_ids: list[str],
    candidate_case_ids: list[str],
    reason_fragment: str,
):
    request = _request(tmp_path)
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

    monkeypatch.setattr(
        pipeline_module,
        "commit_prompt_bundle",
        lambda snapshot, prompts: WritebackResult(
            status="applied",
            before_hashes=snapshot.hashes(),
            after_hashes=snapshot.hashes(),
        ),
    )

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
    assert journal["state"] == "prepared"


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
    train_path.write_text('{"eval_cases": []}', encoding="utf-8")
    validation_path.write_text('{"eval_cases": []}', encoding="utf-8")
    config_path.write_text('{"seed": 91}', encoding="utf-8")
    prompt_path.write_bytes(b"baseline\n")
    return PipelineRequest(
        train_path=train_path,
        validation_path=validation_path,
        optimizer_config_path=config_path,
        output_dir=tmp_path / "out",
        target_prompt_paths={"system_prompt": prompt_path},
        gate_config=gate_config or {
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
