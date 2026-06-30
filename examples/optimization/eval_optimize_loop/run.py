"""Reproducible Evaluation + Optimization closed-loop example.

The pipeline runs six auditable phases over a single system prompt:

    1. baseline evaluation (train + val, per-case metrics/pass-fail/trace)
    2. failure attribution (rule based over structured trace + case metadata)
    3. optimization (scripted AgentOptimizer bridge in fake/trace mode)
    4. candidate validation (full re-run + case-by-case diff vs baseline)
    5. acceptance gate (validation-first, configurable, multi-constraint)
    6. audit persistence (JSON + Markdown report, prompt snapshots, repro info)

Default mode is fake/trace and requires no API key. The first invocation may
spend time on a one-off ``uv sync``; once dependencies are installed the loop
itself completes in a few seconds::

    uv run python examples/optimization/eval_optimize_loop/run.py

Log verbosity is controlled by the ``YUN_LOG_LEVEL`` environment variable
(default ``INFO``).
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from trpc_agent_sdk.evaluation import AgentEvaluator, AgentOptimizer, EvalSet, TargetPrompt
except Exception:  # pragma: no cover - keeps fake mode runnable if the SDK changes.
    AgentEvaluator = AgentOptimizer = TargetPrompt = None
    EvalSet = None

HERE = Path(__file__).resolve().parent
logger = logging.getLogger("eval_optimize_loop")

# Tool name treated as the "authoritative" search backend. When a case declares
# ``tool_intent == "authoritative_search"`` and the agent fails to call this
# tool, the trajectory miss is attributed to weak knowledge recall rather than a
# generic tool error.
AUTHORITATIVE_SEARCH_TOOL = "uapi_search"


@dataclass
class CaseResult:
    """Scored outcome of a single evaluation case.

    Attributes:
        case_id: The ``eval_id`` of the case.
        score: Weighted aggregate score in ``[0, 1]``.
        passed: Whether ``score`` meets the pass threshold.
        hard_fail: Whether ``score`` falls below the gate hard-fail threshold.
        key: Whether the case is marked critical (must not regress).
        metrics: Per-metric sub-scores (final_response / tool_trajectory / rubric).
        failure_types: Attributed failure categories (empty when passed).
        reason: Human-readable summary (``"pass"`` or joined failure types).
        trace: Key trajectory fields used for attribution and auditing.
    """

    case_id: str
    score: float
    passed: bool
    hard_fail: bool
    key: bool
    metrics: dict[str, float]
    failure_types: list[str]
    reason: str
    trace: dict[str, Any] = field(default_factory=dict)


def load_json(path: Path) -> dict[str, Any]:
    """Load a JSON document, raising a readable error on malformed input.

    Args:
        path: Path to the JSON file.

    Returns:
        The parsed JSON object.

    Raises:
        SystemExit: If the file cannot be read or parsed.
    """
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise SystemExit(f"无法读取 JSON 文件 {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise SystemExit(f"JSON 解析失败 {path}: {exc}") from exc


def write_json(path: Path, data: dict[str, Any]) -> None:
    """Serialize ``data`` to ``path`` as UTF-8 JSON with stable indentation."""
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def sha256_text(text: str) -> str:
    """Return the hex SHA-256 digest of ``text`` (used for prompt audit)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def validate_evalset(path: Path) -> dict[str, Any]:
    """Load an evalset and validate it against the SDK schema when available.

    JSON/IO errors abort with a readable message; an SDK schema mismatch is a
    non-fatal warning (the fake evaluator only needs the documented fields), but
    an evalset with no ``eval_cases`` is treated as fatal.

    Args:
        path: Path to the ``*.evalset.json`` file.

    Returns:
        The parsed evalset object.

    Raises:
        SystemExit: On IO/JSON errors or an empty/invalid evalset.
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SystemExit(f"无法读取评测集 {path}: {exc}") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"评测集 JSON 解析失败 {path}: {exc}") from exc
    if EvalSet is not None:
        try:
            EvalSet.model_validate_json(raw)
        except Exception as exc:  # pragma: no cover - schema drift is non-fatal here.
            logger.warning("EvalSet schema 校验未通过 %s: %s", path.name, str(exc)[:300])
    if not data.get("eval_cases"):
        raise SystemExit(f"评测集缺少 eval_cases 或为空: {path}")
    return data


def sdk_trace_smoke(evalset_path: Path) -> dict[str, Any]:
    """Run a trace-only ``AgentEvaluator`` smoke check against an evalset.

    This proves the example is wired to the real SDK evaluator without needing a
    model: the metric uses a deterministic ``final_response contains`` criterion.
    A threshold miss is expected and reported as ``FAILED_EXPECTED`` rather than
    an error, so the smoke never blocks the pipeline.

    Args:
        evalset_path: Evalset to feed the SDK evaluator.

    Returns:
        A status dict describing the smoke outcome.
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
        logger.warning("AgentEvaluator 不可导入，跳过 SDK 冒烟。")
        return {"status": "SKIPPED", "reason": "AgentEvaluator is not importable"}

    async def _run() -> dict[str, Any]:
        cwd = Path.cwd()
        os.chdir(HERE)
        try:
            executer = AgentEvaluator.get_executer(
                evalset_path.name,
                eval_metrics_file_path_or_dir=metrics_path.name,
                print_detailed_results=False,
                print_summary_report=False,
            )
            try:
                await executer.evaluate()
                status = "PASSED"
                reason = "trace-only AgentEvaluator smoke completed"
            except AssertionError as exc:
                status = "FAILED_EXPECTED"
                reason = str(exc)[:500]
            except Exception as exc:  # pragma: no cover - defensive: SDK runtime error.
                status = "FAILED_SDK_SMOKE"
                reason = f"{type(exc).__name__}: {str(exc)[:500]}"
            return {
                "status": status,
                "reason": reason,
                "has_result": executer.get_result() is not None,
                "metrics_file": metrics_path.name,
            }
        finally:
            os.chdir(cwd)

    return asyncio.run(_run())


def invocation_text(invocation: dict[str, Any], field_name: str) -> str:
    """Concatenate the text parts of a conversation field (``user_content`` etc.)."""
    content = invocation[field_name]
    return "".join(part.get("text", "") for part in content.get("parts", []))


def expected_tools(invocation: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the expected ``tool_uses`` list from an expected invocation."""
    data = invocation.get("intermediate_data") or {}
    return data.get("tool_uses") or []


def fake_agent(prompt: str, query: str) -> dict[str, Any]:
    """Deterministic stand-in for a real agent, keyed off prompt feature flags.

    The fake reads two scripted capability flags from the prompt
    (``USE_UAPI_TOOLS`` / ``AGGRESSIVE_SEARCH``) so the optimization candidate
    produces observable behavior changes without any model call.

    Args:
        prompt: The system prompt (baseline or candidate) currently under test.
        query: The user query for this case.

    Returns:
        A dict with ``text`` (final response) and ``tools`` (tool-call list).
    """
    uses_uapi = "USE_UAPI_TOOLS" in prompt
    aggressive_search = "AGGRESSIVE_SEARCH" in prompt

    if "公网 IP" in query:
        if uses_uapi:
            return {"text": "你的公网 IP 是 203.0.113.10。", "tools": [{"name": "get_my_public_ip", "args": {"source": "commercial"}}]}
        return {"text": "我无法确定你的公网 IP。", "tools": []}

    if "2026-10-01" in query:
        if uses_uapi:
            return {"text": "2026-10-01 是法定休息日。", "tools": [{"name": "query_holiday_calendar", "args": {"date": "2026-10-01", "holiday_type": "legal"}}]}
        return {"text": "这天大概率是节日，但我没有查询。", "tools": []}

    if "只返回 JSON" in query:
        return {"text": "status ok", "tools": []}

    if "Go 最新版本" in query:
        if uses_uapi:
            return {"text": "Go 1.26 是当前查询到的最新版本。", "tools": [{"name": "uapi_search", "args": {"query": "Go 最新版本"}}]}
        return {"text": "Go 有新版本，但我没有足够信息确认。", "tools": [{"name": "websearch", "args": {"query": "Go 最新版本"}}]}

    if query == "在吗":
        if aggressive_search:
            return {"text": "我查了一下网页：在。", "tools": [{"name": "uapi_search", "args": {"query": "在吗"}}]}
        return {"text": "在。", "tools": []}

    if "北京天气" in query:
        if aggressive_search:
            return {
                "text": "北京今天有天气信息。",
                "tools": [
                    {"name": "get_current_weather", "args": {"city": "北京"}},
                    {"name": "uapi_search", "args": {"query": "北京天气"}},
                ],
            }
        return {"text": "北京今天有天气信息。", "tools": [{"name": "get_current_weather", "args": {"city": "北京"}}]}

    return {"text": "收到。", "tools": []}


def normalize_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Project tool calls to ``{name, args}`` for order-sensitive comparison."""
    return [{"name": tool.get("name"), "args": tool.get("args", {})} for tool in tools]


def rubric_score(meta: dict[str, Any], actual: dict[str, Any]) -> float:
    """Score the rubric dimension a case declares in ``case_meta.json``.

    The rubric kind is data driven (not inferred from the case id):

        * ``json_format``  -> 1.0 if the reply is a JSON object, else 0.0
        * ``no_tool``      -> 1.0 if no tool was called, else 0.0
        * ``single_tool``  -> 0.5 if more than one tool was called, else 1.0
        * ``none`` / unset -> 1.0

    Args:
        meta: The case metadata entry.
        actual: The agent output (``text`` + ``tools``).

    Returns:
        A rubric score in ``[0, 1]``.
    """
    kind = meta.get("rubric", "none")
    if kind == "json_format":
        return 1.0 if actual["text"].strip().startswith("{") else 0.0
    if kind == "no_tool":
        return 0.0 if actual["tools"] else 1.0
    if kind == "single_tool":
        return 0.5 if len(actual["tools"]) > 1 else 1.0
    return 1.0


def classify_tool_failure(
    actual: list[dict[str, Any]],
    expected: list[dict[str, Any]],
    tool_intent: str,
) -> str | None:
    """Attribute a tool-trajectory mismatch to a specific failure category.

    Classification uses only the structured trajectory (expected vs actual tool
    calls) plus the case's declared ``tool_intent`` — never the case id:

        * ``knowledge_recall_insufficient`` — the case relies on the
          authoritative search tool but the agent did not call it.
        * ``spurious_tool_call`` — the agent issued every expected call *and*
          extra ones (over-calling), including calling tools when none were
          expected.
        * ``parameter_error`` — the leading tool name matches but arguments
          differ.
        * ``tool_call_error`` — a wrong or missing tool otherwise.

    Args:
        actual: Actual tool-call list.
        expected: Expected tool-call list.
        tool_intent: Attribution hint from ``case_meta.json``.

    Returns:
        A failure category, or ``None`` when trajectories match.
    """
    a, e = normalize_tools(actual), normalize_tools(expected)
    if a == e:
        return None
    a_names = {tool["name"] for tool in a}
    e_names = {tool["name"] for tool in e}
    if tool_intent == "authoritative_search" and AUTHORITATIVE_SEARCH_TOOL not in a_names:
        return "knowledge_recall_insufficient"
    if a and all(tool in a for tool in e) and len(a) > len(e):
        return "spurious_tool_call"
    if a and e and a[0]["name"] == e[0]["name"]:
        return "parameter_error"
    return "tool_call_error"


def classify_rubric_failure(meta: dict[str, Any]) -> str:
    """Map a failed rubric dimension to its attribution category."""
    if meta.get("rubric") == "json_format":
        return "format_error"
    return "llm_rubric_not_met"


def failure_types_for(
    meta: dict[str, Any],
    final_score: float,
    tool_score: float,
    rubric: float,
    actual: dict[str, Any],
    expected: list[dict[str, Any]],
) -> list[str]:
    """Collect all failure categories for a case from its sub-scores.

    Args:
        meta: Case metadata (``rubric`` / ``tool_intent``).
        final_score: Final-response sub-score.
        tool_score: Tool-trajectory sub-score.
        rubric: Rubric sub-score.
        actual: Agent output.
        expected: Expected tool calls.

    Returns:
        An ordered, de-duplicated list of failure category labels.
    """
    failures: list[str] = []
    if final_score < 1.0:
        failures.append("final_response_mismatch")
    if tool_score < 1.0:
        label = classify_tool_failure(actual["tools"], expected, meta.get("tool_intent", "none"))
        if label:
            failures.append(label)
    if rubric < 1.0:
        failures.append(classify_rubric_failure(meta))
    return failures


def score_case(
    case: dict[str, Any],
    prompt: str,
    cfg: dict[str, Any],
    case_meta: dict[str, Any],
) -> CaseResult:
    """Evaluate one case against a prompt and return its scored result.

    Args:
        case: An ``eval_cases`` entry from the evalset.
        prompt: The system prompt under test.
        cfg: The optimizer config (metric weights, gate thresholds).
        case_meta: Mapping of ``eval_id`` to per-case metadata.

    Returns:
        A :class:`CaseResult` with metrics, attribution and trace.
    """
    invocation = case["conversation"][0]
    query = invocation_text(invocation, "user_content")
    expected_text = invocation_text(invocation, "final_response")
    expected = expected_tools(invocation)
    meta = case_meta.get(case["eval_id"], {})
    actual = fake_agent(prompt, query)

    final_score = 1.0 if expected_text.lower() in actual["text"].lower() else 0.0
    tool_score = 1.0 if normalize_tools(actual["tools"]) == normalize_tools(expected) else 0.0
    rubric = rubric_score(meta, actual)
    weights = {m["name"]: m["weight"] for m in cfg["evaluate"]["metrics"]}
    score = round(
        final_score * weights["final_response"]
        + tool_score * weights["tool_trajectory"]
        + rubric * weights["rubric"],
        4,
    )
    passed = score >= 0.8
    failure_types = failure_types_for(meta, final_score, tool_score, rubric, actual, expected)
    hard_fail = score < cfg["gate"]["hard_fail_threshold"]
    return CaseResult(
        case_id=case["eval_id"],
        score=score,
        passed=passed,
        hard_fail=hard_fail,
        key=bool(meta.get("key", False)),
        metrics={
            "final_response": final_score,
            "tool_trajectory": tool_score,
            "rubric": rubric,
        },
        failure_types=failure_types,
        reason="pass" if passed else "; ".join(failure_types),
        trace={
            "query": query,
            "expected_text": expected_text,
            "actual_text": actual["text"],
            "expected_tools": expected,
            "actual_tools": actual["tools"],
        },
    )


def evaluate_evalset(
    evalset: dict[str, Any],
    prompt: str,
    cfg: dict[str, Any],
    case_meta: dict[str, Any],
) -> dict[str, Any]:
    """Score every case in an evalset and aggregate mean score and pass rate."""
    cases = [score_case(case, prompt, cfg, case_meta) for case in evalset["eval_cases"]]
    mean = round(sum(case.score for case in cases) / len(cases), 4)
    return {
        "eval_set_id": evalset["eval_set_id"],
        "mean_score": mean,
        "pass_rate": round(sum(case.passed for case in cases) / len(cases), 4),
        "cases": {case.case_id: case.__dict__ for case in cases},
    }


def attribute_failures(*results: dict[str, Any]) -> dict[str, Any]:
    """Cluster baseline failures into category counts and a per-case breakdown.

    Args:
        *results: One or more evaluated evalsets (baseline train / val).

    Returns:
        A dict with ``counts`` (category -> frequency) and ``by_case``
        (case id -> failure categories), covering only failing cases.
    """
    counts: Counter[str] = Counter()
    by_case: dict[str, list[str]] = {}
    for result in results:
        for case_id, case in result["cases"].items():
            if case["passed"]:
                continue
            failure_types = case["failure_types"] or ["unknown"]
            by_case[case_id] = failure_types
            counts.update(failure_types)
    return {"counts": dict(counts), "by_case": by_case}


def optimize_prompt(baseline: str, cfg: dict[str, Any], run_dir: Path) -> tuple[str, dict[str, Any]]:
    """Produce a candidate prompt for the current (fake/trace) mode.

    In fake/trace mode this applies the deterministic ``candidate_patch`` from
    the config instead of invoking :class:`AgentOptimizer`, so the example stays
    reproducible without an API key. The returned status distinguishes optimizer
    *availability* from *invocation* to avoid implying a real search happened.

    Args:
        baseline: The baseline prompt text.
        cfg: The optimizer config.
        run_dir: Directory for candidate prompt snapshots.

    Returns:
        A tuple of ``(candidate_text, status_dict)``.
    """
    candidate = baseline.rstrip() + "\n" + "\n".join(cfg["optimize"]["candidate_patch"]) + "\n"
    candidate_path = run_dir / "candidate_prompt.md"
    candidate_path.write_text(candidate, encoding="utf-8")
    return candidate, {
        "status": "SCRIPTED_CANDIDATE",
        "algorithm": cfg["optimize"]["algorithm"],
        "agent_optimizer_available": AgentOptimizer is not None and TargetPrompt is not None,
        "agent_optimizer_invoked": False,
        "note": "fake/trace mode applies a deterministic patch; see examples/optimization/quickstart for a live GEPA run.",
        "candidate_prompt_path": candidate_path.relative_to(HERE).as_posix(),
        "cost_usd": 0.0,
        "tokens": 0,
    }


def diff_cases(baseline: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    """Compute per-case deltas (new_pass / new_fail / score_up / score_down / same)."""
    delta = {}
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
    """Run the validation-first acceptance gate and return its decision.

    Five independent, configurable checks must all pass to ACCEPT:

        1. validation mean-score gain meets ``min_val_score_gain``;
        2. no new hard failure appears on validation;
        3. no *key* validation case regresses (new_fail / score_down);
        4. not overfitting (train up while validation down);
        5. optimization cost stays within ``max_cost_usd``.

    Args:
        baseline_train: Baseline train evaluation.
        candidate_train: Candidate train evaluation.
        baseline_val: Baseline validation evaluation.
        candidate_val: Candidate validation evaluation.
        val_delta: Per-case validation deltas from :func:`diff_cases`.
        cfg: Optimizer config (the ``gate`` block).
        cost_usd: Optimization cost in USD.

    Returns:
        A decision dict with ``accepted`` / ``decision`` / ``reason`` and the
        per-check breakdown.
    """
    gate = cfg["gate"]
    train_gain = round(candidate_train["mean_score"] - baseline_train["mean_score"], 4)
    val_gain = round(candidate_val["mean_score"] - baseline_val["mean_score"], 4)
    new_hard_fails = [
        case_id
        for case_id, case in candidate_val["cases"].items()
        if case["hard_fail"] and not baseline_val["cases"][case_id]["hard_fail"]
    ]
    # A "critical" regression is one on a case explicitly marked key=true in
    # case_meta.json, not merely any validation case.
    critical_regressions = [
        case_id
        for case_id, diff in val_delta.items()
        if candidate_val["cases"][case_id]["key"] and diff["kind"] in {"new_fail", "score_down"}
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
            "detail": f"cost_usd={cost_usd:.4f}, budget={gate['max_cost_usd']:.4f}",
        },
    ]
    accepted = all(check["passed"] for check in checks)
    return {
        "accepted": accepted,
        "decision": "ACCEPT" if accepted else "REJECT",
        "reason": "all gates passed" if accepted else "; ".join(check["name"] for check in checks if not check["passed"]),
        "train_gain": train_gain,
        "val_gain": val_gain,
        "checks": checks,
    }


def narrate_report(report: dict[str, Any]) -> str:
    """Render a data-driven, plain-language Chinese summary of the run.

    The narrative is derived entirely from the gate decision, validation deltas
    and failure attribution, so it stays correct for any input (unlike a static
    paragraph). It is deterministic and needs no model, keeping the no-key path
    reproducible.

    Args:
        report: The fully assembled report dict.

    Returns:
        A short multi-sentence Chinese summary.
    """
    gate = report["gate"]
    verb = "接受" if gate["decision"] == "ACCEPT" else "拒绝"
    parts = [
        f"本次（{report['run']['mode']} 模式）决定**{verb}**候选 prompt。"
        f"训练集均分 {report['baseline']['train']['mean_score']}→{report['candidate']['train']['mean_score']}"
        f"（{gate['train_gain']:+.4f}），验证集 {report['baseline']['val']['mean_score']}→"
        f"{report['candidate']['val']['mean_score']}（{gate['val_gain']:+.4f}）。"
    ]
    if gate["train_gain"] > 0 and gate["val_gain"] < 0:
        parts.append("训练涨但验证跌，呈现过拟合特征。")

    new_pass = [cid for cid, d in report["delta"]["val"].items() if d["kind"] == "new_pass"]
    new_fail = [cid for cid, d in report["delta"]["val"].items() if d["kind"] == "new_fail"]
    if new_pass:
        parts.append(f"验证集新增通过：{'、'.join(new_pass)}。")
    if new_fail:
        parts.append(f"⚠️ 验证集新增失败：{'、'.join(new_fail)}。")

    failed_checks = [c["name"] for c in gate["checks"] if not c["passed"]]
    if gate["decision"] == "REJECT" and failed_checks:
        parts.append("被以下 gate 拦截：" + "、".join(failed_checks) + "。")
    elif gate["decision"] == "ACCEPT":
        parts.append("五项 gate 全部通过：验证集提升达标、无过拟合、关键 case 未退化、无新增 hard fail、成本在预算内。")

    counts = report["failure_attribution"]["counts"]
    if counts:
        top = "、".join(f"{k}×{v}" for k, v in counts.items())
        parts.append(f"baseline 失败归因：{top}。")
    return "".join(parts)


def build_report(
    cfg: dict[str, Any],
    baseline_prompt: str,
    candidate_prompt: str,
    artifacts: dict[str, Any],
    sdk_smoke: dict[str, Any],
) -> dict[str, Any]:
    """Assemble the full audit report from run metadata and computed artifacts."""
    return {
        "run": {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "mode": cfg["mode"],
            "seed": cfg["seed"],
            "sdk_bridge": {
                "evalset_validated_with_trpc_sdk": EvalSet is not None,
                "agent_evaluator_available": AgentEvaluator is not None,
                "agent_optimizer_available": AgentOptimizer is not None,
                "agent_evaluator_trace_smoke": sdk_smoke,
            },
            "repro": {
                "train_evalset": "train.evalset.json",
                "val_evalset": "val.evalset.json",
                "case_meta": "case_meta.json",
                "optimizer_config": "optimizer.json",
                "prompt_source": cfg["target_prompt"]["path"],
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


def write_markdown(report: dict[str, Any], path: Path) -> None:
    """Write the human-readable Markdown report, including the data-driven summary."""
    gate = report["gate"]
    lines = [
        "# Optimization Report",
        "",
        "## 人话总结",
        "",
        narrate_report(report),
        "",
        f"- Mode: `{report['run']['mode']}`",
        f"- Decision: **{gate['decision']}**",
        f"- Reason: {gate['reason']}",
        f"- Baseline train score: {report['baseline']['train']['mean_score']}",
        f"- Candidate train score: {report['candidate']['train']['mean_score']}",
        f"- Baseline val score: {report['baseline']['val']['mean_score']}",
        f"- Candidate val score: {report['candidate']['val']['mean_score']}",
        f"- Train gain: {gate['train_gain']:+.4f}",
        f"- Val gain: {gate['val_gain']:+.4f}",
        "",
        "## Failure Attribution",
        "",
    ]
    for name, count in report["failure_attribution"]["counts"].items():
        lines.append(f"- {name}: {count}")
    lines.extend(["", "## Validation Delta", ""])
    for case_id, diff in report["delta"]["val"].items():
        lines.append(f"- `{case_id}`: {diff['kind']} ({diff['baseline_score']} -> {diff['candidate_score']}, delta {diff['delta']:+.4f})")
    lines.extend(["", "## Gate Checks", ""])
    for check in gate["checks"]:
        mark = "PASS" if check["passed"] else "FAIL"
        lines.append(f"- {mark} `{check['name']}`: {check['detail']}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    """Run the full evaluation + optimization loop and persist the audit report."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--train", default="train.evalset.json")
    parser.add_argument("--val", default="val.evalset.json")
    parser.add_argument("--optimizer", default="optimizer.json")
    parser.add_argument("--prompt", default="prompts/baseline_system.md")
    args = parser.parse_args()

    logging.basicConfig(
        level=os.environ.get("YUN_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    )

    start = time.perf_counter()
    cfg = load_json(HERE / args.optimizer)
    train = validate_evalset(HERE / args.train)
    val = validate_evalset(HERE / args.val)
    case_meta = {k: v for k, v in load_json(HERE / cfg.get("case_meta", "case_meta.json")).items() if not k.startswith("_")}
    logger.info("加载完成 mode=%s seed=%s train_cases=%d val_cases=%d",
                cfg["mode"], cfg["seed"], len(train["eval_cases"]), len(val["eval_cases"]))

    sdk_smoke = sdk_trace_smoke(HERE / args.train)
    logger.info("SDK trace 冒烟: %s", sdk_smoke["status"])
    baseline_prompt = (HERE / args.prompt).read_text(encoding="utf-8")

    run_dir = HERE / "runs" / "latest"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "baseline_prompt.md").write_text(baseline_prompt, encoding="utf-8")

    # Phase 1 — baseline evaluation.
    baseline_train = evaluate_evalset(train, baseline_prompt, cfg, case_meta)
    baseline_val = evaluate_evalset(val, baseline_prompt, cfg, case_meta)
    logger.info("baseline 均分 train=%.4f val=%.4f", baseline_train["mean_score"], baseline_val["mean_score"])

    # Phase 2 — failure attribution over baseline failures only.
    failures = attribute_failures(baseline_train, baseline_val)
    logger.info("baseline 失败归因: %s", failures["counts"])

    # Phase 3 — optimization (scripted candidate in fake/trace mode).
    candidate_prompt, opt_status = optimize_prompt(baseline_prompt, cfg, run_dir)
    logger.info("优化器: status=%s invoked=%s", opt_status["status"], opt_status["agent_optimizer_invoked"])

    # Phase 4 — candidate validation + diff.
    candidate_train = evaluate_evalset(train, candidate_prompt, cfg, case_meta)
    candidate_val = evaluate_evalset(val, candidate_prompt, cfg, case_meta)
    logger.info("candidate 均分 train=%.4f val=%.4f", candidate_train["mean_score"], candidate_val["mean_score"])
    train_delta = diff_cases(baseline_train, candidate_train)
    val_delta = diff_cases(baseline_val, candidate_val)

    # Phase 5 — acceptance gate.
    gate = gate_decision(baseline_train, candidate_train, baseline_val, candidate_val,
                         val_delta, cfg, opt_status["cost_usd"])
    for check in gate["checks"]:
        logger.info("gate %-30s %s | %s", check["name"], "PASS" if check["passed"] else "FAIL", check["detail"])
    logger.info("gate 决策: %s (%s)", gate["decision"], gate["reason"])
    duration = round(time.perf_counter() - start, 4)

    # Phase 6 — audit persistence.
    report = build_report(
        cfg,
        baseline_prompt,
        candidate_prompt,
        {
            "baseline": {"train": baseline_train, "val": baseline_val},
            "candidate": {"train": candidate_train, "val": candidate_val},
            "delta": {"train": train_delta, "val": val_delta},
            "failure_attribution": failures,
            "optimizer": opt_status,
            "gate": gate,
            "audit": {
                "duration_seconds": duration,
                "cost_usd": opt_status["cost_usd"],
                "tokens": opt_status["tokens"],
                "config_snapshot": cfg,
            },
        },
        sdk_smoke,
    )
    write_json(HERE / "optimization_report.json", report)
    write_markdown(report, HERE / "optimization_report.md")
    logger.info("已写出 optimization_report.json / .md，用时 %.4fs", duration)
    print(f"{gate['decision']}: {gate['reason']}")
    print("wrote optimization_report.json and optimization_report.md")


if __name__ == "__main__":
    main()
