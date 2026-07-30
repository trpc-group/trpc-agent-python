"""Isolated candidate-generation workspace and sanitized artifact ingestion."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from trpc_agent_sdk.evaluation import EvalConfig, EvalSet, OptimizeConfig

from .artifacts import AuditSink
from .attribution import select_attribution
from .contracts import CandidateGenerator
from .evaluation import dataset_fingerprint, split_train_dataset
from .models import (
    AttributionSnapshot,
    CandidateProposal,
    EvaluationSnapshot,
    InnerSplit,
)
from .prompt_workspace import PromptWorkspace
from .schema import add_exception_note, validate_secret_free_text


def prepare_candidate_inputs(
    *,
    train: EvalSet,
    baseline: EvaluationSnapshot,
    attribution: AttributionSnapshot,
    seed: int,
    selection_ratio: float,
    sink: AuditSink,
) -> tuple[InnerSplit, AttributionSnapshot, Path, Path]:
    """Create and persist the optimizer-only inner split and attribution."""

    inner_train, inner_selection = split_train_dataset(
        train,
        baseline,
        seed=seed,
        selection_ratio=selection_ratio,
    )
    train_path = sink.write_json(
        "inner_train.evalset.json",
        inner_train.model_dump(mode="json", by_alias=True),
    )
    selection_path = sink.write_json(
        "inner_selection.evalset.json",
        inner_selection.model_dump(mode="json", by_alias=True),
    )
    inner = InnerSplit(
        train_case_ids=tuple(case.eval_id for case in inner_train.eval_cases),
        selection_case_ids=tuple(case.eval_id for case in inner_selection.eval_cases),
        train_hash=dataset_fingerprint(inner_train),
        selection_hash=dataset_fingerprint(inner_selection),
        train_path=train_path.relative_to(sink.run_dir).as_posix(),
        selection_path=selection_path.relative_to(sink.run_dir).as_posix(),
    )
    selected = select_attribution(attribution, frozenset(inner.train_case_ids))
    sink.write_json(
        "inner_train.attribution.json",
        selected.model_dump(mode="json", by_alias=True),
    )
    return inner, selected, train_path, selection_path


async def generate_candidate(
    *,
    generator: CandidateGenerator,
    workspace: PromptWorkspace,
    sink: AuditSink,
    eval_config: EvalConfig,
    optimize_config: OptimizeConfig,
    train_attribution: AttributionSnapshot,
    inner_train_path: Path,
    inner_selection_path: Path,
) -> CandidateProposal:
    """Run a generator outside the audit tree, then import only sanitized output."""

    sink.phase_dir("candidate_generation")
    with tempfile.TemporaryDirectory(prefix="trpc-agent-eval-optimize-") as runtime_value:
        runtime_root = Path(runtime_value)
        candidate_target = workspace.create_candidate_target(str(runtime_root / "prompt_sandbox"))
        runtime_config = runtime_root / "optimizer.json"
        runtime_config.write_text(
            json.dumps(
                {
                    "evaluate": eval_config.model_dump(mode="json", by_alias=True),
                    "optimize": optimize_config.model_dump(mode="json", by_alias=True),
                },
                ensure_ascii=False,
                allow_nan=False,
            ),
            encoding="utf-8",
        )
        optimizer_output = runtime_root / "optimizer-output"
        try:
            proposal = await generator.generate(
                target_prompt=candidate_target,
                baseline_prompts=workspace.baseline,
                train_attribution=train_attribution,
                inner_train_path=str(inner_train_path),
                inner_selection_path=str(inner_selection_path),
                config_path=str(runtime_config),
                output_dir=str(optimizer_output),
            )
        except BaseException as primary_error:
            try:
                sink.import_tree(optimizer_output, "candidate_generation/optimizer")
            except Exception as import_error:
                add_exception_note(
                    primary_error,
                    f"optimizer artifact import also failed with "
                    f"{type(import_error).__name__}: {import_error}",
                )
            raise
        else:
            sink.import_tree(optimizer_output, "candidate_generation/optimizer")
    validated = CandidateProposal.model_validate(proposal.model_dump(mode="python", by_alias=True))
    if validated.baseline_prompts != workspace.baseline:
        raise ValueError("candidate generator baseline prompts differ from the workspace baseline")
    for name, text in validated.prompts.items():
        validate_secret_free_text(text, name=f"candidate prompt {name!r}")
    for round_ in validated.rounds:
        for name, text in round_.candidate_prompts.items():
            validate_secret_free_text(text, name=f"candidate round {round_.round} prompt {name!r}")
    return validated
