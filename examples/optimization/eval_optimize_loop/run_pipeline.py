#!/usr/bin/env python3
"""Evaluation + Optimization Pipeline — main entry point.

Implements the full closed loop:
  baseline → attribution → optimization → validation → gate → report

With complete audit tracing (seeds, timing, cost, reproducibility).

Usage:
    python run_pipeline.py --mode fake
    python run_pipeline.py --mode fake --max-iterations 3 --verbose
    python run_pipeline.py --mode fake --ci
    python run_pipeline.py --optimizer-config data/optimizer.json
"""

import argparse
import asyncio
import os
import sys
import time
import uuid
from datetime import datetime, timezone

# Ensure imports work from the example directory and the repo root
# （repo root 含 trpc_agent_sdk 源码包，live 模式需要）
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
# eval_optimize_loop → optimization → examples → trpc-agent-python（3 级）
_REPO_ROOT = os.path.abspath(os.path.join(_HERE, os.pardir, os.pardir, os.pardir))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

# 兼容 Windows GBK 控制台：输出统一用 UTF-8，避免 emoji/中文 print 崩溃
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

from pipeline.config import (
    PipelineConfig,
    load_evalset,
    load_optimizer_json,
    load_pipeline_config,
)
from pipeline.baseline import BaselineResult, run_baseline_fake
from pipeline.attribution import attribute_failures, AttributionReport
from pipeline.gate import evaluate_gate, GateDecision, GateResult
from pipeline.validate import run_validation_trace, ValidationResult
from pipeline.report import generate_json_report, generate_md_report
from pipeline.optimize import (
    run_optimize_fake,
    run_optimize_live,
    OptimizeResult,
)
from pipeline.tracing import AuditTracer


def build_reproduce_command(args: argparse.Namespace) -> str:
    """Build a complete reproduce command from CLI args.

    Only non-default args are appended, so a default run stays minimal
    while non-default configurations can be reproduced exactly.
    """
    parts = [f"python run_pipeline.py --mode {args.mode}"]
    if args.seed != 42:
        parts.append(f"--seed {args.seed}")
    if args.scenario != "fix_attributed":
        parts.append(f"--scenario {args.scenario}")
    if args.max_iterations != 3:
        parts.append(f"--max-iterations {args.max_iterations}")
    if args.min_improvement != 0.05:
        parts.append(f"--min-improvement {args.min_improvement}")
    if args.max_cost != 10.0:
        parts.append(f"--max-cost {args.max_cost}")
    if args.output_dir != "sample_output":
        parts.append(f"--output-dir {args.output_dir}")
    if args.train_evalset != "data/train.evalset.json":
        parts.append(f"--train-evalset {args.train_evalset}")
    if args.val_evalset != "data/val.evalset.json":
        parts.append(f"--val-evalset {args.val_evalset}")
    if args.holdout_evalset != "data/holdout.evalset.json":
        parts.append(f"--holdout-evalset {args.holdout_evalset}")
    if args.optimizer_config != "data/optimizer.json":
        parts.append(f"--optimizer-config {args.optimizer_config}")
    if args.val_regression_cases:
        parts.append(f"--val-regression-cases {args.val_regression_cases}")
    if args.critical_cases:
        parts.append(f"--critical-cases {args.critical_cases}")
    if args.verbose:
        parts.append("--verbose")
    if args.ci:
        parts.append("--ci")
    return " ".join(parts)


def ci_exit_code(decision: GateDecision, ci_mode: bool) -> int:
    """Map a gate decision to the process exit code in CI mode.

    0 = accepted (or CI disabled), 1 = rejected, 2 = needs review.
    """
    if not ci_mode:
        return 0
    if decision == GateDecision.REJECT:
        return 1
    if decision == GateDecision.NEEDS_REVIEW:
        return 2
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Evaluation + Optimization Closed-Loop Pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python run_pipeline.py --mode fake
  python run_pipeline.py --mode fake --verbose
  python run_pipeline.py --mode fake --ci
  python run_pipeline.py --mode fake --max-iterations 5
  python run_pipeline.py --output-dir ./results
        """,
    )
    parser.add_argument("--mode", default="fake", choices=["fake", "live"],
                        help="Execution mode (default: fake)")
    parser.add_argument("--train-evalset", default="data/train.evalset.json")
    parser.add_argument("--val-evalset", default="data/val.evalset.json")
    parser.add_argument("--holdout-evalset", default="data/holdout.evalset.json",
                        help="Holdout set (optional, scored in report)")
    parser.add_argument("--optimizer-config", default="data/optimizer.json")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--max-iterations", type=int, default=3,
                        help="Maximum optimization iterations (default: 3)")
    parser.add_argument("--min-improvement", type=float, default=0.05)
    parser.add_argument("--max-cost", type=float, default=10.0)
    parser.add_argument("--output-dir", default="sample_output")
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument("--ci", action="store_true",
                        help="CI mode: exit 1 on gate rejection, 2 on needs-review")
    parser.add_argument("--scenario", default="fix_attributed",
                        choices=["fix_attributed", "noop", "overfit"],
                        help="Candidate generation strategy (default: fix_attributed)")
    parser.add_argument("--val-regression-cases", default="",
                        help="Comma-separated val case ids to regress in overfit scenario")
    parser.add_argument("--critical-cases", default="",
                        help="Comma-separated case ids that must not regress on train or validation")
    args = parser.parse_args()

    # Generate task ID
    task_id = f"opt-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"

    cfg = load_pipeline_config(
        train_evalset=args.train_evalset,
        val_evalset=args.val_evalset,
        optimizer_config=args.optimizer_config,
        seed=args.seed,
        max_iterations=args.max_iterations,
        min_improvement_threshold=args.min_improvement,
        max_cost_budget=args.max_cost,
        output_dir=args.output_dir,
        mode=args.mode,
        verbose=args.verbose,
        ci_mode=args.ci,
        scenario=args.scenario,
        holdout_evalset=args.holdout_evalset,
        val_regression_cases=[x.strip() for x in args.val_regression_cases.split(",") if x.strip()],
        critical_case_ids=[x.strip() for x in args.critical_cases.split(",") if x.strip()],
    )

    # Initialize audit tracer
    tracer = AuditTracer(
        seed=cfg.seed,
        mode=cfg.mode,
        algorithm=cfg.algorithm,
        reproduce_command=build_reproduce_command(args),
    )
    errors: list[str] = []

    # ═══════════════════════════════════════════════════════════════
    # Stage 1: Load Configuration
    # ═══════════════════════════════════════════════════════════════
    print("[1/7] Loading configuration...")
    tracer.start_stage("config")
    try:
        train_data = load_evalset(cfg.train_evalset)
        val_data = load_evalset(cfg.val_evalset)
    except (FileNotFoundError, ValueError) as e:
        print(f"  ❌ Configuration error: {e}")
        tracer.add_error(str(e))
        return 1
    tracer.record_input_file("train_evalset", cfg.train_evalset)
    tracer.record_input_file("val_evalset", cfg.val_evalset)
    tracer.record_input_file("optimizer_config", cfg.optimizer_config)
    tracer.end_stage("config")
    print(f"  Train: {cfg.train_evalset} ({len(train_data.get('eval_cases', []))} cases)")
    print(f"  Val:   {cfg.val_evalset} ({len(val_data.get('eval_cases', []))} cases)")

    # ═══════════════════════════════════════════════════════════════
    # Stage 2: Baseline Evaluation
    # ═══════════════════════════════════════════════════════════════
    print("[2/7] Running baseline evaluation...")
    tracer.start_stage("baseline")
    if cfg.mode == "fake":
        baseline_train = run_baseline_fake(cfg.train_evalset, cfg)
        baseline_val = run_baseline_fake(cfg.val_evalset, cfg)
    else:
        print("  [live] trace-replay baseline（SDK AgentEvaluator，无需 API key）")
        from pipeline.baseline import run_baseline_sdk
        from pipeline.config import load_optimize_config
        from agent.agent import build_call_agent
        _call_agent = build_call_agent()
        try:
            _eval_config = load_optimize_config(cfg.optimizer_config)
        except Exception as _e:
            _eval_config = None
            print(f"  ⚠️  EvalConfig 加载失败（将降级到 trace comparator）: {_e}")
        baseline_train = asyncio.run(
            run_baseline_sdk(cfg.train_evalset, call_agent=_call_agent,
                             eval_config=_eval_config)
        )
        baseline_val = asyncio.run(
            run_baseline_sdk(cfg.val_evalset, call_agent=_call_agent,
                             eval_config=_eval_config)
        )

    if baseline_train.errors:
        for e in baseline_train.errors:
            print(f"  ⚠️  Train: {e}")
            tracer.add_warning(e)
            errors.append(e)
    if baseline_val.errors:
        for e in baseline_val.errors:
            print(f"  ⚠️  Val: {e}")
            tracer.add_warning(e)
            errors.append(e)

    tracer.end_stage("baseline")
    print(f"  Train pass rate: {baseline_train.pass_rate:.1%} "
          f"({baseline_train.passed_cases}/{baseline_train.total_cases})")
    print(f"  Val pass rate:   {baseline_val.pass_rate:.1%} "
          f"({baseline_val.passed_cases}/{baseline_val.total_cases})")

    # Holdout 集评分（可选）：加载 holdout evalset 并用 comparator 评分，
    # 结果写入审计字典供报告展示
    holdout_result = None
    if os.path.exists(cfg.holdout_evalset):
        try:
            holdout_result = run_baseline_fake(cfg.holdout_evalset, cfg)
            print(f"  Holdout pass rate: {holdout_result.pass_rate:.1%} "
                  f"({holdout_result.passed_cases}/{holdout_result.total_cases})")
            tracer.add_cost(0.0, "holdout")
        except Exception as e:
            print(f"  ⚠️  Holdout 评分失败: {e}")
            tracer.add_warning(f"Holdout eval failed: {e}")

    # ═══════════════════════════════════════════════════════════════
    # Stage 3: Failure Attribution
    # ═══════════════════════════════════════════════════════════════
    print("[3/7] Attributing failures...")
    tracer.start_stage("attribution")
    attribution = attribute_failures(
        baseline_train.__dict__ if hasattr(baseline_train, '__dict__') else baseline_train,
        baseline_val.__dict__ if hasattr(baseline_val, '__dict__') else baseline_val,
    )
    tracer.end_stage("attribution")
    print(f"  {attribution.total_failures} failure(s) across {len(attribution.by_category)} categories")
    if cfg.verbose:
        for cat, count in sorted(attribution.by_category.items(), key=lambda x: x[1], reverse=True):
            print(f"    {cat}: {count}")

    # ═══════════════════════════════════════════════════════════════
    # Stage 4: Optimization
    # ═══════════════════════════════════════════════════════════════
    print("[4/7] Running optimization...")
    tracer.start_stage("optimization")
    if cfg.mode == "fake":
        optimize_result = run_optimize_fake(attribution, cfg, scenario=cfg.scenario)
    else:
        print("  [live] AgentOptimizer (GEPA) — 离线确定性 call_agent 或真实 API")
        from agent.agent import build_call_agent
        optimize_result = asyncio.run(
            run_optimize_live(cfg.optimizer_config, cfg, call_agent=build_call_agent())
        )

    if optimize_result.errors:
        for e in optimize_result.errors:
            print(f"  ❌ Optimization error: {e}")
            tracer.add_error(e)
            errors.append(e)

    optimization_cost = optimize_result.total_cost
    optimized_fields = optimize_result.optimized_fields
    tracer.add_cost(optimization_cost, "optimization")
    tracer.end_stage("optimization")
    print(f"  Algorithm: {optimize_result.algorithm}")
    print(f"  Scenario: {cfg.scenario}")
    print(f"  Iterations: {optimize_result.total_iterations}")
    print(f"  Best score: {optimize_result.best_score:.3f}")
    print(f"  Cost: ${optimization_cost:.4f}")
    if cfg.verbose and optimize_result.rounds:
        for r in optimize_result.rounds:
            print(f"    Round {r.round_index}: score={r.score:.3f}, cost=${r.cost:.4f}")

    # ═══════════════════════════════════════════════════════════════
    # Stage 5: Candidate Validation
    # ═══════════════════════════════════════════════════════════════
    print("[5/7] Validating candidate on validation set...")
    tracer.start_stage("validate")

    validation = run_validation_trace(
        cfg.train_evalset,
        cfg.val_evalset,
        baseline_val,
        optimize_result,
        cfg,
        scenario=cfg.scenario,
        val_regression_cases=cfg.val_regression_cases,
    )
    candidate_train = validation.candidate_train or baseline_train
    tracer.end_stage("validate")
    print(f"  New passes: {validation.new_passes}, "
          f"New failures: {validation.new_failures}, "
          f"Unchanged: {validation.unchanged}")
    if validation.is_overfitting:
        print(f"  ⚠️  Overfitting detected!")
        tracer.add_warning("Overfitting detected: candidate regresses on validation set")

    # ═══════════════════════════════════════════════════════════════
    # Stage 6: Gate Decision
    # ═══════════════════════════════════════════════════════════════
    print("[6/7] Evaluating gate...")
    tracer.start_stage("gate")
    gate = evaluate_gate(
        baseline_pass_rate=baseline_train.pass_rate,
        candidate_pass_rate=candidate_train.pass_rate,
        baseline_metrics=baseline_train.metric_breakdown,
        candidate_metrics=candidate_train.metric_breakdown,
        min_improvement=cfg.min_improvement_threshold,
        critical_case_ids=cfg.critical_case_ids,
        baseline_failed=baseline_train.failed_case_ids,
        candidate_failed=candidate_train.failed_case_ids,
        max_cost=cfg.max_cost_budget,
        optimization_cost=optimization_cost,
        validation_new_failures=validation.new_failures,
        validation_new_failed=[d.eval_id for d in validation.deltas if d.change == "new_fail"],
    )
    tracer.end_stage("gate")
    gate_icon = {"accept": "[ACCEPT]", "reject": "[REJECT]", "needs_review": "[REVIEW]"}
    print(f"  {gate_icon.get(gate.decision.value, '[????]')} {gate.decision.value.upper()}: {gate.reason}")

    # ═══════════════════════════════════════════════════════════════
    # Stage 7: Report Generation
    # ═══════════════════════════════════════════════════════════════
    print("[7/7] Generating reports...")
    tracer.start_stage("report")

    improvement = round(candidate_train.pass_rate - baseline_train.pass_rate, 4)
    tracer.set_results(
        baseline_train_pass_rate=baseline_train.pass_rate,
        candidate_train_pass_rate=candidate_train.pass_rate,
        improvement=improvement,
    )

    optimization_info = {
        "algorithm": optimize_result.algorithm,
        "mode": cfg.mode,
        "optimized_fields": optimized_fields,
        "optimization_cost": optimization_cost,
        "total_iterations": optimize_result.total_iterations,
        "converged": optimize_result.converged,
        "best_score": optimize_result.best_score,
    }

    audit_dict = tracer.to_dict()
    # Enrich audit with backward-compatible fields
    audit_dict.update({
        "seed": cfg.seed,
        "mode": cfg.mode,
        "duration_seconds": audit_dict["timing"]["total_duration_s"],
        "optimization_cost": round(optimization_cost, 4),
        "improvement": improvement,
        "baseline_train_pass_rate": baseline_train.pass_rate,
        "candidate_train_pass_rate": candidate_train.pass_rate,
        "errors": errors,
        "reproduce_command": audit_dict["reproducibility"]["reproduce_command"],
    })
    # Holdout 结果（可选）写入审计，供报告展示
    if holdout_result is not None:
        audit_dict["holdout"] = {
            "evalset_id": holdout_result.evalset_id,
            "pass_rate": holdout_result.pass_rate,
            "passed_cases": holdout_result.passed_cases,
            "total_cases": holdout_result.total_cases,
        }

    json_report = generate_json_report(
        task_id, baseline_train, baseline_val,
        attribution, gate, validation, optimization_info, audit_dict,
    )
    md_report = generate_md_report(
        task_id, baseline_train, baseline_val,
        attribution, gate, validation, audit_dict,
    )

    # Write reports
    os.makedirs(cfg.output_dir, exist_ok=True)
    json_path = os.path.join(cfg.output_dir, "optimization_report.json")
    md_path = os.path.join(cfg.output_dir, "optimization_report.md")
    with open(json_path, "w", encoding="utf-8") as f:
        f.write(json_report)
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md_report)
    tracer.set_output_files(json_path, md_path)
    tracer.end_stage("report")
    print(f"  Reports written to {json_path}, {md_path}")

    # ═══════════════════════════════════════════════════════════════
    # Summary
    # ═══════════════════════════════════════════════════════════════
    final_audit = tracer.finalize()
    print(f"\n{'='*50}")
    print(f"Pipeline Complete: {task_id}")
    print(f"  Duration: {final_audit.total_duration_s:.1f}s")
    print(f"  Gate:     {gate.decision.value}")
    print(f"  Baseline: {baseline_train.pass_rate:.1%} → Candidate: {candidate_train.pass_rate:.1%}")
    print(f"  Cost:     ${final_audit.total_cost_usd:.4f}")
    print(f"  Mode:     {cfg.mode}")
    print(f"  Seed:     {cfg.seed}")
    print(f"  Reproduce: {final_audit.reproduce_command}")

    # CI mode exit code: 1 = rejected, 2 = needs review
    return ci_exit_code(gate.decision, cfg.ci_mode)


if __name__ == "__main__":
    sys.exit(main())
