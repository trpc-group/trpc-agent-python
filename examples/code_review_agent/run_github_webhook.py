#!/usr/bin/env python3
"""Run the GitHub webhook receiver with Uvicorn."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import uvicorn

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def main() -> None:
    uvicorn.run(
        "examples.code_review_agent.github_integration.app:build_app_from_environment",
        factory=True,
        host=os.getenv("GITHUB_WEBHOOK_HOST", "127.0.0.1"),
        port=int(os.getenv("GITHUB_WEBHOOK_PORT", "8080")),
        log_level=os.getenv("GITHUB_WEBHOOK_LOG_LEVEL", "info"),
    )


if __name__ == "__main__":
    main()
