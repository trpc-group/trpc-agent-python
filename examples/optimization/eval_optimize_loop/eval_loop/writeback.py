"""Best-effort prompt CAS with per-file atomic replacement.

Hash checks narrow concurrency windows but cannot make a multi-file operation
transactional. A non-cooperating writer can still race after the last check.
"""

from __future__ import annotations

import hashlib
import os
import sys
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from .schemas import WritebackResult


class ConcurrentPromptUpdateError(RuntimeError):
    """Raised when source prompts no longer match their captured snapshot."""


@dataclass(frozen=True)
class PromptFileSnapshot:
    """Byte-for-byte snapshot of one source prompt file."""

    name: str
    path: Path
    content: bytes
    sha256: str


@dataclass(frozen=True)
class PromptSnapshot:
    """Ordered bundle of source prompt snapshots keyed by prompt name."""

    files: dict[str, PromptFileSnapshot]

    def hashes(self) -> dict[str, str]:
        """Return a fresh name-to-hash mapping for this snapshot."""

        return {name: prompt_file.sha256 for name, prompt_file in self.files.items()}


def _hash_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def snapshot_prompt_files(paths: dict[str, str | Path]) -> PromptSnapshot:
    """Read source prompt files and capture their exact bytes and hashes."""

    files: dict[str, PromptFileSnapshot] = {}
    for name, raw_path in paths.items():
        path = Path(raw_path)
        content = path.read_bytes()
        files[name] = PromptFileSnapshot(
            name=name,
            path=path,
            content=content,
            sha256=_hash_bytes(content),
        )
    return PromptSnapshot(files=files)


def _current_hashes(snapshot: PromptSnapshot) -> dict[str, str]:
    return {name: _hash_bytes(prompt_file.path.read_bytes()) for name, prompt_file in snapshot.files.items()}


def _atomic_replace_bytes(
    path: Path,
    content: bytes,
    *,
    expected_sha256: str | None = None,
) -> None:
    """Replace one file atomically if its last observed hash is still expected."""

    fd, temp_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temp_path = Path(temp_name)
    try:
        temp_file = os.fdopen(fd, "wb")
        fd = -1
        with temp_file:
            temp_file.write(content)
            temp_file.flush()
            os.fsync(temp_file.fileno())
        if expected_sha256 is not None:
            current_sha256 = _hash_bytes(path.read_bytes())
            if current_sha256 != expected_sha256:
                raise ConcurrentPromptUpdateError(f"source prompt file changed before replace: {path}")
        os.replace(temp_path, path)
    finally:
        active_exception = sys.exc_info()[0] is not None
        cleanup_error: OSError | None = None
        if fd >= 0:
            try:
                os.close(fd)
            except OSError as error:
                cleanup_error = error
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass
        except OSError as error:
            if cleanup_error is None:
                cleanup_error = error
        if cleanup_error is not None and not active_exception:
            raise cleanup_error


def _restore_snapshot(
    snapshot: PromptSnapshot,
    written_hashes: dict[str, str],
) -> list[str]:
    """Conditionally restore only files this operation successfully replaced."""

    failures: list[str] = []
    for name, candidate_hash in written_hashes.items():
        prompt_file = snapshot.files[name]
        try:
            current_hash = _hash_bytes(prompt_file.path.read_bytes())
        except OSError as error:
            failures.append(f"{name}: restore precondition failed: {error}")
            continue
        if current_hash != candidate_hash:
            failures.append(f"{name}: restore conflict; current hash changed from candidate")
            continue
        try:
            _atomic_replace_bytes(
                prompt_file.path,
                prompt_file.content,
                expected_sha256=candidate_hash,
            )
        except (OSError, ConcurrentPromptUpdateError) as error:
            failures.append(f"{name}: restore failed: {error}")
    return failures


def _encode_prompt_bundle(
    snapshot: PromptSnapshot,
    prompts: dict[str, str],
) -> dict[str, bytes]:
    missing = [name for name in snapshot.files if name not in prompts]
    if missing:
        raise ValueError(f"missing prompts for snapshot files: {', '.join(missing)}")
    return {name: prompts[name].encode("utf-8") for name in snapshot.files}


def _restoration_error(snapshot: PromptSnapshot, failures: list[str]) -> str | None:
    details: list[str] = []
    if failures:
        details.append(f"restore failures: {'; '.join(failures)}")

    try:
        current_hashes = _current_hashes(snapshot)
    except OSError as error:
        details.append(f"restore verification failed: {error}")
    else:
        expected_hashes = snapshot.hashes()
        mismatched = [name for name, expected_hash in expected_hashes.items() if current_hashes[name] != expected_hash]
        if mismatched:
            details.append(f"restored hashes differ for: {', '.join(mismatched)}")

    if not details:
        return None
    return "; ".join(details)


@contextmanager
def temporary_prompt_bundle(
    snapshot: PromptSnapshot,
    prompts: dict[str, str],
) -> Iterator[None]:
    """Install candidates temporarily using best-effort CAS and conditional restore."""

    encoded_prompts = _encode_prompt_bundle(snapshot, prompts)
    expected_hashes = snapshot.hashes()
    current_hashes = _current_hashes(snapshot)
    if current_hashes != expected_hashes:
        changed = [name for name, expected_hash in expected_hashes.items() if current_hashes[name] != expected_hash]
        raise ConcurrentPromptUpdateError(f"source prompt files changed since snapshot: {', '.join(changed)}")

    candidate_hashes = {name: _hash_bytes(content) for name, content in encoded_prompts.items()}
    written_hashes: dict[str, str] = {}
    try:
        for name, prompt_file in snapshot.files.items():
            _atomic_replace_bytes(
                prompt_file.path,
                encoded_prompts[name],
                expected_sha256=expected_hashes[name],
            )
            written_hashes[name] = candidate_hashes[name]
        yield
    except BaseException as primary_error:
        failures = _restore_snapshot(snapshot, written_hashes)
        restoration_error = _restoration_error(snapshot, failures)
        if restoration_error is not None:
            diagnostic = f"failed to restore prompt snapshot: {restoration_error}"
            add_note = getattr(primary_error, "add_note", None)
            if add_note is not None:
                add_note(diagnostic)
            else:
                raise primary_error from RuntimeError(diagnostic)
        raise
    else:
        failures = _restore_snapshot(snapshot, written_hashes)
        restoration_error = _restoration_error(snapshot, failures)
        if restoration_error is not None:
            raise RuntimeError(f"failed to restore prompt snapshot: {restoration_error}")


def commit_prompt_bundle(
    snapshot: PromptSnapshot,
    prompts: dict[str, str],
) -> WritebackResult:
    """Apply a bundle with best-effort CAS and conditional compensating rollback."""

    before_hashes = _current_hashes(snapshot)
    expected_hashes = snapshot.hashes()
    if before_hashes != expected_hashes:
        changed = [name for name, expected_hash in expected_hashes.items() if before_hashes[name] != expected_hash]
        raise ConcurrentPromptUpdateError(f"source prompt files changed since snapshot: {', '.join(changed)}")

    encoded_prompts = _encode_prompt_bundle(snapshot, prompts)
    candidate_hashes = {name: _hash_bytes(content) for name, content in encoded_prompts.items()}
    written_hashes: dict[str, str] = {}
    try:
        for name, prompt_file in snapshot.files.items():
            _atomic_replace_bytes(
                prompt_file.path,
                encoded_prompts[name],
                expected_sha256=expected_hashes[name],
            )
            written_hashes[name] = candidate_hashes[name]
        applied_hashes = _current_hashes(snapshot)
        candidate_mismatches = [
            name for name, candidate_hash in candidate_hashes.items() if applied_hashes[name] != candidate_hash
        ]
        if candidate_mismatches:
            raise ConcurrentPromptUpdateError(
                "candidate prompt files changed before final verification: " f"{', '.join(candidate_mismatches)}"
            )
    except (OSError, ConcurrentPromptUpdateError) as error:
        if isinstance(error, ConcurrentPromptUpdateError) and not written_hashes:
            raise

        rollback_failures = _restore_snapshot(snapshot, written_hashes)
        try:
            after_hashes = _current_hashes(snapshot)
        except OSError as hash_error:
            after_hashes = {}
            rollback_failures.append(f"hash verification: {hash_error}")
        else:
            rollback_failures.extend(
                f"{name}: final hash differs from snapshot"
                for name, expected_hash in expected_hashes.items()
                if after_hashes[name] != expected_hash
            )

        error_message = f"prompt commit failed: {error}"
        if rollback_failures:
            error_message += f"; rollback failures: {'; '.join(rollback_failures)}"
        return WritebackResult(
            status="rolled_back" if after_hashes == expected_hashes else "rollback_failed",
            before_hashes=before_hashes,
            after_hashes=after_hashes,
            error=error_message,
        )

    return WritebackResult(
        status="applied",
        before_hashes=before_hashes,
        after_hashes=applied_hashes,
    )
