"""失败归因（Stage 2）单元测试。

测试 FailureAttributor 的分类和聚类逻辑，覆盖：
  - 通过 case（空分类）
  - 单一 metric 失败（final_response / tool_trajectory）
  - 双重 metric 失败（both_metrics_failed）
  - LLM rubric 失败（llm_rubric_fail / knowledge_recall_insufficient）
  - 未知失败（无 metric 数据）
  - 批量聚类和摘要生成
"""

import pytest

from trpc_agent_sdk.evaluation._eval_metrics import EvalMetric, EvalStatus
from trpc_agent_sdk.evaluation._eval_result import EvalCaseResult, EvalMetricResult


def make_case_result(eval_id, eval_status, metric_results):
    """创建测试用 EvalCaseResult 的辅助函数。

    Args:
        eval_id: case ID。
        eval_status: 最终状态（EvalStatus.PASSED 或 FAILED）。
        metric_results: EvalMetricResult 列表。

    Returns:
        EvalCaseResult 实例。
    """
    return EvalCaseResult(
        eval_set_id="test_set",
        eval_id=eval_id,
        final_eval_status=eval_status,
        overall_eval_metric_results=metric_results,
        eval_metric_result_per_invocation=[],
        session_id=f"session_{eval_id}",
    )


def make_metric_result(metric_name, eval_status, score=0.0, threshold=1.0):
    """创建测试用 EvalMetricResult 的辅助函数。

    Args:
        metric_name: metric 名称。
        eval_status: 评估状态。
        score: 得分。
        threshold: 阈值。

    Returns:
        EvalMetricResult 实例。
    """
    metric = EvalMetric(metric_name=metric_name, threshold=threshold)
    result = EvalMetricResult(
        metric_name=metric.metric_name,
        threshold=metric.threshold,
        score=score,
        eval_status=eval_status,
    )
    return result


class TestFailureAttribution:
    """FailureAttributor 的单元测试。

    验证失败分类逻辑的正确性和聚类报告的结构完整性。
    """

    @pytest.fixture
    def attributor(self):
        """导入 FailureAttributor 类作为 fixture。"""
        from pipeline._stage_failure_attribution import FailureAttributor
        return FailureAttributor

    def test_classify_passing_case(self, attributor):
        """通过的 case 应返回空失败类别列表。"""
        result = make_case_result(
            "case_001",
            EvalStatus.PASSED,
            [
                make_metric_result("final_response_avg_score", EvalStatus.PASSED, score=1.0),
                make_metric_result("tool_trajectory_avg_score", EvalStatus.PASSED, score=1.0),
            ],
        )
        categories = attributor.classify(result)
        assert categories == []

    def test_classify_final_response_failure(self, attributor):
        """仅 final_response_avg_score 失败时，应返回 'final_response_mismatch'。"""
        result = make_case_result(
            "case_001",
            EvalStatus.FAILED,
            [
                make_metric_result("final_response_avg_score", EvalStatus.FAILED, score=0.0),
                make_metric_result("tool_trajectory_avg_score", EvalStatus.PASSED, score=1.0),
            ],
        )
        categories = attributor.classify(result)
        assert "final_response_mismatch" in categories

    def test_classify_tool_trajectory_failure(self, attributor):
        """仅 tool_trajectory_avg_score 失败时，应返回 'tool_trajectory_mismatch'。"""
        result = make_case_result(
            "case_002",
            EvalStatus.FAILED,
            [
                make_metric_result("final_response_avg_score", EvalStatus.PASSED, score=1.0),
                make_metric_result("tool_trajectory_avg_score", EvalStatus.FAILED, score=0.0),
            ],
        )
        categories = attributor.classify(result)
        assert "tool_trajectory_mismatch" in categories

    def test_classify_both_failed(self, attributor):
        """两个 metric 同时失败时，应返回 'both_metrics_failed' 而非单独类别。"""
        result = make_case_result(
            "case_003",
            EvalStatus.FAILED,
            [
                make_metric_result("final_response_avg_score", EvalStatus.FAILED, score=0.0),
                make_metric_result("tool_trajectory_avg_score", EvalStatus.FAILED, score=0.0),
            ],
        )
        categories = attributor.classify(result)
        assert categories == ["both_metrics_failed"]

    def test_classify_unknown(self, attributor):
        """无 metric 数据的失败 case 应返回 'unknown'。"""
        result = make_case_result("case_004", EvalStatus.FAILED, [])
        categories = attributor.classify(result)
        assert categories == ["unknown"]

    def test_cluster_multiple_cases(self, attributor):
        """多个失败 case 应按类别正确聚类。

        创建 3 个失败 case（2 个回复不匹配 + 1 个工具轨迹不匹配）
        和 1 个通过 case，验证聚类结果。
        """
        case1 = make_case_result(
            "c1", EvalStatus.FAILED,
            [make_metric_result("final_response_avg_score", EvalStatus.FAILED, score=0.0)],
        )
        case2 = make_case_result(
            "c2", EvalStatus.FAILED,
            [make_metric_result("final_response_avg_score", EvalStatus.FAILED, score=0.0)],
        )
        case3 = make_case_result(
            "c3", EvalStatus.FAILED,
            [make_metric_result("tool_trajectory_avg_score", EvalStatus.FAILED, score=0.0)],
        )
        case4 = make_case_result(
            "c4", EvalStatus.PASSED,
            [make_metric_result("final_response_avg_score", EvalStatus.PASSED, score=1.0)],
        )

        results_by_id = {"c1": [case1], "c2": [case2], "c3": [case3], "c4": [case4]}
        report = attributor.cluster(results_by_id)

        assert report.total_cases_evaluated == 4
        assert report.total_failed == 3
        assert "final_response_mismatch" in report.clusters
        assert "tool_trajectory_mismatch" in report.clusters
        assert len(report.clusters["final_response_mismatch"]) == 2
        assert len(report.clusters["tool_trajectory_mismatch"]) == 1

    def test_cluster_empty(self, attributor):
        """空结果集应产生空报告。"""
        report = attributor.cluster({})
        assert report.total_cases_evaluated == 0
        assert report.total_failed == 0
        assert report.clusters == {}

    def test_cluster_all_passing(self, attributor):
        """全部通过的 case 应产生空聚类报告。"""
        case1 = make_case_result(
            "c1", EvalStatus.PASSED,
            [make_metric_result("final_response_avg_score", EvalStatus.PASSED, score=1.0)],
        )
        case2 = make_case_result(
            "c2", EvalStatus.PASSED,
            [make_metric_result("final_response_avg_score", EvalStatus.PASSED, score=1.0)],
        )
        results_by_id = {"c1": [case1], "c2": [case2]}
        report = attributor.cluster(results_by_id)

        assert report.total_cases_evaluated == 2
        assert report.total_failed == 0
        assert report.clusters == {}

    def test_classify_llm_rubric_failure(self, attributor):
        """llm_rubric_response 失败时应返回 'llm_rubric_fail'。"""
        result = make_case_result(
            "case_005",
            EvalStatus.FAILED,
            [
                make_metric_result("final_response_avg_score", EvalStatus.PASSED, score=1.0),
                make_metric_result("llm_rubric_response", EvalStatus.FAILED, score=0.3, threshold=0.66),
            ],
        )
        categories = attributor.classify(result)
        assert "llm_rubric_fail" in categories

    def test_classify_knowledge_recall_failure(self, attributor):
        """llm_rubric_knowledge_recall 失败时应返回 'knowledge_recall_insufficient'。"""
        result = make_case_result(
            "case_006",
            EvalStatus.FAILED,
            [
                make_metric_result("final_response_avg_score", EvalStatus.PASSED, score=1.0),
                make_metric_result("llm_rubric_knowledge_recall", EvalStatus.FAILED, score=0.2, threshold=0.5),
            ],
        )
        categories = attributor.classify(result)
        assert "knowledge_recall_insufficient" in categories

    def test_per_case_categories_populated(self, attributor):
        """per_case_categories 映射应包含每个失败 case 的归因。"""
        case1 = make_case_result(
            "train_001", EvalStatus.FAILED,
            [make_metric_result("final_response_avg_score", EvalStatus.FAILED, score=0.0)],
        )
        case2 = make_case_result(
            "train_002", EvalStatus.FAILED,
            [
                make_metric_result("final_response_avg_score", EvalStatus.FAILED, score=0.0),
                make_metric_result("tool_trajectory_avg_score", EvalStatus.FAILED, score=0.0),
            ],
        )
        results_by_id = {"train_001": [case1], "train_002": [case2]}
        report = attributor.cluster(results_by_id)

        assert "train_001" in report.per_case_categories
        assert "train_002" in report.per_case_categories
        assert report.per_case_categories["train_001"] == ["final_response_mismatch"]
        assert report.per_case_categories["train_002"] == ["both_metrics_failed"]

    def test_summary_generated(self, attributor):
        """报告应包含人类可读摘要，提及失败数量。"""
        case1 = make_case_result(
            "c1", EvalStatus.FAILED,
            [make_metric_result("final_response_avg_score", EvalStatus.FAILED, score=0.0)],
        )
        results_by_id = {"c1": [case1]}
        report = attributor.cluster(results_by_id)

        assert len(report.summary) > 0
        assert "1" in report.summary  # 提及失败数量
