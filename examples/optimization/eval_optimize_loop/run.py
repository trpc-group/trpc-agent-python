# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Six-stage Evaluation + Optimization loop around AgentOptimizer.

This example is intentionally self-contained. It has two execution modes:

* fake: no API key, deterministic fake model/judge/optimizer, complete report.
* live: real LlmAgent bridge plus real AgentOptimizer.optimize.

Both modes use the same train/validation evalsets, scorer, gate, report schema,
and prompt snapshots. The fake mode exists so the closed-loop behavior can be
tested in CI or on a laptop with no model credentials.

The live path registers one ``TargetPrompt`` field and delegates candidate search
to ``AgentOptimizer``. Raw live artifacts under ``runs/latest/agent_optimizer``
include optimizer ``RoundRecord`` files; this outer script adds the issue-level
baseline/candidate/delta/gate/audit report around those SDK artifacts.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
import shutil
import sys
import time
from collections import Counter
from dataclasses import asdict
from dataclasses import dataclass
from datetime import datetime
from datetime import timezone
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

try:
    from trpc_agent_sdk.evaluation import AgentEvaluator
    from trpc_agent_sdk.evaluation import AgentOptimizer
    from trpc_agent_sdk.evaluation import EvalSet
    from trpc_agent_sdk.evaluation import TargetPrompt
    SDK_IMPORT_ERROR = None
except Exception as exc:  # pragma: no cover - fake mode should still explain itself.
    AgentEvaluator = None
    AgentOptimizer = None
    EvalSet = None
    TargetPrompt = None
    SDK_IMPORT_ERROR = f"{type(exc).__name__}: {exc}"


LOGGER = logging.getLogger("eval_optimize_loop")


@dataclass
class CaseResult:
    """Per-case score record persisted into optimization_report.json.

    The fields mirror the issue acceptance criteria: metric scores, pass/fail,
    hard-fail status, failure reasons, and key trajectory data.
    """

    case_id: str
    score: float
    passed: bool
    hard_fail: bool
    key: bool
    metrics: dict[str, float]
    failure_types: list[str]
    reason: str
    trace: dict[str, Any]


def load_json(path: Path) -> dict[str, Any]:
    """Load a JSON config/evalset document with a readable fatal error."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise SystemExit(f"Cannot read JSON file {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid JSON in {path}: {exc}") from exc


def write_json(path: Path, data: dict[str, Any]) -> None:
    """Write a stable UTF-8 JSON artifact used by the audit trail."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def sha256_text(text: str) -> str:
    """Hash prompt text so audits can prove which candidate was evaluated."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def resolve_path(value: str) -> Path:
    """Resolve example-relative paths from optimizer.json and CLI flags."""
    path = Path(value)
    return path if path.is_absolute() else HERE / path


def validate_evalset(path: Path) -> dict[str, Any]:
    """Validate an evalset with SDK ``EvalSet`` when available."""
    raw = path.read_text(encoding="utf-8")
    data = json.loads(raw)
    if EvalSet is not None:
        EvalSet.model_validate_json(raw)
    if not data.get("eval_cases"):
        raise SystemExit(f"{path} has no eval_cases")
    return data


async def sdk_trace_smoke(evalset_path: Path) -> dict[str, Any]:
    """Run SDK ``AgentEvaluator`` on one trace-mode evalset.

    The outer loop has its own deterministic scorer so fake mode remains usable
    even when optional SDK dependencies are missing. When ``AgentEvaluator`` is
    importable, this function records a real trace-mode SDK evaluation attempt
    for both train and validation sets.
    """
    metrics_path = HERE / "_sdk_eval_metrics.json"
    write_json(
        metrics_path,
        {
            "metrics": [
                {
                    "metric_name": "final_response_avg_score",
                    "threshold": 0.1,
                    "criterion": {
                        "final_response": {
                            "text": {"match": "contains", "case_insensitive": True}
                        }
                    },
                }
            ]
        },
    )
    if AgentEvaluator is None:
        return {
            "status": "SKIPPED",
            "reason": "AgentEvaluator import failed",
            "import_error": SDK_IMPORT_ERROR,
        }

    cwd = Path.cwd()
    os.chdir(HERE)
    try:
        runner = AgentEvaluator.get_executer(
            evalset_path.name,
            eval_metrics_file_path_or_dir=metrics_path.name,
            print_detailed_results=False,
            print_summary_report=False,
        )
        try:
            await asyncio.wait_for(runner.evaluate(), timeout=30)
            status = "PASSED"
            reason = "AgentEvaluator trace-mode evaluation completed"
        except AssertionError as exc:
            status = "FAILED_EXPECTED"
            reason = str(exc)[:500]
        except Exception as exc:  # pragma: no cover - SDK runtime drift.
            status = "FAILED_SDK_SMOKE"
            reason = f"{type(exc).__name__}: {str(exc)[:500]}"
        return {
            "status": status,
            "reason": reason,
            "evalset": evalset_path.name,
            "has_result": runner.get_result() is not None,
            "metrics_file": metrics_path.name,
        }
    finally:
        os.chdir(cwd)


def text_field(invocation: dict[str, Any], field_name: str) -> str:
    """Extract concatenated text from an EvalCase invocation field."""
    content = invocation.get(field_name) or {}
    return "".join(part.get("text", "") for part in content.get("parts", []))


def expected_tools(invocation: dict[str, Any]) -> list[dict[str, Any]]:
    """Return expected tool calls from an EvalCase trace invocation."""
    return (invocation.get("intermediate_data") or {}).get("tool_uses") or []


def normalize_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalize tool calls for order-sensitive trajectory comparison."""
    return [{"name": item.get("name"), "args": item.get("args", {})} for item in tools]


def fake_agent(prompt: str, query: str) -> dict[str, Any]:
    """Deterministic fake model used by fake mode.

    The fake reads prompt feature flags written by the scripted optimizer. This
    gives repeatable behavior changes without a model key or remote service.
    """
    use_catalog = "USE_CATALOG_LOOKUP" in prompt
    aggressive_lookup = "AGGRESSIVE_LOOKUP" in prompt

    if "shipping status for order A100" in query:
        if use_catalog:
            return {
                "text": "Order A100 is in transit and arrives on Friday.",
                "tools": [{"name": "lookup_order", "args": {"order_id": "A100"}}],
            }
        return {"text": "I do not have enough order data.", "tools": []}

    if "refund policy for damaged items" in query:
        if use_catalog:
            return {
                "text": "Damaged items are eligible for a full refund within 30 days.",
                "tools": [{"name": "search_policy", "args": {"topic": "damaged item refund"}}],
            }
        return {"text": "You may be eligible, but I cannot confirm the policy.", "tools": []}

    if "Return only JSON" in query:
        return {"text": "status ok", "tools": []}

    if "warranty period for Model Z" in query:
        if use_catalog:
            return {
                "text": "Model Z has a 24-month warranty.",
                "tools": [{"name": "search_policy", "args": {"topic": "Model Z warranty"}}],
            }
        return {
            "text": "I am not sure about the Model Z warranty.",
            "tools": [{"name": "web_search", "args": {"query": "Model Z warranty"}}],
        }

    if query.strip() == "Thanks":
        if aggressive_lookup:
            return {
                "text": "You are welcome.",
                "tools": [{"name": "search_policy", "args": {"topic": "thanks"}}],
            }
        return {"text": "You are welcome.", "tools": []}

    if "order A200" in query:
        if aggressive_lookup:
            return {
                "text": "Order A200 is delivered.",
                "tools": [
                    {"name": "lookup_order", "args": {"order_id": "A200"}},
                    {"name": "search_policy", "args": {"topic": "order A200"}},
                ],
            }
        return {
            "text": "Order A200 is delivered.",
            "tools": [{"name": "lookup_order", "args": {"order_id": "A200"}}],
        }

    return {"text": "I can help with support questions.", "tools": []}


def rubric_score(meta: dict[str, Any], output: dict[str, Any]) -> float:
    """Score the case-specific fake judge rubric declared in case_meta.json."""
    kind = meta.get("rubric", "none")
    if kind == "json_format":
        try:
            json.loads(output["text"])
            return 1.0
        except json.JSONDecodeError:
            return 0.0
    if kind == "no_tool":
        return 1.0 if not output["tools"] else 0.0
    if kind == "single_tool":
        return 1.0 if len(output["tools"]) <= 1 else 0.5
    return 1.0


def classify_tool_failure(
    actual: list[dict[str, Any]],
    expected: list[dict[str, Any]],
    meta: dict[str, Any],
) -> str | None:
    """Cluster tool trajectory failures into issue-required categories."""
    actual_norm = normalize_tools(actual)
    expected_norm = normalize_tools(expected)
    if actual_norm == expected_norm:
        return None
    actual_names = {item["name"] for item in actual_norm}
    expected_names = {item["name"] for item in expected_norm}
    authoritative = meta.get("authoritative_tool")
    if authoritative and authoritative not in actual_names:
        return "knowledge_recall_insufficient"
    if actual_norm and all(item in actual_norm for item in expected_norm) and len(actual_norm) > len(expected_norm):
        return "spurious_tool_call"
    if actual_norm and expected_norm and actual_norm[0]["name"] == expected_norm[0]["name"]:
        return "parameter_error"
    if expected_names and not actual_names:
        return "tool_call_error"
    return "tool_call_error"


def classify_rubric_failure(meta: dict[str, Any]) -> str:
    """Map failed rubric dimensions to human-auditable failure labels."""
    if meta.get("rubric") == "json_format":
        return "format_error"
    return "llm_rubric_not_met"


def failure_types_for(
    meta: dict[str, Any],
    final_score: float,
    tool_score: float,
    rubric: float,
    output: dict[str, Any],
    expected: list[dict[str, Any]],
) -> list[str]:
    """Collect all failure labels for one case from metric sub-scores."""
    failures: list[str] = []
    if final_score < 1.0:
        failures.append("final_response_mismatch")
    if tool_score < 1.0:
        label = classify_tool_failure(output["tools"], expected, meta)
        if label:
            failures.append(label)
    if rubric < 1.0:
        failures.append(classify_rubric_failure(meta))
    return list(dict.fromkeys(failures))


async def produce_output(query: str, prompt_path: Path, mode: str) -> dict[str, Any]:
    """Run either the fake agent or the live ``LlmAgent`` bridge."""
    prompt_text = prompt_path.read_text(encoding="utf-8")
    if mode == "live":
        from agent.agent import run_agent

        return await run_agent(query=query, prompt_path=prompt_path)
    return fake_agent(prompt_text, query)


def score_case(
    case: dict[str, Any],
    output: dict[str, Any],
    cfg: dict[str, Any],
    case_meta: dict[str, Any],
) -> CaseResult:
    """Score one EvalCase against already-produced model output."""
    invocation = case["conversation"][0]
    case_id = case["eval_id"]
    query = text_field(invocation, "user_content")
    expected_text = text_field(invocation, "final_response")
    expected = expected_tools(invocation)
    meta = case_meta.get(case_id, {})

    final_score = 1.0 if expected_text.lower() in output["text"].lower() else 0.0
    tool_score = 1.0 if normalize_tools(output["tools"]) == normalize_tools(expected) else 0.0
    rubric = rubric_score(meta, output)
    weights = {item["name"]: item["weight"] for item in cfg["evaluate"]["metrics"]}
    score = round(
        final_score * weights["final_response"]
        + tool_score * weights["tool_trajectory"]
        + rubric * weights["rubric"],
        4,
    )
    failures = failure_types_for(meta, final_score, tool_score, rubric, output, expected)
    passed = score >= cfg["evaluate"].get("pass_threshold", 0.8)
    return CaseResult(
        case_id=case_id,
        score=score,
        passed=passed,
        hard_fail=score < cfg["gate"]["hard_fail_threshold"],
        key=bool(meta.get("key", False)),
        metrics={
            "final_response": final_score,
            "tool_trajectory": tool_score,
            "rubric": rubric,
        },
        failure_types=failures,
        reason="pass" if passed else "; ".join(failures or ["unknown"]),
        trace={
            "query": query,
            "expected_text": expected_text,
            "actual_text": output["text"],
            "expected_tools": expected,
            "actual_tools": output["tools"],
        },
    )


async def evaluate_evalset(
    evalset: dict[str, Any],
    prompt_path: Path,
    cfg: dict[str, Any],
    case_meta: dict[str, Any],
    mode: str,
) -> dict[str, Any]:
    """Evaluate all cases in one train/validation evalset."""
    cases: list[CaseResult] = []
    for case in evalset["eval_cases"]:
        query = text_field(case["conversation"][0], "user_content")
        output = await produce_output(query=query, prompt_path=prompt_path, mode=mode)
        cases.append(score_case(case, output, cfg, case_meta))
    mean = round(sum(item.score for item in cases) / len(cases), 4)
    return {
        "eval_set_id": evalset["eval_set_id"],
        "mean_score": mean,
        "pass_rate": round(sum(item.passed for item in cases) / len(cases), 4),
        "cases": {item.case_id: asdict(item) for item in cases},
    }


def attribute_failures(*results: dict[str, Any]) -> dict[str, Any]:
    """Cluster baseline failures and count each explanation type."""
    counts: Counter[str] = Counter()
    by_case: dict[str, list[str]] = {}
    for result in results:
        for case_id, case in result["cases"].items():
            if case["passed"]:
                continue
            failures = case["failure_types"] or ["unknown"]
            by_case[case_id] = failures
            counts.update(failures)
    return {"counts": dict(counts), "by_case": by_case}


def diff_cases(baseline: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    """Compare candidate against baseline case-by-case."""
    delta: dict[str, Any] = {}
    for case_id, cand in candidate["cases"].items():
        base = baseline["cases"][case_id]
        if not base["passed"] and cand["passed"]:
            kind = "new_pass"
        elif base["passed"] and not cand["passed"]:
            kind = "new_fail"
        elif cand["score"] > base["score"]:
            kind = "score_up"
        elif cand["score"] < base["score"]:
            kind = "score_down"
        else:
            kind = "same"
        delta[case_id] = {
            "kind": kind,
            "baseline_score": base["score"],
            "candidate_score": cand["score"],
            "delta": round(cand["score"] - base["score"], 4),
            "baseline_passed": base["passed"],
            "candidate_passed": cand["passed"],
        }
    return delta


def gate_decision(
    baseline_train: dict[str, Any],
    candidate_train: dict[str, Any],
    baseline_val: dict[str, Any],
    candidate_val: dict[str, Any],
    val_delta: dict[str, Any],
    cfg: dict[str, Any],
    cost_usd: float,
) -> dict[str, Any]:
    """Apply the configurable validation-first acceptance gate."""
    gate = cfg["gate"]
    train_gain = round(candidate_train["mean_score"] - baseline_train["mean_score"], 4)
    val_gain = round(candidate_val["mean_score"] - baseline_val["mean_score"], 4)
    new_hard_fails = [
        case_id
        for case_id, case in candidate_val["cases"].items()
        if case["hard_fail"] and not baseline_val["cases"][case_id]["hard_fail"]
    ]
    critical_regressions = [
        case_id
        for case_id, delta in val_delta.items()
        if candidate_val["cases"][case_id]["key"] and delta["kind"] in {"new_fail", "score_down"}
    ]
    checks = [
        {
            "name": "validation_gain_threshold",
            "passed": val_gain >= gate["min_val_score_gain"],
            "detail": f"val_gain={val_gain:+.4f}, required>={gate['min_val_score_gain']:+.4f}",
        },
        {
            "name": "no_new_hard_fail",
            "passed": not (gate["reject_on_new_hard_fail"] and new_hard_fails),
            "detail": f"new_hard_fails={new_hard_fails}",
        },
        {
            "name": "no_critical_regression",
            "passed": not (gate["reject_on_critical_regression"] and critical_regressions),
            "detail": f"critical_regressions={critical_regressions}",
        },
        {
            "name": "not_overfit_train_up_val_down",
            "passed": not (gate["reject_overfit_train_up_val_down"] and train_gain > 0 and val_gain < 0),
            "detail": f"train_gain={train_gain:+.4f}, val_gain={val_gain:+.4f}",
        },
        {
            "name": "cost_budget",
            "passed": cost_usd <= gate["max_cost_usd"],
            "detail": f"cost_usd={cost_usd:.6f}, budget={gate['max_cost_usd']:.6f}",
        },
    ]
    accepted = all(item["passed"] for item in checks)
    return {
        "accepted": accepted,
        "decision": "ACCEPT" if accepted else "REJECT",
        "reason": "all gates passed" if accepted else "; ".join(item["name"] for item in checks if not item["passed"]),
        "train_gain": train_gain,
        "val_gain": val_gain,
        "checks": checks,
    }


def precheck_live_mode() -> None:
    """Fail fast before live mode spends time evaluating a broken environment."""
    if AgentOptimizer is None or TargetPrompt is None:
        raise SystemExit(
            "Live mode requires trpc_agent_sdk.evaluation.AgentOptimizer and "
            f"TargetPrompt. SDK import error: {SDK_IMPORT_ERROR}"
        )
    missing = [
        name
        for name in ("TRPC_AGENT_API_KEY", "TRPC_AGENT_BASE_URL", "TRPC_AGENT_MODEL_NAME")
        if not os.getenv(name)
    ]
    if missing:
        raise SystemExit(
            "Live mode requires model credentials before baseline evaluation: "
            + ", ".join(missing)
            + ". Use --mode fake for the no-key path."
        )


def optimizer_fake(baseline_prompt: str, cfg: dict[str, Any], candidate_path: Path) -> tuple[str, dict[str, Any]]:
    """Create a deterministic fake candidate without invoking AgentOptimizer."""
    candidate = baseline_prompt.rstrip() + "\n\n" + "\n".join(cfg["optimize"]["fake_candidate_patch"]) + "\n"
    candidate_path.write_text(candidate, encoding="utf-8")
    return candidate, {
        "mode": "fake",
        "status": "SCRIPTED_CANDIDATE",
        "agent_optimizer_available": AgentOptimizer is not None,
        "agent_optimizer_invoked": False,
        "candidate_prompt_path": candidate_path.relative_to(HERE).as_posix(),
        "cost_usd": 0.0,
        "tokens": 0,
        "rounds": 1,
    }


async def optimizer_live(
    source_prompt_path: Path,
    train_path: Path,
    val_path: Path,
    cfg: dict[str, Any],
    candidate_path: Path,
    run_dir: Path,
) -> tuple[str, dict[str, Any]]:
    """Invoke SDK ``AgentOptimizer.optimize`` for the registered TargetPrompt.

    The source prompt is the snapshot under ``runs/latest``. ``update_source`` is
    configurable but defaults to false so the example produces candidates for
    review rather than silently overwriting the baseline prompt.
    """
    if AgentOptimizer is None or TargetPrompt is None:
        raise SystemExit("Live mode requires trpc_agent_sdk.evaluation.AgentOptimizer and TargetPrompt.")
    from agent.agent import make_call_agent

    sdk_config = resolve_path(cfg["optimize"]["sdk_config"])
    optimizer_dir = run_dir / "agent_optimizer"
    target = TargetPrompt().add_path("system_prompt", str(source_prompt_path))
    started = time.perf_counter()
    result = await AgentOptimizer.optimize(
        config_path=str(sdk_config),
        call_agent=make_call_agent(source_prompt_path),
        target_prompt=target,
        train_dataset_path=str(train_path),
        validation_dataset_path=str(val_path),
        output_dir=str(optimizer_dir),
        update_source=bool(cfg["optimize"].get("update_source", False)),
        verbose=int(cfg["optimize"].get("verbose", 1)),
    )
    best = (result.best_prompts or {}).get("system_prompt")
    if not best:
        best = source_prompt_path.read_text(encoding="utf-8")
    candidate_path.write_text(best, encoding="utf-8")
    token_usage = getattr(result, "total_token_usage", None) or {}
    return best, {
        "mode": "live",
        "status": getattr(result, "status", "UNKNOWN"),
        "finish_reason": getattr(result, "finish_reason", None),
        "agent_optimizer_available": True,
        "agent_optimizer_invoked": True,
        "sdk_output_dir": optimizer_dir.relative_to(HERE).as_posix(),
        "candidate_prompt_path": candidate_path.relative_to(HERE).as_posix(),
        "cost_usd": round(float(getattr(result, "total_llm_cost", 0.0) or 0.0), 6),
        "tokens": token_usage.get("total", 0) if isinstance(token_usage, dict) else 0,
        "rounds": getattr(result, "total_rounds", None),
        "duration_seconds": round(time.perf_counter() - started, 4),
    }


def build_report(
    *,
    mode: str,
    cfg: dict[str, Any],
    baseline_prompt: str,
    candidate_prompt: str,
    sdk_evaluator_runs: dict[str, Any],
    artifacts: dict[str, Any],
) -> dict[str, Any]:
    """Assemble the machine-readable issue-level audit report."""
    return {
        "run": {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "mode": mode,
            "seed": cfg["seed"],
            "sdk_bridge": {
                "agent_evaluator_available": AgentEvaluator is not None,
                "agent_optimizer_available": AgentOptimizer is not None,
                "evalset_validated_with_trpc_sdk": EvalSet is not None,
                "sdk_import_error": SDK_IMPORT_ERROR,
                "agent_evaluator_trace_runs": sdk_evaluator_runs,
            },
            "repro": {
                "train_evalset": cfg["inputs"]["train_evalset"],
                "val_evalset": cfg["inputs"]["val_evalset"],
                "case_meta": cfg["inputs"]["case_meta"],
                "prompt_source": cfg["target_prompt"]["path"],
                "optimizer_config": "optimizer.json",
                "sdk_optimizer_config": cfg["optimize"].get("sdk_config"),
            },
        },
        "prompt_audit": {
            "target": cfg["target_prompt"],
            "baseline_sha256": sha256_text(baseline_prompt),
            "candidate_sha256": sha256_text(candidate_prompt),
            "baseline_snapshot": "runs/latest/baseline_prompt.md",
            "candidate_snapshot": "runs/latest/candidate_prompt.md",
        },
        **artifacts,
    }


def render_summary(report: dict[str, Any]) -> str:
    """Create a short human-readable decision summary for Markdown."""
    gate = report["gate"]
    new_pass = [case_id for case_id, item in report["delta"]["val"].items() if item["kind"] == "new_pass"]
    new_fail = [case_id for case_id, item in report["delta"]["val"].items() if item["kind"] == "new_fail"]
    return (
        f"Decision: {gate['decision']}. "
        f"Train mean changed {report['baseline']['train']['mean_score']} -> "
        f"{report['candidate']['train']['mean_score']} ({gate['train_gain']:+.4f}); "
        f"validation mean changed {report['baseline']['val']['mean_score']} -> "
        f"{report['candidate']['val']['mean_score']} ({gate['val_gain']:+.4f}). "
        f"New validation passes: {new_pass or 'none'}. "
        f"New validation failures: {new_fail or 'none'}. "
        f"Gate reason: {gate['reason']}."
    )


def write_markdown(report: dict[str, Any], path: Path) -> None:
    """Write the human-readable optimization_report.md artifact."""
    lines = [
        "# Optimization Report",
        "",
        "## Summary",
        "",
        render_summary(report),
        "",
        "## Scores",
        "",
        f"- Mode: `{report['run']['mode']}`",
        f"- Baseline train mean: {report['baseline']['train']['mean_score']}",
        f"- Candidate train mean: {report['candidate']['train']['mean_score']}",
        f"- Baseline validation mean: {report['baseline']['val']['mean_score']}",
        f"- Candidate validation mean: {report['candidate']['val']['mean_score']}",
        f"- Decision: **{report['gate']['decision']}**",
        f"- Reason: {report['gate']['reason']}",
        "",
        "## Failure Attribution",
        "",
    ]
    for name, count in sorted(report["failure_attribution"]["counts"].items()):
        lines.append(f"- {name}: {count}")
    lines.extend(["", "## Validation Delta", ""])
    for case_id, item in report["delta"]["val"].items():
        lines.append(
            f"- `{case_id}`: {item['kind']} "
            f"({item['baseline_score']} -> {item['candidate_score']}, {item['delta']:+.4f})"
        )
    lines.extend(["", "## Gate Checks", ""])
    for check in report["gate"]["checks"]:
        status = "PASS" if check["passed"] else "FAIL"
        lines.append(f"- {status} `{check['name']}`: {check['detail']}")
    lines.extend(
        [
            "",
            "## Audit",
            "",
            f"- Cost USD: {report['audit']['cost_usd']}",
            f"- Tokens: {report['audit']['tokens']}",
            f"- Duration seconds: {report['audit']['duration_seconds']}",
            f"- Baseline SHA-256: `{report['prompt_audit']['baseline_sha256']}`",
            f"- Candidate SHA-256: `{report['prompt_audit']['candidate_sha256']}`",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


async def run_pipeline(args: argparse.Namespace) -> None:
    """Run all six issue-required stages and persist report artifacts."""
    logging.basicConfig(
        level=os.environ.get("EVAL_OPT_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    )
    started = time.perf_counter()
    cfg = load_json(resolve_path(args.optimizer))
    mode = args.mode or cfg.get("mode", "fake")
    if mode not in {"fake", "live"}:
        raise SystemExit("--mode must be fake or live")
    if mode == "live":
        precheck_live_mode()

    train_path = resolve_path(args.train or cfg["inputs"]["train_evalset"])
    val_path = resolve_path(args.val or cfg["inputs"]["val_evalset"])
    if train_path.resolve() == val_path.resolve():
        raise SystemExit("train and validation evalset paths must be different")
    prompt_source = resolve_path(args.prompt or cfg["target_prompt"]["path"])
    case_meta = {
        key: value
        for key, value in load_json(resolve_path(cfg["inputs"]["case_meta"])).items()
        if not key.startswith("_")
    }
    train = validate_evalset(train_path)
    val = validate_evalset(val_path)

    run_dir = HERE / "runs" / "latest"
    if run_dir.exists():
        shutil.rmtree(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    baseline_path = run_dir / "baseline_prompt.md"
    candidate_path = run_dir / "candidate_prompt.md"
    baseline_prompt = prompt_source.read_text(encoding="utf-8")
    baseline_path.write_text(baseline_prompt, encoding="utf-8")

    LOGGER.info("loaded mode=%s train_cases=%d val_cases=%d", mode, len(train["eval_cases"]), len(val["eval_cases"]))
    sdk_evaluator_runs = {
        "train": await sdk_trace_smoke(train_path),
        "val": await sdk_trace_smoke(val_path),
    }
    LOGGER.info(
        "AgentEvaluator trace runs: train=%s val=%s",
        sdk_evaluator_runs["train"]["status"],
        sdk_evaluator_runs["val"]["status"],
    )

    baseline_train = await evaluate_evalset(train, baseline_path, cfg, case_meta, mode)
    baseline_val = await evaluate_evalset(val, baseline_path, cfg, case_meta, mode)
    LOGGER.info("baseline mean train=%.4f val=%.4f", baseline_train["mean_score"], baseline_val["mean_score"])

    failures = attribute_failures(baseline_train, baseline_val)

    if mode == "live":
        candidate_prompt, optimizer_status = await optimizer_live(
            source_prompt_path=baseline_path,
            train_path=train_path,
            val_path=val_path,
            cfg=cfg,
            candidate_path=candidate_path,
            run_dir=run_dir,
        )
    else:
        candidate_prompt, optimizer_status = optimizer_fake(baseline_prompt, cfg, candidate_path)
    LOGGER.info(
        "optimizer status=%s invoked=%s",
        optimizer_status["status"],
        optimizer_status["agent_optimizer_invoked"],
    )

    candidate_train = await evaluate_evalset(train, candidate_path, cfg, case_meta, mode)
    candidate_val = await evaluate_evalset(val, candidate_path, cfg, case_meta, mode)
    LOGGER.info("candidate mean train=%.4f val=%.4f", candidate_train["mean_score"], candidate_val["mean_score"])

    train_delta = diff_cases(baseline_train, candidate_train)
    val_delta = diff_cases(baseline_val, candidate_val)
    gate = gate_decision(
        baseline_train,
        candidate_train,
        baseline_val,
        candidate_val,
        val_delta,
        cfg,
        optimizer_status["cost_usd"],
    )

    duration = round(time.perf_counter() - started, 4)
    report = build_report(
        mode=mode,
        cfg=cfg,
        baseline_prompt=baseline_prompt,
        candidate_prompt=candidate_prompt,
        sdk_evaluator_runs=sdk_evaluator_runs,
        artifacts={
            "baseline": {"train": baseline_train, "val": baseline_val},
            "candidate": {"train": candidate_train, "val": candidate_val},
            "delta": {"train": train_delta, "val": val_delta},
            "failure_attribution": failures,
            "optimizer": optimizer_status,
            "gate": gate,
            "audit": {
                "duration_seconds": duration,
                "cost_usd": optimizer_status["cost_usd"],
                "tokens": optimizer_status["tokens"],
                "config_snapshot": cfg,
            },
        },
    )
    write_json(HERE / "optimization_report.json", report)
    write_markdown(report, HERE / "optimization_report.md")
    print(f"{gate['decision']}: {gate['reason']}")
    print("wrote optimization_report.json and optimization_report.md")


def parse_args() -> argparse.Namespace:
    """Parse CLI flags for fake/live mode and alternate input files."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["fake", "live"], default=None)
    parser.add_argument("--optimizer", default="optimizer.json")
    parser.add_argument("--train", default=None)
    parser.add_argument("--val", default=None)
    parser.add_argument("--prompt", default=None)
    return parser.parse_args()


def main() -> None:
    """Entrypoint used by README commands and CI smoke checks."""
    asyncio.run(run_pipeline(parse_args()))


if __name__ == "__main__":
    main()
