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
        restore_error: BaseException | None = None
        try:
            await self._target_prompt.write_all(self._baseline)
            if await self._target_prompt.read_all() != self._baseline:
                restore_error = SourceRestoreError("baseline prompt restoration failed")
        except BaseException as error:
            restore_error = error
        if restore_error is None:
            return
        if exc is not None:
            if hasattr(exc, "add_note"):
                exc.add_note(f"PromptSandbox restore failed: {restore_error}")
            return
        raise restore_error
