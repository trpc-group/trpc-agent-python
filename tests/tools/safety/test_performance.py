import time

from trpc_agent_sdk.tools.safety import Decision
from trpc_agent_sdk.tools.safety import ToolScriptSafetyScanner


def test_500_line_safe_python_scans_under_one_second():
    script = "\n".join(f"value_{i} = {i}" for i in range(500))
    scanner = ToolScriptSafetyScanner()
    started = time.perf_counter()
    report = scanner.scan_script(script, "python")
    elapsed = time.perf_counter() - started
    assert report.decision == Decision.ALLOW
    assert elapsed < 1


def test_500_line_safe_bash_scans_under_one_second():
    script = "\n".join("echo ok" for _ in range(500))
    scanner = ToolScriptSafetyScanner()
    started = time.perf_counter()
    report = scanner.scan_script(script, "bash")
    elapsed = time.perf_counter() - started
    assert report.decision == Decision.ALLOW
    assert elapsed < 1
