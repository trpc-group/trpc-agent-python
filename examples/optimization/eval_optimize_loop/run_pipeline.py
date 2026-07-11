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
from examples.optimization.eval_optimize_loop.pipeline.models import CandidateReport, OptimizationReport, SplitReport
from examples.optimization.eval_optimize_loop.pipeline.optimizer_backend import AgentOptimizerBackend, PipelineExecutionError
from examples.optimization.eval_optimize_loop.pipeline.attribution import attribute_case
from examples.optimization.eval_optimize_loop.pipeline.normalization import normalize_eval_results
from examples.optimization.eval_optimize_loop.pipeline.prompt_sandbox import PromptSandbox
from examples.optimization.eval_optimize_loop.pipeline.reporter import write_reports



async def run_fake_pipeline(*, output_dir: Path) -> OptimizationReport:
    config = load_pipeline_config(HERE / "optimizer.json", mode="fake")
    report = OptimizationReport.empty(mode="fake", seed=config.pipeline.reproducibility.seed)
    with tempfile.TemporaryDirectory(prefix="trpc-agent-issue91-") as temporary_dir:
        prompt_dir = Path(temporary_dir)
        source_prompt_dir = HERE / "agent" / "prompts"
        for prompt_name in ("system.md", "router.md"):
            (prompt_dir / prompt_name).write_text((source_prompt_dir / prompt_name).read_text(encoding="utf-8"), encoding="utf-8")

        target = TargetPrompt().add_path("system_prompt", str(prompt_dir / "system.md")).add_path("router_prompt", str(prompt_dir / "router.md"))
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
            decision = evaluate_gate(baseline_validation, validation, settings=config.pipeline.gate, case_deltas=deltas, train_score_delta=train.aggregate_score - baseline_train.aggregate_score)
            report.candidates.append(CandidateReport(candidate_id=candidate.candidate_id, accepted=decision.accepted, reasons=decision.reasons, train=train, validation=validation, gate=decision, validation_case_deltas=deltas))
    accepted = [candidate for candidate in report.candidates if candidate.accepted]
    report.selected_candidate_id = accepted[0].candidate_id if accepted else None
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
        for case in normalized.values()
    ]
    report = OptimizationReport.empty(mode="trace", seed=config_payload["seed"])
    report.baseline_validation = SplitReport.from_cases(cases)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "trace_raw_results.json").write_text(
        json.dumps(
            {
                "raw_evaluator_ran": True,
                "results_by_eval_id": {
                    eval_id: [result.model_dump(mode="json") for result in results]
                    for eval_id, results in results_by_eval_id.items()
                },
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    (output_dir / "trace_normalized_cases.json").write_text(
        json.dumps([case.model_dump(mode="json") for case in cases], ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    write_reports(report, output_dir)
    return report


async def run_live_pipeline(*, output_dir: Path) -> OptimizationReport:
    """Run the public optimizer API, then independently evaluate its proposals."""
    config = load_pipeline_config(HERE / "optimizer.json", mode="live")
    report = OptimizationReport.empty(mode="live", seed=config.pipeline.reproducibility.seed)
    source_prompt_dir = HERE / "agent" / "prompts"
    source_baseline = {
        "system_prompt": (source_prompt_dir / "system.md").read_text(encoding="utf-8"),
        "router_prompt": (source_prompt_dir / "router.md").read_text(encoding="utf-8"),
    }
    with tempfile.TemporaryDirectory(prefix="trpc-agent-issue91-regression-") as temporary_dir:
        prompt_dir = Path(temporary_dir)
        target = TargetPrompt()
        for name, content in source_baseline.items():
            path = prompt_dir / f"{name}.md"
            path.write_text(content, encoding="utf-8")
            target.add_path(name, str(path))
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
                )
            )
    accepted = [candidate for candidate in report.candidates if candidate.accepted]
    report.selected_candidate_id = accepted[0].candidate_id if accepted else None
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
