# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Adversarial tests that document real bugs in the Cube implementation.

Each test in this file encodes the *correct* behaviour. When run today
the assertion fails (the production code has the bug), and the
``@pytest.mark.xfail(strict=True)`` marker records it as ``XFAIL`` — the
suite stays green but the bug is visible in ``pytest -v`` output.

If someone later fixes the bug, the assertion succeeds, ``strict=True``
turns that into a failing ``XPASS``, and the author is forced to flip
the marker off, which doubles as a regression sentinel.

This is a deliberate choice: every item here is a concrete,
reproducible defect, documented with an explicit file:line pointer.
"""

from __future__ import annotations

import posixpath
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from trpc_agent_sdk.code_executors._types import (
    WorkspaceInfo,
    WorkspaceInputSpec,
    WorkspaceOutputSpec,
    WorkspaceStageOptions,
)
from trpc_agent_sdk.code_executors.cube import _paths, _runtime
from trpc_agent_sdk.code_executors.cube._runtime import (
    CubeWorkspaceFS,
    CubeWorkspaceManager,
)
from trpc_agent_sdk.code_executors.utils import detect_content_type
from trpc_agent_sdk.code_executors.cube._sandbox import (
    CubeCommandResult,
    CubeSandboxClient,
)
from trpc_agent_sdk.code_executors.cube._types import CubeCodeExecutorConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ok(stdout="", stderr="", exit_code=0) -> CubeCommandResult:
    return CubeCommandResult(stdout=stdout, stderr=stderr, exit_code=exit_code, duration=0.0)


def _err(stderr="err", exit_code=1) -> CubeCommandResult:
    return CubeCommandResult(stdout="", stderr=stderr, exit_code=exit_code, duration=0.0)


def _ws(path="/ws/run_1") -> WorkspaceInfo:
    return WorkspaceInfo(id="r1", path=path)


@pytest.fixture
def mock_client():
    c = MagicMock(spec=CubeSandboxClient)
    c.sandbox_id = "sbx"
    c.commands_run = AsyncMock(return_value=_ok())
    c.read_file_bytes = AsyncMock(return_value=b"")
    c.write_file_bytes = AsyncMock()
    c.upload_path = AsyncMock()
    return c


# ===========================================================================
# BUG 1 — binary stdin is silently corrupted by UTF-8 replacement [FIXED]
#
# File: trpc_agent_sdk/code_executors/cube/_paths.py
#
# Originally ``wrap_stdin_heredoc`` did
# ``stdin.decode("utf-8", errors="replace")``, lossily turning any
# non-UTF-8 byte into U+FFFD before reaching the sandbox. The fix
# routes binary payloads through a ``base64 -d | cmd`` heredoc so the
# original bytes reach the command's stdin verbatim. UTF-8 payloads
# still take the simple ``cmd << 'MARKER'`` text fast path.
#
# This regression test drives the rendered command through real bash
# and asserts byte-for-byte recovery for every byte 0x00..0xff.
# ===========================================================================


def test_bug1_binary_stdin_preserved_byte_for_byte():
    """Every byte 0x00..0xff must reach the command's stdin verbatim."""
    import subprocess
    import tempfile

    payload = bytes(range(256))
    with tempfile.TemporaryDirectory() as tmp:
        sink = Path(tmp) / "received.bin"
        cmd = _paths.wrap_stdin_heredoc(f"cat > {sink}", payload)
        result = subprocess.run(
            ["bash", "-c", cmd], capture_output=True, check=True
        )
        assert result.returncode == 0, result.stderr
        assert sink.read_bytes() == payload, (
            "binary bytes lost on the wire to the sandbox shell"
        )


# ===========================================================================
# BUG 2 — heredoc marker collision only checks the payload, not the command
#
# File: trpc_agent_sdk/code_executors/cube/_paths.py:69-73
#     while marker in payload:
#
# If the ``command`` argument itself happens to contain the chosen
# marker (e.g. a multi-line shell wrapper embedding the same literal),
# the heredoc can close prematurely. The collision check only inspects
# ``payload``, not ``command``.
# ===========================================================================


def test_bug2_marker_collision_against_command(monkeypatch):
    # Regression sentinel for the fix at _paths.py:71. The first hex
    # collides with a marker embedded in the command; the second is
    # safe. The implementation must rotate to the safe marker.
    colliding_hex = "cafebabecafebabe"
    safe_hex = "1234567890abcdef"
    calls = {"n": 0}

    def fake_token_hex(_nbytes):
        calls["n"] += 1
        return colliding_hex if calls["n"] == 1 else safe_hex

    monkeypatch.setattr(_paths.secrets, "token_hex", fake_token_hex)

    colliding_marker = f"TRPC_STDIN_EOF_{colliding_hex}"
    safe_marker = f"TRPC_STDIN_EOF_{safe_hex}"
    adversarial_cmd = f"cat\n{colliding_marker}\necho after"
    payload = b"harmless body"

    out = _paths.wrap_stdin_heredoc(adversarial_cmd, payload)

    # Collision detection must have consumed the first candidate and
    # selected the safe marker as the actual heredoc delimiter.
    assert calls["n"] >= 2
    assert out.endswith(f"\n{safe_marker}")
    assert f"<< '{safe_marker}'" in out
    # The adversarial line in the command remains as data, but it must
    # NOT match the chosen heredoc delimiter.
    assert colliding_marker != safe_marker
    # Sanity: the closing marker comes after the payload body.
    closing_index = out.rindex(f"\n{safe_marker}")
    payload_index = out.index(payload.decode())
    assert closing_index > payload_index


# ===========================================================================
# BUG 3 — stage_directory(read_only=True) silently ignores chmod failures
#
# File: trpc_agent_sdk/code_executors/cube/_runtime.py:170-171
#     if opt.read_only:
#         await self._client.commands_run(
#             f"chmod -R a-w {shell_quote(target)}", timeout=self._timeout)
#
# The result's exit_code is ignored. If chmod fails (permissions,
# missing tool, read-only filesystem), the read_only guarantee is
# quietly violated — the caller believes the directory is locked
# when it isn't.
# ===========================================================================


@pytest.mark.asyncio
async def test_bug3_chmod_failure_must_raise(mock_client, tmp_path):
    src = tmp_path / "d"
    src.mkdir()
    fs = CubeWorkspaceFS(mock_client, 30.0)

    # upload_path succeeds; chmod fails.
    def commands_router(cmd, **kwargs):
        if cmd.startswith("chmod"):
            return _err("chmod: Operation not permitted")
        return _ok()

    mock_client.commands_run.side_effect = commands_router

    with pytest.raises(RuntimeError):
        await fs.stage_directory(
            _ws(), str(src), "d", WorkspaceStageOptions(read_only=True)
        )


# ===========================================================================
# BUG 4 — cleanup() pops cache before rm; on rm failure the workspace is
#         orphaned AND unrecoverable via a retry
#
# File: trpc_agent_sdk/code_executors/cube/_runtime.py:123-129
#     info = self._ws_paths.pop(exec_id, None)   # <-- pop first
#     if not info or not info.path:
#         return
#     cmd = f"rm -rf {shell_quote(info.path)}"
#     result = await self._client.commands_run(cmd, ...)
#     if result.exit_code != 0:
#         raise RuntimeError(...)                 # <-- but cache already gone
#
# If rm fails, cache entry is lost; calling cleanup() again is a no-op
# ("unknown id" branch), so there is no way to retry cleanup through the
# manager interface. The remote dir becomes a permanent orphan.
# ===========================================================================


@pytest.mark.asyncio
async def test_bug4_cleanup_retryable_on_rm_failure(mock_client):
    mgr = CubeWorkspaceManager(mock_client, "/ws", 30.0)
    info = await mgr.create_workspace("id")

    # First cleanup: rm fails.
    mock_client.commands_run.side_effect = [_err("rm fail")]
    with pytest.raises(RuntimeError):
        await mgr.cleanup("id")

    # A second cleanup call should still try to rm the orphan, because
    # the first attempt failed. The bug: the id was popped from the
    # cache already, so this call is a silent no-op.
    mock_client.commands_run.reset_mock()
    mock_client.commands_run.side_effect = None
    mock_client.commands_run.return_value = _ok()
    await mgr.cleanup("id")
    # Assertion: the second cleanup must have issued a rm -rf again.
    assert any(
        call.args[0].startswith("rm -rf")
        for call in mock_client.commands_run.await_args_list
    ), "second cleanup silently did nothing — remote workspace orphaned"


# ===========================================================================
# BUG 5 — glob patterns containing spaces got word-split [FIXED]
#
# File: trpc_agent_sdk/code_executors/cube/_runtime.py
#
# Originally the rendered shell was
#     for p in 'my dir/*.txt'; do for f in $p; do ...
# The outer `for p in ...` preserved the quoting, but the inner
# `for f in $p` was unquoted, so bash performed word-splitting on $p
# and turned "my dir/*.txt" into two patterns "my" and "dir/*.txt".
# Quoting `"$p"` would have suppressed splitting but also disabled
# globbing.
#
# The fix passes patterns via a bash array (preserves spaces per
# element) and temporarily clears IFS so the unquoted `$p` inside
# `matches=( $p )` is *not* word-split, while bash still performs path
# expansion on it. globstar is preserved (compgen -G does not honour
# **, so it's intentionally not used).
#
# This regression test drives the actual rendered command — taken from
# `_glob` via a real fake client — through bash and asserts the
# expected matches.
# ===========================================================================


@pytest.mark.asyncio
async def test_bug5_glob_pattern_with_space():
    """Glob patterns that contain spaces must match as a single literal."""
    import subprocess
    import tempfile
    import os

    with tempfile.TemporaryDirectory() as tmp:
        target_dir = os.path.join(tmp, "my dir")
        os.makedirs(target_dir)
        Path(target_dir, "file.txt").write_text("content")
        os.makedirs(os.path.join(tmp, "a", "b"))
        Path(tmp, "a", "b", "deep.txt").write_text("deep")

        captured: list[str] = []

        async def fake_run(cmd, timeout=None):
            captured.append(cmd)
            r = subprocess.run(
                ["bash", "-c", cmd], capture_output=True, text=True
            )
            return CubeCommandResult(
                stdout=r.stdout, stderr=r.stderr, exit_code=r.returncode, duration=0.0
            )

        client = MagicMock(spec=CubeSandboxClient)
        client.commands_run = AsyncMock(side_effect=fake_run)
        fs = CubeWorkspaceFS(client, 30.0)

        # Pattern with a space must match exactly the one file under "my dir".
        out = await fs._glob(tmp, ["my dir/*.txt"])
        assert any(p.endswith("my dir/file.txt") for p in out), (
            f"word-splitting corrupted the glob: {out!r}"
        )
        assert len(out) == 1, f"unexpected matches: {out!r}"

        # globstar (**) must still work after the fix.
        out2 = await fs._glob(tmp, ["**/*.txt"])
        joined = "\n".join(out2)
        assert "deep.txt" in joined and "file.txt" in joined, (
            f"globstar regressed: {out2!r}"
        )


# ===========================================================================
# BUG 6 — collect_outputs() does not dedup by relative path
#
# File: trpc_agent_sdk/code_executors/cube/_runtime.py:251-278
#
# ``collect()`` has an explicit ``seen: set[str]`` dedup step. The
# sibling ``collect_outputs()`` walks the same glob result but has no
# dedup. When two patterns overlap (e.g. ``['*.txt', 'out/*.txt']``),
# the same file is emitted twice, double-counted against ``max_files``,
# and — if ``save=True`` — saved twice as an artifact.
# ===========================================================================


@pytest.mark.asyncio
async def test_bug6_collect_outputs_dedups_by_rel(mock_client):
    ws = _ws()
    # Glob returns the same file twice (overlapping patterns).
    mock_client.commands_run.return_value = _ok(
        stdout=f"{ws.path}/a.txt\n{ws.path}/a.txt\n"
    )
    mock_client.read_file_bytes.return_value = b"x"
    fs = CubeWorkspaceFS(mock_client, 30.0)

    manifest = await fs.collect_outputs(ws, WorkspaceOutputSpec(globs=["*.txt", "./a.txt"]))
    # Expected: one file, not two.
    names = [f.name for f in manifest.files]
    assert names == ["a.txt"], f"duplicate emitted: {names}"


# ===========================================================================
# BUG 7 — _detect_mime over-eagerly labels anything starting with { or [ as JSON
#
# File: trpc_agent_sdk/code_executors/cube/_runtime.py:83-84
#     if sample.startswith(b"{") or sample.startswith(b"["):
#         return "application/json"
#
# Python pickle protocol-0, MessagePack, BSON, Lua tables, gnuplot
# output, shell brace expansion logs — all start with ``{`` or ``[``
# without being JSON. A zero-length JSON check (no matching closer, no
# structural parse) is not a reliable sniff.
# ===========================================================================


def test_bug7_detect_mime_not_json_for_python_repr():
    """Python repr of a dict starts with ``{`` but must not be labelled JSON.

    Regression test for the historical ``_detect_mime`` bug where any
    payload starting with ``{`` or ``[`` was blindly classified as
    ``application/json``. The shared :func:`detect_content_type`
    helper now validates JSON via ``json.loads``, so a Python repr
    (single-quoted keys, ``None``/``True`` literals) falls through to
    a text/binary classification instead.
    """
    sample = b"{'key': 'value', 'n': 3}"
    mime = detect_content_type(Path("noextension_please"), sample)
    assert mime != "application/json", (
        f"false-positive JSON classification: Python repr labelled as {mime}"
    )


# ===========================================================================
# BUG 8 — create_workspace trusts cache without re-verifying the remote dir
#         [FIXED]
#
# File: trpc_agent_sdk/code_executors/cube/_runtime.py
#
# Originally the manager early-returned on a pure in-memory dict hit:
#
#     if exec_id in self._ws_paths:
#         return self._ws_paths[exec_id]
#
# If the remote directory was deleted externally (operator cleanup,
# sandbox snapshot rollback, sibling cleanup() on a shared sandbox,
# host process restart re-attaching to a live sandbox) the cache still
# returned the stale path; subsequent put_files / run_program /
# collect_outputs / stage_inputs targeted a non-existent path and
# failed deep inside with cryptic "No such file" errors instead of a
# clean "workspace vanished; recreate" signal.
#
# Fix: the path remains stable per exec_id (callers can rely on it),
# but every create_workspace call now unconditionally re-issues an
# idempotent ``mkdir -p`` for the four standard subdirs. ``mkdir -p``
# is a no-op when the tree already exists, so steady-state cost is one
# round-trip; on miss the workspace heals transparently.
# ===========================================================================


@pytest.mark.asyncio
async def test_bug8_create_workspace_reconciles_with_remote(mock_client):
    """Repeat create_workspace calls keep a stable path and re-issue an
    idempotent mkdir -p so the workspace heals if it was deleted externally.
    """
    mgr = CubeWorkspaceManager(mock_client, "/ws", 30.0)
    info1 = await mgr.create_workspace("id")

    # Simulate an external force having deleted the remote dir between
    # calls. A correct implementation must re-issue mkdir -p (so the
    # tree is healed) and must keep the same path for the same exec_id
    # (callers cache it and rely on path stability).
    mock_client.commands_run.reset_mock()
    info2 = await mgr.create_workspace("id")

    assert info2.path == info1.path, (
        "path must stay stable across calls for the same exec_id"
    )
    assert mock_client.commands_run.await_count == 1, (
        "cache returned stale WorkspaceInfo without reconciling remote"
    )
    cmd = mock_client.commands_run.await_args.args[0]
    assert "mkdir -p" in cmd
    assert f"'{info1.path}'" in cmd
    # All four standard subdirs must be in the reconciling mkdir.
    for sub in ("work", "out", "skills", "runs"):
        assert posixpath.join(info1.path, sub) in cmd or f"'{info1.path}/{sub}'" in cmd


@pytest.mark.asyncio
async def test_bug8_create_workspace_surfaces_mkdir_failure_on_reconcile(mock_client):
    """If the reconciling mkdir -p fails (e.g. parent vanished, perms),
    the second create_workspace must raise a clear error instead of
    silently handing back a stale, broken WorkspaceInfo.
    """
    mgr = CubeWorkspaceManager(mock_client, "/ws", 30.0)
    await mgr.create_workspace("id")

    mock_client.commands_run.reset_mock()
    mock_client.commands_run.return_value = _err("mkdir: cannot create directory")
    with pytest.raises(RuntimeError, match="Failed to create cube workspace"):
        await mgr.create_workspace("id")


# ===========================================================================
# BUG 9 — collect() decodes binary files to str with errors="replace"
#
# File: trpc_agent_sdk/code_executors/cube/_runtime.py:230
#     content=content.decode("utf-8", errors="replace"),
#
# ``CodeFile.content: str`` forces a string, so binary files (PDFs,
# images, gzip archives) are converted to a UTF-8 replacement-laden
# mess. Downstream consumers cannot recover the original bytes. The
# sibling ``collect_outputs`` avoids this with ``inline=True``
# gated — but ``collect()`` has no such guard.
# ===========================================================================


@pytest.mark.xfail(strict=True, reason="BUG 9: collect() corrupts binary files to str (_runtime.py:230)")
@pytest.mark.asyncio
async def test_bug9_collect_preserves_binary_bytes(mock_client):
    ws = _ws()
    mock_client.commands_run.return_value = _ok(stdout=f"{ws.path}/image.png\n")
    binary = b"\x89PNG\r\n\x1a\nnot-valid-utf8\x80\x81\x82"
    mock_client.read_file_bytes.return_value = binary
    fs = CubeWorkspaceFS(mock_client, 30.0)
    files = await fs.collect(ws, ["*.png"])
    assert len(files) == 1
    # The raw bytes should be recoverable. They are not: utf-8
    # replace turns \x80 into U+FFFD, and re-encoding does not roundtrip.
    assert files[0].content.encode("utf-8") == binary, (
        "binary file silently corrupted by utf-8 replace"
    )


# ===========================================================================
# BUG 10 — open_new truncates fractional idle_timeout via int()  [FIXED]
#
# Original failure mode: ``CubeCodeExecutorConfig(idle_timeout=0.9)`` was
# accepted (field was typed ``float``) and then silently truncated by
# ``timeout=int(cfg.idle_timeout)`` in ``_sandbox.py``. ``int(0.9) == 0``,
# which most sandbox APIs interpret as "no timeout" or "expire immediately".
#
# Fix: ``idle_timeout`` is now typed ``int`` (matching the e2b API
# contract) and ``CubeCodeExecutorConfig.__post_init__`` rejects values
# that are non-int or < 1. The ``int(...)`` cast in ``open_new`` /
# ``set_timeout`` is gone — values flow through unchanged.
# ===========================================================================


@pytest.mark.asyncio
async def test_bug10_fractional_idle_timeout_rejected_at_construction(
    fake_e2b, fake_async_sandbox,
):
    fake_e2b.AsyncSandbox.create = AsyncMock(return_value=fake_async_sandbox)
    with pytest.raises(TypeError, match="idle_timeout must be an int"):
        CubeCodeExecutorConfig(
            template="t", api_url="u", api_key="k",
            idle_timeout=0.9,  # type: ignore[arg-type]
        )


@pytest.mark.asyncio
async def test_bug10_zero_idle_timeout_rejected_at_construction():
    with pytest.raises(ValueError, match="idle_timeout must be >= 1"):
        CubeCodeExecutorConfig(
            template="t", api_url="u", api_key="k",
            idle_timeout=0,
        )


@pytest.mark.asyncio
async def test_bug10_int_idle_timeout_passed_through_unchanged(
    fake_e2b, fake_async_sandbox,
):
    fake_e2b.AsyncSandbox.create = AsyncMock(return_value=fake_async_sandbox)
    cfg = CubeCodeExecutorConfig(
        template="t", api_url="u", api_key="k",
        idle_timeout=42,
    )
    await CubeSandboxClient.open_new(cfg)
    kwargs = fake_e2b.AsyncSandbox.create.await_args.kwargs
    assert kwargs["timeout"] == 42
    assert isinstance(kwargs["timeout"], int)


# ===========================================================================
# BUG 11 — cp -a with pre-existing destination directory nests the source [FIXED]
#
# File: trpc_agent_sdk/code_executors/cube/_runtime.py  (``_copy_remote``)
#
# Original failure mode: ``cp -a SRC DST`` has the long-standing POSIX
# directory-footgun — if DST already exists as a directory (e.g. from a
# prior stage_inputs call on the same dst), cp copies SRC *into* DST as
# DST/basename(SRC), nesting sources instead of replacing them.
#
# Fix: ``_copy_remote`` now does ``mkdir -p parent(DST); rm -rf DST;
# cp -a SRC DST`` — the defensive rm removes any stale dst before the
# copy, so the second call is idempotent. The rm step surfaces its own
# failures with ``remote rm failed:`` so silent mis-stages are impossible.
#
# This regression test pins the emitted command sequence so a refactor
# that drops the rm step (or re-orders the pipeline) fails loudly.
# ===========================================================================


@pytest.mark.asyncio
async def test_bug11_copy_remote_issues_rm_before_cp(mock_client):
    """Pin the ``mkdir → rm -rf → cp -a`` sequence in ``_copy_remote``."""
    fs = CubeWorkspaceFS(mock_client, 30.0)
    await fs._copy_remote("/src", "/dst")

    cmds = [call.args[0] for call in mock_client.commands_run.await_args_list]
    assert len(cmds) == 3, f"expected 3 shell steps, got {len(cmds)}: {cmds!r}"
    assert cmds[0].startswith("mkdir -p"), f"step 0 must be mkdir, got: {cmds[0]!r}"
    assert cmds[1].startswith("rm -rf"), (
        f"step 1 must be the defensive rm (cp -a directory-footgun guard), "
        f"got: {cmds[1]!r}"
    )
    assert "'/dst'" in cmds[1], "rm must target DST"
    assert cmds[2].startswith("cp -a"), f"step 2 must be cp -a, got: {cmds[2]!r}"
    # And the rm must come BEFORE the cp.
    rm_idx = next(i for i, c in enumerate(cmds) if c.startswith("rm -rf"))
    cp_idx = next(i for i, c in enumerate(cmds) if c.startswith("cp -a"))
    assert rm_idx < cp_idx, "rm must precede cp"





@pytest.mark.asyncio
async def test_bug12_commands_run_translates_timeout_to_structured_result(
    fake_e2b, fake_async_sandbox,
):
    """TimeoutException at the e2b boundary must become a CubeCommandResult."""
    fake_async_sandbox.commands.run = AsyncMock(
        side_effect=fake_e2b.TimeoutException()
    )
    client = CubeSandboxClient(
        fake_async_sandbox,
        CubeCodeExecutorConfig(template="t", api_url="u", api_key="k", idle_timeout=60, execute_timeout=30.0),
    )
    result = await client.commands_run("sleep 9999", timeout=1.5)
    assert isinstance(result, CubeCommandResult)
    assert result.timed_out is True, "timed_out flag must be set"
    assert result.exit_code == -1, (
        f"exit_code on timeout must be -1 (matches local/container "
        f"executors); got {result.exit_code}"
    )
    assert result.stdout == "", f"stdout must be empty on timeout: {result.stdout!r}"
    # The rewritten stderr is short and hand-written. Importantly, it does
    # NOT contain the e2b vendor boilerplate.
    assert "timed out" in result.stderr.lower(), (
        f"stderr must describe the timeout: {result.stderr!r}"
    )
    assert "1.5" in result.stderr, (
        f"stderr must mention the configured timeout value: {result.stderr!r}"
    )
    for leaked in ("passing 'timeout'", "context deadline exceeded", "Use '0'"):
        assert leaked not in result.stderr, (
            f"vendor message leaked into stderr: {leaked!r} in {result.stderr!r}"
        )


@pytest.mark.asyncio
async def test_bug12_execute_code_surfaces_deadline_exceeded_outcome(
    fake_e2b, fake_async_sandbox,
):
    """Timeout must appear as OUTCOME_DEADLINE_EXCEEDED, not a raised exception."""
    from trpc_agent_sdk.code_executors._types import (
        CodeBlock,
        CodeExecutionInput,
        Outcome,
    )
    from trpc_agent_sdk.code_executors.cube._code_executor import CubeCodeExecutor

    fake_async_sandbox.commands.run = AsyncMock(
        side_effect=fake_e2b.TimeoutException()
    )
    cfg = CubeCodeExecutorConfig(
        template="t", api_url="u", api_key="k",
        idle_timeout=60, execute_timeout=2.0,
    )
    client = CubeSandboxClient(fake_async_sandbox, cfg)
    executor = CubeCodeExecutor(client, cfg)

    # execute_code MUST return a result, not raise.
    result = await executor.execute_code(
        invocation_context=None,  # type: ignore[arg-type]
        code_execution_input=CodeExecutionInput(
            code_blocks=[CodeBlock(code="import time; time.sleep(9999)", language="python")],
        ),
    )
    assert result.outcome == Outcome.OUTCOME_DEADLINE_EXCEEDED, (
        f"expected OUTCOME_DEADLINE_EXCEEDED, got {result.outcome}"
    )
    assert "timed out" in result.output.lower(), (
        f"output must mention the timeout: {result.output!r}"
    )
