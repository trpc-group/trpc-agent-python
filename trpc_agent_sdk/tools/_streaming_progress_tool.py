# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Streaming-progress function tool.

A `StreamingProgressTool` lets a long-running tool push intermediate progress
events to the user **while** it is still executing, in the same way that the
LLM streams text. This is **different** from the two existing streaming-ish
tools shipped in this package:

| Class                       | What gets streamed                                  |
| --------------------------- | --------------------------------------------------- |
| ``StreamingFunctionTool``   | The *arguments* the LLM is generating for the call. |
| ``LongRunningFunctionTool`` | Just marks the call as long-running; one final     |
|                             | result, no intermediate events.                     |
| ``StreamingProgressTool``   | The tool's *own* execution progress (this file).    |

Usage
-----

The wrapped function must be an ``async def`` generator (i.e. uses ``yield``).
Each yielded value becomes a partial ``Event`` surfaced to the caller in real
time. The **last** yielded value is *also* used as the final
``function_response`` returned to the LLM:

.. code-block:: python

    import asyncio
    from typing import AsyncIterator

    from trpc_agent_sdk.tools import StreamingProgressTool


    async def crawl_site(url: str) -> AsyncIterator[dict]:
        '''Crawl a website and report progress.'''
        yield {"status": "started", "url": url}
        total = 5
        for i in range(total):
            await asyncio.sleep(1)
            yield {"status": "fetching", "page": i + 1, "total": total}
        # Last yield = both the final progress event AND the function_response
        # that is fed back to the LLM.
        yield {"status": "done", "url": url, "pages": total}


    tool = StreamingProgressTool(crawl_site)
    agent = LlmAgent(name="crawler", model=model, tools=[tool])

Consuming the partial progress events from the runner side looks like:

.. code-block:: python

    async for event in runner.run_async(...):
        if event.partial and event.custom_metadata.get("tool_progress"):
            print("[progress]", event.custom_metadata["tool_name"],
                  event.get_text() or event.custom_metadata.get("payload"))

The yielded value can be:

- ``dict``: surfaced verbatim under ``event.custom_metadata['payload']`` and
  also serialised as JSON text on a ``Part`` so plain text consumers see it.
- ``str``:  surfaced as a regular text ``Part``.
- ``BaseModel``: ``.model_dump()`` is used to coerce to ``dict``.

Constraints
-----------

- ``parallel_tool_calls=True`` is *not* recommended together with progress
  streaming. The framework will fall back to sequential execution when at
  least one progress-streaming tool is invoked in a batch, otherwise
  intermediate events from concurrent tools would interleave unpredictably.
- The wrapped function MUST be an async generator. A regular ``async def``
  that returns a value will raise ``TypeError`` at construction time, with a
  hint to use ``LongRunningFunctionTool`` or ``FunctionTool`` instead.
"""

from __future__ import annotations

import inspect
from typing import Any
from typing import AsyncIterator
from typing import Callable
from typing import Dict
from typing import Optional
from typing_extensions import override

from pydantic import BaseModel

from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.filter import BaseFilter

from ._constants import TOOL_CONTEXT
from ._function_tool import FunctionTool
from .utils import convert_pydantic_args
from .utils import get_mandatory_args


class StreamingProgressTool(FunctionTool):
    """A function tool that yields intermediate progress events.

    See module docstring for usage and rationale.
    """

    def __init__(
        self,
        func: Callable[..., AsyncIterator[Any]],
        filters_name: Optional[list[str]] = None,
        filters: Optional[list[BaseFilter]] = None,
        *,
        skip_summarization: bool = False,
    ):
        """Wrap ``func`` (an async generator) into a streaming-progress tool.

        Args:
            func: The wrapped ``async def`` generator function. Each ``yield``
                becomes one streaming event; the last yield is also used as
                the final ``function_response``.
            filters_name: Optional filter names (forwarded to FunctionTool).
            filters: Optional filter instances (forwarded to FunctionTool).
            skip_summarization: If True, the framework treats the tool's
                streamed output as the **final** user-facing answer and
                stops the agent loop after this tool finishes; no LLM
                follow-up call is made. Use this when the user has already
                consumed the streaming output and an LLM summary would just
                be redundant. Implemented by setting
                ``event.actions.skip_summarization=True`` on the final
                ``function_response`` event, which
                :meth:`LlmAgent._run_async_impl` checks to terminate the
                conversation loop early.
        """
        if not inspect.isasyncgenfunction(func):
            raise TypeError("StreamingProgressTool requires an `async def` *generator* function "
                            f"(one that uses `yield`). Got: {type(func).__name__}. "
                            "If your tool only returns a single result, use `FunctionTool` (fast) "
                            "or `LongRunningFunctionTool` (for long but non-streaming work) instead.")
        super().__init__(func, filters_name=filters_name, filters=filters)
        self._skip_summarization = bool(skip_summarization)

    @property
    def is_progress_streaming(self) -> bool:
        """Marks this tool as one that yields progress events during execution."""
        return True

    @property
    def skip_summarization(self) -> bool:
        """Whether the final tool event should set ``skip_summarization=True``.

        When True the LlmAgent loop exits after this tool returns, without
        calling the LLM to summarize the streamed output.
        """
        return self._skip_summarization

    @override
    async def _run_async_impl(self, *, tool_context: InvocationContext, args: Dict[str, Any]) -> Any:
        """Refuse direct invocation.

        Progress-streaming tools must be driven through
        ``ToolsProcessor`` (which calls :meth:`run_streaming`). Allowing
        ``_run_async_impl`` to silently drain the generator would violate
        single-responsibility: this class would have two ways to be executed
        with subtly different semantics (no partial events surfaced, no
        ``function_response`` event built, callers thinking they got a
        "normal" tool result).

        If you need a single-shot tool, wrap the function with
        :class:`FunctionTool` or :class:`LongRunningFunctionTool` instead.
        """
        raise RuntimeError(f"{type(self).__name__} (`{self.name}`) does not support direct "
                           "invocation via `run_async` / `_run_async_impl`. It must be "
                           "executed through `ToolsProcessor.execute_tools_async`, which "
                           "drives it via `run_streaming` and surfaces interim progress "
                           "events. If you only need a one-shot result, use `FunctionTool` "
                           "or `LongRunningFunctionTool` instead.")

    async def run_streaming(
        self,
        *,
        tool_context: InvocationContext,
        args: Dict[str, Any],
    ) -> AsyncIterator[Any]:
        """Yield progress values produced by the wrapped async generator.

        The framework wraps each yielded value into a partial ``Event`` and
        surfaces it through ``Runner.run_async``. The *last* yielded value is
        additionally used as the final ``function_response`` part fed back to
        the LLM.

        Mandatory-argument validation, ``tool_context`` injection and
        pydantic-arg coercion mirror :class:`FunctionTool`.
        """
        args_to_call = args.copy()
        signature = inspect.signature(self.func)

        if TOOL_CONTEXT in signature.parameters:
            args_to_call[TOOL_CONTEXT] = tool_context

        args_to_call = convert_pydantic_args(args_to_call, signature)

        mandatory_args = get_mandatory_args(self.func)
        missing = [arg for arg in mandatory_args if arg not in args_to_call]
        if missing:
            yield {
                "error": (f"Invoking `{self.name}()` failed: missing mandatory input parameters: "
                          f"{', '.join(missing)}. Please call again with all required arguments.")
            }
            return

        async for value in self.func(**args_to_call):
            if isinstance(value, BaseModel):
                value = value.model_dump()
            yield value
