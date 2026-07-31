#!/usr/bin/env python3
import argparse
import json
import re
import os

def parse_diff(diff_content):
    """
    Parses unified diff content.
    Returns a dict mapping filename -> list of dicts(line_no, type, content)
    """
    files = {}
    current_file = None
    current_line_num = 0

    lines = diff_content.splitlines()
    for line in lines:
        if line.startswith('diff --git'):
            # Reset current file info
            current_file = None
        elif line.startswith('--- '):
            continue
        elif line.startswith('+++ '):
            # Target file path
            # strip a/ or b/ if present
            m = re.match(r'^\+\+\+\s+b/(.*)$', line)
            if m:
                current_file = m.group(1)
            else:
                m2 = re.match(r'^\+\+\+\s+(.*)$', line)
                if m2:
                    current_file = m2.group(1)
            if current_file:
                # Remove possible quotes or trailing garbage
                current_file = current_file.strip().strip('"')
                files[current_file] = []
        else:
            hunk_match = None
            if line.startswith('@@') or '@@' in line:
                hunk_match = re.search(r'@@\s+-\d+(?:,\d+)?\s+\+(\d+)(?:,\d+)?\s*@@', line)
            if hunk_match:
                current_line_num = int(hunk_match.group(1))
            elif current_file is not None:
                if line.startswith('+'):
                    # Added/Modified line
                    files[current_file].append({
                        'line': current_line_num,
                        'type': 'added',
                        'content': line[1:]
                    })
                    current_line_num += 1
                elif line.startswith('-'):
                    # Deleted line, doesn't increment current_line_num for new file
                    pass
                else:
                    # Context line
                    files[current_file].append({
                        'line': current_line_num,
                        'type': 'context',
                        'content': line[1:] if (line and line[0] == ' ') else line
                    })
                    current_line_num += 1

    return files

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--diff', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()

    if not os.path.exists(args.diff):
        print(f"Error: diff file {args.diff} does not exist.")
        # Create empty output on failure so sandbox run doesn't crash completely
        with open(args.output, 'w') as f:
            json.dump({}, f)
        return

    with open(args.diff, 'r', encoding='utf-8', errors='ignore') as f:
        content = f.read()

    parsed = parse_diff(content)

    out_dir = os.path.dirname(args.output)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(args.output, 'w', encoding='utf-8') as f:
        json.dump(parsed, f, indent=2)

if __name__ == '__main__':
    main()
