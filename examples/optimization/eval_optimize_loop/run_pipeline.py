# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Run the evaluation and optimization loop example."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from pipeline import EvalOptimizePipeline  # noqa: E402

TRAIN_PATH = _HERE / "train.evalset.json"
VAL_PATH = _HERE / "val.evalset.json"
OPTIMIZER_PATH = _HERE / "optimizer.json"
GATE_PATH = _HERE / "gate.json"
OUTPUT_DIR = _HERE / "output"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run evaluation, optimization, candidate validation, and gate.")
    parser.add_argument(
        "--mode",
        choices=("real", "fake"),
        default="fake",
        help="real uses AgentOptimizer; fake uses deterministic local optimization.",
    )
    parser.add_argument(
        "--gate-config",
        default=str(GATE_PATH),
        help="Path to the example-private gate.json config.",
    )
    return parser.parse_args()


async def main() -> None:
    args = _parse_args()
    pipeline = EvalOptimizePipeline(
        train_evalset_path=TRAIN_PATH,
        val_evalset_path=VAL_PATH,
        optimizer_config_path=OPTIMIZER_PATH,
        gate_config_path=Path(args.gate_config),
        output_dir=OUTPUT_DIR,
        mode=args.mode,
    )
    report_paths = await pipeline.run()
    print(f"optimization_report.json written to: {report_paths.json_path}")
    print(f"optimization_report.md written to: {report_paths.markdown_path}")


if __name__ == "__main__":
    asyncio.run(main())
