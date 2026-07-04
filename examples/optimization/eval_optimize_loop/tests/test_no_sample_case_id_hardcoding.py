from __future__ import annotations

import json
from pathlib import Path

from examples.optimization.eval_optimize_loop.run_pipeline import run_pipeline


BUSINESS_LOGIC_FILES = [
    "eval_loop/fake_model.py",
    "eval_loop/fake_judge.py",
    "eval_loop/optimizer.py",
    "eval_loop/report.py",
    "eval_loop/gate.py",
]


def test_sample_case_ids_do_not_appear_in_business_logic():
    root = Path("examples/optimization/eval_optimize_loop")
    case_ids = set()
    for rel in ("data/train.evalset.json", "data/val.evalset.json"):
        payload = json.loads((root / rel).read_text(encoding="utf-8"))
        case_ids.update(case["id"] for case in payload["cases"])

    for rel in BUSINESS_LOGIC_FILES:
        source = (root / rel).read_text(encoding="utf-8")
        leaked = sorted(case_id for case_id in case_ids if case_id in source)
        assert leaked == [], f"{rel} contains sample case ids: {leaked}"


def test_uuid_style_case_ids_still_drive_expected_behavior(tmp_path: Path):
    train_path = tmp_path / "train.evalset.json"
    val_path = tmp_path / "val.evalset.json"
    optimizer_path = tmp_path / "optimizer.json"
    prompt_path = tmp_path / "prompt.txt"

    train_path.write_text(
        json.dumps({
            "split": "train",
            "cases": [
                _json_case("1d5b548a-39ec-451d-9b01-7d73101b95b1", "train"),
                _exact_case("c28596bf-370d-4898-bdf7-e4cc306fa4d3", "train", protected=False),
                _rubric_case("3aa39806-878e-4634-9a68-d7f479ab7c9d", "train"),
            ],
        }),
        encoding="utf-8",
    )
    val_path.write_text(
        json.dumps({
            "split": "validation",
            "cases": [
                _json_case("534f045c-06f8-4b96-af55-cb7b6712cc0c", "validation"),
                _rubric_case("b6914390-b2bb-4f43-a35b-24b6fe2825cd", "validation"),
                _exact_case("70c59d31-adf5-409f-a457-286bcb887f52", "validation", protected=True),
            ],
        }),
        encoding="utf-8",
    )
    optimizer_path.write_text(
        json.dumps({
            "seed": 91,
            "optimizer": {"name": "fake_two_candidate_optimizer"},
            "metrics": {"case_score": "mean"},
            "gate": {
                "min_val_score_improvement": 0.01,
                "allow_new_hard_fail": False,
                "protected_case_ids": ["70c59d31-adf5-409f-a457-286bcb887f52"],
                "max_score_drop_per_case": 0.0,
                "max_total_cost": 1.0,
            },
        }),
        encoding="utf-8",
    )
    prompt_path.write_text("Baseline prompt", encoding="utf-8")

    report = run_pipeline(
        train_path=train_path,
        val_path=val_path,
        optimizer_config_path=optimizer_path,
        prompt_path=prompt_path,
        output_dir=tmp_path / "out",
        mode="fake",
        trace=True,
    )

    decisions = {decision.candidate_id: decision for decision in report.gate_decisions}
    assert report.selected_candidate == "candidate_002_safe"
    assert decisions["candidate_001_overfit"].accepted is False
    assert decisions["candidate_001_overfit"].overfit_detected is True
    assert decisions["candidate_002_safe"].accepted is True


def _json_case(case_id: str, split: str) -> dict:
    return {
        "id": case_id,
        "input": "Return strict JSON.",
        "expectation": {
            "type": "json",
            "required_keys": ["answer"],
            "expected_values": {"answer": "ok"},
            "expected_failure_category": "format_violation",
        },
    }


def _exact_case(case_id: str, split: str, *, protected: bool) -> dict:
    payload = {
        "id": case_id,
        "input": "Answer exactly YES.",
        "expectation": {
            "type": "exact",
            "expected": "YES",
            "expected_failure_category": "final_response_mismatch",
        },
        "protected": protected,
        "tags": ["baseline_pass"] if protected else [],
    }
    return payload


def _rubric_case(case_id: str, split: str) -> dict:
    return {
        "id": case_id,
        "input": "Explain in prose.",
        "expectation": {
            "type": "rubric",
            "must_include": ["cache", "stale data"],
            "forbidden": ["{", "}", "json"],
            "max_chars": 120,
            "expected_failure_category": "format_violation",
        },
        "tags": ["prose"],
    }
