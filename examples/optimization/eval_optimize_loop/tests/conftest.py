"""pytest 配置 + 共享 fixtures"""

import json
import sys
from pathlib import Path

import pytest

# 将项目根加入 sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# ── pytest-asyncio 配置 ──
pytest_plugins = ("pytest_asyncio",)


@pytest.fixture
def config_path():
    """optimizer.json 路径"""
    return PROJECT_ROOT / "config" / "optimizer.json"


@pytest.fixture
def gate_config(config_path):
    """加载 gate 配置"""
    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)
    return config.get("gate", {})


@pytest.fixture
def train_evalset_path():
    return PROJECT_ROOT / "config" / "train.evalset.json"


@pytest.fixture
def val_evalset_path():
    return PROJECT_ROOT / "config" / "val.evalset.json"


@pytest.fixture
def train_evalset(train_evalset_path):
    with open(train_evalset_path, "r", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def val_evalset(val_evalset_path):
    with open(val_evalset_path, "r", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def sample_baseline_scores():
    """模拟 baseline 验证集分数"""
    return {"val_001": 0.95, "val_002": 0.45, "val_003": 0.40}


@pytest.fixture
def sample_candidate_scores():
    """模拟候选验证集分数（改善）"""
    return {"val_001": 0.97, "val_002": 0.72, "val_003": 0.55}


@pytest.fixture
def sample_regressed_scores():
    """模拟候选验证集分数（退化）"""
    return {"val_001": 0.93, "val_002": 0.40, "val_003": 0.35}


@pytest.fixture
def output_dir(tmp_path):
    """临时输出目录"""
    out = tmp_path / "output"
    out.mkdir()
    return out
