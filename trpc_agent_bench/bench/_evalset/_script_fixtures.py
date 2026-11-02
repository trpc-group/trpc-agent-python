"""Script builders and text fixtures shared by built-in benchmarks."""

from __future__ import annotations

from ...core import FunctionBlock
from ...core import Reply
from ...core import TextBlock
from ...core import Turn

REAL_TEXT = ("The quick brown fox jumps over the lazy dog. "
             "Pack my box with five dozen liquor jugs. "
             "How vexingly quick daft zebras jump!")


def make_turn_script(user_turns: int, *, chunks: int = 1) -> list[Turn]:
    """Build a pure-text multi-turn conversation."""

    if user_turns < 1:
        raise ValueError("user_turns must be positive")
    return [[TextBlock(REAL_TEXT, chunks=chunks)] for _ in range(user_turns)]


def make_loop_script(llm_calls: int, *, chunks: int = 5) -> list[Turn]:
    """Build one user turn with ``llm_calls`` model replies.

    Every reply except the last one requests a tool call, so the agent really
    executes ``llm_calls - 1`` tool rounds before the final text reply.
    """

    if llm_calls < 1:
        raise ValueError("llm_calls must be positive")
    replies: list[Reply] = [[FunctionBlock("add_numbers", {
        "a": index + 1,
        "b": index + 2
    })] for index in range(llm_calls - 1)]
    replies.append(TextBlock(REAL_TEXT, chunks=chunks))
    return [replies]
