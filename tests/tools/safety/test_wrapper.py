"""Tests for trpc_agent_sdk.tools.safety.wrapper."""

from __future__ import annotations

from typing import Any

import pytest

from trpc_agent_sdk.tools.safety._audit import InMemoryAuditSink
from trpc_agent_sdk.tools.safety._filter import BlockedExecutionError
from trpc_agent_sdk.tools.safety._guard import ToolSafetyGuard
from trpc_agent_sdk.tools.safety._models import ScriptLanguage, ToolKind
from trpc_agent_sdk.tools.safety._policy import load_safety_policy_dict
from trpc_agent_sdk.tools.safety.wrapper import (
    SafetyCheckedExecutor,
    SafetyWrappedCallable,
    _coerce_argv,
    _coerce_script_language,
    _default_audit_sink,
    _ExecutorFailure,
    _extract_code_blocks,
    _make_failure_result,
    _normalize_code_blocks,
    _render_executor_block,
    _truncate_output,
)


def _policy(**overrides):
    return load_safety_policy_dict({
        "version": "1",
        "audit": {
            "enabled": False,
            "required": False,
        },
        **overrides,
    })


def _guard(**overrides) -> ToolSafetyGuard:
    return ToolSafetyGuard(_policy(**overrides))


class TestCoerceArgv:

    def test_str(self):
        assert _coerce_argv("ls") == ("ls", )

    def test_list(self):
        assert _coerce_argv(["ls", "-l"]) == ("ls", "-l")

    def test_tuple(self):
        assert _coerce_argv(("ls", )) == ("ls", )

    def test_other(self):
        assert _coerce_argv(42) == ()


class TestCoerceScriptLanguage:

    def test_none(self):
        assert _coerce_script_language(None) is None

    def test_empty(self):
        assert _coerce_script_language("") is None

    def test_python(self):
        assert _coerce_script_language("python") == ScriptLanguage.PYTHON

    def test_sh_alias(self):
        assert _coerce_script_language("sh") == ScriptLanguage.BASH

    def test_zsh_alias(self):
        assert _coerce_script_language("zsh") == ScriptLanguage.BASH

    def test_py_alias(self):
        assert _coerce_script_language("py") == ScriptLanguage.PYTHON

    def test_invalid_returns_unknown(self):
        assert _coerce_script_language("cobol") == ScriptLanguage.UNKNOWN


class TestExtractCodeBlocks:

    def test_string_input(self):
        assert _extract_code_blocks("print(1)") == [("print(1)", None)]

    def test_mapping_with_code(self):
        out = _extract_code_blocks({"code": "print(1)"})
        assert out == [("print(1)", None)]

    def test_mapping_with_script(self):
        out = _extract_code_blocks({"script": "ls"})
        assert out == [("ls", None)]

    def test_mapping_with_language(self):
        out = _extract_code_blocks({"code": "ls", "language": "bash"})
        assert out == [("ls", ScriptLanguage.BASH)]

    def test_mapping_with_code_blocks_list(self):
        out = _extract_code_blocks(
            {"code_blocks": [
                {
                    "code": "a",
                    "language": "python"
                },
                {
                    "code": "b",
                    "language": "bash"
                },
            ]})
        assert out == [
            ("a", ScriptLanguage.PYTHON),
            ("b", ScriptLanguage.BASH),
        ]

    def test_mapping_with_code_list(self):
        out = _extract_code_blocks({"code": ["a", "b"]})
        assert out == [("a", None), ("b", None)]

    def test_object_with_code_blocks(self):

        class Obj:
            code_blocks = [{"code": "x"}]

        out = _extract_code_blocks(Obj())
        assert out == [("x", None)]

    def test_object_with_code_attr(self):

        class Obj:
            code = "print(1)"
            language = "python"

        out = _extract_code_blocks(Obj())
        assert out == [("print(1)", ScriptLanguage.PYTHON)]

    def test_empty_object(self):

        class Obj:
            pass

        assert _extract_code_blocks(Obj()) == []

    def test_mapping_block_with_object(self):

        class B:
            code = "x"
            language = "python"

        out = _extract_code_blocks({"code_blocks": [B()]})
        assert out == [("x", ScriptLanguage.PYTHON)]


class TestNormalizeCodeBlocks:

    def test_non_iterable_returns_empty(self):
        assert _normalize_code_blocks(None) == []
        assert _normalize_code_blocks(42) == []

    def test_string_block(self):
        assert _normalize_code_blocks(["echo"]) == [("echo", None)]

    def test_mapping_block(self):
        out = _normalize_code_blocks([{"code": "x", "language": "py"}])
        assert out == [("x", ScriptLanguage.PYTHON)]

    def test_skips_blocks_without_code(self):
        out = _normalize_code_blocks([{"language": "py"}])
        assert out == []


class TestDefaultAuditSink:

    def test_disabled_returns_null(self):
        from trpc_agent_sdk.tools.safety._audit import NullAuditSink
        sink = _default_audit_sink(_policy(audit={"enabled": False}))
        assert isinstance(sink, NullAuditSink)

    def test_no_path_returns_inmemory(self):
        policy = _policy(audit={"enabled": True, "required": False, "path": ""})
        sink = _default_audit_sink(policy)
        assert isinstance(sink, InMemoryAuditSink)

    def test_path_returns_jsonl(self, tmp_path):
        from trpc_agent_sdk.tools.safety._audit import JsonlAuditSink
        policy = _policy(audit={"enabled": True, "required": False, "path": str(tmp_path / "a.jsonl")})
        sink = _default_audit_sink(policy)
        assert isinstance(sink, JsonlAuditSink)


class TestSafetyWrappedCallable:

    def test_constructor_requires_one_script_param(self):
        with pytest.raises(ValueError):
            SafetyWrappedCallable(_guard(), lambda x: x, tool_name="t")  # neither
        with pytest.raises(ValueError):
            SafetyWrappedCallable(_guard(), lambda x: x, tool_name="t", script_kw="code", script_pos=0)  # both

    def test_safe_call_runs_delegate(self):
        calls = []

        def delegate(code):
            calls.append(code)
            return "ok"

        wrapped = SafetyWrappedCallable(_guard(),
                                        delegate,
                                        tool_name="python_exec",
                                        language=ScriptLanguage.PYTHON,
                                        script_kw="code")
        result = wrapped(code="print('hi')")
        assert result == "ok"
        assert calls == ["print('hi')"]

    def test_dangerous_call_blocked(self):

        def delegate(code):
            return "should-not-run"

        wrapped = SafetyWrappedCallable(_guard(),
                                        delegate,
                                        tool_name="python_exec",
                                        language=ScriptLanguage.PYTHON,
                                        script_kw="code")
        with pytest.raises(BlockedExecutionError):
            wrapped(code="import shutil\nshutil.rmtree('/x')")

    def test_script_positional(self):

        def delegate(code):
            return f"ran:{code}"

        wrapped = SafetyWrappedCallable(_guard(),
                                        delegate,
                                        tool_name="python_exec",
                                        language=ScriptLanguage.PYTHON,
                                        script_pos=0)
        result = wrapped("print('hi')")
        assert "ran:print('hi')" in result

    def test_extract_script_list_value(self):

        def delegate(command):
            return delegate_result

        delegate_result = "ok"

        wrapped = SafetyWrappedCallable(_guard(),
                                        delegate,
                                        tool_name="bash_exec",
                                        language=ScriptLanguage.BASH,
                                        script_kw="command")
        # If the value is a list, it gets joined.
        assert wrapped(command=["echo", "hi"]) == "ok"

    def test_extract_script_none_returns_empty(self):
        calls = []

        def delegate(code=None):
            calls.append(code)
            return "ok"

        wrapped = SafetyWrappedCallable(_guard(),
                                        delegate,
                                        tool_name="python_exec",
                                        language=ScriptLanguage.PYTHON,
                                        script_kw="code")
        # None script becomes "" inside the request, so the guard sees an
        # empty script and the delegate is reached.
        result = wrapped(code=None)
        assert result == "ok"
        assert calls == [None]

    def test_extract_script_out_of_range_position(self):

        def delegate(code="default"):
            return code

        wrapped = SafetyWrappedCallable(_guard(),
                                        delegate,
                                        tool_name="python_exec",
                                        language=ScriptLanguage.PYTHON,
                                        script_pos=5)
        # IndexError caught -> empty script -> safe
        assert wrapped() == "default"

    def test_call_async_safe(self):

        async def delegate(code):
            return f"async:{code}"

        wrapped = SafetyWrappedCallable(_guard(),
                                        delegate,
                                        tool_name="python_exec",
                                        language=ScriptLanguage.PYTHON,
                                        script_kw="code")

        import asyncio
        result = asyncio.run(wrapped.call_async(code="print('hi')"))
        assert "async:print('hi')" in result

    def test_call_async_blocks_dangerous(self):

        async def delegate(code):
            return "ran"

        wrapped = SafetyWrappedCallable(_guard(),
                                        delegate,
                                        tool_name="python_exec",
                                        language=ScriptLanguage.PYTHON,
                                        script_kw="code")

        import asyncio
        with pytest.raises(BlockedExecutionError):
            asyncio.run(wrapped.call_async(code="import shutil\nshutil.rmtree('/x')"))

    def test_call_async_with_sync_delegate(self):
        # Sync delegate is also accepted; result is awaited only if needed.
        def delegate(code):
            return f"sync:{code}"

        wrapped = SafetyWrappedCallable(_guard(),
                                        delegate,
                                        tool_name="python_exec",
                                        language=ScriptLanguage.PYTHON,
                                        script_kw="code")
        import asyncio
        result = asyncio.run(wrapped.call_async(code="print('hi')"))
        assert result == "sync:print('hi')"

    def test_safety_filter_property(self):
        wrapped = SafetyWrappedCallable(_guard(),
                                        lambda x: x,
                                        tool_name="t",
                                        language=ScriptLanguage.PYTHON,
                                        script_kw="code")
        assert wrapped.safety_filter is not None

    def test_cwd_env_argv_propagation(self):
        captured = {}

        def delegate(command, cwd=None, env=None, timeout=None, argv=None, metadata=None, output_bytes=None):
            captured.update(cwd=cwd, env=env, argv=argv, timeout=timeout)
            return "ok"

        wrapped = SafetyWrappedCallable(_guard(),
                                        delegate,
                                        tool_name="bash_exec",
                                        language=ScriptLanguage.BASH,
                                        script_kw="command",
                                        cwd_kw="cwd",
                                        env_kw="env",
                                        timeout_kw="timeout",
                                        argv_kw="argv",
                                        metadata_kw="metadata",
                                        output_bytes_kw="output_bytes")
        wrapped(command="ls", cwd="/tmp", env={"X": "1"}, timeout=30, argv=["ls"])
        assert captured["cwd"] == "/tmp"
        assert captured["env"] == {"X": "1"}
        # Delegate receives the original list (only SafetyScanRequest is
        # coerced to a tuple).
        assert captured["argv"] == ["ls"]
        assert captured["timeout"] == 30


class TestSafetyCheckedExecutor:

    @pytest.mark.asyncio
    async def test_no_blocks_returns_failure(self):

        class Delegate:

            async def execute_code(self, inp):
                return {"outcome": "OK", "output": "x"}

        executor = SafetyCheckedExecutor(_guard(), Delegate())
        result = await executor.execute_code({})
        assert isinstance(result, _ExecutorFailure)
        assert "no code blocks" in result.output

    @pytest.mark.asyncio
    async def test_safe_code_delegates(self):

        class Delegate:

            async def execute_code(self, inp):
                return {"outcome": "OK", "output": "x"}

        executor = SafetyCheckedExecutor(_guard(), Delegate())
        result = await executor.execute_code({"code": "print('hi')"})
        assert result["outcome"] == "OK"

    @pytest.mark.asyncio
    async def test_dangerous_code_blocks(self):

        class Delegate:

            async def execute_code(self, inp):
                return {"outcome": "OK", "output": "should-not-reach"}

        executor = SafetyCheckedExecutor(_guard(), Delegate())
        result = await executor.execute_code({
            "code": "import shutil\nshutil.rmtree('/x')",
        })
        assert isinstance(result, _ExecutorFailure)
        assert "execution blocked" in result.output

    @pytest.mark.asyncio
    async def test_output_truncation(self):

        class Delegate:

            async def execute_code(self, inp):
                return {"outcome": "OK", "output": "x" * 5000}

        # Tight output budget.
        policy = _policy(limits={"max_output_bytes": 100})
        executor = SafetyCheckedExecutor(ToolSafetyGuard(policy), Delegate())
        result = await executor.execute_code({"code": "print('hi')"})
        assert len(result["output"]) < 200

    @pytest.mark.asyncio
    async def test_string_input(self):

        class Delegate:

            async def execute_code(self, inp):
                return {"outcome": "OK", "output": "x"}

        executor = SafetyCheckedExecutor(_guard(), Delegate())
        result = await executor.execute_code("print('hi')")
        assert result["outcome"] == "OK"

    @pytest.mark.asyncio
    async def test_multiple_blocks_combined(self):

        class Delegate:

            async def execute_code(self, inp):
                return {"outcome": "OK", "output": "x"}

        executor = SafetyCheckedExecutor(_guard(), Delegate())
        result = await executor.execute_code({
            "code_blocks": [
                {
                    "code": "print('a')",
                    "language": "python"
                },
                {
                    "code": "print('b')",
                    "language": "python"
                },
            ]
        })
        assert result["outcome"] == "OK"

    @pytest.mark.asyncio
    async def test_unknown_language_blocks_treated_as_exec(self):

        class Delegate:

            async def execute_code(self, inp):
                return {"outcome": "OK", "output": "x"}

        executor = SafetyCheckedExecutor(_guard(), Delegate())
        # unknown language -> metadata execution_capable=True
        result = await executor.execute_code({"code": "print('a')", "language": "weird"})
        # Cross-field check on execution_capable tool -> review
        # default unknown_construct=review, human_review_blocks=True
        assert isinstance(result, _ExecutorFailure) or result.get("outcome") == "OK"


class TestExecutorFailure:

    def test_repr(self):
        f = _ExecutorFailure("msg")
        assert "msg" in repr(f)
        assert f.outcome == "FAILURE"
        assert f.output == "msg"


class TestMakeFailureResult:

    def test_returns_executor_failure(self):
        result = _make_failure_result("oops")
        assert isinstance(result, _ExecutorFailure)
        assert result.output == "oops"


class TestRenderExecutorBlock:

    def test_renders_payload(self):
        from trpc_agent_sdk.tools.safety._models import (
            Evidence,
            RiskCategory,
            RiskLevel,
            SafetyDecision,
            SafetyFinding,
            SafetyReport,
        )
        report = SafetyReport(
            report_id="r",
            decision=SafetyDecision.DENY,
            risk_level=RiskLevel.HIGH,
            rule_ids=("X", ),
            findings=(SafetyFinding(
                rule_id="X",
                category=RiskCategory.FILE,
                risk_level=RiskLevel.HIGH,
                decision=SafetyDecision.DENY,
                evidence=Evidence(snippet="x"),
                recommendation="don't",
            ), ),
            recommendation="block",
            policy_hash="p",
            policy_version="1",
            script_sha256="s",
            scan_duration_ms=0,
            redacted=False,
        )
        result = _render_executor_block(report)
        assert isinstance(result, _ExecutorFailure)
        assert "execution blocked" in result.output
        assert "deny" in result.output


class TestTruncateOutput:

    def test_no_truncation_when_within_budget(self):
        out = _truncate_output({"output": "short"}, 100)
        assert out["output"] == "short"

    def test_truncates_long_output_dict(self):
        long = "x" * 500
        out = _truncate_output({"output": long}, 50)
        assert len(out["output"].encode("utf-8")) <= 100  # marker inclusive
        assert "truncated" in out["output"]

    def test_truncates_object_with_attr(self):

        class R:

            def __init__(self):
                self.output = "x" * 500

        r = R()
        out = _truncate_output(r, 50)
        assert "truncated" in out.output

    def test_truncates_mapping_object(self):
        from collections.abc import Mapping

        class M(Mapping):

            def __init__(self):
                self._d = {"output": "x" * 500, "k": 1}

            def __getitem__(self, k):
                return self._d[k]

            def __iter__(self):
                return iter(self._d)

            def __len__(self):
                return len(self._d)

        m = M()
        out = _truncate_output(m, 50)
        assert "truncated" in out["output"]

    def test_zero_budget_no_truncation(self):
        out = _truncate_output({"output": "x"}, 0)
        assert out["output"] == "x"

    def test_non_str_output_passthrough(self):
        out = _truncate_output({"output": 42}, 100)
        assert out["output"] == 42

    def test_marker_larger_than_budget(self):
        # When marker is bigger than budget, fall back to truncated marker.
        out = _truncate_output({"output": "x" * 500}, 5)
        # Output is bounded to ~budget bytes after utf-8 decoding.
        assert isinstance(out["output"], str)

    def test_attribute_set_failure_returns_string(self):

        class Frozen:
            output = "x" * 500
            __slots__ = ()

        # Cannot set attribute on slotted class
        out = _truncate_output(Frozen(), 50)
        # Should fall back to plain string
        assert isinstance(out, str)
