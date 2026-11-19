# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Shared-memory run table for multi-worker cancel and steering.

One host-wide segment stores a fixed number of run entries. A master process
creates it with :func:`init_shared_mem`; forked workers inherit the same
mapping. Entries carry process ownership, cancellation state, and a bounded
steering queue.
"""

from __future__ import annotations

import fcntl
import gc
import mmap
import os
import struct
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import List
from typing import Optional

from trpc_agent_sdk.log import logger
from trpc_agent_sdk.types import Content

from ._types import SharedMemoryConfig
from ._types import SteeringStatus

SEGMENT_DIR = "/dev/shm/trpc-agent-py"
SEGMENT_PATH = os.path.join(SEGMENT_DIR, "cancel")
LOCK_PATH = SEGMENT_PATH + ".lock"

_MAGIC = b"TRPCAGNT"
_HEADER_STRUCT = struct.Struct("<8sIQ")
_HEADER_SIZE = 64
_ENTRY_META_STRUCT = struct.Struct("<B7xQI16sB3xI20x")
_ENTRY_META_SIZE = 64
_RECORD_HEADER_SIZE = 4
_MASTER_LOCK_TIMEOUT = 2.0
_MASTER_LOCK_RETRY = 0.05

_STATE_FREE = 0
_STATE_RUNNING = 1

_CANCEL_OFFSET = 36
_COUNT_OFFSET = 40

_U32 = struct.Struct("<I")


@dataclass(frozen=True)
class _Slot:
    """Identity of one entry generation in the shared table."""

    entry_index: int
    generation: int
    digest: bytes


def entry_size_for(config: SharedMemoryConfig) -> int:
    """Return the aligned size of one entry."""
    raw = _ENTRY_META_SIZE + config.steering_queue_size * (_RECORD_HEADER_SIZE + config.max_steering_bytes)
    return (raw + 63) // 64 * 64


def segment_size_for(config: SharedMemoryConfig) -> int:
    """Return the total segment size for ``config``."""
    return _HEADER_SIZE + config.max_active_runs * entry_size_for(config)


def record_offset(record_index: int, max_steering_bytes: int) -> int:
    """Return the offset of one steering record inside an entry."""
    return _ENTRY_META_SIZE + record_index * (_RECORD_HEADER_SIZE + max_steering_bytes)


def key_digest(key) -> bytes:
    """Return the 128-bit digest used by the shared entry table."""
    from hashlib import blake2b

    payload = f"{key.app_name}\0{key.user_id}\0{key.session_id}".encode("utf-8")
    return blake2b(payload, digest_size=16).digest()


def pid_is_alive(pid: int) -> bool:
    """Return whether ``pid`` still exists."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class _SharedMem:
    """One process's mapping of the shared memory segment."""

    def __init__(
        self,
        *,
        fd: int,
        mm: mmap.mmap,
        view: memoryview,
        config: SharedMemoryConfig,
        path: str,
        owner_pid: int,
        master_lock_fd: Optional[int] = None,
    ) -> None:
        self._fd = fd
        self._mm = mm
        self._view = view
        self._config = config
        self._path = path
        self._owner_pid = owner_pid
        self._master_lock_fd = master_lock_fd
        self._pid = os.getpid()
        self._alloc_lock = threading.Lock()
        self._entry_locks = [threading.Lock() for _ in range(config.max_active_runs)]
        self._closed = False

    @classmethod
    def create(cls, config: SharedMemoryConfig) -> "_SharedMem":
        """Create a fresh segment, acquiring the host-wide master lock."""
        os.makedirs(SEGMENT_DIR, mode=0o700, exist_ok=True)
        lock_fd = os.open(LOCK_PATH, os.O_CREAT | os.O_RDWR, 0o600)
        deadline = time.monotonic() + _MASTER_LOCK_TIMEOUT
        acquired = False
        while True:
            try:
                fcntl.lockf(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB, 1, 0)
                acquired = True
                break
            except OSError:
                if time.monotonic() >= deadline:
                    break
                time.sleep(_MASTER_LOCK_RETRY)

        if not acquired:
            os.close(lock_fd)
            raise RuntimeError(f"Another process is already master for {SEGMENT_PATH}")

        segment_fd: Optional[int] = None
        mm: Optional[mmap.mmap] = None
        view: Optional[memoryview] = None
        try:
            try:
                os.unlink(SEGMENT_PATH)
            except FileNotFoundError:
                pass
            segment_fd = os.open(SEGMENT_PATH, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
            os.ftruncate(segment_fd, segment_size_for(config))
            mm = mmap.mmap(segment_fd, 0)
            view = memoryview(mm)
            _HEADER_STRUCT.pack_into(
                view,
                0,
                _MAGIC,
                os.getpid(),
                int(time.time() * 1000),
            )
        except Exception:
            if view is not None:
                view.release()
            if mm is not None:
                mm.close()
            if segment_fd is not None:
                try:
                    os.close(segment_fd)
                except OSError:
                    pass
            try:
                os.unlink(SEGMENT_PATH)
            except OSError:
                pass
            fcntl.lockf(lock_fd, fcntl.LOCK_UN, 1, 0)
            os.close(lock_fd)
            raise

        return cls(
            fd=segment_fd,
            mm=mm,
            view=view,
            config=config,
            path=SEGMENT_PATH,
            owner_pid=os.getpid(),
            master_lock_fd=lock_fd,
        )

    @property
    def config(self) -> SharedMemoryConfig:
        return self._config

    @property
    def owner_pid(self) -> int:
        return self._owner_pid

    @property
    def max_active_runs(self) -> int:
        return self._config.max_active_runs

    @property
    def steering_queue_size(self) -> int:
        return self._config.steering_queue_size

    @property
    def max_steering_bytes(self) -> int:
        return self._config.max_steering_bytes

    @property
    def entry_size(self) -> int:
        return entry_size_for(self._config)

    @property
    def closed(self) -> bool:
        return self._closed

    def _ensure_process_state(self) -> None:
        if os.getpid() == self._pid:
            return
        # A fork child inherits the mapping and fds. Recreate only the
        # process-local locks: they may have been held by another thread at
        # fork time, while the shared mapping itself stays valid.
        self._pid = os.getpid()
        self._alloc_lock = threading.Lock()
        self._entry_locks = [threading.Lock() for _ in range(self.max_active_runs)]

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("Shared memory is closed")

    def entry_view(self, index: int) -> memoryview:
        """Return the memoryview of one entry."""
        if index < 0 or index >= self.max_active_runs:
            raise IndexError(f"entry index {index} out of range")
        offset = _HEADER_SIZE + index * self.entry_size
        return self._view[offset:offset + self.entry_size]

    @contextmanager
    def lock_alloc(self):
        """Serialize entry allocation across processes and threads."""
        self._ensure_process_state()
        with self._alloc_lock:
            fcntl.lockf(self._fd, fcntl.LOCK_EX, 1, 0)
            try:
                yield
            finally:
                fcntl.lockf(self._fd, fcntl.LOCK_UN, 1, 0)

    @contextmanager
    def lock_entry(self, index: int):
        """Serialize one entry across processes and threads."""
        self._ensure_process_state()
        with self._entry_locks[index]:
            offset = _HEADER_SIZE + index * self.entry_size
            fcntl.lockf(self._fd, fcntl.LOCK_EX, 1, offset)
            try:
                yield
            finally:
                fcntl.lockf(self._fd, fcntl.LOCK_UN, 1, offset)

    def allocate(self, key) -> _Slot:
        """Reserve one entry for ``key``."""
        self._ensure_process_state()
        self._ensure_open()
        digest = key_digest(key)

        with self.lock_alloc():
            index, generation = self._find_or_reserve(digest, key)
            with self.lock_entry(index):
                view = self.entry_view(index)
                state, current_generation, owner_pid, entry_digest, _cancel, _count = _ENTRY_META_STRUCT.unpack_from(
                    view, 0)
                if state == _STATE_RUNNING and entry_digest == digest and pid_is_alive(owner_pid):
                    raise RuntimeError(f"Session {key.session_id} already has an active run")
                generation = max(generation, current_generation) + 1
                self._write_running(view, generation, digest)
        return _Slot(index, generation, digest)

    def _find_or_reserve(self, digest: bytes, key) -> tuple[int, int]:
        capacity = self.max_active_runs
        start = int.from_bytes(digest, "little") % capacity
        first_free: Optional[tuple[int, int]] = None

        for offset in range(capacity):
            index = (start + offset) % capacity
            view = self.entry_view(index)
            state, generation, owner_pid, entry_digest, _cancel, _count = _ENTRY_META_STRUCT.unpack_from(view, 0)
            if entry_digest == digest:
                if state != _STATE_RUNNING or not pid_is_alive(owner_pid):
                    return index, generation
                raise RuntimeError(f"Session {key.session_id} already has an active run")
            if state != _STATE_RUNNING:
                if first_free is None:
                    first_free = (index, generation)
            elif not pid_is_alive(owner_pid) and first_free is None:
                first_free = (index, generation)

        if first_free is None:
            raise RuntimeError(f"Shared memory run table is full (max_active_runs={capacity})")
        return first_free

    @staticmethod
    def _write_running(view: memoryview, generation: int, digest: bytes) -> None:
        _ENTRY_META_STRUCT.pack_into(
            view,
            0,
            _STATE_RUNNING,
            generation,
            os.getpid(),
            digest,
            0,
            0,
        )

    @staticmethod
    def _write_free(view: memoryview) -> None:
        state, generation, _owner_pid, _digest, _cancel, _count = _ENTRY_META_STRUCT.unpack_from(view, 0)
        _ENTRY_META_STRUCT.pack_into(
            view,
            0,
            _STATE_FREE,
            generation,
            0,
            b"\x00" * 16,
            0,
            0,
        )

    @staticmethod
    def _matches(state: int, generation: int, digest: bytes, slot: _Slot) -> bool:
        return state == _STATE_RUNNING and generation == slot.generation and digest == slot.digest

    def release(self, slot: _Slot) -> None:
        """Free ``slot`` when it still belongs to the same generation."""
        if self._closed:
            return
        try:
            with self.lock_entry(slot.entry_index):
                view = self.entry_view(slot.entry_index)
                state, generation, _owner_pid, digest, _cancel, _count = _ENTRY_META_STRUCT.unpack_from(view, 0)
                if self._matches(state, generation, digest, slot):
                    self._write_free(view)
        except (OSError, ValueError, IndexError, struct.error):
            pass

    def find(self, key) -> Optional[_Slot]:
        """Return the live slot for ``key``, if another worker owns it."""
        if self._closed:
            return None
        digest = key_digest(key)
        capacity = self.max_active_runs
        start = int.from_bytes(digest, "little") % capacity
        try:
            for offset in range(capacity):
                index = (start + offset) % capacity
                view = self.entry_view(index)
                state, generation, owner_pid, entry_digest, _cancel, _count = _ENTRY_META_STRUCT.unpack_from(view, 0)
                if state != _STATE_RUNNING or entry_digest != digest or not pid_is_alive(owner_pid):
                    continue
                with self.lock_entry(index):
                    view = self.entry_view(index)
                    state, generation, owner_pid, entry_digest, _cancel, _count = _ENTRY_META_STRUCT.unpack_from(
                        view, 0)
                    if state == _STATE_RUNNING and entry_digest == digest and pid_is_alive(owner_pid):
                        return _Slot(index, generation, digest)
        except (OSError, ValueError, IndexError, struct.error):
            return None
        return None

    def publish_cancel(self, slot: _Slot) -> bool:
        """Set the cancel flag of ``slot``."""
        if self._closed:
            return False
        try:
            with self.lock_entry(slot.entry_index):
                view = self.entry_view(slot.entry_index)
                state, generation, _owner_pid, digest, _cancel, _count = _ENTRY_META_STRUCT.unpack_from(view, 0)
                if not self._matches(state, generation, digest, slot):
                    return False
                struct.pack_into("<B", view, _CANCEL_OFFSET, 1)
                return True
        except (OSError, ValueError, IndexError, struct.error):
            return False

    def is_cancelled(self, slot: _Slot) -> bool:
        """Return whether ``slot`` was cancelled or no longer exists."""
        if self._closed:
            return True
        try:
            view = self.entry_view(slot.entry_index)
            state, generation, _owner_pid, digest, cancel, _count = _ENTRY_META_STRUCT.unpack_from(view, 0)
        except (ValueError, IndexError, struct.error):
            return True
        if not self._matches(state, generation, digest, slot):
            return True
        return bool(cancel)

    def enqueue(self, slot: _Slot, content: Content) -> SteeringStatus:
        """Append one steering message to ``slot``."""
        if self._closed:
            return SteeringStatus.NO_ACTIVE_RUN

        payload = content.model_dump_json(exclude_none=True).encode("utf-8")
        if len(payload) > self.max_steering_bytes:
            return SteeringStatus.MESSAGE_TOO_LARGE

        try:
            with self.lock_entry(slot.entry_index):
                view = self.entry_view(slot.entry_index)
                state, generation, _owner_pid, digest, _cancel, count = _ENTRY_META_STRUCT.unpack_from(view, 0)
                if not self._matches(state, generation, digest, slot):
                    return SteeringStatus.NO_ACTIVE_RUN

                queue_size = self.steering_queue_size
                if count >= queue_size:
                    return SteeringStatus.QUEUE_FULL

                record = record_offset(count, self.max_steering_bytes)
                _U32.pack_into(view, record, len(payload))
                view[record + _RECORD_HEADER_SIZE:record + _RECORD_HEADER_SIZE + len(payload)] = payload
                _U32.pack_into(view, _COUNT_OFFSET, count + 1)
                return SteeringStatus.ACCEPTED
        except (OSError, ValueError, IndexError, struct.error):
            return SteeringStatus.NO_ACTIVE_RUN

    def drain(self, slot: _Slot) -> List[Content]:
        """Take all steering messages from ``slot`` in FIFO order."""
        if self._closed:
            return []

        try:
            with self.lock_entry(slot.entry_index):
                view = self.entry_view(slot.entry_index)
                state, generation, _owner_pid, digest, _cancel, count = _ENTRY_META_STRUCT.unpack_from(view, 0)
                if not self._matches(state, generation, digest, slot):
                    return []

                queue_size = self.steering_queue_size
                if count > queue_size:
                    logger.warning(
                        "Corrupted steering queue for entry %s; resetting",
                        slot.entry_index,
                    )
                    _U32.pack_into(view, _COUNT_OFFSET, 0)
                    return []

                contents: List[Content] = []
                for index in range(count):
                    record = record_offset(index, self.max_steering_bytes)
                    try:
                        length = _U32.unpack_from(view, record)[0]
                        if length > self.max_steering_bytes:
                            raise ValueError(f"record length {length} exceeds {self.max_steering_bytes}")
                        payload = bytes(view[record + _RECORD_HEADER_SIZE:record + _RECORD_HEADER_SIZE + length])
                        contents.append(Content.model_validate_json(payload.decode("utf-8")))
                    except (ValueError, IndexError, struct.error, UnicodeDecodeError) as ex:
                        logger.warning(
                            "Dropping corrupted steering record for entry %s: %s",
                            slot.entry_index,
                            ex,
                        )
                        _U32.pack_into(view, _COUNT_OFFSET, 0)
                        return contents

                _U32.pack_into(view, _COUNT_OFFSET, 0)
                return contents
        except (OSError, ValueError):
            return []

    def close(self) -> None:
        """Close the mapping and release the master lock, if held."""
        if self._closed:
            return
        self._closed = True
        self._view.release()
        try:
            self._mm.close()
        except BufferError:
            # A caller may still hold an entry_view slice. Drop collectable
            # temporary references before retrying once.
            gc.collect()
            self._mm.close()
        os.close(self._fd)

        if self._master_lock_fd is not None:
            try:
                fcntl.lockf(self._master_lock_fd, fcntl.LOCK_UN, 1, 0)
            except OSError:
                pass
            try:
                os.close(self._master_lock_fd)
            except OSError:
                pass
            self._master_lock_fd = None


def init_shared_mem(config: Optional[SharedMemoryConfig] = None) -> _SharedMem:
    """Create the host-wide shared segment."""
    config = config or SharedMemoryConfig()
    return _SharedMem.create(config)


def destroy_shared_mem(mem: _SharedMem) -> None:
    """Destroy the master segment or detach a worker copy.

    Only the master unlinks the segment. Non-master processes close their
    inherited mapping and leave the segment alone.
    """
    if mem.owner_pid == os.getpid():
        try:
            os.unlink(SEGMENT_PATH)
        except FileNotFoundError:
            pass
    try:
        mem.close()
    except (OSError, ValueError):
        pass


__all__ = [
    "destroy_shared_mem",
    "init_shared_mem",
]
