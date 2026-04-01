# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""File tool set containing all file operation and text editing tools."""

from typing import List
from typing import Optional
from typing_extensions import override

from trpc_agent_sdk.abc import ToolSetABC
from trpc_agent_sdk.context import InvocationContext

from .._base_tool import BaseTool
from ._bash_tool import BashTool
from ._edit_tool import EditTool
from ._glob_tool import GlobTool
from ._grep_tool import GrepTool
from ._read_tool import ReadTool
from ._write_tool import WriteTool


class FileToolSet(ToolSetABC):
    """File tool set containing all file operation and text editing tools."""

    def __init__(self, cwd: Optional[str] = None):
        """Initialize tool set.

        Args:
            cwd: Working directory, shared by all tools
        """
        super().__init__()
        self.cwd = cwd

    @override
    async def get_tools(self, invocation_context: Optional[InvocationContext] = None) -> List[BaseTool]:
        """Get all tools in the tool set.

        Args:
            invocation_context: Optional invocation context

        Returns:
            List of tools
        """
        return [
            ReadTool(cwd=self.cwd),
            WriteTool(cwd=self.cwd),
            EditTool(cwd=self.cwd),
            GrepTool(cwd=self.cwd),
            BashTool(cwd=self.cwd),
            GlobTool(cwd=self.cwd),
        ]
