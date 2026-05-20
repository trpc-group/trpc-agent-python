# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""跑完 baseline + advanced 后用本脚本对比 result.json，输出对比表。

适用场景
--------
高阶策略 A/B 对照实验的分析端。先跑 run_baseline.py + run_advanced.py，
再跑本脚本：自动选取 runs/ 下最新的 baseline_* 与 advanced_* 目录解析
result.json，按多维度对比表输出。

result.json 字段命名注意
------------------------
SDK 内部 snake_case，但序列化到 result.json 时通过 alias_generator 转换为
camelCase。本脚本按 camelCase 索引（stopReason / totalRounds / bestPassRate
等）。自有脚本读 result.json 时同样按此约定。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


_HERE = Path(__file__).resolve().parent
RUNS_DIR = _HERE / "runs"


def _latest(prefix: str) -> Path | None:
    """挑出 runs/<prefix>_* 中最新一次的目录。"""
    candidates = sorted(RUNS_DIR.glob(f"{prefix}_*"))
    return candidates[-1] if candidates else None


def _load(run_dir: Path) -> dict:
    """读 result.json；缺失返回空 dict。"""
    rj = run_dir / "result.json"
    if not rj.exists():
        return {}
    return json.loads(rj.read_text(encoding="utf-8"))


def _short(d: dict) -> dict:
    """从完整 result.json 中抽取本次对比关注的维度。

    维度选择原则：能直接反映高阶策略行为差异的字段（轮次接受率、merge
    触发次数、reflection LM 调用数等），而非仅最终 best_pass_rate
    （高阶策略往往与 baseline 收敛到相近最终分数，差异在到达路径上）。
    """
    rounds = d.get("rounds") or []
    accepted = sum(1 for r in rounds if r.get("accepted"))
    merge_total = sum(1 for r in rounds if r.get("kind") == "merge")
    merge_accepted = sum(1 for r in rounds if r.get("kind") == "merge" and r.get("accepted"))
    return {
        "stop_reason": d.get("stopReason"),
        "finish_reason": d.get("finishReason"),
        "duration_s": round(d.get("durationSeconds") or 0.0, 1),
        "total_rounds": d.get("totalRounds"),
        "rounds_accepted": accepted,
        "merge_rounds_total": merge_total,
        "merge_rounds_accepted": merge_accepted,
        "reflection_lm_calls": d.get("totalReflectionLmCalls"),
        "baseline_pass_rate": d.get("baselinePassRate"),
        "best_pass_rate": d.get("bestPassRate"),
    }


def main() -> int:
    """读两次最新 run，输出对比表。"""
    base = _latest("baseline")
    adv = _latest("advanced")
    if base is None or adv is None:
        print(
            "Need both baseline_* and advanced_* runs in runs/. "
            "Run run_baseline.py and run_advanced.py first.",
            file=sys.stderr,
        )
        return 1

    print(f"baseline run : {base.name}")
    print(f"advanced run : {adv.name}\n")

    a = _short(_load(base))
    b = _short(_load(adv))
    keys = list(a.keys())
    width = max(len(k) for k in keys) + 2
    print(f"{'metric'.ljust(width)}{'baseline'.rjust(18)}{'advanced'.rjust(18)}")
    print("-" * (width + 36))
    for k in keys:
        va = a.get(k)
        vb = b.get(k)
        print(f"{k.ljust(width)}{str(va).rjust(18)}{str(vb).rjust(18)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
