"""Evaluation + Optimization Pipeline.

统一的 pipeline 包入口。核心阶段：config → baseline → attribution
→ optimize → validate → gate → report。

模块职责：
- comparator:  trace 回放评测器（期望 vs 实际的逐 case 判定与归因）
- config:      PipelineConfig + evalset/optimizer 配置加载
- baseline:    baseline 评测（fake trace 回放 / SDK AgentEvaluator）
- attribution: 失败归因聚类
- optimize:    候选生成（三类场景）+ AgentOptimizer 集成
- validate:    候选重评分 + 过拟合检测
- gate:        多维接受决策（含验证集回归拒绝）
- report:      JSON/Markdown 报告生成
- tracing:     审计追踪（seed/耗时/成本/复现命令）
"""

from .comparator import (
    CaseVerdict,
    FailureCategory,
    TraceMatcher,
    bare_answer,
    compare_case,
    compare_invocations,
    default_matcher,
    extract_numbers,
    normalize_text,
)
from .config import (
    PipelineConfig,
    load_evalset,
    load_optimizer_json,
    load_pipeline_config,
)
from .baseline import BaselineResult, run_baseline_fake, run_baseline_sdk
from .attribution import (
    AttributionEntry,
    AttributionReport,
    attribute_failures,
)
from .optimize import OptimizeResult, RoundRecord, run_optimize_fake, run_optimize_live
from .validate import ValidationDelta, ValidationResult, run_validation_fake, run_validation_trace
from .gate import GateDecision, GateResult, evaluate_gate
from .report import generate_json_report, generate_md_report
from .tracing import AuditTrail, AuditTracer

__all__ = [
    # comparator
    "CaseVerdict", "FailureCategory", "TraceMatcher",
    "bare_answer", "compare_case", "compare_invocations",
    "default_matcher", "extract_numbers", "normalize_text",
    # config
    "PipelineConfig", "load_evalset", "load_optimizer_json", "load_pipeline_config",
    # baseline
    "BaselineResult", "run_baseline_fake", "run_baseline_sdk",
    # attribution
    "AttributionEntry", "AttributionReport", "attribute_failures",
    # optimize
    "OptimizeResult", "RoundRecord", "run_optimize_fake", "run_optimize_live",
    # validate
    "ValidationDelta", "ValidationResult", "run_validation_fake", "run_validation_trace",
    # gate
    "GateDecision", "GateResult", "evaluate_gate",
    # report
    "generate_json_report", "generate_md_report",
    # tracing
    "AuditTrail", "AuditTracer",
]
