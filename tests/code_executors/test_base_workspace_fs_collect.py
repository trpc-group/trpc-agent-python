# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for ``BaseWorkspaceFS`` collect helpers.

These tests pin down the shared "matches -> models" pipeline used by
every workspace backend (local / container / cube). The helpers were
moved onto :class:`BaseWorkspaceFS` so that subclasses can call them
directly via ``self._build_code_files`` / ``self._build_manifest_output``
and override them when needed; this suite exercises them as protected
static methods on the base class. They focus on edge paths that the
backend-specific tests don't otherwise exercise:

- ``_relativize`` fallback when an absolute match does not live under
  the workspace root.
- ``_build_code_files`` happy-path / dedupe / fetcher-failure /
  truncation flagging.
- ``_build_manifest_output`` limit handling (``max_files`` /
  ``max_total_bytes`` / per-file truncation), inline + save branches,
  fetcher failures, and the ``strict_truncated_save`` guard.
"""

from __future__ import annotations

from typing import Tuple

import pytest

from trpc_agent_sdk.code_executors import _base_workspace_runtime as _base
from trpc_agent_sdk.code_executors._base_workspace_runtime import BaseWorkspaceFS
from trpc_agent_sdk.code_executors._types import WorkspaceOutputSpec


def _make_fetcher(payloads):
    """Build a fetcher that yields ``payloads[path]`` honouring ``max_bytes``.

    ``payloads`` is a ``{path: bytes}`` map. Returns ``(slice, raw_size)``
    so the helpers can compute truncation flags.
    """

    async def _fetch(path: str, max_bytes: int) -> Tuple[bytes, int]:
        data = payloads[path]
        return data[:max_bytes], len(data)

    return _fetch


# ---------------------------------------------------------------------------
# _relativize
# ---------------------------------------------------------------------------


class TestRelativize:

    def test_strips_workspace_prefix(self):
        assert BaseWorkspaceFS._relativize("/ws", "/ws/sub/file.txt") == "sub/file.txt"

    def test_handles_trailing_slash_on_ws(self):
        # The helper appends ``"/"`` only when ws_path doesn't already end in
        # one, so a trailing slash on the input must not produce ``"//"``.
        assert BaseWorkspaceFS._relativize("/ws/", "/ws/file") == "file"

    def test_returns_full_path_when_outside_workspace(self):
        # Fallback when a match somehow escapes the workspace root (e.g. a
        # symlink resolution surfaced an absolute path on a different
        # mount). The full path is preserved verbatim rather than silently
        # mangled.
        full = "/elsewhere/file.txt"
        assert BaseWorkspaceFS._relativize("/ws", full) == full


# ---------------------------------------------------------------------------
# _build_code_files
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestBuildCodeFiles:

    async def test_basic_collection(self):
        payloads = {
            "/ws/a.txt": b"alpha",
            "/ws/sub/b.bin": b"\x00\x01beta",
        }
        files = await BaseWorkspaceFS._build_code_files(
            "/ws",
            ["/ws/a.txt", "/ws/sub/b.bin"],
            _make_fetcher(payloads),
        )
        names = sorted(f.name for f in files)
        assert names == ["a.txt", "sub/b.bin"]
        # Sizes / truncation flags must be populated from the fetcher's
        # raw_size, not just len(data).
        for f in files:
            assert f.truncated is False
            assert f.size_bytes == len(payloads[f"/ws/{f.name}"])

    async def test_deduplicates_by_relative_name(self):
        # Two glob patterns can yield the same absolute path. The helper
        # must surface only the first hit, not double-count it.
        payloads = {"/ws/a.txt": b"x"}
        files = await BaseWorkspaceFS._build_code_files(
            "/ws",
            ["/ws/a.txt", "/ws/a.txt"],
            _make_fetcher(payloads),
        )
        assert [f.name for f in files] == ["a.txt"]

    async def test_fetcher_failure_emits_sentinel(self):
        # collect() is best-effort: a single failing read must not abort
        # the whole batch. We expect an empty-content sentinel with the
        # canonical octet-stream MIME.
        payloads = {"/ws/ok.txt": b"hi"}

        async def fetcher(path, max_bytes):
            if path == "/ws/bad.txt":
                raise OSError("denied")
            data = payloads[path]
            return data[:max_bytes], len(data)

        files = await BaseWorkspaceFS._build_code_files(
            "/ws",
            ["/ws/bad.txt", "/ws/ok.txt"],
            fetcher,
        )
        assert len(files) == 2
        bad = next(f for f in files if f.name == "bad.txt")
        assert bad.content == ""
        assert bad.mime_type == "application/octet-stream"

    async def test_truncation_flag_set_when_raw_exceeds_data(self):
        # Fetcher reports a raw_size larger than the slice → the helper
        # must mark ``truncated=True``.
        async def fetcher(path, max_bytes):
            return b"hi", 1024

        files = await BaseWorkspaceFS._build_code_files(
            "/ws",
            ["/ws/big.bin"],
            fetcher,
            max_read_size=2,
        )
        assert len(files) == 1
        assert files[0].truncated is True
        assert files[0].size_bytes == 1024

    async def test_default_cap_uses_module_constant(self):
        # When ``max_read_size`` is omitted the helper falls back to
        # :data:`MAX_READ_SIZE_BYTES` (bound at definition time). Verify
        # the default actually flows into the fetcher as the byte budget.
        seen_caps: list[int] = []

        async def fetcher(path, max_bytes):
            seen_caps.append(max_bytes)
            return b"", 0

        await BaseWorkspaceFS._build_code_files("/ws", ["/ws/a"], fetcher)
        assert seen_caps == [_base.MAX_READ_SIZE_BYTES]

    async def test_explicit_cap_overrides_default(self):
        # Callers can still pass an explicit ``max_read_size`` to override
        # the module-level default.
        seen_caps: list[int] = []

        async def fetcher(path, max_bytes):
            seen_caps.append(max_bytes)
            return b"", 0

        await BaseWorkspaceFS._build_code_files(
            "/ws", ["/ws/a"], fetcher, max_read_size=7,
        )
        assert seen_caps == [7]


# ---------------------------------------------------------------------------
# _build_manifest_output
# ---------------------------------------------------------------------------


class _FakeArtifactCtx:
    """Minimal :class:`InvocationContext` stand-in that records save calls.

    ``save_artifact_helper`` only needs ``ctx.save_artifact(name, part)`` —
    we don't have to mock the whole context surface.
    """

    def __init__(self):
        self.saved: list[Tuple[str, bytes, str]] = []
        self._next_version = 1

    async def save_artifact(self, filename, artifact):
        # The helper wraps bytes in Part(inline_data=Blob(...)). Pull them
        # back out so assertions can stay terse.
        blob = artifact.inline_data
        self.saved.append((filename, blob.data, blob.mime_type))
        v = self._next_version
        self._next_version += 1
        return v


@pytest.mark.asyncio
class TestBuildManifestOutput:

    async def test_basic_inline(self):
        spec = WorkspaceOutputSpec(globs=["**/*"], inline=True)
        payloads = {"/ws/a.txt": b"alpha"}
        manifest, names, versions = await BaseWorkspaceFS._build_manifest_output(
            "/ws",
            spec,
            ["/ws/a.txt"],
            _make_fetcher(payloads),
            ctx=None,
        )
        assert names == [] and versions == []
        assert len(manifest.files) == 1
        ref = manifest.files[0]
        assert ref.name == "a.txt"
        assert ref.content == "alpha"
        assert ref.saved_as == ""
        assert ref.version == 0
        assert manifest.limits_hit is False

    async def test_save_branch_uses_name_template_and_records_versions(self):
        spec = WorkspaceOutputSpec(globs=["**/*"], save=True, name_template="run-1/")
        payloads = {"/ws/a.txt": b"alpha", "/ws/b.txt": b"beta"}
        ctx = _FakeArtifactCtx()
        manifest, names, versions = await BaseWorkspaceFS._build_manifest_output(
            "/ws",
            spec,
            ["/ws/a.txt", "/ws/b.txt"],
            _make_fetcher(payloads),
            ctx=ctx,
        )
        assert names == ["run-1/a.txt", "run-1/b.txt"]
        assert versions == [1, 2]
        # The artifact service must receive the full byte payload with the
        # detected MIME type; saved_as must mirror name_template + rel.
        assert [s[0] for s in ctx.saved] == ["run-1/a.txt", "run-1/b.txt"]
        assert manifest.files[0].saved_as == "run-1/a.txt"
        assert manifest.files[0].version == 1

    async def test_save_without_ctx_raises(self):
        spec = WorkspaceOutputSpec(globs=["**/*"], save=True)
        with pytest.raises(ValueError, match="Context is required"):
            await BaseWorkspaceFS._build_manifest_output(
                "/ws",
                spec,
                ["/ws/a.txt"],
                _make_fetcher({"/ws/a.txt": b"data"}),
                ctx=None,
            )

    async def test_max_files_limit_sets_limits_hit(self):
        spec = WorkspaceOutputSpec(globs=["**/*"], max_files=1)
        payloads = {"/ws/a.txt": b"a", "/ws/b.txt": b"b"}
        manifest, _, _ = await BaseWorkspaceFS._build_manifest_output(
            "/ws",
            spec,
            ["/ws/a.txt", "/ws/b.txt"],
            _make_fetcher(payloads),
            ctx=None,
        )
        assert len(manifest.files) == 1
        assert manifest.limits_hit is True

    async def test_max_total_bytes_first_guard_breaks_before_fetch(self):
        # First file fills the budget; second iteration's
        # ``total_bytes >= max_total`` guard breaks before any fetch.
        spec = WorkspaceOutputSpec(globs=["**/*"], max_total_bytes=3)
        payloads = {"/ws/a.txt": b"abc", "/ws/b.txt": b"def"}

        async def fetcher(path, max_bytes):
            data = payloads[path]
            return data[:max_bytes], len(data)

        manifest, _, _ = await BaseWorkspaceFS._build_manifest_output(
            "/ws",
            spec,
            ["/ws/a.txt", "/ws/b.txt"],
            fetcher,
            ctx=None,
        )
        assert [f.name for f in manifest.files] == ["a.txt"]
        assert manifest.limits_hit is True

    async def test_zero_read_budget_breaks_with_limits_hit(self, monkeypatch):
        # Defensive ``read_budget <= 0`` break. The only way to reach it
        # is when *both* the per-file cap and the total cap collapse to
        # <= 0 before the first fetch on a given iteration. We force
        # this by monkeypatching the resolved defaults so an unset
        # ``spec.max_file_bytes`` (which falls back to
        # ``MAX_READ_SIZE_BYTES``) and an unset ``spec.max_total_bytes``
        # (falls back to ``DEFAULT_MAX_TOTAL_BYTES``) both materialise
        # such that the inner ``min(...)`` collapses to zero — but the
        # outer guard ``total_bytes >= max_total`` only fires once
        # ``total_bytes`` is non-zero. Patch ``DEFAULT_MAX_TOTAL_BYTES``
        # slightly above zero to skip the outer guard and
        # ``MAX_READ_SIZE_BYTES`` to zero so
        # ``min(max_file_bytes=0, remaining_total>0) == 0`` and the
        # inner guard fires.
        monkeypatch.setattr(_base, "MAX_READ_SIZE_BYTES", 0)
        monkeypatch.setattr(_base, "DEFAULT_MAX_TOTAL_BYTES", 1)
        spec = WorkspaceOutputSpec(globs=["**/*"])

        async def fetcher(path, max_bytes):  # pragma: no cover - never invoked
            raise AssertionError("fetcher must not run when budget is zero")

        manifest, _, _ = await BaseWorkspaceFS._build_manifest_output(
            "/ws",
            spec,
            ["/ws/a.txt"],
            fetcher,
            ctx=None,
        )
        assert manifest.files == []
        assert manifest.limits_hit is True

    async def test_per_file_truncation_marks_limits_hit(self):
        # max_file_bytes < raw_size → fetcher returns a slice; helper
        # must flag ``limits_hit`` because the per-file cap actually bit.
        spec = WorkspaceOutputSpec(globs=["**/*"], max_file_bytes=2, inline=True)
        payloads = {"/ws/a.txt": b"abcdef"}
        manifest, _, _ = await BaseWorkspaceFS._build_manifest_output(
            "/ws",
            spec,
            ["/ws/a.txt"],
            _make_fetcher(payloads),
            ctx=None,
        )
        assert manifest.limits_hit is True
        assert manifest.files[0].content == "ab"

    async def test_strict_truncated_save_raises(self):
        # strict_truncated_save is the container's "refuse to persist a
        # half-read binary" guard.
        spec = WorkspaceOutputSpec(globs=["**/*"], save=True, max_file_bytes=2)
        payloads = {"/ws/big.bin": b"0123456789"}
        ctx = _FakeArtifactCtx()
        with pytest.raises(RuntimeError, match="cannot save truncated output file"):
            await BaseWorkspaceFS._build_manifest_output(
                "/ws",
                spec,
                ["/ws/big.bin"],
                _make_fetcher(payloads),
                ctx=ctx,
                strict_truncated_save=True,
            )
        # The save must NOT have been attempted before the raise.
        assert ctx.saved == []

    async def test_non_strict_truncated_save_persists_partial(self):
        # local/cube historically allow saving the truncated prefix; the
        # opposite side of the strict guard. Sanity-check that branch.
        spec = WorkspaceOutputSpec(globs=["**/*"], save=True, max_file_bytes=2)
        payloads = {"/ws/big.bin": b"0123456789"}
        ctx = _FakeArtifactCtx()
        manifest, names, _ = await BaseWorkspaceFS._build_manifest_output(
            "/ws",
            spec,
            ["/ws/big.bin"],
            _make_fetcher(payloads),
            ctx=ctx,
            strict_truncated_save=False,
        )
        assert names == ["big.bin"]
        assert ctx.saved[0][1] == b"01"
        assert manifest.limits_hit is True

    async def test_fetcher_failure_emits_sentinel_and_continues(self):
        # Mirrors _build_code_files behaviour: a single failing fetch
        # must surface as an empty ManifestFileRef while the rest of
        # the batch proceeds.
        spec = WorkspaceOutputSpec(globs=["**/*"], inline=True)
        payloads = {"/ws/ok.txt": b"ok"}

        async def fetcher(path, max_bytes):
            if path == "/ws/bad.bin":
                raise IOError("transient")
            data = payloads[path]
            return data[:max_bytes], len(data)

        manifest, _, _ = await BaseWorkspaceFS._build_manifest_output(
            "/ws",
            spec,
            ["/ws/bad.bin", "/ws/ok.txt"],
            fetcher,
            ctx=None,
        )
        names = [f.name for f in manifest.files]
        assert names == ["bad.bin", "ok.txt"]
        bad = manifest.files[0]
        assert bad.mime_type == "application/octet-stream"
        # Sentinel entries do NOT carry inlined content even when
        # spec.inline is set, because there are no bytes to decode.
        assert bad.content == ""
        ok = manifest.files[1]
        assert ok.content == "ok"

    async def test_dedup_by_relative_name(self):
        spec = WorkspaceOutputSpec(globs=["**/*"], inline=True)
        payloads = {"/ws/a.txt": b"x"}
        manifest, _, _ = await BaseWorkspaceFS._build_manifest_output(
            "/ws",
            spec,
            ["/ws/a.txt", "/ws/a.txt"],
            _make_fetcher(payloads),
            ctx=None,
        )
        assert len(manifest.files) == 1


# ---------------------------------------------------------------------------
# Subclass override surface
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_subclass_can_override_build_code_files():
    """Sanity-check that the protected helper is overridable.

    The whole point of moving these onto the base class was to let
    backends extend the post-fetch shape (e.g. emit a richer
    ``CodeFile`` subclass) without re-implementing the dedupe / sniff /
    cap loop. Pin that the override path is reachable.
    """

    class _CountingFS(BaseWorkspaceFS):
        # Concrete stubs for the abstract methods so we can instantiate.
        async def put_files(self, ws, files, ctx=None):  # pragma: no cover
            return None
        async def stage_directory(self, ws, src, dst, opt, ctx=None):  # pragma: no cover
            return None
        async def collect(self, ws, patterns, ctx=None):  # pragma: no cover
            return []
        async def stage_inputs(self, ws, specs, ctx=None):  # pragma: no cover
            return None
        async def collect_outputs(self, ws, spec, ctx=None):  # pragma: no cover
            from trpc_agent_sdk.code_executors._types import ManifestOutput
            return ManifestOutput()

        invocation_count = 0

        @staticmethod
        async def _build_code_files(ws_path, matches, fetcher, *, max_read_size=_base.MAX_READ_SIZE_BYTES):
            _CountingFS.invocation_count += 1
            return await BaseWorkspaceFS._build_code_files(
                ws_path, matches, fetcher, max_read_size=max_read_size,
            )

    fs = _CountingFS()
    files = await fs._build_code_files(
        "/ws", ["/ws/a.txt"], _make_fetcher({"/ws/a.txt": b"hi"}),
    )
    assert _CountingFS.invocation_count == 1
    assert [f.name for f in files] == ["a.txt"]
