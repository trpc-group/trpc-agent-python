# Tencent is pleased to support the open source community by making trpc-agent-python available.
# Copyright (C) 2025 Tencent. All rights reserved.
# trpc-agent-python is licensed under the Apache License Version 2.0.
"""Unified diff parser — converts git diff text into structured data."""

import re
from typing import Any


HUNK_HEADER_RE = re.compile(r'^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@')
ONLY_FILES_RE = re.compile(r'^diff --git a/(.*) b/(.*)$')
NEW_FILE_RE = re.compile(r'^new file mode')
DELETED_FILE_RE = re.compile(r'^deleted file mode')


def parse_diff(diff_text: str) -> dict[str, Any]:
    """Parse unified diff into structured data.

    Returns dict with:
        - files: list of file change objects
        - total_added_lines: int
        - _all_hunks: flat list of all hunks
    """
    files: list[dict[str, Any]] = []
    all_hunks: list[dict[str, Any]] = []
    current_file: dict[str, Any] | None = None
    current_hunk: dict[str, Any] | None = None

    for line in diff_text.splitlines():
        if line.startswith('diff --git '):
            m = ONLY_FILES_RE.match(line)
            if m:
                current_file = {
                    'path': m.group(2),
                    'old_path': m.group(1),
                    'hunks': []
                }
                files.append(current_file)
                current_hunk = None
            continue

        if not current_file:
            continue

        if line.startswith('--- ') or line.startswith('+++ '):
            continue

        hunk_match = HUNK_HEADER_RE.match(line)
        if hunk_match:
            current_hunk = {
                'old_start': int(hunk_match.group(1)),
                'old_count': int(hunk_match.group(2) or 1),
                'new_start': int(hunk_match.group(3)),
                'new_count': int(hunk_match.group(4) or 1),
                'added_lines': [],
                'context_before': [],
                'file_path': current_file['path'],
                '_line_counter': int(hunk_match.group(3)),
            }
            current_file['hunks'].append(current_hunk)
            all_hunks.append(current_hunk)
            continue

        if current_hunk is None:
            continue

        if line.startswith('+') and not line.startswith('+++'):
            text = line[1:] if len(line) > 1 else ''
            current_hunk['added_lines'].append({
                'line': current_hunk['_line_counter'],
                'text': text,
                'context': list(current_hunk['context_before'][-3:])
            })
            current_hunk['_line_counter'] += 1
        elif not line.startswith('-'):
            ctx_text = line[1:] if line.startswith(' ') else line
            current_hunk['context_before'].append(ctx_text)
            current_hunk['_line_counter'] += 1

    total_added = sum(
        sum(len(h['added_lines']) for h in f['hunks'])
        for f in files
    )

    for f in files:
        for h in f['hunks']:
            h.pop('_line_counter', None)

    return {
        'files': files,
        'total_added_lines': total_added,
        '_all_hunks': all_hunks,
    }
