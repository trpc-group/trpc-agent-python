# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under the Apache License, Version 2.0.
"""Prompt snapshots and isolated path-backed ``TargetPrompt`` instances."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path

from trpc_agent_sdk.evaluation import TargetPrompt

from .config import PromptFieldConfig
from .schemas import PromptSnapshot


class PromptWorkspaceError(ValueError):
    """A prompt source cannot safely participate in an isolated run."""


class SourcePromptDriftError(RuntimeError):
    """One or more source prompts changed after the baseline snapshot."""


def resolve_inside_example_root(example_root: Path, relative_path: str, label: str) -> Path:
    """Resolve a configured path and reject traversal or symlink escape."""
    root = example_root.resolve()
    candidate = (root / relative_path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise PromptWorkspaceError(f"{label} escapes the example root: {relative_path}") from exc
    return candidate


def validate_prompt_sources(example_root: Path, prompts: list[PromptFieldConfig]) -> list[Path]:
    """Validate path-backed, UTF-8 prompt files and return resolved sources."""
    sources: list[Path] = []
    seen_paths: set[Path] = set()
    for prompt in prompts:
        source = resolve_inside_example_root(example_root, prompt.path, f"prompt {prompt.name!r}")
        raw_source = example_root.resolve() / prompt.path
        if raw_source.is_symlink():
            raise PromptWorkspaceError(f"prompt {prompt.name!r} must not be a symlink")
        if not source.is_file():
            raise PromptWorkspaceError(f"prompt {prompt.name!r} is not a regular file: {prompt.path}")
        try:
            source.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise PromptWorkspaceError(f"prompt {prompt.name!r} is not UTF-8: {prompt.path}") from exc
        if source in seen_paths:
            raise PromptWorkspaceError(f"multiple prompt fields reference {prompt.path}")
        seen_paths.add(source)
        sources.append(source)
    return sources


def verify_source_hashes(snapshots: list[PromptSnapshot]) -> None:
    """Fail if a source prompt no longer matches its preparation snapshot.

    Later writeback code must call this immediately before an ACCEPT write.  It
    is useful in stage one as a read-only concurrency guard; this module does
    not expose a source-writing operation.
    """
    drifted: list[str] = []
    for snapshot in snapshots:
        source = Path(snapshot.source_path)
        try:
            content = source.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            drifted.append(snapshot.field_name)
            continue
        digest = sha256(content.encode("utf-8")).hexdigest()
        if digest != snapshot.sha256:
            drifted.append(snapshot.field_name)
    if drifted:
        raise SourcePromptDriftError(f"source prompt hash changed for fields: {sorted(drifted)}")


def stage_prompt_workspace(
    *,
    example_root: Path,
    staging_run_dir: Path,
    final_run_dir: Path,
    prompts: list[PromptFieldConfig],
    sources: list[Path],
) -> tuple[list[PromptSnapshot], TargetPrompt, TargetPrompt]:
    """Copy prompt sources into a staging run and build source/working targets.

    The returned working target intentionally points at *final* paths.  The
    caller atomically renames ``staging_run_dir`` into ``final_run_dir`` only
    once every source has been copied, so no later phase can observe a partial
    prompt workspace.
    """
    prompts_dir = staging_run_dir / "workspace" / "prompts"
    prompts_dir.mkdir(parents=True)

    source_target = TargetPrompt()
    working_target = TargetPrompt()
    snapshots: list[PromptSnapshot] = []

    for index, (prompt, source) in enumerate(zip(prompts, sources, strict=True), start=1):
        content = source.read_text(encoding="utf-8")
        suffix = source.suffix or ".txt"
        working_name = f"{index:02d}_{prompt.name}{suffix}"
        staged_path = prompts_dir / working_name
        final_path = final_run_dir / "workspace" / "prompts" / working_name
        staged_path.write_text(content, encoding="utf-8")

        source_target.add_path(prompt.name, str(source))
        working_target.add_path(prompt.name, str(final_path))
        snapshots.append(
            PromptSnapshot(
                field_name=prompt.name,
                source_path=str(source),
                working_path=str(final_path),
                content=content,
                sha256=sha256(content.encode("utf-8")).hexdigest(),
            ))

    return snapshots, source_target, working_target
