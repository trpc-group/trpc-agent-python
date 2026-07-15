# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under the Apache License, Version 2.0.
"""Run the deterministic offline evaluation/candidate stage."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if __package__ in (None, ""):
    _REPO_ROOT = _HERE.parents[2]
    if str(_REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(_REPO_ROOT))
    from examples.optimization.eval_optimize_loop.pipeline import prepare_run
    from examples.optimization.eval_optimize_loop.pipeline import run_fake_stage
else:
    from .pipeline import prepare_run
    from .pipeline import run_fake_stage


def _format_snapshot(label: str, snapshot: object) -> str:
    score = getattr(snapshot, "average_score", None)
    score_text = "unavailable" if score is None else f"{score:.3f}"
    return (
        f"{label}: {snapshot.passed_case_count}/{snapshot.total_case_count} passed, "
        f"average score={score_text}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the deterministic offline eval/optimization stage.")
    parser.add_argument("--config", type=Path, default=_HERE / "pipeline.json")
    parser.add_argument("--run-id", help="Optional reproducible run identifier.")
    parser.add_argument(
        "--scenario",
        choices=("improve", "no_improvement", "overfit"),
        help="Override execution.fake_candidate_scenario for this run.",
    )
    args = parser.parse_args()

    prepared = prepare_run(args.config, run_id=args.run_id)
    result = asyncio.run(run_fake_stage(prepared, scenario=args.scenario))
    print(f"Completed deterministic pipeline stage: {prepared.workspace.run_dir}")
    print(f"Candidate: {result.candidate.candidate_id} ({result.scenario})")
    print(_format_snapshot("Baseline train", result.baseline_train))
    print(_format_snapshot("Baseline validation", result.baseline_validation))
    print(_format_snapshot("Candidate train", result.candidate_train))
    print(_format_snapshot("Candidate validation", result.candidate_validation))
    print("Stage 2 does not run Gate, write reports, or update source prompts.")


if __name__ == "__main__":
    main()
