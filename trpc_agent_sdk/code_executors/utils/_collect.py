# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Shared "matches -> models" pipeline for workspace output collection.

Every workspace backend (local / container / cube) has to walk a list of
matched file paths, read their bytes (with per-file and cumulative
caps), sniff the MIME type, optionally inline the content, and
optionally persist the bytes via the artifact service. The *how-to-read*
part is backend-specific (direct filesystem vs. docker ``get_archive``
vs. Cube RPC), but everything after "we have the bytes" is identical.

This module factors that shared tail into two small helpers:

- :func:`build_code_files` — materialises a ``collect(...)`` call, which
  historically returns :class:`CodeFile` and only caps per-file size.
- :func:`build_manifest_output` — materialises a ``collect_outputs(...)``
  call, which honours :class:`WorkspaceOutputSpec` (limits, inline,
  save, name_template) and produces a :class:`ManifestOutput`.

Backends supply a ``fetcher`` coroutine that knows how to fetch the
raw bytes of a single absolute path — bounded by an input byte budget —
plus the *raw* size of the file on the underlying medium. Returning
the raw size separately lets the shared helpers compute
``truncated`` / ``limits_hit`` without requiring the fetcher to read
past the budget.
"""

from __future__ import annotations

from typing import Awaitable
from typing import Callable
from typing import List
from typing import Optional
from typing import Tuple

from trpc_agent_sdk.context import InvocationContext

from .._artifacts import save_artifact_helper
from .._constants import DEFAULT_MAX_FILES
from .._constants import DEFAULT_MAX_TOTAL_BYTES
from .._constants import MAX_READ_SIZE_BYTES
from .._types import CodeFile
from .._types import ManifestFileRef
from .._types import ManifestOutput
from .._types import WorkspaceOutputSpec
from ._files import detect_content_type

# A fetcher is an async callable ``(absolute_path, max_bytes) -> (data, raw_size)``.
#
# Contract:
# - ``data`` is the file's content truncated to at most ``max_bytes``. If the
#   underlying medium cannot cheaply report the full size (e.g. a streaming
#   read), the fetcher may return ``raw_size = len(data)``; callers that care
#   about truncation must then treat ``len(data) == max_bytes`` as "possibly
#   truncated".
# - ``raw_size`` is the size of the file on the underlying medium before any
#   truncation, used only to decide ``truncated`` / ``limits_hit`` flags.
# - The fetcher must *not* raise for merely-empty files; it should raise only
#   for genuine I/O errors so the backend can surface a meaningful message.
ManifestFetcher = Callable[[str, int], Awaitable[Tuple[bytes, int]]]


def _relativize(ws_path: str, full_path: str) -> str:
    """Return ``full_path`` stripped of the ``ws.path + "/"`` prefix.

    Kept as a single helper so every backend produces identical relative
    paths in :class:`CodeFile` / :class:`ManifestFileRef`.
    """
    prefix = ws_path.rstrip("/") + "/"
    if full_path.startswith(prefix):
        return full_path[len(prefix):]
    return full_path


async def build_code_files(
    ws_path: str,
    matches: List[str],
    fetcher: ManifestFetcher,
    *,
    max_read_size: Optional[int] = None,
) -> List[CodeFile]:
    """Materialise a :meth:`BaseWorkspaceFS.collect` call.

    Reads each matched path with a single per-file byte cap
    (``max_read_size``, defaulting to :data:`MAX_READ_SIZE_BYTES` resolved
    at call time so tests can ``monkeypatch.setattr`` the constant),
    sniffs the MIME type, and wraps the result in a :class:`CodeFile`.
    Duplicate ``rel`` paths are skipped so callers can pass the raw glob
    output without pre-deduping.
    """
    cap = MAX_READ_SIZE_BYTES if max_read_size is None else max_read_size
    seen: set[str] = set()
    out: List[CodeFile] = []
    for full_path in matches:
        rel = _relativize(ws_path, full_path)
        if rel in seen:
            continue
        seen.add(rel)
        try:
            data, raw_size = await fetcher(full_path, cap)
        except Exception:  # pylint: disable=broad-except
            # Keep collect() best-effort: a single unreadable file must
            # not abort the whole batch. Backends that prefer strict
            # semantics can short-circuit themselves before calling us.
            out.append(CodeFile(name=rel, content="", mime_type="application/octet-stream"))
            continue
        mime = detect_content_type(full_path, data)
        out.append(
            CodeFile(
                name=rel,
                content=data.decode("utf-8", errors="replace"),
                mime_type=mime,
                size_bytes=raw_size,
                truncated=raw_size > len(data),
            ))
    return out


async def build_manifest_output(
    ws_path: str,
    spec: WorkspaceOutputSpec,
    matches: List[str],
    fetcher: ManifestFetcher,
    ctx: Optional[InvocationContext],
    *,
    strict_truncated_save: bool = False,
) -> Tuple[ManifestOutput, List[str], List[int]]:
    """Materialise a :meth:`BaseWorkspaceFS.collect_outputs` call.

    Applies ``spec``'s limits (``max_files`` / ``max_file_bytes`` /
    ``max_total_bytes``), fills ``inline`` / ``save`` branches, and
    produces a :class:`ManifestOutput`. Also returns the list of saved
    artifact names and versions so backends that record metadata (e.g.
    local's ``OutputRecordMeta``) don't need to re-scan the manifest.

    Args:
        ws_path: Absolute workspace path, used to produce relative
            ``name`` fields.
        spec: The output spec declared by the caller.
        matches: Absolute paths already filtered by the backend's glob.
        fetcher: Async callable that returns ``(data, raw_size)`` for a
            path, capped by a requested byte budget. See
            :data:`ManifestFetcher`.
        ctx: Invocation context. Required when ``spec.save`` is set,
            because artifact persistence goes through it.
        strict_truncated_save: When ``True``, raise ``RuntimeError`` if
            ``spec.save`` is requested for a file that was truncated by
            the per-file cap. Container preserves this "refuse to save
            half a binary" behaviour; local/cube historically allow it.

    Returns:
        Tuple of ``(manifest, saved_names, saved_versions)``.
    """
    max_files = spec.max_files or DEFAULT_MAX_FILES
    max_file_bytes = spec.max_file_bytes or MAX_READ_SIZE_BYTES
    max_total = spec.max_total_bytes or DEFAULT_MAX_TOTAL_BYTES

    manifest = ManifestOutput()
    saved_names: List[str] = []
    saved_versions: List[int] = []

    seen: set[str] = set()
    total_bytes = 0
    count = 0

    for full_path in matches:
        # Check limits *before* fetching so a blown budget doesn't cause
        # a useless read of the next big file.
        if count >= max_files or total_bytes >= max_total:
            manifest.limits_hit = True
            break

        rel = _relativize(ws_path, full_path)
        if rel in seen:
            continue
        seen.add(rel)

        # Per-file cap is ``max_file_bytes``, but also clamp to the
        # remaining total budget so a single huge file cannot exceed
        # ``max_total`` all on its own.
        remaining_total = max_total - total_bytes
        read_budget = min(max_file_bytes, remaining_total)
        if read_budget <= 0:
            manifest.limits_hit = True
            break

        try:
            data, raw_size = await fetcher(full_path, read_budget)
        except Exception:  # pylint: disable=broad-except
            # Mirror ``build_code_files``: a single unreadable file must
            # not abort the whole collection. Emit a sentinel entry with
            # empty content and the canonical "unknown / unreadable"
            # MIME type. This preserves the pre-refactor local behaviour
            # (``_read_limited_with_cap`` caught and returned
            # ``("", "application/octet-stream")``) and is a small
            # tolerance upgrade for the container backend, which used to
            # abort on the first transient tar error.
            manifest.files.append(ManifestFileRef(name=rel, mime_type="application/octet-stream"))
            count += 1
            continue

        # Mark limits_hit if either cap actually bit.
        if raw_size > len(data):
            manifest.limits_hit = True

        truncated = raw_size > len(data)
        if truncated and spec.save and strict_truncated_save:
            raise RuntimeError(f"cannot save truncated output file: {rel}")

        total_bytes += len(data)
        count += 1

        mime = detect_content_type(full_path, data)
        file_ref = ManifestFileRef(name=rel, mime_type=mime)

        if spec.inline:
            file_ref.content = data.decode("utf-8", errors="replace")

        if spec.save:
            if ctx is None:
                raise ValueError("Context is required to save artifacts")
            save_name = (spec.name_template + rel) if spec.name_template else rel
            version = await save_artifact_helper(ctx, save_name, data, mime)
            file_ref.saved_as = save_name
            file_ref.version = version
            saved_names.append(save_name)
            saved_versions.append(version)

        manifest.files.append(file_ref)

    return manifest, saved_names, saved_versions
