# Tencent is pleased to support the open source community by making trpc-agent-python available.
# Copyright (C) 2025 Tencent. All rights reserved.
# trpc-agent-python is licensed under the Apache License Version 2.0.
#!/usr/bin/env python3
"""Run static checks on parsed diff output and generate findings."""
import sys
import json
import re


RULES = [
    # Security
    {
        'rule_id': 'SEC-001',
        'category': 'security',
        'severity': 'critical',
        'title': 'Hardcoded Secret',
        'patterns': [
            r'(?:password|passwd|pwd)\s*=\s*["\'][^"\']{3,}["\']',
            r'(?:api[_-]?key|apikey|secret[_-]?key)\s*=\s*["\'][^"\']{6,}["\']',
            r'(?:token|auth[_-]?token)\s*=\s*["\'][^"\']{6,}["\']',
        ],
        'recommendation': 'Use environment variables or secret management instead of hardcoded secrets'
    },
    {
        'rule_id': 'SEC-002',
        'category': 'security',
        'severity': 'critical',
        'title': 'Command Injection Risk',
        'patterns': [
            r'shell\s*=\s*True',
            r'\bos\.system\(',
            r'\bos\.popen\(',
        ],
        'recommendation': 'Use subprocess.run() with argument list and shell=False'
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
        'recommendation': 'Use yaml.safe_load() or only unpickle trusted data'
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
        'recommendation': 'Avoid eval() and exec(). Use safe alternatives or explicit logic.'
    },
    # Resource leaks
    {
        'rule_id': 'RSC-001',
        'category': 'resource_leak',
        'severity': 'high',
        'title': 'Unclosed File Handle',
        'patterns': [
            r'^\s*[^=#]*\bopen\(',
        ],
        'recommendation': 'Use "with open(...) as f:" context manager to ensure file closure'
    },
    {
        'rule_id': 'RSC-002',
        'category': 'resource_leak',
        'severity': 'high',
        'title': 'Unclosed HTTP Session',
        'patterns': [
            r'requests\.(?:Session|get|post|put|delete|patch)\(',
            r'aiohttp\.ClientSession\(',
        ],
        'recommendation': 'Use context manager "with requests.Session() as s:" or remember to call .close()'
    },
    {
        'rule_id': 'RSC-003',
        'category': 'resource_leak',
        'severity': 'critical',
        'title': 'Potential DB Connection Leak',
        'patterns': [
            r'(?:pymysql|sqlite3|psycopg2|mysql\.connector)\.connect\(',
        ],
        'recommendation': 'Use connection pooling or context manager for database connections'
    },
    # Error handling
    {
        'rule_id': 'ERR-001',
        'category': 'error_handling',
        'severity': 'high',
        'title': 'Swallowed Exception',
        'patterns': [
            r'except\s*(?:(?:Exception|BaseException)\s*)?:',
        ],
        'recommendation': 'Log the exception or re-raise. Never silently swallow exceptions.'
    },
    {
        'rule_id': 'ERR-002',
        'category': 'error_handling',
        'severity': 'medium',
        'title': 'Bare Except Clause',
        'patterns': [
            r'^\s*except\s*:',
        ],
        'recommendation': 'Catch specific exception types (e.g., except ValueError as e:)'
    },
    # Testing
    {
        'rule_id': 'TST-001',
        'category': 'testing',
        'severity': 'medium',
        'title': 'New Function Without Test',
        'patterns': [],
        'recommendation': 'Add unit tests for new functions and methods'
    },
    {
        'rule_id': 'TST-002',
        'category': 'testing',
        'severity': 'low',
        'title': 'Modified Code Without Test Update',
        'patterns': [],
        'recommendation': 'Update existing tests to cover modified behavior'
    },
]


def has_test_changes(files: list) -> bool:
    """Check if any changed files look like test files."""
    for f in files:
        path = f.get('path', '')
        if 'test' in path.lower() or path.endswith('_test.py'):
            return True
    return False


def check_line(line_text: str, patterns: list, line_num: int, file_path: str) -> list:
    """Check a single line against patterns."""
    findings = []
    for pattern in patterns:
        match = re.search(pattern, line_text, re.IGNORECASE)
        if match:
            findings.append({
                'line': line_num,
                'match': match.group(0),
            })
    return findings


def run_checks(parsed_diff: dict) -> list:
    """Run all rules against parsed diff data."""
    findings = []
    files = parsed_diff.get('files', [])

    for file_info in files:
        file_path = file_info.get('path', '')
        for hunk in file_info.get('hunks', []):
            for added in hunk.get('added_lines', []):
                line_text = added.get('text', '')
                line_num = added.get('line', 0)

                for rule in RULES:
                    if not rule['patterns']:
                        continue
                    hits = check_line(line_text, rule['patterns'], line_num, file_path)
                    for hit in hits:
                        findings.append({
                            'severity': rule['severity'],
                            'category': rule['category'],
                            'file': file_path,
                            'line': hit['line'],
                            'title': rule['title'],
                            'evidence': hit['match'],
                            'recommendation': rule['recommendation'],
                            'confidence': 0.95 if rule['severity'] == 'critical' else 0.85,
                            'rule_id': rule['rule_id'],
                        })

    # Testing rules: check if new functions/classes added but no test changes
    has_new_func = False
    for file_info in files:
        for hunk in file_info.get('hunks', []):
            for added in hunk.get('added_lines', []):
                txt = added.get('text', '')
                if re.match(r'^def\s+\w+', txt):
                    has_new_func = True

    if has_new_func and not has_test_changes(files):
        for file_info in files:
            for hunk in file_info.get('hunks', []):
                for added in hunk.get('added_lines', []):
                    txt = added.get('text', '')
                    if re.match(r'^def\s+\w+', txt):
                        findings.append({
                            'severity': 'medium',
                            'category': 'testing',
                            'file': file_info.get('path', ''),
                            'line': added.get('line', 0),
                            'title': 'New Function Without Test',
                            'evidence': f'Added function without corresponding test: {txt.strip()}',
                            'recommendation': 'Add unit tests for new functions',
                            'confidence': 0.80,
                            'rule_id': 'TST-001',
                        })

    return findings


if __name__ == '__main__':
    if len(sys.argv) < 2:
        input_data = json.load(sys.stdin)
    else:
        input_path = sys.argv[1]
        with open(input_path, encoding='utf-8') as f:
            input_data = json.loads(f.read())

    findings = run_checks(input_data)
    print(json.dumps({'findings': findings, 'count': len(findings)}, indent=2))
