"""SafeCodeExecutor — Wrapper adapter for BaseCodeExecutor with safety pre-check.

This adapter wraps any BaseCodeExecutor to add safety scanning before code execution.
It intercepts execute_code() calls, scans each code block, and blocks execution
if any block is denied.

Usage:
    from trpc_agent_sdk.tools.safety.adapters import SafeCodeExecutor
    from trpc_agent_sdk.code_executors.local import UnsafeLocalCodeExecutor

    safe_executor = SafeCodeExecutor(inner=UnsafeLocalCodeExecutor())
    agent = LlmAgent(code_executor=safe_executor)
"""

from __future__ import annotations

import logging
from typing import Optional

from pydantic import Field

from trpc_agent_sdk.code_executors._base_code_executor import BaseCodeExecutor
from trpc_agent_sdk.code_executors._types import (
    CodeExecutionInput,
    create_code_execution_result,
)
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.tools.safety.guard import ScriptSafetyGuard
from trpc_agent_sdk.tools.safety.models import (
    Decision,
    Language,
    SafetyCheckInput,
    SafetyCheckResult,
    ToolMetadata,
)
from trpc_agent_sdk.tools.safety.policy import PolicyConfig
from trpc_agent_sdk.types import CodeExecutionResult

logger = logging.getLogger(__name__)


class SafeCodeExecutor(BaseCodeExecutor):
    """Code executor wrapper that adds safety scanning before execution.

    Wraps an inner BaseCodeExecutor and intercepts execute_code():
    1. Iterates over all code_blocks in the input
    2. Runs guard.check() on each block
    3. If ANY block is DENY → returns error CodeExecutionResult (no execution)
    4. If all blocks pass → delegates to inner.execute_code()

    Attributes:
        inner: The wrapped code executor that performs actual execution.
        policy: Optional policy config for the safety guard.
        block_on_review: If True, NEEDS_HUMAN_REVIEW also blocks execution.
    """

    inner: BaseCodeExecutor = Field(description="The wrapped code executor.")
    policy: Optional[PolicyConfig] = Field(
        default=None,
        description="Policy config. If None, built-in defaults are used.",
    )
    block_on_review: bool = Field(
        default=False,
        description="If True, NEEDS_HUMAN_REVIEW decisions also block execution.",
    )

    # Internal guard instance (excluded from serialization)
    _guard: Optional[ScriptSafetyGuard] = None

    model_config = {"arbitrary_types_allowed": True}

    def model_post_init(self, __context) -> None:
        """Initialize the guard after Pydantic model construction."""
        self._guard = ScriptSafetyGuard(policy=self.policy)

    @property
    def guard(self) -> ScriptSafetyGuard:
        """Access the underlying guard instance."""
        if self._guard is None:
            self._guard = ScriptSafetyGuard(policy=self.policy)
        return self._guard

    async def execute_code(
        self,
        invocation_context: InvocationContext,
        code_execution_input: CodeExecutionInput,
    ) -> CodeExecutionResult:
        """Execute code with pre-execution safety scanning.

        Args:
            invocation_context: The invocation context.
            code_execution_input: The code execution input with code blocks.

        Returns:
            CodeExecutionResult — either an error (if blocked) or the inner executor's result.
        """
        # Collect all code blocks to check
        blocks_to_check = code_execution_input.code_blocks

        # Also check the top-level code field if present
        if code_execution_input.code and not blocks_to_check:
            # Treat bare code as a Python block by default
            from trpc_agent_sdk.code_executors._types import CodeBlock

            blocks_to_check = [CodeBlock(language="python", code=code_execution_input.code)]

        # Build tool metadata from invocation context
        tool_metadata = _build_metadata_from_context(invocation_context)

        # Check each block
        blocked_results: list[SafetyCheckResult] = []

        for block in blocks_to_check:
            if not block.code.strip():
                continue

            language = _normalize_language(block.language)
            check_input = SafetyCheckInput(
                script_content=block.code,
                language=language,
                tool_metadata=tool_metadata,
            )

            try:
                result = self.guard.check(check_input)
            except Exception as e:
                # Guard should never raise, but fail-open if it does
                logger.error(
                    "SafeCodeExecutor: guard.check() raised: %s", e, exc_info=True
                )
                continue

            if result.decision == Decision.DENY:
                blocked_results.append(result)
            elif result.decision == Decision.NEEDS_HUMAN_REVIEW and self.block_on_review:
                blocked_results.append(result)

        # If any block was blocked, return error without executing
        if blocked_results:
            return _make_blocked_result(blocked_results)

        # All blocks passed — delegate to inner executor
        return await self.inner.execute_code(invocation_context, code_execution_input)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _normalize_language(lang_str: str) -> Language:
    """Normalize a language string to Language enum."""
    lang_lower = lang_str.lower().strip()
    if lang_lower in ("bash", "sh", "shell", "zsh"):
        return Language.BASH
    return Language.PYTHON


def _build_metadata_from_context(ctx: InvocationContext) -> ToolMetadata:
    """Extract tool metadata from InvocationContext."""
    tool_name = "code_executor"
    invocation_id = ""
    agent_name = ""
    user_id = ""

    if hasattr(ctx, "invocation_id") and ctx.invocation_id:
        invocation_id = str(ctx.invocation_id)
    if hasattr(ctx, "agent_name") and ctx.agent_name:
        agent_name = str(ctx.agent_name)
    if hasattr(ctx, "user_id") and ctx.user_id:
        user_id = str(ctx.user_id)

    return ToolMetadata(
        tool_name=tool_name,
        invocation_id=invocation_id,
        agent_name=agent_name,
        user_id=user_id,
    )


def _make_blocked_result(blocked_results: list[SafetyCheckResult]) -> CodeExecutionResult:
    """Create an error CodeExecutionResult when safety check blocks execution."""
    all_findings = []
    for r in blocked_results:
        all_findings.extend(r.findings)

    # Build a human-readable error message
    findings_summary = "\n".join(
        f"  - [{f.rule_id}] {f.severity.value.upper()}: {f.description}"
        for f in all_findings[:5]  # Show at most 5 findings
    )
    if len(all_findings) > 5:
        findings_summary += f"\n  ... and {len(all_findings) - 5} more findings"

    error_msg = (
        f"Script Safety Guard blocked code execution.\n"
        f"Decision: DENY\n"
        f"Findings ({len(all_findings)}):\n{findings_summary}"
    )

    return create_code_execution_result(stderr=error_msg)
