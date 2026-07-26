"""Orchestration for baseline evaluation, optimization, replay, and gating."""

from __future__ import annotations

import hashlib
import json
import platform
import shutil
import subprocess
import time
from datetime import datetime
from datetime import timedelta
from pathlib import Path
from typing import Any

from trpc_agent_sdk.evaluation import AgentOptimizer
from trpc_agent_sdk.evaluation import TargetPrompt

from ..agent.agent import fake_call_agent
from ..agent.agent import real_call_agent
from .analysis import GateInput
from .analysis import attribute_cases
from .analysis import compare_snapshots
from .analysis import evaluate_gate
from .evaluation import EvaluationRequest
from .evaluation import evaluate_split
from .evaluation import validate_inputs
from .models import AuditInfo
from .models import CostSummary
from .models import GateDecision
from .models import EvaluationSnapshot
from .models import InputBundle
from .models import OptimizationReport
from .models import OptimizationSummary
from .models import PipelineOptions
from .models import PipelineResult
from .models import SplitName
from .reporting import write_reports

PROMPT_KEY = "system_prompt"
PROMPT_RELATIVE_PATH = Path("prompts") / "system.md"
REAL_MODE = "real"
FAKE_MODEL_MODE = "fake-model"
TRACE_MODE = "trace"


async def run_pipeline(options: PipelineOptions) -> PipelineResult:
    """Run the loop and convert unexpected failures into an audit report."""
    started = time.monotonic()
    try:
        return await _run_pipeline(options, started)
    except Exception as exc:
        return _failure_result(options, started, exc)


async def _run_pipeline(options: PipelineOptions, started: float) -> PipelineResult:
    """Run the complete evaluation and optimization loop."""
    bundle, _, gate_config = validate_inputs(options.paths)
    workspace = _prepare_workspace(bundle, options.output_dir)
    baseline = await _evaluate_pair(workspace, bundle, options, "baseline")
    optimization, candidate_prompts = await _optimize(
        workspace,
        bundle,
        options,
        gate_config.primary_metric,
    )
    candidate = await _evaluate_candidate(
        workspace,
        bundle,
        options,
        candidate_prompts,
    )
    deltas = {
        split: compare_snapshots(baseline[split], candidate[split])
        for split in (SplitName.TRAIN, SplitName.VALIDATION)
    }
    attributions, counts = _collect_attributions(baseline)
    cost = CostSummary(
        optimizer_cost=optimization.total_cost,
        external_cost=0.0,
        total_cost=optimization.total_cost,
        cost_complete=options.mode != REAL_MODE,
    )
    duration = time.monotonic() - started
    decision = evaluate_gate(
        GateInput(
            config=gate_config,
            train_delta=deltas[SplitName.TRAIN],
            validation_delta=deltas[SplitName.VALIDATION],
            baseline_validation=baseline[SplitName.VALIDATION],
            candidate_validation=candidate[SplitName.VALIDATION],
            cost=cost,
            duration_seconds=duration,
        ))
    report = _build_report(
        bundle,
        options,
        baseline,
        candidate,
        deltas,
        attributions,
        counts,
        optimization,
        cost,
        decision,
        duration,
        False,
    )
    json_path, markdown_path = write_reports(report, options.output_dir)
    source_updated = await _maybe_write_back(
        bundle,
        candidate_prompts,
        decision.accepted,
        options.write_back,
    )
    report.source_updated = source_updated
    return PipelineResult(report=report, json_path=json_path, markdown_path=markdown_path)


def _failure_result(options: PipelineOptions, started: float, error: Exception) -> PipelineResult:
    finished = datetime.now().astimezone()
    started_wall = finished - timedelta(seconds=time.monotonic() - started)
    reason = f"{type(error).__name__}: pipeline stage failed"
    decision = GateDecision(accepted=False, overfitting=False, reasons=[reason])
    report = OptimizationReport(
        status="REJECTED",
        baseline={},
        candidate={},
        delta={},
        optimization=OptimizationSummary(
            status="FAILED",
            finish_reason="pipeline_exception",
            error_message=reason,
        ),
        gate=decision,
        cost=CostSummary(cost_complete=False),
        audit=AuditInfo(
            seed=91,
            input_hashes={},
            model_name=options.model_name,
            num_runs=1,
            case_parallelism=options.case_parallelism,
            python_version=platform.python_version(),
            sdk_version=_sdk_version(),
            git_sha=_git_sha(),
            started_at=started_wall.isoformat(),
            finished_at=finished.isoformat(),
            stage_durations={"pipeline": time.monotonic() - started},
        ),
        failures=[reason],
    )
    json_path, markdown_path = write_reports(report, options.output_dir)
    return PipelineResult(report=report, json_path=json_path, markdown_path=markdown_path)


async def _evaluate_pair(
    prompt_path: Path,
    bundle: InputBundle,
    options: PipelineOptions,
    phase: str,
) -> dict[SplitName, EvaluationSnapshot]:
    call_agent = None if options.mode == TRACE_MODE else _make_call_agent(prompt_path, options.mode)
    train_path = bundle.train_path
    validation_path = bundle.validation_path
    if options.mode == TRACE_MODE:
        if options.trace_file is None:
            raise ValueError("trace_file is required in trace mode")
        train_path = _trace_dataset_path(options.trace_file, phase, SplitName.TRAIN, options.output_dir)
        validation_path = _trace_dataset_path(
            options.trace_file,
            phase,
            SplitName.VALIDATION,
            options.output_dir,
        )
    return {
        SplitName.TRAIN:
        await evaluate_split(
            EvaluationRequest(
                train_path,
                bundle.optimizer_path,
                SplitName.TRAIN,
                call_agent,
                options.fake_judge,
            )),
        SplitName.VALIDATION:
        await evaluate_split(
            EvaluationRequest(
                validation_path,
                bundle.optimizer_path,
                SplitName.VALIDATION,
                call_agent,
                options.fake_judge,
            )),
    }


def _trace_dataset_path(trace_file: Path, phase: str, split: SplitName, output_dir: Path) -> Path:
    payload = json.loads(trace_file.read_text(encoding="utf-8"))
    try:
        cases = payload[phase][split.value]
    except (KeyError, TypeError) as exc:
        raise ValueError(f"trace file missing {phase}/{split.value}") from exc
    path = output_dir / "work" / "trace" / phase / f"{split.value}.evalset.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    normalized = [{
        **case, "actual_conversation": case.get("actual_conversation", case.get("conversation", []))
    } for case in cases]
    path.write_text(
        json.dumps({
            "eval_set_id": f"{phase}-{split.value}",
            "eval_cases": normalized
        }),
        encoding="utf-8",
    )
    return path


async def _optimize(
    prompt_path: Path,
    bundle: InputBundle,
    options: PipelineOptions,
    primary_metric: str,
) -> tuple[OptimizationSummary, dict[str, str]]:
    del primary_metric
    baseline_prompt = prompt_path.read_text(encoding="utf-8")
    if options.mode in (FAKE_MODEL_MODE, TRACE_MODE):
        candidate = {PROMPT_KEY: baseline_prompt + "\n\nOPTIMIZED_CANDIDATE\n"}
        return OptimizationSummary(
            status="SUCCEEDED",
            finish_reason="fake_candidate",
            best_prompts=candidate,
        ), candidate
    if options.mode != REAL_MODE:
        raise ValueError(f"unsupported mode: {options.mode}")
    target = TargetPrompt().add_path(PROMPT_KEY, str(prompt_path))
    call_agent = _make_call_agent(prompt_path, options.mode)
    result = await AgentOptimizer.optimize(
        config_path=str(bundle.optimizer_path),
        call_agent=call_agent,
        target_prompt=target,
        train_dataset_path=str(bundle.train_path),
        validation_dataset_path=str(bundle.validation_path),
        output_dir=str(options.output_dir / "optimizer"),
        update_source=False,
        verbose=0,
    )
    summary = OptimizationSummary(
        status=str(result.status),
        finish_reason=str(result.finish_reason),
        best_prompts=result.best_prompts,
        rounds=[round_record.model_dump(mode="json") for round_record in result.rounds],
        total_cost=float(result.total_llm_cost),
        error_message=result.error_message,
    )
    candidate = result.best_prompts
    if not candidate or PROMPT_KEY not in candidate:
        candidate = {PROMPT_KEY: baseline_prompt}
    return summary, candidate


async def _evaluate_candidate(
    prompt_path: Path,
    bundle: InputBundle,
    options: PipelineOptions,
    prompts: dict[str, str],
) -> dict[SplitName, EvaluationSnapshot]:
    baseline = prompt_path.read_text(encoding="utf-8")
    try:
        prompt_path.write_text(prompts[PROMPT_KEY], encoding="utf-8")
        return await _evaluate_pair(prompt_path, bundle, options, "candidate")
    finally:
        prompt_path.write_text(baseline, encoding="utf-8")


def _prepare_workspace(bundle: InputBundle, output_dir: Path) -> Path:
    prompt_path = output_dir / "work" / PROMPT_RELATIVE_PATH
    prompt_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(bundle.prompt_path, prompt_path)
    return prompt_path


def _make_call_agent(prompt_path: Path, mode: str):
    if mode == FAKE_MODEL_MODE:

        async def call(query: str) -> str:
            return await fake_call_agent(prompt_path, query)

        return call
    if mode == REAL_MODE:

        async def call(query: str) -> str:
            return await real_call_agent(prompt_path, query)

        return call
    raise ValueError(f"unsupported call-agent mode: {mode}")


def _collect_attributions(snapshots: dict[SplitName, EvaluationSnapshot], ) -> tuple[list, dict]:
    all_attributions = []
    counts = {}
    for snapshot in snapshots.values():
        attributions, snapshot_counts = attribute_cases(snapshot)
        all_attributions.extend(attributions)
        for category, count in snapshot_counts.items():
            counts[category] = counts.get(category, 0) + count
    return all_attributions, counts


async def _maybe_write_back(
    bundle: InputBundle,
    prompts: dict[str, str],
    accepted: bool,
    write_back: bool,
) -> bool:
    if not accepted or not write_back:
        return False
    target = TargetPrompt().add_path(PROMPT_KEY, str(bundle.prompt_path))
    await target.write_all(prompts)
    return True


def _build_report(
    bundle: InputBundle,
    options: PipelineOptions,
    baseline: dict[SplitName, EvaluationSnapshot],
    candidate: dict[SplitName, EvaluationSnapshot],
    deltas: dict[SplitName, Any],
    attributions: list,
    counts: dict,
    optimization: OptimizationSummary,
    cost: CostSummary,
    decision,
    duration: float,
    source_updated: bool,
) -> OptimizationReport:
    finished = datetime.now().astimezone()
    started = finished - timedelta(seconds=duration)
    audit = AuditInfo(
        seed=91,
        input_hashes={
            **bundle.hashes, "candidate_prompt": _prompt_hash(optimization.best_prompts)
        },
        model_name=options.model_name,
        num_runs=1,
        case_parallelism=options.case_parallelism,
        python_version=platform.python_version(),
        sdk_version=_sdk_version(),
        git_sha=_git_sha(),
        started_at=started.isoformat(),
        finished_at=finished.isoformat(),
        stage_durations={"pipeline": duration},
    )
    status = "ACCEPTED" if decision.accepted else "REJECTED"
    return OptimizationReport(
        status=status,
        baseline=baseline,
        candidate=candidate,
        delta=deltas,
        attributions=attributions,
        attribution_counts=counts,
        optimization=optimization,
        gate=decision,
        cost=cost,
        audit=audit,
        source_updated=source_updated,
    )


def _prompt_hash(prompts: dict[str, str]) -> str:
    value = prompts.get(PROMPT_KEY, "")
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _git_sha() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def _sdk_version() -> str:
    import trpc_agent_sdk
    return str(getattr(trpc_agent_sdk, "__version__", "unknown"))
