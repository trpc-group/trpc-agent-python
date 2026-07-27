# Pipeline lock: fcntl.flock (POSIX) + PID lock (Windows) (R24)
"""Kernel-level mutual exclusion for eval-optimize pipeline.

POSIX: fcntl.flock(fd, LOCK_EX | LOCK_NB) — kernel auto-releases on process
death. No PID tracking, no staleness, no PID-reuse deadlock.
Windows: PID-based lock (fcntl unavailable). PID reuse on shared hosts is a
known limitation documented in acquire_pipeline_lock.
"""

import os as _os
import sys


def acquire_pipeline_lock(lock_path: str, pid: int | None = None,
                          started_at: str = "") -> int | None:
    """Acquire pipeline mutual-exclusion lock.

    Returns:
        Lock token on success (fd on POSIX, pid on Windows), or None if
        another instance holds the lock.
    """
    if pid is None:
        pid = _os.getpid()
    dirname = _os.path.dirname(lock_path)
    if dirname:
        _os.makedirs(dirname, exist_ok=True)

    if sys.platform != "win32":
        return _acquire_flock(lock_path, pid, started_at)
    return _acquire_pid_lock_win32(lock_path, pid, started_at)


def release_pipeline_lock(token: int | None, lock_path: str = "") -> None:
    """Release a previously acquired pipeline lock.

    Args:
        token: Lock token from acquire_pipeline_lock.
        lock_path: Path to the lock file (needed for POSIX cleanup).
    """
    if token is None:
        return
    if sys.platform != "win32":
        _release_flock(token, lock_path)
    else:
        _release_pid_lock_win32(token)


# ---- POSIX: fcntl.flock ----


def _acquire_flock(lock_path: str, pid: int, started_at: str) -> int | None:
    """Acquire kernel-level flock. Returns fd on success, None if locked."""
    import fcntl

    fd = _os.open(lock_path, _os.O_CREAT | _os.O_RDWR | _os.O_TRUNC, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (IOError, OSError):
        _os.close(fd)
        return None
    # Write PID + timestamp for diagnostic purposes only
    _os.write(fd, f"{pid} {started_at}\n".encode())
    _os.fsync(fd)
    return fd


def _release_flock(fd: int, lock_path: str = "") -> None:
    """Close fd to release kernel flock.

    The lock file is intentionally NOT removed.  Removing it after close
    creates a race: between close (flock released) and remove, another
    process can flock the same inode, then the remove deletes it, and a
    third process creates a new inode + independent flock at the same
    path ? breaking mutual exclusion (two concurrent "holders").
    Since flock is kernel-level, the file can safely persist as a marker.
    """
    _os.close(fd)


# ---- Windows: PID-based lock ----


def _acquire_pid_lock_win32(lock_path: str, pid: int,
                            started_at: str) -> int | None:
    """Acquire PID-based lock on Windows.

    NOTE: PID reuse on shared hosts can cause false "alive" detection,
    leading to permanent lock blockage. This is an inherent limitation
    of PID locks without kernel primitives (flock/mutex). For CI or
    shared-host deployments, prefer a dedicated lock directory.
    """
    # Try atomic create first
    try:
        fd = _os.open(lock_path, _os.O_CREAT | _os.O_EXCL | _os.O_WRONLY, 0o644)
        with _os.fdopen(fd, "w", encoding="utf-8") as lf:
            lf.write(f"{pid} {started_at}")
            lf.flush()
            _os.fsync(lf.fileno())
        return pid
    except FileExistsError:
        pass

    # Lock exists — check staleness
    try:
        with open(lock_path, "r", encoding="utf-8") as lf:
            raw = lf.read().strip()
            parts = raw.split()
            if not parts:
                raise ValueError("empty lock file")
            old_pid = int(parts[0])
    except (FileNotFoundError, ValueError, IndexError):
        _cleanup_lock_file(lock_path)
        # Retry once after cleanup instead of failing immediately.
        # Avoids blocking CI on a single corrupt lock file that was
        # just repaired.
        return _acquire_pid_lock_win32(lock_path, pid, started_at)

    if _pid_alive(old_pid):
        return None  # another instance is running

    # Stale lock — atomic takeover
    tmp = f"{lock_path}.{pid}.tmp"
    with open(tmp, "w", encoding="utf-8") as tf:
        tf.write(f"{pid} {started_at}")
        tf.flush()
        _os.fsync(tf.fileno())
    _os.replace(tmp, lock_path)

    # Verify ownership
    try:
        with open(lock_path, "r", encoding="utf-8") as vf:
            raw = vf.read().strip()
            parts = raw.split()
            if not parts:
                raise ValueError("empty lock file")
            owner = int(parts[0])
        if owner != pid:
            return None
    except (FileNotFoundError, ValueError, IndexError):
        _cleanup_lock_file(lock_path)
        # Retry once after cleanup instead of failing immediately.
        # Avoids blocking CI on a single corrupt lock file that was
        # just repaired.
        return _acquire_pid_lock_win32(lock_path, pid, started_at)

    return pid


def _release_pid_lock_win32(token: int) -> None:
    """Windows PID lock release is handled by the caller removing LOCK_FILE."""
    pass


def _cleanup_lock_file(lock_path: str) -> None:
    """Remove a corrupted/stale lock file."""
    try:
        _os.remove(lock_path)
    except FileNotFoundError:
        pass


def _pid_alive(pid: int) -> bool:
    """Check if a process is running.

    Windows: OpenProcess with PROCESS_QUERY_LIMITED_INFORMATION.
    Unix: os.kill(pid, 0) — POSIX null-signal liveness probe.
    """
    if sys.platform == "win32":
        try:
            import ctypes

            h = ctypes.windll.kernel32.OpenProcess(0x0400, False, pid)
            if h:
                ctypes.windll.kernel32.CloseHandle(h)
                return True
            return False
        except Exception:
            return False  # assume dead to allow cleanup

    try:
        _os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True
