# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Evaluation + Optimization 自动闭环入口。

一条命令跑完六阶段：
  Baseline 评测 → 失败归因 → 优化执行 → 候选验证(逐 case delta)
  → 接受门控 → 审计落盘(optimization_report.json + .md)

用法
----
    # 离线 fake 模式（无需 API Key，≤3min，确定性可复现）——默认
    python run_pipeline.py

    # 真实模式（需 TRPC_AGENT_API_KEY / _BASE_URL / _MODEL_NAME）
    python run_pipeline.py --mode real

产物写到 ``runs/<timestamp>/``，并在其中生成 optimization_report.{json,md}。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import sys
from datetime import datetime
from functools import partial
from pathlib import Path


_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parents[2]
for _p in (str(_REPO_ROOT), str(_HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from agent import PROMPT_PATHS  # noqa: E402
from pipeline import attribution as attrib_mod  # noqa: E402
from pipeline import gate as gate_mod  # noqa: E402
from pipeline import optimize as opt_mod  # noqa: E402
from pipeline import report as report_mod  # noqa: E402
from pipeline.evaluate import evaluate_set  # noqa: E402


TRAIN_PATH = _HERE / "data" / "train.evalset.json"
VAL_PATH = _HERE / "data" / "val.evalset.json"
METRICS_PATH = _HERE / "eval_metrics.json"
CONFIG_PATH = _HERE / "config.json"
OPTIMIZER_PATH = _HERE / "optimizer.json"
RUNS_DIR = _HERE / "runs"


def _build_call_agent(mode: str):
    """按模式返回 call_agent 回调。"""
    if mode == "fake":
        from agent import fake_backend  # 延迟导入：real 模式不必加载
        return partial(fake_backend.call_agent_fake, prompt_paths=PROMPT_PATHS)
    from agent.orchestrator import call_agent_real
    return call_agent_real


async def run(mode: str, output_dir: Path) -> dict:
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    seed = int(config.get("seed", 42))
    random.seed(seed)

    call_agent = _build_call_agent(mode)
    started_at = datetime.now()
    output_dir.mkdir(parents=True, exist_ok=True)

    # ---- 阶段 1：Baseline 评测（train + val，读源 prompt=baseline）----
    print(f"[1/6] Baseline 评测 (mode={mode}) ...")
    baseline_train = await evaluate_set(TRAIN_PATH, call_agent, METRICS_PATH, output_dir / "baseline_train")
    baseline_val = await evaluate_set(VAL_PATH, call_agent, METRICS_PATH, output_dir / "baseline_val")
    print(f"      train {baseline_train.pass_count}/{baseline_train.total}"
          f" | val {baseline_val.pass_count}/{baseline_val.total}")

    # ---- 阶段 2：失败归因 ----
    print("[2/6] 失败归因 ...")
    train_attrib = await attrib_mod.attribute_failures(baseline_train, mode)
    val_attrib = await attrib_mod.attribute_failures(baseline_val, mode)
    print(f"      train clusters={train_attrib['clusters']} | val clusters={val_attrib['clusters']}")

    # ---- 阶段 3：优化执行（结束后源 prompt 已还原到 baseline）----
    print("[3/6] 优化执行 ...")
    if mode == "fake":
        candidate = await opt_mod.optimize_fake(TRAIN_PATH, VAL_PATH)
    else:
        candidate = await opt_mod.optimize_real(
            OPTIMIZER_PATH, call_agent, TRAIN_PATH, VAL_PATH, output_dir / "optimize"
        )
    print(f"      status={candidate.status} fields={candidate.optimized_fields}"
          f" cost=${candidate.cost_usd:.4f}")

    # ---- 阶段 4：候选验证（临时把候选写入源 prompt，评测后还原）----
    print("[4/6] 候选验证 ...")
    snapshot = opt_mod.apply_candidate(candidate.prompts)
    try:
        # 同时在训练集与验证集上重跑候选：训练集用于识别"训练提升但验证退化"
        # 的过拟合，验证集用于门控。
        candidate_train = await evaluate_set(TRAIN_PATH, call_agent, METRICS_PATH, output_dir / "candidate_train")
        candidate_val = await evaluate_set(VAL_PATH, call_agent, METRICS_PATH, output_dir / "candidate_val")
    finally:
        opt_mod.restore_prompts(snapshot)
    deltas = gate_mod.compute_delta(baseline_val, candidate_val)
    train_deltas = gate_mod.compute_delta(baseline_train, candidate_train)
    print(f"      candidate train {candidate_train.pass_count}/{candidate_train.total}"
          f" | val {candidate_val.pass_count}/{candidate_val.total}")

    # ---- 阶段 5：接受门控 ----
    print("[5/6] 接受门控 ...")
    decision = gate_mod.evaluate_gate(
        baseline_val, candidate_val, deltas, candidate.cost_usd, config.get("gate", {})
    )
    print(f"      decision={'ACCEPT' if decision.accepted else 'REJECT'} :: {decision.summary}")

    # ---- 阶段 6：审计落盘 ----
    print("[6/6] 审计落盘 ...")
    finished_at = datetime.now()
    run_meta = {
        "mode": mode,
        "seed": seed,
        "started_at": started_at.isoformat(timespec="seconds"),
        "finished_at": finished_at.isoformat(timespec="seconds"),
        "elapsed_seconds": round((finished_at - started_at).total_seconds(), 2),
        "train_dataset": str(TRAIN_PATH.relative_to(_HERE)),
        "val_dataset": str(VAL_PATH.relative_to(_HERE)),
        "target_fields": list(PROMPT_PATHS.keys()),
        "gate_config": config.get("gate", {}),
    }
    report = report_mod.build_report(
        run_meta=run_meta,
        baseline_train=baseline_train,
        baseline_val=baseline_val,
        train_attrib=train_attrib,
        val_attrib=val_attrib,
        candidate=candidate,
        candidate_train=candidate_train,
        candidate_val=candidate_val,
        deltas=deltas,
        train_deltas=train_deltas,
        gate=decision,
    )
    report_mod.save_json(report, output_dir / "optimization_report.json")
    report_mod.save_markdown(report, output_dir / "optimization_report.md")
    print(f"      报告已写入 {output_dir}/optimization_report.(json|md)")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluation + Optimization 自动闭环")
    parser.add_argument("--mode", choices=["fake", "real"], default="fake",
                        help="fake=离线确定性(默认)；real=真实 LLM + AgentOptimizer")
    parser.add_argument("--output-dir", default="", help="产物目录；默认 runs/<timestamp>")
    args = parser.parse_args()

    output_dir = Path(args.output_dir) if args.output_dir else (
        RUNS_DIR / datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    )
    report = asyncio.run(run(args.mode, output_dir))
    accepted = report["gate_decision"]["accepted"]
    # 退出码：接受=0，拒绝=2（便于 CI 判定；拒绝不是错误，是有效负决策）
    sys.exit(0 if accepted else 2)


if __name__ == "__main__":
    main()
