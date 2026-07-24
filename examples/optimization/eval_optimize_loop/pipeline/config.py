"""Configuration loading for the eval+optimize pipeline."""

import json
import os
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
