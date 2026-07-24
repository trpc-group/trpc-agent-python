from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_FILES = [
    ROOT / "data" / "train.evalset.json",
    ROOT / "data" / "val.evalset.json",
]
NO_SAMPLE_ID_FILES = [
    ROOT / "eval_loop" / "fake_model.py",
    ROOT / "eval_loop" / "optimizer.py",
    ROOT / "eval_loop" / "backends.py",
]
FORBIDDEN_MODEL_ACCESSES = [
    ".expectation",
    ".split",
    ".protected",
    ".tags",
    ".simulated_outputs",
    ".case_id",
]


def test_sample_case_ids_do_not_appear_in_fake_runtime_logic():
    case_ids = {
        case["eval_id"]
        for path in DATA_FILES
        for case in json.loads(path.read_text(encoding="utf-8"))["eval_cases"]
    }

    for path in NO_SAMPLE_ID_FILES:
        source = path.read_text(encoding="utf-8")
        leaked = sorted(case_id for case_id in case_ids if case_id in source)
        assert leaked == [], f"{path.name} contains sample case ids: {leaked}"


def test_fake_model_source_cannot_read_evaluator_only_case_fields():
    source = (ROOT / "eval_loop" / "fake_model.py").read_text(encoding="utf-8")

    leaked = [token for token in FORBIDDEN_MODEL_ACCESSES if token in source]

    assert leaked == []


def test_fake_runtime_uses_no_private_optimizer_markers():
    for relative in ("eval_loop/fake_model.py", "eval_loop/optimizer.py"):
        source = (ROOT / relative).read_text(encoding="utf-8")
        assert "OPTIMIZER_MARKER" not in source
