#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

try:
    from .pipeline import EvalOptimizePipeline  # noqa: E402
except ImportError:
    from pipeline import EvalOptimizePipeline  # type: ignore


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Eval + Optimize closed-loop pipeline",
    )
    parser.add_argument(
        "--pipeline-config",
        type=str,
        default=str(_HERE / "pipeline.json"),
        help="Path to pipeline.json (default: pipeline.json in same directory)",
    )
    args = parser.parse_args()

    pipeline = EvalOptimizePipeline.from_config(args.pipeline_config)
    result = await pipeline.run()

    output_dir = Path(pipeline._config.output_dir).resolve()
    print(f"\nPipeline complete: {result.gate_decision}")
    print(f"  Duration: {result.duration_seconds:.2f}s")
    print(f"  Reports: {output_dir}/")
    print(f"    optimization_report.json")
    print(f"    optimization_report.md")

    if result.gate_decision == "REJECT":
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
