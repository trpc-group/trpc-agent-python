#
# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#
"""End-to-end orchestration for the evaluation and optimization closed loop."""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from datetime import datetime
from datetime import timezone
from pathlib import Path
from typing import Any

from trpc_agent_sdk.evaluation import AgentOptimizer
from trpc_agent_sdk.evaluation import EvalConfig
from trpc_agent_sdk.evaluation import EvalSet
from trpc_agent_sdk.evaluation import TargetPrompt

from .analysis import RegressionAnalyzer
from .models import BaselineEvaluation
from .models import CandidateAudit
from .models import CandidateDelta
from .models import CandidateEvaluation
from .models import OptimizationReport
from .models import OptimizerAudit
from .models import PipelineSpec
from .models import RunAudit
from .offline import configure_offline_models
from .offline import create_offline_call_agent
from .reporting import write_reports
from .trace import TraceEvaluator


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _sha256_text(text: str) -> str:
    return _sha256_bytes(text.encode("utf-8"))


class _ProposalCapture:
    """Observe every reflection proposal, including ones GEPA later rejects."""

    def __init__(self) -> None:
        self._parents: dict[int, dict[str, str]] = {}
        self.proposals: list[tuple[int, dict[str, str]]] = []

    def on_proposal_start(self, event: dict[str, Any]) -> None:
        self._parents[int(event["iteration"])] = dict(event["parent_candidate"])

    def on_proposal_end(self, event: dict[str, Any]) -> None:
        iteration = int(event["iteration"])
        candidate = dict(self._parents.get(iteration, {}))
        candidate.update(dict(event.get("new_instructions") or {}))
        if candidate:
            self.proposals.append((iteration, candidate))


class EvalOptimizePipeline:
    """A deep module whose only operation executes the complete closed loop."""

    def __init__(self, spec: PipelineSpec) -> None:
        self._spec = spec

    async def run(self) -> OptimizationReport:
        """Run baseline, optimization, independent regression, gate, and reports."""
        started_clock = time.perf_counter()
        started_at = datetime.now(timezone.utc)
        spec = self._spec
        spec.output_dir.mkdir(parents=True, exist_ok=True)

        analyzer = RegressionAnalyzer(
            seed=spec.seed,
            bootstrap_samples=spec.bootstrap_samples,
            confidence_level=spec.confidence_level,
        )
        trace_evaluator = TraceEvaluator(spec.output_dir)
        train_set = EvalSet.model_validate_json(spec.train_dataset.read_text(encoding="utf-8"))
        validation_set = EvalSet.model_validate_json(spec.validation_dataset.read_text(encoding="utf-8"))
        known_candidates = {
            source.candidate_id: source.path.read_text(encoding="utf-8")
            for source in spec.candidate_sources
        }
        prompt_text = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (list(spec.target_prompts.values()) + [source.path for source in spec.candidate_sources]))
        data_quality = analyzer.validate_data_quality(
            train_set,
            validation_set,
            train_path=spec.train_dataset,
            validation_path=spec.validation_dataset,
            prompt_text=prompt_text,
        )
        regression_config = EvalConfig.model_validate_json(spec.regression_metrics_config.read_text(encoding="utf-8"))
        gate_config = json.loads(spec.gate_config.read_text(encoding="utf-8"))

        configure_offline_models(
            eval_sets=[train_set, validation_set],
            candidate_prompts=list(known_candidates.values()),
        )
        target_prompt = TargetPrompt()
        for name, path in spec.target_prompts.items():
            target_prompt.add_path(name, str(path))
        baseline_prompts = await target_prompt.read_all()
        call_agent = create_offline_call_agent(spec.target_prompts)

        baseline = BaselineEvaluation(
            train=await trace_evaluator.evaluate(
                train_set,
                split="train",
                eval_config=regression_config,
                prompts=baseline_prompts,
                trace_label="baseline",
            ),
            validation=await trace_evaluator.evaluate(
                validation_set,
                split="validation",
                eval_config=regression_config,
                prompts=baseline_prompts,
                trace_label="baseline",
            ),
        )

        optimizer_dir = spec.output_dir / "optimizer"
        proposal_capture = _ProposalCapture()
        optimize_result = await AgentOptimizer.optimize(
            config_path=str(spec.optimizer_config),
            call_agent=call_agent,
            target_prompt=target_prompt,
            train_dataset_path=str(spec.train_dataset),
            validation_dataset_path=str(spec.validation_dataset),
            output_dir=str(optimizer_dir),
            update_source=False,
            verbose=0,
            extra_gepa_callbacks=[proposal_capture],
        )
        proposal_records = analyzer.unique_proposals(
            proposal_capture.proposals,
            best_prompts=dict(optimize_result.best_prompts),
        )
        optimizer_resources = analyzer.optimizer_resources(optimize_result)

        candidates: list[CandidateEvaluation] = []
        try:
            for optimizer_round, prompts in proposal_records:
                candidate_started = time.perf_counter()
                candidate_id = analyzer.candidate_id(prompts, known_candidates)
                await target_prompt.write_all(prompts)
                candidate_train = await trace_evaluator.evaluate(
                    train_set,
                    split="train",
                    eval_config=regression_config,
                    prompts=prompts,
                    trace_label=candidate_id,
                )
                candidate_validation = await trace_evaluator.evaluate(
                    validation_set,
                    split="validation",
                    eval_config=regression_config,
                    prompts=prompts,
                    trace_label=candidate_id,
                )
                delta = CandidateDelta(
                    train=analyzer.diff(baseline.train, candidate_train),
                    validation=analyzer.diff(baseline.validation, candidate_validation),
                )
                candidate_resources = analyzer.candidate_resources(
                    train_set=train_set,
                    validation_set=validation_set,
                    prompts=prompts,
                    eval_config=regression_config,
                    duration_seconds=time.perf_counter() - candidate_started,
                )
                gate = analyzer.gate(
                    baseline=baseline,
                    candidate_train=candidate_train,
                    candidate_validation=candidate_validation,
                    delta=delta,
                    optimizer_status=optimize_result.status,
                    resources=candidate_resources,
                    config=gate_config,
                )
                candidates.append(
                    CandidateEvaluation(
                        candidate_id=candidate_id,
                        prompts=prompts,
                        train=candidate_train,
                        validation=candidate_validation,
                        delta=delta,
                        gate=gate,
                        audit=CandidateAudit(
                            prompt_sha256=_sha256_text(json.dumps(prompts, sort_keys=True, ensure_ascii=False)),
                            source="GEPA on_proposal_end",
                            optimizer_round=optimizer_round,
                            seed=spec.seed,
                            resources=candidate_resources,
                        ),
                    ))
        finally:
            await target_prompt.write_all(baseline_prompts)

        analyzer.mark_pareto(candidates)
        accepted_candidates = [candidate for candidate in candidates if candidate.gate.accepted]
        selected = (max(
            accepted_candidates,
            key=lambda item: (
                item.validation.pass_rate,
                item.validation.average_score,
                -item.audit.resources.total_tokens,
                item.candidate_id,
            ),
        ) if accepted_candidates else None)
        if selected is not None and spec.apply_if_accepted:
            await target_prompt.write_all(selected.prompts)

        scoped_cases = {
            "baseline/train": baseline.train.cases,
            "baseline/validation": baseline.validation.cases,
        }
        for candidate_item in candidates:
            scoped_cases[f"candidate/{candidate_item.candidate_id}/train"] = candidate_item.train.cases
            scoped_cases[f"candidate/{candidate_item.candidate_id}/validation"] = candidate_item.validation.cases
        failure_summary = analyzer.failure_summary(scoped_cases)

        finished_at = datetime.now(timezone.utc)
        duration = time.perf_counter() - started_clock
        status = "accepted" if selected is not None else "rejected"
        report = OptimizationReport(
            status=status,
            baseline=baseline,
            candidates=candidates,
            selected_candidate_id=selected.candidate_id if selected is not None else None,
            candidate=selected,
            delta=selected.delta if selected is not None else None,
            gate=selected.gate if selected is not None else analyzer.combined_rejection_gate(candidates),
            failure_attribution=failure_summary,
            optimizer=OptimizerAudit(
                algorithm=optimize_result.algorithm,
                status=optimize_result.status,
                stop_reason=optimize_result.stop_reason,
                used_agent_optimizer=True,
                baseline_pass_rate=optimize_result.baseline_pass_rate,
                best_pass_rate=optimize_result.best_pass_rate,
                rounds=optimize_result.total_rounds,
                resources=optimizer_resources,
                artifact_dir="optimizer",
            ),
            data_quality=data_quality,
            audit=RunAudit(
                run_id=f"offline-{uuid.uuid4().hex[:12]}",
                started_at=started_at.isoformat(),
                finished_at=finished_at.isoformat(),
                duration_seconds=duration,
                seed=spec.seed,
                config_sha256=_sha256_text("".join(
                    path.read_text(encoding="utf-8") for path in (
                        spec.manifest_path,
                        spec.optimizer_config,
                        spec.regression_metrics_config,
                        spec.gate_config,
                    ))),
                train_sha256=_sha256_file(spec.train_dataset),
                validation_sha256=_sha256_file(spec.validation_dataset),
                baseline_prompt_sha256={
                    name: _sha256_text(text)
                    for name, text in baseline_prompts.items()
                },
                command="python examples/optimization/eval_optimize_loop/run_pipeline.py",
            ),
        )
        write_reports(report, spec.output_dir)
        return report
