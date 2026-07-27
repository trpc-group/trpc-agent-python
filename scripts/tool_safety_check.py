#!/usr/bin/env python
"""Thin launcher for the Tool Script Safety Guard CLI."""

from trpc_agent_sdk.tools.safety import safety_cli_main

if __name__ == "__main__":
    raise SystemExit(safety_cli_main())
