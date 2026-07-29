#
# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#

"""Acceptance tests for A7 AST scope and degradation behavior."""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_ROOT = PROJECT_ROOT / "skills" / "code-review" / "scripts"
sys.path.insert(0, str(SCRIPTS_ROOT))

from lib.diff_parser import build_snapshot_change_set, parse_unified_diff  # noqa: E402
from lib.rule_engine import RuleEngine  # noqa: E402
from lib.rules_ast import default_ast_rules  # noqa: E402


def _ast_engine() -> RuleEngine:
    return RuleEngine(default_ast_rules())


def _added_file_diff(*lines: str, filename: str = "src/example.py") -> str:
    header = [
        f"diff --git a/{filename} b/{filename}",
        "new file mode 100644",
        "--- /dev/null",
        f"+++ b/{filename}",
        f"@@ -0,0 +1,{len(lines)} @@",
    ]
    return "\n".join([*header, *(f"+{line}" for line in lines)])


def _complete_modified_change_set():
    """Represent a repo input where full text is available after staging."""
    diff = "\n".join(
        [
            "diff --git a/src/example.py b/src/example.py",
            "--- a/src/example.py",
            "+++ b/src/example.py",
            "@@ -1,3 +1,3 @@",
            " def process(value):",
            "     legacy = eval(value)",
            "-    return value",
            "+    return exec(value)",
        ]
    )
    parsed = parse_unified_diff(diff)
    file_change = replace(
        parsed.files[0],
        full_text=(
            "def process(value):\n"
            "    legacy = eval(value)\n"
            "    return exec(value)\n"
        ),
        analysis_mode="ast_validated",
    )
    return replace(parsed, files=(file_change,))


def test_changed_lines_only_report_ast_nodes_intersecting_new_lines() -> None:
    change_set = _complete_modified_change_set()

    matches = _ast_engine().match(change_set)

    assert [(match.rule_id, match.line, match.source) for match in matches] == [
        ("security.dynamic-exec", 3, "ast"),
    ]
    assert matches[0].confidence >= 0.90
    assert matches[0].line in change_set.files[0].new_changed_lines


def test_full_file_snapshot_reports_historical_ast_nodes() -> None:
    change_set = build_snapshot_change_set(
        {
            "src/example.py": (
                "def process(value):\n"
                "    legacy = eval(value)\n"
                "    return value\n"
            )
        }
    )

    matches = _ast_engine().match(change_set)

    assert [(match.rule_id, match.line, match.source) for match in matches] == [
        ("security.dynamic-eval", 2, "ast"),
    ]
    assert change_set.files[0].review_scope == "full_file"


def test_added_file_uses_ast_and_reports_all_supported_security_patterns() -> None:
    change_set = parse_unified_diff(
        _added_file_diff(
            "import os",
            "import subprocess",
            "def unsafe(value, command):",
            "    eval(value)",
            "    exec(value)",
            "    os.system(command)",
            "    subprocess.run(command, shell=True)",
            '    return f"SELECT * FROM users WHERE id = {value}"',
        )
    )

    matches = _ast_engine().match(change_set)

    assert change_set.files[0].analysis_mode == "ast_validated"
    assert {match.rule_id for match in matches} == {
        "security.dynamic-eval",
        "security.dynamic-exec",
        "security.os-system",
        "security.sql-fstring",
        "security.subprocess-shell-true",
    }
    assert all(match.source == "ast" for match in matches)


def test_ast_detects_supported_dangerous_api_variants() -> None:
    """验证完整文件 AST 能识别无需数据流推断的危险 API 变体。"""

    change_set = build_snapshot_change_set(
        {
            "src/variants.py": (
                "import builtins\n"
                "import os\n"
                "import subprocess\n"
                "from os import system as run_system\n"
                "def unsafe(payload, command):\n"
                "    builtins.eval(payload)\n"
                "    getattr(builtins, 'eval')(payload)\n"
                "    run_system(command)\n"
                "    os.popen(command)\n"
                "    subprocess.getoutput(command)\n"
                "    subprocess.getstatusoutput(command)\n"
            )
        }
    )

    matches = _ast_engine().match(change_set)

    assert [(match.rule_id, match.line) for match in matches] == [
        ("security.dynamic-eval", 6),
        ("security.dynamic-eval", 7),
        ("security.os-system", 8),
        ("security.os-popen", 9),
        ("security.subprocess-shell-command", 10),
        ("security.subprocess-shell-command", 11),
    ]


def test_ast_detects_dynamic_sql_without_flagging_safe_formatting() -> None:
    """验证 AST 检出三种动态 SQL，同时忽略参数化 SQL 和普通格式化。"""

    change_set = build_snapshot_change_set(
        {
            "src/sql_variants.py": (
                "def build(user_id, name, cursor):\n"
                '    query_concat = "SELECT * FROM users WHERE id = " + user_id\n'
                '    query_format = "DELETE FROM users WHERE id = {}".format(user_id)\n'
                '    query_percent = "UPDATE users SET name = \'%s\'" % name\n'
                '    safe = "SELECT * FROM users WHERE id = %s"\n'
                "    cursor.execute(safe, (user_id,))\n"
                '    message = "selected user {}".format(user_id)\n'
            )
        }
    )

    matches = _ast_engine().match(change_set)

    assert [(match.rule_id, match.line) for match in matches] == [
        ("security.sql-interpolation", 2),
        ("security.sql-interpolation", 3),
        ("security.sql-interpolation", 4),
    ]


def test_ast_api_variants_ignore_shadowed_modules_and_reassigned_aliases() -> None:
    """验证同名参数和已重绑导入别名不会产生标准库 API 误报。"""

    change_set = build_snapshot_change_set(
        {
            "src/safe_variants.py": (
                "import builtins\n"
                "import os\n"
                "import subprocess\n"
                "from os import system as run_system\n"
                "run_system = safe_runner\n"
                "def safe(builtins, os, subprocess, getattr, payload, command):\n"
                "    builtins.eval(payload)\n"
                "    getattr(builtins, 'eval')(payload)\n"
                "    os.popen(command)\n"
                "    subprocess.getoutput(command)\n"
                "run_system(command)\n"
            )
        }
    )

    assert _ast_engine().match(change_set) == ()


def test_deleted_file_does_not_run_ast_code_rules() -> None:
    diff = "\n".join(
        [
            "diff --git a/src/removed.py b/src/removed.py",
            "deleted file mode 100644",
            "--- a/src/removed.py",
            "+++ /dev/null",
            "@@ -1,2 +0,0 @@",
            "-def unsafe(value):",
            "-    return eval(value)",
        ]
    )

    assert _ast_engine().match(parse_unified_diff(diff)) == ()


def test_incomplete_diff_never_attempts_ast_parse() -> None:
    diff = "\n".join(
        [
            "diff --git a/src/service.py b/src/service.py",
            "--- a/src/service.py",
            "+++ b/src/service.py",
            "@@ -10 +10 @@",
            "-    return value",
            "+    return eval(value)",
        ]
    )
    change_set = parse_unified_diff(diff)

    assert change_set.files[0].full_text is None
    with patch("lib.rules_ast.ast.parse") as parse:
        assert _ast_engine().match(change_set) == ()
    parse.assert_not_called()


def test_syntax_error_downgrades_and_records_a_sanitized_parse_warning() -> None:
    change_set = build_snapshot_change_set(
        {"src/broken.py": "def broken(\n    return eval(value)\n"}
    )

    assert change_set.files[0].analysis_mode == "diff_heuristic"
    assert change_set.parse_warnings == ("ast_parse_failed:src/broken.py",)
    assert _ast_engine().match(change_set) == ()


def test_ast_rules_declare_full_file_requirement() -> None:
    rules = default_ast_rules()

    assert len(rules) == 1
    assert rules[0].requires_full_file is True
    assert rules[0].category == "security"
    assert 0.90 <= rules[0].confidence <= 1.00
