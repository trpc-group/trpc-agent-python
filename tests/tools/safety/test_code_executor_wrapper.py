# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.

from __future__ import annotations

import asyncio
import logging
from unittest.mock import Mock
from unittest.mock import patch

from pydantic import Field

from trpc_agent_sdk.code_executors import BaseCodeExecutor
from trpc_agent_sdk.code_executors import BaseWorkspaceRuntime
from trpc_agent_sdk.code_executors import CodeBlock
from trpc_agent_sdk.code_executors import CodeBlockDelimiter
from trpc_agent_sdk.code_executors import CodeExecutionInput
from trpc_agent_sdk.code_executors import CodeExecutionResult
from trpc_agent_sdk.code_executors import CodeFile
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.tools.safety import RiskLevel
from trpc_agent_sdk.tools.safety import RiskType
from trpc_agent_sdk.tools.safety import SafetyAuditEvent
from trpc_agent_sdk.tools.safety import SafetyDecision
from trpc_agent_sdk.tools.safety import SafetyGuardedCodeExecutor
from trpc_agent_sdk.tools.safety import SafetyPolicy
from trpc_agent_sdk.tools.safety import SafetyReport
from trpc_agent_sdk.tools.safety import ScanFinding
from trpc_agent_sdk.tools.safety import ScanTarget
from trpc_agent_sdk.tools.safety import ScriptLanguage
from trpc_agent_sdk.types import Outcome


class RecordingDelegate(BaseCodeExecutor):
    calls: list[CodeExecutionInput] = Field(default_factory=list)
    result: CodeExecutionResult = Field(
        default_factory=lambda: CodeExecutionResult(
            outcome=Outcome.OUTCOME_OK,
            output="delegate output",
        )
    )

    async def execute_code(
        self,
        invocation_context: InvocationContext,
        code_execution_input: CodeExecutionInput,
    ) -> CodeExecutionResult:
        self.calls.append(code_execution_input)
        return self.result


def _enable_caplog_logger(name: str) -> None:
    target_logger = logging.getLogger(name)
    target_logger.disabled = False
    target_logger.propagate = True


class RecordingAuditLogger:
    def __init__(self):
        self.events: list[SafetyAuditEvent] = []

    def emit(self, event: SafetyAuditEvent) -> None:
        self.events.append(event)


class StaticScanner:
    def __init__(self, policy: SafetyPolicy, reports: list[SafetyReport] | None = None):
        self.policy = policy
        self.reports = list(reports or [])
        self.targets: list[ScanTarget] = []

    def scan(self, target: ScanTarget) -> SafetyReport:
        self.targets.append(target)
        if self.reports:
            return self.reports.pop(0)
        return _report(self.policy, language=target.language)


class RaisingScanner:
    def __init__(self, policy: SafetyPolicy):
        self.policy = policy

    def scan(self, target: ScanTarget) -> SafetyReport:
        raise RuntimeError("secret-token-value")


class DummyInvocationContext:
    function_call_id = "fc-1"
    agent_name = "agent-a"


def _execute(
    executor: SafetyGuardedCodeExecutor,
    code_execution_input: CodeExecutionInput,
) -> CodeExecutionResult:
    return asyncio.run(executor.execute_code(DummyInvocationContext(), code_execution_input))


def _finding(
    rule_id: str,
    *,
    decision: SafetyDecision,
    risk_level: RiskLevel,
    evidence: str,
    recommendation: str = "Review the code before execution.",
    redacted: bool = False,
) -> ScanFinding:
    return ScanFinding(
        rule_id=rule_id,
        risk_type=RiskType.POLICY_VIOLATION,
        risk_level=risk_level,
        decision=decision,
        message=f"{rule_id} detected.",
        evidence=evidence,
        recommendation=recommendation,
        redacted=redacted,
    )


def _report(
    policy: SafetyPolicy,
    *,
    decision: SafetyDecision = SafetyDecision.ALLOW,
    risk_level: RiskLevel = RiskLevel.LOW,
    findings: list[ScanFinding] | None = None,
    language: ScriptLanguage = ScriptLanguage.UNKNOWN,
    elapsed_ms: float = 1.0,
) -> SafetyReport:
    findings = findings or []
    return SafetyReport(
        decision=decision,
        risk_level=risk_level,
        findings=findings,
        elapsed_ms=elapsed_ms,
        redacted=any(finding.redacted for finding in findings),
        blocked=False,
        language=language,
        policy_name=policy.name,
        metadata={"target_tool": "code_executor"},
    )


class TestSafetyGuardedCodeExecutor:
    def test_allow_calls_delegate_and_records_audit_span(self):
        policy = SafetyPolicy()
        scanner = StaticScanner(policy)
        audit_logger = RecordingAuditLogger()
        delegate = RecordingDelegate()
        executor = SafetyGuardedCodeExecutor(
            delegate=delegate,
            scanner=scanner,
            audit_logger=audit_logger,
        )
        code_input = CodeExecutionInput(code_blocks=[CodeBlock(language="python", code="print('ok')")])

        with patch("trpc_agent_sdk.tools.safety._code_executor.set_safety_span_attributes") as mock_span:
            result = _execute(executor, code_input)

        assert result.output == "delegate output"
        assert delegate.calls == [code_input]
        assert scanner.targets[0].content == "print('ok')"
        assert scanner.targets[0].language == ScriptLanguage.PYTHON
        assert audit_logger.events[0].tool_name == "code_executor"
        assert audit_logger.events[0].function_call_id == "fc-1"
        assert audit_logger.events[0].agent_name == "agent-a"
        mock_span.assert_called_once()

    def test_deny_blocks_delegate_and_returns_multiline_plain_text(self):
        policy = SafetyPolicy()
        audit_logger = RecordingAuditLogger()
        delegate = RecordingDelegate()
        executor = SafetyGuardedCodeExecutor(
            delegate=delegate,
            policy=policy,
            audit_logger=audit_logger,
        )
        code_input = CodeExecutionInput(code_blocks=[CodeBlock(language="bash", code="rm -rf ~/.ssh")])

        result = _execute(executor, code_input)

        assert delegate.calls == []
        assert result.outcome == Outcome.OUTCOME_FAILED
        assert result.output.splitlines()[0] == "Tool code execution blocked by safety policy"
        assert "Decision: deny" in result.output
        assert "Risk level: critical" in result.output
        assert "Rules: FILE_RECURSIVE_DELETE, FILE_SENSITIVE_READ" in result.output
        assert "Evidence: rm -rf ~/.ssh" in result.output
        assert audit_logger.events[0].blocked is True

    def test_review_blocks_by_default_and_can_be_nonblocking(self):
        finding = _finding(
            "PROC_OS_SYSTEM",
            decision=SafetyDecision.NEEDS_HUMAN_REVIEW,
            risk_level=RiskLevel.MEDIUM,
            evidence="subprocess.run(['git', 'status'])",
        )
        blocking_policy = SafetyPolicy()
        blocking_delegate = RecordingDelegate()
        blocking_executor = SafetyGuardedCodeExecutor(
            delegate=blocking_delegate,
            scanner=StaticScanner(
                blocking_policy,
                [
                    _report(
                        blocking_policy,
                        decision=SafetyDecision.NEEDS_HUMAN_REVIEW,
                        risk_level=RiskLevel.MEDIUM,
                        findings=[finding],
                    )
                ],
            ),
        )

        blocked = _execute(
            blocking_executor,
            CodeExecutionInput(code_blocks=[CodeBlock(language="python", code="subprocess.run(['git'])")]),
        )

        assert blocking_delegate.calls == []
        assert blocked.outcome == Outcome.OUTCOME_FAILED
        assert "Decision: needs_human_review" in blocked.output

        nonblocking_policy = SafetyPolicy(review_blocks_execution=False)
        nonblocking_delegate = RecordingDelegate()
        audit_logger = RecordingAuditLogger()
        nonblocking_executor = SafetyGuardedCodeExecutor(
            delegate=nonblocking_delegate,
            scanner=StaticScanner(
                nonblocking_policy,
                [
                    _report(
                        nonblocking_policy,
                        decision=SafetyDecision.NEEDS_HUMAN_REVIEW,
                        risk_level=RiskLevel.MEDIUM,
                        findings=[finding],
                    )
                ],
            ),
            audit_logger=audit_logger,
        )

        allowed = _execute(
            nonblocking_executor,
            CodeExecutionInput(code_blocks=[CodeBlock(language="python", code="subprocess.run(['git'])")]),
        )

        assert allowed.output == "delegate output"
        assert len(nonblocking_delegate.calls) == 1
        assert audit_logger.events[0].decision == SafetyDecision.NEEDS_HUMAN_REVIEW
        assert audit_logger.events[0].blocked is False

    def test_multi_code_blocks_are_scanned_and_reports_are_merged(self):
        policy = SafetyPolicy()
        review_finding = _finding(
            "PROC_OS_SYSTEM",
            decision=SafetyDecision.NEEDS_HUMAN_REVIEW,
            risk_level=RiskLevel.MEDIUM,
            evidence="os.system('pwd')",
        )
        deny_finding = _finding(
            "FILE_RECURSIVE_DELETE",
            decision=SafetyDecision.DENY,
            risk_level=RiskLevel.CRITICAL,
            evidence="rm -rf ~/.ssh",
        )
        scanner = StaticScanner(
            policy,
            [
                _report(
                    policy,
                    decision=SafetyDecision.NEEDS_HUMAN_REVIEW,
                    risk_level=RiskLevel.MEDIUM,
                    findings=[review_finding],
                    language=ScriptLanguage.PYTHON,
                    elapsed_ms=1.5,
                ),
                _report(
                    policy,
                    decision=SafetyDecision.DENY,
                    risk_level=RiskLevel.CRITICAL,
                    findings=[deny_finding],
                    language=ScriptLanguage.SHELL,
                    elapsed_ms=2.5,
                ),
            ],
        )
        audit_logger = RecordingAuditLogger()
        delegate = RecordingDelegate()
        executor = SafetyGuardedCodeExecutor(
            delegate=delegate,
            scanner=scanner,
            audit_logger=audit_logger,
        )

        result = _execute(
            executor,
            CodeExecutionInput(
                code_blocks=[
                    CodeBlock(language="python", code="os.system('pwd')"),
                    CodeBlock(language="bash", code="rm -rf ~/.ssh"),
                ]
            ),
        )

        assert delegate.calls == []
        assert len(scanner.targets) == 2
        assert result.outcome == Outcome.OUTCOME_FAILED
        assert "Rules: PROC_OS_SYSTEM, FILE_RECURSIVE_DELETE" in result.output
        assert audit_logger.events[0].decision == SafetyDecision.DENY
        assert audit_logger.events[0].risk_level == RiskLevel.CRITICAL
        assert audit_logger.events[0].elapsed_ms == 4.0
        assert audit_logger.events[0].language == ScriptLanguage.UNKNOWN
        assert audit_logger.events[0].rule_ids == ["PROC_OS_SYSTEM", "FILE_RECURSIVE_DELETE"]

    def test_fallback_code_is_scanned_when_code_blocks_are_empty(self):
        policy = SafetyPolicy()
        scanner = StaticScanner(policy)
        delegate = RecordingDelegate()
        executor = SafetyGuardedCodeExecutor(delegate=delegate, scanner=scanner)
        code_input = CodeExecutionInput(code="print('fallback')")

        result = _execute(executor, code_input)

        assert result.output == "delegate output"
        assert scanner.targets[0].content == "print('fallback')"
        assert scanner.targets[0].tool_metadata["source"] == "code"
        assert delegate.calls == [code_input]

    def test_executable_input_file_suffix_uses_path_suffix_lower(self):
        policy = SafetyPolicy()
        scanner = StaticScanner(policy)
        delegate = RecordingDelegate()
        executor = SafetyGuardedCodeExecutor(delegate=delegate, scanner=scanner)
        code_input = CodeExecutionInput(
            input_files=[
                CodeFile(
                    name="nested/RUN.SH",
                    content="echo ok",
                    mime_type="text/x-shellscript",
                )
            ]
        )

        result = _execute(executor, code_input)

        assert result.output == "delegate output"
        assert scanner.targets[0].content == "echo ok"
        assert scanner.targets[0].language == ScriptLanguage.SHELL
        assert scanner.targets[0].tool_metadata["file_name"] == "nested/RUN.SH"
        assert delegate.calls == [code_input]

    def test_plain_data_file_is_skipped_and_no_target_delegates_without_audit(self):
        policy = SafetyPolicy()
        scanner = StaticScanner(policy)
        audit_logger = RecordingAuditLogger()
        delegate = RecordingDelegate()
        executor = SafetyGuardedCodeExecutor(
            delegate=delegate,
            scanner=scanner,
            audit_logger=audit_logger,
        )
        code_input = CodeExecutionInput(
            input_files=[CodeFile(name="notes.txt", content="rm -rf ~/.ssh", mime_type="text/plain")]
        )

        with patch("trpc_agent_sdk.tools.safety._code_executor.set_safety_span_attributes") as mock_span:
            result = _execute(executor, code_input)

        assert result.output == "delegate output"
        assert delegate.calls == [code_input]
        assert scanner.targets == []
        assert audit_logger.events == []
        mock_span.assert_not_called()

    def test_empty_input_delegates_without_audit_or_span(self):
        policy = SafetyPolicy()
        scanner = StaticScanner(policy)
        audit_logger = RecordingAuditLogger()
        delegate = RecordingDelegate()
        executor = SafetyGuardedCodeExecutor(
            delegate=delegate,
            scanner=scanner,
            audit_logger=audit_logger,
        )
        code_input = CodeExecutionInput()

        with patch("trpc_agent_sdk.tools.safety._code_executor.set_safety_span_attributes") as mock_span:
            result = _execute(executor, code_input)

        assert result.output == "delegate output"
        assert delegate.calls == [code_input]
        assert scanner.targets == []
        assert audit_logger.events == []
        mock_span.assert_not_called()

    def test_delegate_executor_fields_are_mirrored(self):
        runtime = Mock(spec=BaseWorkspaceRuntime)
        code_delimiters = [CodeBlockDelimiter(start="```bash\n", end="\n```")]
        result_delimiters = [CodeBlockDelimiter(start="<out>", end="</out>")]
        delegate = RecordingDelegate(
            optimize_data_file=True,
            stateful=True,
            error_retry_attempts=5,
            execute_once_per_invocation=True,
            code_block_delimiters=code_delimiters,
            execution_result_delimiters=result_delimiters,
            workspace_runtime=runtime,
            ignore_codes=["ignored"],
        )

        executor = SafetyGuardedCodeExecutor(delegate=delegate)

        assert executor.optimize_data_file is True
        assert executor.stateful is True
        assert executor.error_retry_attempts == 5
        assert executor.execute_once_per_invocation is True
        assert executor.code_block_delimiters == code_delimiters
        assert executor.execution_result_delimiters == result_delimiters
        assert executor.workspace_runtime == runtime
        assert executor.ignore_codes == ["ignored"]

    def test_audit_and_span_do_not_contain_secret_values(self):
        policy = SafetyPolicy()
        scanner = StaticScanner(policy)
        audit_logger = RecordingAuditLogger()
        delegate = RecordingDelegate()
        executor = SafetyGuardedCodeExecutor(
            delegate=delegate,
            scanner=scanner,
            audit_logger=audit_logger,
        )
        secret = "secret-token-value"
        code_input = CodeExecutionInput(
            code_blocks=[CodeBlock(language="python", code=f"print('{secret}')")],
            input_files=[CodeFile(name="script.sh", content=f"echo {secret}", mime_type="text/x-shellscript")],
        )

        with patch("trpc_agent_sdk.tools.safety._code_executor.set_safety_span_attributes") as mock_span:
            result = _execute(executor, code_input)

        assert result.output == "delegate output"
        assert len(scanner.targets) == 2
        dumped_audit = "".join(event.model_dump_json() for event in audit_logger.events)
        dumped_span_calls = str(mock_span.call_args_list)
        assert secret not in dumped_audit
        assert secret not in dumped_span_calls

    def test_fail_closed_blocks_and_logs_without_exception_detail(self, caplog):
        policy = SafetyPolicy(fail_closed=True)
        audit_logger = RecordingAuditLogger()
        delegate = RecordingDelegate()
        executor = SafetyGuardedCodeExecutor(
            delegate=delegate,
            scanner=RaisingScanner(policy),
            audit_logger=audit_logger,
        )
        _enable_caplog_logger("trpc_agent_sdk.tools.safety._code_executor")
        caplog.set_level(logging.WARNING, logger="trpc_agent_sdk.tools.safety._code_executor")

        with patch("trpc_agent_sdk.tools.safety._code_executor.set_safety_span_attributes") as mock_span:
            result = _execute(executor, CodeExecutionInput(code="print('ok')"))

        assert delegate.calls == []
        assert result.outcome == Outcome.OUTCOME_FAILED
        assert "Tool code execution blocked by safety policy" in result.output
        assert "secret-token-value" not in result.output
        assert audit_logger.events[0].blocked is True
        mock_span.assert_called_once()
        assert "RuntimeError" in caplog.text
        assert "secret-token-value" not in caplog.text

    def test_fail_open_delegates_and_skips_audit_span(self, caplog):
        policy = SafetyPolicy(fail_closed=False)
        audit_logger = RecordingAuditLogger()
        delegate = RecordingDelegate()
        executor = SafetyGuardedCodeExecutor(
            delegate=delegate,
            scanner=RaisingScanner(policy),
            audit_logger=audit_logger,
        )
        _enable_caplog_logger("trpc_agent_sdk.tools.safety._code_executor")
        caplog.set_level(logging.WARNING, logger="trpc_agent_sdk.tools.safety._code_executor")

        with patch("trpc_agent_sdk.tools.safety._code_executor.set_safety_span_attributes") as mock_span:
            result = _execute(executor, CodeExecutionInput(code="print('ok')"))

        assert result.output == "delegate output"
        assert len(delegate.calls) == 1
        assert audit_logger.events == []
        mock_span.assert_not_called()
        assert "RuntimeError" in caplog.text
        assert "secret-token-value" not in caplog.text

    def test_package_export_is_safety_only(self):
        import trpc_agent_sdk.code_executors as code_executors
        import trpc_agent_sdk.tools.safety as safety

        assert safety.SafetyGuardedCodeExecutor is SafetyGuardedCodeExecutor
        assert not hasattr(code_executors, "SafetyGuardedCodeExecutor")
