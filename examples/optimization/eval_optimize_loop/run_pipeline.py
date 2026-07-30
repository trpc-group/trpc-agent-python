# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Run the deterministic Evaluation + Optimization regression loop."""

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

from trpc_agent_sdk.evaluation import EvaluationOptimizationPipeline  # noqa: E402
from trpc_agent_sdk.evaluation import TargetPrompt  # noqa: E402

from fake_runtime import build_fake_call_agent  # noqa: E402
from fake_runtime import fake_optimizer_runner  # noqa: E402

CONFIG_PATH = _HERE / "optimizer.json"
TRAIN_PATH = _HERE / "train.evalset.json"
VALIDATION_PATH = _HERE / "val.evalset.json"
PROMPT_PATH = _HERE / "prompts" / "system.md"


async def run(output_dir: Path) -> None:
    """Execute the example without a model endpoint or API key."""
    target = TargetPrompt().add_path("system_prompt", str(PROMPT_PATH))
    report = await EvaluationOptimizationPipeline.run(
        config_path=str(CONFIG_PATH),
        target_prompt=target,
        train_dataset_path=str(TRAIN_PATH),
        validation_dataset_path=str(VALIDATION_PATH),
        output_dir=str(output_dir),
        call_agent=build_fake_call_agent(PROMPT_PATH),
        optimizer_runner=fake_optimizer_runner,
        verbose=0,
    )
    decision = "ACCEPT" if report.gate_decision.accepted else "REJECT"
    print(f"Decision: {decision}")
    print(f"JSON report: {output_dir / 'optimization_report.json'}")
    print(f"Markdown report: {output_dir / 'optimization_report.md'}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the offline Evaluation + Optimization regression loop.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=_HERE / "runs" / "latest",
        help="Artifact directory (default: examples/.../runs/latest).",
    )
    args = parser.parse_args()
    asyncio.run(run(args.output_dir.resolve()))


if __name__ == "__main__":
    main()
