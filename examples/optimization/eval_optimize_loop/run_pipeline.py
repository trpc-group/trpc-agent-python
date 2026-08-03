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
import shlex
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
from pipeline.validate import (
    run_validation_trace,
    ValidationDelta,
    ValidationResult,
)
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
    while non-default configurations can be reproduced exactly. String
    values are shell-quoted so paths with spaces/metacharacters replay.
    """
    parts = [f"python run_pipeline.py --mode {shlex.quote(args.mode)}"]
    if args.seed != 42:
        parts.append(f"--seed {args.seed}")
    if args.scenario != "fix_attributed":
        parts.append(f"--scenario {shlex.quote(args.scenario)}")
    if args.max_iterations != 3:
        parts.append(f"--max-iterations {args.max_iterations}")
    if args.min_improvement != 0.05:
        parts.append(f"--min-improvement {args.min_improvement}")
    if args.max_cost != 10.0:
        parts.append(f"--max-cost {args.max_cost}")
    if args.output_dir != "sample_output":
        parts.append(f"--output-dir {shlex.quote(args.output_dir)}")
    if args.train_evalset != "data/train.evalset.json":
        parts.append(f"--train-evalset {shlex.quote(args.train_evalset)}")
    if args.val_evalset != "data/val.evalset.json":
        parts.append(f"--val-evalset {shlex.quote(args.val_evalset)}")
    if args.holdout_evalset != "data/holdout.evalset.json":
        parts.append(f"--holdout-evalset {shlex.quote(args.holdout_evalset)}")
    if args.optimizer_config != "data/optimizer.json":
        parts.append(f"--optimizer-config {shlex.quote(args.optimizer_config)}")
    if args.val_regression_cases:
        parts.append(f"--val-regression-cases {shlex.quote(args.val_regression_cases)}")
    if args.critical_cases:
        parts.append(f"--critical-cases {shlex.quote(args.critical_cases)}")
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


def is_output_dir_allowed(output_dir: str) -> bool:
    """output_dir 必须解析到仓库根目录的严格子目录（防任意文件写入）。

    拒绝 `..` 越界、外部绝对路径，以及直接写到仓库根（会污染根目录）。
    `_REPO_ROOT` 为项目根（3 级上跳）。
    """
    _root_abs = os.path.realpath(_REPO_ROOT)
    _out_abs = os.path.realpath(output_dir)
    return _out_abs.startswith(_root_abs + os.sep)


def live_gate_downgrade(gate: GateResult, *, live: bool,
                        optimization_cost: float,
                        max_cost_budget: float) -> GateResult:
    """live 模式下把不可比评分驱动的 ACCEPT/REJECT 降级为 NEEDS_REVIEW。

    baseline=SDK 评分、候选=trace comparator 重评，口径不可比；除"成本超预算"
    （真实约束、与评分口径无关）外，依赖 pass-rate 差值的 ACCEPT 与各类 REJECT
    （退化/关键 case/过拟合/新增失败）一律降级，避免误判 ACCEPT 或 CI 阻断。

    Returns:
        处理后的 gate（可能已降级为 NEEDS_REVIEW）。
    """
    _details = gate.details or {}
    # 成本超预算判定：gate 决策顺序短路——退化/关键 case/过拟合 REJECT 时
    # details 无顶层 budget 键，但 checks 里 cost_budget 项仍标记未通过；
    # 两类都视为"真实成本约束"，不得被降级规避
    _cost_exceeded = ("budget" in _details) or any(
        c.get("check") == "cost_budget" and c.get("passed") is False
        for c in _details.get("checks") or []
    )
    if (live and gate.decision in (GateDecision.ACCEPT, GateDecision.REJECT)
            and not (gate.decision == GateDecision.REJECT
                     and (_cost_exceeded
                          or _details.get("reason_code") == "scenario_config_error"))):
        return GateResult(
            decision=GateDecision.NEEDS_REVIEW,
            reason=("live mode: " + gate.decision.value.upper()
                    + " downgraded to review — baseline=SDK vs "
                    f"candidate=trace-comparator scoring differ: {gate.reason}"),
            details=gate.details,
        )
    return gate


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
                        help=("CI mode: exit 1 on gate rejection, 2 on needs-review; "
                              "live mode is informational only (NEEDS_REVIEW exits 0)"))
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

    # output_dir 路径安全：必须位于仓库根目录内（拒绝 `..` 越界与外部绝对路径）。
    # 相对路径按 CWD 解析，默认 `sample_output` 需在仓库目录内运行本脚本。
    if not is_output_dir_allowed(cfg.output_dir):
        print(f"  ❌ output-dir 必须位于仓库内（{_REPO_ROOT}），拒绝: {cfg.output_dir}")
        print("     提示：请先 cd 到仓库目录再运行 run_pipeline.py（相对路径按当前目录解析）。")
        return 1

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
        # 同时校验 optimizer 配置存在与结构（fake/live 两模式配置错误都在 Stage 1 暴露）
        load_optimizer_json(cfg.optimizer_config)
    except (FileNotFoundError, ValueError) as e:
        print(f"  ❌ Configuration error: {e}")
        tracer.add_error(str(e))
        tracer.end_stage("config")  # 保证审计阶段闭合
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
        try:
            # train/val 在同一个 asyncio.run 内顺序执行（而非 gather 并发：
            # SDK AgentEvaluator 的并发可重入性未验证，顺序执行避免共享状态
            # 相互干扰）。注意：Stage 4 的 optimize 是第二次独立 asyncio.run——
            # 当前 call_agent 为确定性 fake、不绑定 loop 资源，故分开无碍；
            # 后续接入绑定 loop 的真实 agent 时需合并为同一事件循环。
            async def _run_live_baselines():
                train = await run_baseline_sdk(
                    cfg.train_evalset, call_agent=_call_agent,
                    eval_config=_eval_config,
                    optimizer_config_path=cfg.optimizer_config,
                    config=cfg)
                val = await run_baseline_sdk(
                    cfg.val_evalset, call_agent=_call_agent,
                    eval_config=_eval_config,
                    optimizer_config_path=cfg.optimizer_config,
                    config=cfg)
                return train, val

            baseline_train, baseline_val = asyncio.run(_run_live_baselines())
        except Exception as _e:
            # 与 fake 模式一致：SDK 未捕获的异常（RuntimeError 等）也优雅降级，
            # 避免整个 pipeline 崩溃（errors 会在下方统一打印/记录）
            # 注意：不能在这里 from-import run_baseline_fake（会使该名字在 main()
            # 内变为局部变量，fake 路径未赋值即用 → UnboundLocalError）
            print(f"  ⚠️  live baseline 异常，降级到 trace comparator: {_e}")
            baseline_train = run_baseline_fake(cfg.train_evalset, cfg)
            baseline_val = run_baseline_fake(cfg.val_evalset, cfg)
            _msg = (f"live baseline failed ({type(_e).__name__}: {_e}); "
                    f"fell back to trace comparator")
            # 保留 fake 回退自身的原始错误（如文件缺失/解析失败），不覆盖
            baseline_train.errors = [_msg] + baseline_train.errors
            baseline_val.errors = [_msg] + baseline_val.errors

    if baseline_train.errors:
        for e in baseline_train.errors:
            print(f"  ⚠️  Train: {e}")
            # 降级是预期行为：记录为 warning，不混入 audit.errors（避免误判致命错误）
            tracer.add_warning(e)
    if baseline_val.errors:
        for e in baseline_val.errors:
            print(f"  ⚠️  Val: {e}")
            tracer.add_warning(e)

    # live 模式下 SDK 评估降级为 trace comparator 时显式提示，
    # 避免把 fallback 结果误当作真实 SDK 评分。
    if cfg.mode == "live" and (baseline_train.errors or baseline_val.errors):
        print("  ⚠️  live baseline fell back to trace comparator — "
              "pass rates are not real SDK scoring")
        tracer.add_warning(
            "live baseline fell back to trace comparator (SDK errors above) — "
            "pass rates are not real SDK scoring")

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
            if cfg.mode == "live":
                print("  ⚠️  holdout 由 trace comparator 评分（live 下 train/val 走 SDK），"
                      "与 baseline 语义不可比")
                tracer.add_warning(
                    "holdout scored via trace comparator in live mode "
                    "(train/val use SDK) — not directly comparable")
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
        try:
            from agent.agent import build_call_agent
            optimize_result = asyncio.run(
                run_optimize_live(cfg.optimizer_config, cfg,
                                  call_agent=build_call_agent())
            )
        except Exception as _e:
            # 与 fake 模式一致：SDK 未捕获的异常也优雅降级，不崩溃
            # （OptimizeResult 已在模块顶部导入，此处不能重复 from-import，
            #   否则会在 main() 内遮蔽为局部变量）
            print(f"  ⚠️  live optimize 异常，降级为空结果: {_e}")
            optimize_result = OptimizeResult(algorithm=cfg.algorithm)
            optimize_result.errors = [
                f"live optimize failed ({type(_e).__name__}: {_e})"
            ]

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

    _scenario_error = ""
    try:
        validation = run_validation_trace(
            cfg.train_evalset,
            cfg.val_evalset,
            baseline_val,
            optimize_result,
            cfg,
            scenario=cfg.scenario,
            val_regression_cases=cfg.val_regression_cases,
        )
    except ValueError as _ve:
        # 场景配置边界（如 overfit + 空 val 集）显式报错时，不崩溃：
        # 记录为 warning，并构造"有新增失败"的结果让 gate 拒绝/需审查，继续出报告。
        # 标记场景错误，后续 gate reason 反映真实原因而非误报 "Overfitting"。
        _scenario_error = str(_ve)
        print(f"  ⚠️  Validation scenario error: {_ve}")
        tracer.add_warning(f"Validation scenario error: {_ve}")
        validation = ValidationResult(
            baseline=baseline_val,
            candidate=None,
            deltas=[
                ValidationDelta(
                    eval_id="__scenario_error__",
                    baseline_passed=True,
                    candidate_passed=False,
                    change="new_fail",
                )
            ],
        )
        validation.candidate_train = baseline_train
        errors.append(str(_ve))
    candidate_train = validation.candidate_train or baseline_train
    tracer.end_stage("validate")
    print(f"  New passes: {validation.new_passes}, "
          f"New failures: {validation.new_failures}, "
          f"Unchanged: {validation.unchanged}")
    if validation.is_overfitting:
        print(f"  ⚠️  Overfitting detected!")
        tracer.add_warning("Overfitting detected: candidate regresses on validation set")

    # live 模式诚实标注：验证/门控基于 scenario 合成候选，而非真实优化后 prompt。
    # （真实重评需要基于 optimize_result.best_prompt 驱动 SDK agent，属后续工作）
    if cfg.mode == "live":
        msg = ("live mode: validation/gate uses scenario-simulated candidates, "
               "not the real optimized prompt — results are indicative, not authoritative")
        print(f"  ⚠️  {msg}")
        tracer.add_warning(msg)

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

    # overfit 场景配置错误（空 val / 回归 case 不可扰动）被 run_validation_trace 报错时，
    # gate 的 overfitting REJECT 是"合成 delta"导致的——改写 reason 反映真实原因，
    # 避免把场景配置错误误报为真实的过拟合。
    if _scenario_error:
        gate = GateResult(
            decision=GateDecision.REJECT,
            reason=f"Validation scenario configuration error: {_scenario_error}",
            details={
                **(gate.details or {}),
                "reason_code": "scenario_config_error",
            },
        )
        tracer.add_warning(f"Gate rejected due to scenario config error: {_scenario_error}")

    # live 模式下 baseline=SDK 评分、候选=trace comparator 重评，口径不可比：
    # 除"成本超预算"外，依赖 pass-rate 差值的 ACCEPT/REJECT 一律降级 NEEDS_REVIEW。
    _downgraded = live_gate_downgrade(
        gate, live=(cfg.mode == "live"),
        optimization_cost=optimization_cost,
        max_cost_budget=cfg.max_cost_budget,
    )
    if _downgraded.decision != gate.decision:
        gate = _downgraded
        tracer.add_warning(
            "live mode: gate decision downgraded to NEEDS_REVIEW "
            "(incomparable SDK/comparator scoring)"
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
    # live 模式两端口径不可比（SDK baseline 评分 vs trace comparator 重评分），
    # 标注说明避免把差值当作客观提升
    improvement_note = (
        "live mode: SDK baseline 与 trace comparator 评分口径不可比，improvement 仅供参考"
        if cfg.mode == "live" else ""
    )
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

    # 报告路径已知后立即登记，确保 to_dict() 序列化的 audit.output_files 非空
    json_path = os.path.join(cfg.output_dir, "optimization_report.json")
    md_path = os.path.join(cfg.output_dir, "optimization_report.md")
    tracer.set_output_files(json_path, md_path)

    audit_dict = tracer.to_dict()
    # Enrich audit with backward-compatible fields
    audit_dict.update({
        "seed": cfg.seed,
        "mode": cfg.mode,
        "duration_seconds": audit_dict["timing"]["total_duration_s"],
        "optimization_cost": round(optimization_cost, 4),
        "improvement": improvement,
        "improvement_note": improvement_note,
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

    # Write reports（路径已在 to_dict 前登记）
    if os.path.isfile(cfg.output_dir):
        # output_dir 指向已存在文件：makedirs 会抛 FileExistsError，
        # 且此时所有阶段已跑完——提前显式报错，避免结果静默丢失
        raise ValueError(
            f"output_dir '{cfg.output_dir}' is a file, not a directory")
    os.makedirs(cfg.output_dir, exist_ok=True)
    with open(json_path, "w", encoding="utf-8") as f:
        f.write(json_report)
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md_report)
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

    # CI mode exit code: 1 = rejected, 2 = needs review。
    # live 模式下 baseline=SDK 与候选=comparator 评分不可比，gate 恒被降级为
    # NEEDS_REVIEW——此时 CI 仅作 informational，退出 0，避免 --mode live --ci 恒失败。
    if cfg.mode == "live" and gate.decision == GateDecision.NEEDS_REVIEW:
        return 0
    return ci_exit_code(gate.decision, cfg.ci_mode)


if __name__ == "__main__":
    sys.exit(main())
