#
# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2025 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#
"""数据集 sanity check：
- train / val 各 3 条
- eval_id 互斥
"""

from __future__ import annotations

import json
from pathlib import Path


def _load(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


def test_train_size(loop_root: Path) -> None:
    data = _load(loop_root / "data" / "train.evalset.json")
    assert len(data["eval_cases"]) == 3


def test_val_size(loop_root: Path) -> None:
    data = _load(loop_root / "data" / "val.evalset.json")
    assert len(data["eval_cases"]) == 3


def test_eval_id_disjoint(loop_root: Path) -> None:
    train = _load(loop_root / "data" / "train.evalset.json")
    val = _load(loop_root / "data" / "val.evalset.json")
    train_ids = {c["eval_id"] for c in train["eval_cases"]}
    val_ids = {c["eval_id"] for c in val["eval_cases"]}
    assert train_ids & val_ids == set(), f"train/val eval_id 重叠：{train_ids & val_ids}"


def test_eval_id_unique_within_set(loop_root: Path) -> None:
    for fname in ("train.evalset.json", "val.evalset.json"):
        data = _load(loop_root / "data" / fname)
        ids = [c["eval_id"] for c in data["eval_cases"]]
        assert len(ids) == len(set(ids)), f"{fname} 中 eval_id 重复"


def test_state_metadata_keys(loop_root: Path) -> None:
    """每条 case 必须带 split / slice / risk_level / protected / scenario_tag。"""
    required = {"split", "slice", "risk_level", "protected", "scenario_tag"}
    for fname in ("train.evalset.json", "val.evalset.json"):
        data = _load(loop_root / "data" / fname)
        for c in data["eval_cases"]:
            state = c.get("session_input", {}).get("state", {})
            missing = required - set(state.keys())
            assert not missing, f"{fname} {c['eval_id']} 缺 state 字段：{missing}"


def test_one_protected_and_one_high_risk_in_val(loop_root: Path) -> None:
    """val 集至少含一个 protected 与一个 high risk case，让 G3/G4 可触发。"""
    data = _load(loop_root / "data" / "val.evalset.json")
    states = [c["session_input"]["state"] for c in data["eval_cases"]]
    assert any(s.get("protected") for s in states)
    assert any(s.get("risk_level") == "high" for s in states)
