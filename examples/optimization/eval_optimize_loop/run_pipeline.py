# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Command-line entry point for the evaluation-optimization loop example."""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from pipeline import run_pipeline  # noqa: E402


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=("Run baseline evaluation, prompt optimization, validation "
                                                  "regression gates, and audit reporting."), )
    parser.add_argument(
        "--config",
        type=Path,
        default=HERE / "optimizer.json",
        help="Optimizer and pipeline configuration.",
    )
    parser.add_argument(
        "--train",
        type=Path,
        default=HERE / "train.evalset.json",
        help="Training evalset.",
    )
    parser.add_argument(
        "--validation",
        type=Path,
        default=HERE / "val.evalset.json",
        help="Validation evalset.",
    )
    parser.add_argument(
        "--prompt",
        type=Path,
        default=HERE / "prompts" / "system.md",
        help="Baseline prompt source.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Run output directory. Defaults to runs/<timestamp>.",
    )
    parser.add_argument(
        "--run-id",
        default=None,
        help="Stable run identifier for audit and tests.",
    )
    parser.add_argument(
        "--update-source",
        action="store_true",
        help="Write the selected candidate only when the external gate accepts it.",
    )
    return parser.parse_args()


async def main() -> None:
    args = _parse_args()
    timestamp = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    output_dir = args.output_dir or HERE / "runs" / timestamp
    report = await run_pipeline(
        config_path=args.config,
        train_path=args.train,
        validation_path=args.validation,
        prompt_path=args.prompt,
        output_dir=output_dir,
        run_id=args.run_id,
        update_source=args.update_source,
    )

    baseline = report["baseline"]
    selected = report["candidate"]
    print("Evaluation + Optimization Loop")
    print(f"Run ID: {report['run_id']}")
    print("Baseline: "
          f"train={baseline['train']['score']:.4f}, "
          f"validation={baseline['validation']['score']:.4f}")
    for item in report["rounds"]:
        print(f"Round {item['round']} ({item['id']}): "
              f"train={item['evaluation']['train']['score']:.4f}, "
              f"validation={item['evaluation']['validation']['score']:.4f}, "
              f"gate={item['gate_decision']['decision'].upper()}")
    print(f"Selected candidate: {selected['id']} "
          f"({report['gate_decision']['decision'].upper()})")
    print(f"Model calls: {report['cost']['model_calls']}")
    print(f"Estimated cost: ${report['cost']['total_usd']:.4f}")
    print(f"JSON report: {output_dir / 'optimization_report.json'}")
    print(f"Markdown report: {output_dir / 'optimization_report.md'}")


if __name__ == "__main__":
    asyncio.run(main())
