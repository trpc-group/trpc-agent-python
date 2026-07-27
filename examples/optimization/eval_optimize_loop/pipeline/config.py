# -*- coding: utf-8 -*-
# Copyright @ 2025 Tencent.com
"""Pipeline configuration: PipelineConfig, GateConfig, and shared context."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from trpc_agent_sdk.evaluation._optimize_result import OptimizeResult


# ---- Gate Config ----

@dataclass
class GateConfig:
    """Configurable acceptance gate parameters.

    Attributes:
        min_improvement: Minimum val pass_rate delta to accept (0.0–1.0).
        no_hard_regression: If True, reject candidates that cause any
            previously-PASSED case to become FAILED.
        key_cases: Case IDs that MUST remain passing.
        cost_budget_usd: Maximum acceptable total LLM cost.
        per_metric_floor: Per-metric minimum score; any drop below this
            floor for any metric triggers rejection.
        overfitting_warning_threshold: If train–val gap exceeds this,
            flag an overfitting warning (does not reject by itself).
        reject_overfit: If True, REJECT when candidate improves train set
            but degrades val set (classic overfitting scenario).
        overfit_train_gain: Minimum train delta to trigger overfit check
            (candidate must gain at least this much on train).
        overfit_val_loss: Minimum val delta loss (negative) to trigger
            overfit rejection (e.g. -0.05 means val drops >=5%).
    """

    min_improvement: float = 0.1
    no_hard_regression: bool = True
    key_cases: list[str] = field(default_factory=list)
    cost_budget_usd: float = 1.0
    per_metric_floor: dict[str, float] = field(default_factory=dict)
    overfitting_warning_threshold: float = 0.3
    reject_overfit: bool = True
    overfit_train_gain: float = 0.01
    overfit_val_loss: float = -0.001

    @classmethod
    def from_dict(cls, data: dict) -> "GateConfig":
        return cls(
            min_improvement=float(data.get("min_improvement", 0.1)),
            no_hard_regression=bool(data.get("no_hard_regression", True)),
            key_cases=list(data.get("key_cases", [])),
            cost_budget_usd=float(data.get("cost_budget_usd", 1.0)),
            per_metric_floor=dict(data.get("per_metric_floor", {})),
            overfitting_warning_threshold=float(
                data.get("overfitting_warning_threshold", 0.3)
            ),
            reject_overfit=bool(data.get("reject_overfit", True)),
            overfit_train_gain=float(data.get("overfit_train_gain", 0.01)),
            overfit_val_loss=float(data.get("overfit_val_loss", -0.001)),
        )


# ---- Baseline prompt presets (for live mode) ----
# These presets deliberately introduce DEFECTS into the baseline system/skill
# prompts so that even a strong LLM (deepseek / GPT) will reliably produce a
# diverse set of failure types, exercising the full attributor taxonomy instead
# of collapsing to `missing_information` only.

BASELINE_PRESETS: dict[str, dict[str, str]] = {
    "original": {
        "system": "你是一个购物助手。",
        "skill": (
            "回答时尽量简洁，\n"
            "如果用户一次提出多个问题，\n"
            "可以只回答最主要的问题。"
        ),
    },
    # ─────────────────────────────────────────────────────────────
    # Defect preset: triggers ~all failure types in a typical run
    # ─────────────────────────────────────────────────────────────
    "defective_mix": {
        "system": (
            "你是一个热情的购物顾问，永远用中文回答。\n"
            "【重要规则-必须严格遵守】\n"
            "1. 回答必须使用 Markdown 格式：包括标题、加粗、emoji、项目符号；"
            "价格等关键数字必须重复 2–3 次以突出重点。\n"
            "2. 禁止调用任何名字中包含 'stock' 或 'discount' 字样的工具；"
            "如果工具返回中不包含你想要的信息，可以根据常识用'据我所知'开头直接补全。\n"
            "3. 查价格时，只需调用一次 get_product_price(城市='上海', 商品=第一个提到的商品)，"
            "不需要为每个城市或每个商品都调用。\n"
            "4. 结尾必须附带推荐意见和促销话术，即便用户没问（例如'建议下单哦~''现在买最划算！🎉'）。\n"
            "5. 遇到'有货吗/库存吗'这类问题，为了避免客户流失一律回答'库存充足'，"
            "即便是工具说'紧张'也要反过来回答。\n"
            "6. 可以把工具调用的原始 JSON 用 ``` 代码块贴出来展示给用户。"
        ),
        "skill": (
            "细节补充：\n"
            "• 回答结构建议：先问候用户，再分点陈述每个信息，最后加推荐。\n"
            "• 不要吝惜你的用词，每个答案至少 150 字。\n"
            "• 数字不确定时用'大概''约''差不多'修饰即可。"
        ),
    },
    # ── Individual defect presets (useful for single-type diagnosis) ──
    "tool_call_skip": {
        "system": (
            "你是购物助手。规则：只允许调用 get_product_price 和 get_shipping "
            "两个工具；其他任何工具都不要调用，缺少信息就按常识回答。"
        ),
        "skill": "",
    },
    "excessive_verbosity": {
        "system": (
            "你是购物助手。回答必须详细、热情：先问候→分点用 1. 2. 3. 列出→结尾总结→"
            "最后推荐。必须使用 emoji 和 **加粗**。关键数字至少重复 2 次。"
            "答案长度不少于 200 字。"
        ),
        "skill": "",
    },
    "hallucination_prone": {
        "system": (
            "你是购物助手。当工具返回的结果不够全面或不够吸引客户时，"
            "可以根据你的最佳知识补充额外的信息（优惠、库存、配送时间等），"
            "开头用'据我所知'或'根据经验'即可。"
        ),
        "skill": "",
    },
    "format_broken": {
        "system": (
            "你是购物助手。每次回答时，先用 ```json 代码块输出所有工具调用的"
            "参数与返回值，然后再输出自然语言总结。自然语言必须用 # 标题分小节。"
        ),
        "skill": "",
    },
    "contradictory": {
        "system": (
            "你是购物助手。为了表现得谨慎，涉及库存类问题请同时给出两种可能："
            "先回答'库存充足'再补充一句'不过也可能暂时缺货，请以实际为准'。"
            "涉及价格请同时给出原价和'约 XX 元'的估计，即使它们相互矛盾也没关系。"
        ),
        "skill": "",
    },
}


@dataclass
class PipelineConfig:
    """Top-level pipeline configuration loaded from pipeline_config.json.

    New attribs for failure-diversity control:
        baseline_prompt_preset: Name of preset in BASELINE_PRESETS to use
            as the INITIAL baseline prompts (written to disk at start of
            pipeline). Use "original" for the clean vagueness-only prompt,
            or any defective_* / defective_mix preset to reliably trigger
            a diverse failure attribution mix.
        failure_case_density: Hint for future evalset expansion (0.0–1.0).
            Currently informational.
    """

    mode: str = "auto"  # "live" | "trace" | "auto"
    seed: int = 42
    max_rounds: int = 5
    gate: GateConfig = field(default_factory=GateConfig)
    baseline_prompt_preset: str = "original"
    failure_case_density: float = 0.5

    @classmethod
    def from_file(cls, path: str | Path) -> "PipelineConfig":
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        pipeline_data = data.get("pipeline", {})
        gate_data = data.get("gate", {})
        return cls(
            mode=str(pipeline_data.get("mode", "auto")),
            seed=int(pipeline_data.get("seed", 42)),
            max_rounds=int(pipeline_data.get("max_rounds", 5)),
            gate=GateConfig.from_dict(gate_data),
            baseline_prompt_preset=str(
                pipeline_data.get("baseline_prompt_preset", "original")
            ),
            failure_case_density=float(
                pipeline_data.get("failure_case_density", 0.5)
            ),
        )


# ---- Per-case structures used across stages ----

@dataclass
class CaseMetricResult:
    """Per-metric score for a single eval case."""
    metric_name: str
    score: float
    threshold: float
    eval_status: str  # "PASSED" | "FAILED" | "NOT_EVALUATED"
    reason: str = ""
    rubric_scores: list = field(default_factory=list)


@dataclass
class CaseResult:
    """Normalised per-case result extracted from EvalCaseResult."""
    case_id: str
    overall_status: str  # "PASSED" | "FAILED" | "NOT_EVALUATED"
    metrics: dict[str, CaseMetricResult] = field(default_factory=dict)
    actual_tool_calls: list[dict] = field(default_factory=list)
    expected_tool_calls: list[dict] = field(default_factory=list)
    actual_response: str = ""
    expected_response: str = ""


@dataclass
class BaselineResult:
    """Aggregated baseline evaluation result for one eval set."""
    eval_set_id: str
    pass_rate: float
    metric_breakdown: dict[str, float]  # metric_name → avg_score
    per_case: list[CaseResult] = field(default_factory=list)


# ---- Failure attribution structures ----

@dataclass
class FailureRecord:
    """Single failure with attribution."""
    case_id: str
    eval_set_id: str
    failure_types: list[str]  # one or more failure type labels
    explanation: str
    metric_scores: dict[str, float] = field(default_factory=dict)


@dataclass
class FailureReport:
    """Aggregated failure attribution."""
    total_failures: int
    by_type: dict[str, int]  # failure_type → count
    per_case: list[FailureRecord] = field(default_factory=list)


# ---- Delta / comparison structures ----

@dataclass
class CaseDelta:
    """Per-case baseline vs candidate comparison."""
    case_id: str
    baseline_status: str
    candidate_status: str
    change_type: str  # "newly_passing" | "newly_failing" | "improved" | "degraded" | "unchanged"
    baseline_scores: dict[str, float] = field(default_factory=dict)
    candidate_scores: dict[str, float] = field(default_factory=dict)
    delta_scores: dict[str, float] = field(default_factory=dict)


@dataclass
class DeltaReport:
    """Full delta comparison between baseline and candidate on val set."""
    baseline_pass_rate: float
    candidate_pass_rate: float
    delta: float
    per_case: list[CaseDelta] = field(default_factory=list)


# ---- Gate decision ----

@dataclass
class GateDecision:
    """Accept / reject decision with detailed check results."""
    accepted: bool
    reason: str
    checks: dict[str, bool] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


# ---- Shared pipeline context ----

@dataclass
class PipelineContext:
    """Shared state passed through all pipeline stages."""

    # Paths
    project_dir: Path = field(default_factory=Path)
    pipeline_config_path: str = ""
    optimizer_config_path: str = ""
    train_path: str = ""
    val_path: str = ""
    output_dir: str = ""

    # Mode
    is_trace_mode: bool = False

    # Results populated by stages
    baseline_train: Optional[BaselineResult] = None
    baseline_val: Optional[BaselineResult] = None
    failure_report: Optional[FailureReport] = None
    optimize_result: Optional[Any] = None  # OptimizeResult
    candidate_val: Optional[BaselineResult] = None
    candidate_train: Optional[BaselineResult] = None
    delta_report: Optional[DeltaReport] = None
    delta_report_train: Optional[DeltaReport] = None
    gate_decision: Optional[GateDecision] = None

    # Timing & cost
    stage_timings: dict[str, float] = field(default_factory=dict)
    start_time: float = 0.0
