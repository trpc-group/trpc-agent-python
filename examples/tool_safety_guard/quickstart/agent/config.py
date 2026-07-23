# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Configuration for the tool safety guard quickstart."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


QUICKSTART_DIR = Path(__file__).resolve().parents[1]
DEFAULT_POLICY = QUICKSTART_DIR / "policy.yaml"
DEFAULT_OUT = QUICKSTART_DIR / "out"
SCRIPTS_DIR = QUICKSTART_DIR / "scripts"


@dataclass(frozen=True)
class ScriptSafetyCase:
    """A script-like payload that the quickstart feeds into the guard."""

    name: str
    script_path: Path
    language: str


DEFAULT_CASES = (
    ScriptSafetyCase("safe_report", SCRIPTS_DIR / "safe_report.py", "python"),
    ScriptSafetyCase("external_upload", SCRIPTS_DIR / "external_upload.py", "python"),
    ScriptSafetyCase("read_secret", SCRIPTS_DIR / "read_secret.py", "python"),
    ScriptSafetyCase("review_subprocess", SCRIPTS_DIR / "review_subprocess.py", "python"),
    ScriptSafetyCase("dangerous_cleanup", SCRIPTS_DIR / "dangerous_cleanup.sh", "bash"),
)
