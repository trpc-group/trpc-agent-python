#
# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#

"""Tests for the deterministic rule protocol and security rules."""

from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_ROOT = PROJECT_ROOT / "skills" / "code-review" / "scripts"
sys.path.insert(0, str(SCRIPTS_ROOT))

from lib.diff_parser import build_snapshot_change_set, parse_unified_diff  # noqa: E402
from lib.rule_engine import RuleEngine  # noqa: E402
from lib.rules_async import default_async_rules  # noqa: E402
from lib.rules_db import default_db_rules  # noqa: E402
from lib.rules_resource import default_resource_rules  # noqa: E402
from lib.rules_security import default_security_rules  # noqa: E402
from lib.rules_tests import default_test_rules  # noqa: E402


def _added_python_change_set(*lines: str):
    diff_lines = [
        "diff --git a/src/example.py b/src/example.py",
        "new file mode 100644",
        "--- /dev/null",
        "+++ b/src/example.py",
        f"@@ -0,0 +1,{len(lines)} @@",
    ]
    diff_lines.extend(f"+{line}" for line in lines)
    return parse_unified_diff("\n".join(diff_lines))


def _engine() -> RuleEngine:
    return RuleEngine(default_security_rules())


def _full_engine() -> RuleEngine:
    return RuleEngine(
        (
            *default_security_rules(),
            *default_async_rules(),
            *default_resource_rules(),
        )
    )


def _a6_engine() -> RuleEngine:
    return RuleEngine(
        (
            *default_security_rules(),
            *default_async_rules(),
            *default_resource_rules(),
            *default_db_rules(),
            *default_test_rules(),
        )
    )


def test_security_rules_expose_protocol_metadata_and_detect_dangerous_code() -> None:
    change_set = _added_python_change_set(
        'query = f"SELECT * FROM users WHERE name = \'{name}\'"',
        "subprocess.run(command, shell=True)",
        "result = eval(user_input)",
        "exec(compiled_code)",
        "os.system(command)",
    )

    matches = _engine().match(change_set)

    assert all(
        rule.rule_id
        and rule.category
        and rule.severity
        and 0.0 <= rule.confidence <= 1.0
        and isinstance(rule.requires_full_file, bool)
        for rule in _engine().rules
    )
    assert [match.rule_id for match in matches] == [
        "security.sql-fstring",
        "security.subprocess-shell-true",
        "security.dynamic-eval",
        "security.dynamic-exec",
        "security.os-system",
    ]
    assert all(match.category == "security" for match in matches)
    assert {match.severity for match in matches} == {"high", "critical"}
    assert all(0.70 <= match.confidence <= 0.85 for match in matches)
    assert all(match.source == "heuristic" for match in matches)
    assert [match.line for match in matches] == [1, 2, 3, 4, 5]
    assert all(match.line_side == "new" for match in matches)


def test_security_rules_ignore_comments_docstrings_and_ordinary_strings() -> None:
    change_set = _added_python_change_set(
        "# subprocess.run(command, shell=True); eval(user_input)",
        'description = "os.system(command); exec(payload)"',
        'documentation = "query = f\'SELECT * FROM users WHERE id = {user_id}\'"',
        '"""eval(user_input); subprocess.run(command, shell=True)"""',
        '"""',
        "exec(payload)",
        '"""',
    )

    matches = _engine().match(change_set)

    assert matches == ()


def test_security_rules_only_report_new_changed_lines() -> None:
    diff = "\n".join(
        [
            "diff --git a/src/example.py b/src/example.py",
            "--- a/src/example.py",
            "+++ b/src/example.py",
            "@@ -10,2 +10,2 @@",
            "-result = eval(user_input)",
            "+result = sanitize(user_input)",
            " context = 'exec(payload)'",
        ]
    )

    assert _engine().match(parse_unified_diff(diff)) == ()


def test_security_heuristics_detect_qualified_shell_and_eval_variants() -> None:
    """验证纯 diff 行级规则识别可由限定名直接确认的危险调用。"""

    change_set = _added_python_change_set(
        "import builtins",
        "import os",
        "import subprocess",
        "builtins.eval(payload)",
        "os.popen(command)",
        "subprocess.getoutput(command)",
        "subprocess.getstatusoutput(command)",
    )

    matches = _engine().match(change_set)

    assert [(match.rule_id, match.line) for match in matches] == [
        ("security.dynamic-eval", 4),
        ("security.os-popen", 5),
        ("security.subprocess-shell-command", 6),
        ("security.subprocess-shell-command", 7),
    ]


def test_security_heuristic_variants_ignore_literals_and_custom_objects() -> None:
    """验证新增限定名规则忽略注释、普通字符串和自定义对象方法。"""

    change_set = _added_python_change_set(
        "# builtins.eval(payload); os.popen(command)",
        'description = "subprocess.getoutput(command)"',
        "client.getoutput(command)",
        "platform.popen(command)",
    )

    assert _engine().match(change_set) == ()


def test_secret_rule_scans_real_format_string_literals_without_structure_filter() -> None:
    token = "ghp_" + "abcdefghijklmnopqrstuvwxyz0123456789"
    change_set = _added_python_change_set(f"GITHUB_TOKEN = '{token}'")

    matches = _engine().match(change_set)

    assert len(matches) == 1
    assert matches[0].rule_id == "secrets.github_token"
    assert matches[0].category == "secrets"
    assert matches[0].line == 1
    assert token not in matches[0].evidence
    assert "[REDACTED:github_token]" in matches[0].evidence


def test_async_rules_detect_blocking_and_unawaited_coroutines() -> None:
    change_set = _added_python_change_set(
        "async def fetch_data():",
        "    return 1",
        "async def handler():",
        "    time.sleep(1)",
        "    fetch_data()",
        "    asyncio.sleep(1)",
    )

    matches = _full_engine().match(change_set)

    assert [(match.rule_id, match.line) for match in matches] == [
        ("async.blocking-time-sleep", 4),
        ("async.unawaited-coroutine", 5),
        ("async.unawaited-coroutine", 6),
    ]
    assert all(match.category == "async-errors" for match in matches)
    assert all(0.60 <= match.confidence <= 0.90 for match in matches)


def test_async_rules_ignore_awaited_and_sync_uses() -> None:
    change_set = _added_python_change_set(
        "def sync_handler():",
        "    time.sleep(1)",
        "async def fetch_data():",
        "    return 1",
        "async def handler():",
        "    await fetch_data()",
        "    await asyncio.sleep(1)",
    )

    assert not [match for match in _full_engine().match(change_set) if match.category == "async-errors"]


def test_async_rules_use_hunk_context_to_identify_async_scope() -> None:
    diff = "\n".join(
        [
            "diff --git a/src/example.py b/src/example.py",
            "--- a/src/example.py",
            "+++ b/src/example.py",
            "@@ -1,2 +1,3 @@",
            " async def handler():",
            "+    time.sleep(1)",
            "     return None",
        ]
    )

    matches = _full_engine().match(parse_unified_diff(diff))

    assert [(match.rule_id, match.line) for match in matches] == [
        ("async.blocking-time-sleep", 2),
    ]


def test_resource_rules_detect_unclosed_open_and_client_session_across_hunk_lines() -> None:
    change_set = _added_python_change_set(
        "def read(path):",
        "    handle = open(path)",
        "    return handle.read()",
        "async def request():",
        "    session = aiohttp.ClientSession()",
        "    return await session.get(url)",
    )

    matches = _full_engine().match(change_set)

    assert [(match.rule_id, match.line) for match in matches] == [
        ("resource.open-without-close", 2),
        ("resource.client-session-without-close", 5),
    ]
    assert all(match.category == "resource-leak" for match in matches)


def test_resource_rules_ignore_context_managed_and_closed_resources() -> None:
    change_set = _added_python_change_set(
        "def read(path):",
        "    with open(path) as handle:",
        "        return handle.read()",
        "def write(path):",
        "    handle = open(path)",
        "    handle.close()",
        "async def request():",
        "    async with aiohttp.ClientSession() as session:",
        "        return await session.get(url)",
        "async def request_with_close():",
        "    session = aiohttp.ClientSession()",
        "    await session.close()",
    )

    assert not [match for match in _full_engine().match(change_set) if match.category == "resource-leak"]


def test_resource_rules_use_hunk_context_for_lifecycle_evidence() -> None:
    diff = "\n".join(
        [
            "diff --git a/src/example.py b/src/example.py",
            "--- a/src/example.py",
            "+++ b/src/example.py",
            "@@ -1,3 +1,4 @@",
            " def read(path):",
            "+    handle = open(path)",
            "     handle.read()",
            "     handle.close()",
        ]
    )

    matches = _full_engine().match(parse_unified_diff(diff))

    assert not [match for match in matches if match.category == "resource-leak"]


def test_db_rules_detect_unclosed_connection_and_unfinalized_transaction() -> None:
    change_set = _added_python_change_set(
        "connection = sqlite3.connect(database_path)",
        "transaction = connection.begin()",
        "transaction.execute(statement)",
    )

    matches = _a6_engine().match(change_set)

    assert [(match.rule_id, match.line) for match in matches] == [
        ("db.connection-without-close", 1),
        ("tests.missing-coverage", 1),
        ("db.transaction-without-finalize", 2),
    ]
    db_matches = [match for match in matches if match.category == "db-lifecycle"]
    assert all(0.60 <= match.confidence <= 0.90 for match in db_matches)


def test_db_rules_ignore_close_commit_and_rollback() -> None:
    change_set = _added_python_change_set(
        "connection = sqlite3.connect(database_path)",
        "transaction = connection.begin()",
        "transaction.commit()",
        "connection.close()",
        "other_transaction = connection.begin()",
        "other_transaction.rollback()",
    )

    assert not [match for match in _a6_engine().match(change_set) if match.category == "db-lifecycle"]


def test_db_rules_use_hunk_context_for_close_and_commit_evidence() -> None:
    diff = "\n".join(
        [
            "diff --git a/src/example.py b/src/example.py",
            "--- a/src/example.py",
            "+++ b/src/example.py",
            "@@ -1,3 +1,5 @@",
            " def write():",
            "+    connection = sqlite3.connect(database_path)",
            "+    transaction = connection.begin()",
            "     transaction.commit()",
            "     connection.close()",
        ]
    )

    assert not [match for match in _a6_engine().match(parse_unified_diff(diff)) if match.category == "db-lifecycle"]


def test_missing_tests_is_low_confidence_change_set_heuristic() -> None:
    change_set = build_snapshot_change_set(
        {"src/service.py": "def calculate(value):\n    return value + 1\n"}
    )

    matches = _a6_engine().match(change_set)

    assert len(matches) == 1
    match = matches[0]
    assert match.rule_id == "tests.missing-coverage"
    assert match.category == "missing-tests"
    assert 0.50 <= match.confidence < 0.80
    assert match.line == 1


def test_missing_tests_is_not_reported_when_a_test_file_changes() -> None:
    change_set = build_snapshot_change_set(
        {
            "src/service.py": "def calculate(value):\n    return value + 1\n",
            "tests/test_service.py": "def test_calculate():\n    assert True\n",
        }
    )

    assert not [match for match in _a6_engine().match(change_set) if match.category == "missing-tests"]
