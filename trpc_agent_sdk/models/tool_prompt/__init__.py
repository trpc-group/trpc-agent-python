# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Tool prompt module for TRPC Agent framework."""

from ._base import ToolPrompt
from ._factory import ToolPromptFactory
from ._factory import get_factory
from ._factory import initialize
from ._factory import initialize as initialize_factory
from ._json import JsonToolPrompt
from ._xml import XmlToolPrompt

__all__ = [
    "ToolPrompt",
    "ToolPromptFactory",
    "get_factory",
    "initialize",
    "initialize_factory",
    "JsonToolPrompt",
    "XmlToolPrompt",
]

initialize_factory()
