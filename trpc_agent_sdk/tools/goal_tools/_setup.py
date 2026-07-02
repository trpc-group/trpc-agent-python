# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Enforcement for the Goal capability: guidance / nudge injection, premature
final-response interception + same-invocation re-run, and the ``setup_goal``
assembly helper.

Phase-1 core. While a goal is ``active``:
  - ``before_model`` injects the guidance (once per request) and, if a re-run
    was requested, appends a user-role nudge to the request.
  - ``after_model`` watches each stream chunk; on a *premature* final response
    (looks final, no tool call, no error) it suppresses the finalisation,
    schedules a nudge and flips the agent loop's ``running`` flag back to
    ``True`` so :class:`LlmAgent` re-runs within the same invocation. A
    ``max_retries`` budget guarantees fail-open (the loop never spins forever).

Counters live in invocation-scoped ``agent_context`` metadata, so they reset
naturally when the next ``Runner.run_async`` starts and are never persisted.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING
from typing import Any
from typing import Callable
from typing import List
from typing import Literal
from typing import Optional

from pydantic import BaseModel

from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.log import logger
from trpc_agent_sdk.models import LlmRequest
from trpc_agent_sdk.models import LlmResponse
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part

from ._goal_toolset import GoalToolSet
from ._helpers import DEFAULT_STATE_KEY_PREFIX
from ._helpers import decode_goal
from ._helpers import state_key
from ._models import GoalRecord
from ._models import GoalStatus
from ._prompt import DEFAULT_GUIDANCE
from ._prompt import DEFAULT_NUDGE
from ._prompt import _GUIDANCE_MARKER

if TYPE_CHECKING:
    from trpc_agent_sdk.agents import LlmAgent

# Invocation-scoped metadata keys (stored in ``agent_context.metadata``).
_RETRY_COUNT_KEY = "__goal_enforce_retry_count"
_REMINDER_PENDING_KEY = "__goal_enforce_reminder_pending"
# Tracks whether *we* flipped the loop's running flag, so we only ever reset
# what we set (never clobbering another feature's re-run request).
_RERUN_ARMED_KEY = "__goal_enforce_rerun_armed"

OnRetry = Callable[["RetryEvent"], None]


class RetryEvent(BaseModel):
    """Observability payload emitted on every interception / budget exhaustion."""

    reason: Literal["blocked", "exhausted"]
    agent_name: str
    goal: GoalRecord
    attempt_number: int
    max_retries: int


@dataclass
class GoalOptions:
    """Configuration for the goal capability.

    Attributes:
        state_key_prefix: Session state-key prefix (``goal`` by default). Avoid
            ``temp:`` — that prefix is invocation-only and is never persisted.
        inject_guidance: Inject :data:`DEFAULT_GUIDANCE` into the system
            instruction once per request while a goal is active.
        guidance: The guidance text to inject.
        max_retries: Interception budget. Once this many premature finals have
            been intercepted in one invocation, the next one is let through
            (fail-open) so the loop never spins forever.
        nudge_template: User-role reminder template; receives ``attempt``,
            ``max_retries`` and ``objective``.
        on_retry: Optional observability callback. Invoked with try/except so
            a faulty callback never breaks the main flow.
    """

    state_key_prefix: str = DEFAULT_STATE_KEY_PREFIX
    inject_guidance: bool = True
    guidance: str = DEFAULT_GUIDANCE
    max_retries: int = 3
    nudge_template: str = DEFAULT_NUDGE
    on_retry: Optional[OnRetry] = None

    def toolset(self) -> GoalToolSet:
        """Build a :class:`GoalToolSet` matching these options."""
        return GoalToolSet(state_key_prefix=self.state_key_prefix)


class _GoalCallbacks:
    """A pair of model callbacks implementing goal enforcement."""

    def __init__(self, opts: GoalOptions) -> None:
        self._opts = opts

    # -- helpers -------------------------------------------------------------
    def _resolve_branch(self, ctx: InvocationContext) -> str:
        return ctx.branch or ctx.agent_name or ""

    def _state_key(self, ctx: InvocationContext) -> str:
        return state_key(self._opts.state_key_prefix, self._resolve_branch(ctx))

    def _load_goal(self, ctx: InvocationContext) -> Optional[GoalRecord]:
        return decode_goal(ctx.state.get(self._state_key(ctx)))

    def _emit(self, ctx: InvocationContext, goal: GoalRecord, reason: str, attempt: int) -> None:
        callback = self._opts.on_retry
        if callback is None:
            return
        try:
            callback(
                RetryEvent(
                    reason=reason,  # type: ignore[arg-type]
                    agent_name=ctx.agent_name,
                    goal=goal,
                    attempt_number=attempt,
                    max_retries=self._opts.max_retries,
                ))
        except Exception as ex:  # pylint: disable=broad-except
            logger.warning("goal on_retry callback raised, ignoring: %s", ex)

    @staticmethod
    def _is_premature_final(response: Optional[LlmResponse]) -> bool:
        """Whether ``response`` is a *final* chunk that prematurely ends the turn.

        A final chunk = not partial, no error, carries visible (non-thought)
        text, and is NOT a tool-call / tool-response. Intermediate partial
        chunks and tool calls always pass through untouched.
        """
        if response is None or response.partial or response.error_code:
            return False
        content = response.content
        if content is None or not content.parts:
            return False
        has_text = False
        for part in content.parts:
            if part.function_call or part.function_response:
                return False
            if getattr(part, "code_execution_result", None) or getattr(part, "executable_code", None):
                return False
            if part.text and not getattr(part, "thought", False):
                has_text = True
        return has_text

    # -- callbacks -----------------------------------------------------------
    async def before_model(self, ctx: InvocationContext, request: LlmRequest) -> Optional[LlmResponse]:
        """Inject guidance (once) and a pending nudge; never short-circuits."""
        meta = ctx.agent_context.metadata
        # Consume any re-run we armed last turn: reset the loop's running flag
        # to its default (False) so the loop only continues if THIS turn arms it
        # again (or produces a real tool call). We only touch the flag we set.
        if meta.get(_RERUN_ARMED_KEY):
            from trpc_agent_sdk.agents._constants import TRPC_AGENT_RUNNING_KEY
            ctx.agent_context.with_metadata(TRPC_AGENT_RUNNING_KEY, False)
            meta[_RERUN_ARMED_KEY] = False

        if self._opts.inject_guidance:
            existing = ""
            if request.config and request.config.system_instruction:
                existing = str(request.config.system_instruction)
            if _GUIDANCE_MARKER not in existing:
                request.append_instructions([self._opts.guidance])

        goal = self._load_goal(ctx)
        if goal is None or goal.status != GoalStatus.ACTIVE:
            return None

        if meta.get(_REMINDER_PENDING_KEY):
            attempt = int(meta.get(_RETRY_COUNT_KEY, 0))
            nudge = self._opts.nudge_template.format(
                attempt=attempt,
                max_retries=self._opts.max_retries,
                objective=goal.objective,
            )
            request.contents.append(Content(role="user", parts=[Part.from_text(text=nudge)]))
            meta[_REMINDER_PENDING_KEY] = False
        return None

    async def after_model(self, ctx: InvocationContext, response: Any) -> Optional[LlmResponse]:
        """Intercept a premature final response and request a same-invocation re-run."""
        if not isinstance(response, LlmResponse):
            return None
        if not self._is_premature_final(response):
            return None

        goal = self._load_goal(ctx)
        if goal is None or goal.status != GoalStatus.ACTIVE:
            return None

        meta = ctx.agent_context.metadata
        retry = int(meta.get(_RETRY_COUNT_KEY, 0))

        if retry >= self._opts.max_retries:
            # Budget exhausted: fail-open. Let the final response through and
            # reset counters so the loop ends naturally.
            self._emit(ctx, goal, "exhausted", retry)
            meta[_RETRY_COUNT_KEY] = 0
            meta[_REMINDER_PENDING_KEY] = False
            return None

        retry += 1
        meta[_RETRY_COUNT_KEY] = retry
        meta[_REMINDER_PENDING_KEY] = True
        meta[_RERUN_ARMED_KEY] = True
        self._emit(ctx, goal, "blocked", retry)

        # The only legal lever for a same-invocation re-run: flip the loop's
        # running flag back to True (read at the tail of LlmAgent's while loop).
        # ``before_model`` resets it next turn (see _RERUN_ARMED_KEY) so the loop
        # ends naturally once the model stops giving premature finals.
        from trpc_agent_sdk.agents._constants import TRPC_AGENT_RUNNING_KEY
        ctx.agent_context.with_metadata(TRPC_AGENT_RUNNING_KEY, True)

        # Replace the premature final with a content-less, partial control
        # response so the finalisation text is not committed as the answer.
        return LlmResponse(content=None, partial=True, custom_metadata={"goal_enforced": True})


def _chain_callbacks(existing: Any, new: Callable) -> List[Callable]:
    """Append ``new`` after any existing callback(s), preserving order."""
    if existing is None:
        return [new]
    if isinstance(existing, list):
        return [*existing, new]
    return [existing, new]


def setup_goal(agent: "LlmAgent", opts: Optional[GoalOptions] = None) -> "LlmAgent":
    """Mount the goal capability on ``agent`` in one call.

    Appends the :class:`GoalToolSet` to ``agent.tools`` and chains the
    enforcement callbacks onto ``before_model_callback`` / ``after_model_callback``
    (B1: tools and callbacks are registered separately).

    Returns the same ``agent`` for chaining.
    """
    opts = opts or GoalOptions()
    callbacks = _GoalCallbacks(opts)
    agent.tools.append(opts.toolset())
    agent.before_model_callback = _chain_callbacks(agent.before_model_callback, callbacks.before_model)
    agent.after_model_callback = _chain_callbacks(agent.after_model_callback, callbacks.after_model)
    return agent
