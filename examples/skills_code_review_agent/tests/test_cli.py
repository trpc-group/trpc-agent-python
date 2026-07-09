"""Tests for CLI entry point — run_review.py main() integration.

Tests the CLI argument parsing and main() execution paths.
"""

import os
import sys
import subprocess

import pytest


def _run_review_cli(*args) -> subprocess.CompletedProcess:
    """Run the review CLI and return the completed process."""
    script = os.path.join(
        os.path.dirname(__file__), "..", "run_review.py"
    )
    return subprocess.run(
        [sys.executable, script, *args],
        capture_output=True,
        text=True,
        timeout=30,
    )


class TestCLIBasic:
    """Basic CLI argument tests."""

    def test_help_output(self):
        result = _run_review_cli("--help")
        assert result.returncode == 0
        assert "Code Review" in result.stdout

    def test_no_args_error(self):
        result = _run_review_cli()
        assert result.returncode != 0

    def test_nonexistent_diff_file(self):
        result = _run_review_cli("--diff-file", "nonexistent.diff")
        assert result.returncode != 0

    def test_fixture_security(self):
        fixture = os.path.join(
            os.path.dirname(__file__), "..", "fixtures", "diffs", "security.diff"
        )
        result = _run_review_cli(
            "--diff-file", fixture,
            "--output-dir", os.path.join(os.path.dirname(__file__), ".."),
            "--dry-run",
        )
        assert result.returncode == 0

    def test_fixture_clean(self):
        fixture = os.path.join(
            os.path.dirname(__file__), "..", "fixtures", "diffs", "clean.diff"
        )
        result = _run_review_cli(
            "--diff-file", fixture,
            "--output-dir", os.path.join(os.path.dirname(__file__), ".."),
            "--dry-run",
        )
        # Clean diff may have no changes
        assert result.returncode in (0, 1)


class TestCLIOutputFiles:
    """Test that CLI produces expected output files."""

    def test_generates_json_report(self, tmp_path):
        fixture = os.path.join(
            os.path.dirname(__file__), "..", "fixtures", "diffs", "security.diff"
        )
        result = _run_review_cli(
            "--diff-file", fixture,
            "--output-dir", str(tmp_path),
            "--db-path", str(tmp_path / "test.db"),
            "--dry-run",
        )
        assert result.returncode == 0
        assert (tmp_path / "review_report.json").exists()

    def test_generates_md_report(self, tmp_path):
        fixture = os.path.join(
            os.path.dirname(__file__), "..", "fixtures", "diffs", "security.diff"
        )
        result = _run_review_cli(
            "--diff-file", fixture,
            "--output-dir", str(tmp_path),
            "--db-path", str(tmp_path / "test.db"),
            "--dry-run",
        )
        assert result.returncode == 0
        assert (tmp_path / "review_report.md").exists()

    def test_db_created(self, tmp_path):
        fixture = os.path.join(
            os.path.dirname(__file__), "..", "fixtures", "diffs", "security.diff"
        )
        db_path = tmp_path / "review.db"
        result = _run_review_cli(
            "--diff-file", fixture,
            "--output-dir", str(tmp_path),
            "--db-path", str(db_path),
            "--dry-run",
        )
        assert result.returncode == 0
        assert db_path.exists()


class TestCLIVerbose:
    """Tests for verbose output mode."""

    def test_verbose_flag_accepted(self):
        fixture = os.path.join(
            os.path.dirname(__file__), "..", "fixtures", "diffs", "clean.diff"
        )
        result = _run_review_cli(
            "--diff-file", fixture,
            "--output-dir", os.path.join(os.path.dirname(__file__), ".."),
            "--verbose",
            "--dry-run",
        )
        # Should work without crashing
        assert result.returncode in (0, 1)


class TestCLIAllFixtures:
    """Test CLI against all 8 fixture diffs."""

    FIXTURES = [
        "clean", "security", "async_resource_leak", "db_lifecycle",
        "missing_tests", "duplicate_finding", "sandbox_failure", "secret_redaction",
    ]

    @pytest.mark.parametrize("fixture_name", FIXTURES)
    def test_fixture_runs(self, fixture_name, tmp_path):
        fixture = os.path.join(
            os.path.dirname(__file__), "..", "fixtures", "diffs", f"{fixture_name}.diff"
        )
        db_path = tmp_path / f"{fixture_name}.db"
        result = _run_review_cli(
            "--diff-file", fixture,
            "--output-dir", str(tmp_path),
            "--db-path", str(db_path),
            "--dry-run",
        )
        assert result.returncode in (0, 1)
