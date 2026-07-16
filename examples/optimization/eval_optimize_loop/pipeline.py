from __future__ import annotations

import os
import time
import tempfile
from datetime import datetime, timezone
from typing import Callable, Optional

from trpc_agent_sdk.evaluation import (
    AgentEvaluator,
    AgentOptimizer,
    CallAgent,
    EvalCaseResult,
    EvalStatus,
    EvaluateResult,
    TargetPrompt,
)

from .delta import compute_delta
from .failure_attribution import attribute_failures
from .gate import apply_gate
from .models import (
    PerCaseResult,
    PipelineConfig,
    PipelineResult,
    SplitResult,
)
from .reporting import write_reports


class EvalOptimizePipeline:
    def __init__(self, config: PipelineConfig) -> None:
        self._config = config
        self._live_call_agent: Optional[CallAgent] = None
        self._live_target_prompt: Optional[TargetPrompt] = None
        self._optimizer_call: Optional[Callable] = None

    @classmethod
    def from_config(
        cls,
        config_path: str,
        *,
        call_agent: Optional[CallAgent] = None,
        target_prompt: Optional[TargetPrompt] = None,
    ) -> "EvalOptimizePipeline":
        with open(config_path, "r", encoding="utf-8") as f:
            raw = f.read()

        config = PipelineConfig.model_validate_json(raw)

        if config.mode == "live" and (call_agent is None or target_prompt is None):
            raise ValueError(
                "live mode requires call_agent and target_prompt "
                "to be passed to from_config()"
            )

        pipeline = cls(config)
        pipeline._live_call_agent = call_agent
        pipeline._live_target_prompt = target_prompt
        return pipeline

    async def run(self) -> PipelineResult:
        started_at = datetime.now(timezone.utc).isoformat()
        t0 = time.monotonic()

        if self._config.mode == "trace":
            baseline_train, baseline_val = await self._evaluate_trace_baseline()
            fa = attribute_failures(self._extract_case_results(baseline_train))
            candidate_train, candidate_val = await self._evaluate_trace_candidate()
        elif self._config.mode == "live":
            baseline_train, baseline_val = await self._evaluate_live_baseline()
            fa = attribute_failures(self._extract_case_results(baseline_train))
            await self._run_optimization()
            candidate_train, candidate_val = await self._evaluate_live_candidate()
        else:
            raise ValueError(f"unknown mode: {self._config.mode}")

        baseline_split = {
            "train": self._build_split_result(baseline_train),
            "val": self._build_split_result(baseline_val),
        }
        candidate_split = {
            "train": self._build_split_result(candidate_train),
            "val": self._build_split_result(candidate_val),
        }

        delta = compute_delta(baseline_split, candidate_split)

        duration = time.monotonic() - t0
        gate = apply_gate(
            delta, self._config.gate, cost_usd=0.0, duration_seconds=duration
        )

        finished_at = datetime.now(timezone.utc).isoformat()

        result = PipelineResult(
            mode=self._config.mode,
            gate_decision=gate.decision,
            gate_reasons=gate.reasons,
            baseline=baseline_split,
            candidate=candidate_split,
            delta=delta,
            failure_attribution=fa,
            overfitting_warning=gate.overfitting_warning,
            duration_seconds=duration,
            cost_usd=0.0,
            seed=self._config.seed,
            started_at=started_at,
            finished_at=finished_at,
        )

        write_reports(result, self._config.output_dir)
        return result

    # ── Trace mode evals ────────────────────────────────────────────

    async def _evaluate_trace_baseline(
        self,
    ) -> tuple[EvaluateResult, EvaluateResult]:
        train = await self._run_eval(self._config.train_baseline_evalset)
        val = await self._run_eval(self._config.val_baseline_evalset)
        return train, val

    async def _evaluate_trace_candidate(
        self,
    ) -> tuple[EvaluateResult, EvaluateResult]:
        train = await self._run_eval(self._config.train_candidate_evalset)
        val = await self._run_eval(self._config.val_candidate_evalset)
        return train, val

    async def _run_eval(self, evalset_path: str) -> EvaluateResult:
        eval_config_path = await self._write_eval_config_temp()
        try:
            executer = AgentEvaluator.get_executer(
                evalset_path,
                eval_metrics_file_path_or_dir=eval_config_path,
                print_detailed_results=False,
                print_summary_report=False,
            )
            await executer.evaluate()
            return executer.get_result()
        finally:
            os.unlink(eval_config_path)

    async def _write_eval_config_temp(self) -> str:
        fd, path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        with open(path, "w", encoding="utf-8") as f:
            f.write(
                self._config.evaluate.model_dump_json(indent=2, by_alias=True)
            )
        return path

    # ── Live mode evals ─────────────────────────────────────────────

    async def _evaluate_live_baseline(
        self,
    ) -> tuple[EvaluateResult, EvaluateResult]:
        train = await self._run_eval_with_agent(self._config.live_train_evalset)
        val = await self._run_eval_with_agent(self._config.live_val_evalset)
        return train, val

    async def _evaluate_live_candidate(
        self,
    ) -> tuple[EvaluateResult, EvaluateResult]:
        train = await self._run_eval_with_agent(self._config.live_train_evalset)
        val = await self._run_eval_with_agent(self._config.live_val_evalset)
        return train, val

    async def _run_eval_with_agent(self, evalset_path: str) -> EvaluateResult:
        eval_config_path = await self._write_eval_config_temp()
        try:
            executer = AgentEvaluator.get_executer(
                evalset_path,
                call_agent=self._live_call_agent,
                eval_metrics_file_path_or_dir=eval_config_path,
                print_detailed_results=False,
                print_summary_report=False,
            )
            await executer.evaluate()
            return executer.get_result()
        finally:
            os.unlink(eval_config_path)

    async def _run_optimization(self) -> None:
        if self._optimizer_call is not None:
            await self._optimizer_call(self)
            return

        await AgentOptimizer.optimize(
            config_path=self._config.optimizer_config_path,
            call_agent=self._live_call_agent,
            target_prompt=self._live_target_prompt,
            train_dataset_path=self._config.live_train_evalset,
            validation_dataset_path=self._config.live_val_evalset,
            output_dir=os.path.join(self._config.output_dir, "optimizer"),
            update_source=True,
            verbose=0,
        )

    # ── Result helpers ──────────────────────────────────────────────

    @staticmethod
    def _extract_case_results(
        eval_result: EvaluateResult,
    ) -> dict[str, list[EvalCaseResult]]:
        cases_by_id: dict[str, list[EvalCaseResult]] = {}
        for aggregate in eval_result.results_by_eval_set_id.values():
            for case_id, case_results in aggregate.eval_results_by_eval_id.items():
                if case_id not in cases_by_id:
                    cases_by_id[case_id] = []
                cases_by_id[case_id].extend(case_results)
        return cases_by_id

    def _build_split_result(self, eval_result: EvaluateResult) -> SplitResult:
        cases_by_id = self._extract_case_results(eval_result)

        per_case: dict[str, PerCaseResult] = {}
        metric_sums: dict[str, float] = {}
        metric_counts: dict[str, int] = {}
        passed_count = 0

        for case_id, case_results in cases_by_id.items():
            all_passed = all(
                cr.final_eval_status == EvalStatus.PASSED and not cr.error_message
                for cr in case_results
            )
            if all_passed:
                passed_count += 1

            run_scores: dict[str, list[float]] = {}
            for cr in case_results:
                for mr in cr.overall_eval_metric_results:
                    score = mr.score if mr.score is not None else 0.0
                    if mr.metric_name not in run_scores:
                        run_scores[mr.metric_name] = []
                    run_scores[mr.metric_name].append(score)

            avg_scores: dict[str, float] = {}
            for name, scores in run_scores.items():
                avg = sum(scores) / len(scores)
                avg_scores[name] = avg
                metric_sums[name] = metric_sums.get(name, 0.0) + avg
                metric_counts[name] = metric_counts.get(name, 0) + 1

            per_case[case_id] = PerCaseResult(
                case_id=case_id,
                passed=all_passed,
                metric_scores=avg_scores,
            )

        total = len(per_case)
        pass_rate = passed_count / total if total > 0 else 0.0

        metric_breakdown: dict[str, float] = {}
        for name in metric_sums:
            if metric_counts[name] > 0:
                metric_breakdown[name] = metric_sums[name] / metric_counts[name]

        return SplitResult(
            pass_rate=pass_rate,
            metric_breakdown=metric_breakdown,
            per_case=per_case,
        )
