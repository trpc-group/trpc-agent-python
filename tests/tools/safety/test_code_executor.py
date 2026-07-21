import json

from pydantic import PrivateAttr

from trpc_agent_sdk.code_executors import BaseCodeExecutor
from trpc_agent_sdk.code_executors import CodeBlock
from trpc_agent_sdk.code_executors import CodeBlockDelimiter
from trpc_agent_sdk.code_executors import CodeExecutionInput
from trpc_agent_sdk.code_executors import CodeExecutionResult
from trpc_agent_sdk.tools.safety._code_executor import SafetyGuardedCodeExecutor
from trpc_agent_sdk.tools.safety._guard import ToolSafetyGuard
from trpc_agent_sdk.tools.safety._models import RiskLevel
from trpc_agent_sdk.tools.safety._models import SafetyDecision
from trpc_agent_sdk.tools.safety._models import SafetyReport
from trpc_agent_sdk.tools.safety._policy import ToolSafetyPolicy
from trpc_agent_sdk.types import Outcome


class RecordingExecutor(BaseCodeExecutor):
    work_dir: str = ""
    timeout: float | None = None
    environment: dict[str, str] | None = None
    _calls = PrivateAttr(default_factory=list)
    _closed = PrivateAttr(default=False)

    async def execute_code(self, invocation_context, code_execution_input):
        self._calls.append((invocation_context, code_execution_input))
        return CodeExecutionResult(outcome=Outcome.OUTCOME_OK, output="delegated")

    def code_block_delimiter(self):
        return [CodeBlockDelimiter(start="```custom\n", end="\n```")]

    def close(self):
        self._closed = True


class ScriptScanner:

    def __init__(self, policy):
        self.policy = policy
        self.requests = []

    def scan(self, request):
        self.requests.append(request)
        denied = "danger" in request.script
        decision = SafetyDecision.DENY if denied else SafetyDecision.ALLOW
        return SafetyReport(
            tool_name=request.tool_name,
            language=request.language,
            decision=decision,
            risk_level=RiskLevel.HIGH if denied else RiskLevel.LOW,
            rule_ids=["TEST001"] if denied else [],
            duration_ms=0.1,
            script_sha256=("d" if denied else "e") * 64,
            policy_version=self.policy.version,
            redacted=True,
            blocked=denied,
        )


class BrokenScanner(ScriptScanner):

    def scan(self, request):
        del request
        raise RuntimeError("scanner failed with secret material")


def _wrapper(inner=None):
    policy = ToolSafetyPolicy()
    scanner = ScriptScanner(policy)
    events = []
    inner = inner or RecordingExecutor()
    wrapper = SafetyGuardedCodeExecutor(
        inner=inner,
        guard=ToolSafetyGuard(policy, scanner=scanner, audit_sink=events.append),
    )
    return wrapper, inner, scanner, events


async def test_code_executor_scans_all_blocks_and_aggregates_denial():
    wrapper, inner, scanner, events = _wrapper()
    execution_input = CodeExecutionInput(code_blocks=[
        CodeBlock(language="python", code="print('safe')"),
        CodeBlock(language="bash", code="danger --do-not-run"),
    ])

    result = await wrapper.execute_code(object(), execution_input)

    assert len(scanner.requests) == 2
    assert len(events) == 1
    assert events[0].decision is SafetyDecision.DENY
    assert inner._calls == []
    assert result.outcome == Outcome.OUTCOME_FAILED
    payload = json.loads(result.output.removeprefix("Code execution error:\n").rstrip())
    assert payload["blocked"] is True
    assert payload["reports"][0]["block_index"] == 1
    assert "danger --do-not-run" not in result.output


async def test_code_executor_delegates_once_when_every_block_is_allowed():
    wrapper, inner, scanner, _ = _wrapper()
    context = object()
    execution_input = CodeExecutionInput(code_blocks=[
        CodeBlock(language="python", code="print('one')"),
        CodeBlock(language="sh", code="echo two"),
    ])

    result = await wrapper.execute_code(context, execution_input)

    assert result.output == "delegated"
    assert len(scanner.requests) == 2
    assert inner._calls == [(context, execution_input)]


def test_code_executor_mirrors_all_base_behavior_fields_and_delimiter_override():
    inner = RecordingExecutor(
        optimize_data_file=True,
        stateful=True,
        error_retry_attempts=7,
        execute_once_per_invocation=True,
        code_block_delimiters=[CodeBlockDelimiter(start="a", end="b")],
        execution_result_delimiters=[CodeBlockDelimiter(start="c", end="d")],
        ignore_codes=["skip"],
    )
    wrapper, _, _, _ = _wrapper(inner)

    for field_name in BaseCodeExecutor.model_fields:
        assert getattr(wrapper, field_name) == getattr(inner, field_name)
    assert wrapper.code_block_delimiter() == inner.code_block_delimiter()


async def test_code_executor_records_aggregate_denial_after_later_allow():
    wrapper, inner, _, events = _wrapper()
    execution_input = CodeExecutionInput(code_blocks=[
        CodeBlock(language="bash", code="danger first"),
        CodeBlock(language="python", code="print('safe')"),
    ])

    await wrapper.execute_code(object(), execution_input)

    assert inner._calls == []
    assert len(events) == 1
    assert events[0].decision is SafetyDecision.DENY
    assert events[0].blocked is True


def test_code_executor_serialization_copy_and_lifecycle_proxy(tmp_path):
    from trpc_agent_sdk.tools.safety._audit import JsonlAuditSink

    inner = RecordingExecutor()
    policy = ToolSafetyPolicy()
    scanner = ScriptScanner(policy)
    wrapper = SafetyGuardedCodeExecutor(
        inner=inner,
        guard=ToolSafetyGuard(policy, scanner=scanner, audit_sink=JsonlAuditSink(tmp_path / "audit.jsonl")),
    )
    wrapper.error_retry_attempts = 11
    wrapper.ignore_codes = ["wrapper-only"]

    serialized = wrapper.model_dump_json()
    copied = wrapper.model_copy(deep=True)
    wrapper.close()

    assert "guard" not in serialized
    assert "inner" not in serialized
    assert copied.inner is inner
    assert copied.guard is wrapper.guard
    assert copied.error_retry_attempts == 11
    assert copied.ignore_codes == ["wrapper-only"]
    assert copied.ignore_codes is not wrapper.ignore_codes
    assert inner._closed is True


async def test_code_executor_passes_runtime_context_to_scanner():
    inner = RecordingExecutor(
        work_dir="/safe/workspace",
        timeout=45,
        environment={"API_KEY": "do-not-retain"},
    )
    wrapper, _, scanner, _ = _wrapper(inner)

    await wrapper.execute_code(object(), CodeExecutionInput(code="print('ok')"))

    request = scanner.requests[0]
    assert request.cwd == "/safe/workspace"
    assert request.timeout_seconds == 45
    assert request.environment_keys == ["API_KEY"]
    assert "do-not-retain" not in request.model_dump_json()


async def test_code_executor_scanner_failure_is_audited_and_not_delegated():
    policy = ToolSafetyPolicy()
    inner = RecordingExecutor()
    events = []
    wrapper = SafetyGuardedCodeExecutor(
        inner=inner,
        guard=ToolSafetyGuard(policy, scanner=BrokenScanner(policy), audit_sink=events.append),
    )

    result = await wrapper.execute_code(object(), CodeExecutionInput(code="print('ok')"))

    assert inner._calls == []
    assert len(events) == 1
    assert events[0].rule_id == "SCAN-ERROR"
    assert "secret material" not in result.output


async def test_code_executor_invalid_request_context_is_audited_and_blocked():
    policy = ToolSafetyPolicy()
    inner = RecordingExecutor(timeout=-1)
    events = []
    wrapper = SafetyGuardedCodeExecutor(
        inner=inner,
        guard=ToolSafetyGuard(policy, scanner=ScriptScanner(policy), audit_sink=events.append),
    )

    result = await wrapper.execute_code(object(), CodeExecutionInput(code="print('ok')"))

    assert inner._calls == []
    assert len(events) == 1
    assert events[0].rule_id == "SCAN-ERROR"
    assert "ValidationError" not in result.output


async def test_code_executor_context_enricher_failure_is_audited_and_blocked():
    policy = ToolSafetyPolicy()
    inner = RecordingExecutor()
    events = []

    def broken_enricher(*args):
        del args
        raise RuntimeError("context contains secret material")

    wrapper = SafetyGuardedCodeExecutor(
        inner=inner,
        guard=ToolSafetyGuard(policy, scanner=ScriptScanner(policy), audit_sink=events.append),
        context_enricher=broken_enricher,
    )

    result = await wrapper.execute_code(object(), CodeExecutionInput(code="print('ok')"))

    assert inner._calls == []
    assert len(events) == 1
    assert events[0].rule_id == "SCAN-ERROR"
    assert "secret material" not in result.output
