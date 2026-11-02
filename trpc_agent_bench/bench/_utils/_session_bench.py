"""Shared turn and loop implementation for session benchmarks."""

from __future__ import annotations

import asyncio

from ._common import concurrency_label
from ._common import new_session_id
from ._common import result_name
from ...core import BenchSetup
from ...core import PerformanceEval
from ...core import ToolsFactory
from ...core import drain
from ...core import ensure_turn_completed
from ...core import get_concurrency
from ...core import get_iterations
from ...core import run_benchmarks
from ...core import setup
from ...core import teardown
from ...core import FunctionBlock
from ...core import TextBlock
from ...core import Turn


def _session_user_id(base_user_id: str, index: int) -> str:
    return f"{base_user_id}_{index}"


def _expected_text(turn: Turn) -> str | None:
    for reply in reversed(turn):
        if isinstance(reply, TextBlock):
            return reply.text
    return None


def _has_function(turn: Turn) -> bool:
    return any(isinstance(reply, list) and any(isinstance(block, FunctionBlock) for block in reply) for reply in turn)


def _make_conversation(
    *,
    handle,
    app_name: str,
    base_user_id: str,
    script: list[Turn],
    current: int,
):

    async def one_session(index: int) -> None:
        user_id = _session_user_id(base_user_id, index)
        session_id = new_session_id()
        for turn in script:
            events = await drain(
                handle.run_turn(
                    app_name=app_name,
                    user_id=user_id,
                    session_id=session_id,
                    message="Say ok.",
                ), )
            ensure_turn_completed(
                events,
                expected_text=_expected_text(turn),
                expect_tool_success=_has_function(turn),
            )

    async def arun_conversation() -> None:
        await asyncio.gather(*(one_session(index) for index in range(current)))

    return arun_conversation


async def _run_turns(
    script: list[Turn],
    *,
    name: str,
    description: str,
    tools_factory: ToolsFactory | None,
) -> None:
    current = get_concurrency()
    box: BenchSetup = await setup(script)
    handle = await box.target.handle(
        box.prepared,
        tools_factory=tools_factory or (lambda: []),
    )
    try:
        await run_benchmarks([
            PerformanceEval(
                name=result_name(name),
                display_name=name,
                label=concurrency_label(),
                func=_make_conversation(
                    handle=handle,
                    app_name=box.conf.app_name,
                    base_user_id=box.conf.user_id,
                    script=script,
                    current=current,
                ),
                num_iterations=get_iterations(),
                warmup_runs=1,
                description=description,
            ),
        ], )
    finally:
        await teardown(box)


def run_turns(
    script: list[Turn],
    *,
    name: str,
    description: str,
    tools_factory: ToolsFactory | None = None,
) -> int:
    if not script:
        raise ValueError("script must contain at least one turn")
    asyncio.run(_run_turns(script, name=name, description=description, tools_factory=tools_factory))
    return 0
