# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under the Apache License, Version 2.0.
"""Run the deterministic offline evaluation, candidate, analysis, and Gate stage."""

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
    from examples.optimization.eval_optimize_loop.config import load_pipeline_config
else:
    from .config import load_pipeline_config
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
    parser = argparse.ArgumentParser(
        description="Run the deterministic offline evaluation and Gate stage."
    )
    parser.add_argument("--config", type=Path, default=_HERE / "pipeline.json")
    parser.add_argument("--run-id", help="Optional reproducible run identifier.")
    parser.add_argument(
        "--scenario",
        choices=("improve", "no_improvement", "overfit"),
        help="Override execution.fake_candidate_scenario for this run.",
    )
    args = parser.parse_args()

    config = load_pipeline_config(args.config)
    if config.execution.mode == "real":
        parser.error(
            "real mode requires an injected business agent; use the Python API "
            "run_real_stage(prepared, call_agent=...)"
        )
    if config.execution.mode != "fake":
        parser.error("this CLI currently supports execution.mode='fake' only")

    prepared = prepare_run(args.config, run_id=args.run_id)
    result = asyncio.run(run_fake_stage(prepared, scenario=args.scenario))
    print(f"Completed deterministic pipeline stage: {prepared.workspace.run_dir}")
    print(f"Candidate: {result.candidate.candidate_id} ({result.scenario})")
    print(_format_snapshot("Baseline train", result.baseline_train))
    print(_format_snapshot("Baseline validation", result.baseline_validation))
    print(_format_snapshot("Candidate train", result.candidate_train))
    print(_format_snapshot("Candidate validation", result.candidate_validation))
    print(f"Gate decision: {result.gate_decision.decision.upper()}")
    rejected_rules = [
        rule for rule in result.gate_decision.rule_results if rule.outcome == "reject"
    ]
    if rejected_rules:
        print("Rejection reasons:")
        for rule in rejected_rules:
            print(f"- [{rule.rule_id}] {rule.message}")
    if result.gate_decision.warnings:
        print("Warnings:")
        for warning in result.gate_decision.warnings:
            print(f"- {warning}")
    print(
        f"Writeback: {result.writeback.status.upper()} "
        f"({result.writeback.reason})"
    )
    print("Stage 4 does not write optimization reports; reporting is added in Stage 5.")


if __name__ == "__main__":
    main()
