#!/usr/bin/env python3
"""评测 + 优化流水线 CLI 入口。

本脚本是完整的"评测 → 失败归因 → Prompt 优化 → 回归验证 → 产物审计"自动闭环
流水线的命令行入口，场景为电商购物助手。

两种运行模式:
  Demo 模式（默认，无需 API key）: 使用预录制 trace 数据运行流水线，
    trace 模式下评测从预录制轨迹计算 metric，不实际调用 LLM。

  Real 模式（需要 API key）: 实际调用 LLM 进行推理和优化，需先设置环境变量:
      export TRPC_AGENT_API_KEY=your_api_key
      export TRPC_AGENT_BASE_URL=https://api.openai.com/v1
      export TRPC_AGENT_MODEL_NAME=gpt-4o-mini  # 可选，默认 gpt-4o-mini

用法:
    # Demo 模式（无需 API key，使用预录制 trace 数据）
    python run_pipeline.py

    # Demo 模式显式指定
    python run_pipeline.py --demo-mode

    # Real 模式（需要 API key）
    python run_pipeline.py --no-demo-mode

    # 自定义输出目录
    python run_pipeline.py --output-dir my_runs/experiment_1

    # Real 模式 + 自定义门控配置
    python run_pipeline.py --no-demo-mode --max-regressions 1

流水线执行 6 个阶段:
  1. 基线评测（Baseline Evaluation）   — 在训练集和验证集上运行 AgentEvaluator
  2. 失败归因（Failure Attribution）    — 对失败 case 分类并聚类
  3. 优化执行（Optimization Execution） — 加载 demo 或运行真实 AgentOptimizer
  4. 候选验证（Candidate Validation）   — 重新评估验证集，计算逐 case delta
  5. 接受门控（Acceptance Gate）        — 按可配置标准评估候选 prompt
  6. 审计轨迹（Audit Trail）            — 生成 optimization_report.json + .md
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from pipeline._eval_backend import LiveBackend, TraceBackend
from pipeline._models import AcceptanceGateConfig
from pipeline._runner import PipelineRunner

# ---- 路径常量 ----
_HERE = Path(__file__).parent
DATA_DIR = _HERE / "data"                              # 数据目录（evalset、配置等）
OUTPUT_DIR = _HERE / "output"                          # 默认输出目录
PROMPT_PATH = _HERE / "agent" / "prompts" / "system.md"  # 系统 prompt 文件


def derive_scenario(eval_id: str) -> str:
    """按 eval_id 后缀推导场景. 不维护映射表.

    - ``_optimizable`` → ``optimizable_success``
    - ``_ineffective`` → ``optimization_ineffective``
    - ``_working`` / ``_regression`` → ``optimization_regression``
    - 未知后缀 → ``"unknown"`` (调用方应显式处理)
    """
    if eval_id.endswith("_optimizable"):
        return "optimizable_success"
    if eval_id.endswith("_ineffective"):
        return "optimization_ineffective"
    if eval_id.endswith("_working") or eval_id.endswith("_regression"):
        return "optimization_regression"
    return "unknown"


def build_gate_config(
    min_improvement: float = 0.0,
    no_new_hard_failures: bool = True,
    max_regressions: int = 0,
    critical_case_ids: list[str] | None = None,
    max_cost_budget: float = 0.0,
) -> AcceptanceGateConfig:
    """构建接受门控配置。

    Args:
        min_improvement: 接受候选所需的最小 pass rate 提升（0~1）。
        no_new_hard_failures: 是否禁止新增 hard failure（基线通过→候选失败）。
        max_regressions: 允许的最大回归 case 数。
        critical_case_ids: 必须通过的关键 case ID 列表。
        max_cost_budget: 最大 LLM 成本预算（USD，0=不限制）。

    Returns:
        AcceptanceGateConfig 实例。
    """
    return AcceptanceGateConfig(
        min_improvement_threshold=min_improvement,
        no_new_hard_failures=no_new_hard_failures,
        max_regressions_allowed=max_regressions,
        critical_case_ids=critical_case_ids or [],
        max_cost_budget=max_cost_budget,
    )


def _build_scenario_map(val_eval_path: Path) -> dict[str, str]:
    """读取验证集 evalset.json, 推导出每个 case 的场景."""
    cases = json.loads(val_eval_path.read_text())["eval_cases"]
    return {c["eval_id"]: derive_scenario(c["eval_id"]) for c in cases}


async def main() -> None:
    """CLI 主入口：解析命令行参数并运行流水线。"""
    parser = argparse.ArgumentParser(
        description="评测 + 优化流水线 — 电商购物助手场景",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    # ---- 命令行参数 ----
    parser.add_argument(
        "--output-dir",
        type=str,
        default=str(OUTPUT_DIR),
        help=f"报告输出目录（默认: {OUTPUT_DIR}）",
    )
    parser.add_argument(
        "--min-improvement",
        type=float,
        default=0.0,
        help="接受候选所需的最小 pass rate 提升（默认: 0.0）",
    )
    parser.add_argument(
        "--max-regressions",
        type=int,
        default=0,
        help="允许的最大回归 case 数（默认: 0）",
    )
    parser.add_argument(
        "--allow-regressions",
        action="store_true",
        help="允许任意数量的回归（设置 no_new_hard_failures=False, max_regressions=999）",
    )
    parser.add_argument(
        "--critical-cases",
        type=str,
        nargs="*",
        default=[],
        help="必须通过的关键 case ID（如 --critical-cases val_003_regression）",
    )
    parser.add_argument(
        "--max-cost",
        type=float,
        default=0.0,
        help="最大 LLM 成本预算（USD，默认: 0 = 不限制）",
    )
    parser.add_argument(
        "--demo-mode",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="以 demo 模式运行（使用预录制 trace 数据，无需 API key，默认: True）。"
             "使用 --no-demo-mode 进入 real 模式，需要设置 TRPC_AGENT_API_KEY 等环境变量。",
    )

    args = parser.parse_args()

    # Real 模式下检查环境变量
    if not args.demo_mode:
        missing = []
        if not os.environ.get("TRPC_AGENT_API_KEY"):
            missing.append("TRPC_AGENT_API_KEY")
        if not os.environ.get("TRPC_AGENT_BASE_URL"):
            missing.append("TRPC_AGENT_BASE_URL")
        if missing:
            print(f"[错误] Real 模式需要设置以下环境变量: {', '.join(missing)}")
            print("示例:")
            print("  export TRPC_AGENT_API_KEY=your_api_key")
            print("  export TRPC_AGENT_BASE_URL=https://api.openai.com/v1")
            print("  export TRPC_AGENT_MODEL_NAME=gpt-4o-mini  # 可选")
            print()
            print("或使用 Demo 模式（无需 API key）:")
            print("  python run_pipeline.py --demo-mode")
            sys.exit(1)
        model_name = os.environ.get("TRPC_AGENT_MODEL_NAME", "gpt-4o-mini")
        print(f"[Real 模式] API Key: sk-{'*' * 10}... | Base URL: {os.environ['TRPC_AGENT_BASE_URL']} | Model: {model_name}")
    else:
        print("[Demo 模式] 使用预录制 trace 数据，无需 API key")

    # 处理 --allow-regressions 标志
    if args.allow_regressions:
        no_new_hard = False
        max_reg = 999
    else:
        no_new_hard = True
        max_reg = args.max_regressions

    # 构建门控配置
    gate_config = build_gate_config(
        min_improvement=args.min_improvement,
        no_new_hard_failures=no_new_hard,
        max_regressions=max_reg,
        critical_case_ids=args.critical_cases,
        max_cost_budget=args.max_cost,
    )

    # 组装 backend 与路径 — demo/real 模式的唯一差异收敛于此.
    if args.demo_mode:
        val_baseline_eval_path = DATA_DIR / "trace" / "val_baseline.evalset.json"
        val_candidate_eval_path = DATA_DIR / "trace" / "val_optimized.evalset.json"
        train_eval_path = str(DATA_DIR / "trace" / "train.evalset.json")
        backend = TraceBackend()
        demo_optimize_result_path = str(DATA_DIR / "demo_optimize_result.json")
        train_eval_path_real = None
    else:
        val_baseline_eval_path = DATA_DIR / "live" / "val.evalset.json"
        val_candidate_eval_path = DATA_DIR / "live" / "val.evalset.json"
        train_eval_path = str(DATA_DIR / "live" / "train.evalset.json")
        # LiveBackend 默认 agent_factory=create_agent; 每次评估重建 agent 以重读 system.md.
        backend = LiveBackend()
        demo_optimize_result_path = None
        train_eval_path_real = str(DATA_DIR / "live" / "train.evalset.json")

    scenario_map = _build_scenario_map(val_baseline_eval_path)

    runner = PipelineRunner(
        train_eval_path=train_eval_path,
        val_baseline_eval_path=str(val_baseline_eval_path),
        val_candidate_eval_path=str(val_candidate_eval_path),
        gate_metrics_config_path=str(DATA_DIR / "gate_metrics.json"),
        optimizer_config_path=str(DATA_DIR / "optimizer.json"),
        prompt_source_path=str(PROMPT_PATH),
        prompt_field_name="system_prompt",
        gate_config=gate_config,
        backend=backend,
        demo_mode=args.demo_mode,
        output_dir=args.output_dir,
        scenario_map=scenario_map,
        demo_optimize_result_path=demo_optimize_result_path,
        train_eval_path_real=train_eval_path_real,
    )

    report = await runner.run()

    # ---- 打印摘要 ----
    print("\n" + "=" * 60)
    print(f"流水线结果: {report.overall_verdict}")
    print(f"基线 Pass Rate: {report.baseline_val.pass_rate:.4f}" if report.baseline_val else "基线: N/A")
    print(f"候选 Pass Rate: {report.candidate_validation.pass_rate:.4f}" if report.candidate_validation else "候选: N/A")
    print(f"改善: {report.overall_pass_rate_change:+.4f}")

    if report.gate_decision:
        for check in report.gate_decision.checks:
            status = "通过" if check.passed else "失败"
            print(f"  [{status}] {check.check_name}: {check.detail}")

    # runner 在 output_dir 下创建 <UTC-timestamp>/ 子目录; PipelineRunner 暴露
    # 该路径以便此处准确告知用户报告位置.
    print(f"\n报告已写入: {runner.run_dir}")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
