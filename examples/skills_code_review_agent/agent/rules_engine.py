"""Deterministic code review rules used in dry-run and fake-model modes."""

from __future__ import annotations

import re
from pathlib import PurePosixPath

from .models import ChangedFile
from .models import ChangedLine
from .models import Finding
from .redaction import contains_secret
from .redaction import REDACTION_TOKEN
from .redaction import redact_text


PY_SOURCE_EXTENSIONS = {".py", ".pyi"}
TEST_PATH_RE = re.compile(r"(^|/)(tests?|test)/|(^|/)test_[^/]+\.py$|_test\.py$")


class RuleEngine:
    """Static, explainable review rules for changed lines."""

    def analyze(self, changed_files: list[ChangedFile]) -> list[Finding]:
        findings: list[Finding] = []
        for changed_file in changed_files:
            for line in changed_file.added_lines:
                findings.extend(self._analyze_added_line(line))
        findings.extend(self._check_missing_tests(changed_files))
        return findings

    def _analyze_added_line(self, line: ChangedLine) -> list[Finding]:
        content = line.content
        stripped = content.strip()
        findings: list[Finding] = []

        if not stripped or stripped.startswith("#"):
            return findings

        findings.extend(self._secret_findings(line))
        findings.extend(self._security_findings(line))
        findings.extend(self._async_findings(line))
        findings.extend(self._resource_findings(line))
        findings.extend(self._database_findings(line))
        return findings

    def _secret_findings(self, line: ChangedLine) -> list[Finding]:
        if not contains_secret(line.content) and REDACTION_TOKEN not in line.content:
            return []
        evidence, _ = redact_text(line.content.strip())
        return [
            Finding(
                severity="critical",
                category="sensitive_info",
                file=line.file,
                line=line.new_line,
                title="Potential secret committed in code",
                evidence=evidence,
                recommendation=(
                    "Remove the secret from the diff, rotate the exposed credential, "
                    "and load it from a secret manager or environment variable."
                ),
                confidence=0.98,
                source="rule:sensitive-info",
            )
        ]

    def _security_findings(self, line: ChangedLine) -> list[Finding]:
        text = line.content
        stripped = text.strip()
        findings: list[Finding] = []

        if re.search(r"\b(eval|exec)\s*\(", stripped):
            findings.append(
                Finding(
                    severity="high",
                    category="security",
                    file=line.file,
                    line=line.new_line,
                    title="Dynamic code execution introduced",
                    evidence=stripped,
                    recommendation="Avoid eval/exec on runtime data; use a constrained parser or explicit dispatch table.",
                    confidence=0.9,
                    source="rule:dangerous-exec",
                ))

        if re.search(r"\bos\.(system|popen)\s*\(", stripped):
            findings.append(
                Finding(
                    severity="high",
                    category="security",
                    file=line.file,
                    line=line.new_line,
                    title="Shell command execution introduced",
                    evidence=stripped,
                    recommendation="Use subprocess with an argument list, shell=False, and explicit input validation.",
                    confidence=0.86,
                    source="rule:command-injection",
                ))

        if re.search(r"\bshell\s*=\s*True\b", stripped) and re.search(r"\bsubprocess\.", stripped):
            findings.append(
                Finding(
                    severity="high",
                    category="security",
                    file=line.file,
                    line=line.new_line,
                    title="subprocess uses shell=True",
                    evidence=stripped,
                    recommendation="Pass an argument list with shell=False and validate any user-controlled arguments.",
                    confidence=0.88,
                    source="rule:shell-injection",
                ))

        if re.search(r"\bexecute\s*\(\s*f[\"']", stripped) or re.search(r"\bexecute\s*\([^)]*\.format\(", stripped):
            findings.append(
                Finding(
                    severity="high",
                    category="security",
                    file=line.file,
                    line=line.new_line,
                    title="SQL built with string interpolation",
                    evidence=stripped,
                    recommendation="Use parameterized SQL placeholders and pass values separately to execute().",
                    confidence=0.9,
                    source="rule:sql-injection",
                ))

        if re.search(r"\bexecute\s*\([^)]*(\+|%)", stripped):
            findings.append(
                Finding(
                    severity="high",
                    category="security",
                    file=line.file,
                    line=line.new_line,
                    title="SQL built with string concatenation",
                    evidence=stripped,
                    recommendation="Use parameterized SQL placeholders and pass values separately to execute().",
                    confidence=0.86,
                    source="rule:sql-injection",
                ))

        if "verify=False" in stripped and ("requests." in stripped or "httpx." in stripped):
            findings.append(
                Finding(
                    severity="medium",
                    category="security",
                    file=line.file,
                    line=line.new_line,
                    title="TLS certificate verification disabled",
                    evidence=stripped,
                    recommendation="Remove verify=False and configure trusted CAs explicitly when needed.",
                    confidence=0.8,
                    source="rule:tls-verification",
                ))

        if re.search(r"\byaml\.load\s*\([^)]*\)", stripped) and "SafeLoader" not in stripped:
            findings.append(
                Finding(
                    severity="medium",
                    category="security",
                    file=line.file,
                    line=line.new_line,
                    title="Unsafe YAML loading",
                    evidence=stripped,
                    recommendation="Use yaml.safe_load() or specify SafeLoader for untrusted YAML input.",
                    confidence=0.82,
                    source="rule:unsafe-deserialization",
                ))

        if re.search(r"\bpickle\.loads?\s*\(", stripped):
            findings.append(
                Finding(
                    severity="high",
                    category="security",
                    file=line.file,
                    line=line.new_line,
                    title="Unsafe pickle deserialization",
                    evidence=stripped,
                    recommendation="Do not unpickle untrusted data; use JSON or another safe serialization format.",
                    confidence=0.86,
                    source="rule:unsafe-deserialization",
                ))

        return findings

    def _async_findings(self, line: ChangedLine) -> list[Finding]:
        stripped = line.content.strip()
        findings: list[Finding] = []
        if "aiohttp.ClientSession(" in stripped and "async with" not in stripped:
            findings.append(
                Finding(
                    severity="high",
                    category="async_resource",
                    file=line.file,
                    line=line.new_line,
                    title="aiohttp ClientSession is not scoped with async with",
                    evidence=stripped,
                    recommendation="Use async with aiohttp.ClientSession() as session or close the session in finally.",
                    confidence=0.88,
                    source="rule:async-session-lifecycle",
                ))
        if "httpx.AsyncClient(" in stripped and "async with" not in stripped:
            findings.append(
                Finding(
                    severity="high",
                    category="async_resource",
                    file=line.file,
                    line=line.new_line,
                    title="httpx AsyncClient is not scoped with async with",
                    evidence=stripped,
                    recommendation="Use async with httpx.AsyncClient() as client or close the client in finally.",
                    confidence=0.86,
                    source="rule:async-client-lifecycle",
                ))
        if re.search(r"\basyncio\.create_task\s*\(", stripped) and "=" not in stripped:
            findings.append(
                Finding(
                    severity="medium",
                    category="async_error",
                    file=line.file,
                    line=line.new_line,
                    title="Created task is not retained or awaited",
                    evidence=stripped,
                    recommendation="Keep the task handle, await it, or attach error handling for background failures.",
                    confidence=0.72,
                    source="rule:async-task-lifecycle",
                    disposition="needs_human_review",
                ))
        return findings

    def _resource_findings(self, line: ChangedLine) -> list[Finding]:
        stripped = line.content.strip()
        findings: list[Finding] = []
        if re.search(r"=\s*open\s*\(", stripped) and "with " not in stripped:
            findings.append(
                Finding(
                    severity="medium",
                    category="resource_leak",
                    file=line.file,
                    line=line.new_line,
                    title="File handle opened without context manager",
                    evidence=stripped,
                    recommendation="Use with open(...) as f or ensure the handle is closed in a finally block.",
                    confidence=0.78,
                    source="rule:file-lifecycle",
                ))
        if "tempfile.mktemp(" in stripped:
            findings.append(
                Finding(
                    severity="medium",
                    category="resource_leak",
                    file=line.file,
                    line=line.new_line,
                    title="Insecure temporary file creation",
                    evidence=stripped,
                    recommendation="Use NamedTemporaryFile or mkstemp to avoid predictable temporary paths.",
                    confidence=0.84,
                    source="rule:tempfile-lifecycle",
                ))
        return findings

    def _database_findings(self, line: ChangedLine) -> list[Finding]:
        stripped = line.content.strip()
        findings: list[Finding] = []
        if re.search(r"=\s*(sqlite3|psycopg2|pymysql|aiomysql)\.connect\s*\(", stripped):
            findings.append(
                Finding(
                    severity="high",
                    category="db_lifecycle",
                    file=line.file,
                    line=line.new_line,
                    title="Database connection lacks scoped lifecycle",
                    evidence=stripped,
                    recommendation=(
                        "Wrap the connection in a context manager or close it in finally; "
                        "ensure transactions commit or roll back explicitly."
                    ),
                    confidence=0.86,
                    source="rule:db-connection-lifecycle",
                ))
        if re.search(r"\bSession\s*\(\s*\)", stripped) and "=" in stripped and "with " not in stripped:
            findings.append(
                Finding(
                    severity="medium",
                    category="db_lifecycle",
                    file=line.file,
                    line=line.new_line,
                    title="Database session may outlive request scope",
                    evidence=stripped,
                    recommendation="Use a session context manager and close, commit, or roll back on every path.",
                    confidence=0.75,
                    source="rule:db-session-lifecycle",
                ))
        return findings

    def _check_missing_tests(self, changed_files: list[ChangedFile]) -> list[Finding]:
        source_files = [
            f for f in changed_files
            if self._is_python_source(f.path) and not self._is_test_file(f.path) and f.added_lines
        ]
        tests_changed = any(self._is_test_file(f.path) for f in changed_files)
        if not source_files or tests_changed:
            return []
        findings: list[Finding] = []
        for changed_file in source_files:
            first_line = changed_file.added_lines[0].new_line if changed_file.added_lines else None
            findings.append(
                Finding(
                    severity="low",
                    category="testing",
                    file=changed_file.path,
                    line=first_line,
                    title="Production code changed without tests",
                    evidence=f"{changed_file.path} changed, but no test file was included in the diff.",
                    recommendation="Add or update tests that cover the changed behavior before merging.",
                    confidence=0.62,
                    source="rule:test-coverage",
                    disposition="needs_human_review",
                ))
        return findings

    @staticmethod
    def _is_python_source(path: str) -> bool:
        return PurePosixPath(path).suffix in PY_SOURCE_EXTENSIONS

    @staticmethod
    def _is_test_file(path: str) -> bool:
        normalized = path.replace("\\", "/")
        return bool(TEST_PATH_RE.search(normalized))
