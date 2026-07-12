"""Fake optimizer with the same candidate contract as the real adapter."""

from __future__ import annotations


async def optimize(prompts: dict[str, str], targets: list[str], profile: str = "overfit") -> dict:
    candidate = dict(prompts)
    if profile == "ineffective":
        return {
            "total_rounds": 1,
            "rounds": [
                {
                    "round": 1,
                    "profile": profile,
                    "targets": [],
                    "candidate_prompts": candidate,
                    "cost": 0.0,
                    "tokens": 0,
                    "duration_seconds": 0.0,
                }
            ],
            "best_prompts": candidate,
            "cost": 0.0,
            "tokens": 0,
            "seed": 42,
            "candidate_profile": profile,
        }
    if profile not in ("accepted", "overfit"):
        raise ValueError(f"unknown candidate profile: {profile}")
    if "router_prompt" in targets:
        candidate["router_prompt"] += "\nREFUND_ROUTE=STRICT\n"
        if profile == "overfit":
            candidate["router_prompt"] += "BILLING_TO_REFUND=ON\n"
    if "skill_prompt" in targets:
        candidate["skill_prompt"] += "\nREFUND_REASON=REQUIRED\n"
    if "system_prompt" in targets:
        candidate["system_prompt"] += "\nJSON_STATUS=ALWAYS_REQUIRED\n"
    return {
        "total_rounds": 1,
        "rounds": [
            {
                "round": 1,
                "profile": profile,
                "targets": list(targets),
                "candidate_prompts": candidate,
                "cost": 0.0,
                "tokens": 0,
                "duration_seconds": 0.0,
            }
        ],
        "best_prompts": candidate,
        "cost": 0.0,
        "tokens": 0,
        "seed": 42,
        "candidate_profile": profile,
    }
