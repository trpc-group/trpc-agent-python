"""Optimizer adapters and gated atomic source write-back."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from trpc_agent_sdk.evaluation import AgentOptimizer, EvalSet, TargetPrompt


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


async def apply_if_accepted(gate: dict, apply: bool, prompts: dict[str, str], paths: dict[str, Path]) -> dict:
    before = {name: _hash(path) for name, path in paths.items()}
    result = {"requested": apply, "applied": False, "baseline_hashes": before, "final_hashes": before}
    if not gate.get("accepted") or not apply:
        return result
    target = TargetPrompt()
    for name, path in paths.items():
        target.add_path(name, str(path))
    baseline = await target.read_all()
    try:
        await target.write_all(prompts)
        after = {name: _hash(path) for name, path in paths.items()}
        if after == before:
            raise RuntimeError("write-back did not change prompt hashes")
        result.update({"applied": True, "final_hashes": after})
        return result
    except BaseException:
        await target.write_all(baseline)
        raise


async def run_real_optimizer(
    *,
    config_path: Path,
    call_agent,
    prompt_paths: dict[str, Path],
    train_path: Path,
    actionable_case_ids: set[str],
    validation_path: Path,
    output_dir: Path,
) -> dict:
    """Invoke the public optimizer without permitting source mutation."""
    output_dir.mkdir(parents=True, exist_ok=True)
    train = EvalSet.model_validate_json(train_path.read_text(encoding="utf-8"))
    filtered = train.model_copy(
        update={"eval_cases": [case for case in train.eval_cases if case.eval_id in actionable_case_ids]},
        deep=True,
    )
    if not filtered.eval_cases:
        raise ValueError("real optimizer requires at least one actionable trusted training case")
    filtered_path = output_dir / "actionable_train.evalset.json"
    filtered_path.write_text(filtered.model_dump_json(by_alias=True, indent=2), encoding="utf-8")
    target = TargetPrompt()
    for name, path in prompt_paths.items():
        target.add_path(name, str(path))
    baseline_prompts = await target.read_all()
    try:
        result = await AgentOptimizer.optimize(
            config_path=str(config_path),
            call_agent=call_agent,
            target_prompt=target,
            train_dataset_path=str(filtered_path),
            validation_dataset_path=str(validation_path),
            output_dir=str(output_dir),
            update_source=False,
        )
    finally:
        if await target.read_all() != baseline_prompts:
            await target.write_all(baseline_prompts)
    rounds = [record.model_dump(mode="json") if hasattr(record, "model_dump") else record for record in result.rounds]
    config = json.loads(config_path.read_text(encoding="utf-8"))
    return {
        "algorithm": result.algorithm,
        "total_rounds": result.total_rounds,
        "rounds": rounds,
        "baseline_pass_rate": result.baseline_pass_rate,
        "best_pass_rate": result.best_pass_rate,
        "pass_rate_improvement": result.pass_rate_improvement,
        "baseline_metric_breakdown": result.baseline_metric_breakdown,
        "best_metric_breakdown": result.best_metric_breakdown,
        "metric_thresholds": result.metric_thresholds,
        "baseline_prompts": result.baseline_prompts,
        "best_prompts": result.best_prompts,
        "cost": result.total_llm_cost,
        "tokens": result.total_token_usage,
        "duration_seconds": result.duration_seconds,
        "seed": config["optimize"]["algorithm"].get("seed", 42),
        "status": result.status,
    }
