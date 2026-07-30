# Tencent is pleased to support the open source community by making trpc-agent-python available.
# Copyright (C) 2025 Tencent. All rights reserved.
# trpc-agent-python is licensed under the Apache License Version 2.0.
"""Unit tests for the automated code review agent."""
import json
import tempfile
import os
import sys
from pathlib import Path

import pytest

# Add the example directory to path
sys.path.insert(0, str(Path(__file__).parent))

from agent.diff_parser import parse_diff
from agent.rule_engine import run_rules
from agent.dedup import dedup_findings
from agent.redaction import redact_text, redact_findings, check_sensitive
from agent.filter import check_dangerous
from sandbox.runner import SandboxRunner
from storage.schema import ReviewStore
from report.json_report import generate_json_report
from report.markdown_report import generate_markdown_report


FIXTURES_DIR = Path(__file__).parent / 'fixtures'


def load_fixture(name: str) -> str:
    return (FIXTURES_DIR / name).read_text(encoding='utf-8')


# ===========================================================
# Diff Parser Tests
# ===========================================================

def test_parse_clean_diff():
    diff_text = load_fixture('clean.diff')
    result = parse_diff(diff_text)
    assert result['total_added_lines'] > 0
    assert len(result['files']) > 0
    file_info = result['files'][0]
    assert file_info['path'].endswith('utils.py')
    assert len(file_info['hunks']) > 0


def test_parse_security_diff():
    diff_text = load_fixture('security.diff')
    result = parse_diff(diff_text)
    assert len(result['files']) == 2
    assert result['total_added_lines'] > 5


def test_parse_empty_diff():
    result = parse_diff('')
    assert result['total_added_lines'] == 0
    assert len(result['files']) == 0


def test_parse_multiple_files():
    diff_text = load_fixture('security.diff')
    result = parse_diff(diff_text)
    paths = [f['path'] for f in result['files']]
    assert 'auth.py' in paths[0]
    assert 'config.py' in paths[1]


# ===========================================================
# Rule Engine Tests
# ===========================================================

def test_rule_engine_clean():
    diff_text = load_fixture('clean.diff')
    parsed = parse_diff(diff_text)
    findings = run_rules(parsed)
    # Clean diff: may have testing warnings but no critical security/leak issues
    critical = [f for f in findings if f['severity'] == 'critical']
    assert len(critical) == 0


def test_rule_engine_security():
    diff_text = load_fixture('security.diff')
    parsed = parse_diff(diff_text)
    findings = run_rules(parsed)
    categories = set(f['category'] for f in findings)
    assert 'security' in categories
    security_findings = [f for f in findings if f['category'] == 'security']
    assert len(security_findings) >= 3


def test_rule_engine_resource_leak():
    diff_text = load_fixture('resource_leak.diff')
    parsed = parse_diff(diff_text)
    findings = run_rules(parsed)
    categories = set(f['category'] for f in findings)
    assert 'resource_leak' in categories


def test_rule_engine_db_lifecycle():
    diff_text = load_fixture('db_lifecycle.diff')
    parsed = parse_diff(diff_text)
    findings = run_rules(parsed)
    categories = set(f['category'] for f in findings)
    assert any(c in categories for c in ['resource_leak', 'security'])


def test_rule_engine_missing_test():
    diff_text = load_fixture('missing_test.diff')
    parsed = parse_diff(diff_text)
    findings = run_rules(parsed)
    test_findings = [f for f in findings if f['category'] == 'testing']
    assert len(test_findings) > 0


def test_rule_engine_sandbox_fail():
    diff_text = load_fixture('sandbox_fail.diff')
    parsed = parse_diff(diff_text)
    findings = run_rules(parsed)
    assert isinstance(findings, list)


def test_rule_engine_findings_have_required_fields():
    diff_text = load_fixture('security.diff')
    parsed = parse_diff(diff_text)
    findings = run_rules(parsed)
    for f in findings:
        assert 'severity' in f
        assert 'category' in f
        assert 'file' in f
        assert 'line' in f
        assert 'title' in f
        assert 'evidence' in f
        assert 'recommendation' in f
        assert 'confidence' in f
        assert 'rule_id' in f


# ===========================================================
# Dedup Tests
# ===========================================================

def test_dedup_removes_duplicates():
    findings = [
        {'file': 'a.py', 'line': 1, 'category': 'security', 'title': 'X', 'confidence': 0.95, 'severity': 'critical', 'evidence': '', 'recommendation': '', 'rule_id': 'S-1'},
        {'file': 'a.py', 'line': 1, 'category': 'security', 'title': 'Y', 'confidence': 0.90, 'severity': 'high', 'evidence': '', 'recommendation': '', 'rule_id': 'S-2'},
    ]
    f, w = dedup_findings(findings)
    # Same file+line+category -> only first kept
    assert len(f) == 1


def test_dedup_different_lines_kept():
    findings = [
        {'file': 'a.py', 'line': 1, 'category': 'security', 'title': 'X', 'confidence': 0.95, 'severity': 'critical', 'evidence': '', 'recommendation': '', 'rule_id': 'S-1'},
        {'file': 'a.py', 'line': 2, 'category': 'security', 'title': 'Y', 'confidence': 0.95, 'severity': 'critical', 'evidence': '', 'recommendation': '', 'rule_id': 'S-2'},
    ]
    f, w = dedup_findings(findings)
    assert len(f) == 2


def test_dedup_low_confidence_to_warnings():
    findings = [
        {'file': 'a.py', 'line': 1, 'category': 'testing', 'title': 'T', 'confidence': 0.70, 'severity': 'low', 'evidence': '', 'recommendation': '', 'rule_id': 'T-1'},
    ]
    f, w = dedup_findings(findings)
    assert len(f) == 0
    assert len(w) == 1


# ===========================================================
# Redaction Tests
# ===========================================================

def test_redact_password():
    text = 'password = "admin123"'
    result = redact_text(text)
    assert 'admin123' not in result
    assert 'REDACTED' in result


def test_redact_api_key():
    text = 'API_KEY = "sk-abc123def456"'
    result = redact_text(text)
    assert 'sk-abc123def456' not in result
    assert 'REDACTED' in result


def test_redact_github_token():
    text = 'export GITHUB_TOKEN=ghp_1234567890abcdefghijklmn'
    result = redact_text(text)
    assert 'ghp_' not in result


def test_redact_findings_clears_sensitive():
    findings = [
        {'title': 'Test', 'evidence': 'password = "secret123"', 'recommendation': 'Use env var for API_KEY=sk-proj-abcdef123456', 'file': 'a.py', 'line': 1},
    ]
    result = redact_findings(findings)
    assert 'secret123' not in result[0]['evidence']
    assert 'sk-proj-abcdef123456' not in result[0]['recommendation']


def test_check_sensitive_detects():
    detections = check_sensitive('password = "admin123"')
    assert len(detections) >= 1
    assert detections[0]['type'] == 'password'


# ===========================================================
# Filter Tests
# ===========================================================

def test_filter_blocks_dangerous():
    findings = [
        {'evidence': 'rm -rf /', 'severity': 'critical', 'category': 'security', 'file': 'x.sh', 'line': 1},
    ]
    blocked, needs_review, allowed = check_dangerous(findings)
    assert len(blocked) == 1
    assert blocked[0]['filter_action'] == 'deny'
    assert len(needs_review) == 0


def test_filter_allows_safe():
    findings = [
        {'evidence': 'password = "test"', 'severity': 'high', 'category': 'security', 'file': 'a.py', 'line': 1},
    ]
    blocked, needs_review, allowed = check_dangerous(findings)
    assert len(blocked) == 0
    assert len(allowed) == 1
    assert allowed[0]['filter_action'] == 'allow'


def test_filter_flags_sudo_as_ask():
    findings = [
        {'evidence': 'sudo apt-get update', 'severity': 'high', 'category': 'security', 'file': 'x.sh', 'line': 1},
    ]
    blocked, needs_review, allowed = check_dangerous(findings)
    assert len(blocked) == 0
    assert len(needs_review) >= 1
    assert needs_review[0]['filter_action'] == 'ask'


def test_filter_flags_pip_install_as_review():
    findings = [
        {'evidence': 'pip install requests', 'severity': 'medium', 'category': 'resource_leak', 'file': 'x.sh', 'line': 1},
    ]
    blocked, needs_review, allowed = check_dangerous(findings)
    assert len(blocked) == 0
    assert len(needs_review) >= 1
    assert needs_review[0]['filter_action'] == 'needs_human_review'


# ===========================================================
# Sandbox Tests
# ===========================================================

def test_sandbox_handles_failure():
    runner = SandboxRunner(timeout=1)
    result = runner.run_script('nonexistent_script.py')
    assert result['exit_code'] != 0 or result['stderr']


def test_sandbox_timeout():
    import time
    # Create a script that runs too long
    script = Path(tempfile.gettempdir()) / '_test_sleep.py'
    script.write_text('import time\ntime.sleep(10)\nprint("done")')
    runner = SandboxRunner(timeout=1)
    result = runner.run_script(str(script))
    assert result['timed_out'] or result['exit_code'] != 0
    script.unlink(missing_ok=True)


# ===========================================================
# Storage Tests
# ===========================================================

def test_storage_create_and_query():
    store = ReviewStore(':memory:')
    task_id = 'test-task-001'
    store.create_task(task_id)
    task = store.get_task(task_id)
    assert task is not None
    assert task['status'] == 'running'
    store.close()


def test_storage_save_findings():
    store = ReviewStore(':memory:')
    task_id = 'test-finding-002'
    store.create_task(task_id)
    store.save_findings(task_id, [
        {'severity': 'critical', 'category': 'security', 'file': 'a.py', 'line': 1, 'title': 'X', 'evidence': 'e', 'recommendation': 'r', 'confidence': 0.95, 'rule_id': 'S-1'},
    ])
    findings = store.get_findings(task_id)
    assert len(findings) == 1
    store.close()


def test_storage_complete_task():
    store = ReviewStore(':memory:')
    task_id = 'test-complete-003'
    store.create_task(task_id)
    store.complete_task(task_id, 100, 3, 50)
    task = store.get_task(task_id)
    assert task['status'] == 'completed'
    assert task['total_duration_ms'] == 100
    store.close()


def test_storage_full_lifecycle():
    store = ReviewStore(':memory:')
    task_id = 'test-lifecycle-004'
    store.create_task(task_id)
    store.save_findings(task_id, [
        {'severity': 'high', 'category': 'security', 'file': 'x.py', 'line': 1, 'title': 'Issue', 'evidence': 'e', 'recommendation': 'r', 'confidence': 0.95, 'rule_id': 'R-1'},
    ])
    store.save_sandbox_run(task_id, {'script': 'test.py', 'exit_code': 0, 'stdout': 'ok', 'stderr': '', 'duration_ms': 50, 'timed_out': False})
    store.save_filter_decision(task_id, 'allow', 'safe', 'No risk detected')
    store.save_monitoring(task_id, {'total_duration_ms': 200, 'finding_count': 1, 'severity_distribution': {'high': 1}})

    details = store.get_task_details(task_id)
    assert details['task'] is not None
    assert len(details['findings']) == 1
    assert len(details['sandbox_runs']) == 1
    assert len(details['filter_decisions']) == 1
    store.close()


# ===========================================================
# Report Tests
# ===========================================================

def test_json_report_generation():
    data = {
        'task_id': 'test-001',
        'findings': [],
        'warnings': [],
        'monitoring': {'file_count': 1, 'total_added_lines': 10, 'total_duration_ms': 100},
    }
    report = generate_json_report(data)
    assert '"task_id"' in report
    report_dict = json.loads(report)
    assert report_dict['summary']['total_findings'] == 0


def test_markdown_report_generation():
    data = {
        'task_id': 'test-002',
        'findings': [
            {'severity': 'critical', 'category': 'security', 'file': 'a.py', 'line': 1, 'title': 'Hardcoded Secret', 'evidence': 'pwd = "x"', 'recommendation': 'Use env', 'confidence': 0.95, 'rule_id': 'SEC-001'},
        ],
        'warnings': [],
        'monitoring': {'file_count': 1, 'total_added_lines': 5, 'total_duration_ms': 50, 'severity_distribution': {'critical': 1, 'high': 0, 'medium': 0, 'low': 0}},
    }
    report = generate_markdown_report(data)
    assert 'Code Review Report' in report
    assert 'SEC-001' in report


def test_markdown_report_empty():
    data = {
        'task_id': 'test-003',
        'findings': [],
        'warnings': [],
        'monitoring': {'file_count': 1, 'total_added_lines': 3, 'total_duration_ms': 10, 'severity_distribution': {}},
    }
    report = generate_markdown_report(data)
    assert 'No issues detected' in report


# ===========================================================
# Integration Tests (8 fixtures)
# ===========================================================

@pytest.mark.parametrize('fixture_name,expected', [
    ('clean.diff', {'min_critical': 0, 'test_category': 'testing'}),
    ('security.diff', {'min_critical': 3, 'test_category': 'security'}),
    ('resource_leak.diff', {'min_critical': 1, 'test_category': 'resource_leak'}),
    ('db_lifecycle.diff', {'min_critical': 1, 'test_category': 'resource_leak'}),
    ('missing_test.diff', {'test_category': 'testing'}),
    ('duplicate.diff', {'min_critical': 1, 'test_category': 'security'}),
    ('sandbox_fail.diff', {}),
    ('sensitive_info.diff', {'min_critical': 2, 'test_category': 'security'}),
])
def test_fixture_review(fixture_name, expected):
    """Test each of the 8 diff fixtures produces valid output."""
    diff_text = load_fixture(fixture_name)
    parsed = parse_diff(diff_text)
    findings = run_rules(parsed)
    findings, warnings = dedup_findings(findings)
    findings = redact_findings(findings)

    # All findings must have required fields
    for f in findings:
        for key in ['severity', 'category', 'file', 'line', 'title', 'evidence', 'recommendation', 'confidence', 'rule_id']:
            assert key in f, f'Missing field {key} in finding'

    if 'min_critical' in expected:
        critical_count = sum(1 for f in findings if f['severity'] == 'critical')
        assert critical_count >= expected['min_critical'], \
            f'{fixture_name}: expected >= {expected["min_critical"]} critical, got {critical_count}'

    if 'test_category' in expected:
        categories = set(f['category'] for f in (findings + warnings))
        assert expected['test_category'] in categories, \
            f'{fixture_name}: expected category {expected["test_category"]}, got {categories}'
