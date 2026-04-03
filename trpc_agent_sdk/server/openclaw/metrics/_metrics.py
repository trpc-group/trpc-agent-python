# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Metrics module for trpc-claw."""

from typing import Callable

from trpc_agent_sdk.log import logger

from ..config import ClawConfig
from ._langfuse import setup_langfuse

_metrics_setup_functions = {
    "langfuse": setup_langfuse,
}


def register_metrics(metrics_type: str, setup_function: Callable[..., bool], force: bool = False) -> bool:
    """Register metrics.
    Args:
        metrics_type: Metrics type.
        setup_function: Setup function with args: ClawConfig.
        force: Force register metrics.
    Returns:
        bool: True if register metrics success, False otherwise.
    """
    if metrics_type in _metrics_setup_functions and not force:
        logger.warning("Metrics type %s already registered", metrics_type)
        return False
    _metrics_setup_functions[metrics_type] = setup_function
    return True


def setup_metrics(config: ClawConfig) -> bool:
    """Setup metrics.
    Args:
        config: ClawConfig
    Returns:
        bool: True if setup metrics success, False otherwise.
    """
    try:
        setup_function = _metrics_setup_functions.get(config.metrics.type)
        if not setup_function:
            logger.warning("Invalid metrics type: %s", config.metrics.type)
            return False
        return setup_function(config)
    except Exception as e:  # pylint: disable=broad-except
        logger.warning("Setup metrics failed: %s", e)
        return False
