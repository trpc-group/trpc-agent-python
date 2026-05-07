#!/usr/bin/env python3
# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""End-to-end demo for `CubeCodeExecutor` and `CubeWorkspaceRuntime`.

Requires the optional ``[cube]`` extra and the following environment
variables (same names hermes uses):

- ``CUBE_TEMPLATE_ID``: Cube template id (e.g. ``std-XXXXXXXX``)
- ``E2B_API_URL``:       Cube/E2B-compatible gateway URL
- ``E2B_API_KEY``:       API key for the gateway

Usage::

    pip install 'trpc-agent-py[cube]'
    export CUBE_TEMPLATE_ID=...
    export E2B_API_URL=...
    export E2B_API_KEY=...
    python examples/code_executors/cube_demo.py

The demo walks through:
  1. ``CubeCodeExecutor.create`` (no sandbox_id -> fresh sandbox)
  2. ``execute_code`` for Python and Bash code blocks
  3. Workspace runtime: ``create_workspace`` -> ``put_files`` -> ``run_program`` -> ``collect_outputs``
  4. ``destroy`` (kills the remote sandbox)
"""

from __future__ import annotations

import asyncio
import os
import sys

from trpc_agent_sdk.code_executors.cube import CubeCodeExecutor
from trpc_agent_sdk.code_executors.cube import CubeCodeExecutorConfig
from trpc_agent_sdk.code_executors.cube import create_cube_workspace_runtime
from trpc_agent_sdk.code_executors._types import CodeBlock
from trpc_agent_sdk.code_executors._types import CodeExecutionInput
from trpc_agent_sdk.code_executors._types import WorkspaceOutputSpec
from trpc_agent_sdk.code_executors._types import WorkspacePutFileInfo
from trpc_agent_sdk.code_executors._types import WorkspaceRunProgramSpec


def _require_env() -> None:
    missing = [name for name in ("CUBE_TEMPLATE_ID", "E2B_API_URL", "E2B_API_KEY") if not os.getenv(name)]
    if missing:
        sys.stderr.write(f"missing required env vars: {', '.join(missing)}\n")
        sys.exit(2)


async def _run() -> None:
    _require_env()

    cfg = CubeCodeExecutorConfig(
        execute_timeout=30.0,
        idle_timeout=600,
    )

    executor = await CubeCodeExecutor.create(cfg)
    print(f"created sandbox: {executor.sandbox_id}")

    try:
        # 1. execute_code with two blocks (python and bash).
        result = await executor.execute_code(
            invocation_context=None,  # type: ignore[arg-type]
            code_execution_input=CodeExecutionInput(code_blocks=[
                CodeBlock(code="print('hello from cube py')", language="python"),
                CodeBlock(code="echo hello from cube bash", language="bash"),
            ]),
        )
        print("execute_code result:")
        print(result.output)

        # 2. Workspace runtime end-to-end.
        runtime = create_cube_workspace_runtime(executor)
        manager = runtime.manager()
        fs = runtime.fs()
        runner = runtime.runner()

        ws = await manager.create_workspace("demo-1")
        print(f"workspace path: {ws.path}")

        await fs.put_files(ws, [
            WorkspacePutFileInfo(path="work/script.py",
                                 content=b"print('script ran')\n"),
        ])

        run_result = await runner.run_program(
            ws,
            WorkspaceRunProgramSpec(cmd="python3", args=["work/script.py"], timeout=15.0),
        )
        print(f"run_program exit={run_result.exit_code} stdout={run_result.stdout!r}")

        outputs = await fs.collect_outputs(ws, WorkspaceOutputSpec(globs=["work/*.py"], inline=True))
        for ref in outputs.files:
            print(f"output: {ref.name} ({len(ref.content)} chars)")

        await manager.cleanup("demo-1")
    finally:
        await executor.destroy()
        print("sandbox destroyed")


if __name__ == "__main__":
    asyncio.run(_run())
