"""Verified TargetPrompt mutation, restoration and controlled application."""

from __future__ import annotations

import hashlib
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from trpc_agent_sdk.evaluation import TargetPrompt

from .schema import validate_safe_component


class PromptRestoreError(RuntimeError):
    """The baseline prompt state could not be restored and verified."""


def hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def prompt_hashes(prompts: dict[str, str]) -> dict[str, str]:
    return {name: hash_text(text) for name, text in prompts.items()}


class PromptWorkspace:
    """Own every prompt mutation and verify all writes by reading them back."""

    def __init__(self, target: TargetPrompt) -> None:
        if not target.names():
            raise ValueError("PromptWorkspace requires at least one TargetPrompt field")
        for name in target.names():
            validate_safe_component(name, name="prompt field")
        self._target = target
        self._baseline: dict[str, str] | None = None
        self._baseline_hashes: dict[str, str] | None = None

    async def initialize(self) -> None:
        baseline = await self._target.read_all()
        self._validate_prompt_map(baseline)
        self._baseline = dict(baseline)
        self._baseline_hashes = prompt_hashes(baseline)

    @property
    def baseline(self) -> dict[str, str]:
        self._ensure_initialized()
        return dict(self._baseline or {})

    @property
    def baseline_hashes(self) -> dict[str, str]:
        self._ensure_initialized()
        return dict(self._baseline_hashes or {})

    async def current_hashes(self) -> dict[str, str]:
        self._ensure_initialized()
        current = await self._target.read_all()
        self._validate_prompt_map(current)
        return prompt_hashes(current)

    async def _write_verified(self, prompts: dict[str, str]) -> dict[str, str]:
        self._validate_prompt_map(prompts)
        await self._target.write_all(dict(prompts))
        actual = await self._target.read_all()
        if actual != prompts:
            raise IOError("TargetPrompt read-back differs from requested content")
        return prompt_hashes(actual)

    async def restore(self) -> None:
        self._ensure_initialized()
        try:
            hashes = await self._write_verified(self.baseline)
            if hashes != self.baseline_hashes:
                raise IOError("baseline prompt hashes differ after restoration")
        except BaseException as error:
            raise PromptRestoreError("baseline prompt restoration could not be verified") from error

    async def apply(self, candidate: dict[str, str]) -> dict[str, str]:
        self._ensure_initialized()
        try:
            return await self._write_verified(candidate)
        except BaseException:
            await self.restore()
            raise

    @asynccontextmanager
    async def temporary(self, candidate: dict[str, str]) -> AsyncIterator[dict[str, str]]:
        self._ensure_initialized()
        primary: BaseException | None = None
        try:
            hashes = await self._write_verified(candidate)
            yield hashes
        except BaseException as error:
            primary = error
            raise
        finally:
            try:
                await self.restore()
            except PromptRestoreError as restore_error:
                if primary is not None:
                    raise PromptRestoreError(
                        "baseline restoration failed while propagating another error") from restore_error
                raise

    def create_candidate_target(self, directory: str) -> TargetPrompt:
        """Create an independent file-backed TargetPrompt for AgentOptimizer."""

        self._ensure_initialized()
        root = Path(directory)
        root.mkdir(parents=True, exist_ok=False)
        target = TargetPrompt()
        for name, content in self.baseline.items():
            path = root / f"{name}.md"
            path.write_text(content, encoding="utf-8")
            target.add_path(name, str(path))
        return target

    def _validate_prompt_map(self, prompts: dict[str, str]) -> None:
        expected = set(self._target.names())
        if set(prompts) != expected:
            raise ValueError(f"prompt keys mismatch; expected {sorted(expected)}, got {sorted(prompts)}")
        if any(not isinstance(text, str) for text in prompts.values()):
            raise TypeError("all prompt values must be UTF-8 text strings")

    def _ensure_initialized(self) -> None:
        if self._baseline is None or self._baseline_hashes is None:
            raise RuntimeError("PromptWorkspace.initialize() must be awaited first")
