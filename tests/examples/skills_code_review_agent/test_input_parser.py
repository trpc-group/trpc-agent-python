"""Tests for canonical code-review input and static Skill rules."""

from __future__ import annotations

import json
import runpy
from pathlib import Path

import pytest
from pydantic import ValidationError

from examples.skills_code_review_agent.agent.input_parser import GitDiffOptions
from examples.skills_code_review_agent.agent.input_parser import InputValidationError
from examples.skills_code_review_agent.agent.input_parser import load_diff_file
from examples.skills_code_review_agent.agent.input_parser import load_file_list
from examples.skills_code_review_agent.agent.input_parser import load_repo_diff
from examples.skills_code_review_agent.agent.input_parser import parse_diff_text
from examples.skills_code_review_agent.agent.models import Finding

EXAMPLE_ROOT = Path("examples/skills_code_review_agent")
FIXTURE_ROOT = EXAMPLE_ROOT / "fixtures"
SCANNER_PATH = EXAMPLE_ROOT / "skills/code-review/scripts/scan_rules.py"
SCAN = runpy.run_path(str(SCANNER_PATH))["scan"]


@pytest.mark.parametrize("diff_path", sorted(FIXTURE_ROOT.glob("*.diff")))
def test_all_public_fixtures_parse(diff_path):
    review_input = load_diff_file(diff_path)

    assert review_input.files
    assert len(review_input.digest) == 64


def test_plain_unified_diff_parses_without_git_header():
    review_input = parse_diff_text("--- a/app.py\n"
                                   "+++ b/app.py\n"
                                   "@@ -1,1 +1,1 @@\n"
                                   "-old\n"
                                   "+new\n", )

    assert review_input.files[0].new_path == "app.py"
    assert review_input.files[0].hunks[0].lines[-1].new_line == 1


def test_hunk_count_mismatch_is_rejected():
    malformed = ("diff --git a/app.py b/app.py\n"
                 "--- a/app.py\n"
                 "+++ b/app.py\n"
                 "@@ -1,2 +1,1 @@\n"
                 "-old\n"
                 "+new\n")

    with pytest.raises(InputValidationError, match="hunk count mismatch"):
        parse_diff_text(malformed)


@pytest.mark.parametrize(
    "path",
    [
        "C:/outside.py",
        r"\\server\share\outside.py",
        "../outside.py",
        "/outside.py",
        "safe/\x00bad.py",
    ],
)
def test_finding_rejects_unsafe_paths(path):
    with pytest.raises(ValidationError):
        Finding(
            severity="high",
            category="security",
            file=path,
            line=1,
            title="unsafe",
            evidence="evidence",
            recommendation="fix",
            confidence=0.9,
            source="test",
        )


def test_file_list_digest_includes_file_content(tmp_path):
    source = tmp_path / "app.py"
    source.write_text("value = 1\n", encoding="utf-8")
    file_list = tmp_path / "files.txt"
    file_list.write_text("app.py\n", encoding="utf-8")
    first = load_file_list(file_list)

    source.write_text("value = 2\n", encoding="utf-8")
    second = load_file_list(file_list)

    assert first.digest != second.digest


def test_file_list_rejects_empty_non_utf8_and_symlink(tmp_path):
    empty = tmp_path / "empty.txt"
    empty.write_text("", encoding="utf-8")
    with pytest.raises(InputValidationError, match="must not be empty"):
        load_file_list(empty)

    invalid = tmp_path / "invalid.txt"
    invalid.write_bytes(b"\xff")
    with pytest.raises(InputValidationError, match="UTF-8"):
        load_file_list(invalid)


def test_repo_revision_rejects_option_injection(tmp_path, monkeypatch):
    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(
        "examples.skills_code_review_agent.agent.input_parser._run_git_diff",
        lambda root, arguments: "",
    )

    options = GitDiffOptions(base="main", head="--output=outside")
    with pytest.raises(InputValidationError, match="unsafe Git revision"):
        load_repo_diff(tmp_path, options)


def test_binary_diff_becomes_warning():
    review_input = parse_diff_text(
        "diff --git a/image.png b/image.png\n"
        "Binary files a/image.png and b/image.png differ\n", )

    assert review_input.files[0].is_binary is True
    assert review_input.warnings == ["binary file skipped: image.png"]


@pytest.mark.parametrize(
    ("fixture", "category", "line"),
    [
        ("security", "security", 4),
        ("async_resource", "async_error", 2),
        ("async_resource", "resource_leak", 3),
        ("db_lifecycle", "db_lifecycle", 2),
        ("missing_tests", "missing_test", 2),
        ("secrets", "secret_leak", 2),
    ],
)
def test_scanner_detects_required_rule_categories(fixture, category, line):
    review_input = load_diff_file(FIXTURE_ROOT / f"{fixture}.diff")
    findings = SCAN(review_input.model_dump(mode="json"))

    assert any(item["category"] == category and item["line"] == line for item in findings)


def test_scanner_clean_fixture_has_no_false_positive():
    review_input = load_diff_file(FIXTURE_ROOT / "clean.diff")
    expected = json.loads((FIXTURE_ROOT / "clean.expected.json").read_text())

    assert expected["expected_findings"] == []
    assert SCAN(review_input.model_dump(mode="json")) == []


def test_scanner_redacts_quoted_secret_key_with_example_context():
    secret = "CorrectHorseBatteryStaple"
    review_input = parse_diff_text(
        "diff --git a/config.py b/config.py\n"
        "--- a/config.py\n"
        "+++ b/config.py\n"
        "@@ -0,0 +1 @@\n"
        f'+settings = {{"password": "{secret}"}}  # example config\n', )

    findings = SCAN(review_input.model_dump(mode="json"))

    assert findings[0]["category"] == "secret_leak"
    assert secret not in findings[0]["evidence"]
    assert "[REDACTED]" in findings[0]["evidence"]


def test_scanner_ignores_placeholder_secret_value():
    review_input = parse_diff_text(
        "diff --git a/config.py b/config.py\n"
        "--- a/config.py\n"
        "+++ b/config.py\n"
        "@@ -0,0 +1 @@\n"
        '+url = "https://user:changeme@example.invalid/path"\n', )

    assert SCAN(review_input.model_dump(mode="json")) == []
