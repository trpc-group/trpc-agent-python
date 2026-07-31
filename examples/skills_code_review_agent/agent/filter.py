# Tencent is pleased to support the open source community by making trpc-agent-python available.
# Copyright (C) 2025 Tencent. All rights reserved.
# trpc-agent-python is licensed under the Apache License Version 2.0.
"""Filter utilities — three-level command safety checks.

DENY         — system-destroying commands (rm -rf /, mkfs, fork bomb, etc.)
ASK          — operations needing confirmation (sudo, iptables, etc.)
NEEDS_REVIEW — suspicious but potentially legitimate (pip install, curl, etc.)

Matching is token-based (shlex-split) so that:
  - ``su``/``curl`` match regardless of separator (space, tab) and never
    false-positive on longer identifiers like ``supervisorctl``/``curl_parser``,
  - directory paths are only flagged when they appear as command arguments,
    not as incidental path literals in source code.

This module has NO framework dependencies. Safe to import from test suites.
"""

import shlex
from typing import Any


# ---- Danger levels ----

_ASK_COMMANDS = frozenset({'sudo', 'su', 'passwd', 'chown', 'iptables'})

_REVIEW_COMMANDS = frozenset({'curl', 'wget', 'nc', 'netcat'})

_INSTALLERS = frozenset({
    'pip', 'pip3', 'npm', 'go', 'apt-get', 'apt', 'yum', 'gem', 'cargo', 'brew',
})

_DESTRUCTIVE_COMMANDS = frozenset({'shutdown', 'reboot', 'mkfs'})

# Files whose mere access is dangerous regardless of context.
_EXACT_FORBIDDEN = (
    '/etc/shadow', '/etc/passwd',
    '~/.ssh', 'C:\\Windows\\System32\\', 'C:\\Windows\\SysWOW64\\',
)

# Sensitive directories: only flagged when used as a command argument
# (not when they appear in code assignments like `log_dir = "/root/app"`).
_SENSITIVE_DIRS = ('/root/', '/boot/', '/proc/', '/sys/', '/var/log')

# System directories protected against world-writable chmod.
_SYSTEM_DIRS = ('/etc', '/bin', '/sbin', '/lib', '/usr', '/var',
                '/boot', '/root', '/proc', '/sys', '/dev')


def _is_flag(token: str) -> bool:
    return token.startswith('-') and token != '-'


def _path_target(tokens: list[str]) -> str:
    """First non-flag token after a command verb."""
    for tok in tokens[1:]:
        if not _is_flag(tok):
            return tok
    return ''


def _has_deny_sequence(tokens: list[str]) -> str | None:
    """Detect destructive command sequences (rm -rf / family, fork bomb, ...)."""
    for i, tok in enumerate(tokens):
        if tok == 'rm':
            flags = [t for t in tokens[i + 1:] if _is_flag(t)]
            has_r = any('r' in f.lstrip('-') for f in flags)
            has_f = any('f' in f.lstrip('-') for f in flags)
            target = _path_target(tokens[i:])
            norm = target.rstrip('/').rstrip('\\') or '/'
            if has_r and has_f and norm in ('/', '/*', '~', '~/'):
                return 'rm -rf /'
            break
    joined = ' '.join(tokens)
    if ':(){' in joined:
        return ':(){ :|:& };:'
    if tokens and tokens[0].lower() in _DESTRUCTIVE_COMMANDS:
        return tokens[0]
    for tok in tokens:
        low = tok.lower()
        if low.startswith('mkfs'):
            return low
    if tokens and tokens[0].lower() == 'dd' and any('if=' in t for t in tokens):
        return 'dd if='
    for tok in tokens:
        if tok in ('/dev/sda', '/dev/sdb', '/dev/sd*'):
            return '> /dev/sda'
    if 'chmod' in tokens:
        ci = tokens.index('chmod')
        rest = tokens[ci + 1:]
        mode = rest[0] if rest else ''
        target = rest[1] if len(rest) > 1 else ''
        if mode == '777' and (target in ('/', '/*', '~', '~/')
                              or target.startswith(_SYSTEM_DIRS)):
            return 'chmod 777 /'
    return None


def _forbidden_path(tokens: list[str]) -> str | None:
    """Return a forbidden path if a command argument points at one."""
    is_assignment = '=' in tokens
    for tok in tokens:
        for prefix in _EXACT_FORBIDDEN:
            if tok.startswith(prefix):
                return prefix
        if is_assignment:
            continue
        for prefix in _SENSITIVE_DIRS:
            if tok.startswith(prefix):
                return prefix
    return None


def _ask_command(tokens: list[str]) -> str | None:
    for tok in tokens:
        low = tok.lower()
        if low in _ASK_COMMANDS:
            return low
    if 'chmod' in tokens:
        ci = tokens.index('chmod')
        rest = tokens[ci + 1:]
        mode = rest[0] if rest else ''
        target = rest[1] if len(rest) > 1 else ''
        if mode == '777' and not (target in ('/', '/*', '~', '~/')
                                  or target.startswith(_SYSTEM_DIRS)):
            return 'chmod 777'
    return None


def _review_command(tokens: list[str]) -> str | None:
    for i, tok in enumerate(tokens):
        low = tok.lower()
        if low in _REVIEW_COMMANDS:
            return low
        if low in _INSTALLERS and i + 1 < len(tokens) and tokens[i + 1].lower() == 'install':
            return f'{low} install'
    return None


def _tokenize(text: str) -> list[str]:
    """Split a command string into shell tokens (POSIX rules)."""
    try:
        return shlex.split(text)
    except ValueError:
        return text.split()


def classify_command(text: str) -> tuple[str, str]:
    """Classify a command/text by risk level.

    Returns (level, matched_pattern) where level is one of:
    'deny', 'ask', 'needs_human_review', 'allow'.
    """
    if not text or not text.strip():
        return 'allow', ''

    tokens = _tokenize(text)
    if not tokens:
        return 'allow', ''

    deny = _has_deny_sequence(tokens)
    if deny:
        return 'deny', deny
    path = _forbidden_path(tokens)
    if path:
        return 'deny', path
    ask = _ask_command(tokens)
    if ask:
        return 'ask', ask
    review = _review_command(tokens)
    if review:
        return 'needs_human_review', review
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
