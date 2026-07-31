"""PipelineRunner — 编排 6 阶段流水线, 通过 EvalBackend 收敛 demo/real 差异."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from trpc_agent_sdk.evaluation._target_prompt import TargetPrompt

from pipeline._eval_backend import EvalBackend
from pipeline._models import (
    AcceptanceGateConfig,
    PerCaseScore,
    PipelineReport,
)
from pipeline._stage_acceptance_gate import AcceptanceGate
from pipeline._stage_audit_trail import ReportGenerator
from pipeline._stage_baseline import BaselineEvaluator
from pipeline._stage_failure_attribution import FailureAttributor
from pipeline._stage_optimization import OptimizationExecutor
from pipeline._stage_validation import ValidationComparator


class PipelineRunner:
    def __init__(
        self,
        *,
        train_eval_path: str,
        val_baseline_eval_path: str,
        gate_metrics_config_path: str,
        optimizer_config_path: str,
        prompt_source_path: str,
        prompt_field_name: str,
        gate_config: AcceptanceGateConfig,
        backend: EvalBackend,
        demo_mode: bool,
        output_dir: str = "output",
        scenario_map: Optional[dict[str, str]] = None,
        demo_optimize_result_path: Optional[str] = None,  # 仅 demo_mode=True 时有意义
        train_eval_path_real: Optional[str] = None,        # 仅 demo_mode=False 时使用
    ) -> None:
        self._train_path = train_eval_path
        self._val_baseline_path = val_baseline_eval_path
        self._train_path_real = train_eval_path_real
        self._gate_metrics_path = gate_metrics_config_path
        self._optimizer_config_path = optimizer_config_path
        self._prompt_source_path = prompt_source_path
        self._prompt_field_name = prompt_field_name
        self._gate_config = gate_config
        self._backend = backend
        self._demo_mode = demo_mode
        self._output_dir = output_dir
        self._scenario_map = scenario_map or {}
        self._demo_result_path = demo_optimize_result_path

    def set_scenario_map(self, scenario_map: dict[str, str]) -> None:
        """Public setter for scenario_map — Task 14 uses this to avoid direct
        private-attribute mutation."""
        self._scenario_map = scenario_map

    async def run(self) -> PipelineReport:
        start = time.time()
        run_ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        run_dir = Path(self._output_dir) / run_ts
        run_dir.mkdir(parents=True, exist_ok=True)

        report = PipelineReport(
            pipeline_version="1.0.0",
            timestamp=datetime.now(timezone.utc).isoformat(),
            demo_mode=self._demo_mode,
        )

        # ---- Stage 1: 基线 ----
        print("[Stage 1/6] 基线评测...")
        _, train_report = await BaselineEvaluator.evaluate(
            eval_set_path=self._train_path,
            metrics_config_path=self._gate_metrics_path,
            backend=self._backend,
            num_runs=2,
        )
        _, val_report = await BaselineEvaluator.evaluate(
            eval_set_path=self._val_baseline_path,
            metrics_config_path=self._gate_metrics_path,
            backend=self._backend,
            num_runs=2,
        )
        report.baseline_train = train_report
        report.baseline_val = val_report

        # ---- Stage 2: 失败归因 ----
        print("[Stage 2/6] 失败归因...")
        # 合并 train + val 的 per_case 为 dict[eval_id, PerCaseScore]
        combined: dict[str, PerCaseScore] = {}
        for c in train_report.per_case:
            combined[c.eval_id] = c
        for c in val_report.per_case:
            combined[c.eval_id] = c
        attribution = FailureAttributor.cluster_from_per_case(combined)
        report.failure_attribution = attribution

        # ---- Stage 3: 优化 ----
        print("[Stage 3/6] 优化执行...")
        if self._demo_mode and self._demo_result_path:
            opt_report = OptimizationExecutor.run_demo(self._demo_result_path)
        else:
            from agent.agent import create_agent

            target_prompt = TargetPrompt().add_path(self._prompt_field_name, self._prompt_source_path)

            async def call_agent(input_text: str) -> str:
                from trpc_agent_sdk.types import Content, Part
                # agent 每次重建以重读 system.md
                agent = create_agent(demo_mode=False)
                user_content = Content(parts=[Part.from_text(text=input_text)])
                response = await agent.generate_content(user_content)
                if response.candidates and response.candidates[0].content:
                    return "".join(p.text or "" for p in response.candidates[0].content.parts)
                return ""

            opt_report = await OptimizationExecutor.run_real(
                config_path=self._optimizer_config_path,
                call_agent=call_agent,
                target_prompt=target_prompt,
                train_dataset_path=self._train_path_real or self._train_path,
                validation_dataset_path=self._val_baseline_path,
                output_dir=str(run_dir),
            )
        report.optimization_execution = opt_report

        # ---- Stage 4: 候选验证 ----
        print("[Stage 4/6] 候选验证...")
        target_prompt_obj = TargetPrompt().add_path(self._prompt_field_name, self._prompt_source_path)
        candidate_report, deltas = await ValidationComparator.evaluate_and_compare(
            backend=self._backend,
            val_eval_path=self._val_baseline_path,
            metrics_config_path=self._gate_metrics_path,
            target_prompt=target_prompt_obj,
            best_prompts=opt_report.best_prompts,
            baseline_report=val_report,
            scenario_map=self._scenario_map,
        )
        report.candidate_validation = candidate_report
        report.case_deltas = deltas

        # ---- Stage 5: 门控 ----
        print("[Stage 5/6] 接受门控...")
        gate = AcceptanceGate(self._gate_config)
        decision = gate.evaluate(
            baseline_pass_rate=val_report.pass_rate,
            candidate_pass_rate=candidate_report.pass_rate,
            baseline_case_statuses={c.eval_id: c.overall_status for c in val_report.per_case},
            candidate_case_statuses={c.eval_id: c.overall_status for c in candidate_report.per_case},
            total_cost=opt_report.total_llm_cost,
        )
        report.gate_decision = decision
        report.overall_pass_rate_change = candidate_report.pass_rate - val_report.pass_rate
        report.overall_verdict = "ACCEPTED" if decision.accepted else "REJECTED"

        # ---- Stage 6: 报告 ----
        print("[Stage 6/6] 审计落盘...")
        report.pipeline_duration_seconds = time.time() - start
        ReportGenerator.generate_json(report, str(run_dir))
        ReportGenerator.generate_markdown(report, str(run_dir))
        return report