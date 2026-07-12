"""Sensitive-value redaction used before persistence and reporting."""

import re

from reports.models import ReviewAnalysis
from reports.models import ReviewFinding
from reports.models import ReviewReport

# Apply specific credential formats before the broader key/value patterns.
_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
            re.DOTALL,
        ),
        "[REDACTED_PRIVATE_KEY]",
    ),
    (
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*\Z", re.DOTALL),
        "[REDACTED_PRIVATE_KEY]",
    ),
    (
        re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{8,}", re.IGNORECASE),
        "Bearer [REDACTED]",
    ),
    (
        re.compile(r"\bsk-[A-Za-z0-9_-]{10,}"),
        "sk-[REDACTED]",
    ),
    (
        re.compile(r"\b(?:sk|pk|rk)_(?:live|test)_[A-Za-z0-9_-]{8,}"),
        "[REDACTED_SERVICE_KEY]",
    ),
    (
        re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}"),
        "[REDACTED_SLACK_TOKEN]",
    ),
    (
        re.compile(r"\bAIza[0-9A-Za-z_-]{20,}"),
        "[REDACTED_GOOGLE_KEY]",
    ),
    (
        re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
        "AWS[REDACTED]",
    ),
    (
        re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
        "gh_[REDACTED]",
    ),
    (
        re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
        "github_pat_[REDACTED]",
    ),
    (
        re.compile(r"\bglpat-[A-Za-z0-9_-]{16,}\b"),
        "glpat-[REDACTED]",
    ),
    (
        re.compile(r"\b(?:npm|hf)_[A-Za-z0-9_-]{20,}\b"),
        "[REDACTED_SERVICE_TOKEN]",
    ),
    (
        re.compile(r"\bpypi-[A-Za-z0-9_-]{20,}\b"),
        "pypi-[REDACTED]",
    ),
    (
        re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b"),
        "[REDACTED_JWT]",
    ),
    (
        re.compile(r"(?i)([a-z][a-z0-9+.-]*://[^\s:/]+:)[^\s@/]+(@)"),
        r"\1[REDACTED]\2",
    ),
    (
        re.compile(
            r"(?i)([\"']?[a-z0-9_.-]*(?:api[_-]?key|access[_-]?token|"
            r"client[_-]?secret|private[_-]?key|authorization|credential|token|"
            r"password|passwd|secret)[a-z0-9_.-]*[\"']?\s*[:=]\s*)"
            r"([\"']?)[^\s,;\"']{4,}\2"
        ),
        r"\1\2[REDACTED]\2",
    ),
    (
        re.compile(
            r"(?i)([\"']?[a-z0-9_.-]*(?:api[_-]?key|access[_-]?token|"
            r"client[_-]?secret|private[_-]?key|authorization|credential|token|"
            r"password|passwd|secret)[a-z0-9_.-]*[\"']?\s*[:=]\s*)"
            r"[\"']?[^\s,;\"']{4,}"
        ),
        r"\1[REDACTED]",
    ),
)
_SECRET_PATH_TERMS = {
    "credential",
    "credentials",
    "passwd",
    "password",
    "passwords",
    "secret",
    "secrets",
    "token",
    "tokens",
}
_SECRET_FILE_SUFFIXES = {".key", ".p12", ".pem", ".pfx"}
_SOURCE_FILE_SUFFIXES = {
    ".c", ".cc", ".cpp", ".go", ".java", ".js", ".jsx", ".kt",
    ".php", ".py", ".rb", ".rs", ".ts", ".tsx",
}


def is_likely_secret_path(value: str) -> bool:
    """Return whether a repository-relative path should not be opened automatically."""
    parts = [part.lower() for part in value.replace("\\", "/").split("/") if part]
    if not parts:
        return False
    filename = parts[-1]
    if filename == ".env" or filename.startswith(".env."):
        return True
    if filename in {"id_rsa", "id_dsa", "id_ecdsa", "id_ed25519"}:
        return True
    if any(filename.endswith(suffix) for suffix in _SECRET_FILE_SUFFIXES):
        return True
    if any(
        {word for word in re.split(r"[._-]+", part) if word}
        & _SECRET_PATH_TERMS
        for part in parts[:-1]
    ):
        return True
    if any(filename.endswith(suffix) for suffix in _SOURCE_FILE_SUFFIXES):
        return False
    words = {word for word in re.split(r"[._-]+", filename) if word}
    return bool(words & _SECRET_PATH_TERMS)


def redact_text(value: str) -> str:
    """Replace common credential forms with stable placeholders."""
    redacted = value
    for pattern, replacement in _PATTERNS:
        redacted = pattern.sub(replacement, redacted)
    return redacted


def redact_analysis(analysis: ReviewAnalysis) -> ReviewAnalysis:
    """Redact every free-text field emitted by a model or rule."""

    def redact_finding(finding: ReviewFinding) -> ReviewFinding:
        return finding.model_copy(
            update={
                "title": redact_text(finding.title),
                "file": redact_text(finding.file),
                "evidence": redact_text(finding.evidence),
                "recommendation": redact_text(finding.recommendation),
                "source": redact_text(finding.source),
            }
        )

    return analysis.model_copy(
        update={
            "summary": redact_text(analysis.summary),
            "findings": [redact_finding(item) for item in analysis.findings],
            "warnings": [redact_finding(item) for item in analysis.warnings],
            "needs_human_review": [
                redact_finding(item) for item in analysis.needs_human_review
            ],
            "checks_performed": [redact_text(item) for item in analysis.checks_performed],
        }
    )


def redact_report(report: ReviewReport) -> ReviewReport:
    """Redact report fields before serialization so JSON remains valid."""
    # Redact typed fields instead of applying regexes to serialized JSON text.
    input_summary = report.input_summary.model_copy(
        update={
            "source": redact_text(report.input_summary.source),
            "files": [redact_text(item) for item in report.input_summary.files],
            "redacted_preview": redact_text(report.input_summary.redacted_preview),
        }
    )
    decisions = [
        decision.model_copy(
            update={
                "command": redact_text(decision.command),
                "reason": redact_text(decision.reason),
            }
        )
        for decision in report.filter_decisions
    ]
    runs = [
        run.model_copy(
            update={
                "command": redact_text(run.command),
                "stdout_summary": redact_text(run.stdout_summary),
                "stderr_summary": redact_text(run.stderr_summary),
                "error_type": redact_text(run.error_type) if run.error_type else None,
            }
        )
        for run in report.sandbox_runs
    ]
    return report.model_copy(
        update={
            "repository": redact_text(report.repository),
            "input_summary": input_summary,
            "analysis": redact_analysis(report.analysis),
            "filter_decisions": decisions,
            "sandbox_runs": runs,
            "conclusion": redact_text(report.conclusion),
        }
    )
