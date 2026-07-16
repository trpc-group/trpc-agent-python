# tests/test_models.py
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
EXAMPLE_DIR = HERE.parent
sys.path.insert(0, str(EXAMPLE_DIR))

from agent.models import Finding, Severity, Bucket


def test_finding_defaults_to_findings_bucket():
    f = Finding(severity=Severity.HIGH,
                category="security",
                file="a.py",
                line=1,
                title="t",
                evidence="e",
                recommendation="r",
                confidence=0.9,
                source="rule",
                rule_id="SEC001")
    assert f.bucket == Bucket.FINDINGS
