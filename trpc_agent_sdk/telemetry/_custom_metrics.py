# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Custom metrics reporter for ecosystem agents and user-defined custom agents.

Parallel to :mod:`trpc_agent_sdk.telemetry._custom_trace`. Custom agents that do
not go through the standard ``_llm_processor`` / ``_tools_processor`` paths use
this reporter to emit OTel ``gen_ai.*`` metrics for each LLM response and each
tool invocation by pairing ``function_call`` events with their matching
``function_response`` events.

``invoke_agent`` metrics are already emitted by ``BaseAgent.run_async`` for
every agent subclass, so this reporter intentionally covers only ``chat`` and
``execute_tool``.

Example usage:
    ```python
    from trpc_agent_sdk.telemetry import CustomMetricsReporter, CustomTraceReporter

    class MyCustomAgent(BaseAgent):
        async def _run_async_impl(self, ctx):
            trace_reporter = CustomTraceReporter(
                agent_name=self.name, model_prefix="custom")
            metrics_reporter = CustomMetricsReporter(
                agent_name=self.name, model_prefix="custom")

            async for event in self._stream():
                trace_reporter.trace_event(ctx, event)
                metrics_reporter.report_event(ctx, event)
                yield event
    ```
"""

from __future__ import annotations

import time
from typing import Any
from typing import Mapping
from typing import Optional

from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.events import Event
from trpc_agent_sdk.models import LlmRequest
from trpc_agent_sdk.models import LlmResponse

from ._custom_trace import _SyntheticTool
from ._metrics import report_call_llm
from ._metrics import report_execute_tool


class CustomMetricsReporter:
    """Reusable metrics reporter for custom agent implementations.

    Mirrors :class:`CustomTraceReporter`: stream every event through
    :meth:`report_event` and the reporter will emit ``call_llm`` metrics for
    each complete LLM response event and ``execute_tool`` metrics for each
    ``function_call`` / ``function_response`` pair. ``invoke_agent`` metrics
    are emitted by ``BaseAgent.run_async`` and are not handled here.

    Timing model:
        - ``call_llm.duration`` is measured from the end of the previous
          segment (agent start, or the last ``function_response``) to the
          arrival of the current non-partial LLM event.
        - ``call_llm.time_to_first_token`` is measured from the segment start
          to the first event carrying user-visible content.
        - ``execute_tool.duration`` is measured from a ``function_call`` event
          to its matching ``function_response`` event.

    Attributes:
        agent_name: The name of the agent using this reporter.
        model_prefix: Prefix for the model name in metrics (e.g., "claude"). The
            emitted ``gen_ai.request.model`` will be ``{model_prefix}:{agent_name}``.
        is_stream: Whether the underlying agent streams its responses. Affects
            the ``gen_ai.is_stream`` attribute on emitted records.
        extra_attributes: Optional constant attributes merged onto every record.
    """

    def __init__(
        self,
        agent_name: str,
        model_prefix: str = "custom",
        *,
        is_stream: bool = True,
        extra_attributes: Optional[Mapping[str, Any]] = None,
    ):
        """Initialize the CustomMetricsReporter.

        Args:
            agent_name: The name of the agent using this reporter.
            model_prefix: Prefix for the model name in emitted metrics
                (default: ``"custom"``). The full model name will be
                ``"{model_prefix}:{agent_name}"``.
            is_stream: Whether the agent streams its output (default: ``True``).
            extra_attributes: Optional extra attributes merged onto every
                emitted record. ``None`` values are ignored.
        """
        self.agent_name = agent_name
        self.model_prefix = model_prefix
        self.is_stream = is_stream
        self.extra_attributes = dict(extra_attributes) if extra_attributes else None

        self._pending_tool_starts: dict[str, tuple[str, float]] = {}
        self._llm_segment_start: Optional[float] = None
        self._llm_ttft: Optional[float] = None

    def report_event(self, ctx: InvocationContext, event: Event) -> None:
        """Process metrics for a single event.

        Routes the event to one of three handlers:
          * ``function_call`` events start tool timers and close the current
            LLM segment (emitting ``call_llm``).
          * ``function_response`` events close tool timers (emitting
            ``execute_tool``) and start a new LLM segment.
          * Any other non-partial event carrying user-visible content is
            treated as a complete LLM response (emitting ``call_llm``).

        Partial events only update the TTFT measurement for the current LLM
        segment; they do not trigger any emission.

        Args:
            ctx: Invocation context.
            event: The event to process.
        """
        now = time.monotonic()
        if self._llm_segment_start is None:
            self._llm_segment_start = now
        if self._llm_ttft is None and event.has_content():
            self._llm_ttft = now - self._llm_segment_start

        if event.partial:
            return

        if event.get_function_calls():
            self._emit_call_llm(ctx, event, now)
            for fc in event.get_function_calls():
                self._pending_tool_starts[fc.id] = (fc.name, now)
            self._llm_segment_start = None
            self._llm_ttft = None
            return

        if event.get_function_responses():
            for fr in event.get_function_responses():
                state = self._pending_tool_starts.pop(fr.id, None)
                if state is None:
                    continue
                tool_name, t0 = state
                report_execute_tool(
                    ctx,
                    _SyntheticTool(name=tool_name),
                    duration_s=now - t0,
                    error_type=self._tool_error_type(event),
                    extra_attributes=self.extra_attributes,
                )
            self._llm_segment_start = now
            self._llm_ttft = None
            return

        if event.has_content():
            self._emit_call_llm(ctx, event, now)
            self._llm_segment_start = now
            self._llm_ttft = None

    def reset(self) -> None:
        """Reset reporter state.

        Clears pending tool-call timers and LLM-segment bookkeeping. Call
        between separate runs of the same reporter instance.
        """
        self._pending_tool_starts.clear()
        self._llm_segment_start = None
        self._llm_ttft = None

    def _emit_call_llm(
        self,
        ctx: InvocationContext,
        event: Event,
        now: float,
    ) -> None:
        if self._llm_segment_start is None:
            return
        duration = now - self._llm_segment_start
        ttft = self._llm_ttft if self._llm_ttft is not None else duration

        llm_request = LlmRequest(model=f"{self.model_prefix}:{self.agent_name}")
        llm_response = LlmResponse(
            content=event.content,
            usage_metadata=getattr(event, "usage_metadata", None),
            error_code=getattr(event, "error_code", None),
            error_message=getattr(event, "error_message", None),
        )
        error_type: Optional[str] = None
        if getattr(event, "error_code", None):
            error_type = str(event.error_code)
        report_call_llm(
            ctx,
            llm_request=llm_request,
            llm_response=llm_response,
            duration_s=duration,
            ttft_s=ttft,
            is_stream=self.is_stream,
            error_type=error_type,
            extra_attributes=self.extra_attributes,
        )

    @staticmethod
    def _tool_error_type(event: Event) -> Optional[str]:
        """Best-effort error-type extraction from a function-response event."""
        err_code = getattr(event, "error_code", None)
        if err_code:
            return str(err_code)
        if getattr(event, "error_message", None):
            return "error"
        return None
