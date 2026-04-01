# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""File utilities for TRPC Agent framework.

This module provides utility functions for file operations.
"""

from __future__ import annotations

import glob
import mimetypes
import os
import shutil
from pathlib import Path
from typing import Optional

try:
    import magic
    HAS_MAGIC = True
except ImportError:
    HAS_MAGIC = False


def path_join(base: str, path: str) -> str:
    """Join a base path and a path.

    Args:
        base: Base path
        path: Path

    Returns:
        The joined path.
    """
    return os.path.join(base, os.path.normpath(path))


def copy_dir(src: Path, dst: Path) -> None:
    """Recursively copy a directory tree from src to dst.

    This function replicates the Go copyDir behavior:
    - Creates destination directory if it doesn't exist
    - Walks through source directory tree
    - Copies files preserving permissions
    - Creates subdirectories as needed

    Args:
        src: Source directory path
        dst: Destination directory path

    Raises:
        OSError: If directory creation or file operations fail
    """
    # Use shutil.copytree for efficient directory copying
    # dirs_exist_ok=True allows copying into existing directory
    shutil.copytree(src, dst, dirs_exist_ok=True, symlinks=False)


def make_tree_read_only(root: Path) -> None:
    """Remove write bits from the entire directory tree.

    This function replicates the Go makeTreeReadOnly behavior:
    - Walks through the directory tree
    - Removes write permissions (owner/group/other) from all files and directories
    - Preserves read and execute permissions

    Args:
        root: Root directory path to make read-only

    Raises:
        OSError: If permission changes fail
    """
    root_path = Path(root)

    # Walk through all files and directories
    for item in root_path.rglob('*'):
        try:
            # Get current permissions
            current_mode = item.stat().st_mode
            # Clear write bits (0o222 = owner/group/other write)
            new_mode = current_mode & ~0o222
            item.chmod(new_mode)
        except OSError:
            pass  # Continue on error

    # Process the root directory itself
    try:
        current_mode = root_path.stat().st_mode
        new_mode = current_mode & ~0o222
        root_path.chmod(new_mode)
    except OSError:
        pass


def copy_path(src: str, dst: str) -> None:
    """Copy a file or directory from src to dst.

    Args:
        src: Source path (file or directory)
        dst: Destination path

    Raises:
        OSError: If copy operations fail
    """
    src_path = Path(src)
    dst_path = Path(dst)

    if src_path.is_dir():
        # Source is a directory
        copy_dir(src_path, dst_path)
    else:
        # Source is a file
        # Ensure destination directory exists
        dst_path.parent.mkdir(parents=True, mode=0o755, exist_ok=True)

        # Read and write file using Path
        data = src_path.read_bytes()
        dst_path.write_bytes(data)

        # Preserve file permissions
        dst_path.chmod(src_path.stat().st_mode)


def make_symlink(root: str, dst: str, target: str) -> None:
    """Create a symbolic link in the workspace.

    Args:
        root: Workspace root directory
        dst: Destination path for the symlink
        target: Target path for the symlink (absolute path)

    Raises:
        OSError: If symlink creation fails
    """
    dst: Path = Path(path_join(root, dst))

    # Ensure parent directory exists
    dst.parent.mkdir(parents=True, mode=0o755, exist_ok=True)

    # Remove existing path if present
    if dst.exists() or dst.is_symlink():
        if dst.is_dir() and not dst.is_symlink():
            shutil.rmtree(dst.as_posix())
        else:
            dst.unlink()

    # Create symlink
    dst.symlink_to(target)


def collect_files_with_glob(ws_path: str, glob_pattern: str) -> list[str]:
    """
    Collect files matching a glob pattern within a workspace.

    This function exactly mimics the Go code behavior:
    ```go
    abs := filepath.Join(ws.Path, g)
    pattern := strings.TrimPrefix(abs, "/")
    matches, err := ds.Glob(os.DirFS("/"), pattern)
    ```

    Args:
        ws_path: Workspace root path (e.g., "/tmp/workspace")
        glob_pattern: Glob pattern relative to workspace (e.g., "out/*.txt", "**/*.py")

    Returns:
        List of matched file paths (absolute paths), sorted alphabetically

    Raises:
        Exception: If glob matching fails

    Examples:
        >>> # Example 1: Simple glob pattern
        >>> ws_path = "/tmp/workspace"
        >>> pattern = "out/*.txt"
        >>> matches = collect_files_by_glob(ws_path, pattern)
        >>> # Returns: ['/tmp/workspace/out/file1.txt', '/tmp/workspace/out/file2.txt']

        >>> # Example 2: Doublestar pattern
        >>> ws_path = "/home/user/project"
        >>> pattern = "**/*.py"
        >>> matches = collect_files_by_glob(ws_path, pattern)
        >>> # Returns: ['/home/user/project/src/main.py', '/home/user/project/tests/test.py']

        >>> # Example 3: Nested directory pattern
        >>> ws_path = "/var/data"
        >>> pattern = "logs/**/error.log"
        >>> matches = collect_files_by_glob(ws_path, pattern)
        >>> # Returns: ['/var/data/logs/2024/01/error.log', '/var/data/logs/2024/02/error.log']
    """
    # Step 1: Join workspace path with glob pattern (equivalent to filepath.Join)
    # Using Path for cross-platform compatibility
    abs_path = str(Path(ws_path) / glob_pattern)

    # Step 2: Remove leading "/" if present (equivalent to strings.TrimPrefix)
    pattern = abs_path.lstrip("/")

    # Step 3: Perform glob matching from root "/" (equivalent to ds.Glob(os.DirFS("/"), pattern))
    # Prepend "/" back to search from root
    search_pattern = "/" + pattern

    try:
        # Use glob with recursive=True for doublestar (**) support
        matches = glob.glob(search_pattern, recursive=True)

        # Filter out directories, keep only files (matching Go's behavior)
        file_matches = [m for m in matches if os.path.isfile(m)]

        return sorted(file_matches)  # Sort for consistent output
    except Exception as ex:  # pylint: disable=broad-except
        raise Exception(f"Glob matching failed for pattern '{search_pattern}': {ex}")


def detect_content_type(filename: Path, data: bytes) -> str:
    """Detect content type from filename and data.

    Args:
        filename: Path to the file
        data: Data of the file

    Returns:
        The content type of the file.
    """
    # try to guess from filename
    mime_type, _ = mimetypes.guess_type(str(filename))
    if mime_type:
        return mime_type

    # filename guess failed, use magic to guess
    if HAS_MAGIC:
        return magic.from_buffer(data, mime=True)

    # magic guess failed, use simple content-based detection
    if data.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'image/png'
    if data.startswith(b'\xff\xd8\xff'):
        return 'image/jpeg'
    if data.startswith(b'%PDF'):
        return 'application/pdf'
    if data.startswith(b'PK'):
        return 'application/zip'
    if data.startswith(b'<!DOCTYPE html') or data.startswith(b'<html'):
        return 'text/html'
    if data.startswith(b'<?xml'):
        return 'text/xml'
    # check if it is text
    try:
        data.decode('utf-8')
        return 'text/plain; charset=utf-8'
    except UnicodeDecodeError:
        pass

    # default to application/octet-stream
    return 'application/octet-stream'


def get_rel_path(base: Path | str, path: Path | str) -> Optional[Path]:
    """Get the relative path from base to path.

    Args:
        base: Base path
        path: Path

    Returns:
        The relative path.
    """
    if isinstance(base, str):
        base = Path(base)
    if isinstance(path, str):
        path = Path(path)
    try:
        return Path(path).relative_to(Path(base))
    except ValueError:
        return None
