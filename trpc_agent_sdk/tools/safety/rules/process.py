"""Process execution safety rule — detects risky subprocess/command invocations.

Rule IDs:
- PROC-001: Execution of non-allowed command (HIGH)
- PROC-002: Shell injection risk (HIGH)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from trpc_agent_sdk.tools.safety.models import (
    Decision,
    Finding,
    Language,
    RiskCategory,
    Severity,
)
from trpc_agent_sdk.tools.safety.rules._base import BaseRule, register_rule
from trpc_agent_sdk.tools.safety.scanner import bash_scanner, python_scanner

if TYPE_CHECKING:
    from trpc_agent_sdk.tools.safety.models import ScanContext
    from trpc_agent_sdk.tools.safety.policy import PolicyConfig

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Python functions that execute subprocesses
_PYTHON_EXEC_FUNCS: set[str] = {
    "os.system",
    "os.popen",
    "os.exec",
    "os.execl",
    "os.execle",
    "os.execlp",
    "os.execlpe",
    "os.execv",
    "os.execve",
    "os.execvp",
    "os.execvpe",
    "os.spawnl",
    "os.spawnle",
    "subprocess.run",
    "subprocess.call",
    "subprocess.check_call",
    "subprocess.check_output",
    "subprocess.Popen",
}

# Python functions with shell injection risk (shell=True semantics)
_PYTHON_SHELL_FUNCS: set[str] = {
    "os.system",
    "os.popen",
}

# Bash dangerous commands (not about files — about system-level process ops)
_BASH_DANGEROUS_COMMANDS: dict[str, str] = {
    "eval": r"\beval\s+",
    "exec": r"\bexec\s+",
    "source_remote": r"\bsource\s+<\(",
    "bash_c": r"\bbash\s+-c\s+",
    "sh_c": r"\bsh\s+-c\s+",
    "nohup": r"\bnohup\s+",
    "crontab": r"\bcrontab\s+",
    "at_cmd": r"\bat\s+",
    "sudo": r"\bsudo\s+",
    "su": r"\bsu\s+",
    "chmod_suid": r"\bchmod\s+[u+]*s",
}


def _extract_command_from_args(str_args: list[str]) -> str | None:
    """Try to extract the command name from string arguments.

    For subprocess.run(["ls", "-la"]), the command is "ls".
    For os.system("ls -la"), the command is "ls".
    """
    if not str_args:
        return None
    first_arg = str_args[0]
    # If it looks like a full command string (contains space), take first word
    if " " in first_arg:
        return first_arg.split()[0]
    return first_arg


# ---------------------------------------------------------------------------
# Rule: PROC-001 — Non-allowed command execution
# ---------------------------------------------------------------------------


@register_rule
class ProcessExecutionRule(BaseRule):
    """Detects execution of commands not in the allowed list."""

    rule_id = "PROC-001"
    category = RiskCategory.PROCESS
    severity = Severity.HIGH
    languages = [Language.PYTHON, Language.BASH]
    description = "Detects subprocess/command execution of non-allowed commands."

    def scan(self, ctx: "ScanContext", policy: "PolicyConfig | None" = None) -> list[Finding]:
        findings: list[Finding] = []
        allowed_commands = policy.process.allowed_commands if policy else []

        if ctx.language == Language.PYTHON and ctx.ast_tree is not None:
            findings.extend(self._scan_python(ctx, allowed_commands))
        elif ctx.language == Language.BASH:
            findings.extend(self._scan_bash(ctx, allowed_commands))

        return findings

    def _scan_python(self, ctx: "ScanContext", allowed_commands: list[str]) -> list[Finding]:
        findings: list[Finding] = []
        tree = ctx.ast_tree

        calls = python_scanner.find_function_calls(tree, _PYTHON_EXEC_FUNCS)
        for call in calls:
            call_name = python_scanner.get_call_name(call)
            str_args = python_scanner.get_string_args(call)
            command = _extract_command_from_args(str_args)

            if command:
                # Extract just the binary name (strip path)
                binary = command.rsplit("/", 1)[-1]
                if binary not in allowed_commands:
                    findings.append(
                        Finding(
                            rule_id=self.rule_id,
                            category=self.category,
                            severity=self.severity,
                            decision=Decision.NEEDS_HUMAN_REVIEW,
                            evidence=f"{call_name}({str_args[0]!r})",
                            line_number=call.lineno,
                            description=f"Execution of non-allowed command: {binary}",
                            recommendation=f"Add '{binary}' to process.allowed_commands if this is expected.",
                        ))
            else:
                # Cannot determine command statically
                findings.append(
                    Finding(
                        rule_id=self.rule_id,
                        category=self.category,
                        severity=self.severity,
                        decision=Decision.NEEDS_HUMAN_REVIEW,
                        confidence=0.7,
                        evidence=call_name,
                        line_number=call.lineno,
                        description=f"Subprocess call with non-static command: {call_name}",
                        recommendation="Ensure the executed command is safe and expected.",
                    ))

        return findings

    def _scan_bash(self, ctx: "ScanContext", allowed_commands: list[str]) -> list[Finding]:
        findings: list[Finding] = []
        patterns = bash_scanner.CompiledPatternSet(_BASH_DANGEROUS_COMMANDS)
        matches = bash_scanner.scan_lines(ctx.source_code, patterns)

        for m in matches:
            # Check if the matched command is in allowed list
            if m.pattern_name not in allowed_commands:
                findings.append(
                    Finding(
                        rule_id=self.rule_id,
                        category=self.category,
                        severity=self.severity,
                        decision=Decision.NEEDS_HUMAN_REVIEW,
                        evidence=m.line_content,
                        line_number=m.line_number,
                        description=f"Dangerous command execution: {m.pattern_name}",
                        recommendation="Verify this command execution is intentional and safe.",
                    ))

        return findings


# ---------------------------------------------------------------------------
# Rule: PROC-002 — Shell injection risk
# ---------------------------------------------------------------------------


@register_rule
class ShellInjectionRule(BaseRule):
    """Detects patterns with shell injection risk."""

    rule_id = "PROC-002"
    category = RiskCategory.PROCESS
    severity = Severity.HIGH
    languages = [Language.PYTHON, Language.BASH]
    description = "Detects shell injection risk patterns (os.system, shell=True, eval, etc.)."

    def scan(self, ctx: "ScanContext", policy: "PolicyConfig | None" = None) -> list[Finding]:
        findings: list[Finding] = []

        if ctx.language == Language.PYTHON and ctx.ast_tree is not None:
            findings.extend(self._scan_python(ctx))
        elif ctx.language == Language.BASH:
            findings.extend(self._scan_bash(ctx))

        return findings

    def _scan_python(self, ctx: "ScanContext") -> list[Finding]:
        findings: list[Finding] = []
        tree = ctx.ast_tree

        # Detect os.system / os.popen (always shell=True semantics)
        shell_calls = python_scanner.find_function_calls(tree, _PYTHON_SHELL_FUNCS)
        for call in shell_calls:
            call_name = python_scanner.get_call_name(call)
            findings.append(
                Finding(
                    rule_id=self.rule_id,
                    category=self.category,
                    severity=self.severity,
                    decision=Decision.NEEDS_HUMAN_REVIEW,
                    evidence=call_name,
                    line_number=call.lineno,
                    description=f"Shell injection risk: {call_name} executes commands through shell",
                    recommendation="Use subprocess.run([...]) with shell=False for safer execution.",
                ))

        # Detect subprocess calls with shell=True keyword arg
        subprocess_funcs = {
            "subprocess.run", "subprocess.call", "subprocess.check_call", "subprocess.check_output", "subprocess.Popen"
        }
        subprocess_calls = python_scanner.find_function_calls(tree, subprocess_funcs)
        for call in subprocess_calls:
            for kw in call.keywords:
                if kw.arg == "shell":
                    # Check if shell=True
                    if hasattr(kw.value, "value") and kw.value.value is True:
                        call_name = python_scanner.get_call_name(call)
                        findings.append(
                            Finding(
                                rule_id=self.rule_id,
                                category=self.category,
                                severity=self.severity,
                                decision=Decision.NEEDS_HUMAN_REVIEW,
                                evidence=f"{call_name}(shell=True)",
                                line_number=call.lineno,
                                description=f"Shell injection risk: {call_name} with shell=True",
                                recommendation="Use shell=False and pass command as a list.",
                            ))

        # Detect eval/exec builtins
        eval_funcs = python_scanner.find_function_calls(tree, {"eval", "exec", "compile"})
        for call in eval_funcs:
            call_name = python_scanner.get_call_name(call)
            findings.append(
                Finding(
                    rule_id=self.rule_id,
                    category=self.category,
                    severity=self.severity,
                    decision=Decision.DENY,
                    evidence=call_name,
                    line_number=call.lineno,
                    description=f"Code injection risk: {call_name}() allows arbitrary code execution",
                    recommendation="Avoid eval/exec. Use safer alternatives for dynamic behavior.",
                ))

        return findings

    def _scan_bash(self, ctx: "ScanContext") -> list[Finding]:
        findings: list[Finding] = []
        # Detect eval and unquoted variable expansion in commands
        injection_patterns = bash_scanner.CompiledPatternSet({
            "eval": r"\beval\s+",
            "backtick_expansion": r"`[^`]+`",
            "unquoted_var_in_cmd": r"\$\{?[A-Za-z_]\w*\}?",
        })
        matches = bash_scanner.scan_lines(ctx.source_code, injection_patterns)

        for m in matches:
            if m.pattern_name == "eval":
                findings.append(
                    Finding(
                        rule_id=self.rule_id,
                        category=self.category,
                        severity=self.severity,
                        decision=Decision.DENY,
                        evidence=m.line_content,
                        line_number=m.line_number,
                        description="Shell injection risk: eval executes arbitrary strings",
                        recommendation="Avoid eval. Use functions or direct commands.",
                    ))
            elif m.pattern_name == "backtick_expansion":
                findings.append(
                    Finding(
                        rule_id=self.rule_id,
                        category=self.category,
                        severity=Severity.MEDIUM,
                        decision=Decision.NEEDS_HUMAN_REVIEW,
                        confidence=0.7,
                        evidence=m.line_content,
                        line_number=m.line_number,
                        description="Backtick command substitution detected",
                        recommendation="Use $(...) for clarity, and ensure substituted content is safe.",
                    ))

        return findings
