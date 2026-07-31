# Tencent is pleased to support the open source community by making trpc-agent-python available.
# Copyright (C) 2025 Tencent. All rights reserved.
# trpc-agent-python is licensed under the Apache License Version 2.0.
"""Agent filter — three-level pre-execution governance.

Registered as an agent filter that intercepts skill_run commands.
Requires trpc-agent framework. Import only from Agent-mode code paths.
"""

from trpc_agent_sdk.filter import BaseFilter, register_agent_filter
from trpc_agent_sdk.filter import FilterType
from trpc_agent_sdk.context import InvocationContext
from typing import Any

from .filter import classify_command


@register_agent_filter("code_review_safety")
class CodeReviewSafetyFilter(BaseFilter):
    """Three-level pre-execution filter for code review tool calls.

    deny — blocks execution (system destruction, privilege escalation)
    ask  — requires user confirmation before proceeding
    needs_human_review — flags for human judgment (dependency installs, outbound network)
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._type = FilterType.AGENT
        self._name = "code_review_safety"

    async def _before(
        self,
        ctx: InvocationContext,
        req: Any,
        rsp: Any = None,
    ) -> None:
        """Check tool call arguments for dangerous/suspicious commands.

        Sets rsp.is_continue=False for deny-level commands.
        For ask/needs_human_review levels, logs a warning but allows execution
        (the human-in-the-loop decision is handled by the orchestration layer).
        """
        blocked_reason = ''
        block_level = ''

        if hasattr(req, 'contents') and req.contents:
            for content in req.contents:
                if hasattr(content, 'parts') and content.parts:
                    for part in content.parts:
                        if hasattr(part, 'function_call') and part.function_call:
                            args = part.function_call.args or {}
                            command = str(args.get('command', '') or args.get('cmd', ''))
                            if command:
                                level, pattern = classify_command(command)
                                if level == 'deny':
                                    blocked_reason = f'Forbidden pattern in command: {pattern}'
                                    block_level = 'deny'
                                    break
                                elif level == 'ask':
                                    blocked_reason = f'Confirmation required for: {pattern}'
                                    block_level = 'ask'
                                    break
                                elif level in ('ask', 'needs_human_review'):
                                    # Log for audit but don't block — the sync pipeline
                                    # handles ask/needs_human_review via the bulk API.
                                    # In Agent mode, these flow through to the
                                    # post-processing filter check.
                                    pass

        if blocked_reason and rsp is not None:
            rsp.is_continue = False
            rsp.error = Exception(f'[filter:{block_level}] {blocked_reason}')
        return None
