# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Tool functions for the minimal graph workflow."""
import os
import sys
from typing import Any
from typing import Dict

from trpc_agent_sdk.tools import MCPToolset
from trpc_agent_sdk.tools import McpStdioServerParameters
from trpc_agent_sdk.tools import StdioConnectionParams


def _truncate_text(text: str, max_len: int = 80) -> str:
    if len(text) <= max_len:
        return text
    return text[:max_len - 3] + "..."


def _format_value(value: Any, max_len: int = 80) -> str:
    text = repr(value)
    if len(text) <= max_len:
        return text
    return text[:max_len - 3] + "..."


def _log_tool(tool_name: str, message: str) -> None:
    print(f"[tool_execute:{tool_name}] {message}")


def text_stats(text: str) -> Dict[str, Any]:
    """Simple tool: return word and sentence counts for the text."""
    _log_tool("text_stats", f"args.text={_truncate_text(text)}")
    words = text.split()
    sentence_count = sum(text.count(punct) for punct in (".", "!", "?"))
    result = {
        "word_count": len(words),
        "sentence_count": sentence_count,
    }
    _log_tool("text_stats", f"return={_format_value(result)}")
    return result


def weather_tool(location: str) -> Dict[str, Any]:
    """Simple weather tool: always returns sunny weather."""
    result = {
        "location": location,
        "weather": "sunny",
    }
    return result


# ---------------------------------------------------------------------------
# Code node: static Python code to execute
# ---------------------------------------------------------------------------
CODE_PYTHON_ANALYSIS = ("import statistics\n"
                        "import json\n"
                        "\n"
                        "data = [23, 45, 12, 67, 34, 89, 56, 78, 11, 43]\n"
                        "\n"
                        "results = {\n"
                        "    'count': len(data),\n"
                        "    'min': min(data),\n"
                        "    'max': max(data),\n"
                        "    'mean': round(statistics.mean(data), 2),\n"
                        "    'median': statistics.median(data),\n"
                        "    'stdev': round(statistics.stdev(data), 2),\n"
                        "}\n"
                        "\n"
                        "print('=== Python Data Analysis ===')\n"
                        "for key, value in results.items():\n"
                        "    print(f'{key}: {value}')\n"
                        "print(json.dumps(results, indent=2))\n")


# ---------------------------------------------------------------------------
# MCP toolset factory (stdio – self-contained, no external server needed)
# ---------------------------------------------------------------------------
def create_mcp_toolset() -> MCPToolset:
    """Create a stdio-based MCPToolset backed by ``mcp_server.py``."""
    env = os.environ.copy()
    env["LD_LIBRARY_PATH"] = (f"/usr/local/Python{sys.version_info.major}{sys.version_info.minor}/lib/:" +
                              env.get("LD_LIBRARY_PATH", ""))
    svr_file = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "mcp_server.py"), )
    server_params = McpStdioServerParameters(
        command=f"python{sys.version_info.major}.{sys.version_info.minor}",
        args=[svr_file],
        env=env,
    )
    return MCPToolset(connection_params=StdioConnectionParams(server_params=server_params, timeout=5), )
