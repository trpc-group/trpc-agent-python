"""PipelineRunner — 编排 6 阶段评测+优化流水线。

PipelineRunner 是整个流水线的核心编排器，负责按顺序执行六个阶段：
  1. 基线评测（Baseline Evaluation）  — 在训练集和验证集上运行 AgentEvaluator
  2. 失败归因（Failure Attribution）   — 对失败 case 分类并聚类
  3. 优化执行（Optimization Execution）— 运行 AgentOptimizer 或加载 demo 结果
  4. 候选验证（Candidate Validation）  — 重新评估验证集，计算逐 case delta
  5. 接受门控（Acceptance Gate）       — 按可配置标准评估候选 prompt
  6. 审计轨迹（Audit Trail）           — 生成 optimization_report.json + .md

调用方式：
    runner = PipelineRunner(...)
    report = await runner.run()  # 返回 PipelineReport
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from trpc_agent_sdk.evaluation._eval_metrics import EvalStatus
from trpc_agent_sdk.evaluation._target_prompt import TargetPrompt

from pipeline._models import (
    AcceptanceGateConfig,
    EvalSetReport,
    GateDecision,
    PipelineReport,
)
from pipeline._stage_acceptance_gate import AcceptanceGate
from pipeline._stage_audit_trail import ReportGenerator
from pipeline._stage_baseline import BaselineEvaluator
from pipeline._stage_failure_attribution import FailureAttributor
from pipeline._stage_optimization import OptimizationExecutor
from pipeline._stage_validation import ValidationComparator


class PipelineRunner:
    """评测 + 优化流水线编排器。

    负责串联 6 个阶段，传递各阶段的输出给下游阶段，最终生成完整报告。

    属性（私有）:
        _train_path: 训练集 evalset.json 路径
        _val_baseline_path: 验证集（基线）evalset.json 路径
        _val_optimized_path: 验证集（优化后）evalset.json 路径
        _metrics_config_path: test_config.json metric 配置路径
        _optimizer_config_path: optimizer.json 优化器配置路径
        _prompt_source_path: 系统 prompt 文件路径
        _prompt_field_name: prompt 字段名（如 system_prompt）
        _gate_config: 接受门控配置
        _demo_result_path: demo 模式下的预生成优化结果路径
        _output_dir: 报告输出目录
        _demo_mode: 是否为 demo 模式
        _scenario_map: eval_id → scenario 类型的映射
    """

    def __init__(
        self,
        *,
        train_eval_path: str,
        val_baseline_eval_path: str,
        val_optimized_eval_path: str,
        metrics_config_path: str,
        optimizer_config_path: str,
        prompt_source_path: str,
        prompt_field_name: str,
        gate_config: AcceptanceGateConfig,
        demo_optimize_result_path: Optional[str] = None,
        output_dir: str = "output",
        demo_mode: bool = True,
        scenario_map: Optional[dict[str, str]] = None,
    ) -> None:
        """初始化流水线运行器。

        Args:
            train_eval_path: 训练集 evalset 文件路径（trace 模式）。
            val_baseline_eval_path: 验证集（基线）evalset 文件路径。
            val_optimized_eval_path: 验证集（优化后）evalset 文件路径。
            metrics_config_path: metric 配置文件路径（test_config.json）。
            optimizer_config_path: 优化器配置文件路径（optimizer.json）。
            prompt_source_path: 系统 prompt 源文件路径。
            prompt_field_name: prompt 在优化器中注册的字段名。
            gate_config: 接受门控配置（AcceptanceGateConfig 实例）。
            demo_optimize_result_path: demo 模式的预生成优化结果 JSON 路径。
            output_dir: 报告输出目录（默认 "output"）。
            demo_mode: 是否以 demo 模式运行（不调用真实 LLM）。
            scenario_map: eval_id → scenario 类型的可选映射。
        """
        self._train_path = train_eval_path
        self._val_baseline_path = val_baseline_eval_path
        self._val_optimized_path = val_optimized_eval_path
        self._metrics_config_path = metrics_config_path
        self._optimizer_config_path = optimizer_config_path
        self._prompt_source_path = prompt_source_path
        self._prompt_field_name = prompt_field_name
        self._gate_config = gate_config
        self._demo_result_path = demo_optimize_result_path
        self._output_dir = output_dir
        self._demo_mode = demo_mode
        self._scenario_map = scenario_map or {}

    async def run(self) -> PipelineReport:
        """执行完整的 6 阶段流水线。

        各阶段按顺序执行，前阶段的输出作为后阶段的输入。

        Returns:
            PipelineReport: 包含所有阶段输出的完整流水线报告。
        """
        start_time = time.time()
        timestamp = datetime.now(timezone.utc).isoformat()

        # 确保输出目录存在
        os.makedirs(self._output_dir, exist_ok=True)

        # 初始化报告对象
        report = PipelineReport(
            pipeline_version="1.0.0",
            timestamp=timestamp,
            demo_mode=self._demo_mode,
        )

        # ================================================================
        # Stage 1: 基线评测 — 在训练集和验证集上运行 AgentEvaluator
        # trace 模式下无需 agent 调用，直接从预录制轨迹计算 metric
        # ================================================================
        print("[Stage 1/6] 执行基线评测...")
        raw_train, train_report = await BaselineEvaluator.evaluate(
            eval_set_path=self._train_path,
            metrics_config_path=self._metrics_config_path,
        )
        raw_val, val_report = await BaselineEvaluator.evaluate(
            eval_set_path=self._val_baseline_path,
            metrics_config_path=self._metrics_config_path,
        )
        report.baseline_train = train_report
        report.baseline_val = val_report
        print(f"  训练集: {train_report.num_passed}/{train_report.num_cases} 通过 (pass_rate={train_report.pass_rate:.4f})")
        print(f"  验证集: {val_report.num_passed}/{val_report.num_cases} 通过 (pass_rate={val_report.pass_rate:.4f})")

        # ================================================================
        # Stage 2: 失败归因 — 对训练集和验证集的所有失败 case 分类并聚类
        # 合并两个评测集的原始结果，统一交给 FailureAttributor 处理
        # ================================================================
        print("[Stage 2/6] 执行失败归因...")
        combined_results = {}
        for eval_set_id, aggregate in raw_train.results_by_eval_set_id.items():
            combined_results.update(aggregate.eval_results_by_eval_id)
        for eval_set_id, aggregate in raw_val.results_by_eval_set_id.items():
            combined_results.update(aggregate.eval_results_by_eval_id)

        attribution = FailureAttributor.cluster(combined_results)
        report.failure_attribution = attribution
        print(f"  {attribution.total_failed} 个失败 case，分布在 {len(attribution.clusters)} 个类别")

        # ================================================================
        # Stage 3: 优化执行 — demo 模式加载预生成结果，real 模式调用 AgentOptimizer
        # ================================================================
        print("[Stage 3/6] 执行优化...")
        if self._demo_mode and self._demo_result_path:
            # Demo 模式：直接从 JSON 加载预生成的 OptimizeResult
            opt_report = OptimizationExecutor.run_demo(self._demo_result_path)
            print(f"  Demo 模式：加载预生成结果（{opt_report.algorithm}，{opt_report.total_rounds} 轮）")
        else:
            # Real 模式：创建 agent 并通过 AgentOptimizer 进行真实优化
            from agent.agent import create_agent

            agent = create_agent(demo_mode=False)

            async def call_agent(input_text: str) -> str:
                """agent 推理回调，供 AgentOptimizer 使用。"""
                from trpc_agent_sdk.types import Content, Part

                user_content = Content(parts=[Part.from_text(text=input_text)])
                response = await agent.generate_content(user_content)
                if response.candidates and response.candidates[0].content:
                    return "".join(
                        part.text or ""
                        for part in response.candidates[0].content.parts
                    )
                return ""

            target_prompt = (
                TargetPrompt()
                .add_path(self._prompt_field_name, self._prompt_source_path)
            )
            opt_report = await OptimizationExecutor.run_real(
                config_path=self._optimizer_config_path,
                call_agent=call_agent,
                target_prompt=target_prompt,
                train_dataset_path=self._train_path,
                validation_dataset_path=self._val_baseline_path,
                output_dir=self._output_dir,
            )
            print(f"  Real 模式：优化完成（{opt_report.algorithm}，{opt_report.total_rounds} 轮）")
        report.optimization_execution = opt_report

        # ================================================================
        # Stage 4: 候选验证 — 重新评估优化后的验证集，与基线逐 case 对比
        # 计算每个 case 的状态转换和分数变化
        # ================================================================
        print("[Stage 4/6] 执行候选验证...")
        candidate_report, case_deltas = await ValidationComparator.evaluate_and_compare(
            optimized_eval_set_path=self._val_optimized_path,
            metrics_config_path=self._metrics_config_path,
            baseline_report=val_report,
            scenario_map=self._scenario_map,
        )
        report.candidate_validation = candidate_report
        report.case_deltas = case_deltas
        print(f"  候选: {candidate_report.num_passed}/{candidate_report.num_cases} 通过 (pass_rate={candidate_report.pass_rate:.4f})")
        for d in case_deltas:
            # 只打印状态发生变化的 case
            if d.transition != "PASSED->PASSED" and d.transition != "FAILED->FAILED":
                print(f"  Delta: {d.eval_id} {d.transition} ({d.scenario})")

        # ================================================================
        # Stage 5: 接受门控 — 按可配置标准评估候选 prompt 是否可接受
        # 检查项：提升阈值、回归限制、关键 case、成本预算
        # ================================================================
        print("[Stage 5/6] 评估接受门控...")
        gate = AcceptanceGate(self._gate_config)

        # 从报告中提取每个 case 的状态映射
        baseline_statuses = {c.eval_id: c.overall_status for c in val_report.per_case}
        candidate_statuses = {c.eval_id: c.overall_status for c in candidate_report.per_case}

        decision = gate.evaluate(
            baseline_pass_rate=val_report.pass_rate,
            candidate_pass_rate=candidate_report.pass_rate,
            baseline_case_statuses=baseline_statuses,
            candidate_case_statuses=candidate_statuses,
            total_cost=opt_report.total_llm_cost,
        )
        report.gate_decision = decision

        # 计算整体 pass rate 变化并生成最终判决
        report.overall_pass_rate_change = candidate_report.pass_rate - val_report.pass_rate
        report.overall_verdict = "ACCEPTED" if decision.accepted else "REJECTED"

        print(f"  门控决定: {report.overall_verdict}")
        print(f"  原因: {decision.reason}")

        # ================================================================
        # Stage 6: 审计轨迹 — 生成 JSON 和 Markdown 格式的报告
        # ================================================================
        print("[Stage 6/6] 生成审计报告...")
        report.pipeline_duration_seconds = time.time() - start_time

        json_path = ReportGenerator.generate_json(report, self._output_dir)
        md_path = ReportGenerator.generate_markdown(report, self._output_dir)
        print(f"  JSON 报告: {json_path}")
        print(f"  Markdown 报告: {md_path}")

        print(f"\n流水线完成，耗时 {report.pipeline_duration_seconds:.2f}s")
        return report
