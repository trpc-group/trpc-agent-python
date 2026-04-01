# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Utils for AG-UI plugin."""

import multiprocessing
from typing import Any
from typing import Dict

from .._core import AgUiAgent
from ._constants import AGUI_AGENT_PLUGIN_NAME


def set_current_process_var(name: str, value: Any) -> None:
    """Set a custom attribute on the current process object.

    Args:
        name: Name of the attribute to set
        value: Value to assign to the attribute
    """
    current_process = multiprocessing.current_process()
    setattr(current_process, name, value)


def get_current_process_var(name: str, default: Any = None) -> Any:
    """Get a custom attribute from the current process object.

    Args:
        name: Name of the attribute to retrieve
        default: Default value if attribute doesn't exist

    Returns:
        The attribute value if exists, otherwise default value
    """
    current_process = multiprocessing.current_process()
    return getattr(current_process, name, default)


def get_agui_agent(path: str) -> AgUiAgent:
    """Get the AgUiAgent plugin instance.

    Returns:
        AgUiAgent: The AgUiAgent instance.
    """
    return get_current_process_var(AGUI_AGENT_PLUGIN_NAME)[path]


def set_agui_agent(agui_agents: Dict[str, AgUiAgent]):
    """Register the AgUiAgent url and handler.

    Args:
        path: The path of the AgUiAgent.
        service_name: The service name of the AgUiAgent.
    """
    set_current_process_var(AGUI_AGENT_PLUGIN_NAME, agui_agents)
