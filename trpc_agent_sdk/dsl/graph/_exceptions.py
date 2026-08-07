# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Graph runtime exceptions."""

from __future__ import annotations


class GraphResumeError(RuntimeError):
    """Raised when a paused graph receives an invalid resume input.

    A pending interrupt is an exclusive execution state: the next invocation
    must answer that interrupt. Falling back to a fresh graph input can replay
    nodes with external side effects, so callers receive a stable, typed error
    instead.
    """

    error_code = "graph_resume_failed"

    def __init__(
        self,
        reason: str,
        *,
        pending_interrupt_id: str | None = None,
        response_id: str | None = None,
    ) -> None:
        self.reason = reason
        self.pending_interrupt_id = pending_interrupt_id
        self.response_id = response_id
        super().__init__(self._message())

    def _message(self) -> str:
        if self.reason == "missing_function_response":
            return "Graph is waiting for an interrupt response, but the current input is not a FunctionResponse."
        if self.reason == "interrupt_id_mismatch":
            return ("Graph interrupt response ID does not match the pending interrupt "
                    f"(pending={self.pending_interrupt_id!r}, response={self.response_id!r}).")
        if self.reason == "invalid_interrupt_id":
            return f"Graph interrupt response ID is invalid: {self.response_id!r}."
        return f"Graph resume failed: {self.reason}."

    def get_custom_metadata(self) -> dict[str, str]:
        """Return protocol-safe diagnostics for event translators."""
        metadata = {"reason": self.reason}
        if self.pending_interrupt_id:
            metadata["pending_interrupt_id"] = self.pending_interrupt_id
        if self.response_id:
            metadata["response_id"] = self.response_id
        return metadata
