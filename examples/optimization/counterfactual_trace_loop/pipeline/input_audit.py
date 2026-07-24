"""Input contract and per-case reliability checks."""

from __future__ import annotations


def audit_eval_cases(cases: list[dict], overlapping_inputs: set[str]) -> list[dict]:
    audited = []
    for case in cases:
        issues = list(case.get("issues", []))
        if not case.get("has_actual", True):
            issues.append("missing_actual_trace")
        if not case.get("has_reference", True):
            issues.append("missing_reference")
        normalized = case.get("normalized_input", "")
        status = case.get("status", "trusted")
        if issues:
            status = "invalid"
        elif normalized and normalized in overlapping_inputs:
            status = "suspect"
            issues.append("train_validation_leakage")
        audited.append({"case_id": case["case_id"], "status": status, "issues": sorted(set(issues))})
    return audited
