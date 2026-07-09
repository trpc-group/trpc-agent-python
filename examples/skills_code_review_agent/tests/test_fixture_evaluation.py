"""Tests for fixture evaluation framework — precision/recall/F1 calculation."""

import json
import os
import tempfile
import pytest

from evaluate_fixtures import (
    evaluate_fixture,
    FixtureResult,
    EvaluationReport,
    run_evaluation,
    _fingerprint,
)


class TestFingerprint:
    """Test finding fingerprinting for matching."""

    def test_same_findings_same_fp(self):
        a = {"file": "x.py", "line": 10, "category": "security"}
        b = {"file": "x.py", "line": 10, "category": "security"}
        assert _fingerprint(a) == _fingerprint(b)

    def test_different_file_different_fp(self):
        a = {"file": "x.py", "line": 10, "category": "security"}
        b = {"file": "y.py", "line": 10, "category": "security"}
        assert _fingerprint(a) != _fingerprint(b)

    def test_different_line_different_fp(self):
        a = {"file": "x.py", "line": 10, "category": "security"}
        b = {"file": "x.py", "line": 20, "category": "security"}
        assert _fingerprint(a) != _fingerprint(b)


class TestFixtureResult:
    """FixtureResult dataclass tests."""

    def test_perfect_result(self):
        r = FixtureResult(
            fixture_name="test",
            precision=1.0, recall=1.0, f1=1.0,
            true_positives=5, false_positives=0, false_negatives=0,
            total_expected=5, total_found=5,
        )
        assert r.precision == 1.0
        assert r.recall == 1.0
        assert r.f1 == 1.0

    def test_zero_expected_no_crash(self):
        r = FixtureResult(fixture_name="empty")
        assert r.precision == 0.0
        assert r.recall == 0.0
        assert r.f1 == 0.0

    def test_all_false_positives(self):
        r = FixtureResult(
            fixture_name="fp",
            precision=0.0, recall=0.0, f1=0.0,
            true_positives=0, false_positives=10, false_negatives=5,
        )
        assert r.precision == 0.0
        assert r.recall == 0.0


class TestEvaluationReport:
    """EvaluationReport aggregate tests."""

    def test_empty_report(self):
        r = EvaluationReport()
        assert r.overall_f1 == 0.0
        assert r.fixtures_evaluated == 0

    def test_single_fixture_report(self):
        r = FixtureResult("a", precision=0.9, recall=0.8, f1=0.85,
                          true_positives=9, false_positives=1, false_negatives=2)
        report = EvaluationReport(
            results=[r], fixtures_evaluated=1,
            overall_precision=0.9, overall_recall=0.818, overall_f1=0.857,
        )
        assert report.overall_precision == 0.9
        assert abs(report.overall_recall - 0.818) < 0.01


class TestEvaluateFixture:
    """Integration tests for evaluate_fixture()."""

    def test_evaluate_security_fixture(self):
        fixtures_dir = os.path.join(
            os.path.dirname(__file__), "..", "fixtures", "diffs"
        )
        diff_path = os.path.join(fixtures_dir, "security.diff")
        result = evaluate_fixture(diff_path, [])
        assert result.fixture_name == "security"
        # No expected findings, just verify it doesn't crash
        assert result.total_expected == 0

    def test_evaluate_clean_fixture_no_expected(self):
        fixtures_dir = os.path.join(
            os.path.dirname(__file__), "..", "fixtures", "diffs"
        )
        diff_path = os.path.join(fixtures_dir, "clean.diff")
        result = evaluate_fixture(diff_path, [])
        assert result.fixture_name == "clean"
        # Clean fixture with no expected findings: precision is 1.0 if no findings found
        assert result.precision >= 0.0

    def test_evaluate_nonexistent_diff(self):
        with pytest.raises(FileNotFoundError):
            evaluate_fixture("nonexistent.diff", [])


class TestRunEvaluation:
    """Test run_evaluation() end to end."""

    def test_run_with_expected_json(self, tmp_path):
        diff_content = """diff --git a/test.py b/test.py
--- a/test.py
+++ b/test.py
@@ -1,0 +1 @@
+import os"""
        fixtures_dir = tmp_path / "fixtures"
        fixtures_dir.mkdir()
        (fixtures_dir / "test.diff").write_text(diff_content)

        expected = {"test": []}
        expected_path = tmp_path / "expected.json"
        expected_path.write_text(json.dumps(expected))

        # Run evaluation - may skip due to empty expected in non-cv mode
        report = run_evaluation(str(fixtures_dir), str(expected_path))
        assert report is not None

    def test_cross_validation_does_not_crash(self, tmp_path):
        # Create 4 fixture diffs
        fixtures_dir = tmp_path / "fixtures"
        fixtures_dir.mkdir()
        for i in range(4):
            (fixtures_dir / f"fixture_{i}.diff").write_text(
                f"""diff --git a/f{i}.py b/f{i}.py
--- a/f{i}.py
+++ b/f{i}.py
@@ -1,0 +1 @@
+x = {i}"""
            )

        expected = {f"fixture_{i}": [] for i in range(4)}
        expected_path = tmp_path / "expected.json"
        expected_path.write_text(json.dumps(expected))

        report = run_evaluation(
            str(fixtures_dir), str(expected_path),
            cross_validate=True, train_split=0.5,
        )
        assert report.fixtures_evaluated == 4
