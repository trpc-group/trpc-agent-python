"""Tests for dedup module."""

from pipeline.dedup import deduplicate, separate_low_confidence
from pipeline.types import Finding, FindingCategory, Severity


def _make_finding(file="test.py", line=1, category=FindingCategory.SECURITY,
                   title="Test finding", confidence=0.9):
    return Finding(
        severity=Severity.HIGH,
        category=category,
        file=file,
        line=line,
        title=title,
        evidence="some code",
        recommendation="fix it",
        confidence=confidence,
        source="test",
    )


class TestDeduplicate:
    """Finding deduplication tests."""

    def test_no_duplicates(self):
        f1 = _make_finding(title="issue A", line=1)
        f2 = _make_finding(title="issue B", line=2)
        result = deduplicate([f1, f2])
        assert len(result) == 2

    def test_duplicates_removed(self):
        f1 = _make_finding(title="same issue", line=1)
        f2 = _make_finding(title="same issue", line=1)
        result = deduplicate([f1, f2])
        assert len(result) == 1

    def test_duplicate_keeps_higher_confidence(self):
        f1 = _make_finding(title="same issue", line=1, confidence=0.5)
        f2 = _make_finding(title="same issue", line=1, confidence=0.9)
        result = deduplicate([f1, f2])
        assert result[0].confidence == 0.9

    def test_different_files_not_duplicates(self):
        f1 = _make_finding(file="a.py", title="same", line=1)
        f2 = _make_finding(file="b.py", title="same", line=1)
        result = deduplicate([f1, f2])
        assert len(result) == 2

    def test_different_categories_not_duplicates(self):
        f1 = _make_finding(category=FindingCategory.SECURITY, title="same", line=1)
        f2 = _make_finding(category=FindingCategory.RESOURCE_LEAK, title="same", line=1)
        result = deduplicate([f1, f2])
        assert len(result) == 2

    def test_sorted_by_severity(self):
        f1 = _make_finding(title="low issue", line=1)
        f1.severity = Severity.LOW
        f2 = _make_finding(title="critical issue", line=2)
        f2.severity = Severity.CRITICAL
        result = deduplicate([f1, f2])
        assert result[0].severity == Severity.CRITICAL
        assert result[1].severity == Severity.LOW

    def test_empty_list(self):
        result = deduplicate([])
        assert result == []


class TestSeparateLowConfidence:
    """Confidence-based finding separation."""

    def test_high_confidence_separated(self):
        f_high = _make_finding(confidence=0.9)
        f_low = _make_finding(confidence=0.3)
        high, low = separate_low_confidence([f_high, f_low], threshold=0.5)
        assert len(high) == 1
        assert len(low) == 1

    def test_all_high(self):
        findings = [_make_finding(confidence=0.8), _make_finding(confidence=0.9)]
        high, low = separate_low_confidence(findings, threshold=0.5)
        assert len(high) == 2
        assert len(low) == 0

    def test_all_low(self):
        findings = [_make_finding(confidence=0.3), _make_finding(confidence=0.1)]
        high, low = separate_low_confidence(findings, threshold=0.5)
        assert len(high) == 0
        assert len(low) == 2

    def test_boundary_inclusive(self):
        f = _make_finding(confidence=0.5)
        high, low = separate_low_confidence([f], threshold=0.5)
        assert len(high) == 1  # >= threshold
