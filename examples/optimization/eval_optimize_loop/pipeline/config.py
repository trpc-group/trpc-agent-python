"""配置加载、校验、sha256 摘要。"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from trpc_agent_sdk.evaluation import EvalConfig


@dataclass
class GateConfig:
    """可配置的接受策略阈值（gate.json），全部外置不写死。"""

    min_validation_score_delta: float = 0.05
    max_new_hard_fails: int = 0
    max_score_regression_per_case: float = 0.0
    critical_case_ids: list[str] = field(default_factory=list)
    overfitting_enabled: bool = True
    generalization_gap_threshold: float = 0.1
    max_metric_calls: int = 80
    max_duration_seconds: int = 180
    cost_measurement: str = "measured_zero_offline"
    tie_policy: str = "reject"

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "GateConfig":
        overfit = d.get("overfitting", {}) or {}
        budget = d.get("budget", {}) or {}
        return cls(
            min_validation_score_delta=d.get("min_validation_score_delta", 0.05),
            max_new_hard_fails=d.get("max_new_hard_fails", 0),
            max_score_regression_per_case=d.get("max_score_regression_per_case", 0.0),
            critical_case_ids=list(d.get("critical_case_ids", [])),
            overfitting_enabled=overfit.get("enabled", True),
            generalization_gap_threshold=overfit.get("generalization_gap_threshold", 0.1),
            max_metric_calls=budget.get("max_metric_calls", 80),
            max_duration_seconds=budget.get("max_duration_seconds", 180),
            cost_measurement=budget.get("cost_measurement", "measured_zero_offline"),
            tie_policy=d.get("tie_policy", "reject"),
        )


def load_gate_config(path: str | Path) -> GateConfig:
    return GateConfig.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


def load_eval_config(optimizer_path: str | Path) -> EvalConfig:
    """从 optimizer.json 的 evaluate 字段构造 SDK EvalConfig。

    fake/trace/online 三模式共用同一套 metric 配置，避免漂移。
    只读 evaluate 节点（不触发 optimize.algorithm.reflection_lm 的 env 解析），
    这样无 API key 时也能加载。
    """
    data = json.loads(Path(optimizer_path).read_text(encoding="utf-8"))
    evaluate = data["evaluate"]
    return EvalConfig.model_validate(evaluate)


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sha256_file(path: str | Path) -> str:
    return sha256_bytes(Path(path).read_bytes())


def sha256_dict(d: Any) -> str:
    """dict 的确定性摘要（sort_keys 保证可 diff）。"""
    return sha256_bytes(json.dumps(d, sort_keys=True, ensure_ascii=False).encode("utf-8"))
