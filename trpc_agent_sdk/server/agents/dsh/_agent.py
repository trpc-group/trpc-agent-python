# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""DSH-backed tRPC agent."""

from __future__ import annotations

import base64
from collections.abc import AsyncGenerator
from collections.abc import Awaitable
from collections.abc import Callable
from typing import Any

from pydantic import Field
from pydantic import PrivateAttr

from trpc_agent_sdk.agents import BaseAgent
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.events import Event
from trpc_agent_sdk.sessions import session_key
from trpc_agent_sdk.types import Content

from ._config import DSHConfig
from ._convert import to_trpc_event
from ._protocol import DSHRunInput
from ._runtime import DSHRuntime
from ._sdk import JsonObject
from ._state import DSHState

_IMAGE_MIME_TYPES = frozenset({
    "image/png",
    "image/jpeg",
    "image/webp",
    "image/gif",
})
_PART_NAME_KEYS = ("name", "file_name", "filename")


def _part_name(part: Any) -> str | None:
    metadata = getattr(part, "part_metadata", None)
    if not isinstance(metadata, dict):
        return None
    for key in _PART_NAME_KEYS:
        value = metadata.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _default_content_blocks(
    content: Content | None,
    *,
    allow_unconverted: bool,
) -> list[JsonObject]:
    """Convert text and inline bytes into DSH content-block JSON.

    Image/file bytes are sent as encoded blocks; the JS plugin admits them
    into durable attachment references before creating the user message.
    ``Part.file_data`` is a URI rather than bytes, so it is left to
    ``custom_dsh_input`` (or rejected when no callback is configured).
    """

    blocks: list[JsonObject] = []
    has_unconverted = False
    if content is not None and content.parts:
        for part in content.parts:
            text = getattr(part, "text", None)
            if isinstance(text, str) and text:
                blocks.append({"type": "text", "text": text})
                continue

            inline = getattr(part, "inline_data", None)
            if inline is not None:
                data = getattr(inline, "data", None)
                if not isinstance(data, (bytes, bytearray)):
                    raise ValueError("DSH inline_data requires bytes")
                if not data:
                    raise ValueError("DSH inline_data must not be empty")

                mime_type = getattr(inline, "mime_type", None)
                mime = mime_type.strip().lower() if isinstance(mime_type, str) else ""
                encoded = base64.b64encode(bytes(data)).decode("ascii")
                name = _part_name(part)

                if mime in _IMAGE_MIME_TYPES:
                    block: JsonObject = {
                        "type": "image",
                        "data": encoded,
                        "mimeType": mime,
                    }
                else:
                    block = {"type": "file", "data": encoded}
                if name is not None:
                    block["name"] = name
                blocks.append(block)
                continue

            if getattr(part, "file_data", None) is not None:
                has_unconverted = True

    if has_unconverted and not allow_unconverted:
        raise ValueError(
            "DSH file_data is a URI and is not read automatically; pass "
            "custom_dsh_input and set DSHRunInput.content_blocks")
    return blocks


def _validate_content_blocks(blocks: list[JsonObject]) -> None:
    if not blocks:
        raise ValueError("DSHRunInput.content_blocks must not be empty")

    for block in blocks:
        if not isinstance(block, dict):
            raise TypeError("DSHRunInput.content_blocks must contain JSON objects")
        block_type = block.get("type")
        if not isinstance(block_type, str) or not block_type:
            raise ValueError("DSH content block requires a non-empty string type")

        if block_type == "text":
            text = block.get("text")
            if not isinstance(text, str) or not text:
                raise ValueError("DSH text block requires a non-empty text field")
            continue

        if block_type not in ("image", "file"):
            # DSH content blocks are merge-extensible; let the runtime validate
            # plugin-defined types.
            continue

        has_attachment = isinstance(block.get("attachment"), dict)
        data = block.get("data")
        has_data = isinstance(data, str) and bool(data)
        if has_attachment == has_data:
            raise ValueError(
                f"DSH {block_type} block requires exactly one of attachment or data")
        if block_type == "image" and has_data:
            mime_type = block.get("mimeType")
            if not isinstance(mime_type, str) or not mime_type:
                raise ValueError("encoded DSH image block requires mimeType")


class DSHAgent(BaseAgent):
    """Run tRPC invocations through a DeepSeek Harness runtime.

    One ``DSHAgent`` owns one DSH subprocess. Call :meth:`init` once before the
    first run and :meth:`destroy` when the process is no longer needed. The
    model is configured by DSH, not by the tRPC framework; see
    :class:`~trpc_agent_sdk.server.agents.dsh.DSHConfig`.
    """

    dsh_conf: DSHConfig = Field(repr=False, exclude=True)
    """DSH launch, workspace, and model configuration.

    Model selection lives in ``dsh_conf.model``. ``DSHAgent`` never calls the
    model directly; it only forwards the selection to DSH.

    Example:
        >>> DSHAgent(
        ...     name="dsh_agent",
        ...     dsh_conf=DSHConfig(
        ...         profile="custom",
        ...         dsh_home="/data/dsh-home",
        ...         model=DSHModel(
        ...             provider="openai",
        ...             model="deepseek-chat",
        ...         ),
        ...     ),
        ... )
    """

    custom_dsh_input: Callable[
        [InvocationContext, DSHRunInput],
        Awaitable[DSHRunInput],
    ] | None = Field(default=None, exclude=True)
    """Optional async callback invoked once per turn.

    It runs after the framework has built the default :class:`DSHRunInput`
    (including text / image / file blocks) and before ``session/trpc-prompt``
    is sent. Mutate ``run_input`` in place and return it, or return a new
    ``DSHRunInput``; the callback must return a ``DSHRunInput``. Typical uses
    are setting a per-user ``cwd``, adding a durable attachment block, or
    overriding ``run_input.model`` for one turn.

    Example:
        >>> async def custom_dsh_input(
        ...     ctx: InvocationContext,
        ...     run_input: DSHRunInput,
        ... ) -> DSHRunInput:
        ...     run_input.cwd = f"/workspace/{ctx.user_id}"
        ...     return run_input
    """

    _runtime: DSHRuntime = PrivateAttr()

    def __init__(
        self,
        name: str,
        dsh_conf: DSHConfig,
        *,
        description: str = "",
        custom_dsh_input: Callable[
            [InvocationContext, DSHRunInput],
            Awaitable[DSHRunInput],
        ] | None = None,
    ) -> None:
        """Create a DSH-backed agent.

        Args:
            name: Agent name exposed to tRPC. It is also used as the author of
                emitted tRPC events.
            dsh_conf: DSH launch, workspace, and model configuration. The
                model is configured once through ``dsh_conf.model``; this is
                not a per-invocation tRPC model object.
            description: Human-readable agent description.
            custom_dsh_input: Optional async callback invoked after the
                default ``DSHRunInput`` is built and before the prompt is sent.
        """

        super().__init__(
            name=name,
            description=description,
            dsh_conf=dsh_conf,
            custom_dsh_input=custom_dsh_input,
        )
        self._runtime = DSHRuntime(dsh_conf)

    async def init(self) -> None:
        """Start the DSH runtime and complete the initialize handshake.

        This is an application-lifecycle call, not a per-turn call. Calling it
        more than once is a no-op.
        """

        await self._runtime.init()

    async def destroy(self) -> None:
        """Shut down the DSH runtime and release its subprocess."""

        await self._runtime.destroy()

    async def _run_async_impl(
        self,
        ctx: InvocationContext,
    ) -> AsyncGenerator[Event, None]:
        state, run_input = await self._prepare_run(ctx)

        cancel_event = await ctx.get_cancel_event()
        call_names: dict[str, str] = {}
        state_event: Event | None = None
        try:
            async for frame in self._runtime.run(run_input, cancel_event=cancel_event):
                if frame.kind == "snapshot":
                    state.replace(
                        frame.data["jsonl"],
                        int(frame.data["version"]),
                    )
                    continue
                if frame.kind == "event":
                    state.append_event(frame.data)
                event = to_trpc_event(
                    frame,
                    invocation_id=ctx.invocation_id,
                    author=self.name,
                    branch=ctx.branch,
                    call_names=call_names,
                )
                if event is not None:
                    yield event
        finally:
            # Persist the JSONL snapshot even when the turn is cancelled.
            state_event = state.to_session(ctx, author=self.name)

        # The invisible event carries the state delta into the SessionService.
        if state_event is not None:
            yield state_event

        await ctx.raise_if_cancelled()

    async def _prepare_run(
        self,
        ctx: InvocationContext,
    ) -> tuple[DSHState, DSHRunInput]:
        state = DSHState.from_session(ctx.session)
        run_input = self._default_run_input(ctx, state)
        run_input = await self._apply_custom_dsh_input(ctx, run_input)
        state.sync_run_input(run_input)
        _validate_content_blocks(run_input.content_blocks)
        return state, run_input

    async def _apply_custom_dsh_input(
        self,
        ctx: InvocationContext,
        run_input: DSHRunInput,
    ) -> DSHRunInput:
        if self.custom_dsh_input is None:
            return run_input
        built = await self.custom_dsh_input(ctx, run_input)
        if not isinstance(built, DSHRunInput):
            raise TypeError("custom_dsh_input must return a DSHRunInput")
        return built

    def _default_run_input(
        self,
        ctx: InvocationContext,
        state: DSHState,
    ) -> DSHRunInput:
        model = self.dsh_conf.model
        return DSHRunInput(
            session_id=session_key(ctx.app_name, ctx.user_id, ctx.session.id),
            content_blocks=_default_content_blocks(
                ctx.user_content,
                allow_unconverted=self.custom_dsh_input is not None,
            ),
            cwd=str(self._runtime.runtime_cwd),
            jsonl=state.jsonl,
            version=state.version,
            model=model.to_spec() if model is not None else None,
            meta=dict(ctx.run_config.custom_data) if ctx.run_config else {},
        )
