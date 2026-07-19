"""Foundation tests for the skills code review example."""

from __future__ import annotations

from pathlib import Path

import pytest

from examples.skills_code_review_agent.agent.agent import run_review_task
from examples.skills_code_review_agent.agent.config import ReviewAgentConfig
from examples.skills_code_review_agent.run_agent import parse_args
from examples.skills_code_review_agent.src.deduper import dedupe_and_classify_findings
from examples.skills_code_review_agent.src.diff_parser import parse_unified_diff
from examples.skills_code_review_agent.src.input_loader import load_review_input
from examples.skills_code_review_agent.src.rule_engine import run_rule_engine
from examples.skills_code_review_agent.src.review_types import (
    DiffLineType,
    FindingDisposition,
    FindingSource,
    ReviewCategory,
    ReviewConclusion,
    ReviewFinding,
    ReviewInputKind,
    ReviewSeverity,
    ReviewStatus,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def test_parse_unified_diff_extracts_files_hunks_and_candidates() -> None:
    """The parser should extract stable structures from unified diff text."""

    diff_text = """diff --git a/app.py b/app.py
index 1111111..2222222 100644
--- a/app.py
+++ b/app.py
@@ -8,4 +8,5 @@ def run():
     client = make_client()
-    return client.fetch()
+    result = client.fetch()
+    return result
 
 def done():
"""

    parsed = parse_unified_diff(diff_text)

    assert parsed.changed_files_count == 1
    assert parsed.added_lines_count == 2
    assert parsed.deleted_lines_count == 1
    assert parsed.changed_paths == ["app.py"]

    changed_file = parsed.files[0]
    assert changed_file.display_path == "app.py"
    assert changed_file.added_line_numbers == [9, 10]
    assert changed_file.deleted_line_numbers == [9]
    assert changed_file.candidate_line_numbers(radius=1) == [8, 9, 10, 11]

    hunk = changed_file.hunks[0]
    assert hunk.added_line_numbers == [9, 10]
    assert hunk.deleted_line_numbers == [9]
    assert hunk.lines[1].line_type == DiffLineType.DELETE
    assert hunk.lines[2].line_type == DiffLineType.ADD


def test_parse_unified_diff_supports_patch_without_diff_git_header() -> None:
    """Some patch sources omit the `diff --git` header and should still parse."""

    diff_text = """--- a/config.py
+++ b/config.py
@@ -1,2 +1,3 @@
 DEBUG = False
+API_TOKEN = "masked"
 TIMEOUT = 10
"""

    parsed = parse_unified_diff(diff_text)

    assert parsed.changed_files_count == 1
    assert parsed.files[0].display_path == "config.py"
    assert parsed.files[0].added_line_numbers == [2]


def test_load_review_input_reads_diff_file(tmp_path: Path) -> None:
    """Loading a diff file should produce a normalized ReviewInput."""

    diff_path = tmp_path / "sample.diff"
    diff_path.write_text("diff --git a/a.py b/a.py\n", encoding="utf-8")

    review_input = load_review_input(diff_file=diff_path)

    assert review_input.kind == ReviewInputKind.DIFF_FILE
    assert review_input.source == str(diff_path.resolve())
    assert review_input.diff_text == "diff --git a/a.py b/a.py\n"
    assert review_input.repo_path is None


def test_load_review_input_requires_exactly_one_source(tmp_path: Path) -> None:
    """The loader should reject ambiguous or empty inputs."""

    diff_path = tmp_path / "sample.diff"
    diff_path.write_text("content", encoding="utf-8")

    with pytest.raises(ValueError):
        load_review_input()

    with pytest.raises(ValueError):
        load_review_input(diff_file=diff_path, fixture_path=diff_path)


def test_parse_args_reads_diff_file_mode() -> None:
    """The CLI parser should map diff mode arguments into a namespace."""

    args = parse_args(
        [
            "--diff-file",
            "tests/example.diff",
            "--output-dir",
            "outputs",
            "--db-path",
            "review.db",
            "--runtime",
            "local",
            "--dry-run",
            "--fake-model",
        ]
    )

    assert args.diff_file == "tests/example.diff"
    assert args.repo_path is None
    assert args.fixture is None
    assert args.output_dir == "outputs"
    assert args.db_path == "review.db"
    assert args.runtime == "local"
    assert args.dry_run is True
    assert args.fake_model is True


def test_run_review_task_surfaces_missing_tests_for_code_only_change(tmp_path: Path) -> None:
    """Main orchestration should surface a human-review item for missing tests."""

    diff_path = tmp_path / "sample.diff"
    diff_path.write_text(
        """diff --git a/main.py b/main.py
--- a/main.py
+++ b/main.py
@@ -1 +1,2 @@
 print("hello")
+print("world")
""",
        encoding="utf-8",
    )

    config = ReviewAgentConfig(
        diff_file=str(diff_path),
        output_dir=tmp_path / "outputs",
        db_path=tmp_path / "review.db",
        dry_run=True,
        fake_model=True,
    )

    task, report = run_review_task(config)

    assert task.status == ReviewStatus.COMPLETED
    assert task.parsed_diff is not None
    assert task.parsed_diff.changed_paths == ["main.py"]
    assert report.task_id == task.task_id
    assert report.conclusion == ReviewConclusion.NEEDS_HUMAN_REVIEW
    assert report.monitoring_summary["changed_files_count"] == 1
    assert report.monitoring_summary["needs_human_review_count"] == 1


def test_rule_engine_returns_no_findings_for_clean_fixture() -> None:
    """Clean fixture should not trigger deterministic findings."""

    parsed = parse_unified_diff((FIXTURES_DIR / "clean.diff").read_text(encoding="utf-8"))
    findings = dedupe_and_classify_findings(run_rule_engine(parsed))

    assert findings == []


def test_rule_engine_detects_security_patterns() -> None:
    """Security fixture should trigger high-confidence security findings."""

    parsed = parse_unified_diff((FIXTURES_DIR / "security_issue.diff").read_text(encoding="utf-8"))
    findings = dedupe_and_classify_findings(run_rule_engine(parsed))

    security_findings = [item for item in findings if item.category == ReviewCategory.SECURITY]
    assert len(security_findings) >= 2
    assert all(item.disposition == FindingDisposition.FINDING for item in security_findings)


def test_rule_engine_detects_async_and_resource_leak_patterns() -> None:
    """Async/resource fixture should emit lower-confidence review items."""

    parsed = parse_unified_diff(
        (FIXTURES_DIR / "async_resource_leak.diff").read_text(encoding="utf-8")
    )
    findings = dedupe_and_classify_findings(run_rule_engine(parsed))

    categories = {item.category for item in findings}
    assert ReviewCategory.ASYNC in categories
    assert ReviewCategory.RESOURCE_LEAK in categories
    assert all(
        item.disposition == FindingDisposition.NEEDS_HUMAN_REVIEW
        for item in findings
        if item.category in {ReviewCategory.ASYNC, ReviewCategory.RESOURCE_LEAK}
    )


def test_rule_engine_detects_db_lifecycle_pattern() -> None:
    """DB lifecycle fixture should raise at least one DB lifecycle finding."""

    parsed = parse_unified_diff(
        (FIXTURES_DIR / "db_lifecycle_issue.diff").read_text(encoding="utf-8")
    )
    findings = dedupe_and_classify_findings(run_rule_engine(parsed))

    db_findings = [item for item in findings if item.category == ReviewCategory.DB_LIFECYCLE]
    assert db_findings
    assert any(item.disposition == FindingDisposition.FINDING for item in db_findings)


def test_rule_engine_detects_secret_patterns() -> None:
    """Secret fixture should emit at least one high-confidence secret finding."""

    parsed = parse_unified_diff(
        (FIXTURES_DIR / "secret_redaction.diff").read_text(encoding="utf-8")
    )
    findings = dedupe_and_classify_findings(run_rule_engine(parsed))

    secret_findings = [item for item in findings if item.category == ReviewCategory.SECRET]
    assert secret_findings
    assert any(item.disposition == FindingDisposition.FINDING for item in secret_findings)


def test_rule_engine_detects_missing_tests_as_human_review_item() -> None:
    """Changing production code without tests should request human review."""

    parsed = parse_unified_diff((FIXTURES_DIR / "missing_tests.diff").read_text(encoding="utf-8"))
    findings = dedupe_and_classify_findings(run_rule_engine(parsed))

    assert len(findings) == 1
    assert findings[0].category == ReviewCategory.TEST_MISSING
    assert findings[0].disposition == FindingDisposition.NEEDS_HUMAN_REVIEW


def test_deduper_collapses_duplicate_matches_on_same_line() -> None:
    """Duplicate rule hits for the same issue should collapse into one record."""

    duplicate_one = ReviewFinding(
        severity=ReviewSeverity.HIGH,
        category=ReviewCategory.SECRET,
        file="src/settings.py",
        line=2,
        title="Hard-coded secret detected",
        evidence='+API_TOKEN = "super-secret-token"',
        recommendation="Move the token to a secret manager.",
        confidence=0.95,
        source=FindingSource.RULE_ENGINE,
    )
    duplicate_two = ReviewFinding(
        severity=ReviewSeverity.HIGH,
        category=ReviewCategory.SECRET,
        file="src/settings.py",
        line=2,
        title="Hard-coded secret detected",
        evidence='+API_TOKEN   =   "super-secret-token"',
        recommendation="Move the token to a secret manager.",
        confidence=0.90,
        source=duplicate_one.source,
    )

    findings = dedupe_and_classify_findings([duplicate_one, duplicate_two])

    assert len(findings) == 1
    assert findings[0].fingerprint is not None


def test_run_review_task_with_security_fixture_returns_failure() -> None:
    """Main orchestration should fail the review when high-confidence findings exist."""

    config = ReviewAgentConfig(
        fixture_path=str(FIXTURES_DIR / "security_issue.diff"),
        output_dir=FIXTURES_DIR.parent / "outputs",
        db_path=FIXTURES_DIR.parent / "review.db",
        dry_run=True,
        fake_model=True,
    )

    task, report = run_review_task(config)

    assert task.status == ReviewStatus.COMPLETED
    assert report.conclusion == ReviewConclusion.FAIL
    assert any(item.category == ReviewCategory.SECURITY for item in report.findings)
