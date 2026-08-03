# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Pipeline 入口：组装配置、运行闭环、落盘审计产物。

两种后端（用环境变量 EVAL_BACKEND 切换，默认 fake）：
  - fake：确定性 fake_agent + 确定性 contains 匹配，无需任何 API Key（验收主路径）。
  - real：真实 LLM（hy3）生成 + llm_rubric_response 让 hy3 当 judge，需配置
          TRPC_AGENT_API_KEY / TRPC_AGENT_BASE_URL / TRPC_AGENT_MODEL_NAME
          （参考 .env；先把三项填入 eval_optimize_loop/.env）。

运行：
    # fake（无需 Key）
    python examples/optimization/eval_optimize_loop/run_pipeline.py
    # real（需 hy3 Key）
    EVAL_BACKEND=real python examples/optimization/eval_optimize_loop/run_pipeline.py

产物：
    artifacts/optimization_report.json   结构化报告（baseline/candidate/delta/gate/归因）
    artifacts/optimization_report.md     人读的接受决策说明
    artifacts/candidates/<label>.md      每个候选 prompt 快照（审计落盘）
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

from dotenv import load_dotenv

_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parents[2]
for p in (str(_REPO_ROOT), str(_HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

# 加载 .env（真实模式下提供 hy3 凭据；fake 模式忽略）。
for _cand in (_HERE / ".env", _HERE.parent / ".env", Path(".env")):
    if _cand.exists():
        load_dotenv(_cand, override=True)
        break

from pipeline import EvalLoopPipeline  # noqa: E402

BACKEND = os.getenv("EVAL_BACKEND", "fake").strip().lower()

if BACKEND == "real":
    if os.getenv("REAL_SMOKE", "").strip().lower() in ("1", "true", "yes"):
        # 轻量变体：更少 case/候选，用于在 hy3 配额/限流较紧时验证完整真实闭环。
        CONFIG = _HERE / "config" / "real_optimizer_smoke.json"
        BASELINE = _HERE / "prompts" / "baseline_real.md"
        TRAIN = _HERE / "data" / "real_smoke" / "train.evalset.json"
        VAL = _HERE / "data" / "real_smoke" / "val.evalset.json"
    else:
        CONFIG = _HERE / "config" / "real_optimizer.json"
        BASELINE = _HERE / "prompts" / "baseline_real.md"
        TRAIN = _HERE / "data" / "real" / "train.evalset.json"
        VAL = _HERE / "data" / "real" / "val.evalset.json"
else:
    CONFIG = _HERE / "config" / "optimizer.json"
    BASELINE = _HERE / "prompts" / "baseline_system.md"
    TRAIN = _HERE / "data" / "train.evalset.json"
    VAL = _HERE / "data" / "val.evalset.json"
OUTPUT = _HERE / "artifacts"


def _resolve_config_for_real(path: Path) -> str:
    """真实模式：把 ${TRPC_AGENT_*} 占位符展开为环境变量值，写到临时文件返回路径。"""
    raw = path.read_text(encoding="utf-8")
    expanded = os.path.expandvars(raw)
    if "${" in expanded:
        missing = [m for m in ("TRPC_AGENT_API_KEY", "TRPC_AGENT_BASE_URL", "TRPC_AGENT_MODEL_NAME")
                   if not os.getenv(m)]
        if missing:
            raise RuntimeError(f"真实模式缺少环境变量：{missing}（请在 eval_optimize_loop/.env 中配置）")
    tmp = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
    tmp.write(expanded)
    tmp.close()
    return tmp.name


def _render_md(report: dict) -> str:
    """把结构化报告渲染成人读 Markdown。"""
    meta = report["meta"]
    base = report["baseline"]
    lines: list[str] = []
    lines.append("# 评测 - 优化闭环报告（optimization_report）\n")
    lines.append(f"- 模式: `{meta['mode']}`")
    lines.append(f"- 复现种子: `{meta['seed']}`")
    lines.append(f"- 耗时: `{meta['duration_seconds']}s`")
    lines.append(f"- 配置: `{meta['config_path']}`\n")

    lines.append("## 1. Baseline 评测")
    lines.append(f"- 训练集通过率: **{base['train_pass_rate']}**")
    lines.append(f"- 验证集通过率: **{base['val_pass_rate']}**")
    lines.append("\n### 逐 case（baseline）")
    for eid, c in base["train_cases"].items():
        lines.append(f"- train `{eid}`: {'PASS' if c['pass'] else 'FAIL'} (score={c['score']})")
    for eid, c in base["val_cases"].items():
        lines.append(f"- val `{eid}`: {'PASS' if c['pass'] else 'FAIL'} (score={c['score']})")

    lines.append("\n### 失败归因统计（baseline）")
    for ftype, n in base["failure_attribution"].items():
        if n:
            lines.append(f"- `{ftype}`: {n}")

    lines.append("\n## 2. 候选验证与 Gate 决策")
    for cand in report["candidates"]:
        g = cand["gate"]
        decision = "✅ 接受" if g["accept"] else "❌ 拒绝"
        lines.append(f"\n### 候选 `{cand['label']}` — {decision}")
        lines.append(f"- 训练集通过率: {cand['train_pass_rate']} | 验证集通过率: {cand['val_pass_rate']}")
        lines.append(f"- 验证集: {g['val_score_before']} -> {g['val_score_after']} "
                     f"(improvement={g['improvement']})")
        for reason in g["reasons"]:
            lines.append(f"  - {reason}")
        for d in g["case_deltas"]:
            if d["delta"] in ("new_pass", "new_fail", "kept_fail"):
                lines.append(f"  - case `{d['eval_id']}`: {d['delta']} "
                             f"(base={'P' if d['baseline_pass'] else 'F'}, "
                             f"cand={'P' if d['candidate_pass'] else 'F'})")

    lines.append("\n## 3. 最终决策")
    if report["accepted_candidate"]:
        lines.append(f"- **接受候选**: `{report['accepted_candidate']}`")
        d = report["delta"]
        lines.append(f"- 训练集 Δ: {d['train_pass_rate']} | 验证集 Δ: {d['val_pass_rate']}")
    else:
        lines.append("- **未接受任何候选**：所有候选均未通过 gate，建议保留 baseline 或补充训练样本。")

    if report["rejection_summary"]:
        lines.append("\n### 被拒绝候选汇总")
        for r in report["rejection_summary"]:
            lines.append(f"- `{r['label']}` (val={r['val_pass_rate']}): " + "; ".join(r["reasons"]))

    lines.append("\n## 4. 是否值得回写生产？")
    if report["accepted_candidate"]:
        lines.append(f"候选 `{report['accepted_candidate']}` 在验证集上提升且无关键 case "
                     f"退化、无新增 hard fail，建议人工复核后回写 `prompts/baseline_system.md`。")
    else:
        lines.append("当前没有候选比 baseline 更优，不建议回写；优先扩充评测覆盖或修正优化器候选池。")
    return "\n".join(lines) + "\n"


async def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)

    config_path = str(CONFIG)
    if BACKEND == "real":
        config_path = _resolve_config_for_real(CONFIG)

    pipeline = EvalLoopPipeline(
        optimizer_path=config_path,
        baseline_prompt_path=str(BASELINE),
        train_path=str(TRAIN),
        val_path=str(VAL),
        output_dir=str(OUTPUT),
        backend=BACKEND,
    )
    report = await pipeline.run()

    # 审计落盘：结构化报告
    json_path = OUTPUT / "optimization_report.json"
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    # 人读报告
    md_path = OUTPUT / "optimization_report.md"
    md_path.write_text(_render_md(report), encoding="utf-8")

    # 每轮候选 prompt 快照（审计）
    cand_dir = OUTPUT / "candidates"
    cand_dir.mkdir(exist_ok=True)
    for cand in report["candidates"]:
        (cand_dir / f"{cand['label']}.md").write_text(cand["prompt"], encoding="utf-8")

    # 复现配置快照
    import shutil
    shutil.copy(config_path, OUTPUT / "optimizer.snapshot.json")

    print(f"[OK] report -> {json_path}")
    print(f"[OK] report -> {md_path}")
    print(f"[OK] candidates -> {cand_dir}")
    print(f"backend = {BACKEND}")
    print(f"accepted_candidate = {report['accepted_candidate']}")


if __name__ == "__main__":
    asyncio.run(main())
