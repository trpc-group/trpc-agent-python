# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for trpc_agent_sdk.code_executors.cube._code_executor."""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import AsyncMock, MagicMock

import pytest

from trpc_agent_sdk.code_executors._types import (
    CodeBlock,
    CodeBlockDelimiter,
    CodeExecutionInput,
)
from trpc_agent_sdk.code_executors.cube import _code_executor as ce_mod
from trpc_agent_sdk.code_executors.cube._code_executor import CubeCodeExecutor
from trpc_agent_sdk.code_executors.cube._sandbox import (
    CubeCommandResult,
    CubeSandboxClient,
)
from trpc_agent_sdk.code_executors.cube._types import CubeCodeExecutorConfig
from trpc_agent_sdk.context import InvocationContext


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _cfg(**overrides) -> CubeCodeExecutorConfig:
    base = dict(
        template="t",
        api_url="u",
        api_key="k",
        execute_timeout=30.0,
        idle_timeout=600,
    )
    base.update(overrides)
    return CubeCodeExecutorConfig(**base)


def _ok(stdout: str = "", stderr: str = "", exit_code: int = 0) -> CubeCommandResult:
    return CubeCommandResult(stdout=stdout, stderr=stderr, exit_code=exit_code, duration=0.0)


@pytest.fixture
def mock_client():
    c = MagicMock(spec=CubeSandboxClient)
    c.sandbox_id = "sbx-1"
    c.commands_run = AsyncMock(return_value=_ok(stdout="ok"))
    c.destroy = AsyncMock()
    c.close = MagicMock()
    c.assert_running = AsyncMock()
    return c


@pytest.fixture
def mock_ctx():
    return MagicMock(spec=InvocationContext)


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


class TestConstruction:

    def test_defaults(self, mock_client):
        cfg = _cfg()
        ex = CubeCodeExecutor(mock_client, cfg)
        assert ex.stateful is False
        assert ex.optimize_data_file is False
        assert ex.config is cfg
        assert ex.sandbox_client is mock_client

    def test_delimiters_include_bash(self, mock_client):
        ex = CubeCodeExecutor(mock_client, _cfg())
        # Default delimiters: tool_code, python, bash fences.
        delims = [(d.start, d.end) for d in ex.code_block_delimiters]
        assert ("```tool_code\n", "\n```") in delims
        assert ("```python\n", "\n```") in delims
        assert ("```bash\n", "\n```") in delims

    def test_stateful_rejected(self, mock_client):
        with pytest.raises(ValueError, match="cannot be stateful"):
            CubeCodeExecutor(mock_client, _cfg(), stateful=True)

    def test_optimize_data_file_rejected(self, mock_client):
        with pytest.raises(ValueError, match="optimize_data_file"):
            CubeCodeExecutor(mock_client, _cfg(), optimize_data_file=True)

    def test_custom_delimiters_preserved(self, mock_client):
        custom = [CodeBlockDelimiter(start="```py\n", end="\n```")]
        ex = CubeCodeExecutor(mock_client, _cfg(), code_block_delimiters=custom)
        # Note: Pydantic may copy/validate; just check content equivalence.
        assert len(ex.code_block_delimiters) == 1
        assert ex.code_block_delimiters[0].start == "```py\n"

    def test_sandbox_id_reads_client(self, mock_client):
        mock_client.sandbox_id = "custom-id"
        ex = CubeCodeExecutor(mock_client, _cfg())
        assert ex.sandbox_id == "custom-id"


# ---------------------------------------------------------------------------
# create / attach / create_or_recreate
# ---------------------------------------------------------------------------


class TestCreate:

    @pytest.mark.asyncio
    async def test_no_sandbox_id_opens_new(self, fake_e2b, monkeypatch, mock_client):
        open_new = AsyncMock(return_value=mock_client)
        open_existing = AsyncMock()
        monkeypatch.setattr(
            CubeSandboxClient, "open_new", classmethod(lambda cls, cfg: open_new(cfg)),
        )
        monkeypatch.setattr(
            CubeSandboxClient,
            "open_existing",
            classmethod(lambda cls, cfg: open_existing(cfg)),
        )
        cfg = _cfg(sandbox_id=None)
        ex = await CubeCodeExecutor.create(cfg)
        assert ex.sandbox_client is mock_client
        open_new.assert_awaited_once_with(cfg)
        open_existing.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_with_sandbox_id_opens_existing(self, fake_e2b, monkeypatch, mock_client):
        open_new = AsyncMock()
        open_existing = AsyncMock(return_value=mock_client)
        monkeypatch.setattr(
            CubeSandboxClient, "open_new", classmethod(lambda cls, cfg: open_new(cfg))
        )
        monkeypatch.setattr(
            CubeSandboxClient,
            "open_existing",
            classmethod(lambda cls, cfg: open_existing(cfg)),
        )
        cfg = _cfg(sandbox_id="sbx-42")
        await CubeCodeExecutor.create(cfg)
        open_new.assert_not_awaited()
        open_existing.assert_awaited_once_with(cfg)


class TestAttach:

    @pytest.mark.asyncio
    async def test_missing_sandbox_id_raises(self, fake_e2b):
        with pytest.raises(ValueError, match="sandbox_id"):
            await CubeCodeExecutor.attach(_cfg(sandbox_id=None))

    @pytest.mark.asyncio
    async def test_with_sandbox_id_calls_open_existing(self, fake_e2b, monkeypatch, mock_client):
        called = AsyncMock(return_value=mock_client)
        monkeypatch.setattr(
            CubeSandboxClient,
            "open_existing",
            classmethod(lambda cls, cfg: called(cfg)),
        )
        cfg = _cfg(sandbox_id="sbx-1")
        ex = await CubeCodeExecutor.attach(cfg)
        called.assert_awaited_once_with(cfg)
        assert ex.sandbox_client is mock_client

    @pytest.mark.asyncio
    async def test_never_calls_open_new(self, fake_e2b, monkeypatch):
        on_new = AsyncMock()
        on_existing = AsyncMock(side_effect=RuntimeError("expected — test stopper"))
        monkeypatch.setattr(
            CubeSandboxClient,
            "open_new",
            classmethod(lambda cls, cfg: on_new(cfg)),
        )
        monkeypatch.setattr(
            CubeSandboxClient,
            "open_existing",
            classmethod(lambda cls, cfg: on_existing(cfg)),
        )
        with pytest.raises(RuntimeError, match="test stopper"):
            await CubeCodeExecutor.attach(_cfg(sandbox_id="sbx-1"))
        on_new.assert_not_awaited()


class TestCreateOrRecreate:

    @pytest.mark.asyncio
    async def test_no_sandbox_id_delegates_to_create(self, fake_e2b, monkeypatch, mock_client):
        create_mock = AsyncMock(return_value=MagicMock())
        monkeypatch.setattr(
            CubeCodeExecutor,
            "create",
            classmethod(lambda cls, cfg: create_mock(cfg)),
        )
        on_stale = AsyncMock()
        await CubeCodeExecutor.create_or_recreate(_cfg(sandbox_id=None), on_stale=on_stale)
        create_mock.assert_awaited_once()
        on_stale.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_attach_success_no_stale_callback(self, fake_e2b, monkeypatch, mock_client):
        ex_obj = MagicMock()
        create_mock = AsyncMock(return_value=ex_obj)
        monkeypatch.setattr(
            CubeCodeExecutor,
            "create",
            classmethod(lambda cls, cfg: create_mock(cfg)),
        )
        on_stale = AsyncMock()
        result = await CubeCodeExecutor.create_or_recreate(
            _cfg(sandbox_id="sbx-1"), on_stale=on_stale
        )
        assert result is ex_obj
        on_stale.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_not_found_triggers_on_stale_then_recreate(
        self, fake_e2b, monkeypatch
    ):
        calls: list = []

        async def create_side_effect(cfg):
            calls.append(cfg)
            if cfg.sandbox_id == "sbx-1":
                raise fake_e2b.SandboxNotFoundException("gone")
            return "fresh-executor"

        create_mock = AsyncMock(side_effect=create_side_effect)
        monkeypatch.setattr(
            CubeCodeExecutor,
            "create",
            classmethod(lambda cls, cfg: create_mock(cfg)),
        )
        on_stale = AsyncMock()
        result = await CubeCodeExecutor.create_or_recreate(
            _cfg(sandbox_id="sbx-1"), on_stale=on_stale
        )
        assert result == "fresh-executor"
        on_stale.assert_awaited_once()
        # Second call must have sandbox_id=None (recreate).
        assert calls[1].sandbox_id is None
        # Other cfg fields preserved.
        assert calls[1].template == "t"

    @pytest.mark.asyncio
    async def test_not_found_without_on_stale_still_recreates(
        self, fake_e2b, monkeypatch
    ):
        async def create_side_effect(cfg):
            if cfg.sandbox_id == "sbx-1":
                raise fake_e2b.SandboxNotFoundException("gone")
            return "fresh"

        create_mock = AsyncMock(side_effect=create_side_effect)
        monkeypatch.setattr(
            CubeCodeExecutor,
            "create",
            classmethod(lambda cls, cfg: create_mock(cfg)),
        )
        result = await CubeCodeExecutor.create_or_recreate(
            _cfg(sandbox_id="sbx-1"), on_stale=None
        )
        assert result == "fresh"

    @pytest.mark.asyncio
    async def test_paused_propagates(self, fake_e2b, monkeypatch):
        """BUG PROBE: PAUSED state must NOT trigger recreate.

        If ``create_or_recreate`` caught SandboxException (the parent of
        SandboxNotFoundException), operator-managed pauses would be
        silently destroyed. Only the NotFound subclass should recreate.
        """
        create_mock = AsyncMock(
            side_effect=fake_e2b.SandboxException("paused")
        )
        monkeypatch.setattr(
            CubeCodeExecutor,
            "create",
            classmethod(lambda cls, cfg: create_mock(cfg)),
        )
        on_stale = AsyncMock()
        with pytest.raises(fake_e2b.SandboxException, match="paused"):
            await CubeCodeExecutor.create_or_recreate(
                _cfg(sandbox_id="sbx-1"), on_stale=on_stale
            )
        on_stale.assert_not_awaited()


# ---------------------------------------------------------------------------
# Properties & lifecycle
# ---------------------------------------------------------------------------


class TestLifecycle:

    def test_close_calls_client_close(self, mock_client):
        ex = CubeCodeExecutor(mock_client, _cfg())
        ex.close()
        mock_client.close.assert_called_once()

    def test_close_idempotent(self, mock_client):
        ex = CubeCodeExecutor(mock_client, _cfg())
        ex.close()
        ex.close()
        # Second close must not re-call the client.
        mock_client.close.assert_called_once()

    def test_sandbox_client_after_close_raises(self, mock_client):
        ex = CubeCodeExecutor(mock_client, _cfg())
        ex.close()
        with pytest.raises(RuntimeError, match="closed"):
            _ = ex.sandbox_client

    def test_sandbox_id_after_close_raises(self, mock_client):
        ex = CubeCodeExecutor(mock_client, _cfg())
        ex.close()
        with pytest.raises(RuntimeError, match="closed"):
            _ = ex.sandbox_id

    @pytest.mark.asyncio
    async def test_destroy_calls_client_destroy(self, mock_client):
        ex = CubeCodeExecutor(mock_client, _cfg())
        await ex.destroy()
        mock_client.destroy.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_destroy_idempotent(self, mock_client):
        ex = CubeCodeExecutor(mock_client, _cfg())
        await ex.destroy()
        await ex.destroy()
        mock_client.destroy.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_destroy_clears_handle_even_on_error(self, mock_client):
        mock_client.destroy.side_effect = RuntimeError("boom")
        ex = CubeCodeExecutor(mock_client, _cfg())
        with pytest.raises(RuntimeError, match="boom"):
            await ex.destroy()
        # Handle cleared via finally block.
        with pytest.raises(RuntimeError, match="closed"):
            _ = ex.sandbox_client

    @pytest.mark.asyncio
    async def test_assert_running_delegates(self, mock_client):
        ex = CubeCodeExecutor(mock_client, _cfg())
        await ex.assert_running()
        mock_client.assert_running.assert_awaited_once()


# ---------------------------------------------------------------------------
# _select_interpreter
# ---------------------------------------------------------------------------


class TestSelectInterpreter:

    @pytest.mark.parametrize("lang", ["python", "py", "python3", "", "PYTHON", "Py"])
    def test_python_languages(self, lang):
        assert CubeCodeExecutor._select_interpreter(lang) == "python3"

    @pytest.mark.parametrize("lang", ["bash", "sh", "BASH", "Sh"])
    def test_bash_languages(self, lang):
        """BUG PROBE: bash MUST run as a login shell.

        The production code chose ``bash -l`` deliberately so that
        ``/etc/profile.d/*`` populates PATH for tools like uv/conda.
        Regressing to plain ``bash`` would silently break Cube
        templates that rely on profile-based PATH injection.
        """
        assert CubeCodeExecutor._select_interpreter(lang) == "bash -l"

    @pytest.mark.parametrize("lang", ["ruby", "javascript", "go", "rust"])
    def test_unsupported_raises(self, lang):
        with pytest.raises(ValueError, match="unsupported"):
            CubeCodeExecutor._select_interpreter(lang)

    def test_none_language_is_python(self):
        """``_select_interpreter`` tolerates None and treats it as python."""
        assert CubeCodeExecutor._select_interpreter(None) == "python3"


# ---------------------------------------------------------------------------
# _collect
# ---------------------------------------------------------------------------


class TestCollect:

    def test_success_appends_only_stdout(self):
        result = _ok(stdout="out", exit_code=0)
        outs, errs = [], []
        CubeCodeExecutor._collect(result, outs, errs)
        assert outs == ["out"]
        assert errs == []

    def test_non_zero_appends_exit_marker(self):
        result = _ok(stdout="out", stderr="err", exit_code=9)
        outs, errs = [], []
        CubeCodeExecutor._collect(result, outs, errs)
        assert outs == ["out"]
        assert errs == ["Process exited with code: 9\n", "err"]

    def test_empty_fields_add_nothing(self):
        result = _ok(stdout="", stderr="", exit_code=0)
        outs, errs = [], []
        CubeCodeExecutor._collect(result, outs, errs)
        assert outs == []
        assert errs == []


# ---------------------------------------------------------------------------
# execute_code
# ---------------------------------------------------------------------------


class TestExecuteCode:

    @pytest.mark.asyncio
    async def test_single_python_block(self, mock_client, mock_ctx):
        mock_client.commands_run.return_value = _ok(stdout="hi\n")
        ex = CubeCodeExecutor(mock_client, _cfg())
        inp = CodeExecutionInput(code_blocks=[CodeBlock(code="print('hi')", language="python")])
        result = await ex.execute_code(mock_ctx, inp)

        mock_client.commands_run.assert_awaited_once()
        args, kwargs = mock_client.commands_run.await_args
        assert args == ("python3",)
        assert kwargs["stdin"] == b"print('hi')"
        assert kwargs["timeout"] == 30.0
        # Aggregated into result output text.
        assert "hi" in result.output

    @pytest.mark.asyncio
    async def test_bash_block_uses_login_shell(self, mock_client, mock_ctx):
        mock_client.commands_run.return_value = _ok(stdout="")
        ex = CubeCodeExecutor(mock_client, _cfg())
        inp = CodeExecutionInput(code_blocks=[CodeBlock(code="echo hi", language="bash")])
        await ex.execute_code(mock_ctx, inp)
        assert mock_client.commands_run.await_args.args[0] == "bash -l"

    @pytest.mark.asyncio
    async def test_mixed_blocks_run_in_order(self, mock_client, mock_ctx):
        mock_client.commands_run.side_effect = [
            _ok(stdout="PY\n"),
            _ok(stdout="BASH\n"),
        ]
        ex = CubeCodeExecutor(mock_client, _cfg())
        inp = CodeExecutionInput(code_blocks=[
            CodeBlock(code="print('py')", language="python"),
            CodeBlock(code="echo bash", language="bash"),
        ])
        await ex.execute_code(mock_ctx, inp)

        interps = [call.args[0] for call in mock_client.commands_run.await_args_list]
        assert interps == ["python3", "bash -l"]

    @pytest.mark.asyncio
    async def test_empty_block_is_skipped(self, mock_client, mock_ctx):
        ex = CubeCodeExecutor(mock_client, _cfg())
        inp = CodeExecutionInput(code_blocks=[
            CodeBlock(code="", language="python"),
            CodeBlock(code="print('hi')", language="python"),
        ])
        await ex.execute_code(mock_ctx, inp)
        # Only the non-empty block runs.
        assert mock_client.commands_run.await_count == 1

    @pytest.mark.asyncio
    async def test_fallback_to_input_code_field(self, mock_client, mock_ctx):
        """When ``code_blocks`` is empty but ``code`` is set, use code."""
        mock_client.commands_run.return_value = _ok(stdout="")
        ex = CubeCodeExecutor(mock_client, _cfg())
        inp = CodeExecutionInput(code_blocks=[], code="print('fallback')")
        await ex.execute_code(mock_ctx, inp)
        # Single call with synthetic python block.
        call = mock_client.commands_run.await_args
        assert call.args[0] == "python3"
        assert call.kwargs["stdin"] == b"print('fallback')"

    @pytest.mark.asyncio
    async def test_all_empty_returns_empty_result(self, mock_client, mock_ctx):
        ex = CubeCodeExecutor(mock_client, _cfg())
        inp = CodeExecutionInput(code_blocks=[], code="")
        await ex.execute_code(mock_ctx, inp)
        mock_client.commands_run.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_unsupported_language_records_error_continues(self, mock_client, mock_ctx):
        """An unsupported block emits an error note; later blocks still run.

        BUG PROBE: the loop must not short-circuit on a bad-language block.
        """
        mock_client.commands_run.return_value = _ok(stdout="later\n")
        ex = CubeCodeExecutor(mock_client, _cfg())
        inp = CodeExecutionInput(code_blocks=[
            CodeBlock(code="code here", language="rust"),
            CodeBlock(code="print('after')", language="python"),
        ])
        result = await ex.execute_code(mock_ctx, inp)
        # First block skipped (no commands_run), second still ran.
        assert mock_client.commands_run.await_count == 1
        assert "unsupported" in result.output.lower() or "error" in result.output.lower()

    @pytest.mark.asyncio
    async def test_nonzero_exit_does_not_abort(self, mock_client, mock_ctx):
        mock_client.commands_run.side_effect = [
            _ok(stdout="a", exit_code=1, stderr="oops"),
            _ok(stdout="b"),
        ]
        ex = CubeCodeExecutor(mock_client, _cfg())
        inp = CodeExecutionInput(code_blocks=[
            CodeBlock(code="1", language="python"),
            CodeBlock(code="2", language="python"),
        ])
        await ex.execute_code(mock_ctx, inp)
        # Both blocks ran.
        assert mock_client.commands_run.await_count == 2

    @pytest.mark.asyncio
    async def test_closed_executor_raises(self, mock_client, mock_ctx):
        ex = CubeCodeExecutor(mock_client, _cfg())
        ex.close()
        with pytest.raises(RuntimeError, match="closed"):
            await ex.execute_code(mock_ctx, CodeExecutionInput(code="x"))

    @pytest.mark.asyncio
    async def test_custom_execute_timeout_forwarded(self, mock_client, mock_ctx):
        mock_client.commands_run.return_value = _ok()
        ex = CubeCodeExecutor(mock_client, _cfg(execute_timeout=7))
        inp = CodeExecutionInput(code_blocks=[CodeBlock(code="x", language="python")])
        await ex.execute_code(mock_ctx, inp)
        assert mock_client.commands_run.await_args.kwargs["timeout"] == 7
