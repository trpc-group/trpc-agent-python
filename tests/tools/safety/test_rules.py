"""Tests for trpc_agent_sdk.tools.safety._rules."""

from __future__ import annotations

from typing import Iterable

import pytest

from trpc_agent_sdk.tools.safety._facts import (
    ConcurrencyFact,
    DependencyInstallFact,
    DynamicExecFact,
    FileDeleteFact,
    FileReadFact,
    FileWriteFact,
    ForkBombFact,
    LargeWriteFact,
    Loc,
    LongSleepFact,
    NetworkFact,
    ParseErrorFact,
    PrivilegeFact,
    ProcessFact,
    ScriptFacts,
    SecretFlowFact,
    ShellOperatorFact,
    UnboundedLoopFact,
)
from trpc_agent_sdk.tools.safety._models import (
    RiskLevel,
    SafetyDecision,
    SafetyFinding,
    ScriptLanguage,
)
from trpc_agent_sdk.tools.safety._policy import (
    NetworkPolicy,
    PathsPolicy,
    load_safety_policy_dict,
)
from trpc_agent_sdk.tools.safety._redaction import Redactor
from trpc_agent_sdk.tools.safety._rules import (
    CATALOG,
    SafetyRule,
    _concurrency_limit_for,
    _default_unknown,
    _first_token,
    _is_ip_literal,
    _matches_denied_path,
    _SAFE_BASH_COMMANDS,
    check_concurrency,
    check_dependency_install,
    check_dynamic_exec,
    check_file_credential_read,
    check_file_denied_write,
    check_file_dotenv_read,
    check_file_recursive_delete,
    check_fork_bomb,
    check_large_write,
    check_long_sleep,
    check_network_dynamic_target,
    check_network_ip_literals,
    check_network_non_allowlist,
    check_parse_error,
    check_privilege_escalation,
    check_process_exec,
    check_secret_to_file,
    check_secret_to_network,
    check_secret_to_output,
    check_shell_injection,
    check_shell_operator,
    check_unbounded_loop,
    default_rules,
    evaluate_facts,
    resolve_decision,
)


def _redactor() -> Redactor:
    return Redactor()


def _policy(**overrides):
    return load_safety_policy_dict({"version": "1", **overrides})


def _ids(findings: Iterable[SafetyFinding]) -> set[str]:
    return {f.rule_id for f in findings}


class TestResolveDecision:

    def test_no_override_returns_proposed(self):
        p = _policy()
        assert resolve_decision("X", SafetyDecision.DENY, p) \
            == SafetyDecision.DENY

    def test_override_allow(self):
        p = _policy(rule_overrides={"X": "allow"})
        assert resolve_decision("X", SafetyDecision.DENY, p) \
            == SafetyDecision.ALLOW

    def test_override_review(self):
        p = _policy(rule_overrides={"X": "needs_human_review"})
        assert resolve_decision("X", SafetyDecision.DENY, p) \
            == SafetyDecision.NEEDS_HUMAN_REVIEW

    def test_override_deny(self):
        p = _policy(rule_overrides={"X": "deny"})
        assert resolve_decision("X", SafetyDecision.ALLOW, p) \
            == SafetyDecision.DENY

    def test_invalid_override_returns_proposed(self):
        # policy loader rejects invalid values, but rule code is defensive.
        p = _policy()
        out = resolve_decision("X", SafetyDecision.ALLOW, p)
        assert out == SafetyDecision.ALLOW


class TestDefaultUnknown:

    def test_review(self):
        p = _policy()
        assert _default_unknown(p) == SafetyDecision.NEEDS_HUMAN_REVIEW

    def test_deny(self):
        p = _policy(defaults={"unknown_construct": "deny"})
        assert _default_unknown(p) == SafetyDecision.DENY

    def test_allow(self):
        p = _policy(defaults={"unknown_construct": "allow"})
        assert _default_unknown(p) == SafetyDecision.ALLOW


class TestFileRecursiveDelete:

    def test_recursive_delete_denied(self):
        facts = ScriptFacts(file_deletes=(FileDeleteFact(recursive=True, target="/x"), ))
        out = check_file_recursive_delete(facts, _policy(), ScriptLanguage.PYTHON, _redactor())
        assert _ids(out) == {"FILE001_RECURSIVE_DELETE"}
        assert out[0].decision == SafetyDecision.DENY
        assert out[0].risk_level == RiskLevel.CRITICAL

    def test_non_recursive_skipped(self):
        facts = ScriptFacts(file_deletes=(FileDeleteFact(recursive=False), ))
        out = check_file_recursive_delete(facts, _policy(), ScriptLanguage.PYTHON, _redactor())
        assert out == []


class TestFileDeniedWrite:

    def test_denied_path(self):
        facts = ScriptFacts(file_writes=(FileWriteFact(target="/etc/x", explicit=True), ))
        out = check_file_denied_write(
            facts,
            _policy(paths={"deny": ["/etc/**"]}),
            ScriptLanguage.PYTHON,
            _redactor(),
        )
        assert _ids(out) == {"FILE002_DENIED_WRITE"}
        assert out[0].decision == SafetyDecision.DENY

    def test_safe_path_no_finding(self):
        facts = ScriptFacts(file_writes=(FileWriteFact(target="/tmp/x", explicit=True), ))
        out = check_file_denied_write(facts, _policy(), ScriptLanguage.PYTHON, _redactor())
        assert out == []

    def test_dynamic_target_review(self):
        facts = ScriptFacts(file_writes=(FileWriteFact(target="<dynamic>", explicit=False), ))
        out = check_file_denied_write(facts, _policy(), ScriptLanguage.PYTHON, _redactor())
        assert _ids(out) == {"FILE002_DENIED_WRITE"}

    def test_dynamic_target_on_denied_path(self):
        # If dynamic but matches a denied path glob (it won't here), rule
        # still emits review for the dynamic case.
        facts = ScriptFacts(file_writes=(FileWriteFact(target="<dynamic>", explicit=False), ))
        out = check_file_denied_write(facts, _policy(), ScriptLanguage.PYTHON, _redactor())
        # Always review on dynamic.
        assert out


class TestCredentialRead:

    def test_credential_read(self):
        facts = ScriptFacts(file_reads=(FileReadFact(target="/home/x/.ssh/id_rsa", kind="credential"), ))
        out = check_file_credential_read(facts, _policy(), ScriptLanguage.PYTHON, _redactor())
        assert _ids(out) == {"FILE003_CREDENTIAL_READ"}
        assert out[0].risk_level == RiskLevel.CRITICAL

    def test_regular_read_skipped(self):
        facts = ScriptFacts(file_reads=(FileReadFact(target="/tmp/x", kind="regular"), ))
        out = check_file_credential_read(facts, _policy(), ScriptLanguage.PYTHON, _redactor())
        assert out == []


class TestDotenvRead:

    def test_dotenv_read(self):
        facts = ScriptFacts(file_reads=(FileReadFact(target=".env", kind="dotenv"), ))
        out = check_file_dotenv_read(facts, _policy(), ScriptLanguage.BASH, _redactor())
        assert _ids(out) == {"FILE004_DOTENV_READ"}

    def test_non_dotenv_skipped(self):
        facts = ScriptFacts(file_reads=(FileReadFact(target="/tmp/x", kind="regular"), ))
        out = check_file_dotenv_read(facts, _policy(), ScriptLanguage.BASH, _redactor())
        assert out == []


class TestNetworkAllowlist:

    def test_non_allowlisted_host(self):
        facts = ScriptFacts(network_calls=(NetworkFact(target="evil.example.com", library="requests"), ))
        out = check_network_non_allowlist(
            facts,
            _policy(network={"allow_domains": ["api.example.com"]}),
            ScriptLanguage.PYTHON,
            _redactor(),
        )
        assert _ids(out) == {"NET001_DOMAIN_NOT_ALLOWED"}

    def test_allowlisted_host_skipped(self):
        facts = ScriptFacts(network_calls=(NetworkFact(target="api.example.com", library="requests"), ))
        out = check_network_non_allowlist(
            facts,
            _policy(network={"allow_domains": ["api.example.com"]}),
            ScriptLanguage.PYTHON,
            _redactor(),
        )
        assert out == []

    def test_dynamic_network_skipped_by_this_rule(self):
        # dynamic=True goes to NET002 rule
        facts = ScriptFacts(network_calls=(NetworkFact(target="", library="requests", dynamic=True), ))
        out = check_network_non_allowlist(facts, _policy(), ScriptLanguage.PYTHON, _redactor())
        assert out == []


class TestIpLiterals:

    def test_ip_literal_blocked(self):
        facts = ScriptFacts(network_calls=(NetworkFact(target="8.8.8.8", library="requests"), ))
        out = check_network_ip_literals(facts, _policy(), ScriptLanguage.PYTHON, _redactor())
        assert _ids(out) == {"NET003_IP_LITERAL"}

    def test_hostname_not_blocked(self):
        facts = ScriptFacts(network_calls=(NetworkFact(target="api.example.com", library="requests"), ))
        out = check_network_ip_literals(facts, _policy(), ScriptLanguage.PYTHON, _redactor())
        assert out == []

    def test_disable_ip_block(self):
        facts = ScriptFacts(network_calls=(NetworkFact(target="8.8.8.8", library="requests"), ))
        out = check_network_ip_literals(
            facts,
            _policy(network={
                "allow_domains": [],
                "deny_ip_literals": False
            }),
            ScriptLanguage.PYTHON,
            _redactor(),
        )
        assert out == []


class TestDynamicNetworkTarget:

    def test_dynamic_target_review(self):
        facts = ScriptFacts(network_calls=(NetworkFact(target="", library="requests", dynamic=True), ))
        out = check_network_dynamic_target(facts, _policy(), ScriptLanguage.PYTHON, _redactor())
        assert _ids(out) == {"NET002_DYNAMIC_TARGET"}

    def test_static_target_skipped(self):
        facts = ScriptFacts(network_calls=(NetworkFact(target="api.example.com", library="requests"), ))
        out = check_network_dynamic_target(facts, _policy(), ScriptLanguage.PYTHON, _redactor())
        assert out == []

    def test_default_allow_skips_finding(self):
        facts = ScriptFacts(network_calls=(NetworkFact(target="", library="requests", dynamic=True), ))
        out = check_network_dynamic_target(
            facts,
            _policy(defaults={"unknown_construct": "allow"}),
            ScriptLanguage.PYTHON,
            _redactor(),
        )
        assert out == []


class TestProcessExec:

    def test_deny_list_executable(self):
        facts = ScriptFacts(process_calls=(ProcessFact(command="rm"), ))
        out = check_process_exec(
            facts,
            _policy(commands={
                "allow": [],
                "deny": ["rm"]
            }),
            ScriptLanguage.BASH,
            _redactor(),
        )
        assert _ids(out) == {"PROC001_PROCESS_EXEC"}

    def test_shell_true_routed_to_proc002(self):
        facts = ScriptFacts(process_calls=(ProcessFact(command="ls", shell=True), ))
        out = check_process_exec(facts, _policy(), ScriptLanguage.PYTHON, _redactor())
        assert "PROC001_PROCESS_EXEC" not in _ids(out)

    def test_operators_routed_to_proc003(self):
        facts = ScriptFacts(process_calls=(ProcessFact(command="ls | grep", has_operators=True), ))
        out = check_process_exec(facts, _policy(), ScriptLanguage.BASH, _redactor())
        assert "PROC001_PROCESS_EXEC" not in _ids(out)

    def test_empty_executable_review(self):
        facts = ScriptFacts(process_calls=(ProcessFact(command=""), ))
        out = check_process_exec(facts, _policy(), ScriptLanguage.PYTHON, _redactor())
        assert _ids(out) == {"PROC001_PROCESS_EXEC"}

    def test_safe_bash_command_skipped(self):
        facts = ScriptFacts(process_calls=(ProcessFact(command="ls"), ))
        out = check_process_exec(facts, _policy(), ScriptLanguage.BASH, _redactor())
        assert out == []

    def test_allow_list_unrecognized_review(self):
        facts = ScriptFacts(process_calls=(ProcessFact(command="customexec"), ))
        out = check_process_exec(
            facts,
            _policy(commands={
                "allow": ["ls"],
                "deny": []
            }),
            ScriptLanguage.BASH,
            _redactor(),
        )
        assert _ids(out) == {"PROC001_PROCESS_EXEC"}

    @pytest.mark.parametrize("command", ["curl", "ssh", "git"])
    def test_allow_list_overrides_builtin_safe_commands(self, command):
        facts = ScriptFacts(process_calls=(ProcessFact(command=command), ))
        out = check_process_exec(
            facts,
            _policy(commands={
                "allow": ["ls"],
                "deny": []
            }),
            ScriptLanguage.BASH,
            _redactor(),
        )
        assert _ids(out) == {"PROC001_PROCESS_EXEC"}


class TestShellInjection:

    def test_shell_true_emits_critical(self):
        facts = ScriptFacts(process_calls=(ProcessFact(command="ls", shell=True), ))
        out = check_shell_injection(facts, _policy(), ScriptLanguage.PYTHON, _redactor())
        assert _ids(out) == {"PROC002_SHELL_INJECTION"}
        assert out[0].risk_level == RiskLevel.CRITICAL

    def test_no_shell_no_finding(self):
        facts = ScriptFacts(process_calls=(ProcessFact(command="ls", shell=False), ))
        out = check_shell_injection(facts, _policy(), ScriptLanguage.PYTHON, _redactor())
        assert out == []


class TestShellOperator:

    def test_operator_review(self):
        facts = ScriptFacts(shell_operators=(ShellOperatorFact(operator="|"), ))
        out = check_shell_operator(facts, _policy(), ScriptLanguage.BASH, _redactor())
        assert _ids(out) == {"PROC003_SHELL_OPERATOR"}

    def test_default_allow_skips(self):
        facts = ScriptFacts(shell_operators=(ShellOperatorFact(operator="|"), ))
        out = check_shell_operator(
            facts,
            _policy(defaults={"unknown_construct": "allow"}),
            ScriptLanguage.BASH,
            _redactor(),
        )
        assert out == []


class TestPrivilege:

    def test_sudo_denied(self):
        facts = ScriptFacts(privilege_commands=(PrivilegeFact(command="sudo"), ))
        out = check_privilege_escalation(facts, _policy(), ScriptLanguage.BASH, _redactor())
        assert _ids(out) == {"PROC004_PRIVILEGE"}
        assert out[0].risk_level == RiskLevel.CRITICAL


class TestDependencyInstall:

    def test_default_deny(self):
        facts = ScriptFacts(dependency_installs=(DependencyInstallFact(manager="pip", command="pip install x"), ))
        out = check_dependency_install(facts, _policy(), ScriptLanguage.BASH, _redactor())
        assert _ids(out) == {"DEP001_ENV_MUTATION"}

    def test_allow_skips(self):
        facts = ScriptFacts(dependency_installs=(DependencyInstallFact(manager="pip", command="pip install x"), ))
        out = check_dependency_install(
            facts,
            _policy(dependencies={"decision": "allow"}),
            ScriptLanguage.BASH,
            _redactor(),
        )
        assert out == []

    def test_review_path(self):
        facts = ScriptFacts(dependency_installs=(DependencyInstallFact(manager="pip", command="pip install x"), ))
        out = check_dependency_install(
            facts,
            _policy(dependencies={"decision": "needs_human_review"}),
            ScriptLanguage.BASH,
            _redactor(),
        )
        assert out
        assert out[0].decision == SafetyDecision.NEEDS_HUMAN_REVIEW
        assert out[0].risk_level == RiskLevel.MEDIUM


class TestUnboundedLoop:

    def test_loop_denied(self):
        facts = ScriptFacts(unbounded_loops=(UnboundedLoopFact(kind="while-True"), ))
        out = check_unbounded_loop(facts, _policy(), ScriptLanguage.PYTHON, _redactor())
        assert _ids(out) == {"RES001_UNBOUNDED_LOOP"}


class TestForkBomb:

    def test_bomb_denied(self):
        facts = ScriptFacts(fork_bombs=(ForkBombFact(pattern="classic"), ))
        out = check_fork_bomb(facts, _policy(), ScriptLanguage.BASH, _redactor())
        assert _ids(out) == {"RES002_FORK_BOMB"}
        assert out[0].risk_level == RiskLevel.CRITICAL


class TestLongSleep:

    def test_static_within_limit_skipped(self):
        facts = ScriptFacts(long_sleeps=(LongSleepFact(duration_seconds=5.0, raw="5"), ))
        out = check_long_sleep(facts, _policy(), ScriptLanguage.PYTHON, _redactor())
        assert out == []

    def test_static_over_limit_denied(self):
        facts = ScriptFacts(long_sleeps=(LongSleepFact(duration_seconds=120.0, raw="120"), ))
        out = check_long_sleep(facts, _policy(), ScriptLanguage.PYTHON, _redactor())
        assert _ids(out) == {"RES003_LONG_SLEEP"}
        assert out[0].decision == SafetyDecision.DENY

    def test_dynamic_duration_review(self):
        facts = ScriptFacts(long_sleeps=(LongSleepFact(duration_seconds=None, raw="some_var"), ))
        out = check_long_sleep(facts, _policy(), ScriptLanguage.PYTHON, _redactor())
        assert _ids(out) == {"RES003_LONG_SLEEP"}
        assert out[0].risk_level == RiskLevel.LOW


class TestConcurrency:

    def test_within_limit_skipped(self):
        facts = ScriptFacts(concurrency=(ConcurrencyFact(count=2, raw="threading.Thread"), ))
        out = check_concurrency(facts, _policy(), ScriptLanguage.PYTHON, _redactor())
        assert out == []

    def test_over_limit_denied(self):
        facts = ScriptFacts(concurrency=(ConcurrencyFact(count=100, raw="threading.Thread"), ))
        out = check_concurrency(facts, _policy(), ScriptLanguage.PYTHON, _redactor())
        assert _ids(out) == {"RES004_CONCURRENCY"}

    def test_dynamic_count_review(self):
        facts = ScriptFacts(concurrency=(ConcurrencyFact(count=None, raw="threading.Thread"), ))
        out = check_concurrency(facts, _policy(), ScriptLanguage.PYTHON, _redactor())
        assert _ids(out) == {"RES004_CONCURRENCY"}


class TestLargeWrite:

    def test_within_budget(self):
        facts = ScriptFacts(large_writes=(LargeWriteFact(size=100, target="/tmp/x", raw="open"), ))
        out = check_large_write(facts, _policy(), ScriptLanguage.PYTHON, _redactor())
        assert out == []

    def test_over_budget(self):
        # Default max_file_write_bytes is 10_485_760; use a larger size
        # so the threshold check fires.
        facts = ScriptFacts(large_writes=(LargeWriteFact(size=20_000_000, target="/tmp/x", raw="open"), ))
        out = check_large_write(facts, _policy(), ScriptLanguage.PYTHON, _redactor())
        assert _ids(out) == {"RES005_LARGE_WRITE"}

    def test_dynamic_size_review(self):
        facts = ScriptFacts(large_writes=(LargeWriteFact(size=None, target="/tmp/x", raw="open"), ))
        out = check_large_write(facts, _policy(), ScriptLanguage.PYTHON, _redactor())
        assert _ids(out) == {"RES005_LARGE_WRITE"}


class TestSecretFlows:

    def test_output_sink(self):
        facts = ScriptFacts(secret_flows=(SecretFlowFact(source="tainted", sink="print", sink_kind="output"), ))
        out = check_secret_to_output(facts, _policy(), ScriptLanguage.PYTHON, _redactor())
        assert _ids(out) == {"SECRET001_LOG_SINK"}

    def test_file_sink(self):
        facts = ScriptFacts(secret_flows=(SecretFlowFact(source="tainted", sink="open", sink_kind="file"), ))
        out = check_secret_to_file(facts, _policy(), ScriptLanguage.PYTHON, _redactor())
        assert _ids(out) == {"SECRET002_FILE_SINK"}

    def test_network_sink(self):
        facts = ScriptFacts(
            secret_flows=(SecretFlowFact(source="tainted", sink="requests.post", sink_kind="network"), ))
        out = check_secret_to_network(facts, _policy(), ScriptLanguage.PYTHON, _redactor())
        assert _ids(out) == {"SECRET003_NETWORK_SINK"}


class TestParseError:

    def test_parse_error_default_review(self):
        facts = ScriptFacts(parse_errors=(ParseErrorFact(message="boom"), ))
        out = check_parse_error(facts, _policy(), ScriptLanguage.PYTHON, _redactor())
        assert _ids(out) == {"PARSE001_UNCERTAIN"}

    def test_default_allow_skips(self):
        facts = ScriptFacts(parse_errors=(ParseErrorFact(message="boom"), ))
        out = check_parse_error(
            facts,
            _policy(defaults={"unknown_construct": "allow"}),
            ScriptLanguage.PYTHON,
            _redactor(),
        )
        assert out == []


class TestDynamicExec:

    def test_exec_review(self):
        facts = ScriptFacts(dynamic_execs=(DynamicExecFact(kind="eval"), ))
        out = check_dynamic_exec(facts, _policy(), ScriptLanguage.PYTHON, _redactor())
        assert _ids(out) == {"OBF001_DYNAMIC_EXEC"}

    def test_allow_skips(self):
        facts = ScriptFacts(dynamic_execs=(DynamicExecFact(kind="eval"), ))
        out = check_dynamic_exec(
            facts,
            _policy(defaults={"unknown_construct": "allow"}),
            ScriptLanguage.PYTHON,
            _redactor(),
        )
        assert out == []


class TestEvaluateFacts:

    def test_no_facts_no_findings(self):
        out = evaluate_facts(ScriptFacts(), _policy(), ScriptLanguage.PYTHON, _redactor())
        assert out == []

    def test_aggregates_multiple(self):
        facts = ScriptFacts(
            unbounded_loops=(UnboundedLoopFact(kind="x"), ),
            fork_bombs=(ForkBombFact(pattern="x"), ),
        )
        out = evaluate_facts(facts, _policy(), ScriptLanguage.PYTHON, _redactor())
        assert {"RES001_UNBOUNDED_LOOP", "RES002_FORK_BOMB"} <= _ids(out)


class TestHelperFunctions:

    def test_first_token_simple(self):
        assert _first_token("ls -l") == "ls"

    def test_first_token_empty(self):
        assert _first_token("") == ""

    def test_first_token_whitespace_only(self):
        assert _first_token("   ") == ""

    def test_is_ip_literal_v4(self):
        assert _is_ip_literal("8.8.8.8") is True

    def test_is_ip_literal_v6(self):
        assert _is_ip_literal("::1") is True

    def test_is_ip_literal_bracketed(self):
        assert _is_ip_literal("[::1]") is True

    def test_is_ip_literal_hostname(self):
        assert _is_ip_literal("api.example.com") is False

    def test_concurrency_limit_for_processes(self):
        from trpc_agent_sdk.tools.safety._facts import ConcurrencyFact
        fact = ConcurrencyFact(raw="multiprocessing.Process")
        p = _policy(limits={"max_processes": 4, "max_parallel_tasks": 99})
        assert _concurrency_limit_for(fact, p) == 4

    def test_concurrency_limit_for_threads(self):
        from trpc_agent_sdk.tools.safety._facts import ConcurrencyFact
        fact = ConcurrencyFact(raw="threading.Thread")
        p = _policy(limits={"max_processes": 4, "max_parallel_tasks": 16})
        assert _concurrency_limit_for(fact, p) == 16

    def test_matches_denied_path_explicit(self):
        p = _policy(paths={"deny": ["/etc/**"]})
        assert _matches_denied_path("/etc/passwd", p) is True

    def test_matches_denied_path_relative_env(self):
        p = _policy()
        assert _matches_denied_path(".env", p) is True

    def test_matches_denied_path_safe(self):
        # Use a deny pattern that does not match arbitrary basenames via
        # the ** fallback.
        p = _policy(paths={"deny": ["/etc/passwd"]})
        assert _matches_denied_path("/tmp/x", p) is False

    def test_matches_denied_path_empty(self):
        p = _policy()
        assert _matches_denied_path("", p) is False

    def test_safe_bash_commands_contains_read_only(self):
        assert "ls" in _SAFE_BASH_COMMANDS
        assert "cat" in _SAFE_BASH_COMMANDS


class TestDefaultRulesAndCatalog:

    def test_default_rules_count(self):
        rules = default_rules()
        assert len(rules) == 3
        ids = [r.rule_id for r in rules]
        assert "python_scanner" in ids
        assert "bash_scanner" in ids
        assert "cross_field_scanner" in ids

    def test_catalog_is_tuple(self):
        assert isinstance(CATALOG, tuple)
        assert len(CATALOG) >= 20

    def test_default_rules_implement_protocol(self):
        rules = default_rules()
        for rule in rules:
            assert isinstance(rule, SafetyRule)
