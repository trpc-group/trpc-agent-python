"""Deterministic code review rules loaded by the code-review skill."""

from __future__ import annotations

import re
import hashlib
from pathlib import PurePosixPath

from .ast_analyzer import PythonAstAnalyzer
from .models import ChangedLine
from .models import DiffInput
from .models import Finding
from .models import Hunk
from .redaction import redact_text
from .rule_config import RuleConfig

FINDING_SCHEMA_VERSION = 1
FINDING_CONFIDENCE_THRESHOLD = 0.8
WARNING_CONFIDENCE_THRESHOLD = 0.55


class RuleEngine:
    """Small deterministic rule engine used by dry-run and fake model mode."""

    def __init__(self, rule_config: RuleConfig | None = None):
        self.rule_config = rule_config or RuleConfig.load()
        self.ast_analyzer = PythonAstAnalyzer()
        self.ignored_count = 0

    def review(self, diff: DiffInput) -> tuple[list[Finding], list[Finding], list[Finding], int, int]:
        findings: list[Finding] = []
        warnings: list[Finding] = []
        needs_human_review: list[Finding] = []
        redaction_count = 0
        self.ignored_count = 0
        ignore_map = _build_ignore_map(diff)

        for line in diff.added_lines:
            generated, count = self._review_added_line(line)
            redaction_count += count
            for finding in generated:
                if finding.confidence >= FINDING_CONFIDENCE_THRESHOLD:
                    findings.append(finding)
                elif finding.confidence >= WARNING_CONFIDENCE_THRESHOLD:
                    warnings.append(finding)
                else:
                    needs_human_review.append(finding)

        for hunk in diff.hunks:
            generated, count = self._review_hunk(hunk)
            redaction_count += count
            for finding in generated:
                if finding.confidence >= FINDING_CONFIDENCE_THRESHOLD:
                    findings.append(finding)
                elif finding.confidence >= WARNING_CONFIDENCE_THRESHOLD:
                    warnings.append(finding)
                else:
                    needs_human_review.append(finding)

            for finding in self._review_hunk_ast(hunk):
                if finding.confidence >= FINDING_CONFIDENCE_THRESHOLD:
                    findings.append(finding)
                elif finding.confidence >= WARNING_CONFIDENCE_THRESHOLD:
                    warnings.append(finding)
                else:
                    needs_human_review.append(finding)

        source_files = [f for f in diff.files if _is_source_file(f)]
        test_files = [f for f in diff.files if _is_test_file(f)]
        if source_files and not test_files:
            file_for_warning = source_files[0]
            warnings.append(
                build_finding(
                    severity="medium",
                    category="missing_tests",
                    file=file_for_warning,
                    line=_first_added_line(diff, file_for_warning),
                    title="Source change has no matching test update",
                    evidence="Changed source files without a tests/ or test_*.py diff in the same review input.",
                    recommendation="Add or update a focused regression test that exercises the changed behavior.",
                    confidence=0.72,
                    source="rule:missing-tests",
                    rule_id="tests.changed-source-without-test",
                ))

        before_count = len(findings) + len(warnings) + len(needs_human_review)
        findings = self._post_process(findings, ignore_map)
        warnings = self._post_process(warnings, ignore_map)
        needs_human_review = self._post_process(needs_human_review, ignore_map)
        findings = _dedupe(findings)
        warnings = _dedupe(warnings)
        needs_human_review = _dedupe(needs_human_review)
        after_count = len(findings) + len(warnings) + len(needs_human_review)
        return findings, warnings, needs_human_review, redaction_count, before_count - after_count

    def _review_added_line(self, line: ChangedLine) -> tuple[list[Finding], int]:
        content = line.content.strip()
        lowered = content.lower()
        redacted = redact_text(line.content)
        evidence = redacted.text.strip()
        redaction_count = redacted.count
        out: list[Finding] = []

        def add(
            *,
            severity: str,
            category: str,
            title: str,
            recommendation: str,
            confidence: float,
            source: str,
            rule_id: str,
        ) -> None:
            out.append(
                build_finding(
                    severity=severity,
                    category=category,
                    file=line.file,
                    line=line.new_line or 0,
                    title=title,
                    evidence=evidence,
                    recommendation=recommendation,
                    confidence=confidence,
                    source=source,
                    rule_id=rule_id,
                    changed_line=line,
                ))

        if redaction_count or "[REDACTED]" in line.content:
            add(
                severity="critical",
                category="secret_leak",
                title="Potential secret committed in source",
                recommendation=(
                    "Move the secret to a managed secret store, rotate the exposed value, and keep only an "
                    "environment variable reference in code."
                ),
                confidence=0.98,
                source="rule:secret-redaction",
                rule_id="security.secret.material",
            )

        if "shell=true" in lowered:
            add(
                severity="high",
                category="security",
                title="Shell command execution uses shell=True",
                recommendation=(
                    "Pass command arguments as a list and avoid shell=True; if shell expansion is required, "
                    "validate and quote every user-controlled value."
                ),
                confidence=0.92,
                source="rule:command-injection",
                rule_id="security.subprocess.shell-true",
            )
        if re.search(r"\b(eval|exec)\s*\(", content):
            add(
                severity="high",
                category="security",
                title="Dynamic code execution introduced",
                recommendation="Replace eval/exec with a constrained parser or explicit dispatch table.",
                confidence=0.88,
                source="rule:dynamic-code",
                rule_id="security.dynamic-code",
            )
        if re.search(r"\b(pickle\.loads|yaml\.load)\s*\(", content):
            add(
                severity="high",
                category="security",
                title="Unsafe deserialization on changed line",
                recommendation="Use safe loaders and never deserialize untrusted payloads directly.",
                confidence=0.86,
                source="rule:unsafe-deserialization",
                rule_id="security.unsafe-deserialization",
            )
        if "verify=false" in lowered:
            add(
                severity="high",
                category="security",
                title="TLS certificate verification disabled",
                recommendation=(
                    "Keep certificate verification enabled and configure trusted CA roots instead of disabling "
                    "verification."
                ),
                confidence=0.9,
                source="rule:tls-verify",
                rule_id="security.tls-verify-false",
            )
        if _looks_like_sql_concat(content):
            add(
                severity="high",
                category="security",
                title="SQL query appears to use string interpolation",
                recommendation="Use parameterized queries or the ORM query builder to bind untrusted values.",
                confidence=0.84,
                source="rule:sql-injection",
                rule_id="security.sql-interpolation",
            )

        if "asyncio.create_task(" in content and not _line_stores_or_tracks_task(content):
            add(
                severity="medium",
                category="async_error",
                title="Created asyncio task is not tracked",
                recommendation=(
                    "Store the task, await it, or attach explicit exception handling so failures are not lost."
                ),
                confidence=0.82,
                source="rule:async-task",
                rule_id="async.untracked-create-task",
            )
        if "asyncio.gather(" in content and "return_exceptions" not in content and "await " not in content:
            add(
                severity="medium",
                category="async_error",
                title="asyncio.gather result is not awaited or exception-managed",
                recommendation=(
                    "Await gather and handle exceptions explicitly, or use return_exceptions=True when partial "
                    "failure is acceptable."
                ),
                confidence=0.78,
                source="rule:async-gather",
                rule_id="async.gather-not-awaited",
            )

        if _looks_like_open_without_context(content):
            add(
                severity="medium",
                category="resource_leak",
                title="File handle may be opened without a context manager",
                recommendation="Use `with open(...) as f:` or ensure the handle is closed in a finally block.",
                confidence=0.83,
                source="rule:resource-lifecycle",
                rule_id="resource.open-without-context",
            )
        if re.search(r"\b(aiohttp\.ClientSession|requests\.Session)\s*\(",
                     content) and "with " not in content and not _has_close_signal(line.context_after):
            add(
                severity="medium",
                category="resource_leak",
                title="HTTP session may not be closed",
                recommendation="Use a context manager or close the session in a finally block.",
                confidence=0.81,
                source="rule:resource-lifecycle",
                rule_id="resource.session-without-close",
            )

        if re.search(r"\b(sqlite3|psycopg2|pymysql|aiomysql)\.connect\s*\(",
                     content) and "with " not in content and not _has_close_signal(line.context_after):
            add(
                severity="medium",
                category="db_lifecycle",
                title="Database connection opened without scoped lifecycle",
                recommendation="Use a context manager or make sure commit/rollback and close happen on every path.",
                confidence=0.84,
                source="rule:db-lifecycle",
                rule_id="db.connection-lifecycle",
            )
        if re.search(r"(?<!\.)\b(Session|sessionmaker)\s*\(",
                     content) and "with " not in content and not _has_close_signal(line.context_after):
            add(
                severity="medium",
                category="db_lifecycle",
                title="Database session created without scoped lifecycle",
                recommendation="Use `with Session(...) as session:` or close the session in a finally block.",
                confidence=0.8,
                source="rule:db-lifecycle",
                rule_id="db.session-lifecycle",
            )
        if re.search(r"\b(pool|engine)\.acquire\s*\(",
                     content) and "async with " not in content and "with " not in content:
            add(
                severity="medium",
                category="db_lifecycle",
                title="Database pool connection acquired without scoped release",
                recommendation="Use an async context manager or release the acquired connection in a finally block.",
                confidence=0.81,
                source="rule:db-lifecycle",
                rule_id="db.pool-acquire-release",
            )
        if ".execute(" in content and ("f\"" in content or "f'" in content or "%" in content or " + " in content):
            add(
                severity="high",
                category="security",
                title="Database execute call may interpolate query text",
                recommendation="Use bind parameters instead of composing SQL strings.",
                confidence=0.82,
                source="rule:db-query",
                rule_id="db.execute-interpolation",
            )
        if _looks_like_db_write(content) and not _has_transaction_signal(content):
            add(
                severity="medium",
                category="db_transaction",
                title="Database write appears outside explicit transaction handling",
                recommendation="Wrap writes in a transaction and ensure commit on success with rollback on exceptions.",
                confidence=0.76,
                source="rule:db-transaction",
                rule_id="db.write-without-transaction",
            )
        if _starts_transaction(content) and not _has_rollback_signal(content, line.context_after):
            add(
                severity="medium",
                category="db_transaction",
                title="Transaction start lacks nearby rollback handling",
                recommendation="Add exception handling with rollback before propagating the error.",
                confidence=0.74,
                source="rule:db-transaction",
                rule_id="db.transaction-without-rollback",
            )

        return out, redaction_count

    def _review_hunk_ast(self, hunk: Hunk) -> list[Finding]:
        findings: list[Finding] = []
        for item in self.ast_analyzer.analyze_hunk(hunk):
            findings.append(
                build_finding(
                    severity=item.severity,
                    category=item.category,
                    file=item.line.file,
                    line=item.line.new_line or 0,
                    title=item.title,
                    evidence=item.evidence,
                    recommendation=item.recommendation,
                    confidence=item.confidence,
                    source=item.source,
                    rule_id=item.rule_id,
                    changed_line=item.line,
                ))
        return findings

    def _post_process(self, findings: list[Finding], ignore_map: dict[tuple[str, int], set[str]]) -> list[Finding]:
        out: list[Finding] = []
        for finding in findings:
            ignored = ignore_map.get((finding.file, finding.line), set()) | ignore_map.get(
                (finding.file, finding.line - 1), set())
            if finding.rule_id in ignored or "*" in ignored:
                self.ignored_count += 1
                continue
            configured = self.rule_config.apply(finding)
            if configured is None:
                self.ignored_count += 1
                continue
            out.append(configured)
        return out

    def _review_hunk(self, hunk: Hunk) -> tuple[list[Finding], int]:
        added = [line for line in hunk.lines if line.kind == "add"]
        if not added:
            return [], 0

        out: list[Finding] = []
        redaction_count = 0
        added_block = "\n".join(line.content for line in added)

        def add(
            line: ChangedLine,
            *,
            severity: str,
            category: str,
            title: str,
            evidence: str,
            recommendation: str,
            confidence: float,
            source: str,
            rule_id: str,
        ) -> None:
            nonlocal redaction_count
            redacted = redact_text(evidence)
            redaction_count += redacted.count
            out.append(
                build_finding(
                    severity=severity,
                    category=category,
                    file=line.file,
                    line=line.new_line or 0,
                    title=title,
                    evidence=redacted.text.strip(),
                    recommendation=recommendation,
                    confidence=confidence,
                    source=source,
                    rule_id=rule_id,
                    changed_line=line,
                ))

        for index, line in enumerate(added):
            content = line.content
            later_added = "\n".join(item.content for item in added[index:index + 6])
            if (re.search(r"\bsubprocess\.(run|Popen|call|check_call|check_output)\s*\(", content)
                    and "shell=True" not in content and "shell=True" in later_added):
                add(
                    line,
                    severity="high",
                    category="security",
                    title="Multi-line subprocess call enables shell execution",
                    evidence=later_added,
                    recommendation=(
                        "Pass arguments as a list and remove shell=True; validate and quote user-controlled values "
                        "if shell execution is unavoidable."
                    ),
                    confidence=0.9,
                    source="rule:command-injection",
                    rule_id="security.subprocess.multiline-shell-true",
                )

            task_name = _assigned_create_task_name(content)
            if task_name and not _task_is_observed(task_name, added_block):
                add(
                    line,
                    severity="medium",
                    category="async_error",
                    title="Created asyncio task is stored but never observed",
                    evidence=content,
                    recommendation="Await the task, gather it, or attach a done callback that consumes exceptions.",
                    confidence=0.68,
                    source="rule:async-task",
                    rule_id="async.stored-task-not-observed",
                )

        sql_assignments = {
            name: line
            for line in added
            for name in [_interpolated_sql_assignment(line.content)] if name
        }
        for line in added:
            for name, assignment_line in sql_assignments.items():
                if re.search(rf"\.execute\s*\(\s*{re.escape(name)}\b", line.content):
                    add(
                        line,
                        severity="high",
                        category="security",
                        title="Database execute uses interpolated SQL built earlier",
                        evidence=f"{assignment_line.content}\n{line.content}",
                        recommendation="Keep SQL text static and pass untrusted values through bind parameters.",
                        confidence=0.86,
                        source="rule:sql-injection",
                        rule_id="security.sql-interpolated-variable",
                    )

        return out, redaction_count


def _dedupe(findings: list[Finding]) -> list[Finding]:
    seen: set[tuple[str, int, str]] = set()
    out: list[Finding] = []
    severity_rank = {"critical": 4, "high": 3, "medium": 2, "low": 1, "info": 0}
    for finding in sorted(findings,
                          key=lambda f: (-severity_rank.get(f.severity, 0), -f.confidence, f.file, f.line, f.category)):
        key = finding.key()
        if key in seen:
            continue
        seen.add(key)
        out.append(finding)
    return out


def build_finding(
    *,
    severity: str,
    category: str,
    file: str,
    line: int,
    title: str,
    evidence: str,
    recommendation: str,
    confidence: float,
    source: str,
    rule_id: str,
    changed_line: ChangedLine | None = None,
) -> Finding:
    identity = f"{file}:{line}:{category}:{rule_id}:{title}"
    finding_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
    return Finding(
        finding_id=finding_id,
        schema_version=FINDING_SCHEMA_VERSION,
        severity=severity,
        category=category,
        file=file,
        line=line,
        title=title,
        evidence=evidence,
        recommendation=recommendation,
        confidence=confidence,
        source=source,
        rule_id=rule_id,
        hunk_header=changed_line.hunk_header if changed_line else "",
        context_before=list(changed_line.context_before) if changed_line else [],
        context_after=list(changed_line.context_after) if changed_line else [],
    )


def _is_source_file(path: str) -> bool:
    suffix = PurePosixPath(path).suffix
    return suffix in {".py", ".js", ".ts", ".go", ".java", ".rs"} and not _is_test_file(path)


def _is_test_file(path: str) -> bool:
    name = PurePosixPath(path).name
    return path.startswith("tests/") or name.startswith("test_") or name.endswith("_test.py") or ".test." in name


def _first_added_line(diff: DiffInput, file_path: str) -> int:
    for line in diff.added_lines:
        if line.file == file_path and line.new_line is not None:
            return line.new_line
    return 1


def _looks_like_sql_concat(content: str) -> bool:
    lowered = content.lower()
    if not any(word in lowered for word in ("select ", "insert ", "update ", "delete ")):
        return False
    return any(token in content for token in ("f\"", "f'", "%", " + ", ".format("))


def _line_stores_or_tracks_task(content: str) -> bool:
    prefix = content.split("asyncio.create_task(", 1)[0]
    return "=" in prefix or ".append(" in prefix or "tasks.add(" in prefix


def _looks_like_open_without_context(content: str) -> bool:
    if "open(" not in content or "with open(" in content:
        return False
    return bool(re.search(r"=\s*open\s*\(", content) or content.startswith("open("))


def _looks_like_db_write(content: str) -> bool:
    lowered = content.lower()
    return ".execute(" in lowered and any(word in lowered for word in ("insert ", "update ", "delete ", "replace "))


def _has_transaction_signal(content: str) -> bool:
    lowered = content.lower()
    return any(token in lowered for token in ("commit(", "rollback(", "begin(", "transaction", "with "))


def _starts_transaction(content: str) -> bool:
    lowered = content.lower()
    return "begin" in lowered and (".execute(" in lowered or ".begin(" in lowered or "transaction" in lowered)


def _has_rollback_signal(content: str, context_after: list[str]) -> bool:
    haystack = "\n".join([content, *context_after]).lower()
    return "rollback" in haystack


def _has_close_signal(context_after: list[str]) -> bool:
    haystack = "\n".join(context_after).lower()
    return any(token in haystack for token in (".close(", " close(", ".release(", " release("))


def _assigned_create_task_name(content: str) -> str | None:
    match = re.match(r"\s*(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*asyncio\.create_task\s*\(", content)
    if not match:
        return None
    return match.group("name")


def _task_is_observed(name: str, block: str) -> bool:
    patterns = (
        rf"\bawait\s+{re.escape(name)}\b",
        rf"\basyncio\.gather\s*\([^)]*\b{re.escape(name)}\b",
        rf"\b{re.escape(name)}\.add_done_callback\s*\(",
        rf"\b{re.escape(name)}\.result\s*\(",
        rf"\b{re.escape(name)}\.exception\s*\(",
    )
    return any(re.search(pattern, block) for pattern in patterns)


def _interpolated_sql_assignment(content: str) -> str | None:
    match = re.match(r"\s*(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?P<expr>.+)", content)
    if not match:
        return None
    expr = match.group("expr")
    if not _looks_like_sql_concat(expr):
        return None
    return match.group("name")


def _build_ignore_map(diff: DiffInput) -> dict[tuple[str, int], set[str]]:
    ignore_map: dict[tuple[str, int], set[str]] = {}
    for line in diff.added_lines:
        if line.new_line is None:
            continue
        ignored = _parse_ignore_comment(line.content)
        if not ignored:
            continue
        ignore_map.setdefault((line.file, line.new_line), set()).update(ignored)
        ignore_map.setdefault((line.file, line.new_line + 1), set()).update(ignored)
    return ignore_map


def _parse_ignore_comment(content: str) -> set[str]:
    match = re.search(r"#\s*cr-agent:\s*ignore=([A-Za-z0-9_.,*:-]+)", content)
    if not match:
        return set()
    return {item.strip() for item in match.group(1).split(",") if item.strip()}
