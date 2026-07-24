"""eval_optimize_loop 闭环入口。

用法：
    python run_pipeline.py --mode fake [--output-dir sample_output]
    python run_pipeline.py --mode trace   # 与 fake 同路径（确定性 trace 回放）
    python run_pipeline.py --mode online  # 需 TRPC_AGENT_API_KEY 等环境变量（M6 完善）

退出码：0 = accept；2 = reject / needs_review；1 = 出错
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from offline.fixtures import CASES, CANDIDATE_PROMPTS
from pipeline.attribution import attribute_failures
from pipeline.comparator import compare
from pipeline.config import (
    GateConfig,
    load_eval_config,
    load_gate_config,
    sha256_bytes,
    sha256_dict,
    sha256_file,
)
from pipeline.evaluator import evaluate_split
from pipeline.gate import evaluate_gate
from pipeline.models import (
    AuditInfo,
    BaselineResult,
    CandidateResult,
    CostInfo,
    DataQuality,
    OptimizerInfo,
)
from pipeline.reporting import build_report, write_outputs

HERE = Path(__file__).parent
PROMPTS_DIR = HERE / "agent" / "prompts"
GATE_JSON = HERE / "gate.json"
OPTIMIZER_JSON = HERE / "optimizer.json"
TRAIN_EVALSET = HERE / "data" / "train.evalset.json"
VAL_EVALSET = HERE / "data" / "val.evalset.json"

CANDIDATE_VARIANTS = ["robust", "ineffective", "overfit"]
SEED = 42


def read_prompt(variant: str) -> str:
    return (PROMPTS_DIR / CANDIDATE_PROMPTS[variant]).read_text(encoding="utf-8")


def check_data_quality(cases: list[dict]) -> DataQuality:
    train_ids = [c["eval_id"] for c in cases if c["split"] == "train"]
    val_ids = [c["eval_id"] for c in cases if c["split"] == "validation"]
    cross = set(train_ids) & set(val_ids)
    return DataQuality(
        passed=not cross,
        train_cases=len(train_ids),
        validation_cases=len(val_ids),
        cross_split_duplicates=len(cross),
        prompt_leakage_matches=0,
    )


def build_audit(*, started_iso: str, finished_iso: str, duration: float, mode: str, command: str,
                gate_cfg: GateConfig) -> AuditInfo:
    baseline_prompt_sha = {"system_prompt": sha256_bytes(read_prompt("baseline").encode("utf-8"))}
    return AuditInfo(
        run_id=f"{mode}-{int(duration)}-{SEED}",
        started_at=started_iso,
        finished_at=finished_iso,
        duration_seconds=round(duration, 3),
        seed=SEED,
        config_sha256=sha256_dict({
            "gate": json.loads(GATE_JSON.read_text(encoding="utf-8")),
            "optimizer": json.loads(OPTIMIZER_JSON.read_text(encoding="utf-8")),
        }),
        train_sha256=sha256_file(TRAIN_EVALSET),
        validation_sha256=sha256_file(VAL_EVALSET),
        baseline_prompt_sha256=baseline_prompt_sha,
        cost=CostInfo(
            measurement=gate_cfg.cost_measurement,  # type: ignore[arg-type]
            optimization_usd=0.0,
            evaluation_usd=0.0,
            total_usd=0.0,
        ),
        command=command,
    )


async def run_fake(gate_cfg: GateConfig, eval_config, output_dir: Path, command: str, mode: str):
    """fake/trace 模式：确定性 trace 回放（fixtures 提供 actual），全程无 LLM。"""
    started_ts = time.time()
    started_iso = datetime.now(timezone.utc).isoformat()
    cases = CASES

    base_train = await evaluate_split(cases, "baseline", "train", eval_config)
    base_val = await evaluate_split(cases, "baseline", "validation", eval_config)
    baseline = BaselineResult(train=base_train, validation=base_val)

    metric_calls = len(cases)  # baseline
    candidate_results: list[CandidateResult] = []
    for variant in CANDIDATE_VARIANTS:
        ct = await evaluate_split(cases, variant, "train", eval_config)
        cv = await evaluate_split(cases, variant, "validation", eval_config)
        metric_calls += len(cases)
        delta = compare(base_train, base_val, ct, cv)
        elapsed = time.time() - started_ts
        gate = evaluate_gate(delta, gate_cfg, duration_seconds=elapsed, metric_calls=metric_calls)
        prompt_text = read_prompt(variant)
        candidate_results.append(
            CandidateResult(
                candidate_id=variant,
                source="fixture",
                prompts={variant: prompt_text},
                train=ct,
                validation=cv,
                delta=delta,
                gate=gate,
                audit_prompt_sha256=sha256_bytes(prompt_text.encode("utf-8")),
            ))

    failure_attr = attribute_failures(base_train, base_val, cases, "baseline")
    dq = check_data_quality(cases)
    selected = next((c for c in candidate_results if c.gate.accepted), None)
    optimizer = OptimizerInfo(
        algorithm="fixture-deterministic",
        status="succeeded",
        rounds=len(CANDIDATE_VARIANTS),
        used_agent_optimizer=False,
    )
    finished_iso = datetime.now(timezone.utc).isoformat()
    duration = time.time() - started_ts
    audit = build_audit(
        started_iso=started_iso,
        finished_iso=finished_iso,
        duration=duration,
        mode=mode,
        command=command,
        gate_cfg=gate_cfg,
    )
    report = build_report(
        mode=mode,
        seed=SEED,
        baseline=baseline,
        candidates=candidate_results,
        selected_id=selected.candidate_id if selected else None,
        failure_attribution=failure_attr,
        optimizer=optimizer,
        data_quality=dq,
        audit=audit,
    )
    write_outputs(report, output_dir)
    return report


async def run_online(gate_cfg, optimizer_path, output_dir, command):
    """online 模式：真实 AgentOptimizer.optimize（GEPA 反思优化）。需 API key。

    call_agent（agent/agent.py）每次重读 system.md → prompt 热加载，候选 prompt 真实改变 agent 行为。
    SDK 原生 OptimizeResult（baseline/best pass_rate、每轮候选、cost）写入 output_dir/online_run/。
    完整 gate + 自定义 report 闭环在 fake/trace（无 key 可验证）已演示；online 接入业务时复用
    同一套 pipeline 外层。
    """
    for env in ("TRPC_AGENT_API_KEY", "TRPC_AGENT_BASE_URL", "TRPC_AGENT_MODEL_NAME"):
        if not os.environ.get(env):
            print(
                f"[online] 缺少环境变量 {env}，无法运行真实优化。请用 --mode fake 跑确定性闭环。",
                file=sys.stderr,
            )
            return None
    from trpc_agent_sdk.evaluation import AgentOptimizer, TargetPrompt

    from agent.agent import call_agent as real_call_agent

    target = TargetPrompt().add_path("system_prompt", str(PROMPTS_DIR / "system.md"))
    run_dir = output_dir / "online_run"
    run_dir.mkdir(parents=True, exist_ok=True)
    print(f"[online] 启动 AgentOptimizer（真实 GEPA 优化），输出 {run_dir} ...")
    result = await AgentOptimizer.optimize(
        config_path=str(optimizer_path),
        call_agent=real_call_agent,
        target_prompt=target,
        train_dataset_path=str(TRAIN_EVALSET),
        validation_dataset_path=str(VAL_EVALSET),
        output_dir=str(run_dir),
        update_source=False,
        verbose=1,
    )
    print(f"[online] 完成：baseline_pass_rate={result.baseline_pass_rate:.3f} "
          f"best_pass_rate={result.best_pass_rate:.3f} rounds={result.total_rounds}")
    print(f"[online] SDK 原生结果：{run_dir}")
    print("[online] 完整 gate + 自定义 report 闭环见 --mode fake（无 API key 也可跑）。")
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="eval_optimize_loop pipeline")
    parser.add_argument("--mode", choices=["fake", "trace", "online"], default="fake")
    parser.add_argument("--output-dir", default=str(HERE / "sample_output"))
    parser.add_argument("--seed", type=int, default=SEED)
    args = parser.parse_args()

    gate_cfg = load_gate_config(GATE_JSON)
    eval_config = load_eval_config(OPTIMIZER_JSON)
    output_dir = Path(args.output_dir)
    command = f"python run_pipeline.py --mode {args.mode}"

    if args.mode == "online":
        _required = ("TRPC_AGENT_API_KEY", "TRPC_AGENT_BASE_URL", "TRPC_AGENT_MODEL_NAME")
        missing = [e for e in _required if not os.environ.get(e)]
        if missing:
            print(
                f"[online] 缺少环境变量 {missing}。用 --mode fake 跑确定性闭环。",
                file=sys.stderr,
            )
            return 1

    if args.mode in ("fake", "trace"):
        report = asyncio.run(run_fake(gate_cfg, eval_config, output_dir, command, args.mode))
    else:
        report = asyncio.run(run_online(gate_cfg, OPTIMIZER_JSON, output_dir, command))

    if report is None:
        # fake/trace None = 出错；online None = SDK 真实优化已完成（用原生结果）
        return 0 if args.mode == "online" else 1
    print(f"[闭环完成] status={report.status} selected={report.selected_candidate_id}")
    print(f"  报告: {output_dir / 'optimization_report.json'}")
    return 0 if report.status == "accept" else 2


if __name__ == "__main__":
    sys.exit(main())
