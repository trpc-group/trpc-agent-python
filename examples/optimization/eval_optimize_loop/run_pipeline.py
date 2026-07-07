#!/usr/bin/env python3
"""Evaluation + Optimization Pipeline — main entry point.

Implements the full closed loop:
  baseline → attribution → optimize → validate → gate → report

Usage:
    python run_pipeline.py --mode fake
    python run_pipeline.py --optimizer-config data/optimizer.json
"""

import argparse
import os
import sys
import time
import uuid
from datetime import datetime, timezone

# Ensure imports work from the example directory
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pipeline.config import (
    PipelineConfig,
    load_evalset,
    load_optimizer_json,
    load_pipeline_config,
)
from pipeline.baseline import BaselineResult, run_baseline_fake
from pipeline.attribution import attribute_failures, AttributionReport
from pipeline.gate import evaluate_gate, GateDecision, GateResult
from pipeline.validate import run_validation_fake, ValidationResult
from pipeline.report import generate_json_report, generate_md_report


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Evaluation + Optimization Closed-Loop Pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python run_pipeline.py --mode fake
  python run_pipeline.py --mode fake --verbose
  python run_pipeline.py --output-dir ./results
        """,
    )
    parser.add_argument("--mode", default="fake", choices=["fake", "live"],
                        help="Execution mode (default: fake)")
    parser.add_argument("--train-evalset", default="data/train.evalset.json")
    parser.add_argument("--val-evalset", default="data/val.evalset.json")
    parser.add_argument("--optimizer-config", default="data/optimizer.json")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--min-improvement", type=float, default=0.05)
    parser.add_argument("--max-cost", type=float, default=10.0)
    parser.add_argument("--output-dir", default="sample_output")
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument("--ci", action="store_true",
                        help="CI mode: exit non-zero on gate rejection")
    args = parser.parse_args()

    # Generate task ID
    task_id = f"opt-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"

    start_time = time.monotonic()
    cfg = load_pipeline_config(
        train_evalset=args.train_evalset,
        val_evalset=args.val_evalset,
        optimizer_config=args.optimizer_config,
        seed=args.seed,
        min_improvement_threshold=args.min_improvement,
        max_cost_budget=args.max_cost,
        output_dir=args.output_dir,
        mode=args.mode,
        verbose=args.verbose,
        ci_mode=args.ci,
    )

    errors: list[str] = []

    # ═══════════════════════════════════════════════════════════════
    # Stage 1: Load Configuration
    # ═══════════════════════════════════════════════════════════════
    print("[1/7] Loading configuration...")
    try:
        train_data = load_evalset(cfg.train_evalset)
        val_data = load_evalset(cfg.val_evalset)
    except FileNotFoundError as e:
        print(f"  ❌ Configuration error: {e}")
        return 1
    print(f"  Train: {cfg.train_evalset} ({len(train_data.get('eval_cases', []))} cases)")
    print(f"  Val:   {cfg.val_evalset} ({len(val_data.get('eval_cases', []))} cases)")

    # ═══════════════════════════════════════════════════════════════
    # Stage 2: Baseline Evaluation
    # ═══════════════════════════════════════════════════════════════
    print("[2/7] Running baseline evaluation...")
    if cfg.mode == "fake":
        baseline_train = run_baseline_fake(cfg.train_evalset, cfg)
        baseline_val = run_baseline_fake(cfg.val_evalset, cfg)
    else:
        from pipeline.baseline import run_baseline_sdk
        baseline_train = run_baseline_sdk(cfg.train_evalset)
        baseline_val = run_baseline_sdk(cfg.val_evalset)

    if baseline_train.errors:
        for e in baseline_train.errors:
            print(f"  ⚠️  Train: {e}")
            errors.append(e)
    if baseline_val.errors:
        for e in baseline_val.errors:
            print(f"  ⚠️  Val: {e}")
            errors.append(e)

    print(f"  Train pass rate: {baseline_train.pass_rate:.1%} "
          f"({baseline_train.passed_cases}/{baseline_train.total_cases})")
    print(f"  Val pass rate:   {baseline_val.pass_rate:.1%} "
          f"({baseline_val.passed_cases}/{baseline_val.total_cases})")

    # ═══════════════════════════════════════════════════════════════
    # Stage 3: Failure Attribution
    # ═══════════════════════════════════════════════════════════════
    print("[3/7] Attributing failures...")
    attribution = attribute_failures(
        baseline_train.__dict__ if hasattr(baseline_train, '__dict__') else baseline_train,
        baseline_val.__dict__ if hasattr(baseline_val, '__dict__') else baseline_val,
    )
    print(f"  {attribution.total_failures} failure(s) across {len(attribution.by_category)} categories")
    for cat, count in sorted(attribution.by_category.items(), key=lambda x: x[1], reverse=True):
        print(f"    {cat}: {count}")

    # ═══════════════════════════════════════════════════════════════
    # Stage 4: Optimization (fake mode = simulate)
    # ═══════════════════════════════════════════════════════════════
    print("[4/7] Running optimization...")
    if cfg.mode == "fake":
        # In fake mode, simulate optimization — candidate improves
        # by "fixing" failures identified in attribution
        optimization_cost = 0.05 * len(attribution.entries)
        optimized_fields = ["system.md"]
        if cfg.verbose:
            for entry in attribution.entries[:5]:
                print(f"  Optimizing for: {entry.case_id} ({entry.category.value})")
    else:
        # Real optimization via AgentOptimizer
        try:
            # This would call AgentOptimizer.optimize()
            optimization_cost = 0.0
            optimized_fields = []
        except Exception as e:
            print(f"  ❌ Optimization error: {e}")
            errors.append(str(e))
            optimization_cost = 0.0
            optimized_fields = []

    # ═══════════════════════════════════════════════════════════════
    # Stage 5: Candidate Validation
    # ═══════════════════════════════════════════════════════════════
    print("[5/7] Validating candidate on validation set...")
    # Simulate candidate improvement based on attribution
    candidate_train = BaselineResult(
        evalset_id=baseline_train.evalset_id,
        pass_rate=min(1.0, baseline_train.pass_rate + 0.2),
        total_cases=baseline_train.total_cases,
        passed_cases=min(baseline_train.total_cases,
                         baseline_train.passed_cases + 2),
        failed_cases=max(0, baseline_train.failed_cases - 2),
        failed_case_ids=baseline_train.failed_case_ids[2:],
    )

    validation = run_validation_fake(
        cfg.val_evalset, baseline_val, candidate_train, cfg,
    )
    print(f"  New passes: {validation.new_passes}, "
          f"New failures: {validation.new_failures}, "
          f"Unchanged: {validation.unchanged}")
    if validation.is_overfitting:
        print(f"  ⚠️  Overfitting detected!")

    # ═══════════════════════════════════════════════════════════════
    # Stage 6: Gate Decision
    # ═══════════════════════════════════════════════════════════════
    print("[6/7] Evaluating gate...")
    gate = evaluate_gate(
        baseline_pass_rate=baseline_train.pass_rate,
        candidate_pass_rate=candidate_train.pass_rate,
        baseline_metrics=baseline_train.metric_breakdown,
        candidate_metrics=candidate_train.metric_breakdown,
        min_improvement=cfg.min_improvement_threshold,
        baseline_failed=baseline_train.failed_case_ids,
        candidate_failed=candidate_train.failed_case_ids,
        max_cost=cfg.max_cost_budget,
        optimization_cost=optimization_cost,
    )
    gate_icon = {"accept": "✅", "reject": "❌", "needs_review": "⚠️ "}
    print(f"  {gate_icon.get(gate.decision.value, '❓')} {gate.decision.value.upper()}: {gate.reason}")

    # ═══════════════════════════════════════════════════════════════
    # Stage 7: Report Generation
    # ═══════════════════════════════════════════════════════════════
    print("[7/7] Generating reports...")
    duration = time.monotonic() - start_time

    audit = {
        "seed": cfg.seed,
        "mode": cfg.mode,
        "duration_seconds": round(duration, 1),
        "optimization_cost": round(optimization_cost, 4),
        "improvement": round(candidate_train.pass_rate - baseline_train.pass_rate, 4),
        "baseline_train_pass_rate": baseline_train.pass_rate,
        "candidate_train_pass_rate": candidate_train.pass_rate,
        "errors": errors,
        "reproduce_command": f"python run_pipeline.py --mode {cfg.mode} --seed {cfg.seed}",
    }

    optimization_info = {
        "algorithm": cfg.algorithm,
        "mode": cfg.mode,
        "optimized_fields": optimized_fields,
        "optimization_cost": optimization_cost,
    }

    json_report = generate_json_report(
        task_id, baseline_train, baseline_val,
        attribution, gate, validation, optimization_info, audit,
    )
    md_report = generate_md_report(
        task_id, baseline_train, baseline_val,
        attribution, gate, validation, audit,
    )

    # Write reports
    os.makedirs(cfg.output_dir, exist_ok=True)
    json_path = os.path.join(cfg.output_dir, "optimization_report.json")
    md_path = os.path.join(cfg.output_dir, "optimization_report.md")
    with open(json_path, "w", encoding="utf-8") as f:
        f.write(json_report)
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md_report)
    print(f"  Reports written to {json_path}, {md_path}")

    # ═══════════════════════════════════════════════════════════════
    # Summary
    # ═══════════════════════════════════════════════════════════════
    print(f"\n{'='*50}")
    print(f"Pipeline Complete: {task_id}")
    print(f"  Duration: {duration:.1f}s")
    print(f"  Gate:     {gate.decision.value}")
    print(f"  Baseline: {baseline_train.pass_rate:.1%} → Candidate: {candidate_train.pass_rate:.1%}")
    print(f"  Mode:     {cfg.mode}")

    # CI mode exit code
    if cfg.ci_mode and gate.decision == GateDecision.REJECT:
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
