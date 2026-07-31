"""全流水线集成测试（demo/fake 模式）。

本测试模块在 demo 模式下运行完整的 6 阶段流水线,验证:
  1. 流水线能否正常完成
  2. 各阶段产出是否符合预期
  3. 门控决策是否正确拒绝退化
  4. 报告文件是否正确写入

所有测试使用预录制的 trace evalset 数据,无需 API key。
"""

import json
import os
import time
from pathlib import Path

import pytest

from pipeline._eval_backend import TraceBackend
from pipeline._models import AcceptanceGateConfig, PipelineReport
from pipeline._runner import PipelineRunner
from run_pipeline import derive_scenario

# ---- 测试数据路径 ----
DATA_DIR = Path(__file__).parent.parent / "data"
TRACE_DIR = DATA_DIR / "trace"
PROMPT_PATH = Path(__file__).parent.parent / "agent" / "prompts" / "system.md"


def _build_scenario_map_from_eval(eval_path: Path) -> dict[str, str]:
    """读取 evalset.json, 用真实 derive_scenario() 推导每个 case 的场景。

    替代旧的本地 40 条 SCENARIO_MAP 副本 — 现在它直接跟踪 run_pipeline 的实现,
    派生规则变化时测试随之更新。
    """
    cases = json.loads(eval_path.read_text(encoding="utf-8"))["eval_cases"]
    return {c["eval_id"]: derive_scenario(c["eval_id"]) for c in cases}


@pytest.fixture
def trace_scenario_map() -> dict[str, str]:
    """用真实 derive_scenario() 从 val_baseline.evalset.json 推导的场景映射。"""
    return _build_scenario_map_from_eval(TRACE_DIR / "val_baseline.evalset.json")


@pytest.fixture
def gate_config():
    """默认门控配置:禁止回归,提升阈值=0。"""
    return AcceptanceGateConfig(
        min_improvement_threshold=0.0,
        no_new_hard_failures=True,
        max_regressions_allowed=0,
        critical_case_ids=[],
        max_cost_budget=0.0,
    )


@pytest.fixture
def pipeline_runner(tmp_path, gate_config, trace_scenario_map):
    """创建 demo 模式的 PipelineRunner 实例。

    使用 tmp_path 作为输出目录,测试结束后自动清理。
    """
    return PipelineRunner(
        train_eval_path=str(TRACE_DIR / "train.evalset.json"),
        val_baseline_eval_path=str(TRACE_DIR / "val_baseline.evalset.json"),
        val_candidate_eval_path=str(TRACE_DIR / "val_optimized.evalset.json"),
        gate_metrics_config_path=str(DATA_DIR / "gate_metrics.json"),
        optimizer_config_path=str(DATA_DIR / "optimizer.json"),
        prompt_source_path=str(PROMPT_PATH),
        prompt_field_name="system_prompt",
        gate_config=gate_config,
        backend=TraceBackend(),
        demo_mode=True,
        output_dir=str(tmp_path),
        scenario_map=trace_scenario_map,
        demo_optimize_result_path=str(DATA_DIR / "demo_optimize_result.json"),
    )


class TestPipelineFakeMode:
    """全流水线集成测试(demo 模式)。

    验证完整的 6 阶段流水线在 demo 模式下正确运行,
    各阶段产出符合预期,门控决策正确。
    """

    async def test_full_pipeline_completes(self, pipeline_runner):
        """流水线应在 2 分钟内无错误完成。"""
        start = time.time()
        report = await pipeline_runner.run()
        elapsed = time.time() - start

        assert isinstance(report, PipelineReport)
        assert report.demo_mode is True
        assert elapsed < 120  # 2 分钟内完成

    async def test_baseline_generated(self, pipeline_runner):
        """基线评测应为训练集和验证集生成结果。

        预期:
        - 训练集 20 cases: 15 失败(9 optimizable + 6 ineffective),5 通过
        - 验证集 20 cases: 15 失败(10 optimizable + 5 ineffective),5 通过
        """
        report = await pipeline_runner.run()

        assert report.baseline_train is not None
        assert report.baseline_val is not None
        assert report.baseline_train.num_cases == 20
        assert report.baseline_val.num_cases == 20
        # 训练集: 9 optimizable + 6 ineffective = 15 失败, 5 working = 5 通过
        assert report.baseline_train.num_failed == 15
        assert report.baseline_train.num_passed == 5
        # 验证集: 10 optimizable + 5 ineffective = 15 失败, 5 regression/working = 5 通过
        assert report.baseline_val.num_failed == 15
        assert report.baseline_val.num_passed == 5

    async def test_failure_attribution_generated(self, pipeline_runner):
        """失败归因应正确识别失败类别并聚类。

        预期:40 个 case 中 30 个失败,分布在多个类别中。
        """
        report = await pipeline_runner.run()

        assert report.failure_attribution is not None
        # 训练集20 + 验证集20 = 40 个 case
        assert report.failure_attribution.total_cases_evaluated == 40
        # 训练集15失败 + 验证集15失败 = 30 失败
        assert report.failure_attribution.total_failed == 30
        assert len(report.failure_attribution.clusters) > 0
        assert len(report.failure_attribution.summary) > 0

    async def test_optimization_execution_generated(self, pipeline_runner):
        """优化执行应从 demo JSON 加载预生成结果。"""
        report = await pipeline_runner.run()

        assert report.optimization_execution is not None
        assert report.optimization_execution.algorithm == "gepa_reflective"
        assert report.optimization_execution.status == "SUCCEEDED"
        assert report.optimization_execution.total_rounds > 0
        assert len(report.optimization_execution.best_prompts) > 0

    async def test_candidate_validation_with_deltas(self, pipeline_runner):
        """候选验证应生成逐 case delta 对比。

        预期:
        - 20 个 case delta
        - 10 个 FAILED->PASSED(optimizable 场景)
        - 5 个 FAILED->FAILED(ineffective 场景)
        - 5 个 PASSED->FAILED(regression 场景)
        """
        report = await pipeline_runner.run()

        assert report.candidate_validation is not None
        # 验证集 20 条 case 都有 delta
        assert len(report.case_deltas) == 20

        # val_001 优化成功: FAILED -> PASSED
        val_001 = next(d for d in report.case_deltas if d.eval_id == "val_001_optimizable")
        assert val_001.transition == "FAILED->PASSED"
        assert val_001.scenario == "optimizable_success"

        # val_002 优化无效: FAILED -> FAILED
        val_002 = next(d for d in report.case_deltas if d.eval_id == "val_002_ineffective")
        assert val_002.transition == "FAILED->FAILED"
        assert val_002.scenario == "optimization_ineffective"

        # val_003 过拟合退化: PASSED -> FAILED
        val_003 = next(d for d in report.case_deltas if d.eval_id == "val_003_regression")
        assert val_003.transition == "PASSED->FAILED"
        assert val_003.scenario == "optimization_regression"

        # 统计各类 transition 数量
        transitions = [d.transition for d in report.case_deltas]
        assert transitions.count("FAILED->PASSED") == 10  # 10 optimizable cases
        assert transitions.count("FAILED->FAILED") == 5   # 5 ineffective cases
        assert transitions.count("PASSED->FAILED") == 5   # 5 regression cases

    async def test_gate_rejects_regression(self, pipeline_runner):
        """门控应因检测到回归而拒绝候选 prompt。

        由于 val_003_regression 等回归 case 的存在,候选应被拒绝。
        """
        report = await pipeline_runner.run()

        assert report.gate_decision is not None
        assert report.gate_decision.accepted is False
        assert report.overall_verdict == "REJECTED"
        assert "val_003_regression" in report.gate_decision.regressed_case_ids

    async def test_gate_checks_present(self, pipeline_runner):
        """门控决策应包含所有配置的检查项。"""
        report = await pipeline_runner.run()

        assert report.gate_decision is not None
        assert len(report.gate_decision.checks) >= 4

        check_names = {c.check_name for c in report.gate_decision.checks}
        assert "improvement_threshold" in check_names
        assert "regression_check" in check_names or "no_new_hard_failures" in check_names
        assert "critical_cases" in check_names
        assert "cost_budget" in check_names

    async def test_reports_written_to_output(self, pipeline_runner, tmp_path):
        """JSON 和 Markdown 报告应写入输出目录。"""
        await pipeline_runner.run()

        # PipelineRunner.run() 在 output_dir 下创建 <UTC-timestamp>/ 子目录,
        # 报告位于 runner.run_dir。
        assert pipeline_runner.run_dir is not None
        run_dir = str(pipeline_runner.run_dir)
        json_path = os.path.join(run_dir, "optimization_report.json")
        md_path = os.path.join(run_dir, "optimization_report.md")

        assert os.path.exists(json_path)
        assert os.path.exists(md_path)

        # 验证 JSON 内容结构
        with open(json_path, "r") as f:
            data = json.load(f)
        assert "pipelineVersion" in data or "pipeline_version" in data
        assert "overallVerdict" in data or "overall_verdict" in data

        # 验证 Markdown 内容结构
        with open(md_path, "r") as f:
            content = f.read()
        assert "# 优化报告" in content
        assert "## 总体判决" in content

        # 兼容原始约定:旧版本把报告直接写在 tmp_path 下。
        # 这里仅作健全性检查(子目录结构需要存在)。
        assert str(tmp_path) in run_dir

    async def test_gate_accepts_when_allow_regressions(
        self, pipeline_runner, tmp_path, trace_scenario_map
    ):
        """当允许回归时,回归检查应通过。

        创建独立的 runner 实例,关闭 no_new_hard_failures 并允许大量回归。
        注意:由于 demo 数据中 pass rate 无改善,整体仍可能被拒绝,
        但回归检查本身应通过。
        """
        runner = PipelineRunner(
            train_eval_path=str(TRACE_DIR / "train.evalset.json"),
            val_baseline_eval_path=str(TRACE_DIR / "val_baseline.evalset.json"),
            val_candidate_eval_path=str(TRACE_DIR / "val_optimized.evalset.json"),
            gate_metrics_config_path=str(DATA_DIR / "gate_metrics.json"),
            optimizer_config_path=str(DATA_DIR / "optimizer.json"),
            prompt_source_path=str(PROMPT_PATH),
            prompt_field_name="system_prompt",
            gate_config=AcceptanceGateConfig(
                min_improvement_threshold=0.0,
                no_new_hard_failures=False,   # 关闭新增失败禁止
                max_regressions_allowed=999,   # 允许大量回归
                critical_case_ids=[],
                max_cost_budget=0.0,
            ),
            backend=TraceBackend(),
            demo_mode=True,
            output_dir=str(tmp_path),
            scenario_map=trace_scenario_map,
            demo_optimize_result_path=str(DATA_DIR / "demo_optimize_result.json"),
        )

        report = await runner.run()

        # 回归检查应通过(允许回归)
        regression_check = next(
            c for c in report.gate_decision.checks
            if c.check_name == "regression_check"
        )
        assert regression_check.passed is True
