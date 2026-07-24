"""Security and code-quality scanners for code review.

Each scanner is a standalone function that takes a DiffFile and returns
a list of Findings. Scanners can be selectively enabled/disabled.
"""

import re

from .diff_parser import get_changed_lines
from .types import DiffFile, Finding, FindingCategory, Severity


# ── Security scanner ──────────────────────────────────────────────

_SECURITY_PATTERNS = [
    (re.compile(r'os\.system\s*\(', re.IGNORECASE),
     "os.system() with potentially unsanitized input",
     "Use subprocess.run() with shell=False and explicit argument lists",
     Severity.HIGH, 0.9),
    (re.compile(r'subprocess\.(?:call|Popen|run)\s*\([^)]*shell\s*=\s*True'),
     "subprocess with shell=True — command injection risk",
     "Use shell=False and pass arguments as a list",
     Severity.HIGH, 0.9),
    (re.compile(r'eval\s*\(', re.IGNORECASE),
     "eval() on dynamic input — arbitrary code execution risk",
     "Use ast.literal_eval() or parse input with a safe parser",
     Severity.CRITICAL, 0.95),
    (re.compile(r"pickle\.(?:loads?|dump)\s*\("),
     "pickle deserialization — remote code execution risk with untrusted data",
     "Use json instead of pickle for untrusted data",
     Severity.HIGH, 0.85),
    (re.compile(r'yaml\.load\s*\(', re.IGNORECASE),
     "yaml.load() without SafeLoader — arbitrary code execution",
     "Use yaml.safe_load() or yaml.load(..., Loader=yaml.SafeLoader)",
     Severity.HIGH, 0.9),
    (re.compile(r'__import__\s*\(\s*[\'\"]os[\'\"]', re.IGNORECASE),
     "Dynamic import of os module — potential sandbox escape",
     "Avoid dynamic imports of sensitive modules",
     Severity.CRITICAL, 0.95),
]


def scan_security(diff_file: DiffFile) -> list[Finding]:
    """Scan for security vulnerabilities."""
    findings: list[Finding] = []
    changed = get_changed_lines(diff_file)
    for lineno, line in changed:
        for pattern, title, recommendation, severity, confidence in _SECURITY_PATTERNS:
            if pattern.search(line):
                findings.append(Finding(
                    severity=severity,
                    category=FindingCategory.SECURITY,
                    file=diff_file.filename,
                    line=lineno,
                    title=title,
                    evidence=line.strip(),
                    recommendation=recommendation,
                    confidence=confidence,
                    source="security_scanner",
                ))
    return findings


# ── Async error scanner ───────────────────────────────────────────

_ASYNC_ERROR_PATTERNS = [
    (re.compile(r'time\.sleep\s*\('),
     "time.sleep() in async context — blocks the event loop",
     "Use asyncio.sleep() instead of time.sleep()",
     Severity.MEDIUM, 0.8),
    (re.compile(r'(?:def|async def)\s+\w+[^)]*\)\s*:\s*\n(?:\s*#.*\n)*\s*[\w.]+\('),
     "Potential bare coroutine call (missing await)",
     "Add 'await' before async function calls",
     Severity.MEDIUM, 0.6),
    (re.compile(r'asyncio\.get_event_loop\s*\('),
     "asyncio.get_event_loop() — deprecated, may not work in new event loops",
     "Use asyncio.get_running_loop() or asyncio.run()",
     Severity.LOW, 0.7),
    (re.compile(r'(?<!await\s)asyncio\.create_task\s*\(', re.IGNORECASE),
     "Unawaited create_task — task may be silently lost",
     "Store the task reference: task = asyncio.create_task(...)",
     Severity.MEDIUM, 0.75),
]


def scan_async_errors(diff_file: DiffFile) -> list[Finding]:
    """Scan for async/await anti-patterns."""
    findings: list[Finding] = []
    changed = get_changed_lines(diff_file)
    for lineno, line in changed:
        for pattern, title, recommendation, severity, confidence in _ASYNC_ERROR_PATTERNS:
            if pattern.search(line):
                findings.append(Finding(
                    severity=severity,
                    category=FindingCategory.ASYNC_ERROR,
                    file=diff_file.filename,
                    line=lineno,
                    title=title,
                    evidence=line.strip(),
                    recommendation=recommendation,
                    confidence=confidence,
                    source="async_scanner",
                ))
    return findings


# ── Resource leak scanner ─────────────────────────────────────────

_RESOURCE_LEAK_PATTERNS = [
    (re.compile(r'open\s*\([^)]*\)(?!\s*(?:as\s|\.close))', re.IGNORECASE),
     "open() without context manager — potential file handle leak",
     "Use 'with open(...) as f:' to ensure proper cleanup",
     Severity.HIGH, 0.85),
    (re.compile(r'(?:requests|httpx|aiohttp)\.(?:get|post|put|delete)\s*\([^)]*\)(?!\s*(?:as\s|with))'),
     "HTTP request without session/context — connection may leak",
     "Use a session or context manager for connection reuse",
     Severity.LOW, 0.5),
    (re.compile(r'(?:socket|ssl)\.(?:socket|connect|wrap_socket)\s*\([^)]*\)(?!\s*\.close)'),
     "Socket created without explicit close — resource leak risk",
     "Wrap in context manager or ensure close() in finally block",
     Severity.MEDIUM, 0.7),
]


def scan_resource_leaks(diff_file: DiffFile) -> list[Finding]:
    """Scan for resource leak patterns."""
    findings: list[Finding] = []
    changed = get_changed_lines(diff_file)
    for lineno, line in changed:
        for pattern, title, recommendation, severity, confidence in _RESOURCE_LEAK_PATTERNS:
            if pattern.search(line):
                findings.append(Finding(
                    severity=severity,
                    category=FindingCategory.RESOURCE_LEAK,
                    file=diff_file.filename,
                    line=lineno,
                    title=title,
                    evidence=line.strip(),
                    recommendation=recommendation,
                    confidence=confidence,
                    source="resource_leak_scanner",
                ))
    return findings


# ── DB lifecycle scanner ──────────────────────────────────────────

_DB_LIFECYCLE_PATTERNS = [
    (re.compile(r'(?:cursor|conn|connection)\s*=\s*\w+\.(?:cursor|connect)\s*\(',
                re.IGNORECASE),
     "Database cursor/connection created — ensure it is closed",
     "Use context manager: 'with conn.cursor() as cursor:'",
     Severity.HIGH, 0.8),
    (re.compile(r'\.execute\s*\([^)]*\)(?!.*\.commit|.*\.close)', re.IGNORECASE),
     "DB execute without visible commit/close — transaction may hang",
     "Call commit() after write operations, or use context manager",
     Severity.MEDIUM, 0.65),
    (re.compile(r'\.rollback\s*\(\s*\)', re.IGNORECASE),
     "Explicit rollback — is error handling swallowing the issue?",
     "Log the error before rollback for debugging",
     Severity.LOW, 0.4),
]


def scan_db_lifecycle(diff_file: DiffFile) -> list[Finding]:
    """Scan for database connection/transaction lifecycle issues."""
    findings: list[Finding] = []
    changed = get_changed_lines(diff_file)
    for lineno, line in changed:
        for pattern, title, recommendation, severity, confidence in _DB_LIFECYCLE_PATTERNS:
            if pattern.search(line):
                findings.append(Finding(
                    severity=severity,
                    category=FindingCategory.DB_LIFECYCLE,
                    file=diff_file.filename,
                    line=lineno,
                    title=title,
                    evidence=line.strip(),
                    recommendation=recommendation,
                    confidence=confidence,
                    source="db_lifecycle_scanner",
                ))
    return findings


# ── Missing tests scanner ─────────────────────────────────────────

_FUNC_DEF_RE = re.compile(r'^\s*def\s+(\w+)\s*\(')
_TEST_FUNC_RE = re.compile(r'test_\w+|^test_')


def scan_missing_tests(diff_file: DiffFile) -> list[Finding]:
    """Flag new non-test functions that lack corresponding test_* functions."""
    findings: list[Finding] = []
    changed = get_changed_lines(diff_file)

    for lineno, line in changed:
        m = _FUNC_DEF_RE.match(line)
        if m and not _TEST_FUNC_RE.match(m.group(1)):
            findings.append(Finding(
                severity=Severity.LOW,
                category=FindingCategory.MISSING_TESTS,
                file=diff_file.filename,
                line=lineno,
                title=f"New function '{m.group(1)}' may need tests",
                evidence=line.strip(),
                recommendation=f"Add test_{m.group(1)} to the test suite",
                confidence=0.5,
                source="missing_tests_scanner",
            ))

    return findings


# ── Secret info scanner ───────────────────────────────────────────

_SECRET_PATTERNS = [
    (re.compile(r'(?:api[_-]?key|apikey|API_KEY)\s*[=:]\s*["\']\S{8,}["\']',
                re.IGNORECASE),
     "Hardcoded API key detected",
     Severity.CRITICAL, 0.95),
    (re.compile(r'(?:password|passwd|pwd)\s*[=:]\s*["\']\S+["\']', re.IGNORECASE),
     "Hardcoded password detected",
     Severity.CRITICAL, 0.95),
    (re.compile(r'(?:secret|token)\s*[=:]\s*["\'][A-Za-z0-9_\-]{10,}["\']',
                re.IGNORECASE),
     "Hardcoded secret/token detected",
     Severity.CRITICAL, 0.9),
    (re.compile(r'(?:ghp|github_pat|gho|ghu|ghs|ghr)_[A-Za-z0-9_]{20,}'),
     "GitHub personal access token in code",
     Severity.CRITICAL, 0.98),
    (re.compile(r'sk-[A-Za-z0-9]{20,}'),
     "OpenAI API key pattern detected",
     Severity.CRITICAL, 0.98),
    (re.compile(r'(?:private[_-]?key|PRIVATE KEY)'),
     "Private key in code",
     Severity.CRITICAL, 0.9),
]


def scan_secret_info(diff_file: DiffFile) -> list[Finding]:
    """Scan for hardcoded secrets and sensitive information."""
    findings: list[Finding] = []
    changed = get_changed_lines(diff_file)
    for lineno, line in changed:
        for pattern, title, severity, confidence in _SECRET_PATTERNS:
            if pattern.search(line):
                findings.append(Finding(
                    severity=severity,
                    category=FindingCategory.SECRET_INFO,
                    file=diff_file.filename,
                    line=lineno,
                    title=title,
                    evidence="[REDACTED]",  # Never include the actual secret
                    recommendation="Use environment variables or a secrets manager",
                    confidence=confidence,
                    source="secret_scanner",
                ))
    return findings


# ── Bare except scanner ─────────────────────────────────────────────

_BARE_EXCEPT_RE = re.compile(r'^\s*except\s*:', re.MULTILINE)


def scan_bare_except(diff_file: DiffFile) -> list[Finding]:
    """Flag bare except: clauses (catches KeyboardInterrupt, SystemExit)."""
    findings: list[Finding] = []
    changed = get_changed_lines(diff_file)
    for lineno, line in changed:
        if _BARE_EXCEPT_RE.match(line):
            findings.append(Finding(
                severity=Severity.MEDIUM,
                category=FindingCategory.SECURITY,
                file=diff_file.filename,
                line=lineno,
                title="Bare 'except:' clause — catches unexpected exceptions",
                evidence=line.strip(),
                recommendation="Use 'except Exception as e:' to avoid catching "
                               "KeyboardInterrupt and SystemExit.",
                confidence=0.75,
                source="bare_except_scanner",
            ))
    return findings


# ── Mutable default argument scanner ─────────────────────────────────

_MUTABLE_DEFAULT_RE = re.compile(
    r'def\s+\w+\s*\([^)]*(?:=\s*\[\s*\]|=\s*\{\s*\}|=\s*set\s*\(\s*\))',
)


def scan_mutable_defaults(diff_file: DiffFile) -> list[Finding]:
    """Flag mutable default arguments (list, dict, set)."""
    findings: list[Finding] = []
    changed = get_changed_lines(diff_file)
    for lineno, line in changed:
        if _MUTABLE_DEFAULT_RE.search(line):
            findings.append(Finding(
                severity=Severity.MEDIUM,
                category=FindingCategory.RESOURCE_LEAK,
                file=diff_file.filename,
                line=lineno,
                title="Mutable default argument — shared across all calls",
                evidence=line.strip(),
                recommendation="Use 'arg=None' and initialize inside function body.",
                confidence=0.85,
                source="mutable_default_scanner",
            ))
    return findings


# ── Assert for control flow scanner ──────────────────────────────────

_ASSERT_CONTROL_RE = re.compile(r'^\s*assert\s+(?!.*isinstance)', re.MULTILINE)


def scan_assert_control_flow(diff_file: DiffFile) -> list[Finding]:
    """Flag assert used for validation (stripped with -O flag)."""
    findings: list[Finding] = []
    changed = get_changed_lines(diff_file)
    for lineno, line in changed:
        if _ASSERT_CONTROL_RE.search(line):
            findings.append(Finding(
                severity=Severity.LOW,
                category=FindingCategory.SECURITY,
                file=diff_file.filename,
                line=lineno,
                title="assert used for validation — removed with -O flag",
                evidence=line.strip(),
                recommendation="Use explicit 'if/raise' instead of assert for "
                               "production validation logic.",
                confidence=0.7,
                source="assert_control_flow_scanner",
            ))
    return findings


# ── Hardcoded path scanner ───────────────────────────────────────────

_HARDCODED_PATH_RE = re.compile(
    r'["\'](?:/home/|/root/|/etc/|/tmp/|C:\\)[^"\']*["\']',
)


def scan_hardcoded_paths(diff_file: DiffFile) -> list[Finding]:
    """Flag hardcoded absolute paths (portability issue)."""
    findings: list[Finding] = []
    changed = get_changed_lines(diff_file)
    for lineno, line in changed:
        if _HARDCODED_PATH_RE.search(line):
            findings.append(Finding(
                severity=Severity.LOW,
                category=FindingCategory.SECURITY,
                file=diff_file.filename,
                line=lineno,
                title="Hardcoded absolute path — portability issue",
                evidence=line.strip(),
                recommendation="Use os.path.join() or pathlib with relative paths.",
                confidence=0.6,
                source="hardcoded_path_scanner",
            ))
    return findings


# ── Scanner registry ─────────────────────────────────────────────────

_SCANNERS = {
    "security": scan_security,
    "async_error": scan_async_errors,
    "resource_leak": scan_resource_leaks,
    "db_lifecycle": scan_db_lifecycle,
    "missing_tests": scan_missing_tests,
    "secret_info": scan_secret_info,
    "bare_except": scan_bare_except,
    "mutable_defaults": scan_mutable_defaults,
    "assert_control_flow": scan_assert_control_flow,
    "hardcoded_paths": scan_hardcoded_paths,
}

# Per-scanner confidence thresholds (override global min_confidence)
_SCANNER_THRESHOLDS: dict[str, float] = {
    "bare_except": 0.5,
    "mutable_defaults": 0.6,
    "assert_control_flow": 0.4,
    "hardcoded_paths": 0.4,
    "missing_tests": 0.4,
    "db_lifecycle": 0.4,
}


def get_scanner_threshold(scanner_name: str, global_threshold: float = 0.5) -> float:
    """Get the confidence threshold for a specific scanner.

    Args:
        scanner_name: Name of the scanner.
        global_threshold: Fallback threshold if scanner has no specific setting.

    Returns:
        Confidence threshold for this scanner.
    """
    return _SCANNER_THRESHOLDS.get(scanner_name, global_threshold)


def run_scanners(diff_file: DiffFile, enabled: list[str] | None = None,
                 min_confidence: float = 0.5) -> list[Finding]:
    """Run all enabled scanners against a single diff file.

    Args:
        diff_file: The parsed diff file to scan.
        enabled: List of scanner names to enable (default: all).
        min_confidence: Minimum confidence threshold for findings (global default).

    Returns:
        Combined list of Findings from all scanners.
    """
    if enabled is None:
        enabled = list(_SCANNERS.keys())

    all_findings: list[Finding] = []
    for name in enabled:
        scanner = _SCANNERS.get(name)
        if scanner:
            threshold = get_scanner_threshold(name, min_confidence)
            findings = scanner(diff_file)
            all_findings.extend(f for f in findings if f.confidence >= threshold)

    return all_findings


def get_available_scanners() -> list[str]:
    """Return list of available scanner names."""
    return list(_SCANNERS.keys())
