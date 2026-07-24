from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
from pathlib import Path

# When this file is executed directly (the documented CLI path), Python puts
# only this directory on sys.path.  Bootstrap the repository root before
# importing the SDK and the example package so the CLI behaves like
# ``python -m ...`` and like the pytest entry point.
HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from trpc_agent_sdk.evaluation import AgentEvaluator, TargetPrompt
from trpc_agent_sdk.evaluation._eval_config import EvalConfig
from trpc_agent_sdk.evaluation._eval_set import EvalSet

from examples.optimization.eval_optimize_loop.fake.fake_agent import FakeSupportAgent
from examples.optimization.eval_optimize_loop.fake.fake_judge import register_fake_rubric_evaluator
from examples.optimization.eval_optimize_loop.fake.fixture_optimizer import FixtureOptimizerBackend
from examples.optimization.eval_optimize_loop.pipeline.comparator import compare_case
from examples.optimization.eval_optimize_loop.pipeline.config import load_pipeline_config
from examples.optimization.eval_optimize_loop.pipeline.evaluator import evaluate_split
from examples.optimization.eval_optimize_loop.pipeline.gate import evaluate_gate
from examples.optimization.eval_optimize_loop.pipeline.gate import select_winner
from examples.optimization.eval_optimize_loop.pipeline.models import CandidateReport, OptimizationReport, SplitReport
from examples.optimization.eval_optimize_loop.pipeline.optimizer_backend import (
    AgentOptimizerBackend,
    PipelineExecutionError,
    write_back_after_gate,
)
from examples.optimization.eval_optimize_loop.pipeline.attribution import attribute_case
from examples.optimization.eval_optimize_loop.pipeline.normalization import normalize_eval_results
from examples.optimization.eval_optimize_loop.pipeline.prompt_sandbox import PromptSandbox
from examples.optimization.eval_optimize_loop.pipeline.reporter import write_reports, write_secret_free_json
from examples.optimization.eval_optimize_loop.pipeline.audit import write_environment_snapshot, write_input_snapshot
def _attribution_summary(*splits: SplitReport | None) -> dict[str, int]:
    summary: dict[str, int] = {}
    for split in splits:
        if split is not None:
            for case in split.cases:
                if case.failure_attribution is not None:
                    key = case.failure_attribution.primary_type.value
                    summary[key] = summary.get(key, 0) + 1
    return dict(sorted(summary.items()))


def _stable_trace_projection(results_by_eval_id: dict[str, list[object]]) -> dict[str, object]:
    """Keep recorded evaluator evidence while removing per-run identifiers."""
    ephemeral_keys = {"sessionid", "timestamp", "createdat", "updatedat"}

    def project(value: object) -> object:
        if isinstance(value, dict):
            return {
                key: project(item)
                for key, item in value.items()
                if "".join(character for character in str(key).lower() if character.isalnum()) not in ephemeral_keys
            }
        if isinstance(value, list):
            return [project(item) for item in value]
        return value

    return {
        "raw_evaluator_ran": True,
        "results_by_eval_id": {
            eval_id: project([result.model_dump(mode="json") for result in results])
            for eval_id, results in sorted(results_by_eval_id.items())
        },
    }


async def run_fake_pipeline(*, output_dir: Path) -> OptimizationReport:
    config = load_pipeline_config(HERE / "optimizer.json", mode="fake")
    report = OptimizationReport.empty(mode="fake", seed=config.pipeline.reproducibility.seed)
    report.run_metadata = {"evaluation_source": "fixture", "independent_candidate_evaluation": True}
    with tempfile.TemporaryDirectory(prefix="trpc-agent-issue91-") as temporary_dir:
        prompt_dir = Path(temporary_dir)
        source_prompt_dir = HERE / "agent" / "prompts"
        for prompt_name in ("system.md", "router.md"):
            (prompt_dir / prompt_name).write_text((source_prompt_dir / prompt_name).read_text(encoding="utf-8"), encoding="utf-8")

        target = TargetPrompt().add_path("system_prompt", str(prompt_dir / "system.md")).add_path("router_prompt", str(prompt_dir / "router.md"))
        output_dir.mkdir(parents=True, exist_ok=True)
        write_input_snapshot(config, target, output_dir)
        write_environment_snapshot("fake", report.seed, output_dir)
        fake_agent = FakeSupportAgent(target)
        evaluate = lambda path, split: evaluate_split(path, call_agent=fake_agent.call_agent, split=split, metric_weights=config.pipeline.metric_weights)
        baseline_train = await evaluate(HERE / "train.evalset.json", "train")
        baseline_validation = await evaluate(HERE / "val.evalset.json", "validation")
        report.baseline_train = baseline_train
        report.baseline_validation = baseline_validation
        for candidate in FixtureOptimizerBackend(HERE / "fake" / "candidates.json").load_candidates():
            async with PromptSandbox(target, candidate.prompts):
                train = await evaluate(HERE / "train.evalset.json", "train")
                validation = await evaluate(HERE / "val.evalset.json", "validation")
            deltas = [compare_case(base, next(item for item in validation.cases if item.eval_id == base.eval_id), epsilon=config.pipeline.scoring_epsilon, critical_case_ids=set(config.pipeline.gate.critical_case_ids)) for base in baseline_validation.cases]
            decision = evaluate_gate(baseline_validation, validation, settings=config.pipeline.gate, case_deltas=deltas, train_score_delta=train.aggregate_score - baseline_train.aggregate_score, metric_floors=config.pipeline.metric_floors, generation_cost_usd=candidate.generation_cost_usd, duration_seconds=candidate.duration_seconds, epsilon=config.pipeline.scoring_epsilon)
            report.candidates.append(CandidateReport(candidate_id=candidate.candidate_id, accepted=decision.accepted, reasons=decision.reasons, train=train, validation=validation, gate=decision, validation_case_deltas=deltas, independently_evaluated=True, source=candidate.source, generation_cost_usd=candidate.generation_cost_usd, duration_seconds=candidate.duration_seconds))
    report.selected_candidate_id = select_winner(report.candidates)
    report.warnings = sorted({warning for candidate in report.candidates if candidate.gate for warning in candidate.gate.warnings})
    report.attribution_summary = _attribution_summary(report.baseline_train, report.baseline_validation)
    write_reports(report, output_dir)
    return report


async def run_trace_pipeline(*, output_dir: Path) -> OptimizationReport:
    """Evaluate recorded trace conversations without loading an agent or optimizer."""
    trace_dir = HERE / "trace"
    config_payload = json.loads((trace_dir / "trace_config.json").read_text(encoding="utf-8"))
    eval_config = EvalConfig.model_validate(config_payload["evaluate"])
    eval_set = EvalSet.model_validate_json((trace_dir / "trace.evalset.json").read_text(encoding="utf-8"))
    register_fake_rubric_evaluator()
    _, _, _, results_by_eval_id = await AgentEvaluator.evaluate_eval_set(
        eval_set,
        eval_config=eval_config,
        num_runs=eval_config.num_runs,
        print_detailed_results=False,
    )
    normalized = normalize_eval_results(
        results_by_eval_id,
        split="validation",
        metric_weights=config_payload["metric_weights"],
    )
    cases = [
        case if case.passed else case.model_copy(update={"failure_attribution": attribute_case(case)})
        for _, case in sorted(normalized.items())
    ]
    report = OptimizationReport.empty(mode="trace", seed=config_payload["seed"])
    report.run_metadata = {"evaluation_source": "recorded_trace", "independent_candidate_evaluation": False}
    report.baseline_validation = SplitReport.from_cases(cases)
    report.attribution_summary = _attribution_summary(report.baseline_validation)
    output_dir.mkdir(parents=True, exist_ok=True)
    write_secret_free_json(output_dir / "input.snapshot.json", {"trace_config": config_payload, "trace_evalset": "trace/trace.evalset.json"})
    write_environment_snapshot("trace", report.seed, output_dir)
    raw_projection = _stable_trace_projection(results_by_eval_id)
    normalized_projection = {"cases": [case.model_dump(mode="json") for case in cases]}
    write_secret_free_json(output_dir / "trace_raw_results.json", raw_projection)
    write_secret_free_json(output_dir / "trace_normalized_cases.json", normalized_projection)
    write_reports(report, output_dir, raw_payload=raw_projection, normalized_payload=normalized_projection)
    return report


async def run_live_pipeline(*, output_dir: Path) -> OptimizationReport:
    """Run the public optimizer API, then independently evaluate its proposals."""
    config = load_pipeline_config(HERE / "optimizer.json", mode="live")
    report = OptimizationReport.empty(mode="live", seed=config.pipeline.reproducibility.seed)
    report.run_metadata = {"evaluation_source": "independent_full_train_validation", "independent_candidate_evaluation": True, "write_back_default": False}
    source_prompt_dir = HERE / "agent" / "prompts"
    source_baseline = {
        "system_prompt": (source_prompt_dir / "system.md").read_text(encoding="utf-8"),
        "router_prompt": (source_prompt_dir / "router.md").read_text(encoding="utf-8"),
    }
    source_target = TargetPrompt()
    for name, filename in (("system_prompt", "system.md"), ("router_prompt", "router.md")):
        source_target.add_path(name, str(source_prompt_dir / filename))
    with tempfile.TemporaryDirectory(prefix="trpc-agent-issue91-regression-") as temporary_dir:
        prompt_dir = Path(temporary_dir)
        target = TargetPrompt()
        for name, content in source_baseline.items():
            path = prompt_dir / f"{name}.md"
            path.write_text(content, encoding="utf-8")
            target.add_path(name, str(path))
        output_dir.mkdir(parents=True, exist_ok=True)
        write_input_snapshot(config, target, output_dir)
        write_environment_snapshot("live", report.seed, output_dir)
        fake_agent = FakeSupportAgent(target)
        evaluate = lambda path, split: evaluate_split(
            path,
            call_agent=fake_agent.call_agent,
            split=split,
            metric_weights=config.pipeline.metric_weights,
        )
        report.baseline_train = await evaluate(config.pipeline.datasets.train_path, "train")
        report.baseline_validation = await evaluate(config.pipeline.datasets.validation_path, "validation")
        backend = AgentOptimizerBackend(
            raw_config=config.raw,
            candidate_scope=config.pipeline.candidate_validation.scope,
        )
        candidates = await backend.generate_candidates(
            baseline_prompts=source_baseline,
            train_dataset_path=config.pipeline.datasets.train_path,
            validation_dataset_path=config.pipeline.datasets.validation_path,
            output_dir=output_dir,
        )
        for candidate in candidates:
            async with PromptSandbox(target, candidate.prompts):
                train = await evaluate(config.pipeline.datasets.train_path, "train")
                validation = await evaluate(config.pipeline.datasets.validation_path, "validation")
            deltas = [
                compare_case(
                    baseline_case,
                    next(item for item in validation.cases if item.eval_id == baseline_case.eval_id),
                    epsilon=config.pipeline.scoring_epsilon,
                    critical_case_ids=set(config.pipeline.gate.critical_case_ids),
                )
                for baseline_case in report.baseline_validation.cases
            ]
            decision = evaluate_gate(
                report.baseline_validation,
                validation,
                settings=config.pipeline.gate,
                case_deltas=deltas,
                train_score_delta=train.aggregate_score - report.baseline_train.aggregate_score,
                metric_floors=config.pipeline.metric_floors,
                generation_cost_usd=candidate.generation_cost_usd,
                duration_seconds=candidate.duration_seconds,
                epsilon=config.pipeline.scoring_epsilon,
            )
            report.candidates.append(
                CandidateReport(
                    candidate_id=candidate.candidate_id,
                    accepted=decision.accepted,
                    reasons=decision.reasons,
                    train=train,
                    validation=validation,
                    gate=decision,
                    validation_case_deltas=deltas,
                    independently_evaluated=True,
                    source=candidate.source,
                    generation_cost_usd=candidate.generation_cost_usd,
                    duration_seconds=candidate.duration_seconds,
                )
            )
    accepted = [candidate for candidate in report.candidates if candidate.accepted]
    report.selected_candidate_id = select_winner(report.candidates)
    report.warnings = sorted({warning for candidate in report.candidates if candidate.gate for warning in candidate.gate.warnings})
    report.attribution_summary = _attribution_summary(report.baseline_train, report.baseline_validation)
    if config.pipeline.write_back_when_accepted and report.selected_candidate_id:
        selected_record = next(candidate for candidate in candidates if candidate.candidate_id == report.selected_candidate_id)
        selected_report = next(candidate for candidate in accepted if candidate.candidate_id == report.selected_candidate_id)
        if selected_report.gate is None:  # Defensive: accepted reports always carry a GateDecision.
            raise PipelineExecutionError("selected candidate is missing its gate decision")
        await write_back_after_gate(source_target, source_baseline, selected_record.prompts, selected_report.gate)
    write_reports(report, output_dir)
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["fake", "trace", "live"], default="fake")
    parser.add_argument("--output-dir", type=Path, default=HERE / "runs" / "manual")
    args = parser.parse_args()
    try:
        if args.mode == "live":
            # Preflight before constructing the coroutine/optimizer so missing
            # credentials always return the documented input-error exit code.
            load_pipeline_config(HERE / "optimizer.json", mode="live")
            report = asyncio.run(run_live_pipeline(output_dir=args.output_dir))
        elif args.mode == "trace":
            report = asyncio.run(run_trace_pipeline(output_dir=args.output_dir))
        else:
            report = asyncio.run(run_fake_pipeline(output_dir=args.output_dir))
    except ValueError as exc:
        print(f"Input error: {exc}", file=sys.stderr)
        return 2
    except PipelineExecutionError as exc:
        print(f"Pipeline execution error: {exc}", file=sys.stderr)
        return 3
    print(f"Decision: {'ACCEPT' if report.selected_candidate_id else 'REJECT'}")
    print(f"Selected candidate: {report.selected_candidate_id or 'none'}")
    print(f"JSON report: {(args.output_dir / 'optimization_report.json').resolve()}")
    print(f"Markdown report: {(args.output_dir / 'optimization_report.md').resolve()}")
    print("Source prompt updated: no")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
