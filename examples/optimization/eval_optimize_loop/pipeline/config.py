"""Configuration loading for the eval+optimize pipeline."""

import json
import os
import sys
from dataclasses import dataclass, field
from typing import Optional


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
    allow_no_degradation: bool = True
    max_cost_budget: float = 10.0
    critical_case_ids: list[str] = field(default_factory=list)

    # Output
    output_dir: str = "."
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


def _ensure_repo_root_in_path() -> None:
    """确保项目根在 sys.path（trpc_agent_sdk 是源码包，位于项目根）。

    pipeline/ → eval_optimize_loop → optimization → examples → 项目根（4 级）。
    失败时记录 warning。
    """
    try:
        _cfg_dir = os.path.dirname(os.path.abspath(__file__))
        _repo_root = os.path.abspath(
            os.path.join(_cfg_dir, os.pardir, os.pardir, os.pardir, os.pardir))
        if _repo_root not in sys.path:
            sys.path.insert(0, _repo_root)
    except Exception as e:  # pragma: no cover — 极端路径异常
        print(f"  ⚠️  warning: 无法将项目根加入 sys.path: {e}")


def load_optimize_config(path: str) -> "EvalConfig":
    """用 SDK 加载 optimizer.json，返回其 evaluate 段（EvalConfig）。

    供 live 模式 baseline 评测使用（SDK 的 evaluate_eval_set 要求必填
    eval_config）。SDK 不可用时抛 ImportError。

    Args:
        path: optimizer.json 路径。

    Returns:
        SDK EvalConfig 实例（optimizer.json 的 evaluate 段）。
    """
    _ensure_repo_root_in_path()
    from trpc_agent_sdk.evaluation._optimize_config import load_optimize_config as _sdk_load
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
