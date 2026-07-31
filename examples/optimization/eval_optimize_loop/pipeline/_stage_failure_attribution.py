"""Stage 2: 失败归因 — 对评测失败的 case 进行分类和聚类。

本阶段分析 Stage 1 基线评测的失败 case，通过检查各 metric 的失败状态
将失败归类为以下类型之一：
  - final_response_mismatch: 最终回复文本与期望不匹配
  - tool_trajectory_mismatch: 工具调用轨迹与期望不一致
  - both_metrics_failed: 回复和工具两个维度均失败（根本性偏差）
  - llm_rubric_fail: LLM 评判 rubric 未通过
  - knowledge_recall_insufficient: 知识库召回不足
  - unknown: 无法归类的失败

同一 case 可能归入多个类别（如同时存在回复不匹配和 rubric 失败）。
聚类结果用于识别主要失败模式，为 Stage 3 优化提供数据支持。

调用方式：
    # 推荐：直接从 Stage 1 报告的 per_case 派生（不依赖 SDK 内部结构）
    attribution_report = FailureAttributor.cluster_from_per_case(per_case)

    # 兼容旧路径：从 SDK EvalCaseResult 派生
    attribution_report = FailureAttributor.cluster(results_by_eval_id)
"""

from __future__ import annotations

from collections import defaultdict

from trpc_agent_sdk.evaluation._eval_metrics import EvalStatus
from trpc_agent_sdk.evaluation._eval_result import EvalCaseResult

from pipeline._models import FailureAttributionReport, PerCaseScore


class FailureAttributor:
    """失败归因器：分类并聚类评测中的失败 case。

    核心方法：
      - cluster_from_per_case(): 从 Stage 1 报告的 per_case 聚类（推荐，无 SDK 依赖）
      - classify(): 对单个 EvalCaseResult 按 metric 状态分类
      - cluster(): 对一批 case 按类别聚类，生成 FailureAttributionReport

    分类逻辑：
      1. 检查 case 是否 FAILED（通过的 case 不归因）
      2. 检查 final_response_avg_score 和 tool_trajectory_avg_score
         - 两者同时失败 → both_metrics_failed
         - 仅一个失败 → 对应单独类别
      3. 检查 LLM rubric metric（llm_rubric_response / llm_rubric_knowledge_recall）
      4. 无任何已知 metric 失败 → unknown
    """

    @staticmethod
    def cluster_from_per_case(
        per_case: dict[str, PerCaseScore],
    ) -> FailureAttributionReport:
        """从 Stage 1 报告的 per_case 派生失败归因（不依赖 SDK EvalCaseResult）。

        与 cluster() 的分类逻辑等价，但输入是 Stage 1 已产出的 PerCaseScore，
        使 Stage 2 完全自给，不再耦合 SDK 内部数据结构。

        Args:
            per_case: eval_id → PerCaseScore 的映射（来自 EvalSetReport.per_case）。

        Returns:
            FailureAttributionReport: 包含聚类结果、逐 case 类别和摘要。
        """
        clusters: dict[str, list[str]] = defaultdict(list)
        per_case_cats: dict[str, list[str]] = {}
        total = len(per_case)
        total_failed = 0

        for eval_id, score in per_case.items():
            if score.overall_status != "FAILED":
                # 通过 / 未评测的 case 不需要归因
                per_case_cats[eval_id] = []
                continue

            total_failed += 1
            categories: list[str] = []
            statuses = score.metric_statuses

            # 确定性 metric：回复匹配与工具轨迹匹配
            final_resp_failed = (
                statuses.get("final_response_avg_score") == "FAILED"
            )
            tool_traj_failed = (
                statuses.get("tool_trajectory_avg_score") == "FAILED"
            )

            if final_resp_failed and tool_traj_failed:
                # 两个核心 metric 同时失败，说明行为层面出现根本偏差
                categories.append("both_metrics_failed")
            else:
                if final_resp_failed:
                    categories.append("final_response_mismatch")
                if tool_traj_failed:
                    categories.append("tool_trajectory_mismatch")

            # LLM rubric metric（软性评估）
            if statuses.get("llm_rubric_response") == "FAILED":
                categories.append("llm_rubric_fail")

            if statuses.get("llm_rubric_knowledge_recall") == "FAILED":
                categories.append("knowledge_recall_insufficient")

            # 兜底：无已知 metric 失败
            if not categories:
                categories.append("unknown")

            for category in categories:
                clusters[category].append(eval_id)
            per_case_cats[eval_id] = categories

        summary = FailureAttributor._build_summary(
            total, total_failed, dict(clusters)
        )

        return FailureAttributionReport(
            total_cases_evaluated=total,
            total_failed=total_failed,
            clusters=dict(clusters),
            per_case_categories=per_case_cats,
            summary=summary,
        )

    @staticmethod
    def classify(case_result: EvalCaseResult) -> list[str]:
        """对单个评测结果进行失败分类。

        Args:
            case_result: 单个 case 的评测结果（EvalCaseResult 实例）。

        Returns:
            失败类别字符串列表。如果 case 通过，返回空列表。
            可能返回多个类别（如同时命中 final_response_mismatch 和 llm_rubric_fail）。
        """
        # 通过的 case 不需要归因
        if case_result.final_eval_status != EvalStatus.FAILED:
            return []

        metric_results = case_result.overall_eval_metric_results
        if not metric_results:
            return ["unknown"]

        # 构建 metric_name → eval_status 映射
        metric_status_map = {
            m.metric_name: m.eval_status
            for m in metric_results
        }

        categories: list[str] = []

        # 检查确定性 metric（回复匹配和工具轨迹匹配）
        final_resp_failed = metric_status_map.get("final_response_avg_score") == EvalStatus.FAILED
        tool_traj_failed = metric_status_map.get("tool_trajectory_avg_score") == EvalStatus.FAILED

        if final_resp_failed and tool_traj_failed:
            # 两个核心 metric 同时失败，说明行为层面出现根本偏差
            categories.append("both_metrics_failed")
        else:
            if final_resp_failed:
                categories.append("final_response_mismatch")
            if tool_traj_failed:
                categories.append("tool_trajectory_mismatch")

        # 检查 LLM rubric metric（软性评估）
        if metric_status_map.get("llm_rubric_response") == EvalStatus.FAILED:
            categories.append("llm_rubric_fail")

        if metric_status_map.get("llm_rubric_knowledge_recall") == EvalStatus.FAILED:
            categories.append("knowledge_recall_insufficient")

        # 兜底：无已知 metric 失败
        if not categories:
            categories.append("unknown")

        return categories

    @staticmethod
    def cluster(
        results_by_eval_id: dict[str, list[EvalCaseResult]],
    ) -> FailureAttributionReport:
        """对一批 case 按失败类别聚类。

        Args:
            results_by_eval_id: eval_id → EvalCaseResult 列表的映射
                （列表长度取决于 num_runs，通常为 1）。

        Returns:
            FailureAttributionReport: 包含聚类结果、逐 case 类别和摘要。
        """
        clusters: dict[str, list[str]] = defaultdict(list)
        per_case: dict[str, list[str]] = {}
        total = 0
        total_failed = 0

        for eval_id, runs in results_by_eval_id.items():
            total += 1
            case_result = runs[0]  # 取第一次运行的分类结果

            if case_result.final_eval_status == EvalStatus.FAILED:
                total_failed += 1
                categories = FailureAttributor.classify(case_result)
                per_case[eval_id] = categories
                # 将 case 添加到对应的类别聚类中
                for category in categories:
                    clusters[category].append(eval_id)
            else:
                per_case[eval_id] = []

        # 生成人类可读摘要
        summary = FailureAttributor._build_summary(
            total, total_failed, dict(clusters)
        )

        return FailureAttributionReport(
            total_cases_evaluated=total,
            total_failed=total_failed,
            clusters=dict(clusters),
            per_case_categories=per_case,
            summary=summary,
        )

    @staticmethod
    def _build_summary(
        total: int,
        total_failed: int,
        clusters: dict[str, list[str]],
    ) -> str:
        """生成人类可读的失败归因摘要。

        Args:
            total: case 总数。
            total_failed: 失败 case 数。
            clusters: 类别 → case ID 列表的聚类结果。

        Returns:
            摘要字符串（如 "4 out of 10 cases failed. Main issues: ..."）。
        """
        if total_failed == 0:
            return f"全部 {total} 个 case 通过。无失败需要归因。"

        lines = [f"{total_failed}/{total} 个 case 失败。"]

        if clusters:
            lines.append("失败分类:")
            for category, case_ids in sorted(clusters.items()):
                lines.append(f"  - {category}: {len(case_ids)} 个 case")

        return " ".join(lines)
