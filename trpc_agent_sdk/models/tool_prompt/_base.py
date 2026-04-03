# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Base tool prompt class for TRPC Agent framework."""

from abc import ABC
from abc import abstractmethod
from typing import List

from trpc_agent_sdk.types import FunctionCall
from trpc_agent_sdk.types import Tool


class ToolPrompt(ABC):
    """Abstract base class for tool prompt implementations.

    This class defines the interface for converting tools to prompt text
    and parsing function calls from model responses.
    """

    def __init__(self):
        """Initialize the tool prompt."""
        pass

    @abstractmethod
    def build_prompt(self, tools: List[Tool]) -> str:
        """Build a prompt string from a list of tools.

        Args:
            tools: List of Tool objects to convert to prompt text

        Returns:
            String representation of tools for inclusion in system prompt
        """
        pass

    @abstractmethod
    def parse_function(self, content: str) -> List[FunctionCall]:
        """Parse function calls from complete content.

        Args:
            content: Complete content string containing function calls

        Returns:
            List of FunctionCall objects parsed from content

        Raises:
            ValueError: If content cannot be parsed as function calls
        """
        pass
