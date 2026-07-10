from __future__ import annotations

import argparse
import asyncio
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

from trpc_agent_sdk.evaluation import TargetPrompt

from examples.optimization.eval_optimize_loop.fake.fake_agent import FakeSupportAgent
from examples.optimization.eval_optimize_loop.fake.fixture_optimizer import FixtureOptimizerBackend
from examples.optimization.eval_optimize_loop.pipeline.comparator import compare_case
from examples.optimization.eval_optimize_loop.pipeline.config import load_pipeline_config
from examples.optimization.eval_optimize_loop.pipeline.evaluator import evaluate_split
from examples.optimization.eval_optimize_loop.pipeline.gate import evaluate_gate
from examples.optimization.eval_optimize_loop.pipeline.models import CandidateReport, OptimizationReport
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["fake"], default="fake")
    parser.add_argument("--output-dir", type=Path, default=HERE / "runs" / "manual")
    args = parser.parse_args()
    report = asyncio.run(run_fake_pipeline(output_dir=args.output_dir))
    print(f"Decision: {'ACCEPT' if report.selected_candidate_id else 'REJECT'}")
    print(f"Selected candidate: {report.selected_candidate_id or 'none'}")
    print(f"JSON report: {(args.output_dir / 'optimization_report.json').resolve()}")
    print(f"Markdown report: {(args.output_dir / 'optimization_report.md').resolve()}")
    print("Source prompt updated: no")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
