"""Tests for the optional LangGraph dependency boundary."""

import subprocess
import sys


def test_core_agent_imports_do_not_require_graph_dependencies():
    script = r"""
import importlib.abc
import sys


class BlockGraphImports(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".", 1)[0] in {"langgraph", "langchain", "langchain_core"}:
            raise ModuleNotFoundError(f"No module named {fullname!r}", name=fullname)
        return None


sys.meta_path.insert(0, BlockGraphImports())

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.agents.utils import TRPC_EVENT_MARKER
from trpc_agent_sdk.runners import Runner

assert LlmAgent is not None
assert Runner is not None
assert TRPC_EVENT_MARKER == "__trpc_event__"

try:
    from trpc_agent_sdk.agents import LangGraphAgent
except ImportError as exc:
    assert "trpc-agent-py[graph]" in str(exc)
else:
    raise AssertionError("LangGraphAgent import should require the graph extra")
"""
    subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        capture_output=True,
        text=True,
    )
