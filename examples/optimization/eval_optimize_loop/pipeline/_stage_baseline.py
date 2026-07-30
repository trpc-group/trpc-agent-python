"""Stage 1: 基线评测 — 在训练集和验证集上运行 AgentEvaluator。

本阶段是流水线的起点，对训练集和验证集分别执行 trace 模式评测。
trace 模式下无需实际调用 agent，而是从预录制的对话轨迹（evalset.json）
中提取 actual_conversation 和 conversation，通过确定性 metric 计算评分。

评分维度（由 test_config.json 定义）：
  - final_response_avg_score: 检查 agent 最终回复是否包含期望文本
  - tool_trajectory_avg_score: 检查 agent 工具调用轨迹是否与期望一致

调用方式：
    raw_result, report = await BaselineEvaluator.evaluate(
        eval_set_path="data/train_baseline.evalset.json",
        metrics_config_path="data/test_config.json",
    )
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from trpc_agent_sdk.evaluation._agent_evaluator import AgentEvaluator
from trpc_agent_sdk.evaluation._eval_config import EvalConfig
from trpc_agent_sdk.evaluation._eval_metrics import EvalStatus
from trpc_agent_sdk.evaluation._eval_result import EvaluateResult

from pipeline._models import EvalSetReport, PerCaseScore


class BaselineEvaluator:
    """基线评测执行器。

    使用 AgentEvaluator.get_executer() 创建 trace 模式执行器，
    对指定的 evalset 文件运行评测，返回原始结果和结构化报告。

    trace 模式特点：
      - 不需要调用 agent（不需要 API key）
      - 从 evalset 的 actual_conversation 和 conversation 计算 metric
      - 确定性的评测过程，结果可复现
    """

    @staticmethod
    async def evaluate(
        *,
        eval_set_path: str,
        metrics_config_path: str,
    ) -> tuple[EvaluateResult, EvalSetReport]:
        """对单个评测集运行评测。

        Args:
            eval_set_path: .evalset.json 文件路径（trace 模式）。
            metrics_config_path: test_config.json metric 配置文件路径。

        Returns:
            (EvaluateResult, EvalSetReport) 元组：
            - EvaluateResult: SDK 原始评测结果，包含逐 case 详细信息
            - EvalSetReport: 结构化的评测集报告，便于下游阶段使用
        """
        # 加载 metric 配置
        with open(metrics_config_path, "r") as f:
            config_data = json.load(f)

        eval_config = EvalConfig(**config_data)
        eval_metrics = eval_config.get_eval_metrics()

        # 创建 trace 模式执行器（无需 agent 回调）
        executer = AgentEvaluator.get_executer(
            eval_dataset_file_path_or_dir=eval_set_path,
            eval_metrics_file_path_or_dir=metrics_config_path,
            num_runs=1,
        )

        # 执行评测（部分 case 可能失败，但 evaluator 会填充结果后再抛异常）
        try:
            await executer.evaluate()
        except Exception:
            pass

        # 获取评测结果并构建结构化报告
        raw_result: EvaluateResult = executer.get_result()
        if raw_result is None:
            raise RuntimeError(f"评测失败 ({eval_set_path}): 无返回结果")
        report = BaselineEvaluator._build_report(raw_result)
        return raw_result, report

    @staticmethod
    def _build_report(raw_result: EvaluateResult) -> EvalSetReport:
        """将 SDK 原始 EvaluateResult 转换为结构化 EvalSetReport。

        遍历每个评测 case，统计通过/失败数，汇总各 metric 得分，
        计算整体 pass rate。

        Args:
            raw_result: SDK 返回的原始评测结果。

        Returns:
            EvalSetReport: 结构化的评测集报告。
        """
        per_case: list[PerCaseScore] = []
        total = 0
        passed = 0
        failed = 0
        metric_scores: dict[str, list[float]] = {}
        captured_set_id = "unknown"

        # 遍历每个评测集的结果
        for eval_set_id, aggregate in raw_result.results_by_eval_set_id.items():
            captured_set_id = eval_set_id
            # 遍历每个 case 的评测结果
            for eval_id, runs in aggregate.eval_results_by_eval_id.items():
                total += 1
                case_result = runs[0]  # num_runs=1，取第一次运行

                # 统计通过/失败
                if case_result.final_eval_status == EvalStatus.PASSED:
                    passed += 1
                elif case_result.final_eval_status == EvalStatus.FAILED:
                    failed += 1

                # 提取各 metric 的得分和状态
                scores: dict[str, float] = {}
                statuses: dict[str, str] = {}
                for m in case_result.overall_eval_metric_results:
                    scores[m.metric_name] = m.score or 0.0
                    statuses[m.metric_name] = str(m.eval_status.name) if m.eval_status else "NOT_EVALUATED"
                    # 收集各 metric 得分用于计算平均值
                    if m.metric_name not in metric_scores:
                        metric_scores[m.metric_name] = []
                    metric_scores[m.metric_name].append(m.score or 0.0)

                per_case.append(PerCaseScore(
                    eval_id=eval_id,
                    overall_status=str(case_result.final_eval_status.name) if case_result.final_eval_status else "NOT_EVALUATED",
                    metric_scores=scores,
                    metric_statuses=statuses,
                ))

        # 计算各 metric 的平均分
        breakdown = {
            name: sum(scores) / len(scores) if scores else 0.0
            for name, scores in metric_scores.items()
        }

        return EvalSetReport(
            eval_set_id=captured_set_id,
            num_cases=total,
            num_passed=passed,
            num_failed=failed,
            pass_rate=passed / total if total > 0 else 0.0,
            metric_breakdown=breakdown,
            per_case=per_case,
        )
