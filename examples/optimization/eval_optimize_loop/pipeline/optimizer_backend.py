"""Safe, auditable adapter around the public ``AgentOptimizer`` API."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any, Awaitable, Callable, Protocol

from trpc_agent_sdk.evaluation import AgentOptimizer, TargetPrompt

from ..fake.fake_agent import FakeSupportAgent
from .config import canonical_sha256
from .models import CandidateRecord, GateDecision


class PipelineExecutionError(RuntimeError):
    """The optimizer ran unsuccessfully, so no safe decision can be produced."""


class OptimizerBackend(Protocol):
    async def generate_candidates(
        self,
        *,
        baseline_prompts: dict[str, str],
        train_dataset_path: Path,
        validation_dataset_path: Path,
        output_dir: Path,
    ) -> list[CandidateRecord]: ...


def _runtime_config(raw_config: dict[str, Any]) -> dict[str, Any]:
    """Return the SDK-only config without ever resolving secrets from env."""
    try:
        payload = {name: raw_config[name] for name in ("evaluate", "optimize")}
    except KeyError as exc:
        raise PipelineExecutionError(f"live optimizer config is missing {exc.args[0]!r}") from exc

    def reject_literal_secrets(value: object, path: tuple[str, ...] = ()) -> None:
        if isinstance(value, dict):
            for key, nested in value.items():
                normalized = "".join(character for character in str(key).lower() if character.isalnum())
                is_secret = normalized == "key" or normalized.endswith("key") or any(
                    marker in normalized for marker in ("token", "secret", "password", "credential")
                )
                if is_secret and isinstance(nested, str) and not (nested.startswith("${") and nested.endswith("}")):
                    raise PipelineExecutionError(f"runtime config refuses literal secret at {'.'.join((*path, str(key)))}")
                reject_literal_secrets(nested, (*path, str(key)))
        elif isinstance(value, list):
            for index, nested in enumerate(value):
                reject_literal_secrets(nested, (*path, str(index)))

    reject_literal_secrets(payload)
    return payload


def _full_prompt_map(candidate: object, baseline_prompts: dict[str, str]) -> dict[str, str] | None:
    if not isinstance(candidate, dict) or set(candidate) != set(baseline_prompts):
        return None
    if not all(isinstance(value, str) for value in candidate.values()):
        return None
    return dict(candidate)


class AgentOptimizerBackend:
    """Generate proposals in a disposable prompt workspace.

    The concrete agent is deliberately injected through ``call_agent_factory`` for
    production adaptation.  The checked-in demo uses ``FakeSupportAgent`` so the
    only live dependency in this mode is the optimizer reflection model itself.
    """

    def __init__(
        self,
        *,
        raw_config: dict[str, Any],
        candidate_scope: str = "accepted_rounds",
        call_agent_factory: Callable[[TargetPrompt], Callable[[str], Awaitable[str]]] | None = None,
    ) -> None:
        if candidate_scope not in {"best_only", "accepted_rounds", "all"}:
            raise ValueError(f"unsupported candidate scope: {candidate_scope}")
        self._raw_config = raw_config
        self._candidate_scope = candidate_scope
        self._call_agent_factory = call_agent_factory or (lambda target: FakeSupportAgent(target).call_agent)
        self.audit: dict[str, object] = {"duplicate_candidate_ids": {}, "skipped_candidate_ids": []}

    async def generate_candidates(
        self,
        *,
        baseline_prompts: dict[str, str],
        train_dataset_path: Path,
        validation_dataset_path: Path,
        output_dir: Path,
    ) -> list[CandidateRecord]:
        optimizer_dir = output_dir / "optimizer" / "agent_optimizer"
        optimizer_dir.mkdir(parents=True, exist_ok=True)
        runtime_path = optimizer_dir / "_optimizer.runtime.json"
        runtime_path.write_text(json.dumps(_runtime_config(self._raw_config), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

        with tempfile.TemporaryDirectory(prefix="trpc-agent-issue91-live-") as temporary_dir:
            prompt_dir = Path(temporary_dir)
            target = TargetPrompt()
            for name, content in baseline_prompts.items():
                path = prompt_dir / f"{name}.md"
                path.write_text(content, encoding="utf-8")
                target.add_path(name, str(path))
            try:
                result = await AgentOptimizer.optimize(
                    config_path=str(runtime_path),
                    call_agent=self._call_agent_factory(target),
                    target_prompt=target,
                    train_dataset_path=str(train_dataset_path),
                    validation_dataset_path=str(validation_dataset_path),
                    output_dir=str(optimizer_dir),
                    update_source=False,
                    verbose=0,
                )
            except Exception as exc:
                raise PipelineExecutionError("AgentOptimizer.optimize failed") from exc
        if getattr(result, "status", None) != "SUCCEEDED":
            raise PipelineExecutionError(f"AgentOptimizer finished with status {getattr(result, 'status', 'unknown')}")
        records = self._extract_candidates(result, baseline_prompts)
        (optimizer_dir / "candidate_extraction.json").write_text(
            json.dumps(self.audit, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        return records

    def _extract_candidates(self, result: object, baseline_prompts: dict[str, str]) -> list[CandidateRecord]:
        entries: list[tuple[str, int | None, bool | None, str | None, object, float | None, float | None]] = []
        rounds = list(getattr(result, "rounds", []) or [])
        if self._candidate_scope != "best_only":
            for fallback_index, round_record in enumerate(rounds, start=1):
                accepted = bool(getattr(round_record, "accepted", False))
                if self._candidate_scope == "accepted_rounds" and not accepted:
                    continue
                round_index = int(getattr(round_record, "round", fallback_index))
                entries.append((
                    f"round-{round_index:03d}", round_index, accepted, getattr(round_record, "acceptance_reason", None),
                    getattr(round_record, "candidate_prompts", None), getattr(round_record, "generation_cost_usd", getattr(round_record, "cost_usd", None)),
                    getattr(round_record, "duration_seconds", None),
                ))
        entries.append((
            "best", None, True, "OptimizeResult.best_prompts", getattr(result, "best_prompts", None),
            getattr(result, "generation_cost_usd", getattr(result, "cost_usd", None)), getattr(result, "duration_seconds", None),
        ))

        retained: list[CandidateRecord] = []
        seen: dict[str, CandidateRecord] = {}
        duplicates: dict[str, list[str]] = {}
        skipped: list[str] = []
        for candidate_id, round_index, optimizer_accepted, optimizer_reason, prompts, generation_cost_usd, duration_seconds in entries:
            full_prompts = _full_prompt_map(prompts, baseline_prompts)
            if full_prompts is None:
                skipped.append(candidate_id)
                continue
            digest = canonical_sha256(full_prompts)
            if digest in seen:
                duplicates.setdefault(seen[digest].candidate_id, []).append(candidate_id)
                continue
            record = CandidateRecord(
                candidate_id=candidate_id,
                source="agent_optimizer",
                round_index=round_index,
                prompts=full_prompts,
                optimizer_accepted=optimizer_accepted,
                optimizer_reason=optimizer_reason or "",
                generation_cost_usd=generation_cost_usd,
                duration_seconds=duration_seconds,
            )
            seen[digest] = record
            retained.append(record)
        self.audit = {"duplicate_candidate_ids": duplicates, "skipped_candidate_ids": skipped}
        return retained


async def write_back_after_gate(
    target_prompt: TargetPrompt,
    baseline_prompts: dict[str, str],
    candidate_prompts: dict[str, str],
    gate: GateDecision,
) -> bool:
    """Opt-in source write-back with compare-and-swap style integrity checks."""
    if not gate.accepted:
        return False
    current = await target_prompt.read_all()
    if canonical_sha256(current) != canonical_sha256(baseline_prompts):
        raise PipelineExecutionError("source baseline changed before optional write-back")
    if _full_prompt_map(candidate_prompts, baseline_prompts) is None:
        raise PipelineExecutionError("optional write-back requires a complete prompt map")
    await target_prompt.write_all(candidate_prompts)
    if canonical_sha256(await target_prompt.read_all()) != canonical_sha256(candidate_prompts):
        raise PipelineExecutionError("optional write-back candidate verification failed")
    return True
