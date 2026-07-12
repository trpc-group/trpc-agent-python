"""End-to-end trust-aware counterfactual evaluation/optimization pipeline."""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

from trpc_agent_sdk.evaluation import EvalSet, get_all_tool_calls

from ..fake.model import generate_trace
from ..fake.optimizer import optimize as fake_optimize
from .counterfactual import diagnose_trace_case
from .diagnosis import build_failure_digest, classify_non_agent_failure, select_target_prompts
from .gate import evaluate_gate
from .input_audit import audit_eval_cases
from .optimizer import apply_if_accepted
from .probe import evaluate_trace_cases


def _load(path: Path) -> EvalSet:
    return EvalSet.model_validate_json(path.read_text(encoding="utf-8"))


def _text(case) -> str:
    return " ".join(p.text or "" for p in case.conversation[0].user_content.parts).strip().lower()


def _pass(metrics: dict[str, float]) -> bool:
    return bool(metrics) and all(v >= 1 for v in metrics.values())


def _score(results: dict[str, dict[str, float]], total_cases: int | None = None) -> float:
    denominator = len(results) if total_cases is None else total_cases
    return sum(_pass(v) for v in results.values()) / denominator if denominator else 0.0


def _metric_breakdown(results: dict[str, dict[str, float]], total_cases: int) -> dict[str, float]:
    names = sorted({name for metrics in results.values() for name in metrics})
    return (
        {name: sum(metrics.get(name, 0.0) for metrics in results.values()) / total_cases for name in names}
        if total_cases
        else {}
    )


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _content_text(content) -> str:
    if not content:
        return ""
    return "".join(part.text or "" for part in content.parts)


async def _evaluate_with_triage(cases, workspace: Path):
    """Evaluate each case so evaluator/infrastructure failures remain attributable."""
    metrics_by_id = {}
    failures = {}
    for case in cases:
        try:
            metrics_by_id.update(await evaluate_trace_cases([case], workspace))
        except Exception as error:  # classification is intentionally centralized
            failures[case.eval_id] = classify_non_agent_failure(case.eval_id, error=error)
    return metrics_by_id, failures


def _case_results(cases, metrics_by_id: dict[str, dict[str, float]], failures=None) -> list[dict]:
    failures = failures or {}
    results = []
    for case in cases:
        expected = case.conversation[0]
        actual = case.actual_conversation[0]
        metrics = metrics_by_id.get(case.eval_id, {})
        failure = failures.get(case.eval_id)
        failed_metrics = sorted(name for name, score in metrics.items() if score < 1.0)
        results.append(
            {
                "case_id": case.eval_id,
                "passed": _pass(metrics) and failure is None,
                "metrics": metrics,
                "execution_failure": failure.to_dict() if failure else None,
                "failure_reason": (
                    failure.primary_category
                    if failure
                    else f"failed_metrics:{','.join(failed_metrics)}" if failed_metrics else None
                ),
                "trace_summary": {
                    "user_input": _content_text(expected.user_content),
                    "expected_tools": [tool.name for tool in get_all_tool_calls(expected.intermediate_data)],
                    "actual_tools": [tool.name for tool in get_all_tool_calls(actual.intermediate_data)],
                    "expected_final_response": _content_text(expected.final_response),
                    "actual_final_response": _content_text(actual.final_response),
                },
            }
        )
    return results


def _markdown(report: dict) -> str:
    attrs = report["failure_attribution"]["items"]
    deltas = report["candidate_validation"]["case_deltas"]
    return "\n".join(
        [
            "# Trust-Aware Counterfactual Optimization Report",
            "",
            f"Gate: **{'ACCEPTED' if report['gate']['accepted'] else 'REJECTED'}**",
            "",
            "## Baseline failures",
            "",
            *[f"- `{x['case_id']}`: {x['primary_category']} (actionable={x['prompt_actionable']})" for x in attrs],
            "",
            "## Candidate changes",
            "",
            *[f"- `{x['case_id']}`: {x['change']}" for x in deltas],
            "",
            "## Counterfactual regression diagnosis",
            "",
            *[
                f"- `{x['case_id']}`: {x['regression_category']} via {x['counterfactual_evidence']['intervention']}"
                for x in report["regression_diagnosis"]["items"]
            ],
            "",
            "## Decision",
            "",
            f"- Reasons: {', '.join(report['gate']['reason_codes']) or 'all checks passed'}",
            f"- Source write-back: {report['audit']['write_back']['applied']}",
            "",
            "## Optimization",
            "",
            f"- Target prompts: {', '.join(report['target_selection']['selected']) or 'none'}",
            f"- Candidate profile: {report['optimization'].get('candidate_profile', 'real')}",
            "",
            "## Known limitations",
            "",
            *[f"- {item}" for item in report["known_limitations"]],
            "",
        ]
    )


async def run_pipeline(
    base_dir: Path, mode: str, output_dir: Path, apply: bool = False, candidate_profile: str = "overfit"
) -> dict:
    if mode not in {"fake", "trace"}:
        raise ValueError("mode must be fake or trace; real optimization uses run_real_optimizer()")
    started = time.perf_counter()
    train_path, val_path = base_dir / "train.evalset.json", base_dir / "val.evalset.json"
    gate_config = json.loads((base_dir / "gate.json").read_text(encoding="utf-8"))
    prompt_paths = {
        name: base_dir / "prompts" / filename
        for name, filename in {
            "router_prompt": "router.md",
            "skill_prompt": "skill.md",
            "system_prompt": "system.md",
        }.items()
    }
    prompts = {name: path.read_text(encoding="utf-8") for name, path in prompt_paths.items()}
    train, val = _load(train_path), _load(val_path)
    train_inputs, val_inputs = {_text(c) for c in train.eval_cases}, {_text(c) for c in val.eval_cases}
    overlap = train_inputs & val_inputs
    raw_cases = [
        {
            "case_id": c.eval_id,
            "normalized_input": _text(c),
            "has_actual": bool(c.actual_conversation),
            "has_reference": bool(c.conversation),
        }
        for c in train.eval_cases + val.eval_cases
    ]
    reliability = audit_eval_cases(raw_cases, overlap)
    input_errors = []
    if train_path.resolve() == val_path.resolve():
        input_errors.append("train_and_validation_paths_equal")
    ids = [c.eval_id for c in train.eval_cases + val.eval_cases]
    if len(ids) != len(set(ids)):
        input_errors.append("duplicate_eval_id")
    if train_inputs == val_inputs:
        input_errors.append("train_validation_inputs_identical")

    work = output_dir / ".trace_work"
    baseline_train_cases = (
        [generate_trace(c, prompts) for c in train.eval_cases] if mode == "fake" else train.eval_cases
    )
    baseline_val_cases = [generate_trace(c, prompts) for c in val.eval_cases] if mode == "fake" else val.eval_cases
    baseline_train, baseline_train_errors = await _evaluate_with_triage(baseline_train_cases, work)
    baseline_val, baseline_val_errors = await _evaluate_with_triage(baseline_val_cases, work)
    trusted = {x["case_id"] for x in reliability if x["status"] == "trusted"}
    attributions = []
    baseline_train_by_id = {c.eval_id: c for c in baseline_train_cases}
    for case in train.eval_cases:
        if case.eval_id in baseline_train_errors:
            attributions.append(baseline_train_errors[case.eval_id])
        elif not _pass(baseline_train[case.eval_id]):
            state = next(x for x in reliability if x["case_id"] == case.eval_id)
            if state["status"] == "invalid":
                attributions.append(
                    classify_non_agent_failure(case.eval_id, reliability="invalid", issues=state["issues"])
                )
            else:
                attributions.append(
                    await diagnose_trace_case(
                        baseline_train_by_id[case.eval_id], work, gate_config["max_counterfactual_evaluations_per_case"]
                    )
                )
    targets = select_target_prompts([x for x in attributions if x.case_id in trusted])
    optimization = await fake_optimize(prompts, targets, profile=candidate_profile)
    candidates_train = [generate_trace(c, optimization["best_prompts"]) for c in train.eval_cases]
    candidates_val = [generate_trace(c, optimization["best_prompts"]) for c in val.eval_cases]
    candidate_train, candidate_train_errors = await _evaluate_with_triage(candidates_train, work)
    candidate_val, candidate_val_errors = await _evaluate_with_triage(candidates_val, work)
    deltas, new_hard, protected = [], [], []
    for case in val.eval_cases:
        before, after = baseline_val.get(case.eval_id, {}), candidate_val.get(case.eval_id, {})
        bp, ap = _pass(before), _pass(after)
        candidate_error = candidate_val_errors.get(case.eval_id)
        change = (
            "new_pass"
            if not bp and ap
            else (
                "new_fail"
                if bp and not ap
                else (
                    "score_improved"
                    if sum(after.values()) > sum(before.values())
                    else "score_regressed" if sum(after.values()) < sum(before.values()) else "unchanged"
                )
            )
        )
        deltas.append(
            {
                "case_id": case.eval_id,
                "change": change,
                "before": before,
                "after": after,
                "execution_failure": candidate_error.to_dict() if candidate_error else None,
            }
        )
        if change == "new_fail":
            new_hard.append(case.eval_id)
            if case.eval_id in gate_config["protected_cases"]:
                protected.append(case.eval_id)
    regressions = []
    candidate_by_id = {c.eval_id: c for c in candidates_val}
    for delta in deltas:
        if delta["change"] in ("new_fail", "score_regressed"):
            diagnosis = candidate_val_errors.get(delta["case_id"])
            proof = None
            if diagnosis is None:
                diagnosis = await diagnose_trace_case(
                    candidate_by_id[delta["case_id"]], work, gate_config["max_counterfactual_evaluations_per_case"]
                )
                proof = next(
                    (e for e in diagnosis.evidence if hasattr(e, "changed_fail_to_pass") and e.changed_fail_to_pass),
                    None,
                )
            regressions.append(
                {
                    "case_id": delta["case_id"],
                    "change": delta["change"],
                    "regression_category": diagnosis.primary_category,
                    "candidate_related_surface": (
                        diagnosis.recommended_target_prompts[0] if diagnosis.recommended_target_prompts else None
                    ),
                    "counterfactual_evidence": {
                        "intervention": proof.intervention if proof else None,
                        "result": "pass" if proof else "unresolved",
                    },
                }
            )
    duration = time.perf_counter() - started
    trusted_val_count = sum(case.eval_id in trusted for case in val.eval_cases)
    severity_escalations = [
        {"case_id": item["case_id"], "before": "none", "after": item["regression_category"]}
        for item in regressions
        if item["change"] == "new_fail"
    ]
    gate = evaluate_gate(
        {
            "baseline_train": _score(baseline_train, len(train.eval_cases)),
            "candidate_train": _score(candidate_train, len(train.eval_cases)),
            "baseline_validation": _score(baseline_val, len(val.eval_cases)),
            "candidate_validation": _score(candidate_val, len(val.eval_cases)),
            "trusted_baseline": _score({k: v for k, v in baseline_val.items() if k in trusted}, trusted_val_count),
            "trusted_candidate": _score({k: v for k, v in candidate_val.items() if k in trusted}, trusted_val_count),
            "new_hard_fails": new_hard,
            "protected_regressions": protected,
            "severity_escalations": severity_escalations,
            "cost": optimization["cost"],
            "duration_seconds": duration,
            "evidence_sufficient": (
                not candidate_train_errors
                and not candidate_val_errors
                and all(x["counterfactual_evidence"]["result"] == "pass" for x in regressions)
            ),
        },
        gate_config,
    )
    for round_record in optimization["rounds"]:
        round_record.update(
            {
                "validation_score": _score(candidate_val, len(val.eval_cases)),
                "metric_breakdown": _metric_breakdown(candidate_val, len(val.eval_cases)),
                "accepted": gate["accepted"],
                "acceptance_reason": "all gate checks passed" if gate["accepted"] else ",".join(gate["reason_codes"]),
            }
        )
    write_back = await apply_if_accepted(gate, apply, optimization["best_prompts"], prompt_paths)
    digest = build_failure_digest(attributions)
    candidate_train_score = _score(candidate_train, len(train.eval_cases))
    candidate_validation_score = _score(candidate_val, len(val.eval_cases))
    baseline_train_score = _score(baseline_train, len(train.eval_cases))
    baseline_validation_score = _score(baseline_val, len(val.eval_cases))
    report = {
        "schema_version": "1.0",
        "input_audit": {"valid": not input_errors, "errors": input_errors},
        "baseline": {
            "train": {
                "score": baseline_train_score,
                "cases": baseline_train,
                "case_results": _case_results(baseline_train_cases, baseline_train, baseline_train_errors),
            },
            "validation": {
                "score": baseline_validation_score,
                "cases": baseline_val,
                "case_results": _case_results(baseline_val_cases, baseline_val, baseline_val_errors),
            },
        },
        "evalset_reliability": {"cases": reliability},
        "failure_attribution": {"items": [x.to_dict() for x in attributions]},
        "prompt_actionability": digest,
        "target_selection": {"selected": targets},
        "optimization": optimization,
        "candidate": {
            "train": {
                "score": candidate_train_score,
                "cases": candidate_train,
                "case_results": _case_results(candidates_train, candidate_train, candidate_train_errors),
            },
            "validation": {
                "score": candidate_validation_score,
                "cases": candidate_val,
                "case_results": _case_results(candidates_val, candidate_val, candidate_val_errors),
            },
        },
        "delta": {
            "train_score": candidate_train_score - baseline_train_score,
            "validation_score": candidate_validation_score - baseline_validation_score,
            "case_deltas": deltas,
        },
        "candidate_validation": {
            "train_score": candidate_train_score,
            "validation_score": candidate_validation_score,
            "case_deltas": deltas,
        },
        "regression_diagnosis": {"items": regressions},
        "gate": gate,
        "audit": {
            "seed": 42,
            "mode": mode,
            "duration_seconds": duration,
            "cost": {"total": optimization["cost"]},
            "input_hashes": {"train": _sha(train_path), "validation": _sha(val_path)},
            "prompt_hashes": {k: _sha(v) for k, v in prompt_paths.items()},
            "write_back": write_back,
            "reproduction_command": (
                "python examples/optimization/eval_optimize_loop/run_pipeline.py "
                f"--mode {mode} --candidate-profile {candidate_profile}"
            ),
        },
        "known_limitations": [
            "A local trace edit can be structurally valid but semantically incoherent with an original tool response.",
            "LLM-judge variance auditing requires repeated real-judge samples and is not "
            "exercised by this deterministic example.",
            "Real optimizer wiring is mock-verified; production execution requires "
            "credentials, call_agent, and trace capture.",
        ],
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "optimizer_failure_digest.json").write_text(json.dumps(digest, indent=2), encoding="utf-8")
    (output_dir / "optimization_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    (output_dir / "optimization_report.md").write_text(_markdown(report), encoding="utf-8")
    return report
