# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Prompt optimization helpers for the eval-optimize-loop example."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any
from typing import Awaitable
from typing import Callable

from .types import BaselineSplitResult
from .types import OptimizationRoundRecord
from .types import OptimizationRunRecord

CallAgent = Callable[[str], Awaitable[str]]
TARGET_PROMPT_PATHS = {
    "system_prompt": "prompts/system.md",
    "skill": "prompts/skill.md",
}
FINAL_ANSWER_FIX_MARKER = "[fake_optimizer:final_answer_mismatch]"

_KNOWN_ANSWERS = {
    "小明有 4 个苹果，又买了 7 个，现在一共有多少个苹果？": "答案：11 个",
    "一件衣服原价 200 元，打 8 折后多少钱？": "答案：160 元",
    "40 名学生中 25% 戴眼镜，戴眼镜的有多少人？": "答案：10 人",
    "教室里有 5 排座位，每排 8 个，一共有多少个座位？": "答案：42 个",
    "1 升水重 1 千克，3.5 升水重多少千克？": "答案：3.5 千克",
    "班里 30 人，其中 60% 是女生，有多少名女生？": "答案：20 人",
}

_BASELINE_ANSWERS = {
    "小明有 4 个苹果，又买了 7 个，现在一共有多少个苹果？": "答案：11 个",
    "一件衣服原价 200 元，打 8 折后多少钱？": "答案：180 元",
    "40 名学生中 25% 戴眼镜，戴眼镜的有多少人？": "计算 40 的 25%，答案：10 人",
    "教室里有 5 排座位，每排 8 个，一共有多少个座位？": "答案：40 个",
    "1 升水重 1 千克，3.5 升水重多少千克？": "答案：4 千克",
    "班里 30 人，其中 60% 是女生，有多少名女生？": "答案：20 人",
}


class PromptOptimizer:
    """Run fake or real prompt optimization for this example."""

    def __init__(
        self,
        *,
        example_root: Path,
        output_dir: Path,
        optimizer_config_path: Path,
        train_evalset_path: Path,
        val_evalset_path: Path,
    ) -> None:
        self.example_root = example_root
        self.output_dir = output_dir
        self.optimizer_config_path = optimizer_config_path
        self.train_evalset_path = train_evalset_path
        self.val_evalset_path = val_evalset_path

    def prompt_paths(self) -> dict[str, Path]:
        return {name: self.example_root / rel for name, rel in TARGET_PROMPT_PATHS.items()}

    def read_baseline_prompts(self) -> dict[str, str]:
        return {name: path.read_text(encoding="utf-8") for name, path in self.prompt_paths().items()}

    async def optimize(
        self,
        *,
        mode: str,
        optimizer_payload: dict[str, Any],
        train_baseline: BaselineSplitResult,
        val_baseline: BaselineSplitResult,
        call_agent_train_path: Path | None = None,
        call_agent_val_path: Path | None = None,
    ) -> OptimizationRunRecord:
        if mode == "real":
            return await self._run_real_optimizer(
                optimizer_payload,
                train_path=call_agent_train_path,
                val_path=call_agent_val_path,
            )
        return self._run_fake_optimizer(
            optimizer_payload=optimizer_payload,
            train_baseline=train_baseline,
            val_baseline=val_baseline,
        )

    def write_call_agent_evalsets(self) -> tuple[Path, Path]:
        """Persist temporary non-trace evalsets for optimizer call_agent mode."""
        input_dir = self.output_dir / "optimizer_inputs"
        input_dir.mkdir(parents=True, exist_ok=True)
        train_path = input_dir / "train.call_agent.evalset.json"
        val_path = input_dir / "val.call_agent.evalset.json"
        _write_json(train_path, _call_agent_evalset_payload(_load_json(self.train_evalset_path)))
        _write_json(val_path, _call_agent_evalset_payload(_load_json(self.val_evalset_path)))
        return train_path, val_path

    def call_agent_from_prompts(self, prompts: dict[str, str]) -> CallAgent:

        async def call_agent(query: str) -> str:
            return deterministic_answer(query, prompts)

        return call_agent

    def _run_fake_optimizer(
        self,
        *,
        optimizer_payload: dict[str, Any],
        train_baseline: BaselineSplitResult,
        val_baseline: BaselineSplitResult,
    ) -> OptimizationRunRecord:
        started = time.perf_counter()
        baseline_prompts = self.read_baseline_prompts()
        seed = _seed_from_optimizer(optimizer_payload)
        reason = _reason_from_failures(train_baseline, val_baseline)
        best_prompts = _fake_best_prompts(baseline_prompts, reason)
        round_record = OptimizationRoundRecord(
            round=1,
            optimized_field_names=["system_prompt", "skill"],
            before=baseline_prompts,
            after=best_prompts,
            reason=reason,
            accepted=True,
            validation_pass_rate=0.0,
            duration_seconds=0.0,
            cost=0.0,
        )
        return OptimizationRunRecord(
            target_prompt_names=list(baseline_prompts),
            baseline_prompts=baseline_prompts,
            best_prompts=best_prompts,
            rounds=[round_record],
            total_rounds=1,
            total_cost=0.0,
            duration_seconds=time.perf_counter() - started,
            seed=seed,
            reason=reason,
            artifacts={
                "mode": "fake",
                "source_prompts_updated": False,
            },
        )

    async def _run_real_optimizer(
        self,
        optimizer_payload: dict[str, Any],
        *,
        train_path: Path | None,
        val_path: Path | None,
    ) -> OptimizationRunRecord:
        from trpc_agent_sdk.evaluation import AgentOptimizer
        from trpc_agent_sdk.evaluation import TargetPrompt

        started = time.perf_counter()
        baseline_prompts = self.read_baseline_prompts()
        if train_path is None or val_path is None:
            raise ValueError("real mode requires call_agent evalset paths")
        artifact_dir = self.output_dir / "optimizer_artifacts"
        target_prompt = TargetPrompt()
        for name, path in self.prompt_paths().items():
            target_prompt.add_path(name, str(path))

        result = await AgentOptimizer.optimize(
            config_path=str(self.optimizer_config_path),
            call_agent=self.call_agent_from_prompts_file(),
            target_prompt=target_prompt,
            train_dataset_path=str(train_path),
            validation_dataset_path=str(val_path),
            output_dir=str(artifact_dir),
            update_source=False,
            verbose=0,
        )
        return _optimization_record_from_sdk_result(
            result,
            baseline_prompts=baseline_prompts,
            seed=_seed_from_optimizer(optimizer_payload),
            duration_seconds=time.perf_counter() - started,
            artifacts={
                "mode": "real",
                "source_prompts_updated": False,
                "optimizer_artifact_dir": _relative_to_example(artifact_dir, self.example_root),
                "train_call_agent_evalset_path": _relative_to_example(train_path, self.example_root),
                "val_call_agent_evalset_path": _relative_to_example(val_path, self.example_root),
            },
        )

    def call_agent_from_prompts_file(self) -> CallAgent:

        async def call_agent(query: str) -> str:
            return deterministic_answer(query, self.read_baseline_prompts())

        return call_agent


def deterministic_answer(query: str, prompts: dict[str, str]) -> str:
    prompt_text = "\n".join(prompts.values()).lower()
    if FINAL_ANSWER_FIX_MARKER.lower() in prompt_text or "verify arithmetic" in prompt_text:
        return _KNOWN_ANSWERS.get(query, "")
    return _BASELINE_ANSWERS.get(query, "")


def _fake_best_prompts(baseline_prompts: dict[str, str], reason: str) -> dict[str, str]:
    best = dict(baseline_prompts)
    best["system_prompt"] = "".join([
        best["system_prompt"].rstrip(),
        "\n\n",
        "Fake optimizer guidance: verify arithmetic before answering, ",
        f"preserve the requested answer format, and address {reason}. ",
        FINAL_ANSWER_FIX_MARKER,
        "\n",
    ])
    best["skill"] = "".join([
        best["skill"].rstrip(),
        "\n\n",
        "When a baseline failure is attributed to final_answer_mismatch, recompute the numeric result ",
        "and keep the expected unit in the final answer.\n",
    ])
    return best


def _reason_from_failures(train: BaselineSplitResult, val: BaselineSplitResult) -> str:
    counts: dict[str, int] = {}
    for summary in (train.failure_attribution_summary(), val.failure_attribution_summary()):
        for category, count in summary.items():
            if count:
                counts[category] = counts.get(category, 0) + count
    if not counts:
        return "No baseline failure attribution categories were found; keep prompts stable."
    parts = [f"{category}={count}" for category, count in sorted(counts.items())]
    return "Address failure attribution categories: " + ", ".join(parts)


def _optimization_record_from_sdk_result(
    result: Any,
    *,
    baseline_prompts: dict[str, str],
    seed: int | None,
    duration_seconds: float,
    artifacts: dict[str, Any],
) -> OptimizationRunRecord:
    best_prompts = dict(getattr(result, "best_prompts", None) or baseline_prompts)
    rounds = []
    before = dict(getattr(result, "baseline_prompts", None) or baseline_prompts)
    for raw_round in getattr(result, "rounds", []) or []:
        after = dict(getattr(raw_round, "candidate_prompts", None) or before)
        rounds.append(
            OptimizationRoundRecord(
                round=int(getattr(raw_round, "round",
                                  len(rounds) + 1)),
                optimized_field_names=list(getattr(raw_round, "optimized_field_names", []) or []),
                before=before,
                after=after,
                reason=_round_reason(raw_round),
                accepted=bool(getattr(raw_round, "accepted", False)),
                validation_pass_rate=float(getattr(raw_round, "validation_pass_rate", 0.0) or 0.0),
                duration_seconds=float(getattr(raw_round, "duration_seconds", 0.0) or 0.0),
                cost=float(getattr(raw_round, "round_llm_cost", 0.0) or 0.0),
            ))
        before = after

    return OptimizationRunRecord(
        target_prompt_names=list(best_prompts),
        baseline_prompts=dict(getattr(result, "baseline_prompts", None) or baseline_prompts),
        best_prompts=best_prompts,
        rounds=rounds,
        total_rounds=int(getattr(result, "total_rounds", len(rounds)) or len(rounds)),
        total_cost=float(getattr(result, "total_llm_cost", 0.0) or 0.0),
        duration_seconds=float(getattr(result, "duration_seconds", duration_seconds) or duration_seconds),
        seed=seed,
        reason=_result_reason(result),
        artifacts=artifacts,
    )


def _round_reason(raw_round: Any) -> str:
    reason = getattr(raw_round, "acceptance_reason", None) or getattr(raw_round, "skip_reason", None)
    if reason:
        return str(reason)
    diagnosis = getattr(raw_round, "per_field_diagnosis", None)
    if isinstance(diagnosis, dict) and diagnosis:
        return "; ".join(f"{name}: {text}" for name, text in diagnosis.items())
    error = getattr(raw_round, "error_message", None)
    if error:
        return str(error)
    return "Optimizer produced a candidate prompt."


def _result_reason(result: Any) -> str:
    status = getattr(result, "status", "")
    finish_reason = getattr(result, "finish_reason", "")
    improvement = getattr(result, "pass_rate_improvement", None)
    return f"Optimizer finished with status={status}, finish_reason={finish_reason}, improvement={improvement}."


def _call_agent_evalset_payload(payload: dict[str, Any]) -> dict[str, Any]:
    converted = dict(payload)
    cases = []
    for case in payload.get("eval_cases", []):
        if not isinstance(case, dict):
            continue
        converted_case = dict(case)
        converted_case.pop("eval_mode", None)
        converted_case.pop("actual_conversation", None)
        converted_case.pop("actualConversation", None)
        cases.append(converted_case)
    converted["eval_cases"] = cases
    return converted


def _seed_from_optimizer(payload: dict[str, Any]) -> int | None:
    algorithm = ((payload.get("optimize") or {}).get("algorithm") or {})
    seed = algorithm.get("seed") if isinstance(algorithm, dict) else None
    return int(seed) if isinstance(seed, int) else None


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _relative_to_example(path: Path, example_root: Path) -> str:
    try:
        return str(path.resolve().relative_to(example_root.resolve()))
    except ValueError:
        return os.path.relpath(path.resolve(), example_root.resolve())
