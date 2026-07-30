# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tests for the optimizer-facing evaluator call wrapper."""

from __future__ import annotations

import pytest

from trpc_agent_sdk.evaluation._eval_metrics import EvalStatus
from trpc_agent_sdk.evaluation._eval_result import EvalCaseResult
from trpc_agent_sdk.evaluation._eval_result import EvalMetricResult
from trpc_agent_sdk.evaluation._eval_result import EvalSetAggregateResult
from trpc_agent_sdk.evaluation._eval_result import EvaluateResult
from trpc_agent_sdk.evaluation._optimize_evaluator_call import EvaluationOutcome
from trpc_agent_sdk.evaluation._optimize_evaluator_call import run_evaluator
from trpc_agent_sdk.evaluation._optimize_evaluator_call import summarize_outcome


def _metric(name: str, score: float, status: EvalStatus = EvalStatus.PASSED) -> EvalMetricResult:
    return EvalMetricResult(
        metric_name=name,
        threshold=0.5,
        score=score,
        eval_status=status,
    )


def _case(
    eval_id: str,
    final_status: EvalStatus,
    metric_scores: dict[str, tuple[float, EvalStatus]],
) -> EvalCaseResult:
    metrics = [_metric(n, s, st) for n, (s, st) in metric_scores.items()]
    return EvalCaseResult(
        eval_set_id="s1",
        eval_id=eval_id,
        final_eval_status=final_status,
        overall_eval_metric_results=metrics,
        eval_metric_result_per_invocation=[],
        session_id=f"sess-{eval_id}",
    )


def _result(cases: list[EvalCaseResult], num_runs: int = 1) -> EvaluateResult:
    by_id: dict[str, list[EvalCaseResult]] = {}
    for c in cases:
        by_id.setdefault(c.eval_id, []).append(c)
    return EvaluateResult(
        results_by_eval_set_id={
            "s1": EvalSetAggregateResult(
                eval_results_by_eval_id=by_id,
                num_runs=num_runs,
            ),
        }
    )


def test_summarize_outcome_all_passed_pass_rate_one():
    result = _result([
        _case("c1", EvalStatus.PASSED, {"m": (0.9, EvalStatus.PASSED)}),
        _case("c2", EvalStatus.PASSED, {"m": (0.95, EvalStatus.PASSED)}),
    ])
    outcome = summarize_outcome(result)
    assert outcome.pass_rate == 1.0
    assert outcome.failed_case_ids == []
    assert pytest.approx(outcome.tiebreaker) == (0.9 + 0.95) / 2


def test_summarize_outcome_partial_pass_rate():
    result = _result([
        _case("c1", EvalStatus.PASSED, {"m": (0.9, EvalStatus.PASSED)}),
        _case("c2", EvalStatus.FAILED, {"m": (0.3, EvalStatus.FAILED)}),
        _case("c3", EvalStatus.FAILED, {"m": (0.2, EvalStatus.FAILED)}),
        _case("c4", EvalStatus.PASSED, {"m": (0.8, EvalStatus.PASSED)}),
    ])
    outcome = summarize_outcome(result)
    assert outcome.pass_rate == 0.5
    assert set(outcome.failed_case_ids) == {"c2", "c3"}


def test_summarize_outcome_empty_result_zero_pass_rate():
    outcome = summarize_outcome(EvaluateResult())
    assert outcome.pass_rate == 0.0
    assert outcome.tiebreaker == 0.0
    assert outcome.failed_case_ids == []
    assert outcome.metric_breakdown == {}


def test_summarize_outcome_metric_breakdown_averages_scores():
    result = _result([
        _case("c1", EvalStatus.PASSED, {
            "metric_a": (0.8, EvalStatus.PASSED),
            "metric_b": (0.6, EvalStatus.PASSED),
        }),
        _case("c2", EvalStatus.PASSED, {
            "metric_a": (0.6, EvalStatus.PASSED),
            "metric_b": (0.4, EvalStatus.PASSED),
        }),
    ])
    outcome = summarize_outcome(result)
    assert pytest.approx(outcome.metric_breakdown["metric_a"]) == 0.7
    assert pytest.approx(outcome.metric_breakdown["metric_b"]) == 0.5


def test_summarize_outcome_tiebreaker_is_mean_of_all_scores():
    result = _result([
        _case("c1", EvalStatus.PASSED, {
            "metric_a": (1.0, EvalStatus.PASSED),
            "metric_b": (0.0, EvalStatus.PASSED),
        }),
    ])
    outcome = summarize_outcome(result)
    assert pytest.approx(outcome.tiebreaker) == 0.5


def test_summarize_outcome_skips_none_scores():
    case = EvalCaseResult(
        eval_set_id="s1",
        eval_id="c1",
        final_eval_status=EvalStatus.PASSED,
        overall_eval_metric_results=[
            EvalMetricResult(metric_name="m", threshold=0.5, score=None,
                             eval_status=EvalStatus.NOT_EVALUATED),
            EvalMetricResult(metric_name="m2", threshold=0.5, score=0.9,
                             eval_status=EvalStatus.PASSED),
        ],
        eval_metric_result_per_invocation=[],
        session_id="x",
    )
    outcome = summarize_outcome(_result([case]))
    assert outcome.metric_breakdown == {"m2": 0.9}
    assert pytest.approx(outcome.tiebreaker) == 0.9


def test_summarize_outcome_multi_run_repeats_failed_id():
    failing = _case("c1", EvalStatus.FAILED, {"m": (0.2, EvalStatus.FAILED)})
    passing = _case("c2", EvalStatus.PASSED, {"m": (0.9, EvalStatus.PASSED)})
    result = EvaluateResult(
        results_by_eval_set_id={
            "s1": EvalSetAggregateResult(
                eval_results_by_eval_id={
                    "c1": [failing, failing],
                    "c2": [passing, passing],
                },
                num_runs=2,
            ),
        }
    )
    outcome = summarize_outcome(result)
    assert outcome.pass_rate == 0.5
    assert outcome.failed_case_ids.count("c1") == 2


def test_evaluation_outcome_is_immutable():
    outcome = EvaluationOutcome(
        pass_rate=0.5,
        tiebreaker=0.6,
        metric_breakdown={"m": 0.5},
        failed_case_ids=["c1"],
        judge_model_calls=0,
        raw_result=EvaluateResult(),
    )
    try:
        outcome.pass_rate = 1.0  # type: ignore[misc]
    except Exception:
        return
    raise AssertionError("EvaluationOutcome should be frozen")


class _FakeExecuter:
    def __init__(self, result: EvaluateResult) -> None:
        self._result = result
        self.evaluate_called = 0

    async def evaluate(self) -> None:
        self.evaluate_called += 1

    def get_result(self) -> EvaluateResult:
        return self._result


@pytest.mark.asyncio
async def test_run_evaluator_passes_through_call_agent_callbacks_num_runs(monkeypatch):
    captured: dict = {}
    fake_result = _result([
        _case("c1", EvalStatus.PASSED, {"m": (0.9, EvalStatus.PASSED)}),
        _case("c2", EvalStatus.FAILED, {"m": (0.2, EvalStatus.FAILED)}),
    ])

    def fake_get_executer(eval_dataset_file_path_or_dir, **kwargs):
        captured["eval_dataset_path"] = eval_dataset_file_path_or_dir
        captured.update(kwargs)
        return _FakeExecuter(fake_result)

    from trpc_agent_sdk.evaluation import _optimize_evaluator_call as mod

    monkeypatch.setattr(mod.AgentEvaluator, "get_executer", fake_get_executer)

    async def call_agent(q: str) -> str:
        return "x"

    sentinel_callbacks = object()

    outcome = await run_evaluator(
        eval_dataset_path="/tmp/some_set.evalset.json",
        eval_metrics_path="/tmp/metrics.json",
        call_agent=call_agent,
        callbacks=sentinel_callbacks,  # type: ignore[arg-type]
        num_runs=3,
    )

    assert captured["eval_dataset_path"] == "/tmp/some_set.evalset.json"
    assert captured["eval_metrics_file_path_or_dir"] == "/tmp/metrics.json"
    assert captured["call_agent"] is call_agent
    assert captured["callbacks"] is sentinel_callbacks
    assert captured["num_runs"] == 3
    assert captured["print_detailed_results"] is False
    assert captured["eval_result_output_dir"] is None

    assert outcome.pass_rate == 0.5
    assert outcome.failed_case_ids == ["c2"]
    assert outcome.raw_result is fake_result


@pytest.mark.asyncio
async def test_run_evaluator_forwards_case_parallelism(monkeypatch):
    """spec §3.2: optimize.eval_case_parallelism must reach AgentEvaluator.get_executer."""
    captured: dict = {}
    fake_result = _result([_case("c1", EvalStatus.PASSED, {"m": (0.9, EvalStatus.PASSED)})])

    def fake_get_executer(eval_dataset_file_path_or_dir, **kwargs):
        captured.update(kwargs)
        return _FakeExecuter(fake_result)

    from trpc_agent_sdk.evaluation import _optimize_evaluator_call as mod

    monkeypatch.setattr(mod.AgentEvaluator, "get_executer", fake_get_executer)

    async def call_agent(q: str) -> str:
        return "x"

    await run_evaluator(
        eval_dataset_path="/tmp/x.json",
        eval_metrics_path=None,
        call_agent=call_agent,
        callbacks=None,
        num_runs=1,
        case_parallelism=8,
    )

    assert captured["case_parallelism"] == 8


@pytest.mark.asyncio
async def test_run_evaluator_forwards_print_summary_report_false(monkeypatch):
    """Optimizer must keep the evaluator silent so its summary table never
    collides with the reporter timeline."""
    captured: dict = {}
    fake_result = _result([_case("c1", EvalStatus.PASSED, {"m": (0.9, EvalStatus.PASSED)})])

    def fake_get_executer(eval_dataset_file_path_or_dir, **kwargs):
        captured.update(kwargs)
        return _FakeExecuter(fake_result)

    from trpc_agent_sdk.evaluation import _optimize_evaluator_call as mod

    monkeypatch.setattr(mod.AgentEvaluator, "get_executer", fake_get_executer)

    async def call_agent(q: str) -> str:
        return "x"

    await run_evaluator(
        eval_dataset_path="/tmp/x.json",
        eval_metrics_path=None,
        call_agent=call_agent,
        callbacks=None,
    )

    assert captured["print_detailed_results"] is False
    assert captured["print_summary_report"] is False


class _AssertingExecuter:
    """Mimics AgentEvaluator's pytest-style fail-fast on case failure."""

    def __init__(self, result: EvaluateResult, message: str) -> None:
        self._result = result
        self._message = message

    async def evaluate(self) -> None:
        from trpc_agent_sdk.evaluation._agent_evaluator import _EvaluationCasesFailed
        raise _EvaluationCasesFailed(self._message)

    def get_result(self) -> EvaluateResult:
        return self._result


@pytest.mark.asyncio
async def test_run_evaluator_swallows_evaluator_assertion_and_returns_outcome(monkeypatch):
    fake_result = _result([
        _case("c1", EvalStatus.PASSED, {"m": (0.9, EvalStatus.PASSED)}),
        _case("c2", EvalStatus.FAILED, {"m": (0.2, EvalStatus.FAILED)}),
    ])

    def fake_get_executer(eval_dataset_file_path_or_dir, **kwargs):
        return _AssertingExecuter(fake_result, "case c2 failed")

    from trpc_agent_sdk.evaluation import _optimize_evaluator_call as mod

    monkeypatch.setattr(mod.AgentEvaluator, "get_executer", fake_get_executer)

    async def call_agent(q: str) -> str:
        return "x"

    outcome = await run_evaluator(
        eval_dataset_path="/tmp/x.json",
        eval_metrics_path=None,
        call_agent=call_agent,
        callbacks=None,
    )

    assert outcome.pass_rate == 0.5
    assert outcome.failed_case_ids == ["c2"]
    assert outcome.raw_result is fake_result


@pytest.mark.asyncio
async def test_run_evaluator_returns_empty_outcome_when_assertion_loses_result(monkeypatch):
    class _LostResultExecuter:
        async def evaluate(self) -> None:
            from trpc_agent_sdk.evaluation._agent_evaluator import _EvaluationCasesFailed
            raise _EvaluationCasesFailed("boom before result populated")

        def get_result(self):
            return None

    from trpc_agent_sdk.evaluation import _optimize_evaluator_call as mod

    monkeypatch.setattr(
        mod.AgentEvaluator, "get_executer", lambda *a, **k: _LostResultExecuter()
    )

    async def call_agent(q: str) -> str:
        return "x"

    outcome = await run_evaluator(
        eval_dataset_path="/tmp/x.json",
        eval_metrics_path=None,
        call_agent=call_agent,
        callbacks=None,
    )

    assert outcome.pass_rate == 0.0
    assert outcome.failed_case_ids == []


@pytest.mark.asyncio
async def test_run_evaluator_does_not_swallow_unrelated_assertion_error(monkeypatch):
    """FAIL-3: only ``_EvaluationCasesFailed`` is the business signal.

    Third-party / SDK-internal ``AssertionError`` (numpy ``assert_allclose``,
    invariant self-checks, ...) must NOT be silently consumed — that would
    hide real bugs behind a 0.0 pass_rate and let the optimizer continue
    training against phantom data.
    """
    class _BuggyExecuter:
        async def evaluate(self) -> None:
            # Stand-in for an unrelated assertion failure inside the evaluator
            # (e.g. a numpy invariant check, a library bug).
            raise AssertionError("invariant violated: this is NOT a case-failure signal")

        def get_result(self):  # pragma: no cover - never reached
            return None

    from trpc_agent_sdk.evaluation import _optimize_evaluator_call as mod

    monkeypatch.setattr(
        mod.AgentEvaluator, "get_executer", lambda *a, **k: _BuggyExecuter()
    )

    async def call_agent(q: str) -> str:
        return "x"

    with pytest.raises(AssertionError, match="invariant violated"):
        await run_evaluator(
            eval_dataset_path="/tmp/x.json",
            eval_metrics_path=None,
            call_agent=call_agent,
            callbacks=None,
        )


@pytest.mark.asyncio
async def test_run_evaluator_propagates_real_upstream_error(monkeypatch):
    """FAIL-3: real upstream errors (FileNotFoundError, network, ...) must
    propagate, not be silently turned into an empty outcome.

    The pre-fix code had ``try / except AssertionError / finally:
    result = get_result()`` which masked any non-Assertion exception too if
    the executer's ``get_result()`` returned None — actually it re-raised,
    but the optimizer downstream had no way to distinguish "all cases
    silently failed" from "evalset file missing on disk". The post-fix code
    propagates these to ``AgentOptimizer.optimize()`` ``run_error`` path so
    the run terminates with status=FAILED and the cause is preserved in
    ``summary.txt`` rather than silently producing 0.0 pass_rate.
    """
    class _BrokenExecuter:
        async def evaluate(self) -> None:
            raise FileNotFoundError("dataset.evalset.json")

        def get_result(self):  # pragma: no cover - never reached
            return None

    from trpc_agent_sdk.evaluation import _optimize_evaluator_call as mod

    monkeypatch.setattr(
        mod.AgentEvaluator, "get_executer", lambda *a, **k: _BrokenExecuter()
    )

    async def call_agent(q: str) -> str:
        return "x"

    with pytest.raises(FileNotFoundError, match="dataset.evalset.json"):
        await run_evaluator(
            eval_dataset_path="/tmp/x.json",
            eval_metrics_path=None,
            call_agent=call_agent,
            callbacks=None,
        )


def test_evaluation_cases_failed_is_assertion_error_subclass():
    """FAIL-3: ``_EvaluationCasesFailed`` MUST remain an ``AssertionError``
    subclass so direct ``AgentEvaluator.evaluate()`` callers (e.g.
    ``examples/optimization/ci_integration/tests/test_agent_quality.py``)
    can keep using ``except AssertionError`` / pytest's native AssertionError
    rendering for JUnit XML output without any change."""
    from trpc_agent_sdk.evaluation._agent_evaluator import _EvaluationCasesFailed
    err = _EvaluationCasesFailed("failure summary json")
    assert isinstance(err, AssertionError)
    # Message identity matters for JUnit XML stability.
    assert str(err) == "failure summary json"


@pytest.mark.asyncio
async def test_eval_executer_raises_evaluation_cases_failed_on_case_failure(tmp_path, monkeypatch):
    """FAIL-3 end-to-end: ``_EvalExecuter._run`` MUST raise
    ``_EvaluationCasesFailed`` (NOT a bare ``assert False``) when any case
    fails. Replacing the bare assert with a real ``raise`` keeps the signal
    alive under ``python -O`` — which strips ``assert`` statements — and
    avoids piggy-backing business control flow on Python's invariant-check
    mechanism.

    We monkeypatch ``evaluate_eval_set`` so this test does not need a real
    LLM / runner: the test verifies the post-loop branch in ``_run`` that
    converts ``all_failures`` into ``_EvaluationCasesFailed``.
    """
    import json as _json

    from trpc_agent_sdk.evaluation._agent_evaluator import (
        AgentEvaluator as _Eval,
    )
    from trpc_agent_sdk.evaluation._agent_evaluator import (
        _EvaluationCasesFailed,
    )
    from trpc_agent_sdk.evaluation._eval_case import EvalCase
    from trpc_agent_sdk.evaluation._eval_case import Invocation
    from trpc_agent_sdk.evaluation._eval_config import EvalConfig
    from trpc_agent_sdk.evaluation._eval_set import EvalSet
    from trpc_agent_sdk.types import Content
    from trpc_agent_sdk.types import Part

    # Build the smallest possible evalset on disk so _run can load it.
    eval_set = EvalSet(
        eval_set_id="es_fail3",
        eval_cases=[
            EvalCase(
                eval_id="c1",
                conversation=[
                    Invocation(
                        user_content=Content(
                            role="user", parts=[Part.from_text(text="hi")]
                        ),
                        final_response=Content(
                            role="model", parts=[Part.from_text(text="ack")]
                        ),
                    )
                ],
            )
        ],
    )
    evalset_path = tmp_path / "tiny.evalset.json"
    evalset_path.write_text(eval_set.model_dump_json(), encoding="utf-8")
    config_path = tmp_path / "test_config.json"
    config_path.write_text(
        EvalConfig(criteria={"final_response_avg_score": 0.5}).model_dump_json(),
        encoding="utf-8",
    )

    async def fake_evaluate_eval_set(eval_set_arg, **kwargs):
        # Pretend case c1 failed with a structured summary.
        failed_summary = {
            "overallStatus": "failed",
            "evalCases": [{"evalCaseId": "c1", "overallStatus": "failed"}],
        }
        return failed_summary, [], [], {"c1": []}

    monkeypatch.setattr(_Eval, "evaluate_eval_set", staticmethod(fake_evaluate_eval_set))

    async def call_agent(query: str) -> str:
        return "ack"

    executer = _Eval.get_executer(
        str(evalset_path),
        call_agent=call_agent,
        print_summary_report=False,
        print_detailed_results=False,
    )

    with pytest.raises(_EvaluationCasesFailed) as excinfo:
        await executer.evaluate()

    # The error message is the JSON-encoded failure summary — pytest renders
    # this verbatim in JUnit XML, so existing CI dashboards keep working.
    parsed = _json.loads(str(excinfo.value))
    assert parsed[0]["evalSetId"] == "es_fail3"
    assert parsed[0]["summary"]["overallStatus"] == "failed"

    # Back-compat: ``isinstance(err, AssertionError)`` MUST stay True so
    # ``examples/optimization/ci_integration`` (``except AssertionError``)
    # works unchanged.
    assert isinstance(excinfo.value, AssertionError)

    # The result was populated BEFORE the raise (line ordering in _run);
    # callers can recover the EvaluateResult even on the failure path.
    assert executer.get_result() is not None


@pytest.mark.asyncio
async def test_eval_executer_signal_survives_python_O_mode(tmp_path, monkeypatch):
    """FAIL-3 python -O coverage: ``_run`` MUST NOT use ``assert`` for the
    business signal. We can't actually run pytest under ``-O`` here, but
    we can prove the signal does not depend on assertions by checking the
    source code contains ``raise _EvaluationCasesFailed`` and NOT
    ``assert False`` in the case-failure branch.

    A grep-style guard test is overkill for most things, but ``python -O``
    failures are notoriously hard to reproduce and were the exact root
    cause of FAIL-3 — pinning the implementation contract here prevents
    a careless future rewrite from reintroducing the bug.
    """
    import ast
    from pathlib import Path

    source = Path(
        "trpc_agent_sdk/evaluation/_agent_evaluator.py"
    ).read_text(encoding="utf-8")
    assert "raise _EvaluationCasesFailed(combined)" in source, (
        "_run must raise _EvaluationCasesFailed for the case-failure signal"
    )
    # Parse the AST and walk every Assert node inside _EvalExecuter._run;
    # there MUST be none — case failure must be raised, not asserted.
    tree = ast.parse(source)
    run_method = None
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.ClassDef)
            and node.name == "_EvalExecuter"
        ):
            for sub in node.body:
                if isinstance(sub, ast.AsyncFunctionDef) and sub.name == "_run":
                    run_method = sub
                    break
    assert run_method is not None, "could not locate _EvalExecuter._run"
    asserts_in_run = [
        n for n in ast.walk(run_method) if isinstance(n, ast.Assert)
    ]
    assert asserts_in_run == [], (
        f"_EvalExecuter._run MUST NOT contain any ``assert`` statements "
        f"(stripped by python -O); found {len(asserts_in_run)} "
        f"at lines {[a.lineno for a in asserts_in_run]}"
    )


@pytest.mark.asyncio
async def test_run_evaluator_default_num_runs_is_one(monkeypatch):
    captured: dict = {}
    fake_result = _result([_case("c1", EvalStatus.PASSED, {"m": (0.9, EvalStatus.PASSED)})])

    def fake_get_executer(eval_dataset_file_path_or_dir, **kwargs):
        captured.update(kwargs)
        return _FakeExecuter(fake_result)

    from trpc_agent_sdk.evaluation import _optimize_evaluator_call as mod

    monkeypatch.setattr(mod.AgentEvaluator, "get_executer", fake_get_executer)

    async def call_agent(q: str) -> str:
        return "x"

    await run_evaluator(
        eval_dataset_path="/tmp/x.json",
        eval_metrics_path=None,
        call_agent=call_agent,
        callbacks=None,
    )

    assert captured["num_runs"] == 1
    assert captured["callbacks"] is None
    assert captured["eval_metrics_file_path_or_dir"] is None
