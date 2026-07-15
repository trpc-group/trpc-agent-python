"""Tests for the SafetyWrappedCallable and SafetyCheckedExecutor."""

from __future__ import annotations

import pytest

from trpc_agent_sdk.tools.safety._audit import InMemoryAuditSink
from trpc_agent_sdk.tools.safety._filter import BlockedExecutionError, ToolScriptSafetyFilter
from trpc_agent_sdk.tools.safety._guard import ToolSafetyGuard
from trpc_agent_sdk.tools.safety._models import ScriptLanguage, ToolKind
from trpc_agent_sdk.tools.safety._policy import load_safety_policy_dict
from trpc_agent_sdk.tools.safety import (
    SafetyCheckedExecutor,
    SafetyWrappedCallable,
)


@pytest.fixture
def guard(strict_policy_dict):
    return ToolSafetyGuard(load_safety_policy_dict(strict_policy_dict))


@pytest.fixture
def filter_(guard):
    return ToolScriptSafetyFilter(guard, audit_sink=InMemoryAuditSink())


def test_wrapped_callable_allows_safe(guard):
    calls = []

    def delegate(script: str) -> str:
        calls.append(script)
        return "ok"

    wrapped = SafetyWrappedCallable(
        guard, delegate,
        tool_name="python_exec",
        language=ScriptLanguage.PYTHON,
        script_kw="script",
    )
    assert wrapped(script="print('hi')") == "ok"
    assert calls == ["print('hi')"]


def test_wrapped_callable_audits_before_delegate(guard):
    sink = InMemoryAuditSink()
    filter_ = ToolScriptSafetyFilter(guard, audit_sink=sink)

    def delegate(script: str) -> str:
        assert len(sink.events) == 1
        return "ok"

    wrapped = SafetyWrappedCallable(
        guard,
        delegate,
        tool_name="python_exec",
        language=ScriptLanguage.PYTHON,
        script_kw="script",
        filter=filter_,
    )

    assert wrapped(script="print('hi')") == "ok"


def test_wrapped_callable_blocks_danger(guard):
    def delegate(script: str) -> str:
        return "ran"

    wrapped = SafetyWrappedCallable(
        guard, delegate,
        tool_name="python_exec",
        language=ScriptLanguage.PYTHON,
        script_kw="script",
    )
    with pytest.raises(BlockedExecutionError):
        wrapped(script="import shutil\nshutil.rmtree('/x')")


def test_wrapped_callable_scans_explicit_argv_and_metadata(guard):
    wrapped = SafetyWrappedCallable(
        guard,
        lambda **kwargs: "ran",
        tool_name="python_exec",
        language=ScriptLanguage.PYTHON,
        script_kw="script",
        argv_kw="argv",
        metadata_kw="metadata",
    )
    with pytest.raises(BlockedExecutionError):
        wrapped(script="print('safe')", argv=["sudo", "ls"])
    with pytest.raises(BlockedExecutionError):
        wrapped(
            script="print('safe')",
            metadata={"execution_capable": True, "adapter_id": "unknown"},
        )


def test_wrapped_callable_supports_positional(guard):
    def delegate(script: str) -> str:
        return f"got:{script}"

    wrapped = SafetyWrappedCallable(
        guard, delegate,
        tool_name="python_exec",
        language=ScriptLanguage.PYTHON,
        script_pos=0,
    )
    assert wrapped("print(1)") == "got:print(1)"


def test_executor_allow_delegates(guard):
    class FakeInput:
        code_blocks = [type("Block", (), {"code": "print('a')"})()]

    class FakeResult:
        outcome = "SUCCESS"
        output = "x" * 100

    class FakeExecutor:
        async def execute_code(self, inp):
            assert inp is fake_input
            return FakeResult()

    delegate = FakeExecutor()
    wrapped = SafetyCheckedExecutor(guard, delegate,
                                    audit_sink=InMemoryAuditSink())
    fake_input = FakeInput()
    result = asyncio.run(wrapped.execute_code(fake_input))  # noqa: F821
    assert result.outcome == "SUCCESS"


def test_executor_deny_does_not_delegate(guard):
    class FakeInput:
        code_blocks = [type("Block", (),
                            {"code": "import shutil\nshutil.rmtree('/x')"})()]

    called = []

    class FakeExecutor:
        async def execute_code(self, inp):
            called.append(True)
            return None

    wrapped = SafetyCheckedExecutor(guard, FakeExecutor(),
                                    audit_sink=InMemoryAuditSink())
    result = asyncio.run(wrapped.execute_code(FakeInput()))  # noqa: F821
    assert called == []
    assert "blocked" in result.output or "trpc_agent_sdk.tools.safety" in result.output


def test_executor_scans_code_attribute_when_blocks_are_empty(guard):
    class FakeInput:
        code_blocks = []
        code = "import shutil\nshutil.rmtree('/x')"

    class FakeExecutor:
        async def execute_code(self, inp):
            raise AssertionError("unsafe code must not reach the executor")

    sink = InMemoryAuditSink()
    wrapped = SafetyCheckedExecutor(guard, FakeExecutor(), audit_sink=sink)
    result = asyncio.run(wrapped.execute_code(FakeInput()))  # noqa: F821
    assert "FILE001_RECURSIVE_DELETE" in result.output
    assert len(sink.events) == 1


def test_executor_uses_each_block_language(guard):
    class FakeInput:
        code_blocks = [type("Block", (), {
            "language": "bash", "code": "rm -rf /tmp/x",
        })()]

    class FakeExecutor:
        async def execute_code(self, inp):
            raise AssertionError("unsafe Bash must not reach the executor")

    wrapped = SafetyCheckedExecutor(guard, FakeExecutor(),
                                    audit_sink=InMemoryAuditSink())
    result = asyncio.run(wrapped.execute_code(FakeInput()))  # noqa: F821
    assert "FILE001_RECURSIVE_DELETE" in result.output


def test_executor_never_delegates_deny_when_review_is_non_blocking(
    strict_policy_dict,
):
    policy_dict = strict_policy_dict.copy()
    policy_dict["defaults"] = {"human_review_blocks_execution": False}
    guard = ToolSafetyGuard(load_safety_policy_dict(policy_dict))
    called = []

    class FakeInput:
        code_blocks = [type("Block", (), {
            "language": "bash", "code": "rm -rf /tmp/x",
        })()]

    class FakeExecutor:
        async def execute_code(self, inp):
            called.append(True)
            return {"outcome": "SUCCESS", "output": "ran"}

    wrapped = SafetyCheckedExecutor(guard, FakeExecutor(),
                                    audit_sink=InMemoryAuditSink())
    result = asyncio.run(wrapped.execute_code(FakeInput()))  # noqa: F821
    assert called == []
    assert "FILE001_RECURSIVE_DELETE" in result.output


def test_executor_truncates_output(guard):
    class FakeInput:
        code_blocks = [type("Block", (), {"code": "print('hi')"})()]

    class FakeResult:
        outcome = "SUCCESS"
        output = "x" * 4096

    class FakeExecutor:
        async def execute_code(self, inp):
            return FakeResult()

    wrapped = SafetyCheckedExecutor(guard, FakeExecutor(),
                                    audit_sink=InMemoryAuditSink())
    result = asyncio.run(wrapped.execute_code(FakeInput()))  # noqa: F821
    assert len(result.output.encode("utf-8")) <= guard.policy.limits.max_output_bytes


def test_executor_truncates_dict_output(strict_policy_dict):
    policy_dict = strict_policy_dict.copy()
    policy_dict["limits"] = dict(policy_dict["limits"])
    policy_dict["limits"]["max_output_bytes"] = 8
    guard = ToolSafetyGuard(load_safety_policy_dict(policy_dict))

    class FakeInput:
        code_blocks = [type("Block", (), {"code": "print('hi')"})()]

    class FakeExecutor:
        async def execute_code(self, inp):
            return {"outcome": "SUCCESS", "output": "x" * 32}

    wrapped = SafetyCheckedExecutor(guard, FakeExecutor(),
                                    audit_sink=InMemoryAuditSink())
    result = asyncio.run(wrapped.execute_code(FakeInput()))  # noqa: F821
    assert len(result["output"].encode("utf-8")) <= 8


# Need asyncio.run helper
import asyncio  # noqa: E402
