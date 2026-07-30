# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Standalone entry point for the TRPC Agent FastAPI server.

Usage::

    python3 run_server.py [OPTIONS]

All options can also be supplied via environment variables (see help text below).

Examples::

    # Minimal — credentials via env vars
    export TRPC_AGENT_API_KEY=sk-xxx
    export TRPC_AGENT_BASE_URL=https://api.openai.com/v1
    export TRPC_AGENT_MODEL_NAME=gpt-4o-mini
    python3 run_server.py

    # All options on the command line
    python3 run_server.py \\
        --model_key   sk-xxx \\
        --model_url   https://api.openai.com/v1 \\
        --model_name  gpt-4o-mini \\
        --ip          0.0.0.0 \\
        --port        8080

    # Load a custom agent module
    python3 run_server.py --agent_module examples.fastapi_server.agent.agent --port 8080

    # Use the built-in weather demo agent
    python3 run_server.py --agent_module agent.agent --port 8080
"""

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

sys.path.append(str(Path(__file__).parent.parent))
# ---------------------------------------------------------------------------
# Ensure the examples directory is importable when run directly, e.g.
#   cd examples/fastapi_server && python3 run_server.py
# ---------------------------------------------------------------------------
_HERE = os.path.dirname(os.path.abspath(__file__))
_EXAMPLES_ROOT = os.path.dirname(_HERE)
_REPO_ROOT = os.path.dirname(_EXAMPLES_ROOT)
for _p in (_HERE, _EXAMPLES_ROOT, _REPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="run_server.py",
        description="Start the TRPC Agent FastAPI server.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    # ------------------------------------------------------------------
    # Network
    # ------------------------------------------------------------------
    parser.add_argument(
        "--ip",
        default=os.getenv("TRPC_AGENT_HOST", "0.0.0.0"),
        metavar="IP",
        help="Network interface to bind (env: TRPC_AGENT_HOST).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("TRPC_AGENT_PORT", "8080")),
        metavar="PORT",
        help="TCP port to listen on (env: TRPC_AGENT_PORT).",
    )

    # ------------------------------------------------------------------
    # Model credentials
    # ------------------------------------------------------------------
    parser.add_argument(
        "--model_key",
        default=os.getenv("TRPC_AGENT_API_KEY", ""),
        metavar="KEY",
        help="LLM provider API key (env: TRPC_AGENT_API_KEY).",
    )
    parser.add_argument(
        "--model_url",
        default=os.getenv("TRPC_AGENT_BASE_URL", ""),
        metavar="URL",
        help="LLM API base URL, e.g. https://api.openai.com/v1 (env: TRPC_AGENT_BASE_URL).",
    )
    parser.add_argument(
        "--model_name",
        default=os.getenv("TRPC_AGENT_MODEL_NAME", "gpt-4o-mini"),
        metavar="NAME",
        help="Model identifier (env: TRPC_AGENT_MODEL_NAME).",
    )

    # ------------------------------------------------------------------
    # Application
    # ------------------------------------------------------------------
    parser.add_argument(
        "--app_name",
        default=os.getenv("TRPC_AGENT_APP_NAME", "trpc_agent_server"),
        metavar="NAME",
        help="Logical application name shown in logs (env: TRPC_AGENT_APP_NAME).",
    )
    parser.add_argument(
        "--agent_module",
        default=os.getenv("TRPC_AGENT_MODULE", ""),
        metavar="MODULE",
        help=("Dotted Python module path that exports `root_agent` (instance) or "
              "`create_agent()` (factory). When omitted a default assistant agent is "
              "created from the model credentials (env: TRPC_AGENT_MODULE)."),
    )
    parser.add_argument(
        "--instruction",
        default=os.getenv("TRPC_AGENT_INSTRUCTION", ""),
        metavar="TEXT",
        help=("Override the default agent's system instruction. "
              "Ignored when --agent_module is set (env: TRPC_AGENT_INSTRUCTION)."),
    )

    return parser.parse_args()


def main() -> None:
    args = _parse_args()

    # Validate that model credentials are available when no custom module is used.
    if not args.agent_module and not args.model_key:
        print(
            "Error: --model_key (or env TRPC_AGENT_API_KEY) is required when "
            "--agent_module is not specified.",
            file=sys.stderr,
        )
        sys.exit(1)

    from _app import run_server  # noqa: PLC0415

    run_server(
        app_name=args.app_name,
        model_key=args.model_key,
        model_url=args.model_url or None,
        model_name=args.model_name,
        host=args.ip,
        port=args.port,
        agent_module=args.agent_module or None,
        instruction=args.instruction or None,
    )


if __name__ == "__main__":
    main()
