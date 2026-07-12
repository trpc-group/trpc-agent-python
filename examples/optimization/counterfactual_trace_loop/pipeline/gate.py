"""All-must-pass acceptance gate with explicit evidence."""

from __future__ import annotations


def evaluate_gate(observed: dict, config: dict) -> dict:
    delta = observed["candidate_validation"] - observed["baseline_validation"]
    train_delta = observed["candidate_train"] - observed["baseline_train"]
    min_delta = config["min_validation_delta"]
    specs = [
        ("validation_delta", delta >= min_delta, delta, min_delta, "VALIDATION_DELTA"),
        (
            "trusted_validation_non_regression",
            observed["trusted_candidate"] >= observed["trusted_baseline"],
            observed["trusted_candidate"] - observed["trusted_baseline"],
            0.0,
            "TRUSTED_REGRESSION",
        ),
        ("no_new_hard_fail", not observed["new_hard_fails"], observed["new_hard_fails"], [], "NEW_HARD_FAIL"),
        (
            "protected_cases",
            not observed["protected_regressions"],
            observed["protected_regressions"],
            [],
            "PROTECTED_REGRESSION",
        ),
        (
            "no_train_only_improvement",
            not (train_delta > 0 and delta <= 0),
            {"train_delta": train_delta, "validation_delta": delta},
            "validation_delta > 0 when train_delta > 0",
            "TRAIN_ONLY_IMPROVEMENT",
        ),
        (
            "no_severity_escalation",
            not observed["severity_escalations"],
            observed["severity_escalations"],
            [],
            "SEVERITY_ESCALATION",
        ),
        ("cost_budget", observed["cost"] <= config["max_cost"], observed["cost"], config["max_cost"], "COST_BUDGET"),
        (
            "latency_budget",
            observed["duration_seconds"] <= config["max_latency_seconds"],
            observed["duration_seconds"],
            config["max_latency_seconds"],
            "LATENCY_BUDGET",
        ),
        ("evidence_sufficient", observed["evidence_sufficient"], observed["evidence_sufficient"], True, "NEEDS_REVIEW"),
    ]
    checks = [
        {"name": n, "passed": p, "observed": o, "threshold": t, "reason": "passed" if p else code}
        for n, p, o, t, code in specs
    ]
    return {
        "accepted": all(item["passed"] for item in checks),
        "checks": checks,
        "reason_codes": [code for (_, passed, _, _, code) in specs if not passed],
    }
