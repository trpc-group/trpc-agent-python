"""Configuration loading for the eval+optimize pipeline."""

import json
import os
from dataclasses import dataclass, field
from typing import Any

from ._paths import ensure_repo_root_in_path


@dataclass
class PipelineConfig:
    """Pipeline configuration from optimizer.json and CLI args."""

    # Input paths
    train_evalset: str = "data/train.evalset.json"
    val_evalset: str = "data/val.evalset.json"
    optimizer_config: str = "data/optimizer.json"
    prompt_dir: str = "data/prompts"

    # Optimization
    algorithm: str = "gepa_reflective"
    max_iterations: int = 3
    seed: int = 42
    timeout_seconds: int = 600
    max_metric_calls: int = 100

    # Gate
    min_improvement_threshold: float = 0.05
    max_cost_budget: float = 10.0
    critical_case_ids: list[str] = field(default_factory=list)

    # Output（与 run_pipeline.py argparse --output-dir 默认值一致，
    # 避免 "." 默认值使 is_output_dir_allowed 误拒仓库根、复现命令失真）
    output_dir: str = "sample_output"
    mode: str = "fake"       # "fake" or "live"
    verbose: bool = False
    ci_mode: bool = False    # Exit non-zero on failure

    # Candidate scenario
    scenario: str = "fix_attributed"    # fix_attributed / noop / overfit
    holdout_evalset: str = "data/holdout.evalset.json"
    val_regression_cases: list[str] = field(default_factory=list)


def load_optimizer_json(path: str) -> dict:
    """Load and parse optimizer.json configuration file.

    Returns a dict suitable for AgentOptimizer.optimize().
    """
    if not os.path.exists(path):
        raise FileNotFoundError(f"Optimizer config not found: {path}")

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    # Validate required sections
    if "evaluate" not in data:
        raise ValueError("optimizer.json missing 'evaluate' section")
    if "optimize" not in data:
        raise ValueError("optimizer.json missing 'optimize' section")

    return data


def load_optimize_config(path: str) -> Any:
    """用 SDK 加载 optimizer.json，返回其 evaluate 段（EvalConfig）。

    供 live 模式 baseline 评测使用（SDK 的 evaluate_eval_set 要求必填
    eval_config）。SDK 不可用时抛 ImportError。

    Args:
        path: optimizer.json 路径。

    Returns:
        SDK EvalConfig 实例（optimizer.json 的 evaluate 段）。
    """
    ensure_repo_root_in_path()
    # 用 SDK 公开导出，避免耦合私有模块（_optimize_config 重构后 ImportError
    # 会被误判为 "SDK 不可用" 而静默降级为 fake 评分）
    from trpc_agent_sdk.evaluation import load_optimize_config as _sdk_load
    return _sdk_load(path).evaluate


def load_evalset(path: str) -> dict:
    """Load an evalset JSON file and validate structure."""
    if not os.path.exists(path):
        raise FileNotFoundError(f"Evalset not found: {path}")

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if "eval_set_id" not in data:
        raise ValueError(f"Evalset missing 'eval_set_id': {path}")
    if "eval_cases" not in data:
        raise ValueError(f"Evalset missing 'eval_cases': {path}")

    return data


def load_pipeline_config(**overrides) -> PipelineConfig:
    """Load pipeline configuration with optional overrides."""
    cfg = PipelineConfig()
    for k, v in overrides.items():
        if v is not None and hasattr(cfg, k):
            setattr(cfg, k, v)
    return cfg
