# Tencent is pleased to support the open source community by making trpc-agent-python available.
# Copyright (C) 2025 Tencent. All rights reserved.
# trpc-agent-python is licensed under the Apache License Version 2.0.
"""Rule engine — applies deterministic review rules to parsed diff data."""

import re
from typing import Any


_SWALLOW_RE = re.compile(r'^\s*(?:pass|return|break|continue)\s*(?:#.*)?$')
_INDENT_RE = re.compile(r'^(\s*)')


def _get_indent(text: str) -> int:
    m = _INDENT_RE.match(text)
    return len(m.group(1)) if m else 0


def _check_exception_swallow(hunk: dict[str, Any], match_line_idx: int,
                             line_text: str, line_num: int) -> bool:
    """Return True if the except at match_line_idx is followed by a swallow pattern.

    Checks the next 1-2 added lines in the same hunk for pass/return/break/continue.
    Requires: (1) line numbers are consecutive (no context lines between),
    (2) the swallow line has >= indentation of the except line.
    """
    added_lines = hunk.get('added_lines', [])
    except_indent = _get_indent(line_text)

    for offset in range(1, min(3, len(added_lines) - match_line_idx)):
        next_line = added_lines[match_line_idx + offset]
        next_text = next_line.get('text', '')
        # Lines must be consecutive in the file (no context lines between)
        if next_line['line'] != line_num + offset:
            return False
        # Blank line → keep looking
        if not next_text.strip():
            continue
        # Swallow line must be at same or deeper indentation
        if _get_indent(next_text) < except_indent:
            return False
        if _SWALLOW_RE.match(next_text):
            return True
        # Non-blank, non-swallow line → stop looking
        return False
    return False


_NAMED_CHECKS = {
    'check_exception_swallow': _check_exception_swallow,
}


RULES = [
    # === Security ===
    {
        'rule_id': 'SEC-001',
        'category': 'security',
        'severity': 'critical',
        'title': 'Hardcoded Secret Detected',
        'patterns': [
            r'(?:password|passwd|pwd)\s*=\s*["\']([^"\']{3,})["\']',
            r'(?:api[_-]?key|apikey|secret[_-]?key)\s*=\s*["\']([^"\']{6,})["\']',
            r'(?:token|auth[_-]?token)\s*=\s*["\']([^"\']{6,})["\']',
        ],
        'recommendation': 'Use environment variables or a secret management service. Never commit hardcoded secrets.'
    },
    {
        'rule_id': 'SEC-002',
        'category': 'security',
        'severity': 'critical',
        'title': 'Command Injection Risk',
        'patterns': [
            r'shell\s*=\s*True',
            r'\bos\.system\(',
        ],
        'recommendation': 'Use subprocess.run() with an argument list and shell=False.'
    },
    {
        'rule_id': 'SEC-003',
        'category': 'security',
        'severity': 'high',
        'title': 'Unsafe Deserialization',
        'patterns': [
            r'\bpickle\.loads?\(',
            r'\byaml\.load\(',
        ],
        'recommendation': 'Use yaml.safe_load() for YAML, and only unpickle from trusted sources.'
    },
    {
        'rule_id': 'SEC-004',
        'category': 'security',
        'severity': 'critical',
        'title': 'Dynamic Code Execution',
        'patterns': [
            r'\beval\(',
            r'\bexec\(',
        ],
        'recommendation': 'Avoid eval() and exec(). Use explicit logic or safe parsing alternatives.'
    },
    # === Resource Leaks ===
    {
        'rule_id': 'RSC-001',
        'category': 'resource_leak',
        'severity': 'high',
        'title': 'File Handle May Not Be Closed',
        'patterns': [
            r'(?<!\bwith\s)\bopen\(',
        ],
        'recommendation': 'Use "with open(...) as f:" context manager to ensure the file is closed.'
    },
    {
        'rule_id': 'RSC-002',
        'category': 'resource_leak',
        'severity': 'high',
        'title': 'HTTP Resource May Not Be Closed',
        'patterns': [
            r'requests\.(?:get|post|put|delete|patch)\(',
            r'aiohttp\.ClientSession\(',
        ],
        'recommendation': 'Use context manager or ensure .close() is called after use.'
    },
    {
        'rule_id': 'RSC-003',
        'category': 'resource_leak',
        'severity': 'critical',
        'title': 'DB Connection May Not Be Closed',
        'patterns': [
            r'pymysql\.connect\(',
            r'sqlite3\.connect\(',
            r'psycopg2\.connect\(',
            r'mysql\.connector\.connect\(',
        ],
        'recommendation': 'Use connection pooling or "with conn:" context manager.'
    },
    # === Error Handling ===
    {
        'rule_id': 'ERR-001',
        'category': 'error_handling',
        'severity': 'high',
        'title': 'Swallowed Exception',
        'patterns': [
            r'except\s+\w+(?:\s+as\s+\w+)?\s*:',
        ],
        'recommendation': 'Log the exception or re-raise. Never silently swallow exceptions.',
        'check_fn': 'check_exception_swallow',
    },
    {
        'rule_id': 'ERR-002',
        'category': 'error_handling',
        'severity': 'medium',
        'title': 'Bare Except Clause',
        'patterns': [
            r'^\s*except\s*:',
        ],
        'recommendation': 'Catch specific exception types (e.g., except ValueError as e:).'
    },
    # === Testing ===
    {
        'rule_id': 'TST-001',
        'category': 'testing',
        'severity': 'medium',
        'title': 'New Code Without Test Coverage',
        'patterns': [
            r'^\s*def\s+\w+',
            r'^\s*class\s+\w+',
        ],
        'recommendation': 'Add unit tests for the new production code.',
        'confidence': 0.80,
        'needs_test_check': True,
    },
]


def _has_test_file(changed_files: list[dict[str, Any]]) -> bool:
    return any(
        'test' in f.get('path', '').lower() or f.get('path', '').endswith('_test.py')
        for f in changed_files
    )


def run_rules(parsed_diff: dict[str, Any]) -> list[dict[str, Any]]:
    """Run all deterministic rules against parsed diff and return findings."""
    findings: list[dict[str, Any]] = []
    files = parsed_diff.get('files', [])

    for file_info in files:
        file_path = file_info.get('path', '')
        for hunk in file_info.get('hunks', []):
            added_lines = hunk.get('added_lines', [])
            for idx, added in enumerate(added_lines):
                line_text = added.get('text', '')
                line_num = added.get('line', 0)

                for rule in RULES:
                    if not rule['patterns']:
                        continue
                    # TST-001 only triggers when no test file is present
                    if rule.get('needs_test_check') and _has_test_file(files):
                        continue
                    for pat in rule['patterns']:
                        m = re.search(pat, line_text, re.IGNORECASE)
                        if m:
                            # Multi-line check: ERR-001 needs to verify
                            # the next lines are pass/return/break/continue
                            check_fn = rule.get('check_fn')
                            if check_fn and check_fn in _NAMED_CHECKS:
                                if not _NAMED_CHECKS[check_fn](
                                        hunk, idx, line_text, line_num):
                                    break  # matched regex but failed check

                            findings.append({
                                'severity': rule['severity'],
                                'category': rule['category'],
                                'file': file_path,
                                'line': line_num,
                                'title': rule['title'],
                                'evidence': line_text.strip(),
                                'recommendation': rule['recommendation'],
                                'confidence': rule.get('confidence',
                                    0.95 if rule['severity'] == 'critical' else 0.85),
                                'rule_id': rule['rule_id'],
                                'source': 'deterministic_rules',
                            })
                            break

    return findings
