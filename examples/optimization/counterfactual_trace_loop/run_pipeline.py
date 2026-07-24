"""Run the trust-aware counterfactual evaluation/optimization loop."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from examples.optimization.counterfactual_trace_loop.pipeline.pipeline import run_pipeline  # noqa: E402


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--mode", choices=["fake", "trace"], default="fake")
    p.add_argument("--candidate-profile", choices=["accepted", "overfit", "ineffective"], default="overfit")
    p.add_argument("--output-dir", type=Path, default=None)
    p.add_argument("--apply", action="store_true")
    a = p.parse_args()
    asyncio.run(
        run_pipeline(HERE, a.mode, (a.output_dir or HERE / "sample_output").resolve(), a.apply, a.candidate_profile)
    )


if __name__ == "__main__":
    main()
