from __future__ import annotations

from trpc_agent_sdk.evaluation import TargetPrompt


class SourceRestoreError(RuntimeError):
    pass


class PromptSandbox:
    def __init__(self, target_prompt: TargetPrompt, candidate_prompts: dict[str, str]) -> None:
        self._target_prompt = target_prompt
        self._candidate_prompts = candidate_prompts
        self._baseline: dict[str, str] | None = None

    async def __aenter__(self) -> "PromptSandbox":
        self._baseline = await self._target_prompt.read_all()
        await self._target_prompt.write_all(self._candidate_prompts)
        if await self._target_prompt.read_all() != self._candidate_prompts:
            raise SourceRestoreError("candidate prompt verification failed")
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._baseline is None:
            return
        await self._target_prompt.write_all(self._baseline)
        if await self._target_prompt.read_all() != self._baseline:
            raise SourceRestoreError("baseline prompt restoration failed")
