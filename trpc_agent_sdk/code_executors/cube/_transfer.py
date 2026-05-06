# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tar-based directory transfer protocol for the Cube package.

Self-contained protocol layered on :class:`CubeSandboxClient`'s public
primitives (:meth:`commands_run`, :meth:`read_file_bytes`,
:meth:`write_file_bytes`). Used by :meth:`CubeSandboxClient.upload_path`
/ :meth:`download_path` to round-trip whole directory trees while
preserving symlinks, permissions, and special files (mirrors
:class:`ContainerWorkspaceFS` semantics).

Kept separate from :mod:`._sandbox` so the client itself stays focused
on lifecycle/command/file primitives, and so this protocol can be unit
tested against a fake :class:`CubeSandboxClient`-shaped object that only
exposes ``commands_run`` / ``read_file_bytes`` / ``write_file_bytes``.

This module deliberately does **not** import e2b — all vendor quirks
(``CommandExitException`` absorption, ``user=`` plumbing, idle-timeout
renewal) are absorbed inside :class:`CubeSandboxClient`. Any change to
those quirks happens in exactly one place.
"""

from __future__ import annotations

import io
import posixpath
import secrets
import shutil
import tarfile
from pathlib import Path
from typing import TYPE_CHECKING
from typing import Literal

from ._paths import shell_quote

if TYPE_CHECKING:
    # Quoted forward reference to break the runtime import cycle:
    # `_sandbox.py` imports the transfer functions, and the transfer
    # functions in turn want the `CubeSandboxClient` *type* (for
    # type-checkers / IDEs) but only its *duck-typed* surface at runtime.
    from ._sandbox import CubeSandboxClient

# Collision-handling mode for ``download_path`` when the local
# destination already exists. See :func:`reserve_local_destination`.
#
# - ``"error"``  — refuse to clobber (default). Non-empty dir / existing
#                  file / existing symlink → :class:`FileExistsError`.
# - ``"replace"``— remove the existing destination before extracting.
#                  Directories are ``shutil.rmtree``'d (symlinks are
#                  ``unlink``'d first to avoid following the link).
# - ``"merge"``  — overlay onto an existing directory: leave siblings
#                  intact and let the tar payload write its own entries
#                  on top. Existing files/symlinks at the destination
#                  name are still unlinked (you cannot merge into a
#                  regular file).
OnExisting = Literal["error", "replace", "merge"]


def reserve_local_destination(
    local: Path,
    *,
    on_existing: OnExisting = "error",
) -> None:
    """Enforce ``download_path``'s collision policy on the local target.

    - Missing destinations and empty directories are accepted regardless
      of ``on_existing`` — there is no content to clobber.
    - ``"error"`` (default) raises :class:`FileExistsError` when the
      destination is a non-empty directory, a regular file, or any
      symlink (including broken symlinks — the name is taken).
    - ``"replace"`` removes the existing destination (``shutil.rmtree``
      for directories, ``unlink`` for files/symlinks) so the caller
      extracts into a clean slot.
    - ``"merge"`` leaves an existing non-empty directory in place so the
      tar payload overlays its entries; for file/symlink destinations
      it still unlinks because a regular file cannot be merged into.
    """
    # Missing path: nothing to reserve. ``is_symlink()`` handles the
    # broken-symlink case where ``exists()`` returns False but the name
    # is still taken.
    if not local.exists() and not local.is_symlink():
        return

    is_real_dir = local.is_dir() and not local.is_symlink()
    if is_real_dir:
        try:
            next(local.iterdir())
        except StopIteration:
            return
        if on_existing == "error":
            raise FileExistsError(f"download destination is non-empty "
                                  f"(pass on_existing='replace' or 'merge' to resolve): {local}")
        if on_existing == "replace":
            shutil.rmtree(local)
        # "merge": leave the directory in place; tar.extractall overlays.
        return

    # File or symlink (regular file, symlink-to-file, symlink-to-dir,
    # broken symlink). A regular file cannot be "merged" into — merge
    # falls back to replace for non-dir destinations.
    if on_existing == "error":
        raise FileExistsError(f"download destination already exists "
                              f"(pass on_existing='replace' to overwrite): {local}")
    local.unlink()


async def upload_directory_via_tar(
    client: "CubeSandboxClient",
    local_dir: Path,
    remote_abs: str,
) -> None:
    """Upload an entire host directory to ``remote_abs`` via tar.

    The whole tree (symlinks, permissions, special files) is preserved
    in a single round-trip. Requires ``tar`` in the sandbox image (true
    for any standard unix template).
    """
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        tar.add(str(local_dir), arcname=".")
    payload = buf.getvalue()

    token = secrets.token_hex(8)
    temp_remote = f"/tmp/.cube_upload_{token}.tar"
    normalized = posixpath.normpath(remote_abs)
    try:
        await client.write_file_bytes(temp_remote, payload)
        extract_cmd = (f"set -e; mkdir -p {shell_quote(normalized)}; "
                       f"tar -xf {shell_quote(temp_remote)} -C {shell_quote(normalized)}")
        await _run_protocol_step(client, extract_cmd, op="upload tar extract")
    finally:
        await _run_protocol_step(
            client,
            f"rm -f {shell_quote(temp_remote)}",
            op="upload tar cleanup",
            swallow=True,
        )


async def download_directory_via_tar(
    client: "CubeSandboxClient",
    remote_dir: str,
    local: Path,
) -> None:
    """Download an entire remote directory tree to ``local`` via tar.

    Round-trip pair of :func:`upload_directory_via_tar`; symlinks,
    permissions, and special files are preserved.
    """
    token = secrets.token_hex(8)
    temp_remote = f"/tmp/.cube_download_{token}.tar"
    try:
        create_cmd = f"tar -cf {shell_quote(temp_remote)} -C {shell_quote(remote_dir)} ."
        await _run_protocol_step(client, create_cmd, op="download tar create")
        payload = await client.read_file_bytes(temp_remote)
    finally:
        await _run_protocol_step(
            client,
            f"rm -f {shell_quote(temp_remote)}",
            op="download tar cleanup",
            swallow=True,
        )

    if local.exists() and not local.is_dir():
        local.unlink()
    local.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r") as tar:
        try:
            tar.extractall(local, filter="data")  # type: ignore[arg-type]  # py>=3.12
        except TypeError:
            tar.extractall(local)  # noqa: S202 — py3.10/3.11 fallback


async def _run_protocol_step(
    client: "CubeSandboxClient",
    command: str,
    *,
    op: str,
    swallow: bool = False,
) -> None:
    """Run a transfer-protocol shell step (mkdir/tar/rm) and surface failures.

    Goes through the client's :meth:`commands_run` so all e2b vendor
    quirks (``CommandExitException`` absorption, ``user=`` plumbing,
    idle-timeout renewal) are handled in exactly one place. Distinct
    from :meth:`CubeSandboxClient.commands_run` only in that we *raise*
    on non-zero exit instead of returning the structured result —
    these commands are invariants of the transfer contract, so a failed
    ``mkdir``/``tar`` means the transfer didn't happen and the caller
    can't sensibly continue. ``swallow=True`` is reserved for
    best-effort cleanup steps (rm-on-finally).
    """
    result = await client.commands_run(command)
    if result.exit_code != 0 and not swallow:
        raise RuntimeError(f"cube {op} failed (exit={result.exit_code}): {result.stderr}")
