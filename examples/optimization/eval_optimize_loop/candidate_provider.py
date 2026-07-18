# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under the Apache License, Version 2.0.
"""Candidate provider boundary for fake and AgentOptimizer-backed proposals."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Protocol

from trpc_agent_sdk.evaluation import AgentOptimizer
from trpc_agent_sdk.evaluation import CallAgent
from trpc_agent_sdk.evaluation import OptimizeResult
from trpc_agent_sdk.evaluation import TargetPrompt

from .fake.candidate_provider import DeterministicFakeCandidateProvider
from .schemas import CandidateProposal
from .schemas import FakeCandidateScenario
from .schemas import OptimizerCandidateProposal
from .schemas import OptimizerRuntimeParameters


class CandidateProviderError(RuntimeError):
    """A provider could not produce a safe, complete candidate."""


def prompt_mapping_sha256(prompts: dict[str, str]) -> str:
    """Hash a complete prompt mapping using a stable JSON representation."""
    canonical = json.dumps(
        prompts,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class CandidateRequest:
    """Validated inputs handed to one candidate provider."""

    current_prompts: dict[str, str]
    target_prompt: TargetPrompt
    optimizer_config_path: Path
    train_evalset_path: Path
    validation_evalset_path: Path
    output_dir: Path
    seed: int
    retain_native_artifacts: bool = True
    runtime_parameters: OptimizerRuntimeParameters | None = None
    expected_optimizer_sha256: str | None = None


@dataclass(frozen=True)
class CandidateGeneration:
    """A normalized proposal plus an optional native optimizer result."""

    proposal: CandidateProposal
    optimize_result: OptimizeResult | None = None


class CandidateProvider(Protocol):
    """Asynchronous candidate generation used by the pipeline orchestrator."""

    async def propose(self, request: CandidateRequest) -> CandidateGeneration:
        """Return one complete proposal without updating source prompts."""


class FakeCandidateProviderAdapter:
    """Lift the pure synchronous fake provider into the common async boundary."""

    def __init__(self, scenario: FakeCandidateScenario) -> None:
        self._scenario = scenario

    async def propose(self, request: CandidateRequest) -> CandidateGeneration:
        proposal = DeterministicFakeCandidateProvider().propose(
            request.current_prompts,
            scenario=self._scenario,
            seed=request.seed,
        )
        return CandidateGeneration(proposal=proposal)


class AgentOptimizerCandidateProvider:
    """Adapt AgentOptimizer to the pipeline's review-before-write contract."""

    def __init__(self, call_agent: CallAgent) -> None:
        self._call_agent = call_agent

    @staticmethod
    def _replace_persisted_connection_values(value: object) -> object:
        """递归将可能被 SDK 复制到产物的连接值替换为环境占位符。"""
        if isinstance(value, list):
            return [
                AgentOptimizerCandidateProvider._replace_persisted_connection_values(item)
                for item in value
            ]
        if not isinstance(value, dict):
            return value
        placeholders = {
            "api_key": "${TRPC_AGENT_API_KEY}",
            "apiKey": "${TRPC_AGENT_API_KEY}",
            "base_url": "${TRPC_AGENT_BASE_URL}",
            "baseUrl": "${TRPC_AGENT_BASE_URL}",
        }
        return {
            key: placeholders.get(
                key,
                AgentOptimizerCandidateProvider._replace_persisted_connection_values(item),
            )
            for key, item in value.items()
        }

    @staticmethod
    def _prepare_runtime_config(request: CandidateRequest) -> Path:
        """由已校验模板生成无明文凭据的本次运行配置。"""
        if request.runtime_parameters is None:
            return request.optimizer_config_path

        try:
            raw = request.optimizer_config_path.read_bytes()
            if (
                request.expected_optimizer_sha256 is not None
                and sha256(raw).hexdigest() != request.expected_optimizer_sha256
            ):
                raise CandidateProviderError("optimizer config changed after preparation")
            payload = AgentOptimizerCandidateProvider._replace_persisted_connection_values(
                json.loads(raw.decode("utf-8"))
            )
            algorithm = payload["optimize"]["algorithm"]
        except CandidateProviderError:
            raise
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError) as exc:
            raise CandidateProviderError(f"failed to prepare optimizer runtime config: {exc}") from exc

        parameters = request.runtime_parameters
        reflection_lm: dict[str, object] = {
            "provider_name": parameters.provider_name,
            "model_name": parameters.model_name,
            "variant": parameters.variant,
            "base_url": "${TRPC_AGENT_BASE_URL}",
            "api_key": "${TRPC_AGENT_API_KEY}",
            "generation_config": {
                "temperature": parameters.temperature,
                "max_tokens": parameters.max_tokens,
            },
        }
        if parameters.think is not None:
            reflection_lm["think"] = parameters.think
        algorithm["reflection_lm"] = reflection_lm
        algorithm["max_candidate_proposals"] = parameters.max_candidate_proposals

        runtime_path = request.output_dir.parent / "optimizer.runtime.json"
        try:
            runtime_path.parent.mkdir(parents=True, exist_ok=True)
            runtime_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        except OSError as exc:
            raise CandidateProviderError(f"failed to write optimizer runtime config: {exc}") from exc
        return runtime_path

    async def propose(self, request: CandidateRequest) -> CandidateGeneration:
        runtime_config_path = self._prepare_runtime_config(request)
        try:
            result = await AgentOptimizer.optimize(
                config_path=str(runtime_config_path),
                call_agent=self._call_agent,
                target_prompt=request.target_prompt,
                train_dataset_path=str(request.train_evalset_path),
                validation_dataset_path=str(request.validation_evalset_path),
                output_dir=str(request.output_dir),
                update_source=False,
                verbose=0,
            )
        except Exception as exc:
            raise CandidateProviderError(f"AgentOptimizer failed: {exc}") from exc

        if result.status != "SUCCEEDED":
            raise CandidateProviderError(
                f"AgentOptimizer returned {result.status}: {result.error_message or result.finish_reason}"
            )
        expected_fields = set(request.current_prompts)
        if set(result.baseline_prompts) != expected_fields:
            raise CandidateProviderError("optimizer baseline prompt fields do not match the prepared target")
        if result.baseline_prompts != request.current_prompts:
            raise CandidateProviderError("optimizer baseline prompts do not match the prepared working prompts")
        if set(result.best_prompts) != expected_fields:
            raise CandidateProviderError("optimizer best prompt fields do not match the prepared target")
        if any(not isinstance(value, str) for value in result.best_prompts.values()):
            raise CandidateProviderError("optimizer best prompts must contain only strings")

        parent_hash = prompt_mapping_sha256(request.current_prompts)
        candidate_hash = prompt_mapping_sha256(result.best_prompts)
        changed_fields = [
            name
            for name in request.current_prompts
            if request.current_prompts[name] != result.best_prompts[name]
        ]
        retained_output_dir = str(request.output_dir) if request.retain_native_artifacts else None
        proposal = OptimizerCandidateProposal(
            prompts=dict(result.best_prompts),
            changed_fields=changed_fields,
            rationale=(
                f"AgentOptimizer selected the best candidate after {result.total_rounds} rounds "
                f"with finish_reason={result.finish_reason}."
            ),
            parent_prompt_sha256=parent_hash,
            candidate_prompt_sha256=candidate_hash,
            candidate_id=f"optimizer-{candidate_hash[:12]}",
            finish_reason=result.finish_reason,
            stop_reason=result.stop_reason,
            baseline_pass_rate=result.baseline_pass_rate,
            best_pass_rate=result.best_pass_rate,
            optimizer_output_dir=retained_output_dir,
        )
        if not request.retain_native_artifacts:
            try:
                shutil.rmtree(request.output_dir)
            except OSError as exc:
                raise CandidateProviderError(
                    f"failed to discard optimizer artifacts: {exc}"
                ) from exc
        return CandidateGeneration(proposal=proposal, optimize_result=result)
