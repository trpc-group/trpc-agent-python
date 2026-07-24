import yaml

from trpc_agent_sdk.tools.safety import Decision
from trpc_agent_sdk.tools.safety import ToolSafetyPolicy
from trpc_agent_sdk.tools.safety import ToolScriptSafetyScanner


def test_load_default():
    policy = ToolSafetyPolicy.default()
    assert policy.is_domain_allowed("api.example.com")
    assert policy.is_command_allowed("python")
    assert policy.should_block(Decision.DENY)
    assert not policy.should_block(Decision.NEEDS_HUMAN_REVIEW)


def test_load_yaml(tmp_path):
    path = tmp_path / "policy.yaml"
    path.write_text(yaml.safe_dump({"allowed_domains": ["safe.example"], "block_on_review": True}))
    policy = ToolSafetyPolicy.from_file(path)
    assert policy.allowed_domains == ["safe.example"]
    assert policy.block_on_review is True
    assert policy.is_command_allowed("python")


def test_wildcard_domain_allowlist():
    policy = ToolSafetyPolicy.default()
    assert policy.is_domain_allowed("svc.trusted.internal")
    assert not policy.is_domain_allowed("trusted.internal")


def test_denied_path_matching():
    policy = ToolSafetyPolicy.default()
    assert policy.is_path_denied(".env")
    assert policy.is_path_denied("app.pem")
    assert policy.is_path_denied("~/.ssh/id_rsa")
    assert policy.is_path_denied("/etc/passwd")


def test_changing_allowed_domains_changes_decision_without_code_change():
    script = 'import requests\nrequests.get("https://evil.example/collect")'
    scanner = ToolScriptSafetyScanner(ToolSafetyPolicy.default())
    assert scanner.scan_script(script, "python").decision == Decision.DENY

    policy = ToolSafetyPolicy.default()
    policy.allowed_domains = ["evil.example"]
    assert ToolScriptSafetyScanner(policy).scan_script(script, "python").decision == Decision.ALLOW


def test_changing_denied_paths_changes_decision_without_code_change():
    script = 'open("secret.txt").read()'
    assert ToolScriptSafetyScanner(ToolSafetyPolicy.default()).scan_script(script, "python").decision == Decision.ALLOW

    policy = ToolSafetyPolicy.default()
    policy.denied_paths.append("secret.txt")
    assert ToolScriptSafetyScanner(policy).scan_script(script, "python").decision == Decision.DENY


def test_changing_allowed_commands_changes_bash_command_review_behavior():
    script = "awk '{print $1}' data.txt"
    assert ToolScriptSafetyScanner(ToolSafetyPolicy.default()).scan_script(script, "bash").decision == (
        Decision.NEEDS_HUMAN_REVIEW
    )

    policy = ToolSafetyPolicy.default()
    policy.allowed_commands.append("awk")
    assert ToolScriptSafetyScanner(policy).scan_script(script, "bash").decision == Decision.ALLOW
