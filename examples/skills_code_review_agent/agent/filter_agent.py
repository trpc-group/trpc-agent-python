"""Tool filter — three-level pre-execution governance for skill_run tool calls.

Registered as a tool filter that intercepts skill_run commands containing
dangerous patterns before they reach the sandbox executor.

Three-level semantics (see agent/filter.py for the classifier):
  deny              — hard block, never executed (rm -rf /, mkfs, fork bomb)
  ask               — require explicit human confirmation (sudo, iptables, ...)
  needs_human_review — require explicit human confirmation too (pip install,
                       curl, ...); nothing may reach the sandbox without an
                       operator decision (acceptance criterion #7)

Requires trpc-agent framework. Import only from Agent-mode code paths.
"""

import asyncio
from typing import Any, Callable, Optional

from trpc_agent_sdk.filter import BaseFilter, register_tool_filter
from trpc_agent_sdk.filter import FilterType
from trpc_agent_sdk.context import InvocationContext

from .filter import classify_command

DecisionRecorder = Callable[[str, str, str], None]
ConfirmFn = Callable[[str, str], bool]


def _default_confirm(command: str, pattern: str, level: str = 'ask') -> bool:
    """Interactive terminal confirmation. Returns False on EOF/no input."""
    try:
        answer = input(
            f'[filter:{level}] "{pattern}" detected in command: "{command}"\n'
            f'           Approve execution? [y/N]: ',
        )
    except EOFError:
        return False
    return answer.strip().lower() in ('y', 'yes')


@register_tool_filter("code_review_safety")
class CodeReviewSafetyFilter(BaseFilter):
    """Three-level pre-execution filter for skill_run tool calls.

    deny             — blocks execution immediately (is_continue=False)
    ask              — prompts the operator (interactive by default); only
                       proceeds when explicitly approved
    needs_human_review — prompts the operator as well (interactive by default);
                       only proceeds when explicitly approved. The prompt text
                       differs from ``ask``, but the decision gate is the same:
                       nothing reaches the sandbox without operator approval.

    ``confirm`` and ``record`` are injectable so tests can avoid blocking on
    stdin and can assert decisions without a live database.
    """

    def __init__(
        self,
        confirm: Optional[ConfirmFn] = None,
        record: Optional[DecisionRecorder] = None,
        interactive: bool = True,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._type = FilterType.TOOL
        self._name = "code_review_safety"
        self._confirm = confirm
        self._record = record
        self._interactive = interactive

    async def _before(
        self,
        ctx: InvocationContext,
        req: Any,
        rsp: Any = None,
    ) -> None:
        """Inspect the skill_run command and enforce the three-level policy."""
        command = self._extract_command(req)
        if not command:
            return None

        level, pattern = classify_command(command)

        if level == 'deny':
            self._record_decision('deny', pattern, command)
            if rsp is not None:
                rsp.is_continue = False
                rsp.error = Exception(f'[filter:deny] forbidden command: {pattern}')
            return None

        if level in ('ask', 'needs_human_review'):
            approved = await self._confirm_command(command, pattern, level)
            if approved:
                self._record_decision(
                    level, pattern, f'approved: {command}')
                return None
            self._record_decision(
                level, pattern, f'denied (no human confirmation): {command}')
            if rsp is not None:
                rsp.is_continue = False
                rsp.error = Exception(
                    f'[filter:{level}] {pattern} requires human confirmation')
            return None
        return None

    # -- helpers ---------------------------------------------------------

    def _record_decision(self, action: str, rule: str, reason: str) -> None:
        if self._record is not None:
            try:
                self._record(action, rule, reason)
            except Exception:  # recording must never break execution
                pass

    async def _confirm_command(self, command: str, pattern: str,
                               level: str = 'ask') -> bool:
        if self._confirm is not None:
            result = self._confirm(command, pattern)
            if asyncio.iscoroutine(result):
                return bool(await result)
            return bool(result)
        if not self._interactive:
            return False
        return await asyncio.to_thread(_default_confirm, command, pattern, level)

    @staticmethod
    def _extract_command(req: Any) -> Optional[str]:
        """Extract command string from a tool call request.

        Checks command/cmd fields plus args lists (e.g. ["rm","-rf","/"]).
        """
        parts: list[str] = []

        def _add(v: Any) -> None:
            if isinstance(v, str) and v:
                parts.append(v)
            elif isinstance(v, list):
                parts.append(' '.join(str(x) for x in v))

        if isinstance(req, dict):
            _add(req.get('command', ''))
            _add(req.get('cmd', ''))
            _add(req.get('args', ''))
            _add(req.get('argv', ''))
        # Check for function_call parts
        if hasattr(req, 'contents') and req.contents:
            for content in req.contents:
                if hasattr(content, 'parts') and content.parts:
                    for part in content.parts:
                        if hasattr(part, 'function_call') and part.function_call:
                            fc_args = part.function_call.args or {}
                            _add(fc_args.get('command', ''))
                            _add(fc_args.get('cmd', ''))
                            _add(fc_args.get('args', ''))
                            _add(fc_args.get('argv', ''))
        # Fallback: try attribute access (dict only, skip list/tuple/other)
        for attr in ('command', 'cmd', 'args'):
            val = getattr(req, attr, None)
            if val is not None and isinstance(val, dict):
                _add(val.get('command', ''))
                _add(val.get('cmd', ''))
                _add(val.get('args', ''))
        return ' '.join(parts) if parts else None
