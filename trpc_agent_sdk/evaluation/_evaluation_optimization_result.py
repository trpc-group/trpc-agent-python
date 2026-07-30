# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Evaluation and optimization pipeline result data structures."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from typing import Literal
from typing import Optional

from pydantic import Field

from ._common import EvalBaseModel
from ._evaluation_optimization_config import FailureCategoryName


class MetricEvaluation(EvalBaseModel):
    """Aggregated result for one metric on one case."""

    metric_name: str
    score: Optional[float] = None
    threshold: float
    passed: bool
    reasons: list[str] = Field(default_factory=list)


class CaseEvaluation(EvalBaseModel):
    """Case-level result used by the regression gate and report."""

    eval_set_id: str
    case_id: str
    score: float
    passed: bool
    hard_fail: bool
    metrics: list[MetricEvaluation] = Field(default_factory=list)
    failure_categories: list[FailureCategoryName] = Field(default_factory=list)
    failure_reasons: list[str] = Field(default_factory=list)
    key_trace: list[dict[str, Any]] = Field(default_factory=list)


class EvaluationSnapshot(EvalBaseModel):
    """Normalized evaluation output for one dataset and prompt candidate."""

    split: str
    score: float
    pass_rate: float
    case_count: int
    case_run_count: int
    metric_breakdown: dict[str, float] = Field(default_factory=dict)
    cases: list[CaseEvaluation] = Field(default_factory=list)


class CaseDelta(EvalBaseModel):
    """Candidate-minus-baseline comparison for one case."""

    eval_set_id: str
    case_id: str
    baseline_score: Optional[float]
    candidate_score: Optional[float]
    score_delta: Optional[float]
    baseline_passed: Optional[bool]
    candidate_passed: Optional[bool]
    status: str
    baseline_hard_fail: Optional[bool]
    candidate_hard_fail: Optional[bool]


class SplitDelta(EvalBaseModel):
    """Aggregate and per-case delta for a dataset split."""

    score_delta: float
    pass_rate_delta: float
    newly_passed: list[str] = Field(default_factory=list)
    newly_failed: list[str] = Field(default_factory=list)
    improved: list[str] = Field(default_factory=list)
    regressed: list[str] = Field(default_factory=list)
    unchanged: list[str] = Field(default_factory=list)
    cases: list[CaseDelta] = Field(default_factory=list)


class GateCheck(EvalBaseModel):
    """One independently auditable gate condition."""

    name: str
    configured: bool
    passed: bool
    actual: Any = None
    expected: Any = None
    detail: str


class GateDecision(EvalBaseModel):
    """Final decision for one candidate."""

    accepted: bool
    reasons: list[str]
    checks: list[GateCheck]
    new_hard_fail_case_ids: list[str] = Field(default_factory=list)
    critical_regression_case_ids: list[str] = Field(default_factory=list)
    validation_regression_case_ids: list[str] = Field(default_factory=list)
    overfitting_detected: bool = False


class FailureAttributionSummary(EvalBaseModel):
    """Failure-category counts plus the case ids behind each count."""

    total_failed_cases: int
    counts: dict[str, int] = Field(default_factory=dict)
    case_ids: dict[str, list[str]] = Field(default_factory=dict)


class CandidateRoundReport(EvalBaseModel):
    """Independent train/validation regression record for one prompt."""

    round: int
    prompts: dict[str, str]
    optimizer_accepted: bool
    optimizer_acceptance_reason: str
    optimizer_cost_usd: float
    optimizer_duration_seconds: float
    evaluation_duration_seconds: float
    train: EvaluationSnapshot
    validation: EvaluationSnapshot
    train_delta: SplitDelta
    validation_delta: SplitDelta
    gate_decision: GateDecision


class BaselineReport(EvalBaseModel):
    """Baseline measurements for train and validation data."""

    train: EvaluationSnapshot
    validation: EvaluationSnapshot


class CandidateReport(EvalBaseModel):
    """The candidate selected for the top-level report."""

    round: int
    prompts: dict[str, str]
    train: EvaluationSnapshot
    validation: EvaluationSnapshot


class DeltaReport(EvalBaseModel):
    """Top-level selected-candidate delta."""

    train: SplitDelta
    validation: SplitDelta


class FailureAttributionReport(EvalBaseModel):
    """Failure attribution before and after optimization."""

    baseline_train: FailureAttributionSummary
    baseline_validation: FailureAttributionSummary
    candidate_train: FailureAttributionSummary
    candidate_validation: FailureAttributionSummary


class InputArtifact(EvalBaseModel):
    """Portable path and content fingerprint for one pipeline input."""

    path: str
    sha256: str


class PipelineAudit(EvalBaseModel):
    """Reproduction, cost, timing, and source-update audit data."""

    mode: str
    report_language: Literal["en", "zh-CN"] = "en"
    random_seed: int
    started_at: str
    finished_at: str
    duration_seconds: float
    optimizer_algorithm: str
    optimizer_status: str
    optimizer_cost_usd: float
    estimated_evaluation_cost_usd: float
    total_cost_usd: float
    evaluation_cost_is_estimated: bool
    evaluation_case_runs: int
    source_updated: bool
    inputs: dict[str, InputArtifact]
    prompt_inputs: dict[str, InputArtifact]
    config_snapshot: dict[str, Any]


class OptimizationReport(EvalBaseModel):
    """Machine-readable output of the full optimization regression loop."""

    schema_version: str = "v1"
    baseline: BaselineReport
    candidate: CandidateReport
    delta: DeltaReport
    gate_decision: GateDecision
    failure_attribution: FailureAttributionReport
    rounds: list[CandidateRoundReport]
    audit: PipelineAudit

    def write(self, output_dir: str) -> None:
        """Atomically persist JSON and Markdown reports."""
        directory = Path(output_dir)
        directory.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            self.model_dump(mode="json", by_alias=True),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        _atomic_write_text(directory / "optimization_report.json", payload + "\n")
        _atomic_write_text(directory / "optimization_report.md", self.to_markdown())

    def to_markdown(self) -> str:
        """Render the acceptance decision and its evidence for reviewers."""
        if self.audit.report_language == "zh-CN":
            return self._to_markdown_zh_cn()
        return self._to_markdown_en()

    def _to_markdown_en(self) -> str:
        """Render the English reviewer report."""
        decision = "ACCEPT" if self.gate_decision.accepted else "REJECT"
        lines = [
            "# Evaluation + Optimization Report",
            "",
            f"**Decision: {decision}**",
            "",
            "## Score summary",
            "",
            "| Split | Baseline score | Candidate score | Delta | "
            "Baseline pass rate | Candidate pass rate |",
            "| --- | ---: | ---: | ---: | ---: | ---: |",
        ]
        for split in ("train", "validation"):
            baseline = getattr(self.baseline, split)
            candidate = getattr(self.candidate, split)
            delta = getattr(self.delta, split)
            lines.append(f"| {split} | {baseline.score:.4f} | {candidate.score:.4f} | "
                         f"{delta.score_delta:+.4f} | {baseline.pass_rate:.2%} | "
                         f"{candidate.pass_rate:.2%} |")

        lines.extend([
            "",
            "## Gate checks",
            "",
            "| Check | Result | Actual | Expected | Detail |",
            "| --- | --- | --- | --- | --- |",
        ])
        for check in self.gate_decision.checks:
            result = "PASS" if check.passed else "FAIL"
            lines.append(f"| {_markdown(check.name)} | {result} | "
                         f"{_markdown(_compact(check.actual))} | "
                         f"{_markdown(_compact(check.expected))} | "
                         f"{_markdown(check.detail)} |")

        lines.extend([
            "",
            "## Validation case deltas",
            "",
            "| Case | Baseline | Candidate | Delta | Status |",
            "| --- | ---: | ---: | ---: | --- |",
        ])
        for case in self.delta.validation.cases:
            lines.append(f"| {_markdown(case.case_id)} | {_score_text(case.baseline_score)} | "
                         f"{_score_text(case.candidate_score)} | "
                         f"{_signed_score_text(case.score_delta)} | {_markdown(case.status)} |")

        lines.extend([
            "",
            "## Failure attribution",
            "",
            "| Snapshot | Category counts |",
            "| --- | --- |",
        ])
        attribution_rows = (
            ("baseline train", self.failure_attribution.baseline_train),
            ("baseline validation", self.failure_attribution.baseline_validation),
            ("candidate train", self.failure_attribution.candidate_train),
            ("candidate validation", self.failure_attribution.candidate_validation),
        )
        for label, summary in attribution_rows:
            rendered = ", ".join(f"{name}={count}" for name, count in sorted(summary.counts.items())) or "none"
            lines.append(f"| {label} | {_markdown(rendered)} |")

        lines.extend([
            "",
            "## Audit",
            "",
            f"- Optimizer: `{self.audit.optimizer_algorithm}` "
            f"(`{self.audit.optimizer_status}`)",
            f"- Seed: `{self.audit.random_seed}`; mode: `{self.audit.mode}`",
            f"- Estimated total cost: `${self.audit.total_cost_usd:.6f}`",
            f"- Duration: `{self.audit.duration_seconds:.4f}s`",
            f"- Source prompt updated: `{str(self.audit.source_updated).lower()}`",
            "",
        ])
        if self.gate_decision.reasons:
            lines.extend(["## Decision reasons", ""])
            lines.extend(f"- {reason}" for reason in self.gate_decision.reasons)
            lines.append("")
        return "\n".join(lines)

    def _to_markdown_zh_cn(self) -> str:
        """Render a Simplified Chinese reviewer report."""
        accepted = self.gate_decision.accepted
        decision = "接受" if accepted else "拒绝"
        recommendation = (f"建议接受第 {self.candidate.round} 轮候选提示词。"
                          if accepted else f"不建议接受第 {self.candidate.round} 轮候选提示词。")
        validation_baseline = self.baseline.validation
        validation_candidate = self.candidate.validation
        validation_delta = self.delta.validation
        newly_passed = "、".join(validation_delta.newly_passed) or "无"
        newly_failed = "、".join(validation_delta.newly_failed) or "无"
        remaining_failed = "、".join(case.case_id for case in validation_candidate.cases if not case.passed) or "无"
        source_update = ("已写回源提示词。" if self.audit.source_updated else "未自动写回源提示词，仍需人工审核后决定是否采用。")

        lines = [
            "# 评测与提示词优化报告",
            "",
            f"**最终决策：{decision}**",
            "",
            "## 结论摘要",
            "",
            f"- {recommendation}",
            f"- 验证集平均分由 {validation_baseline.score:.4f} 提升至 "
            f"{validation_candidate.score:.4f}，变化为 "
            f"{validation_delta.score_delta:+.4f}。",
            f"- 验证集通过率由 {validation_baseline.pass_rate:.2%} 提升至 "
            f"{validation_candidate.pass_rate:.2%}。",
            f"- 新增通过：{newly_passed}；新增失败：{newly_failed}。",
            f"- 当前仍未通过的验证 case：{remaining_failed}。",
            f"- 预计总成本为 ${self.audit.total_cost_usd:.6f}；{source_update}",
            "",
            "## 分数汇总",
            "",
            "| 数据集 | Baseline 分数 | 候选分数 | 分数变化 | "
            "Baseline 通过率 | 候选通过率 |",
            "| --- | ---: | ---: | ---: | ---: | ---: |",
        ]
        for split, label in (("train", "训练集"), ("validation", "验证集")):
            baseline = getattr(self.baseline, split)
            candidate = getattr(self.candidate, split)
            delta = getattr(self.delta, split)
            lines.append(f"| {label} | {baseline.score:.4f} | {candidate.score:.4f} | "
                         f"{delta.score_delta:+.4f} | {baseline.pass_rate:.2%} | "
                         f"{candidate.pass_rate:.2%} |")

        lines.extend([
            "",
            "## 接受条件检查",
            "",
            "| 检查项 | 结果 | 实际值 | 期望值 | 说明 |",
            "| --- | --- | --- | --- | --- |",
        ])
        for check in self.gate_decision.checks:
            label, detail = _zh_gate_text(check.name)
            result = ("未启用" if not check.configured else ("通过" if check.passed else "未通过"))
            lines.append(f"| {_markdown(label)} | {result} | "
                         f"{_markdown(_compact_zh(check.actual))} | "
                         f"{_markdown(_compact_zh(check.expected))} | "
                         f"{_markdown(detail)} |")

        lines.extend([
            "",
            "## 验证集逐 case 对比",
            "",
            "| Case | Baseline | 候选 | 变化 | 状态 |",
            "| --- | ---: | ---: | ---: | --- |",
        ])
        for case in self.delta.validation.cases:
            lines.append(f"| {_markdown(case.case_id)} | "
                         f"{_score_text(case.baseline_score)} | "
                         f"{_score_text(case.candidate_score)} | "
                         f"{_signed_score_text(case.score_delta)} | "
                         f"{_markdown(_zh_case_status(case.status))} |")

        lines.extend([
            "",
            "## 失败归因",
            "",
            "| 快照 | 失败类型统计 |",
            "| --- | --- |",
        ])
        attribution_rows = (
            ("Baseline 训练集", self.failure_attribution.baseline_train),
            ("Baseline 验证集", self.failure_attribution.baseline_validation),
            ("候选训练集", self.failure_attribution.candidate_train),
            ("候选验证集", self.failure_attribution.candidate_validation),
        )
        for label, summary in attribution_rows:
            rendered = ("，".join(f"{_zh_failure_category(name)}={count}"
                                 for name, count in sorted(summary.counts.items())) or "无")
            lines.append(f"| {label} | {_markdown(rendered)} |")

        lines.extend([
            "",
            "## 运行审计",
            "",
            f"- 优化器：`{self.audit.optimizer_algorithm}`"
            f"（`{self.audit.optimizer_status}`）",
            f"- 随机种子：`{self.audit.random_seed}`；运行模式："
            f"`{_zh_mode(self.audit.mode)}`",
            f"- 预计总成本：`${self.audit.total_cost_usd:.6f}`",
            f"- 总耗时：`{self.audit.duration_seconds:.4f}s`",
            f"- 是否写回源提示词："
            f"`{'是' if self.audit.source_updated else '否'}`",
            "",
            "## 决策理由",
            "",
        ])
        failed_checks = [check for check in self.gate_decision.checks if check.configured and not check.passed]
        if accepted:
            lines.append("- 所有已配置的接受条件均已通过。")
        else:
            for check in failed_checks:
                label, detail = _zh_gate_text(check.name)
                lines.append(f"- {label}未通过：{detail}")
        lines.append("")
        return "\n".join(lines)


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)


def _compact(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def _compact_zh(value: Any) -> str:
    if value is True:
        return "是"
    if value is False:
        return "否"
    if value == "disabled":
        return "未启用"
    return _compact(value)


def _zh_gate_text(name: str) -> tuple[str, str]:
    labels = {
        "optimizer_status": "优化器状态",
        "validation_score_delta": "验证集分数提升",
        "validation_pass_rate_delta": "验证集通过率提升",
        "new_hard_fail": "新增 hard fail",
        "critical_case_regression": "关键 case 退化",
        "validation_regression_count": "验证集退化数量",
        "overfitting_guard": "过拟合保护",
        "total_cost_budget": "总成本预算",
    }
    details = {
        "optimizer_status": "优化器必须正常完成，候选才可被接受。",
        "validation_score_delta": "验证集平均分提升必须达到配置阈值。",
        "validation_pass_rate_delta": "验证集通过率变化不得低于配置阈值。",
        "new_hard_fail": "候选不得引入新的已配置 hard fail。",
        "critical_case_regression": "关键 case 的退化不得超过允许范围。",
        "validation_regression_count": "逐 case 统计的验证集退化数量不得超过上限。",
        "overfitting_guard": "训练集提升但验证集未达标或发生退化时必须拒绝。",
        "total_cost_budget": "优化与评测的总成本不得超过配置预算。",
    }
    return labels.get(name, name), details.get(name, name)


def _zh_case_status(status: str) -> str:
    return {
        "newly_passed": "新增通过",
        "newly_failed": "新增失败",
        "improved": "分数提升",
        "regressed": "分数下降",
        "unchanged": "无变化",
        "added": "新增 case",
        "removed": "缺失 case",
    }.get(status, status)


def _zh_failure_category(category: str) -> str:
    return {
        "final_response_mismatch": "最终回复不匹配",
        "tool_call_error": "工具调用错误",
        "tool_argument_error": "工具参数错误",
        "llm_rubric_failure": "LLM rubric 未达标",
        "knowledge_recall_failure": "知识召回不足",
        "format_violation": "格式不符合要求",
        "execution_error": "执行异常",
        "unknown_failure": "未知失败",
    }.get(category, category)


def _zh_mode(mode: str) -> str:
    return {
        "live": "真实模型",
        "fake": "假模型",
        "trace": "轨迹回放",
    }.get(mode, mode)


def _markdown(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


def _score_text(value: Optional[float]) -> str:
    return "-" if value is None else f"{value:.4f}"


def _signed_score_text(value: Optional[float]) -> str:
    return "-" if value is None else f"{value:+.4f}"
