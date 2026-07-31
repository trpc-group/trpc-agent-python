# Tencent is pleased to support the open source community by making trpc-agent-python available.
# Copyright (C) 2025 Tencent. All rights reserved.
# trpc-agent-python is licensed under the Apache License Version 2.0.
#!/usr/bin/env python3
"""Parse a unified diff file and output structured JSON."""
import sys
import json
import re
from pathlib import Path


HUNK_HEADER_RE = re.compile(r'^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@')
DIFF_FILE_RE = re.compile(r'^(?:---|\+\+\+) [ab]/(.*)$')
ONLY_FILES_RE = re.compile(r'^diff --git a/(.*) b/(.*)$')


def parse_diff(diff_text: str) -> dict:
    """Parse unified diff into structured data."""
    files = []
    current_file = None
    current_hunk = None

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
            m = DIFF_FILE_RE.match(line)
            if m and line.startswith('+++ '):
                current_file['path'] = m.group(1)
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
            }
            current_file['hunks'].append(current_hunk)
            continue

        if current_hunk is not None:
            if line.startswith('+') and not line.startswith('+++'):
                text = line[1:] if len(line) > 1 else ''
                current_hunk['added_lines'].append({
                    'line': current_hunk['new_start'] + len(current_hunk['added_lines']),
                    'text': text,
                    'context': current_hunk['context_before'][-3:]  # last 3 context lines
                })
            elif not line.startswith('-'):
                ctx_text = line[1:] if line.startswith(' ') else line
                current_hunk['context_before'].append(ctx_text)

    result = {
        'files': files,
        'file_count': len(files),
        'total_added_lines': sum(
            sum(len(h['added_lines']) for h in f['hunks'])
            for f in files
        )
    }
    return result


if __name__ == '__main__':
    if len(sys.argv) >= 2:
        diff_path = Path(sys.argv[1])
        if not diff_path.exists():
            print(json.dumps({'error': f'File not found: {diff_path}'}))
            sys.exit(1)
        diff_text = diff_path.read_text(encoding='utf-8')
    else:
        diff_text = sys.stdin.read()

    if not diff_text.strip():
        print(json.dumps({'error': 'No diff content provided'}))
        sys.exit(1)

    result = parse_diff(diff_text)
    print(json.dumps(result, indent=2))
