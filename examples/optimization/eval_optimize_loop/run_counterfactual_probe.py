"""Run the minimal counterfactual trace feasibility probe."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from examples.optimization.eval_optimize_loop.pipeline.probe import (  # noqa: E402
    run_counterfactual_probe,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=HERE / "prototype_output",
        help="Directory for counterfactual_probe.json and counterfactual_probe.md",
    )
    args = parser.parse_args()
    report = asyncio.run(run_counterfactual_probe(args.output_dir.resolve()))
    if not report["feasibility"]["supported"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
