from pathlib import Path

from trpc_agent_sdk.tools.safety import Decision
from trpc_agent_sdk.tools.safety import ToolScriptSafetyScanner

SAMPLES = Path("examples/tool_safety/samples")


def test_sample_matrix_metrics():
    scanner = ToolScriptSafetyScanner()
    matrix = {
        "safe_python.py": Decision.ALLOW,
        "safe_bash.sh": Decision.ALLOW,
        "dangerous_delete.sh": Decision.DENY,
        "read_env.py": Decision.DENY,
        "read_ssh_key.py": Decision.DENY,
        "credential_file_key.py": Decision.DENY,
        "network_non_whitelist.py": Decision.DENY,
        "network_whitelist.py": Decision.ALLOW,
        "subprocess_call.py": Decision.NEEDS_HUMAN_REVIEW,
        "shell_injection.py": Decision.NEEDS_HUMAN_REVIEW,
        "dependency_install.sh": Decision.DENY,
        "infinite_loop.py": Decision.NEEDS_HUMAN_REVIEW,
        "sensitive_output.py": Decision.DENY,
        "bash_pipe_exfiltration.sh": Decision.DENY,
        "dynamic_url_review.py": Decision.NEEDS_HUMAN_REVIEW,
        "eval_review.py": Decision.NEEDS_HUMAN_REVIEW,
    }
    actual = {}
    for sample, expected in matrix.items():
        language = "bash" if sample.endswith(".sh") else None
        actual[sample] = scanner.scan_file(str(SAMPLES / sample), language=language).decision
        assert actual[sample] == expected

    high_risk = [sample for sample, expected in matrix.items() if expected == Decision.DENY]
    detected = [sample for sample in high_risk if actual[sample] == Decision.DENY]
    assert len(detected) / len(high_risk) >= 0.9

    safe = [sample for sample, expected in matrix.items() if expected == Decision.ALLOW]
    false_positive = [sample for sample in safe if actual[sample] != Decision.ALLOW]
    assert len(false_positive) / len(safe) <= 0.1

    for sample in ("read_env.py", "dangerous_delete.sh", "network_non_whitelist.py"):
        assert actual[sample] == Decision.DENY
