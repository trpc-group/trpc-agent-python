"""Unified diff parser — extracts structured file changes from git diffs."""

import re

from .types import DiffFile, DiffHunk

_HUNK_HEADER_RE = re.compile(
    r'^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$'
)
_FILENAME_RE = re.compile(r'^\+\+\+ b/(.*)$')
_QUOTED_FILENAME_RE = re.compile(r'^\+\+\+ "b/(.*)"$')
_OLD_FILENAME_RE = re.compile(r'^--- a/(.*)$')
_OLD_QUOTED_FILENAME_RE = re.compile(r'^--- "a/(.*)"$')
_DEVNULL_RE = re.compile(r'^\+\+\+ /dev/null')
_NEW_FILE_RE = re.compile(r'^new file mode')
_DELETED_FILE_RE = re.compile(r'^deleted file mode')
_BINARY_RE = re.compile(r'^Binary files')
_GIT_DIFF_RE = re.compile(r'^diff --git')


def parse_diff(diff_text: str) -> list[DiffFile]:
    """Parse a unified diff into structured DiffFile objects.

    Args:
        diff_text: Raw unified diff text.

    Returns:
        List of DiffFile objects, one per changed file.
    """
    if not diff_text or not diff_text.strip():
        return []

    lines = diff_text.split('\n')
    files: list[DiffFile] = []
    current_file: DiffFile | None = None
    current_hunk: DiffHunk | None = None

    # Accumulate metadata flags between diff headers
    pending_meta = {"is_new": False, "is_deleted": False, "is_binary": False,
                    "_old_filename": ""}

    for line in lines:
        # Collect metadata before we have a current_file
        if not current_file:
            if _NEW_FILE_RE.match(line):
                pending_meta["is_new"] = True
                continue
            if _DELETED_FILE_RE.match(line):
                pending_meta["is_deleted"] = True
                continue
            if _BINARY_RE.match(line):
                pending_meta["is_binary"] = True
                # For binary diffs without a +++ line, still create a file
                continue

        # New file header (+++ b/filename or +++ "b/filename")
        m = _FILENAME_RE.match(line)
        if not m:
            m = _QUOTED_FILENAME_RE.match(line)
        if m:
            if current_file:
                _finalize_hunk(current_file, current_hunk)
                files.append(current_file)
            current_file = DiffFile(filename=m.group(1))
            # Apply pending metadata
            current_file.is_new = pending_meta["is_new"]
            current_file.is_deleted = pending_meta["is_deleted"]
            current_file.is_binary = pending_meta["is_binary"]
            pending_meta = {"is_new": False, "is_deleted": False, "is_binary": False,
                            "_old_filename": ""}
            current_hunk = None
            continue

        # /dev/null (deleted file after +++ line)
        if _DEVNULL_RE.match(line):
            if current_file:
                current_file.is_deleted = True
            else:
                pending_meta["is_deleted"] = True
            continue

        # Old file header (--- a/filename or --- "a/filename")
        m = _OLD_FILENAME_RE.match(line)
        if not m:
            m = _OLD_QUOTED_FILENAME_RE.match(line)
        if m:
            if current_file:
                current_file.old_filename = m.group(1)
            else:
                # Store old filename for deleted file handling
                pending_meta["_old_filename"] = m.group(1)
            continue

        # Metadata lines that appear after current_file is set
        if current_file:
            if _NEW_FILE_RE.match(line):
                current_file.is_new = True
                continue
            if _DELETED_FILE_RE.match(line):
                current_file.is_deleted = True
                continue
            if _BINARY_RE.match(line):
                current_file.is_binary = True
                continue

        if not current_file:
            # Handle binary-only diffs and deleted-file diffs
            if pending_meta["is_binary"]:
                current_file = DiffFile(
                    filename="unknown",
                    is_binary=True,
                    is_new=pending_meta["is_new"],
                    is_deleted=pending_meta["is_deleted"],
                )
                pending_meta = {"is_new": False, "is_deleted": False, "is_binary": False}
                continue
            # Handle deleted file (+++ /dev/null) — try to get name from --- line
            if pending_meta.get("_old_filename"):
                current_file = DiffFile(
                    filename=pending_meta["_old_filename"],
                    is_deleted=True,
                )
                pending_meta = {"is_new": False, "is_deleted": False, "is_binary": False}
                continue
            continue

        # Hunk header
        m = _HUNK_HEADER_RE.match(line)
        if m and current_file:
            _finalize_hunk(current_file, current_hunk)
            current_hunk = DiffHunk(
                header=line,
                old_start=int(m.group(1)),
                old_count=int(m.group(2)) if m.group(2) else 1,
                new_start=int(m.group(3)),
                new_count=int(m.group(4)) if m.group(4) else 1,
            )
            continue

        # Content lines within a hunk
        if current_file and current_hunk is not None:
            current_hunk.lines.append(line)
            current_file.raw_lines.append(line)

    # Finalize last file
    if current_file:
        _finalize_hunk(current_file, current_hunk)
        files.append(current_file)

    return files


def _finalize_hunk(diff_file: DiffFile, hunk: DiffHunk | None) -> None:
    """Add completed hunk to file's hunk list."""
    if hunk is not None and hunk.lines:
        diff_file.hunks.append(hunk)


def get_changed_lines(diff_file: DiffFile) -> list[tuple[int, str]]:
    """Extract all added/modified lines with their new line numbers.

    Returns:
        List of (line_number, line_content) tuples for added lines.
    """
    result: list[tuple[int, str]] = []
    for hunk in diff_file.hunks:
        new_lineno = hunk.new_start
        for line in hunk.lines:
            if line.startswith('+') and not line.startswith('+++'):
                result.append((new_lineno, line[1:]))
                new_lineno += 1
            elif not line.startswith('-'):
                new_lineno += 1
    return result


def summarize_diff(files: list[DiffFile]) -> str:
    """Generate a human-readable summary of the diff."""
    if not files:
        return "No changes detected."
    parts = [f"{len(files)} file(s) changed:"]
    for f in files:
        status = "A" if f.is_new else ("D" if f.is_deleted else "M")
        parts.append(f"  {status} {f.filename}")
    return "\n".join(parts)
