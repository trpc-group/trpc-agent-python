"""评测+优化流水线的 Pydantic 数据模型定义。

本模块定义了流水线中所有阶段使用的数据结构，包括：
- 门控配置和决策模型
- 评测集报告和逐 case 评分
- 失败归因报告
- 优化执行报告
- 顶层流水线报告

所有模型继承自 EvalBaseModel，支持 JSON 序列化/反序列化。
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import Field

from trpc_agent_sdk.evaluation._common import EvalBaseModel

# ---- 枚举类型定义 ----

FailureCategory = Literal[
    "final_response_mismatch",      # 最终回复文本与期望内容不匹配
    "tool_trajectory_mismatch",     # 工具调用轨迹与期望不一致
    "both_metrics_failed",          # 回复和工具两个维度均失败
    "llm_rubric_fail",              # LLM 评判 rubric 未通过
    "knowledge_recall_insufficient", # 知识库召回不足
    "unknown",                      # 未知失败原因
]

ScenarioType = Literal[
    "optimizable_success",       # 当前失败，通过优化 prompt 可修复
    "optimization_ineffective",  # 当前失败，仅优化 prompt 无法修复（需改代码/工具）
    "optimization_regression",   # 当前通过，但优化后存在退化风险
]

TransitionType = Literal[
    "PASSED->PASSED",   # 基线通过，候选也通过（无变化）
    "FAILED->PASSED",   # 基线失败，候选通过（优化成功）
    "PASSED->FAILED",   # 基线通过，候选失败（退化/过拟合）
    "FAILED->FAILED",   # 基线失败，候选仍失败（优化无效）
]


class CriticalCaseConfig(EvalBaseModel):
    """关键 case 配置：指定候选评估中必须通过的 case。

    用于接受门控中的关键 case 检查——如果某个 case 被标记为关键，
    则候选 prompt 必须在该 case 上通过，否则整个候选被拒绝。
    """

    eval_id: str = Field(description="必须通过的评测 case ID。")
    metric_name: Optional[str] = Field(
        default=None,
        description="如果指定，则该特定 metric 必须通过。为 None 时要求 overall_status 为 PASSED。",
    )


class AcceptanceGateConfig(EvalBaseModel):
    """第 5 阶段接受门控的配置。

    门控采用多检查 AND 逻辑——所有检查项必须通过才接受候选 prompt。
    各检查项包括：pass rate 提升阈值、无新增 hard failure、回归数量限制、
    关键 case 必须通过、成本预算上限。
    """

    min_improvement_threshold: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="接受候选所需的最小 pass_rate 提升。0 表示任何提升均可接受。",
    )
    no_new_hard_failures: bool = Field(
        default=True,
        description="如果为 True，则任何基线 PASSED 但候选 FAILED 的 case 都会导致拒绝。",
    )
    max_regressions_allowed: int = Field(
        default=0,
        ge=0,
        description="允许的最大回归 case 数量。当 > 0 时覆盖 no_new_hard_failures 的严格限制。",
    )
    critical_case_ids: list[str] = Field(
        default_factory=list,
        description="候选评估中必须通过的 case ID 列表。",
    )
    max_cost_budget: float = Field(
        default=0.0,
        ge=0.0,
        description="允许的最大 LLM 调用成本（USD）。0 表示不限制预算。",
    )


class GateCheckResult(EvalBaseModel):
    """单个门控检查项的结果。"""

    check_name: str   # 检查项名称（如 improvement_threshold, regression_check）
    passed: bool       # 是否通过
    detail: str        # 详细说明（含具体数值）


class GateDecision(EvalBaseModel):
    """所有门控检查后的最终决策。"""

    accepted: bool                 # 是否接受候选 prompt
    reason: str                    # 接受/拒绝的原因描述
    checks: list[GateCheckResult]  # 所有检查项的结果列表
    baseline_pass_rate: float      # 基线 pass rate
    candidate_pass_rate: float     # 候选 pass rate
    improvement: float             # pass rate 变化量
    regressed_case_ids: list[str]  # 发生退化的 case ID 列表


class PerCaseScore(EvalBaseModel):
    """单个评测 case 的评分。"""

    eval_id: str                              # case 唯一标识
    overall_status: str                       # 整体状态（PASSED/FAILED/NOT_EVALUATED）
    metric_scores: dict[str, float] = Field(default_factory=dict)    # 各 metric 得分
    metric_statuses: dict[str, str] = Field(default_factory=dict)    # 各 metric 状态


class EvalSetReport(EvalBaseModel):
    """单次评测运行的完整报告。"""

    eval_set_id: str                           # 评测集标识
    num_cases: int                             # case 总数
    num_passed: int                            # 通过数
    num_failed: int                            # 失败数
    pass_rate: float                           # 通过率
    metric_breakdown: dict[str, float] = Field(default_factory=dict)  # 各 metric 平均分
    per_case: list[PerCaseScore] = Field(default_factory=list)        # 逐 case 评分


class PerCaseDelta(EvalBaseModel):
    """单个评测 case 的基线 vs 候选对比。

    用于 Stage 4 候选验证，展示每个 case 在优化前后的变化。
    """

    eval_id: str                               # case 唯一标识
    scenario: str                              # 场景类型（optimizable_success 等）
    baseline_status: str                       # 基线状态
    candidate_status: str                      # 候选状态
    baseline_scores: dict[str, float] = Field(default_factory=dict)   # 基线各 metric 得分
    candidate_scores: dict[str, float] = Field(default_factory=dict)  # 候选各 metric 得分
    score_delta: dict[str, float] = Field(default_factory=dict)       # 分数变化量
    transition: str                            # 状态转换（如 FAILED->PASSED）


class FailureAttributionReport(EvalBaseModel):
    """失败归因阶段（Stage 2）的输出。

    对失败的 case 按失败原因分类并聚类，帮助识别主要失败模式。
    """

    total_cases_evaluated: int                              # 评估的 case 总数
    total_failed: int                                       # 失败的 case 数
    clusters: dict[str, list[str]] = Field(default_factory=dict)          # 类别 → case ID 列表
    per_case_categories: dict[str, list[str]] = Field(default_factory=dict)  # case ID → 失败类别列表
    summary: str = ""                                       # 人类可读的摘要


class OptimizationExecutionReport(EvalBaseModel):
    """优化执行阶段（Stage 3）的输出。

    记录 AgentOptimizer 的运行结果，包括算法、轮次、pass rate 变化和成本。
    """

    algorithm: str                    # 使用的优化算法（如 gepa_reflective）
    status: str                       # 执行状态（SUCCEEDED/FAILED）
    total_rounds: int                 # 优化总轮次
    baseline_pass_rate: float         # 优化前 pass rate
    best_pass_rate: float             # 最优 pass rate
    pass_rate_improvement: float      # pass rate 提升量
    duration_seconds: float           # 优化耗时（秒）
    total_llm_cost: float             # LLM 调用总成本（USD）
    best_prompts: dict[str, str] = Field(default_factory=dict)  # 最优 prompt 内容

    # 审计字段 (Stage 6 报告消费)
    stop_reason: str = ""                          # SDK: result.stop_reason
    finish_reason: str = ""                        # SDK: result.finish_reason
    total_token_usage: dict[str, int] = Field(default_factory=dict)  # SDK: result.total_token_usage
    total_metric_calls: int = 0                    # SDK: result.total_metric_calls
    rounds: list[dict] = Field(default_factory=list)  # SDK: result.rounds


class PipelineReport(EvalBaseModel):
    """顶层流水线报告（对应 optimization_report.json）。

    聚合了所有 6 个阶段的输出结果，是流水线的最终产物。
    """

    pipeline_version: str = "1.0.0"        # 流水线版本号
    timestamp: str = ""                    # 运行时间戳（ISO 8601）
    pipeline_duration_seconds: float = 0.0 # 流水线总耗时
    demo_mode: bool = False                # 是否为 demo 模式

    # 各阶段输出
    baseline_train: Optional[EvalSetReport] = None              # Stage 1: 训练集基线评测
    baseline_val: Optional[EvalSetReport] = None                # Stage 1: 验证集基线评测
    failure_attribution: Optional[FailureAttributionReport] = None  # Stage 2: 失败归因
    optimization_execution: Optional[OptimizationExecutionReport] = None  # Stage 3: 优化执行
    candidate_validation: Optional[EvalSetReport] = None        # Stage 4: 候选验证
    case_deltas: list[PerCaseDelta] = Field(default_factory=list)  # Stage 4: 逐 case delta
    gate_decision: Optional[GateDecision] = None                # Stage 5: 门控决策

    # 汇总
    overall_pass_rate_change: float = 0.0  # 整体 pass rate 变化
    overall_verdict: str = ""              # 最终判决（ACCEPTED/REJECTED）
