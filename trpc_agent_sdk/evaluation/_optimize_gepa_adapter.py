# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""GEPA protocol adapter and reflective-dataset builder.

Implements ``gepa.core.adapter.GEPAAdapter`` so gepa's main loop can
drive evaluation through the framework's ``AgentEvaluator``. The
adapter stays decoupled from any specific gepa algorithm class so
gepa-family algorithms can reuse it without duplicating evaluator I/O.

:meth:`_AgentGEPAAdapter.make_reflective_dataset` renders each failed
case into a turn-sliced markdown record
(``{case_id, score, "Case Body", "Other Active Components"?}``) tuned
for the reflection LM in multi-component / multi-turn / multi-run /
tool-using scenarios.

``gepa`` is an optional dependency: ``EvaluationBatch`` is imported
lazily inside :meth:`_AgentGEPAAdapter.evaluate`, so importing this
module without ``gepa`` installed succeeds but ``evaluate`` then fails
fast.
"""

from __future__ import annotations

import asyncio
import tempfile
import uuid
import os
from pathlib import Path
from typing import Any
from typing import Mapping
from typing import Optional
from typing import Sequence

from ._eval_callbacks import Callbacks
from ._eval_case import EvalCase
from ._eval_case import Invocation
from ._eval_case import get_all_tool_calls
from ._eval_case import get_all_tool_responses
from ._eval_config import EvalConfig
from ._eval_metrics import EvalStatus
from ._eval_metrics import PrebuiltMetrics
from ._eval_result import EvalCaseResult
from ._eval_result import EvalMetricResult
from ._eval_result import EvaluateResult
from ._eval_set import EvalSet
from ._optimize_evaluator_call import run_evaluator
from ._remote_eval_service import CallAgent
from ._target_prompt import TargetPrompt


def _extract_case_output(case_result: EvalCaseResult) -> str:
    """Return the agent's final response text from the first per-invocation entry.

    Used to populate ``EvaluationBatch.outputs`` — GEPA reads that field
    directly to decide whether a candidate's behaviour improved on a case
    even before consulting the trajectory or score.
    """
    per_inv = case_result.eval_metric_result_per_invocation or []
    if not per_inv:
        return ""
    actual = per_inv[0].actual_invocation
    if not actual or not actual.final_response or not actual.final_response.parts:
        return ""
    return "\n".join((p.text or "") for p in actual.final_response.parts).strip()


def _invocation_text(invocation: Optional[Invocation], *, user: bool) -> str:
    """Concatenate a single invocation's user_content or final_response text."""
    if invocation is None:
        return ""
    content = invocation.user_content if user else invocation.final_response
    if content is None or not content.parts:
        return ""
    return "\n".join((p.text or "") for p in content.parts).strip()


def _render_metric_lines(metrics: Sequence[EvalMetricResult]) -> list[str]:
    """Render one block of per-metric verdict lines for a turn or aggregate.

    Drives both per-invocation blocks (``### Turn N``) inside
    :func:`_build_turn_block` and the case-level aggregate block
    (``### Overall``) inside :func:`_build_overall_block`. Each metric
    occupies one ``[PASS|FAIL] name: score=..., threshold=...`` line;
    optional ``reason`` and rubric sub-score lines are nested below it.
    """
    lines: list[str] = []
    for metric in metrics:
        status = _format_status(metric.eval_status)
        score_str = f"{metric.score:.4f}" if metric.score is not None else "n/a"
        lines.append(f"[{status}] {metric.metric_name}: "
                     f"score={score_str}, threshold={metric.threshold:.4f}")

        # ``details.reason`` is only populated by LLM-judged evaluators.
        # For deterministic matchers, synthesize a one-line explanation
        # from the criterion config so the reflection LM sees WHY the
        # check failed.
        explicit_reason = (metric.details.reason if (metric.details and metric.details.reason) else None)
        if explicit_reason:
            lines.append(f"  reason: {explicit_reason}")
        else:
            synthesized = _synthesize_failure_reason(metric)
            if synthesized:
                lines.append(f"  reason: {synthesized}")

        # Expand rubric sub-scores so the reflection LM can target the
        # precise failing aspect instead of guessing.
        rubric_scores = (getattr(metric.details, "rubric_scores", None) if metric.details else None)
        if rubric_scores:
            for rs in rubric_scores:
                rid = (getattr(rs, "id", None) if not isinstance(rs, dict) else rs.get("id")) or "?"
                rscore = (getattr(rs, "score", None) if not isinstance(rs, dict) else rs.get("score"))
                rreason = (getattr(rs, "reason", "") if not isinstance(rs, dict) else rs.get("reason", ""))
                if rscore is None:
                    continue
                rs_status = "PASS" if float(rscore) >= 1.0 else "FAIL"
                line = (f"  · rubric[{rid}]: {rs_status} "
                        f"score={float(rscore):.2f}")
                if rreason:
                    line += f"  reason: {rreason}"
                lines.append(line)
    return lines


def _synthesize_failure_reason(metric: EvalMetricResult) -> Optional[str]:
    """Synthesize a short failure explanation for deterministic metrics.

    Deterministic evaluators (e.g. ``_final_response_evaluator``) only
    emit ``score`` + ``eval_status``; without this, the reflection LM
    has to diff the agent's output against the reference itself to
    guess why the match failed. Translate the criterion config into one
    of:

      - "agent output not byte-equal to expected (case-sensitive)"        (exact)
      - "expected substring not contained in agent output (case-insensitive)"  (contains)
      - "agent output did not match expected regex"                       (regex)
      - "JSON structural comparison failed"                               (json)
      - "text-... AND JSON-..." when both checks are configured

    Returns ``None`` for non-deterministic metrics, currently-passing
    metrics, and missing/malformed criterion configs.
    """
    if metric.metric_name != PrebuiltMetrics.FINAL_RESPONSE_AVG_SCORE.value:
        return None
    if metric.score is None or float(metric.score) >= 1.0:
        return None
    criterion = metric.criterion or {}
    if not isinstance(criterion, dict):
        return None
    fr = criterion.get("final_response") or criterion.get("finalResponse")
    if not isinstance(fr, dict):
        return None

    notes: list[str] = []
    text = fr.get("text") or fr.get("text_strategy") or fr.get("textStrategy")
    if isinstance(text, dict) and not text.get("ignore"):
        match = str(text.get("match") or text.get("match_strategy") or "exact").strip().lower()
        case_ins = bool(text.get("case_insensitive") or text.get("caseInsensitive"))
        case_tag = "case-insensitive" if case_ins else "case-sensitive"
        if match == "exact":
            notes.append(f"agent output not byte-equal to expected ({case_tag})")
        elif match == "contains":
            notes.append(f"expected substring not contained in agent output ({case_tag})")
        elif match == "regex":
            notes.append(f"agent output did not match expected regex ({case_tag})")
        else:
            notes.append(f"text match (mode={match}) failed ({case_tag})")

    json_cfg = fr.get("json") or fr.get("json_strategy") or fr.get("jsonStrategy")
    if isinstance(json_cfg, dict) and not json_cfg.get("ignore"):
        notes.append("JSON structural comparison failed")

    if not notes:
        return None
    return " AND ".join(notes)


def _format_status(status: Any) -> str:
    """Render an EvalStatus as its name (PASSED/FAILED/...) — readable
    to the reflection LM than the numeric ``.value``.
    """
    name = getattr(status, "name", None)
    if isinstance(name, str):
        return name
    return str(status)


def _per_metric_objective_scores(case_runs: Sequence[EvalCaseResult], ) -> dict[str, float]:
    """Build the per-objective score map for one case.

    Each metric name maps to the mean of its ``score`` across runs.
    GEPA uses this to maintain a per-objective Pareto frontier
    independent of the aggregated case score — so a candidate that
    dominates on one metric (e.g. rubric quality) survives even when
    overall pass rates tie. Metrics with no signal across all runs are
    skipped (they would taint the mean).
    """
    sums: dict[str, float] = {}
    counts: dict[str, int] = {}
    for run in case_runs:
        for metric in run.overall_eval_metric_results or []:
            if metric.score is None:
                continue
            sums[metric.metric_name] = sums.get(metric.metric_name, 0.0) + float(metric.score)
            counts[metric.metric_name] = counts.get(metric.metric_name, 0) + 1
    return {name: sums[name] / counts[name] for name in sums}


def _continuous_case_score(case_runs: Sequence[EvalCaseResult]) -> float:
    """Compute case_score as the mean of per-metric continuous scores.

    Per run: average all ``EvalMetricResult.score`` values (each in
    ``[0, 1]``). Across runs (``num_runs > 1``): average the per-run
    scores. Continuous scoring lets gepa distinguish candidates that
    share PASS/FAIL labels but differ in metric quality (e.g. one keeps
    a rubric at 1.0 while another regresses to 0.33 — both still FAIL
    overall but only one is strictly better).
    """
    run_scores: list[float] = []
    for run in case_runs:
        metrics = run.overall_eval_metric_results or []
        metric_scores = [float(m.score) for m in metrics if m.score is not None]
        if metric_scores:
            run_scores.append(sum(metric_scores) / len(metric_scores))
        else:
            # Fallback to the binary PASS/FAIL signal when no per-metric scores
            # are emitted (e.g. error path or evaluator that omits details).
            run_scores.append(1.0 if run.final_eval_status == EvalStatus.PASSED else 0.0)
    if not run_scores:
        return 0.0
    return sum(run_scores) / len(run_scores)


def _format_tool_args(args: Any) -> str:
    """Render a tool-call ``args`` dict inline as ``k=v, k=v``.

    Inline form keeps each tool call on one line; gepa's prompt_renderer
    would otherwise expand each arg into its own ``###### key`` heading
    and hit the H6 cap.
    """
    if not isinstance(args, dict):
        return repr(args)
    parts: list[str] = []
    for key, value in args.items():
        if isinstance(value, str):
            parts.append(f"{key}={value!r}")
        elif isinstance(value, (int, float, bool)) or value is None:
            parts.append(f"{key}={value}")
        else:
            parts.append(f"{key}={value!r}")
    return ", ".join(parts)


def _format_tool_response(response: Any) -> str:
    """Render a tool response inline; collapse single-key dicts to bare value."""
    if isinstance(response, dict):
        if len(response) == 1:
            value = next(iter(response.values()))
            if isinstance(value, str):
                return repr(value)
            return str(value)
        return "{" + _format_tool_args(response) + "}"
    if isinstance(response, str):
        return repr(response)
    return str(response)


def _resolve_turn_metrics(run: EvalCaseResult, turn_idx: int, total_turns: int) -> list[EvalMetricResult]:
    """Pick the verdict slice for one (run, turn).

    Multi-turn cases use ``eval_metric_result_per_invocation[i].
    eval_metric_results``. Single-turn cases sometimes leave that empty
    and only populate ``overall_eval_metric_results`` — fall back so a
    Turn 1 block still carries a verdict.
    """
    per_inv = run.eval_metric_result_per_invocation or []
    if 0 <= turn_idx - 1 < len(per_inv):
        pinv = per_inv[turn_idx - 1]
        if pinv.eval_metric_results:
            return list(pinv.eval_metric_results)
    if total_turns == 1:
        return list(run.overall_eval_metric_results or [])
    return []


def _build_turn_block(
    case: EvalCase,
    case_runs: Sequence[EvalCaseResult],
    turn_idx: int,
    total_turns: int,
    is_multi_run: bool,
) -> str:
    """Render one ``### Turn N`` block grouping user/expected/agent/tool/verdict.

    Conversational truth (User/Expected) is shared across runs and printed
    first; for each run the actual agent_response, function-call trace, and
    per-turn verdict follow. Multi-run cases nest each run under
    ``#### Run N`` so the LM can attribute output variance to a specific
    roll-out.
    """
    lines: list[str] = [f"### Turn {turn_idx}"]

    convo = case.conversation or case.actual_conversation or []
    if 0 <= turn_idx - 1 < len(convo):
        inv = convo[turn_idx - 1]
        user_text = _invocation_text(inv, user=True)
        if user_text:
            lines.append(f"**User**: {user_text}")
        expected_text = _invocation_text(inv, user=False)
        if expected_text:
            lines.append(f"**Expected**: {expected_text}")

    for ordinal, run in enumerate(case_runs, start=1):
        run_id = getattr(run, "run_id", None) or ordinal
        per_inv = run.eval_metric_result_per_invocation or []
        actual_inv: Optional[Invocation] = None
        if 0 <= turn_idx - 1 < len(per_inv):
            actual_inv = per_inv[turn_idx - 1].actual_invocation

        if is_multi_run:
            lines.append("")
            lines.append(f"#### Run {run_id}")

        if actual_inv is not None:
            response_text = _invocation_text(actual_inv, user=False)
            if response_text:
                lines.append(f"**Agent Response**: {response_text}")

            tool_calls = get_all_tool_calls(actual_inv.intermediate_data)
            tool_responses = get_all_tool_responses(actual_inv.intermediate_data)
            if tool_calls or tool_responses:
                lines.append("**Tool Trace**:")
                resp_by_id: dict[str, Any] = {tr.id: tr for tr in tool_responses if tr.id}
                consumed_ids: set[str] = set()
                for tc in tool_calls:
                    args_inline = _format_tool_args(tc.args) if tc.args else ""
                    suffix = ""
                    if tc.id and tc.id in resp_by_id:
                        tr = resp_by_id[tc.id]
                        consumed_ids.add(tc.id)
                        suffix = f" → {_format_tool_response(tr.response)}"
                    id_tag = f" [id={tc.id}]" if tc.id else ""
                    lines.append(f"- {tc.name or '<unnamed>'}({args_inline}){suffix}{id_tag}")
                # Surface tool_responses arriving without a paired call so
                # the reflection LM doesn't miss out-of-band observations.
                for tr in tool_responses:
                    if tr.id and tr.id in consumed_ids:
                        continue
                    id_tag = f" [id={tr.id}]" if tr.id else ""
                    lines.append(f"- (orphan response) {tr.name or '<unnamed>'} → "
                                 f"{_format_tool_response(tr.response)}{id_tag}")

        verdict_metrics = _resolve_turn_metrics(run, turn_idx, total_turns)
        if verdict_metrics:
            run_tag = f", Run {run_id}" if is_multi_run else ""
            lines.append(f"**Verdict** (Turn {turn_idx}{run_tag}):")
            for verdict_line in _render_metric_lines(verdict_metrics):
                lines.append(f"  {verdict_line}")

    return "\n".join(lines)


def _build_overall_block(case_runs: Sequence[EvalCaseResult], is_multi_run: bool) -> str:
    """Render the case-level aggregate verdict block.

    Single-run: ``### Overall (case-level aggregate)`` from the run's
    ``overall_eval_metric_results``. Multi-run: ``### Overall (per-run
    aggregate)`` with one sub-block per run, so the LM can spot which
    runs failed without averaging through to a single mean.
    """
    if is_multi_run:
        lines: list[str] = ["### Overall (per-run aggregate)"]
        for ordinal, run in enumerate(case_runs, start=1):
            run_id = getattr(run, "run_id", None) or ordinal
            lines.append(f"**Run {run_id}**:")
            for verdict_line in _render_metric_lines(run.overall_eval_metric_results or []):
                lines.append(f"  {verdict_line}")
        return "\n".join(lines)

    lines = ["### Overall (case-level aggregate)"]
    if case_runs:
        lines.extend(_render_metric_lines(case_runs[0].overall_eval_metric_results or []))
    return "\n".join(lines)


def _build_case_body(case: EvalCase, case_runs: Sequence[EvalCaseResult]) -> str:
    """Build the per-turn-sliced markdown body of a failed case.

    Each turn is one ``### Turn N`` block bundling user / expected /
    agent_response / Tool Trace / Verdict so each failing metric is
    visually anchored to the turn that produced it. Multi-run cases nest
    each run under ``#### Run N``. Multi-turn or multi-run cases close
    with an ``### Overall`` aggregate.

    Returns an empty string when no usable turn data is available, so
    the caller can decide whether to drop the record.
    """
    if not case_runs:
        return ""

    n_runs = len(case_runs)
    is_multi_run = n_runs > 1

    convo = case.conversation or case.actual_conversation or []
    if convo:
        n_turns = len(convo)
    else:
        n_turns = max(
            (len(run.eval_metric_result_per_invocation or []) for run in case_runs),
            default=0,
        )

    if n_turns == 0:
        return ""

    blocks: list[str] = []
    for turn_idx in range(1, n_turns + 1):
        blocks.append(_build_turn_block(case, case_runs, turn_idx, n_turns, is_multi_run))

    # Single-turn single-run cases skip the Overall block — Turn 1
    # already carries the only verdict that exists.
    if n_turns > 1 or is_multi_run:
        blocks.append(_build_overall_block(case_runs, is_multi_run))

    return "\n\n".join(blocks)


def _build_other_active_components(candidate: dict[str, str], components_to_update: Sequence[str]) -> str:
    """Render the prompt content of every candidate component NOT being
    refined this round.

    GEPA fills ``<curr_param>`` with only the prompt being rewritten,
    but the evaluator's verdict was produced by the agent running with
    ALL prompts. Surfacing the others as static context stops the LM
    from regressing requirements already enforced elsewhere or
    duplicating instructions.

    Returns an empty string when there is only one component or when
    the others contain no text.
    """
    targets = set(components_to_update)
    others = {name: text for name, text in candidate.items() if name not in targets and text}
    if not others:
        return ""
    lines: list[str] = []
    for name in sorted(others):
        lines.append(f"### {name} (current)")
        lines.append(others[name].rstrip())
        lines.append("")
    return "\n".join(lines).rstrip()


def _build_trajectory_entry(
        case: EvalCase,
        score: float,
        *,
        case_runs: Sequence[EvalCaseResult] = (),
        error_message: Optional[str] = None,
) -> dict[str, Any]:
    """Bundle one case's evaluation artifacts for reflective dataset construction.

    ``score`` lets ``make_reflective_dataset`` filter to failed cases
    without re-reading the runs. ``_case`` + ``_case_runs`` carry
    everything the record builder needs to render the turn-sliced body.
    On evaluator-error paths (no runs produced), ``error_message``
    surfaces a diagnostic in place of a Case Body.
    """
    return {
        "score": score,
        "_case": case,
        "_case_runs": list(case_runs),
        "error_message": error_message,
    }


def _make_return_type_checked_call_agent(call_agent: Any) -> Any:
    """Wrap ``call_agent`` with a one-shot return-type check.

    Plain ``async def f(query): return 42`` passes
    :func:`inspect.iscoroutinefunction`, so the broken return type is only
    discovered when a metric tries to call ``.lower()`` / ``.strip()`` on the
    int and produces an opaque ``AttributeError`` deep inside the metric path.

    The wrapper validates ``isinstance(result, str)`` on the first call only,
    raising a clear ``TypeError`` that names the actual returned type. After
    the first successful call subsequent invocations bypass the check, so the
    overhead is a single boolean check on the first case and zero thereafter.
    """
    checked = {"done": False}

    async def _checked(query: str) -> str:
        result = await call_agent(query)
        if not checked["done"]:
            if not isinstance(result, str):
                raise TypeError(f"call_agent must return str; got "
                                f"{type(result).__name__} (value={result!r}). "
                                f"This is checked once on the first invocation.")
            checked["done"] = True
        return result

    return _checked


class _AgentGEPAAdapter:
    """GEPA protocol adapter bridging gepa.optimize() to the framework evaluator.

    Per ``evaluate`` call:
      1. Apply the proposed ``candidate`` to all registered ``TargetPrompt`` fields.
      2. Serialize ``batch`` and ``eval_config`` to a temp directory.
      3. Run ``run_evaluator`` (asyncio.run) and collect per-case pass
         status + final response.
      4. Build an ``EvaluationBatch`` carrying scores, outputs, and
         (optionally) trajectories used by reflective dataset construction.

    ``make_reflective_dataset`` then renders failed trajectories as
    ``{component: [{case_id, score, "Case Body", "Other Active Components"?},
    ...]}`` for gepa's reflection prompt template.
    """

    # gepa's reflective proposer reads ``adapter.propose_new_texts``
    # directly; ``None`` signals "use gepa's default reflection LM path".
    propose_new_texts = None

    def __init__(
        self,
        *,
        target_prompt: TargetPrompt,
        eval_config: EvalConfig,
        call_agent: CallAgent,
        callbacks: Optional[Callbacks] = None,
        num_runs: int = 1,
        case_parallelism: Optional[int] = None,
        top_k_per_case: int = 2,
        output_dir: Optional[str] = None,
    ) -> None:
        self.target_prompt = target_prompt
        self.eval_config = eval_config
        # Wrap call_agent so the first call validates the return type and
        # surfaces a clear TypeError on misuse (e.g. ``async def f(): return 42``
        # passes static signature checks but only blows up inside metrics).
        # The check fires once; later calls bypass the wrapper.
        self.call_agent = _make_return_type_checked_call_agent(call_agent)
        self.callbacks = callbacks
        self.num_runs = num_runs
        self.case_parallelism = case_parallelism
        self.output_dir = output_dir
        self._top_k = max(0, int(top_k_per_case))
        self._best_history: dict[str, list[dict[str, Any]]] = {}
        from ._optimize_evaluator_call import EvaluationOutcome  # local to avoid cycle
        self.last_outcome: Optional[EvaluationOutcome] = None
        # Long-lived event loop reused across every evaluate() call so
        # async resources held inside call_agent (httpx.AsyncClient,
        # asyncpg pools, grpc.aio channels, ...) stay bound to a single
        # loop. Created lazily on first evaluate() because adapter is
        # constructed from an async context; allocating the loop here
        # would not bind to the worker thread that gepa.optimize runs in.
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def _get_or_create_loop(self) -> asyncio.AbstractEventLoop:
        """Return the adapter-owned loop, creating it on first call.

        Must be invoked from the worker thread that drives gepa.optimize
        (no running loop in that thread, so a fresh loop is safe).
        """
        if self._loop is None or self._loop.is_closed():
            self._loop = asyncio.new_event_loop()
        return self._loop

    def close(self) -> None:
        """Close the adapter-owned loop. Idempotent; safe before first evaluate()."""
        loop = getattr(self, "_loop", None)
        self._loop = None
        if loop is None or loop.is_closed():
            return
        try:
            loop.close()
        except Exception:  # pragma: no cover - defensive guard
            pass

    def _record_history(
        self,
        *,
        case_id: str,
        score: float,
        best_response: str,
    ) -> None:
        """Append one historical entry per case, keep at most top_k by score."""
        if self._top_k <= 0:
            return
        bucket = self._best_history.setdefault(case_id, [])
        bucket.append({"score": float(score), "best_response": best_response})
        bucket.sort(key=lambda entry: entry["score"], reverse=True)
        del bucket[self._top_k:]

    def evaluate(
        self,
        batch: list[EvalCase],
        candidate: dict[str, str],
        capture_traces: bool = False,
    ) -> Any:
        """Apply ``candidate`` and run the evaluator over ``batch``.

        Both the prompt write and the evaluator run execute on the
        adapter-owned event loop so async resources held by call_agent
        stay bound to a single loop across every gepa iteration.
        """
        from gepa.core.adapter import EvaluationBatch

        loop = self._get_or_create_loop()
        loop.run_until_complete(self.target_prompt.write_all(candidate))

        tmp_parent = Path(self.output_dir) / "tmp_batches" if getattr(self, "output_dir", None) else Path.cwd() / "tmp" / "optimizer_batches"
        tmp_parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=tmp_parent) as tmp:
            tmp_path = Path(tmp)
            evalset_path = tmp_path / "batch.evalset.json"
            metrics_path = tmp_path / "batch.metrics.json"

            # Unique id per call so the in-memory eval-set manager doesn't
            # reject repeated batches. gepa's batch sampler pads minibatches
            # with least-frequent ids when trainset_size doesn't divide
            # minibatch_size, so the same eval_case can appear twice — rename
            # duplicate eval_ids in place so the manager accepts the EvalSet
            # and every minibatch position still gets scored.
            seen: dict[str, int] = {}
            unique_cases: list[EvalCase] = []
            for case in batch:
                seen[case.eval_id] = seen.get(case.eval_id, 0) + 1
                if seen[case.eval_id] == 1:
                    unique_cases.append(case)
                else:
                    cloned = case.model_copy()
                    cloned.eval_id = f"{case.eval_id}__rep{seen[case.eval_id]}"
                    unique_cases.append(cloned)
            evalset = EvalSet(
                eval_set_id=f"optimize_gepa_batch_{uuid.uuid4().hex[:8]}",
                eval_cases=unique_cases,
            )
            evalset_path.write_text(evalset.model_dump_json(indent=2), encoding="utf-8")
            metrics_path.write_text(self.eval_config.model_dump_json(indent=2), encoding="utf-8")

            outcome = loop.run_until_complete(
                run_evaluator(
                    eval_dataset_path=os.path.relpath(evalset_path, Path.cwd()).replace("\\", "/"),
                    eval_metrics_path=os.path.relpath(metrics_path, Path.cwd()).replace("\\", "/"),
                    call_agent=self.call_agent,
                    callbacks=self.callbacks,
                    num_runs=self.num_runs,
                    case_parallelism=self.case_parallelism,
                ))
            self.last_outcome = outcome

        return self._build_evaluation_batch(
            batch=unique_cases,
            result=outcome.raw_result,
            capture_traces=capture_traces,
            evaluation_batch_cls=EvaluationBatch,
        )

    def make_reflective_dataset(
        self,
        candidate: dict[str, str],
        eval_batch: Any,
        components_to_update: list[str],
    ) -> Mapping[str, Sequence[Mapping[str, Any]]]:
        """Render failed-case trajectories into GEPA's reflective dataset shape.

        Each record is a turn-sliced dict tuned for multi-component /
        multi-turn / multi-run / tool-using / multi-metric scenarios:

          - ``case_id``: stable identifier for cross-referencing.
          - ``score``: aggregated case score in ``[0, 1]``; ``1.0`` =
            every metric passed.
          - ``Case Body``: turn-sliced markdown — see :func:`_build_case_body`.
          - ``Other Active Components`` *(optional)*: current text of
            every other prompt in the candidate. Present only when the
            candidate has more than one component and the others
            contain text. See :func:`_build_other_active_components`.

        Cases on the evaluator-error path (no runs produced) surface a
        minimal record whose Case Body is the captured ``error_message``,
        so the reflection LM still sees that the case failed.
        """
        if not components_to_update:
            return {}

        trajectories = getattr(eval_batch, "trajectories", None)
        if not trajectories:
            return {comp: [] for comp in components_to_update}

        # Per-component records: ``Other Active Components`` depends on
        # which component is being rewritten this round, so rebuild it.
        dataset: dict[str, list[Mapping[str, Any]]] = {}
        for comp in components_to_update:
            other_components_md = _build_other_active_components(candidate, [comp])
            records: list[Mapping[str, Any]] = []
            for traj in trajectories:
                score = traj.get("score", 0.0)
                if score >= 1.0:
                    continue

                case = traj.get("_case")
                case_runs = traj.get("_case_runs") or []
                if not isinstance(case, EvalCase):
                    continue

                case_body = (_build_case_body(case, case_runs) if case_runs else "")
                if not case_body:
                    # Evaluator-error path: fall back to the captured
                    # error_message so the LM still gets a diagnostic.
                    case_body = (traj.get("error_message") or "(no trajectory data captured)")
                record: dict[str, Any] = {
                    "case_id": case.eval_id,
                    "score": float(score),
                    "Case Body": case_body,
                }
                history = self._best_history.get(case.eval_id, [])[:self._top_k]
                if history:
                    record["history_top_k"] = history
                if other_components_md:
                    record["Other Active Components"] = other_components_md
                records.append(record)
            dataset[comp] = records
        return dataset

    def _build_evaluation_batch(
        self,
        *,
        batch: list[EvalCase],
        result: Optional[EvaluateResult],
        capture_traces: bool,
        evaluation_batch_cls: type,
    ) -> Any:
        scores: list[float] = []
        outputs: list[Any] = []
        trajectories: Optional[list[dict[str, Any]]] = [] if capture_traces else None
        # Per-case per-metric scores. Dropped to ``None`` after the loop
        # if no metric data was collected, so gepa's per-objective
        # frontier stays inactive when the evaluator emits none.
        objective_scores: list[dict[str, float]] = []

        if result is None or not result.results_by_eval_set_id:
            for case in batch:
                scores.append(0.0)
                outputs.append("")
                objective_scores.append({})
                if trajectories is not None:
                    trajectories.append(_build_trajectory_entry(case, 0.0, error_message="no result returned"))
            return evaluation_batch_cls(
                outputs=outputs,
                scores=scores,
                trajectories=trajectories,
                objective_scores=None,
            )

        set_result = next(iter(result.results_by_eval_set_id.values()))

        for case in batch:
            case_runs = set_result.eval_results_by_eval_id.get(case.eval_id, [])
            if not case_runs:
                scores.append(0.0)
                outputs.append("")
                objective_scores.append({})
                if trajectories is not None:
                    trajectories.append(
                        _build_trajectory_entry(
                            case,
                            0.0,
                            error_message="case missing from evaluator result",
                        ))
                continue

            case_score = _continuous_case_score(case_runs)
            scores.append(case_score)
            objective_scores.append(_per_metric_objective_scores(case_runs))

            first_run = case_runs[0]
            outputs.append(_extract_case_output(first_run))

            self._record_history(
                case_id=case.eval_id,
                score=case_score,
                best_response=_extract_case_output(first_run),
            )

            if trajectories is not None:
                trajectories.append(_build_trajectory_entry(case, case_score, case_runs=case_runs))

        # Keep the field active when ANY case produced a non-empty metric map;
        # GEPA treats ``None`` as "no per-objective data".
        has_objective_data = any(scores_map for scores_map in objective_scores)
        return evaluation_batch_cls(
            outputs=outputs,
            scores=scores,
            trajectories=trajectories,
            objective_scores=objective_scores if has_objective_data else None,
        )
