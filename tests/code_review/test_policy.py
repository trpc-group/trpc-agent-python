"""Finding normalization and publishing policy tests."""

from examples.code_review_agent.code_review.models import (
    ChangedFile,
    ChangedLine,
    ChangeType,
    DiffHunk,
    Finding,
    LineChangeType,
    ReviewOutput,
    Severity,
)
from examples.code_review_agent.code_review.policy import apply_finding_policy


def _changed_file() -> ChangedFile:
    return ChangedFile(
        path="src/app.py",
        change_type=ChangeType.MODIFIED,
        language="python",
        patch="patch",
        hunks=[
            DiffHunk(
                header="@@ -9 +9,2 @@",
                old_start=9,
                old_count=1,
                new_start=9,
                new_count=2,
                lines=[
                    ChangedLine(
                        change_type=LineChangeType.CONTEXT,
                        content="before",
                        old_line=9,
                        new_line=9,
                    ),
                    ChangedLine(
                        change_type=LineChangeType.ADDED,
                        content="danger()",
                        new_line=10,
                    ),
                ],
            )
        ],
    )


def _finding(**overrides) -> Finding:
    values = {
        "rule_id": "python.security.command-injection",
        "severity": Severity.HIGH,
        "confidence": 0.9,
        "category": "security",
        "file_path": "src/app.py",
        "start_line": 10,
        "end_line": 10,
        "title": "Unsafe command",
        "description": "Untrusted input reaches a command.",
        "suggestion": "Use an argument array.",
    }
    values.update(overrides)
    return Finding(**values)


def test_marks_only_added_lines_as_publishable_and_deduplicates() -> None:
    output, diagnostics = apply_finding_policy(
        ReviewOutput(
            summary=" summary ",
            findings=[
                _finding(confidence=0.8),
                _finding(confidence=0.95),
                _finding(rule_id="python.correctness.other", start_line=9, end_line=9),
            ],
        ),
        [_changed_file()],
    )

    assert output.summary == "summary"
    assert len(output.findings) == 2
    command_finding = next(item for item in output.findings if "command-injection" in item.rule_id)
    assert command_finding.confidence == 0.95
    assert command_finding.publishable is True
    context_finding = next(item for item in output.findings if item.start_line == 9)
    assert context_finding.publishable is False
    assert diagnostics


def test_drops_unchanged_files_and_low_confidence_findings() -> None:
    output, diagnostics = apply_finding_policy(
        ReviewOutput(findings=[
            _finding(file_path="other.py"),
            _finding(rule_id="low", confidence=0.2),
        ]),
        [_changed_file()],
        minimum_confidence=0.5,
    )

    assert output.findings == []
    assert len(diagnostics) == 2
