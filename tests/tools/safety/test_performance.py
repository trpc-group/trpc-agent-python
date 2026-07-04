import time

from trpc_agent_sdk.tools.safety import ToolScriptSafetyScanner


def test_scans_500_line_script_under_one_second():
    script = "\n".join(f"echo line-{index}" for index in range(500))
    scanner = ToolScriptSafetyScanner()

    started = time.perf_counter()
    report = scanner.scan_script(script, "bash")
    elapsed = time.perf_counter() - started

    assert report.decision.value == "allow"
    assert elapsed <= 1.0
