"""Code Review Agent package.

Thin re-export surface so callers can do::

    import agent
    await agent._async_main(ns)   # or agent.main(...)

without reaching into ``agent.agent`` directly. The heavy lifting lives in
``agent.agent`` (orchestration), with ``db/``, ``filters/``, ``llm/``,
``sandbox/`` and ``telemetry/`` as sub-packages.
"""

from .agent import main
from .agent import _async_main
from .agent import skill_load
from .agent import load_diff
from .agent import persist_changeset
from .agent import FIXTURES

__all__ = [
    "main",
    "_async_main",
    "skill_load",
    "load_diff",
    "persist_changeset",
    "FIXTURES",
]
