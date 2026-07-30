import pytest

from trpc_agent_sdk.code_executors import PROGRAM_STATUS_RUNNING
from trpc_agent_sdk.code_executors import ProgramPoll
from trpc_agent_sdk.skills.tools._workspace_exec import _combine_output
from trpc_agent_sdk.skills.tools._workspace_exec import _exec_timeout_seconds
from trpc_agent_sdk.skills.tools._workspace_exec import _exec_yield_seconds
from trpc_agent_sdk.skills.tools._workspace_exec import _normalize_cwd
from trpc_agent_sdk.skills.tools._workspace_exec import _poll_output
from trpc_agent_sdk.skills.tools._workspace_exec import _write_yield_seconds


def test_normalize_cwd():
    assert _normalize_cwd("") == "."
    assert _normalize_cwd("work/demo") == "work/demo"
    with pytest.raises(ValueError, match="within the workspace"):
        _normalize_cwd("../demo")


def test_timeout_and_yield_helpers():
    assert _exec_timeout_seconds(0) > 0
    assert _exec_timeout_seconds(3) == 3.0
    assert _exec_yield_seconds(background=True, raw_ms=None) == 0.0
    assert _exec_yield_seconds(background=False, raw_ms=100) == 0.1
    assert _write_yield_seconds(None) > 0.0
    assert _write_yield_seconds(-1) == 0.0


def test_poll_output_and_combine_output():
    poll = ProgramPoll(status=PROGRAM_STATUS_RUNNING, output="ok", offset=1, next_offset=2)
    out = _poll_output("sid-1", poll)
    assert out["session_id"] == "sid-1"
    assert out["output"] == "ok"
    assert _combine_output("a", "b") == "ab"
