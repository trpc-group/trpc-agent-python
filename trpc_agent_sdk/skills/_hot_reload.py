# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Skill hot-reload helpers.

This module encapsulates filesystem event handling and dirty-directory queues
for skill repositories. Repository implementations can stay focused on indexing
while delegating watcher integration here.
"""

from __future__ import annotations

import importlib
import threading
from pathlib import Path
from typing import Callable

from trpc_agent_sdk.log import logger


class SkillHotReloadTracker:
    """Track changed skill directories and optional watchdog observer state."""

    def __init__(self, skill_file_name: str):
        self._skill_file_name = skill_file_name
        self._watchdog_init_attempted = False
        self._watchdog_observer: object | None = None
        self._changed_dirs_by_root: dict[str, set[str]] = {}
        self._changed_dirs_lock = threading.Lock()

    def clear(self) -> None:
        """Clear queued directory changes."""
        with self._changed_dirs_lock:
            self._changed_dirs_by_root = {}

    def mark_changed_path(self, raw_path: str, is_directory: bool, skill_roots: list[str]) -> None:
        """Mark a changed path as dirty for the next incremental reload."""
        path = Path(raw_path)
        if not is_directory and path.name.lower() != self._skill_file_name.lower():
            return
        target_dir = path if is_directory else path.parent
        self.mark_changed_dir(target_dir, skill_roots)

    def mark_changed_dir(self, path: Path, skill_roots: list[str]) -> None:
        """Queue a changed directory; consumed by repository incremental scans."""
        root_key = self.resolve_root_key(path, skill_roots)
        if root_key is None:
            return
        with self._changed_dirs_lock:
            self._changed_dirs_by_root.setdefault(root_key, set()).add(str(path.resolve(strict=False)))

    def pop_changed_dirs(self, root_key: str) -> list[Path]:
        """Pop and return queued changed directories for a root."""
        with self._changed_dirs_lock:
            raw_dirs = self._changed_dirs_by_root.pop(root_key, set())
        return [Path(raw) for raw in sorted(raw_dirs)]

    def collect_changed_dirs(
        self,
        root_key: str,
        tracked_dirs: set[str],
        dir_mtime_ns: dict[str, int],
        mtime_reader: Callable[[Path], int],
    ) -> list[Path]:
        """Collect changed directories via event queue or mtime probing."""
        changed_dirs = self.pop_changed_dirs(root_key)
        if not changed_dirs:
            for dir_key in sorted(tracked_dirs):
                path = Path(dir_key)
                if not path.exists():
                    continue
                current_mtime = mtime_reader(path)
                if dir_mtime_ns.get(dir_key) != current_mtime:
                    changed_dirs.append(path)
                    dir_mtime_ns[dir_key] = current_mtime
        return self.normalize_scan_targets(changed_dirs)

    @staticmethod
    def resolve_root_key(path: Path, skill_roots: list[str]) -> str | None:
        """Find matching root key for a path."""
        resolved = path.resolve(strict=False)
        seen_roots: set[str] = set()
        for root in skill_roots:
            if not root:
                continue
            root_path = Path(root).resolve()
            root_key = str(root_path)
            if root_key in seen_roots:
                continue
            seen_roots.add(root_key)
            if resolved.is_relative_to(root_path):
                return root_key
        return None

    @staticmethod
    def normalize_scan_targets(changed_dirs: list[Path]) -> list[Path]:
        """Drop nested paths when their parent is already queued."""
        result: list[Path] = []
        for candidate in sorted(changed_dirs, key=lambda p: len(p.parts)):
            if any(candidate.is_relative_to(parent) for parent in result):
                continue
            result.append(candidate)
        return result

    def start_watcher_if_possible(self, skill_roots: list[str]) -> None:
        """Start filesystem watcher for near real-time hot reload if available."""
        if self._watchdog_init_attempted:
            return
        self._watchdog_init_attempted = True
        try:
            events_module = importlib.import_module("watchdog.events")
            observers_module = importlib.import_module("watchdog.observers")
        except ImportError:
            logger.debug("watchdog is unavailable; skill hot reload falls back to mtime probing")
            return
        except Exception as ex:  # pylint: disable=broad-except
            logger.warning("Failed to initialize watchdog imports: %s", ex)
            return

        file_system_event_handler_cls = getattr(events_module, "FileSystemEventHandler", None)
        observer_cls = getattr(observers_module, "Observer", None)
        if file_system_event_handler_cls is None or observer_cls is None:
            logger.warning("watchdog is installed but required symbols are missing")
            return

        tracker = self

        class _SkillDirChangeHandler(file_system_event_handler_cls):  # type: ignore[misc,valid-type]

            def on_any_event(self, event) -> None:  # type: ignore[no-untyped-def]
                src_path = getattr(event, "src_path", None)
                if src_path:
                    tracker.mark_changed_path(src_path, bool(event.is_directory), skill_roots)
                dest_path = getattr(event, "dest_path", None)
                if dest_path:
                    tracker.mark_changed_path(dest_path, bool(event.is_directory), skill_roots)

        observer = observer_cls()
        handler = _SkillDirChangeHandler()
        seen_roots: set[str] = set()
        for root in skill_roots:
            if not root:
                continue
            root_path = Path(root).resolve()
            root_key = str(root_path)
            if root_key in seen_roots:
                continue
            seen_roots.add(root_key)
            if not root_path.is_dir():
                continue
            try:
                observer.schedule(handler, path=root_key, recursive=True)
            except Exception as ex:  # pylint: disable=broad-except
                logger.warning("Failed to watch skill root %s: %s", root_key, ex)

        if not observer.emitters:
            logger.debug("No valid skill roots to watch; hot reload watcher not started")
            return
        observer.start()
        self._watchdog_observer = observer
        logger.debug("Skill hot reload watcher started with %d root(s)", len(observer.emitters))
