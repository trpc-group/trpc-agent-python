"""Run the deterministic Evaluation + Optimization closed-loop example."""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from eval_loop.evaluator import ExampleEvaluator
from eval_loop.fake_judge import FakeJudge
from eval_loop.fake_model import FakeModel
from eval_loop.gate import AcceptanceGate
from eval_loop.loader import load_eval_cases
from eval_loop.loader import load_optimizer_config
from eval_loop.loader import load_prompt
from eval_loop.loader import stable_config_hash
from eval_loop.optimizer import FakeOptimizer
from eval_loop.report import REPRODUCIBILITY_COMMAND
from eval_loop.report import build_report
from eval_loop.report import compute_case_deltas
from eval_loop.report import write_reports
from eval_loop.schemas import CandidatePrompt
from eval_loop.schemas import OptimizationReport


DEFAULT_TRAIN = HERE / "data" / "train.evalset.json"
DEFAULT_VAL = HERE / "data" / "val.evalset.json"
DEFAULT_OPTIMIZER_CONFIG = HERE / "data" / "optimizer.json"
DEFAULT_PROMPT = HERE / "prompts" / "baseline_system_prompt.txt"
DEFAULT_OUTPUT_DIR = Path(tempfile.gettempdir()) / "eval-optimize-loop"


def run_pipeline(
    *,
    train_path: str | Path = DEFAULT_TRAIN,
    val_path: str | Path = DEFAULT_VAL,
    optimizer_config_path: str | Path = DEFAULT_OPTIMIZER_CONFIG,
    prompt_path: str | Path = DEFAULT_PROMPT,
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
    fake_model: bool = True,
    fake_judge: bool = True,
    trace: bool = False,
) -> OptimizationReport:
    """Run baseline eval, fake optimization, validation gate, and reports."""

    if not fake_model or not fake_judge:
        raise ValueError(
            "This example-local implementation only supports offline fake mode. "
            "Pass --fake-model --fake-judge, or replace ExampleEvaluator with a real adapter."
        )

    train_cases = load_eval_cases(train_path, split="train")
    validation_cases = load_eval_cases(val_path, split="validation")
    if len(train_cases) < 3 or len(validation_cases) < 3:
        raise ValueError("example requires at least 3 train and 3 validation eval cases")

    optimizer_config = load_optimizer_config(optimizer_config_path)
    seed = int(optimizer_config.get("seed", 91))
    baseline_prompt = load_prompt(prompt_path)
    evaluator = ExampleEvaluator(FakeModel(seed=seed), FakeJudge(), trace_enabled=trace)

    baseline = CandidatePrompt(
        candidate_id="baseline",
        prompt=baseline_prompt,
        rationale="Prompt source file before optimization.",
        prompt_diff="",
    )
    baseline_train = evaluator.evaluate(
        prompt_id=baseline.candidate_id,
        prompt=baseline.prompt,
        cases=train_cases,
        split="train",
    )
    baseline_validation = evaluator.evaluate(
        prompt_id=baseline.candidate_id,
        prompt=baseline.prompt,
        cases=validation_cases,
        split="validation",
    )

    optimizer = FakeOptimizer()
    candidates = optimizer.propose(baseline_prompt)
    gate = AcceptanceGate(optimizer_config["gate"])

    candidate_records: list[dict[str, Any]] = []
    all_deltas = []
    gate_decisions = []
    for candidate in candidates:
        train_result = evaluator.evaluate(
            prompt_id=candidate.candidate_id,
            prompt=candidate.prompt,
            cases=train_cases,
            split="train",
        )
        validation_result = evaluator.evaluate(
            prompt_id=candidate.candidate_id,
            prompt=candidate.prompt,
            cases=validation_cases,
            split="validation",
        )
        deltas = compute_case_deltas(
            candidate_id=candidate.candidate_id,
            baseline_train=baseline_train,
            baseline_validation=baseline_validation,
            candidate_train=train_result,
            candidate_validation=validation_result,
        )
        decision = gate.decide(
            candidate_id=candidate.candidate_id,
            baseline_train=baseline_train,
            baseline_validation=baseline_validation,
            candidate_train=train_result,
            candidate_validation=validation_result,
            deltas=deltas,
        )
        candidate_records.append({
            "candidate": candidate,
            "train_result": train_result,
            "validation_result": validation_result,
        })
        all_deltas.extend(deltas)
        gate_decisions.append(decision)

    selected_candidate = _select_candidate(candidate_records, gate_decisions)
    audit = _build_audit(
        seed=seed,
        config_hash=stable_config_hash(optimizer_config),
        baseline_train=baseline_train,
        baseline_validation=baseline_validation,
        candidate_records=candidate_records,
        candidates=candidates,
    )
    run = {
        "run_id": f"eval_optimize_loop_seed_{seed}",
        "mode": "fake",
        "fake_model": fake_model,
        "fake_judge": fake_judge,
        "trace_enabled": trace,
        "train_cases": len(train_cases),
        "validation_cases": len(validation_cases),
        "prompt_source": "prompts/baseline_system_prompt.txt",
    }
    report = build_report(
        run=run,
        baseline_train=baseline_train,
        baseline_validation=baseline_validation,
        candidate_records=candidate_records,
        per_case_deltas=all_deltas,
        gate_decisions=gate_decisions,
        selected_candidate=selected_candidate,
        audit=audit,
    )
    write_reports(report, output_dir)
    return report


def _select_candidate(candidate_records: list[dict[str, Any]], gate_decisions: list) -> str | None:
    decisions_by_id = {decision.candidate_id: decision for decision in gate_decisions}
    accepted = []
    for index, record in enumerate(candidate_records):
        candidate = record["candidate"]
        decision = decisions_by_id[candidate.candidate_id]
        if decision.accepted:
            accepted.append((index, record))
    if not accepted:
        return None
    index, record = max(
        accepted,
        key=lambda item: (
            item[1]["validation_result"].score,
            item[1]["train_result"].score,
            -item[0],
        ),
    )
    return record["candidate"].candidate_id


def _build_audit(
    *,
    seed: int,
    config_hash: str,
    baseline_train,
    baseline_validation,
    candidate_records: list[dict[str, Any]],
    candidates: list[CandidatePrompt],
) -> dict[str, Any]:
    baseline_cost = round(baseline_train.cost + baseline_validation.cost, 6)
    candidate_costs = {
        record["candidate"].candidate_id: round(record["train_result"].cost + record["validation_result"].cost, 6)
        for record in candidate_records
    }
    total_cost = round(baseline_cost + sum(candidate_costs.values()), 6)
    return {
        "seed": seed,
        "duration_seconds": 0.0,
        "config_hash": config_hash,
        "cost": {
            "baseline": baseline_cost,
            "candidates": candidate_costs,
            "total": total_cost,
        },
        "candidate_prompts": {
            candidate.candidate_id: {
                "rationale": candidate.rationale,
                "prompt": candidate.prompt,
                "prompt_diff": candidate.prompt_diff,
            }
            for candidate in candidates
        },
        "prompt_diffs": {
            candidate.candidate_id: candidate.prompt_diff
            for candidate in candidates
        },
        "reproducibility_command": REPRODUCIBILITY_COMMAND,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--train", default=str(DEFAULT_TRAIN), help="Path to train.evalset.json")
    parser.add_argument("--val", default=str(DEFAULT_VAL), help="Path to val.evalset.json")
    parser.add_argument("--optimizer-config", default=str(DEFAULT_OPTIMIZER_CONFIG), help="Path to optimizer.json")
    parser.add_argument("--prompt", default=str(DEFAULT_PROMPT), help="Path to baseline system prompt")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="Directory for runtime reports")
    parser.add_argument("--fake-model", action="store_true", help="Use deterministic fake model")
    parser.add_argument("--fake-judge", action="store_true", help="Use deterministic fake judge")
    parser.add_argument("--trace", action="store_true", help="Persist fake model/judge trace details per case")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> OptimizationReport:
    args = parse_args(argv)
    report = run_pipeline(
        train_path=args.train,
        val_path=args.val,
        optimizer_config_path=args.optimizer_config,
        prompt_path=args.prompt,
        output_dir=args.output_dir,
        fake_model=args.fake_model,
        fake_judge=args.fake_judge,
        trace=args.trace,
    )
    output_dir = Path(args.output_dir)
    print(f"Wrote {output_dir / 'optimization_report.json'}")
    print(f"Wrote {output_dir / 'optimization_report.md'}")
    print(f"Selected candidate: {report.selected_candidate}")
    return report


if __name__ == "__main__":
    main()
