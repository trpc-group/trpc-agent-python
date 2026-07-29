# -*- coding: utf-8 -*-
# Copyright @ 2025 Tencent.com
"""Stage 6: Report generation — produce optimization_report.json and .md."""

from __future__ import annotations

import json
import hashlib
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import (
    BaselineResult,
    DeltaReport,
    FailureReport,
    GateDecision,
    GateConfig,
    PipelineConfig,
    PipelineContext,
)


def _serialize_rubric_score(item: Any) -> dict[str, Any]:
    """Convert SDK/dict rubric scores into stable report JSON."""
    if isinstance(item, dict):
        return dict(item)
    if hasattr(item, "model_dump"):
        return item.model_dump()
    return {
        "id": getattr(item, "id", ""),
        "reason": getattr(item, "reason", ""),
        "score": getattr(item, "score", 0.0),
    }


def _metric_details(case: Any) -> dict[str, Any]:
    """Preserve judge evidence needed to audit failure attribution."""
    return {
        metric.metric_name: {
            "score": metric.score,
            "threshold": metric.threshold,
            "status": metric.eval_status,
            "reason": metric.reason,
            "rubric_scores": [
                _serialize_rubric_score(item)
                for item in metric.rubric_scores
            ],
        }
        for metric in case.metrics.values()
    }


def _build_report_dict(ctx: PipelineContext) -> dict[str, Any]:
    """Assemble the full pipeline report as a JSON-serialisable dict."""
    try:
        pipeline_config = PipelineConfig.from_file(ctx.pipeline_config_path)
        config_snapshot = {
            "mode": "trace" if ctx.is_trace_mode else "live",
            "seed": pipeline_config.seed,
            "max_rounds": pipeline_config.max_rounds,
            "baseline_prompt_preset": (
                pipeline_config.baseline_prompt_preset
            ),
            "failure_case_density": pipeline_config.failure_case_density,
            "gate": asdict(pipeline_config.gate),
        }
    except Exception:
        config_snapshot = {
            "mode": "trace" if ctx.is_trace_mode else "live",
            "seed": 42,
        }

    report: dict[str, Any] = {
        "schema_version": "v1",
        "pipeline_name": "eval_optimize_loop",
        "run_timestamp": datetime.now().isoformat(),
        "pipeline_config": config_snapshot,
    }

    # ---- Baseline ----
    baseline_section: dict[str, Any] = {}
    for label, br in [("train", ctx.baseline_train), ("val", ctx.baseline_val)]:
        if br is None:
            continue
        baseline_section[label] = {
            "pass_rate": br.pass_rate,
            "metric_breakdown": br.metric_breakdown,
            "per_case": [
                {
                    "eval_id": c.case_id,
                    "status": c.overall_status,
                    "metric_scores": {
                        m.metric_name: m.score for m in c.metrics.values()
                    },
                    "metric_details": _metric_details(c),
                    "expected_response": c.expected_response,
                    "actual_response": c.actual_response,
                    "expected_tool_calls": c.expected_tool_calls,
                    "actual_tool_calls": c.actual_tool_calls,
                    "failure_types": [],
                    "failure_explanation": "",
                }
                for c in br.per_case
            ],
        }
    report["baseline"] = baseline_section

    # ---- Failure Attribution ----
    if ctx.failure_report:
        fr = ctx.failure_report
        report["failure_attribution"] = {
            "total_failures": fr.total_failures,
            "by_type": fr.by_type,
            "per_case": [
                {
                    "eval_id": f.case_id,
                    "failure_types": f.failure_types,
                    "explanation": f.explanation,
                    "metric_scores": f.metric_scores,
                }
                for f in fr.per_case
            ],
        }

    # ---- Optimization ----
    if ctx.optimize_result is not None:
        opt = ctx.optimize_result
        rounds = getattr(opt, "rounds", None) or []
        rounds_data = []
        for r in rounds:
            rounds_data.append({
                "round": getattr(r, "round", 0),
                "accepted": getattr(r, "accepted", False),
                "acceptance_reason": getattr(r, "acceptance_reason", ""),
                "validation_pass_rate": getattr(r, "validation_pass_rate", 0.0),
                "metric_breakdown": getattr(r, "metric_breakdown", {}),
                "candidate_system_prompt": (
                    getattr(r, "candidate_prompts", {}) or {}
                ).get("system_prompt", ""),
                "candidate_skill": (
                    getattr(r, "candidate_prompts", {}) or {}
                ).get("skill", ""),
            })

        # The analysis-driven extension produces one candidate directly rather
        # than SDK RoundResult objects.  Materialize that real round from the
        # completed validation/Gate state so total_rounds, accepted_rounds and
        # rounds cannot contradict each other in the audit report.
        total_rounds = getattr(opt, "total_rounds", 0)
        if not rounds_data and total_rounds and ctx.candidate_val is not None:
            candidate_prompts = getattr(opt, "best_prompts", {}) or {}
            accepted = bool(
                ctx.gate_decision and ctx.gate_decision.accepted
            )
            rounds_data.append({
                "round": 1,
                "accepted": accepted,
                "acceptance_reason": (
                    ctx.gate_decision.reason
                    if ctx.gate_decision
                    else "Gate decision unavailable"
                ),
                "validation_pass_rate": ctx.candidate_val.pass_rate,
                "metric_breakdown": ctx.candidate_val.metric_breakdown,
                "candidate_system_prompt": candidate_prompts.get(
                    "system_prompt", ""
                ),
                "candidate_skill": candidate_prompts.get("skill", ""),
            })

        report["optimization"] = {
            "algorithm": getattr(opt, "algorithm", "unknown"),
            "status": getattr(opt, "status", "UNKNOWN"),
            "data_scope": {
                "candidate_generation": [
                    "baseline_train",
                    "train_failure_attribution",
                    "train_expected_tool_patterns",
                ],
                "holdout_only": [
                    "baseline_val",
                    "validation_failure_attribution",
                    "candidate_val",
                ],
            },
            "total_rounds": total_rounds,
            "accepted_rounds": sum(
                1 for r in rounds_data if r["accepted"]
            ),
            "baseline_pass_rate": getattr(opt, "baseline_pass_rate", 0.0),
            "best_pass_rate": getattr(opt, "best_pass_rate", 0.0),
            "pass_rate_improvement": getattr(opt, "pass_rate_improvement", 0.0),
            "stop_reason": getattr(opt, "stop_reason", ""),
            "rounds": rounds_data,
        }

    # ---- Candidate Validation (Delta: val + train) ----
    cv_section: dict[str, Any] = {}

    # Helper to build per-case delta detail
    def _build_per_case_delta(dr: DeltaReport, baseline: BaselineResult | None,
                              candidate: BaselineResult | None) -> list[dict]:
        candidate_detail_map: dict[str, dict] = {}
        if candidate:
            for c in candidate.per_case:
                candidate_detail_map[c.case_id] = {
                    "candidate_response": c.actual_response,
                    "candidate_tools": c.actual_tool_calls,
                    "candidate_metric_scores": {
                        m.metric_name: m.score for m in c.metrics.values()
                    },
                }
        baseline_detail_map: dict[str, dict] = {}
        if baseline:
            for c in baseline.per_case:
                baseline_detail_map[c.case_id] = {
                    "expected_response": c.expected_response,
                    "expected_tools": c.expected_tool_calls,
                    "baseline_response": c.actual_response,
                    "baseline_tools": c.actual_tool_calls,
                }
        per_case_delta = []
        for d in dr.per_case:
            entry = {
                "eval_id": d.case_id,
                "baseline_status": d.baseline_status,
                "candidate_status": d.candidate_status,
                "change_type": d.change_type,
                "baseline_scores": d.baseline_scores,
                "candidate_scores": d.candidate_scores,
                "delta_scores": d.delta_scores,
            }
            bd = baseline_detail_map.get(d.case_id, {})
            cd = candidate_detail_map.get(d.case_id, {})
            entry["expected_tools"] = bd.get("expected_tools", [])
            entry["expected_response"] = bd.get("expected_response", "")
            entry["baseline_tools"] = bd.get("baseline_tools", [])
            entry["baseline_response"] = bd.get("baseline_response", "")
            entry["candidate_tools"] = cd.get("candidate_tools", [])
            entry["candidate_response"] = cd.get("candidate_response", "")
            if d.change_type == "newly_failing":
                exp_tools = {t.get("name", "") for t in entry["expected_tools"]}
                cand_tools = {t.get("name", "") for t in entry["candidate_tools"]}
                if exp_tools and exp_tools.issubset(cand_tools) and len(cand_tools) > len(exp_tools):
                    extra = sorted(cand_tools - exp_tools)
                    entry["failure_type"] = "overgeneralization"
                    entry["failure_reason"] = (
                        f"过度泛化：Prompt 导致 Agent 在仅需 {sorted(exp_tools)} 的场景下"
                        f"额外调用了 {extra}。模型过度学习了训练集的模式，"
                        f"在不需要时也补充了额外信息。"
                    )
                else:
                    entry["failure_type"] = "regression"
                    entry["failure_reason"] = f"回归：{d.case_id} 从 PASS 变为 FAIL"
            elif d.change_type == "newly_passing":
                entry["failure_type"] = ""
                entry["failure_reason"] = "优化有效：候选 Prompt 修复了信息遗漏问题"
            elif d.change_type == "degraded":
                entry["failure_type"] = "degradation"
                entry["failure_reason"] = "分数退化：虽然仍通过，但指标分数下降，存在过拟合风险"
            else:
                entry["failure_type"] = ""
                entry["failure_reason"] = ""
            per_case_delta.append(entry)
        return per_case_delta

    if ctx.delta_report:
        dr = ctx.delta_report
        cv_section["val"] = {
            "baseline_pass_rate": dr.baseline_pass_rate,
            "candidate_pass_rate": dr.candidate_pass_rate,
            "delta": dr.delta,
            "per_case": _build_per_case_delta(dr, ctx.baseline_val, ctx.candidate_val),
        }

    if ctx.delta_report_train:
        dtr = ctx.delta_report_train
        cv_section["train"] = {
            "baseline_pass_rate": dtr.baseline_pass_rate,
            "candidate_pass_rate": dtr.candidate_pass_rate,
            "delta": dtr.delta,
            "per_case": _build_per_case_delta(dtr, ctx.baseline_train, ctx.candidate_train),
        }

    # Overfitting summary (if we have both deltas)
    if ctx.delta_report and ctx.delta_report_train:
        val_delta = ctx.delta_report.delta
        train_delta = ctx.delta_report_train.delta
        cv_section["overfit_summary"] = {
            "train_delta": train_delta,
            "val_delta": val_delta,
            "train_val_gap_at_candidate": (
                ctx.delta_report_train.candidate_pass_rate
                - ctx.delta_report.candidate_pass_rate
            ),
            "train_val_gap_at_baseline": (
                ctx.delta_report_train.baseline_pass_rate
                - ctx.delta_report.baseline_pass_rate
            ),
            "is_overfit": (
                train_delta >= 0.01 and val_delta <= -0.001
            ),
            "diagnosis": (
                "过拟合：训练集提升 + 验证集退化，候选 prompt 泛化能力下降"
                if (train_delta >= 0.01 and val_delta <= -0.001)
                else (
                    "潜在过拟合趋势：训练集提升但验证集未同步提升"
                    if (train_delta > 0 >= val_delta)
                    else (
                        "正常：两集同步提升或均无显著变化"
                    )
                )
            ),
        }

    if cv_section:
        report["candidate_validation"] = cv_section

    # ---- Gate Decision ----
    if ctx.gate_decision:
        gd = ctx.gate_decision
        report["gate_decision"] = {
            "accepted": gd.accepted,
            "reason": gd.reason,
            "checks": gd.checks,
            "warnings": gd.warnings,
        }

    # ---- Prompts ----
    if ctx.optimize_result is not None:
        baseline_sys = getattr(ctx, "_baseline_system_snapshot", "")
        baseline_sk = getattr(ctx, "_baseline_skill_snapshot", "")
        if not baseline_sys:
            baseline_sys = ctx.optimize_result.baseline_prompts.get("system_prompt", "")
            baseline_sk = ctx.optimize_result.baseline_prompts.get("skill", "")
        candidate_prompts = getattr(
            ctx.optimize_result, "best_prompts", {}
        ) or {}
        report["prompts"] = {
            "baseline": {"system_prompt": baseline_sys, "skill": baseline_sk},
            "best_candidate": {
                "system_prompt": candidate_prompts.get(
                    "system_prompt", baseline_sys
                ),
                "skill": candidate_prompts.get("skill", baseline_sk),
            },
        }

    # ---- Audit ----
    audit_data: dict[str, Any] = {
        "duration_seconds": sum(ctx.stage_timings.values()),
        "stage_timings": ctx.stage_timings,
        "is_trace_mode": ctx.is_trace_mode,
        "cost": {
            "total": 0.0,
            "baseline": 0.0,
            "optimization": 0.0,
            "candidate": 0.0,
            "scope": "optimizer_only",
            "note": (
                "AgentEvaluator/LLM judge provider billing is not exposed "
                "by the current SDK; consult the provider dashboard."
            ),
        },
        "config_snapshot": report.get("pipeline_config", {}),
    }
    if ctx.optimize_result is not None:
        audit_data["cost"]["optimization"] = getattr(
            ctx.optimize_result, "total_llm_cost", 0.0
        )
        audit_data["cost"]["total"] = audit_data["cost"]["optimization"]
        audit_data["total_tokens"] = getattr(
            ctx.optimize_result, "total_token_usage", {}
        )
        audit_data["reflection_lm_calls"] = getattr(
            ctx.optimize_result, "total_reflection_lm_calls", 0
        )
    try:
        config_bytes = Path(ctx.pipeline_config_path).read_bytes()
        baseline_prompt = report.get("prompts", {}).get("baseline", {})
        candidate_prompt = report.get("prompts", {}).get(
            "best_candidate", {}
        )

        def _prompt_hash(prompt_data: dict) -> str:
            content = json.dumps(
                prompt_data,
                ensure_ascii=False,
                sort_keys=True,
            ).encode("utf-8")
            return hashlib.sha256(content).hexdigest()

        audit_data["reproducibility"] = {
            "config_sha256": hashlib.sha256(config_bytes).hexdigest(),
            "baseline_prompt_sha256": _prompt_hash(baseline_prompt),
            "candidate_prompt_sha256": _prompt_hash(candidate_prompt),
        }
    except Exception:
        pass
    report["audit"] = audit_data

    return report


def _build_markdown(report: dict[str, Any]) -> str:
    """Render the pipeline report as human-readable Markdown."""
    lines: list[str] = []

    def h(level: int, text: str) -> None:
        lines.append(f"{'#' * level} {text}")
        lines.append("")

    def table(headers: list[str], rows: list[list[str]]) -> None:
        lines.append("| " + " | ".join(headers) + " |")
        lines.append("|" + "|".join(["---"] * len(headers)) + "|")
        for row in rows:
            lines.append("| " + " | ".join(str(c) for c in row) + " |")
        lines.append("")

    h(1, "Pipeline Report — eval_optimize_loop")
    lines.append(f"**Run timestamp**: {report.get('run_timestamp', 'N/A')}")
    lines.append("")

    # ---- Overview ----
    h(2, "1. Overview")
    lines.append("")
    lines.append("**Pipeline Stages**: Baseline Evaluation → Failure Attribution → "
                 "Optimization → Candidate Validation → Gate Decision → Report")
    lines.append("")
    gate = report.get("gate_decision", {})
    opt = report.get("optimization", {})
    rows = [
        ["Mode", report.get("pipeline_config", {}).get("mode", "N/A")],
        ["Algorithm", opt.get("algorithm", "N/A")],
        ["Optimization Status", opt.get("status", "N/A")],
        ["Gate Decision", "ACCEPTED" if gate.get("accepted") else "REJECTED"],
        ["Gate Reason", gate.get("reason", "N/A")],
    ]
    table(["Field", "Value"], rows)

    # ---- Baseline ----
    h(2, "2. Baseline Evaluation")
    baseline = report.get("baseline", {})
    for label in ["train", "val"]:
        bl = baseline.get(label, {})
        if not bl:
            continue
        h(3, f"2.{['a','b'][['train','val'].index(label)]}. {label.title()} Set")
        rows = [["Pass Rate", f"{bl.get('pass_rate', 0):.2%}"]]
        for m, s in bl.get("metric_breakdown", {}).items():
            rows.append([f"  {m}", f"{s:.4f}"])
        table(["Metric", "Value"], rows)

        case_rows = []
        for c in bl.get("per_case", []):
            scores = ", ".join(
                f"{k}: {v:.2f}" for k, v in c.get("metric_scores", {}).items()
            )
            case_rows.append([c["eval_id"], c["status"], scores])
        table(["Case ID", "Status", "Scores"], case_rows)

        h(3, f"Detail — {label.title()} Set")
        for c in bl.get("per_case", []):
            status = c.get("status", "?")
            icon = "[OK]" if status == "PASSED" else "[FAIL]"
            lines.append(f"**{icon} {c['eval_id']}** ({status})")
            lines.append(f"- Expected response: `{c.get('expected_response', 'N/A')}`")
            lines.append(f"- Actual response:   `{c.get('actual_response', 'N/A')}`")
            lines.append(f"- Expected tools: {c.get('expected_tool_calls', [])}")
            lines.append(f"- Actual tools:   {c.get('actual_tool_calls', [])}")
            if c.get("failure_types"):
                lines.append(f"- Failure types: {', '.join(c['failure_types'])}")
            if c.get("failure_explanation"):
                lines.append(f"- Explanation: {c['failure_explanation']}")
            scores_detail = c.get("metric_scores", {})
            if scores_detail:
                for m_name, m_score in scores_detail.items():
                    lines.append(f"- Score [{m_name}]: {m_score:.2f}")
            lines.append("")

    # ---- Failure Attribution ----
    fa = report.get("failure_attribution")
    if fa:
        h(2, "3. Failure Attribution")
        rows = [[ft, str(cnt)] for ft, cnt in fa.get("by_type", {}).items()]
        if rows:
            table(["Failure Type", "Count"], rows)
        lines.append(f"**Total failures**: {fa.get('total_failures', 0)}")
        lines.append("")
        for f in fa.get("per_case", []):
            lines.append(f"- **{f['eval_id']}**: {' | '.join(f['failure_types'])}")
            lines.append(f"  - {f['explanation']}")
        lines.append("")

    # ---- Optimization ----
    if opt:
        h(2, "4. Optimization")
        has_rounds = bool(opt.get("rounds", []))
        if has_rounds:
            h(3, "Rounds")
            rows = []
            for r in opt.get("rounds", []):
                rows.append([
                    str(r["round"]),
                    "Yes" if r["accepted"] else "No",
                    f"{r['validation_pass_rate']:.2%}",
                ])
            table(["Round", "Accepted", "Val Pass Rate"], rows)

        lines.append("**Optimization Details**")
        lines.append(f"- Algorithm: {opt.get('algorithm', 'N/A')}")
        lines.append(f"- Status: {opt.get('status', 'N/A')}")
        data_scope = opt.get("data_scope", {})
        if data_scope:
            lines.append(
                "- Candidate-generation data: "
                + ", ".join(data_scope.get("candidate_generation", []))
            )
            lines.append(
                "- Holdout-only data: "
                + ", ".join(data_scope.get("holdout_only", []))
            )
        lines.append(f"- Stop reason: {opt.get('stop_reason', 'N/A')}")
        lines.append(f"- Total rounds: {opt.get('total_rounds', 0)}")
        lines.append(f"- Accepted rounds: {opt.get('accepted_rounds', 0)}")
        lines.append(f"- Baseline → Best pass rate: {opt.get('baseline_pass_rate', 0):.2%} → {opt.get('best_pass_rate', 0):.2%}")
        lines.append(f"- Pass rate improvement: {opt.get('pass_rate_improvement', 0):.2%}")
        lines.append("")

    # ---- Delta (val + train + overfit) ----
    cv = report.get("candidate_validation")
    if cv:
        h(2, "5. Regression Comparison (Val + Train)")

        # Overfit summary first
        overfit = cv.get("overfit_summary")
        if overfit:
            h(3, "5.a Overfit Diagnosis")
            icon = "[OVERFIT]" if overfit.get("is_overfit") else "[OK]"
            rows = [
                ["Train delta", f"{overfit.get('train_delta', 0):+.2%}"],
                ["Val delta",   f"{overfit.get('val_delta', 0):+.2%}"],
                ["Baseline train–val gap", f"{overfit.get('train_val_gap_at_baseline', 0):.2%}"],
                ["Candidate train–val gap", f"{overfit.get('train_val_gap_at_candidate', 0):.2%}"],
                ["Diagnosis", f"{icon} {overfit.get('diagnosis', 'N/A')}"],
            ]
            table(["Metric", "Value"], rows)
            lines.append("")

        for split_label in ("val", "train"):
            split = cv.get(split_label)
            if not split:
                continue
            h(3, f"5.{['b','c'][['val','train'].index(split_label)]}. "
                f"{split_label.title()} Set — Baseline → Candidate")
            rows = [
                ["Baseline pass rate", f"{split.get('baseline_pass_rate', 0):.2%}"],
                ["Candidate pass rate", f"{split.get('candidate_pass_rate', 0):.2%}"],
                ["Delta", f"{split.get('delta', 0):+.2%}"],
            ]
            table(["Metric", "Value"], rows)

            change_rows = []
            for d in split.get("per_case", []):
                change_rows.append([
                    d["eval_id"],
                    d["baseline_status"],
                    d["candidate_status"],
                    d["change_type"],
                ])
            if change_rows:
                table(["Case", "Baseline", "Candidate", "Change"], change_rows)

            h(3, f"Per-Case Trace — {split_label.title()}")
            for d in split.get("per_case", []):
                icon = "[OK]" if d["change_type"] == "newly_passing" else (
                    "[FAIL]" if d["change_type"] == "newly_failing" else (
                        "[WARN]" if d["change_type"] == "degraded" else "[-]"
                    )
                )
                lines.append(f"**{icon} {d['eval_id']}**: {d['baseline_status']} → {d['candidate_status']} ({d['change_type']})")
                lines.append(f"- Expected response: `{d.get('expected_response', 'N/A')}`")
                lines.append(f"- Baseline response: `{d.get('baseline_response', 'N/A')}`")
                lines.append(f"- Candidate response: `{d.get('candidate_response', 'N/A')}`")
                lines.append(f"- Expected tools: {d.get('expected_tools', [])}")
                lines.append(f"- Baseline tools: {d.get('baseline_tools', [])}")
                lines.append(f"- Candidate tools: {d.get('candidate_tools', [])}")
                if d.get("failure_reason"):
                    lines.append(f"- {d['failure_reason']}")
                lines.append("")

    # ---- Gate ----
    if gate:
        h(2, "6. Acceptance Gate")
        h(3, "Decision")
        lines.append(f"- **Accepted**: {gate.get('accepted')}")
        lines.append(f"- **Reason**: {gate.get('reason')}")
        lines.append("")
        h(3, "Checks")
        for check, passed in gate.get("checks", {}).items():
            icon = "[OK]" if passed else "[FAIL]"
            lines.append(f"- {icon} {check}")
        lines.append("")
        if gate.get("warnings"):
            h(3, "Warnings")
            for w in gate["warnings"]:
                lines.append(f"- {w}")
            lines.append("")

    # ---- Audit ----
    audit = report.get("audit", {})
    h(2, "7. Audit Trail")
    lines.append("")
    h(3, "Timing")
    rows = [
        ["Total Duration", f"{audit.get('duration_seconds', 0):.1f}s"],
        ["Trace Mode", str(audit.get("is_trace_mode", False))],
    ]
    for stage, t in audit.get("stage_timings", {}).items():
        rows.append([f"  {stage}", f"{t:.1f}s"])
    table(["Metric", "Value"], rows)

    h(3, "Cost")
    cost = audit.get("cost", {})
    lines.append(f"- Total LLM cost: ${cost.get('total', 0):.4f}")
    lines.append(f"- Baseline eval cost: ${cost.get('baseline', 0):.4f}")
    lines.append(f"- Optimization cost: ${cost.get('optimization', 0):.4f}")
    lines.append(f"- Candidate eval cost: ${cost.get('candidate', 0):.4f}")
    lines.append("")

    h(3, "Config")
    pconfig = report.get("pipeline_config", {})
    lines.append(f"- Mode: {pconfig.get('mode', 'N/A')}")
    lines.append(f"- Seed: {pconfig.get('seed', 'N/A')}")
    lines.append("")

    h(3, "Gate Reason")
    gd = report.get("gate_decision", {})
    lines.append(f"- Decision: {'ACCEPTED' if gd.get('accepted') else 'REJECTED'}")
    lines.append(f"- Reason: {gd.get('reason', 'N/A')}")
    lines.append(f"- Checks: {json.dumps(gd.get('checks', {}))}")
    if gd.get("warnings"):
        lines.append(f"- Warnings: {', '.join(gd['warnings'])}")
    lines.append("")

    # ---- Prompts ----
    prompts = report.get("prompts", {})
    if prompts:
        h(2, "8. Prompts")
        h(3, "Baseline Prompt")
        lines.append("```")
        lines.append(prompts.get("baseline", {}).get("system_prompt", "N/A"))
        lines.append("```")
        lines.append("")
        candidate = prompts.get("best_candidate", {})
        baseline = prompts.get("baseline", {})
        if candidate.get("system_prompt", "") != baseline.get("system_prompt", ""):
            h(3, "Best Candidate Prompt (after optimization)")
            lines.append("```")
            lines.append(candidate.get("system_prompt", "N/A"))
            lines.append("```")
            lines.append("")
        else:
            h(3, "Candidate Prompt")
            lines.append("(unchanged from baseline)")
            lines.append("")

    return "\n".join(lines)


def generate(ctx: PipelineContext) -> tuple[str, str]:
    """Generate optimization_report.json, .md, and per-stage trace files.

    Returns (json_path, md_path).
    """
    out = Path(ctx.output_dir)
    out.mkdir(parents=True, exist_ok=True)

    report = _build_report_dict(ctx)

    # ── Merge failure attribution into baseline per-case ──
    if ctx.failure_report:
        fail_map = {f.case_id: f for f in ctx.failure_report.per_case}
        for label in ("train", "val"):
            bl = report.get("baseline", {}).get(label, {})
            for c in bl.get("per_case", []):
                fr = fail_map.get(c["eval_id"])
                if fr:
                    c["failure_types"] = fr.failure_types
                    c["failure_explanation"] = fr.explanation

    # ── Save per-stage detail files ──
    def _save_json(name: str, data: dict | list) -> str:
        p = out / name
        p.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str),
                     encoding="utf-8")
        return str(p)

    _save_json("baseline_train_detail.json",
               report.get("baseline", {}).get("train", {}))
    _save_json("baseline_val_detail.json",
               report.get("baseline", {}).get("val", {}))
    cv = report.get("candidate_validation", {})
    if cv:
        _save_json("candidate_val_detail.json", cv.get("val", {}))
        if cv.get("train"):
            _save_json("candidate_train_detail.json", cv["train"])
        if cv.get("overfit_summary"):
            _save_json("overfit_summary.json", cv["overfit_summary"])

    # JSON summary
    json_path = out / "optimization_report.json"
    json_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )

    # Markdown
    md_path = out / "optimization_report.md"
    markdown = _build_markdown(report)
    # Model responses may contain Markdown hard-break spaces.  Reports are
    # tracked artifacts, so normalize line endings to keep `git diff --check`
    # clean without altering the JSON source of truth.
    markdown = "\n".join(line.rstrip() for line in markdown.splitlines())
    md_path.write_text(markdown, encoding="utf-8")

    return str(json_path), str(md_path)
