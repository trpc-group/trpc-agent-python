# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Local stdio MCP command server used by the real-agent safety demo."""

from __future__ import annotations

import asyncio
from pathlib import Path

from mcp.server import FastMCP

APP = FastMCP("tool-safety-demo")
WORK_DIR = Path(__file__).resolve().parent
MAX_OUTPUT_CHARS = 4096


@APP.tool()
async def execute_command(command: str) -> dict:
    """Execute one shell command in the disposable example directory."""
    process = await asyncio.create_subprocess_shell(
        command,
        cwd=WORK_DIR,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    return {
        "return_code": process.returncode,
        "stdout": stdout.decode(errors="replace")[:MAX_OUTPUT_CHARS],
        "stderr": stderr.decode(errors="replace")[:MAX_OUTPUT_CHARS],
    }


if __name__ == "__main__":
    APP.run(transport="stdio")
