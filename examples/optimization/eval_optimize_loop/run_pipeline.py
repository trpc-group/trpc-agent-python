# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""eval_optimize_loop 入口脚本：评测→归因→优化→回归→gate→审计 六阶段闭环。

适用场景
--------
你想知道「AgentOptimizer 改出来的 prompt 到底值不值得上线」：不是看优化器
自己报的分数，而是用独立验证集复评、逐 case 对比、跑一遍可配置的接受策略，
并把每一步产物落盘可审计。本脚本零 API Key 可跑（fake agent / fake judge /
fake reflection LM），完整三场景 < 3 分钟。

怎么跑
------
    # 单场景（默认 success）；输出在 runs/<场景>-<时间戳>/
    python examples/optimization/eval_optimize_loop/run_pipeline.py

    # 三个场景全跑（success 接受；no_effect / overfit 拒绝）
    python examples/optimization/eval_optimize_loop/run_pipeline.py --scenario all

    # baseline 用预录轨迹（trace 模式）做评测与归因，不执行 agent
    python examples/optimization/eval_optimize_loop/run_pipeline.py --baseline-from-trace

    # gate 通过时把最优候选写回源 prompt 文件（谨慎！会改动 loop_agent/prompts/；
    # --scenario all 时统一等全部场景跑完后再写回，避免污染后续场景 baseline）
    python examples/optimization/eval_optimize_loop/run_pipeline.py --apply

    # 校验一份已有报告的字段契约
    python examples/optimization/eval_optimize_loop/run_pipeline.py \
        --check sample_output/success/optimization_report.json

输出目录结构
------------
    runs/<场景>-<时间戳>/
    ├── optimization_report.json   # 结构化报告（AC6 契约）
    ├── optimization_report.md     # 人话版报告
    ├── baseline_eval.json         # 阶段① 逐 case 原始记录
    ├── candidate_eval.json        # 阶段④ 逐 case 原始记录
    ├── attribution.json           # 阶段② 归因明细
    ├── pipeline_config.snapshot.json  # 本次运行的 gate/seed 配置快照
    └── optimize/                  # 阶段③ SDK 原生审计目录：
        ├── result.json  summary.txt  run.log  config.snapshot.json
        ├── rounds/round_001.json …（每轮候选 prompt、接受理由、成本、耗时）
        └── baseline_prompts/  best_prompts/

接入自己业务时改哪里
--------------------
- loop_agent/           : 换成你的 agent 包（保留 get_agent_async + call_agent 两个入口）
- data/*.evalset.json   : 换成你的训练/验证评测集（验证集必须独立于训练集！）
- data/eval_config.json : 换成你的验收 metric 套件（judge_model 配真实模型）
- optimizer.json        : reflection_lm 配真实模型；黑盒模式只能用响应类 metric
- pipeline.json         : 按业务风险调整闸门（保护 case、预算、提升阈值）
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

# ---- 路径自举：让脚本在任意 cwd 下都能运行 ----
_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parents[2]
for _p in (str(_REPO_ROOT), str(_HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from trpc_agent_sdk.evaluation import TargetPrompt  # noqa: E402

import loop_agent  # noqa: E402
from loop_agent.fake_models import FakeAgentModel, FakeJudgeModel, FakeReflectionModel  # noqa: E402
from loop_pipeline.attribution import cluster  # noqa: E402
from loop_pipeline.config import PipelineConfig  # noqa: E402
from loop_pipeline.evaluate import run_eval  # noqa: E402
from loop_pipeline.gates import evaluate_gates  # noqa: E402
from loop_pipeline.optimize import SCENARIOS, resolve_scenario, run_optimization  # noqa: E402
from loop_pipeline.regression import compute_delta, evaluate_candidate  # noqa: E402
from loop_pipeline.report import build_report, validate_report, write_reports  # noqa: E402

TRAIN_PATH = _HERE / "data" / "train.evalset.json"
VAL_PATH = _HERE / "data" / "val.evalset.json"
TRACE_PATH = _HERE / "data" / "trace_baseline.evalset.json"
EVAL_CONFIG_PATH = _HERE / "data" / "eval_config.json"
PIPELINE_CONFIG_PATH = _HERE / "pipeline.json"


def _build_target() -> TargetPrompt:
    """注册两个 TargetPrompt 字段：system prompt + skill prompt。"""
    return (TargetPrompt().add_path("system_prompt",
                                    str(loop_agent.SYSTEM_PROMPT_PATH)).add_path("skill", str(loop_agent.SKILL_PATH)))


def _records_json(records_by_split: dict) -> dict:
    return {
        split: {
            eval_id: asdict(rec)
            for eval_id, rec in records.items()
        }
        for split, records in records_by_split.items()
    }


def _dump(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


async def run_scenario(
    scenario: str,
    output_root: Path,
    *,
    baseline_from_trace: bool = False,
    apply_on_accept: bool = False,
    apply_sink: list | None = None,
    quiet: bool = False,
) -> dict:
    """跑一个场景的完整六阶段闭环，返回报告 dict。"""

    def log(message: str) -> None:
        if not quiet:
            print(message)

    pipeline_config = PipelineConfig.load(PIPELINE_CONFIG_PATH)
    spec = resolve_scenario(scenario, _HERE)
    timestamp = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    out_dir = output_root / f"{scenario}-{timestamp}"
    out_dir.mkdir(parents=True, exist_ok=True)

    calls_before = {
        "agent": FakeAgentModel.calls,
        "judge": FakeJudgeModel.calls,
        "reflection": FakeReflectionModel.calls,
    }
    stage_durations: dict[str, float] = {}
    started = time.monotonic()

    # ---- 阶段①：baseline 评测（train + val，完整验收 metric 套件） ----
    log(f"[{scenario}] ① baseline 评测 …")
    t0 = time.monotonic()
    if baseline_from_trace:
        # trace 模式演示：评测 + 归因直接跑在预录轨迹上（不执行 agent）。
        # 回归对比仍使用真实 train/val 集，保证 delta 口径一致。
        trace_records = await run_eval(str(TRACE_PATH), str(EVAL_CONFIG_PATH), agent_module=None)
        _dump(out_dir / "trace_eval.json", {eval_id: asdict(rec) for eval_id, rec in trace_records.items()})
        log(f"[{scenario}]   trace 快照评测：{sum(r.passed for r in trace_records.values())}"
            f"/{len(trace_records)} 通过（明细见 trace_eval.json）")
        trace_attribution = cluster(trace_records)
        _dump(out_dir / "trace_attribution.json", {
            eval_id: [asdict(f) for f in findings]
            for eval_id, findings in trace_attribution.per_case.items()
        })
        log(f"[{scenario}]   trace 快照归因：{trace_attribution.counts}（明细见 trace_attribution.json）")
    baseline = {
        "train": await run_eval(str(TRAIN_PATH), str(EVAL_CONFIG_PATH)),
        "val": await run_eval(str(VAL_PATH), str(EVAL_CONFIG_PATH)),
    }
    stage_durations["baseline_eval"] = round(time.monotonic() - t0, 3)

    # ---- 阶段②：失败归因 ----
    t0 = time.monotonic()
    attribution_train = cluster(baseline["train"])
    attribution_val = cluster(baseline["val"])
    stage_durations["attribution"] = round(time.monotonic() - t0, 3)
    log(f"[{scenario}] ② 失败归因：train={attribution_train.counts} val={attribution_val.counts}")

    # ---- 阶段③：优化执行（AgentOptimizer / GEPA） ----
    log(f"[{scenario}] ③ AgentOptimizer 优化（配置 {spec.optimizer_config.name}，"
        f"优化器验证集 {spec.optimizer_val_dataset.name}）…")
    t0 = time.monotonic()
    target = _build_target()
    optimize_result = await run_optimization(
        spec,
        call_agent=loop_agent.call_agent,
        target=target,
        output_dir=out_dir / "optimize",
    )
    stage_durations["optimize"] = round(time.monotonic() - t0, 3)
    log(f"[{scenario}]   优化器视角：{optimize_result.baseline_pass_rate:.3f} → "
        f"{optimize_result.best_pass_rate:.3f}（status={optimize_result.status}，"
        f"{optimize_result.total_rounds} 轮）")

    # ---- 阶段④：候选回归（独立 train/val 复评 + 逐 case delta） ----
    log(f"[{scenario}] ④ 候选回归（独立验证集复评）…")
    t0 = time.monotonic()
    candidate = await evaluate_candidate(
        target,
        optimize_result.best_prompts,
        {
            "train": str(TRAIN_PATH),
            "val": str(VAL_PATH)
        },
        str(EVAL_CONFIG_PATH),
    )
    delta_train = compute_delta(baseline["train"], candidate["train"], pipeline_config.score_epsilon)
    delta_val = compute_delta(baseline["val"], candidate["val"], pipeline_config.score_epsilon)
    stage_durations["candidate_regression"] = round(time.monotonic() - t0, 3)

    # ---- 阶段⑤：接受策略（六道闸门） ----
    wall_seconds = time.monotonic() - started
    last_round = optimize_result.rounds[-1] if optimize_result.rounds else None
    decision = evaluate_gates(
        pipeline_config.gates,
        delta_val=delta_val,
        delta_train=delta_train,
        optimize_result_view={
            "total_llm_cost": optimize_result.total_llm_cost,
            "budget_used": last_round.budget_used if last_round else None,
            "duration_seconds": optimize_result.duration_seconds,
        },
        wall_seconds=wall_seconds,
    )
    log(f"[{scenario}] ⑤ gate 决策：{'ACCEPT' if decision.accepted else 'REJECT'} —— {decision.reason}")

    # ---- 阶段⑥：审计落盘 ----
    runtime = {
        "pipeline_duration_seconds": round(time.monotonic() - started, 3),
        "stage_durations": stage_durations,
        "baseline_from_trace": baseline_from_trace,
        "fake_model_calls": {
            "agent": FakeAgentModel.calls - calls_before["agent"],
            "judge": FakeJudgeModel.calls - calls_before["judge"],
            "reflection": FakeReflectionModel.calls - calls_before["reflection"],
        },
    }
    report = build_report(
        scenario=scenario,
        seed=pipeline_config.seed,
        inputs={
            "train_evalset": str(TRAIN_PATH.relative_to(_HERE)),
            "val_evalset": str(VAL_PATH.relative_to(_HERE)),
            "optimizer_config": str(spec.optimizer_config.relative_to(_HERE)),
            "optimizer_val_dataset": str(spec.optimizer_val_dataset.relative_to(_HERE)),
            "eval_config": str(EVAL_CONFIG_PATH.relative_to(_HERE)),
            "pipeline_config": str(PIPELINE_CONFIG_PATH.relative_to(_HERE)),
            "prompt_sources": {
                "system_prompt": str(loop_agent.SYSTEM_PROMPT_PATH.relative_to(_HERE)),
                "skill": str(loop_agent.SKILL_PATH.relative_to(_HERE)),
            },
        },
        baseline=baseline,
        attribution_train=attribution_train,
        attribution_val=attribution_val,
        optimize_result=optimize_result,
        candidate=candidate,
        delta_train=delta_train,
        delta_val=delta_val,
        decision=decision,
        runtime=runtime,
        # 相对于报告所在目录的路径：报告与审计产物同目录移动/归档时仍然有效
        optimize_artifacts_dir="optimize/",
    )

    problems = validate_report(report)
    if problems:  # pragma: no cover - 契约破坏应立即暴露
        raise RuntimeError(f"报告契约校验失败：{problems}")

    json_path, md_path = write_reports(out_dir, report)
    _dump(out_dir / "baseline_eval.json", _records_json(baseline))
    _dump(out_dir / "candidate_eval.json", _records_json(candidate))
    _dump(
        out_dir / "attribution.json", {
            "train": {
                eid: [asdict(f) for f in findings]
                for eid, findings in attribution_train.per_case.items()
            },
            "val": {
                eid: [asdict(f) for f in findings]
                for eid, findings in attribution_val.per_case.items()
            },
        })
    _dump(out_dir / "pipeline_config.snapshot.json", pipeline_config.model_dump())

    # --apply：gate 通过时把最优候选写回源 prompt（默认关闭）。
    # 提供 apply_sink 时只登记不写盘（defer-then-apply）：--scenario all 必须等
    # 全部场景跑完后统一写回，否则先接受场景的写回会污染后续场景的 baseline。
    if apply_on_accept and decision.accepted:
        if apply_sink is not None:
            apply_sink.append({"scenario": scenario, "target": target, "prompts": optimize_result.best_prompts})
            log(f"[{scenario}] --apply：gate 通过，候选已登记，待全部场景结束后统一写回")
        else:
            await target.write_all(optimize_result.best_prompts)
            log(f"[{scenario}] --apply：已把最优候选写回源 prompt 文件（loop_agent/prompts/）")

    log(f"[{scenario}] ⑥ 报告已生成：{json_path}\n")
    return report


async def _amain(args: argparse.Namespace) -> int:
    output_root = Path(args.output).resolve() if args.output else (_HERE / "runs")
    scenarios = list(SCENARIOS) if args.scenario == "all" else [args.scenario]
    results = []
    pending_applies: list[dict] = []
    for scenario in scenarios:
        report = await run_scenario(
            scenario,
            output_root,
            baseline_from_trace=args.baseline_from_trace,
            apply_on_accept=args.apply,
            apply_sink=pending_applies,
            quiet=args.quiet,
        )
        results.append((scenario, report))
    # --apply 统一延后到全部场景结束（defer-then-apply）：
    # 避免 --scenario all 时先接受场景的写回污染后续场景的 baseline 评测。
    for pending in pending_applies:
        await pending["target"].write_all(pending["prompts"])
        if not args.quiet:
            print(f"[{pending['scenario']}] --apply：已把最优候选写回源 prompt 文件（loop_agent/prompts/）")
    if not args.quiet:
        print("=" * 72)
        for scenario, report in results:
            decision = report["gate_decision"]
            verdict = "ACCEPT ✅" if decision["accepted"] else "REJECT ❌"
            print(f"{scenario:>10}: {verdict}  val Δ通过率 {report['delta']['val']['pass_rate_delta']:+.3f}  "
                  f"—— {decision['reason'][:60]}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluation + Optimization 自动回归与提示词优化闭环")
    parser.add_argument("--scenario",
                        choices=[*SCENARIOS, "all"],
                        default="success",
                        help="演示场景：success 接受 / no_effect、overfit 拒绝；all 顺序全跑")
    parser.add_argument("--output", default=None, help="输出根目录（默认 example 下的 runs/）")
    parser.add_argument("--baseline-from-trace",
                        action="store_true",
                        help="额外用预录轨迹（trace 模式）跑一遍 baseline 评测与归因，不执行 agent")
    parser.add_argument("--apply",
                        action="store_true",
                        help="gate 通过时把最优候选写回源 prompt 文件（会修改 loop_agent/prompts/；"
                        "--scenario all 时等全部场景结束后统一写回）")
    parser.add_argument("--quiet", action="store_true", help="静默模式")
    parser.add_argument("--check", metavar="REPORT_JSON", default=None, help="只校验一份 optimization_report.json 的字段契约后退出")
    args = parser.parse_args()

    if args.check:
        # cwd 无关：相对路径先按当前目录解析，找不到再回退到 example 目录，
        # 让 README 里从仓库根目录复制的命令直接可用；文件缺失给友好错误。
        check_path = Path(args.check)
        if not check_path.is_file() and not check_path.is_absolute() and (_HERE / check_path).is_file():
            check_path = _HERE / check_path
        if not check_path.is_file():
            print(f"报告文件不存在：{args.check}（已按当前目录与 example 目录 {_HERE} 解析）", file=sys.stderr)
            return 1
        report = json.loads(check_path.read_text(encoding="utf-8"))
        problems = validate_report(report)
        if problems:
            print("报告契约校验失败：")
            for problem in problems:
                print(f"  - {problem}")
            return 1
        print(f"报告契约校验通过：{check_path}")
        return 0

    return asyncio.run(_amain(args))


if __name__ == "__main__":
    raise SystemExit(main())
