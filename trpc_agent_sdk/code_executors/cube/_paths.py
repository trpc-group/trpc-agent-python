# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Pure remote-path and shell-quoting helpers for the Cube package.

No e2b dependency, no ``AsyncSandbox`` reference — these are stateless
string utilities usable by any adapter that targets a Cube/E2B remote
workspace. Lives in its own module so :mod:`._sandbox`, :mod:`._transfer`,
:mod:`._runtime`, and future external adapters (e.g. hermes) can import
them without dragging in the sandbox client or the e2b extra.
"""

from __future__ import annotations

import base64
import posixpath
import secrets

# Random suffix prefix for the bash heredoc marker emitted by
# `wrap_stdin_heredoc`. Chosen to be unlikely to collide with payload
# content while remaining greppable in command logs.
_HEREDOC_MARKER_PREFIX = "TRPC_STDIN_EOF"

# Width of base64 lines emitted on the binary heredoc path. 76 matches
# the canonical MIME wrapping width and keeps command logs readable.
_BASE64_LINE_WIDTH = 76


def shell_quote(value: str) -> str:
    """Single-quote a string for safe inclusion in a bash command line."""
    if not value:
        return "''"
    return "'" + value.replace("'", "'\\''") + "'"


def normalize_remote_relative(path: str, *, allow_current: bool = False) -> str:
    """Normalize a relative remote path and reject escape attempts.

    Pure remote-path logic: this does not know about any host-side
    workspace and never tries to map host absolute paths to
    workspace-relative ones.
    """
    if not path or not path.strip():
        if allow_current:
            return ""
        raise ValueError("cube remote path must not be empty.")
    normalized = posixpath.normpath(path.strip().replace("\\", "/"))
    if normalized in ("", "."):
        if allow_current:
            return ""
        raise ValueError("cube remote path must not be empty.")
    if normalized.startswith("/") or normalized == ".." or normalized.startswith("../"):
        raise ValueError(f"cube remote path escapes its root: {path}")
    return normalized


def join_remote(remote_root: str, relative: str) -> str:
    """Join a relative path under a remote root and collapse ``..`` components."""
    if not relative:
        return remote_root
    return posixpath.normpath(posixpath.join(remote_root, relative))


def wrap_stdin_heredoc(command: str, stdin: bytes) -> str:
    """Embed ``stdin`` as a bash heredoc so the command receives it as input.

    The e2b SDK's ``commands.run(stdin=...)`` is a bool toggle, not a data
    channel, so we transport the payload inside the command string
    itself. Two paths are emitted depending on whether the payload is
    valid UTF-8:

    - **Text fast path.** UTF-8 payloads are inlined verbatim as
      ``{command} << 'MARKER'``. The rendered command stays readable
      in logs and incurs no extra subprocess.
    - **Binary path.** Non-UTF-8 payloads are base64-encoded and routed
      through ``base64 -d | {command}`` so the original bytes reach the
      command's stdin byte-for-byte. ``base64`` ships with coreutils
      and is present in every Cube/E2B template. The base64 alphabet
      (``A-Za-z0-9+/=``) cannot contain ``_``, so the heredoc marker
      can never collide with the payload on this path.

    The marker collision check inspects *both* ``payload`` and
    ``command`` — a marker accidentally embedded in the wrapper
    command (e.g. a multi-line shell function whose body contains the
    chosen literal) would otherwise close the heredoc prematurely.

    For shipping large binary blobs (assets, datasets, etc.), prefer
    :meth:`CubeSandboxClient.upload_path` over piping through stdin.
    """
    try:
        payload = stdin.decode("utf-8")
    except UnicodeDecodeError:
        return _wrap_binary_stdin_heredoc(command, stdin)
    marker = f"{_HEREDOC_MARKER_PREFIX}_{secrets.token_hex(8)}"
    while marker in payload or marker in command:
        marker = f"{_HEREDOC_MARKER_PREFIX}_{secrets.token_hex(8)}"
    return f"{command} << '{marker}'\n{payload}\n{marker}"


def _wrap_binary_stdin_heredoc(command: str, stdin: bytes) -> str:
    """Render a base64-on-the-wire heredoc for non-UTF-8 stdin payloads.

    The base64 alphabet excludes ``_`` so no marker collision with the
    body is possible; the only retry case is a marker that already
    appears inside ``command`` itself.
    """
    encoded = base64.b64encode(stdin).decode("ascii")
    body = "\n".join(encoded[i:i + _BASE64_LINE_WIDTH] for i in range(0, len(encoded), _BASE64_LINE_WIDTH))
    marker = f"{_HEREDOC_MARKER_PREFIX}_{secrets.token_hex(8)}"
    while marker in command:
        marker = f"{_HEREDOC_MARKER_PREFIX}_{secrets.token_hex(8)}"
    return f"base64 -d << '{marker}' | {command}\n{body}\n{marker}"
