#!/usr/bin/env python
"""Thin launcher for the Tool Script Safety Guard CLI."""

from trpc_agent_sdk.tools.safety._cli import main

if __name__ == "__main__":
    raise SystemExit(main())
