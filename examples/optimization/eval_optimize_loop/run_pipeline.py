# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under the Apache License, Version 2.0.
"""Run the offline, trace, or explicitly enabled real optimization loop."""

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
    from examples.optimization.eval_optimize_loop.agent.agent import BusinessModelConfig
    from examples.optimization.eval_optimize_loop.agent.agent import RealBusinessAgent
    from examples.optimization.eval_optimize_loop.agent.agent import load_business_model_config
    from examples.optimization.eval_optimize_loop.core.pipeline import prepare_run
    from examples.optimization.eval_optimize_loop.core.pipeline import run_offline_stage
    from examples.optimization.eval_optimize_loop.core.pipeline import run_real_stage
    from examples.optimization.eval_optimize_loop.core.pipeline import run_trace_stage
    from examples.optimization.eval_optimize_loop.core.reporting import redact_error_message
    from examples.optimization.eval_optimize_loop.data.config import load_pipeline_config
    from examples.optimization.eval_optimize_loop.data.schemas import OptimizerRuntimeParameters
else:
    from .agent.agent import BusinessModelConfig
    from .agent.agent import RealBusinessAgent
    from .agent.agent import load_business_model_config
    from .core.pipeline import prepare_run
    from .core.pipeline import run_offline_stage
    from .core.pipeline import run_real_stage
    from .core.pipeline import run_trace_stage
    from .core.reporting import redact_error_message
    from .data.config import load_pipeline_config
    from .data.schemas import OptimizerRuntimeParameters


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
    parser = argparse.ArgumentParser(
        description="Run the evaluation and prompt-optimization loop."
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=_HERE / "configs" / "offline.json",
        help="Pipeline config; defaults to the deterministic offline mode.",
    )
    parser.add_argument("--run-id", help="Optional reproducible run identifier.")
    parser.add_argument(
        "--scenario",
        choices=("improve", "no_improvement", "overfit"),
        help="Override execution.candidate_scenario for this run.",
    )
    real = parser.add_argument_group("real mode")
    real.add_argument(
        "--run-real",
        action="store_true",
        help="Confirm that real API calls and their cost are intended.",
    )
    real.add_argument("--optimizer-model-name")
    real.add_argument("--optimizer-provider-name")
    real.add_argument("--optimizer-temperature", type=float)
    real.add_argument("--optimizer-max-tokens", type=int)
    real.add_argument(
        "--optimizer-think",
        choices=("auto", "on", "off"),
    )
    real.add_argument("--max-candidate-proposals", type=int)
    return parser


def _optimizer_parameters(
    args: argparse.Namespace,
    parser: argparse.ArgumentParser,
) -> OptimizerRuntimeParameters:
    if not args.optimizer_model_name:
        parser.error("real mode requires --optimizer-model-name")
    return OptimizerRuntimeParameters(
        provider_name=args.optimizer_provider_name or "openai",
        model_name=args.optimizer_model_name,
        temperature=(
            0.8 if args.optimizer_temperature is None else args.optimizer_temperature
        ),
        max_tokens=(
            4096
            if args.optimizer_max_tokens is None
            else args.optimizer_max_tokens
        ),
        think=_think_value(args.optimizer_think or "auto"),
        max_candidate_proposals=(
            1
            if args.max_candidate_proposals is None
            else args.max_candidate_proposals
        ),
    )


def _optimizer_options_supplied(args: argparse.Namespace) -> bool:
    """Return whether any real-only optimizer option was explicitly supplied."""
    return any(
        value is not None
        for value in (
            args.optimizer_model_name,
            args.optimizer_provider_name,
            args.optimizer_temperature,
            args.optimizer_max_tokens,
            args.optimizer_think,
            args.max_candidate_proposals,
        )
    )


async def _run_real(
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
        raise RuntimeError(
            "source Prompt changed even though real integration writeback is disabled"
        )
    return prepared, result


def _print_result(mode: str, prepared: object, result: object) -> None:
    print(f"Completed {mode} pipeline: {prepared.workspace.run_dir}")
    candidate_line = f"Candidate: {result.candidate.candidate_id}"
    scenario = getattr(result, "scenario", None)
    if scenario is not None:
        candidate_line += f" ({scenario})"
    print(candidate_line)
    print(_format_snapshot("Baseline train", result.baseline_train))
    print(_format_snapshot("Baseline validation", result.baseline_validation))
    print(_format_snapshot("Candidate train", result.candidate_train))
    print(_format_snapshot("Candidate validation", result.candidate_validation))
    if mode == "real":
        print(
            f"Optimizer: {result.optimize_result.status}, "
            f"rounds={result.optimize_result.total_rounds}"
        )
    print(f"Gate decision: {result.gate_decision.decision.upper()}")
    rejected_rules = [
        rule
        for rule in result.gate_decision.rule_results
        if rule.outcome == "reject"
    ]
    if rejected_rules:
        print("Rejection reasons:")
        for rule in rejected_rules:
            print(f"- [{rule.rule_id}] {rule.message}")
    if result.gate_decision.warnings:
        print("Warnings:")
        for warning in result.gate_decision.warnings:
            print(f"- {warning}")
    print(f"Writeback: {result.writeback.status.upper()} ({result.writeback.reason})")
    if mode == "real":
        print("Source Prompt unchanged: yes")
    report_dir = Path(prepared.workspace.run_dir) / "report"
    print(f"JSON report: {report_dir / 'optimization_report.json'}")
    print(f"Markdown report: {report_dir / 'optimization_report.md'}")
    print(f"Artifact index: {report_dir / 'artifact_index.json'}")


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    try:
        config = load_pipeline_config(args.config)
    except (OSError, ValueError, ValidationError) as exc:
        parser.error(str(exc))

    if config.execution.mode == "real":
        if not args.run_real:
            parser.error("real API calls require explicit --run-real confirmation")
        if config.writeback.enabled:
            parser.error("real integration requires writeback.enabled=false")
        try:
            business_config = load_business_model_config()
            parameters = _optimizer_parameters(args, parser)
        except (OSError, ValueError, ValidationError) as exc:
            parser.error(str(exc))
        try:
            prepared, result = asyncio.run(
                _run_real(args, business_config, parameters)
            )
        except Exception as exc:
            print(
                f"Real integration failed: {redact_error_message(exc)}",
                file=sys.stderr,
            )
            return 1
        _print_result("real", prepared, result)
        return 0

    if args.run_real:
        parser.error("--run-real is only valid with execution.mode='real'")
    if _optimizer_options_supplied(args):
        parser.error("--optimizer-* options are only valid with execution.mode='real'")
    prepared = prepare_run(args.config, run_id=args.run_id)
    if config.execution.mode == "offline":
        result = asyncio.run(run_offline_stage(prepared, scenario=args.scenario))
    elif config.execution.mode == "trace":
        result = asyncio.run(run_trace_stage(prepared, scenario=args.scenario))
    else:
        parser.error(f"unsupported execution mode: {config.execution.mode}")
    _print_result(config.execution.mode, prepared, result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
