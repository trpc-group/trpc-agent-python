# Tencent is pleased to support the open source community by making
# tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Lightweight shell command parser for safety scanning.

Does NOT invoke a subshell.  Pure-Python heuristics sufficient for
static pre-execution safety analysis.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse


def command_name(full_command: str) -> str:
    """Extract the base command name.

    ``/usr/bin/rm -rf /`` → ``rm``
    """
    cmd = full_command.strip().split(None, 1)[0] if full_command.strip() else ""
    if "/" in cmd:
        cmd = cmd.rsplit("/", 1)[-1]
    if "\\" in cmd:
        cmd = cmd.rsplit("\\", 1)[-1]
    # Strip Windows extensions.
    for ext in (".exe", ".cmd", ".bat", ".com"):
        if cmd.lower().endswith(ext):
            cmd = cmd[:-len(ext)]
    return cmd.lower()


def has_pipeline(command: str) -> bool:
    """Return True if *command* contains a pipeline or command chain.

    Respects quoting so that ``echo "a|b"`` is not flagged.
    """
    in_single = False
    in_double = False
    for i, ch in enumerate(command):
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif ch in ("|", ";") and not in_single and not in_double:
            return True
        elif (ch == "&" and not in_single and not in_double and i + 1 < len(command) and command[i + 1] == "&"):
            return True
    return False


def extract_urls(command: str) -> list[str]:
    """Return HTTP(S) URLs found in *command*."""
    return re.findall(r"https?://[A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]+", command)


def extract_host(url: str) -> str:
    """Return the lower-cased hostname from *url*."""
    try:
        return urlparse(url).hostname or ""
    except Exception:
        return ""


def has_shell_bypass(command: str) -> bool:
    """Detect shell wrapper / subshell bypass patterns."""
    lower = command.lower()
    patterns = [
        "sh -c",
        "bash -c",
        "zsh -c",
        "eval ",
        "`",
        "$(",
        "${",
        " 2>",
    ]
    return any(p in lower for p in patterns)


def parse_args(command: str) -> list[str]:
    """Naive whitespace split of command into argv."""
    return command.strip().split()
