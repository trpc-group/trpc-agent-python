# Tencent is pleased to support the open source community by making trpc-agent-python available.
# Copyright (C) 2025 Tencent. All rights reserved.
# trpc-agent-python is licensed under the Apache License Version 2.0.
"""Filter utilities — three-level command safety checks.

DENY         — system-destroying commands (rm -rf /, mkfs, fork bomb, etc.)
ASK          — operations needing confirmation (sudo, iptables, etc.)
NEEDS_REVIEW — suspicious but potentially legitimate (pip install, curl, etc.)

This module has NO framework dependencies. Safe to import from test suites.
"""

from typing import Any


# ---- Danger levels ----

DENY_PATTERNS = [
    'rm -rf /',
    'mkfs.',
    'dd if=',
    'shutdown',
    'reboot',
    ':(){ :|:& };:',
    'chmod 777 /',
    '> /dev/sda',
]

ASK_PATTERNS = [
    'sudo ',
    'su ',
    'iptables',
    'passwd',
    'chown',
    'chmod 777',
]

REVIEW_PATTERNS = [
    'pip install',
    'pip3 install',
    'npm install',
    'go install',
    'apt-get install',
    'yum install',
    'curl ',
    'wget ',
    'nc ',
    'netcat ',
    'chmod ',
    'chown ',
]

FORBIDDEN_PATHS = [
    '/etc/shadow',
    '/etc/passwd',
    '/root/',
    '/boot/',
    '~/.ssh',
    'C:\\Windows\\System32\\',
    'C:\\Windows\\SysWOW64\\',
    '/var/log',
    '/proc/',
    '/sys/',
]


def _check_patterns(text: str, patterns: list[str]) -> tuple[bool, str]:
    """Check text against a list of patterns. Returns (matched, pattern)."""
    lower = text.lower()
    for p in patterns:
        if p.lower() in lower:
            return True, p
    return False, ''


def classify_command(text: str) -> tuple[str, str]:
    """Classify a command/text by risk level.

    Returns (level, matched_pattern) where level is one of:
    'deny', 'ask', 'needs_human_review', 'allow'
    """
    matched, pattern = _check_patterns(text, DENY_PATTERNS)
    if matched:
        return 'deny', pattern
    matched, pattern = _check_patterns(text, FORBIDDEN_PATHS)
    if matched:
        return 'deny', pattern
    matched, pattern = _check_patterns(text, ASK_PATTERNS)
    if matched:
        return 'ask', pattern
    matched, pattern = _check_patterns(text, REVIEW_PATTERNS)
    if matched:
        return 'needs_human_review', pattern
    return 'allow', ''


def check_dangerous(findings: list[dict[str, Any]]) -> tuple[list[dict], list[dict], list[dict]]:
    """Three-level safety check on findings.

    Returns (blocked_deny, needs_review, allowed).
    blocked_deny  — must not execute (system destruction)
    needs_review  — requires human approval before sandbox (ask + needs_human_review)
    allowed       — safe to execute in sandbox
    """
    blocked: list[dict] = []
    needs_review: list[dict] = []
    allowed: list[dict] = []

    for f in findings:
        evidence = f.get('evidence', '')
        level, pattern = classify_command(evidence)
        if level == 'deny':
            f['filter_action'] = 'deny'
            f['filter_reason'] = f'Forbidden: {pattern}'
            blocked.append(f)
        elif level in ('ask', 'needs_human_review'):
            f['filter_action'] = level
            f['filter_reason'] = f'Needs review: {pattern}'
            needs_review.append(f)
        else:
            f['filter_action'] = 'allow'
            allowed.append(f)

    return blocked, needs_review, allowed
