"""eval_optimize_loop 测试包的共享 fixtures。

提供以下 fixtures:
  - temp_dir: 临时目录，用于测试输出
  - data_dir: data/ 目录路径
  - sample_gate_config: 默认接受门控配置字典
"""

import json
import os
import tempfile
from pathlib import Path

import pytest


@pytest.fixture
def temp_dir():
    """为测试输出创建临时目录，测试结束后自动清理。"""
    with tempfile.TemporaryDirectory() as tmp:
        yield tmp


@pytest.fixture
def data_dir():
    """返回 data/ 目录的绝对路径。"""
    return Path(__file__).parent.parent / "data"


@pytest.fixture
def sample_gate_config():
    """返回测试用的默认接受门控配置。"""
    return {
        "min_improvement_threshold": 0.0,
        "no_new_hard_failures": True,
        "max_regressions_allowed": 0,
        "critical_case_ids": [],
        "max_cost_budget": 0.0,
    }
