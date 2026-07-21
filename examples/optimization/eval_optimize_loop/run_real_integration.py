# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under the Apache License, Version 2.0.
"""显式运行真实业务模型与真实反思模型的完整回归闭环。"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from pydantic import ValidationError

_HERE = Path(__file__).resolve().parent
if __package__ in (None, ""):
    _REPO_ROOT = _HERE.parents[2]
    if str(_REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(_REPO_ROOT))
    from examples.optimization.eval_optimize_loop.config import load_pipeline_config
    from examples.optimization.eval_optimize_loop.pipeline import prepare_run
    from examples.optimization.eval_optimize_loop.pipeline import run_real_stage
    from examples.optimization.eval_optimize_loop.real_agent import BusinessModelConfig
    from examples.optimization.eval_optimize_loop.real_agent import RealBusinessAgent
    from examples.optimization.eval_optimize_loop.real_agent import load_business_model_config
    from examples.optimization.eval_optimize_loop.report_builder import redact_error_message
    from examples.optimization.eval_optimize_loop.schemas import OptimizerRuntimeParameters
else:
    from .config import load_pipeline_config
    from .pipeline import prepare_run
    from .pipeline import run_real_stage
    from .real_agent import BusinessModelConfig
    from .real_agent import RealBusinessAgent
    from .real_agent import load_business_model_config
    from .report_builder import redact_error_message
    from .schemas import OptimizerRuntimeParameters


def _think_value(value: str) -> bool | None:
    return {"auto": None, "on": True, "off": False}[value]


def _format_snapshot(label: str, snapshot: object) -> str:
    score = getattr(snapshot, "average_score", None)
    score_text = "unavailable" if score is None else f"{score:.3f}"
    return (
        f"{label}: {snapshot.passed_case_count}/{snapshot.total_case_count} passed, "
        f"average score={score_text}"
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the explicitly enabled real-model pipeline.")
    parser.add_argument("--run-real", action="store_true", help="Confirm that real API calls are intended.")
    parser.add_argument("--config", type=Path, default=_HERE / "pipeline.real.json")
    parser.add_argument("--run-id")
    parser.add_argument("--optimizer-model-name", required=True)
    parser.add_argument("--optimizer-provider-name", default="openai")
    parser.add_argument("--optimizer-temperature", type=float, default=0.8)
    parser.add_argument("--optimizer-max-tokens", type=int, default=4096)
    parser.add_argument("--optimizer-think", choices=("auto", "on", "off"), default="auto")
    parser.add_argument("--max-candidate-proposals", type=int, default=1)
    return parser


async def _run(
    args: argparse.Namespace,
    business_config: BusinessModelConfig,
    parameters: OptimizerRuntimeParameters,
):
    prepared = prepare_run(args.config, run_id=args.run_id)
    source_before = await prepared.source_target.read_all()
    agent = RealBusinessAgent(prepared.working_target, business_config)
    try:
        result = await run_real_stage(
            prepared,
            call_agent=agent.call_agent,
            optimizer_parameters=parameters,
        )
    except Exception as exc:
        source_after = await prepared.source_target.read_all()
        if source_after != source_before:
            raise RuntimeError(
                "source Prompt changed during a failed real integration run"
            ) from exc
        raise
    source_after = await prepared.source_target.read_all()
    if source_after != source_before:
        raise RuntimeError("source Prompt changed even though real integration writeback is disabled")
    return prepared, result


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    if not args.run_real:
        parser.error("real API calls require explicit --run-real confirmation")
    try:
        business_config = load_business_model_config()
        parameters = OptimizerRuntimeParameters(
            provider_name=args.optimizer_provider_name,
            model_name=args.optimizer_model_name,
            temperature=args.optimizer_temperature,
            max_tokens=args.optimizer_max_tokens,
            think=_think_value(args.optimizer_think),
            max_candidate_proposals=args.max_candidate_proposals,
        )
        config = load_pipeline_config(args.config)
    except (OSError, ValueError, ValidationError) as exc:
        parser.error(str(exc))
    if config.execution.mode != "real":
        parser.error("real integration requires execution.mode='real'")
    if config.writeback.enabled:
        parser.error("real integration requires writeback.enabled=false")

    try:
        prepared, result = asyncio.run(_run(args, business_config, parameters))
    except Exception as exc:
        print(
            f"Real integration failed: {redact_error_message(exc)}",
            file=sys.stderr,
        )
        return 1

    print(f"Completed real-model pipeline: {prepared.workspace.run_dir}")
    print(_format_snapshot("Baseline train", result.baseline_train))
    print(_format_snapshot("Baseline validation", result.baseline_validation))
    print(_format_snapshot("Candidate train", result.candidate_train))
    print(_format_snapshot("Candidate validation", result.candidate_validation))
    print(
        f"Optimizer: {result.optimize_result.status}, "
        f"rounds={result.optimize_result.total_rounds}, candidate={result.candidate.candidate_id}"
    )
    print(f"Gate decision: {result.gate_decision.decision.upper()}")
    for reason in result.gate_decision.rejection_reasons:
        print(f"- {reason}")
    print(f"Writeback: {result.writeback.status.upper()} ({result.writeback.reason})")
    print("Source Prompt unchanged: yes")
    report_dir = Path(prepared.workspace.run_dir) / "report"
    print(f"JSON report: {report_dir / 'optimization_report.json'}")
    print(f"Markdown report: {report_dir / 'optimization_report.md'}")
    print(f"Artifact index: {report_dir / 'artifact_index.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
