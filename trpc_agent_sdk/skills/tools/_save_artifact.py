# -*- coding: utf-8 -*-
#
# Copyright @ 2025 Tencent.com
"""Workspace artifact save tool.

This tool persists an existing file from the current workspace as an artifact.
"""

from __future__ import annotations

import mimetypes
import os
import posixpath
from typing import Any
from typing import Optional

from trpc_agent_sdk.code_executors import BaseWorkspaceRuntime
from trpc_agent_sdk.code_executors import DIR_OUT
from trpc_agent_sdk.code_executors import DIR_RUNS
from trpc_agent_sdk.code_executors import DIR_WORK
from trpc_agent_sdk.code_executors import WORKSPACE_ENV_DIR_KEY
from trpc_agent_sdk.code_executors import WorkspaceRuntimeResolver
from trpc_agent_sdk.code_executors.utils import normalize_globs
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.filter import BaseFilter
from trpc_agent_sdk.tools import BaseTool
from trpc_agent_sdk.types import FunctionDeclaration
from trpc_agent_sdk.types import Part
from trpc_agent_sdk.types import Schema
from trpc_agent_sdk.types import Type

from .._constants import SKILL_ARTIFACTS_STATE_KEY
from ._common import CreateWorkspaceNameCallback
from ._common import default_create_ws_name_callback

_DEFAULT_MAX_BYTES = 64 * 1024 * 1024
_ALLOWED_ROOTS = (DIR_WORK, DIR_OUT, DIR_RUNS)
_SAVE_REASON_NO_SERVICE = "artifact service is not configured"
_SAVE_REASON_NO_SESSION = "session is missing from invocation context"
_SAVE_REASON_NO_SESSION_IDS = "session app/user/session IDs are missing"


def _has_glob_meta(s: str) -> bool:
    return any(ch in s for ch in ("*", "?", "["))


def _normalize_workspace_prefix(path: str) -> str:
    """Normalize workspace env-style prefixes in path."""
    s = path.strip().replace("\\", "/")
    if s.startswith("workspace://"):
        s = s[len("workspace://"):]
    replacements = (
        ("${WORKSPACE_DIR}/", ""),
        ("$WORKSPACE_DIR/", ""),
        ("${WORK_DIR}/", f"{DIR_WORK}/"),
        ("$WORK_DIR/", f"{DIR_WORK}/"),
        ("${OUTPUT_DIR}/", f"{DIR_OUT}/"),
        ("$OUTPUT_DIR/", f"{DIR_OUT}/"),
        ("${RUN_DIR}/", f"{DIR_RUNS}/"),
        ("$RUN_DIR/", f"{DIR_RUNS}/"),
    )
    for src, dst in replacements:
        if s.startswith(src):
            return dst + s[len(src):]
    if s in ("$WORKSPACE_DIR", "${WORKSPACE_DIR}"):
        return ""
    if s in ("$WORK_DIR", "${WORK_DIR}"):
        return DIR_WORK
    if s in ("$OUTPUT_DIR", "${OUTPUT_DIR}"):
        return DIR_OUT
    if s in ("$RUN_DIR", "${RUN_DIR}"):
        return DIR_RUNS
    return s


def _is_workspace_env_path(path: str) -> bool:
    s = path.strip()
    if not s:
        return False
    return s.startswith("$") or s.startswith("${")


def _is_allowed_publish_path(rel: str) -> bool:
    return any(rel == root or rel.startswith(f"{root}/") for root in _ALLOWED_ROOTS)


def _artifact_save_skip_reason(ctx: InvocationContext) -> str:
    if ctx.artifact_service is None:
        return _SAVE_REASON_NO_SERVICE
    if ctx.session is None:
        return _SAVE_REASON_NO_SESSION
    if not (ctx.app_name and ctx.user_id and ctx.session_id):
        return _SAVE_REASON_NO_SESSION_IDS
    return ""


def _apply_artifact_state_delta(ctx: InvocationContext, saved_as: str, version: int, ref: str) -> None:
    tool_call_id = (ctx.function_call_id or "").strip()
    if not tool_call_id or not saved_as or version < 0:
        return
    artifact_ref = ref.strip() or f"artifact://{saved_as}@{version}"
    ctx.actions.state_delta[SKILL_ARTIFACTS_STATE_KEY] = {
        "tool_call_id": tool_call_id,
        "artifacts": [{
            "name": saved_as,
            "version": version,
            "ref": artifact_ref,
        }],
    }


def _resolve_workspace_root_from_config(ctx: InvocationContext) -> str:
    """Resolve workspace root from run_config/env; fallback to cwd."""
    if ctx.run_config and isinstance(ctx.run_config.custom_data, dict):
        for k in ("workspace_dir", "workspace_root", "workspace_path"):
            v = ctx.run_config.custom_data.get(k)
            if isinstance(v, str) and v.strip():
                return os.path.abspath(v.strip())
    env_root = os.environ.get(WORKSPACE_ENV_DIR_KEY, "").strip()
    if env_root:
        return os.path.abspath(env_root)
    return os.path.abspath(os.getcwd())


def _normalize_artifact_path(raw: str, workspace_root: str) -> tuple[str, str]:
    """Return (workspace-relative path, absolute file path)."""
    s = _normalize_workspace_prefix(raw)
    if not s:
        raise ValueError("path is required")
    if _has_glob_meta(s):
        raise ValueError("path must not contain glob patterns")
    if _is_workspace_env_path(s):
        out = normalize_globs([s.replace("${RUN_DIR}", DIR_RUNS).replace("$RUN_DIR", DIR_RUNS)])
        if not out:
            raise ValueError("invalid path")
        s = out[0]

    cleaned = posixpath.normpath(s)
    if posixpath.isabs(cleaned):
        rel = cleaned.lstrip("/")
        rel = posixpath.normpath(rel)
        if rel in ("", ".", ".."):
            raise ValueError("path must point to a file inside the workspace")
    else:
        rel = cleaned
        if rel in (".", "..") or rel.startswith("../"):
            raise ValueError("path must stay within the workspace")

    if not _is_allowed_publish_path(rel):
        raise ValueError("path must stay under work/, out/, or runs/")

    abs_path = os.path.abspath(os.path.join(workspace_root, rel))
    rel_check = os.path.relpath(abs_path, workspace_root).replace("\\", "/")
    if rel_check in (".", "..") or rel_check.startswith("../"):
        raise ValueError("path must stay within the workspace")

    rel = posixpath.normpath(rel)
    return rel, abs_path


class SaveArtifactTool(BaseTool):
    """Persist an existing workspace file as an artifact."""

    def __init__(
        self,
        max_file_bytes: int = _DEFAULT_MAX_BYTES,
        workspace_runtime: Optional[BaseWorkspaceRuntime] = None,
        workspace_runtime_resolver: Optional[WorkspaceRuntimeResolver] = None,
        create_ws_name_cb: Optional[CreateWorkspaceNameCallback] = None,
        filters_name: Optional[list[str]] = None,
        filters: Optional[list[BaseFilter]] = None,
    ):
        super().__init__(
            name="workspace_save_artifact",
            description=("Save an existing file from the current workspace as an artifact. "
                         "Path must be under work/, out/, or runs/."),
            filters_name=filters_name,
            filters=filters,
        )
        self._workspace_runtime_resolver = workspace_runtime_resolver
        self._max_file_bytes = max_file_bytes
        self._workspace_runtime = workspace_runtime
        self._create_ws_name_cb = create_ws_name_cb or default_create_ws_name_callback

    def _get_workspace_runtime(self, ctx: InvocationContext) -> BaseWorkspaceRuntime:
        if self._workspace_runtime_resolver is not None:
            return self._workspace_runtime_resolver(ctx)
        return self._workspace_runtime

    async def _resolve_workspace_root(self, ctx: InvocationContext) -> str:
        """Resolve workspace root, preferring the shared workspace_exec workspace."""
        runtime = self._get_workspace_runtime(ctx)
        if runtime is not None:
            workspace_id = self._create_ws_name_cb(ctx)
            ws = await runtime.manager(ctx).create_workspace(workspace_id, ctx)
            if ws.path:
                return os.path.abspath(ws.path)
        return _resolve_workspace_root_from_config(ctx)

    def _get_declaration(self) -> Optional[FunctionDeclaration]:
        return FunctionDeclaration(
            name="workspace_save_artifact",
            description=("Save an existing file from the current workspace as an artifact. "
                         "Use this to get a stable artifact:// reference for files under "
                         "work/, out/, or runs/."),
            parameters=Schema(
                type=Type.OBJECT,
                required=["path"],
                properties={
                    "path":
                    Schema(
                        type=Type.STRING,
                        description=("Workspace-relative file path to save. "
                                     "Supports prefixes like $WORK_DIR/, $OUTPUT_DIR/, "
                                     "$RUN_DIR/, and workspace://."),
                    ),
                },
            ),
            response=Schema(
                type=Type.OBJECT,
                required=["path", "saved_as", "version", "ref", "size_bytes"],
                properties={
                    "path": Schema(type=Type.STRING, description="Workspace-relative source path."),
                    "saved_as": Schema(type=Type.STRING, description="Artifact name used when saving."),
                    "version": Schema(type=Type.INTEGER, description="Artifact version."),
                    "ref": Schema(type=Type.STRING, description="artifact:// reference for saved artifact."),
                    "mime_type": Schema(type=Type.STRING, description="Detected MIME type."),
                    "size_bytes": Schema(type=Type.INTEGER, description="File size in bytes."),
                },
            ),
        )

    async def _run_async_impl(self, *, tool_context: InvocationContext, args: dict[str, Any]) -> Any:
        raw_path = str(args.get("path", "")).strip()
        if not raw_path:
            return {"error": "INVALID_PARAMETER: path is required"}
        reason = _artifact_save_skip_reason(tool_context)
        if reason:
            return {"error": f"ARTIFACT_SAVE_UNAVAILABLE: {reason}"}

        try:
            workspace_root = await self._resolve_workspace_root(tool_context)
            rel, abs_path = _normalize_artifact_path(raw_path, workspace_root)
        except ValueError as ex:
            return {"error": f"INVALID_PARAMETER: {str(ex)}"}

        if not os.path.exists(abs_path):
            return {"error": f"FILE_NOT_FOUND: workspace artifact file not found: {rel}"}
        if not os.path.isfile(abs_path):
            return {"error": f"INVALID_PATH: path is not a file: {rel}"}

        size_bytes = os.path.getsize(abs_path)
        if size_bytes > self._max_file_bytes:
            return {"error": f"FILE_TOO_LARGE: file exceeds {self._max_file_bytes} bytes limit"}

        with open(abs_path, "rb") as f:
            data = f.read()

        mime_type = mimetypes.guess_type(abs_path)[0] or "application/octet-stream"
        version = await tool_context.save_artifact(rel, Part.from_bytes(data=data, mime_type=mime_type))
        ref = f"artifact://{rel}@{version}"
        _apply_artifact_state_delta(tool_context, rel, version, ref)
        return {
            "path": rel,
            "saved_as": rel,
            "version": version,
            "ref": ref,
            "mime_type": mime_type,
            "size_bytes": size_bytes,
        }
