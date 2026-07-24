"""Tests for scanner modules."""

from pipeline.diff_parser import parse_diff
from pipeline.scanners import (
    get_available_scanners,
    run_scanners,
    scan_async_errors,
    scan_db_lifecycle,
    scan_missing_tests,
    scan_resource_leaks,
    scan_secret_info,
    scan_security,
)
from pipeline.types import DiffFile, FindingCategory, Severity


class TestSecurityScanner:
    """Security vulnerability detection."""

    def test_detect_os_system(self):
        f = _diff_with_line("handler.py", 5, "os.system(user_input)")
        findings = scan_security(f)
        titles = [x.title for x in findings]
        assert any("os.system" in t for t in titles)

    def test_detect_shell_true(self):
        f = _diff_with_line("handler.py", 5, 'subprocess.run("cmd", shell=True)')
        findings = scan_security(f)
        assert any(f.severity == Severity.HIGH for f in findings)

    def test_detect_eval(self):
        f = _diff_with_line("handler.py", 5, 'eval(user_input)')
        findings = scan_security(f)
        assert any(f.severity == Severity.CRITICAL for f in findings)

    def test_detect_pickle_loads(self):
        f = _diff_with_line("handler.py", 5, "pickle.loads(data)")
        findings = scan_security(f)
        assert any("pickle" in f.title for f in findings)

    def test_safe_code_clean(self):
        f = _diff_with_line("safe.py", 5, "print('hello')")
        findings = scan_security(f)
        assert len(findings) == 0

    def test_detect_yaml_load(self):
        f = _diff_with_line("config.py", 10, "config = yaml.load(f)")
        findings = scan_security(f)
        assert any("yaml.load" in f.title for f in findings)

    def test_yaml_safe_load_clean(self):
        f = _diff_with_line("config.py", 10, "config = yaml.safe_load(f)")
        findings = scan_security(f)
        assert len(findings) == 0

    def test_detect_dynamic_import(self):
        f = _diff_with_line("evil.py", 3, '__import__("os")')
        findings = scan_security(f)
        assert any(f.severity == Severity.CRITICAL for f in findings)


class TestAsyncScanner:
    """Async/await anti-pattern detection."""

    def test_detect_time_sleep_in_async(self):
        f = _diff_with_line("worker.py", 8, "time.sleep(0.1)")
        findings = scan_async_errors(f)
        assert any("time.sleep" in x.title.lower() for x in findings)

    def test_detect_get_event_loop(self):
        f = _diff_with_line("worker.py", 8, "loop = asyncio.get_event_loop()")
        findings = scan_async_errors(f)
        assert any("get_event_loop" in x.title for x in findings)

    def test_await_call_clean(self):
        f = _diff_with_line("worker.py", 8, "result = await asyncio.sleep(1)")
        findings = scan_async_errors(f)
        assert len(findings) == 0


class TestResourceLeakScanner:
    """Resource leak detection."""

    def test_detect_open_without_context(self):
        f = _diff_with_line("leak.py", 3, "f = open('data.txt')")
        findings = scan_resource_leaks(f)
        assert any("open() without" in f.title for f in findings)

    def test_with_open_clean(self):
        f = _diff_with_line("safe.py", 3, "with open('data.txt') as f:")
        findings = scan_resource_leaks(f)
        assert len(findings) == 0


class TestDBLifecycleScanner:
    """Database lifecycle issue detection."""

    def test_detect_cursor_creation(self):
        f = _diff_with_line("repo.py", 6, "cursor = conn.cursor()")
        findings = scan_db_lifecycle(f)
        assert any("cursor" in f.title.lower() for f in findings)

    def test_detect_execute_without_commit(self):
        f = _diff_with_line("repo.py", 9, "cursor.execute(sql)")
        findings = scan_db_lifecycle(f)
        assert any("commit" in f.title.lower() for f in findings)


class TestMissingTestsScanner:
    """Missing test detection."""

    def test_new_function_flagged(self):
        f = _diff_with_line("utils.py", 3, "def calculate_average(values):")
        findings = scan_missing_tests(f)
        assert len(findings) == 1
        assert "calculate_average" in findings[0].title

    def test_test_function_not_flagged(self):
        f = _diff_with_line("test_utils.py", 3, "def test_calculate_average():")
        findings = scan_missing_tests(f)
        assert len(findings) == 0

    def test_no_functions_no_findings(self):
        f = _diff_with_line("const.py", 3, "MAX_SIZE = 100")
        findings = scan_missing_tests(f)
        assert len(findings) == 0


class TestSecretInfoScanner:
    """Secret/hardcoded credential detection."""

    def test_detect_openai_key(self):
        f = _diff_with_line("config.py", 3, 'API_KEY = "sk-abc123def456ghi789jkl012mno345"')
        findings = scan_secret_info(f)
        assert len(findings) >= 1
        assert all(f.severity == Severity.CRITICAL for f in findings)

    def test_detect_github_token(self):
        f = _diff_with_line("config.py", 4, 'TOKEN = "github_pat_11AZZJUYA0KF2ao68ScG"')
        findings = scan_secret_info(f)
        assert len(findings) >= 1

    def test_detect_password(self):
        f = _diff_with_line("config.py", 5, 'DB_PASSWORD = "super_secret_db_password"')
        findings = scan_secret_info(f)
        assert any("password" in f.title.lower() for f in findings)

    def test_evidence_is_redacted(self):
        f = _diff_with_line("config.py", 3, 'API_KEY = "sk-abc123def456ghi789jkl012mno345"')
        findings = scan_secret_info(f)
        for f in findings:
            assert "sk-" not in f.evidence  # Evidence must be redacted

    def test_clean_line_no_secrets(self):
        f = _diff_with_line("safe.py", 3, 'logger.info("Processing started")')
        findings = scan_secret_info(f)
        assert len(findings) == 0


class TestScannerRegistry:
    """Scanner registry and combined execution."""

    def test_all_scanners_registered(self):
        scanners = get_available_scanners()
        assert "security" in scanners
        assert "async_error" in scanners
        assert "resource_leak" in scanners
        assert "db_lifecycle" in scanners
        assert "missing_tests" in scanners
        assert "secret_info" in scanners

    def test_selective_scanners(self):
        f = _diff_with_line("test.py", 3, "eval(input())")
        # Only security scanner
        findings = run_scanners(f, enabled=["security"])
        assert all(x.category == FindingCategory.SECURITY for x in findings)

    def test_confidence_filter(self):
        f = _diff_with_line("test.py", 3, "eval(input())")
        # High threshold filters everything
        findings = run_scanners(f, min_confidence=1.0)
        assert len(findings) == 0

    def test_real_diff_security(self, read_diff):
        """Integration: security diff should have multiple findings."""
        files = parse_diff(read_diff("security.diff"))
        all_findings = []
        for f in files:
            all_findings.extend(run_scanners(f))
        # Should have at least os.system, subprocess shell=True, eval, pickle
        assert len(all_findings) >= 4

    def test_real_diff_clean(self, read_diff):
        """Integration: clean diff should have no findings."""
        files = parse_diff(read_diff("clean.diff"))
        all_findings = []
        for f in files:
            all_findings.extend(run_scanners(f))
        assert len(all_findings) == 0


# ── Helpers ──────────────────────────────────────────────────────

def _diff_with_line(filename: str, lineno: int, content: str) -> DiffFile:
    """Create a minimal DiffFile with one added line."""
    f = DiffFile(filename=filename)
    from pipeline.types import DiffHunk
    f.hunks = [DiffHunk(
        header=f"@@ -0,0 +{lineno},1 @@",
        old_start=0, old_count=0,
        new_start=lineno, new_count=1,
        lines=[f"+{content}"],
    )]
    f.raw_lines = [f"+{content}"]
    return f
