# -*- coding: utf-8 -*-
#
# Copyright @ 2025 Tencent.com
"""Skill run tool for executing commands in skill workspaces.

This module provides the SkillRunTool class which allows LLM to execute commands
inside a skill workspace. It stages the entire skill directory and runs commands,
aligned with the Go implementation at:
https://github.com/trpc-group/trpc-agent-go/blob/main/tool/skill/run.go
"""

from __future__ import annotations

import os
import re
import shlex
from pathlib import Path
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing_extensions import override

from pydantic import BaseModel
from pydantic import Field
from trpc_agent_sdk.code_executors import CodeFile
from trpc_agent_sdk.code_executors import DIR_OUT
from trpc_agent_sdk.code_executors import DIR_SKILLS
from trpc_agent_sdk.code_executors import DIR_WORK
from trpc_agent_sdk.code_executors import ENV_SKILL_NAME
from trpc_agent_sdk.code_executors import ManifestOutput
from trpc_agent_sdk.code_executors import WorkspaceInfo
from trpc_agent_sdk.code_executors import WorkspaceInputSpec
from trpc_agent_sdk.code_executors import WorkspaceOutputSpec
from trpc_agent_sdk.code_executors import WorkspacePutFileInfo
from trpc_agent_sdk.code_executors import WorkspaceRunProgramSpec
from trpc_agent_sdk.code_executors import WorkspaceRunResult
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.filter import BaseFilter
from trpc_agent_sdk.log import logger
from trpc_agent_sdk.tools import BaseTool
from trpc_agent_sdk.types import FunctionDeclaration
from trpc_agent_sdk.types import Part
from trpc_agent_sdk.types import Schema

from .._constants import SKILL_LOADED_STATE_KEY_PREFIX
from .._constants import SKILL_REPOSITORY_KEY
from .._repository import BaseSkillRepository
from .._utils import shell_quote
from ..stager import SkillStageRequest
from ..stager import Stager
from ._copy_stager import CopySkillStager

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MAX_OUTPUT_CHARS: int = 16 * 1024  # 16 KB for stdout/stderr
_MAX_PRIMARY_OUTPUT_CHARS: int = 32 * 1024  # 32 KB for primary_output
_AUTO_EXPORT_PATTERN: str = f"{DIR_OUT}/**"
_AUTO_EXPORT_MAX: int = 20

_SKILL_DIR_VENV = ".venv"
_ENV_VIRTUAL_ENV = "VIRTUAL_ENV"
_ENV_PATH = "PATH"
_ENV_EDITOR = "EDITOR"
_ENV_VISUAL = "VISUAL"

_EDITOR_HELPER_DIR = ".trpc_agent_sdk"
_EDITOR_CONTENT_FILE = "editor_input.txt"
_EDITOR_SCRIPT_FILE = "editor_write.sh"

_DISALLOWED_SHELL_META = "\n\r;&|<>"

# env keys blocked from skill_run_env injection
_BLOCKED_SKILL_ENV_KEYS = frozenset({
    "LD_PRELOAD",
    "LD_LIBRARY_PATH",
    "DYLD_INSERT_LIBRARIES",
    "DYLD_LIBRARY_PATH",
    "DYLD_FORCE_FLAT_NAMESPACE",
    "OPENSSL_CONF",
})

_WARN_STDOUT_TRUNCATED = "stdout truncated"
_WARN_STDERR_TRUNCATED = "stderr truncated"
_WARN_FAILED_RUN_EMPTY_OUTPUTS = ("empty output_files omitted because command failed; "
                                  "shell redirections can create empty files before execution fails")

_WARN_SAVE_ARTIFACTS_SKIPPED = ("save_as_artifacts requested but artifact service is not configured; "
                                "outputs are not persisted")

# MIME types considered text for inline/primary-output selection
_TEXT_MIME_PREFIXES = ("text/", )
_TEXT_MIME_EXACT = frozenset({
    "application/json",
    "application/xml",
    "application/xhtml+xml",
    "application/javascript",
    "application/x-javascript",
    "application/typescript",
    "application/yaml",
    "application/toml",
    "application/csv",
    "application/x-sh",
    "application/x-python",
    "application/ld+json",
    "application/graphql",
})

# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _inline_json_schema_refs(schema: dict[str, Any]) -> dict[str, Any]:
    """Inline $ref references in JSON Schema by replacing them with actual definitions."""
    defs = schema.get('$defs', {})
    if not defs:
        return schema

    def resolve_ref(obj: Any) -> Any:
        if isinstance(obj, dict):
            if '$ref' in obj:
                ref_path = obj['$ref']
                if ref_path.startswith('#/$defs/'):
                    ref_name = ref_path.replace('#/$defs/', '')
                    if ref_name in defs:
                        resolved = resolve_ref(defs[ref_name])
                        merged = {**resolved, **{k: v for k, v in obj.items() if k != '$ref'}}
                        return merged
                return obj
            else:
                return {k: resolve_ref(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [resolve_ref(item) for item in obj]
        else:
            return obj

    result = {k: v for k, v in schema.items() if k != '$defs'}
    result = resolve_ref(result)
    return result


def _is_text_mime(mime: str) -> bool:
    """Return True when *mime* is a text-like content type."""
    if not mime:
        return True  # assume text when unknown
    for prefix in _TEXT_MIME_PREFIXES:
        if mime.startswith(prefix):
            return True
    return mime.split(";")[0].strip() in _TEXT_MIME_EXACT


def _should_inline_file_content(f: CodeFile) -> bool:
    """Return True when the file content should be included inline in the response."""
    if not f.content:
        return True
    if not _is_text_mime(f.mime_type):
        return False
    if "\x00" in f.content:
        return False
    return True


def _truncate_output(s: str) -> tuple[str, bool]:
    """Truncate *s* to _MAX_OUTPUT_CHARS. Returns (truncated_str, was_truncated)."""
    if len(s) <= _MAX_OUTPUT_CHARS:
        return s, False
    return s[:_MAX_OUTPUT_CHARS], True


def _workspace_ref(name: str) -> str:
    return f"workspace://{name}" if name else ""


def _filter_failed_empty_outputs(
    exit_code: int,
    timed_out: bool,
    files: list[SkillRunFile],
) -> tuple[list[SkillRunFile], list[str]]:
    """When the command failed, drop empty output files and add a warning."""
    if exit_code == 0 and not timed_out:
        return files, []
    filtered = [f for f in files if f.content or f.size_bytes > 0]
    if len(filtered) == len(files):
        return files, []
    return filtered, [_WARN_FAILED_RUN_EMPTY_OUTPUTS]


def _select_primary_output(files: list[SkillRunFile]) -> Optional[SkillRunFile]:
    """Pick the best small text output file (lexicographically first name)."""
    best: Optional[SkillRunFile] = None
    for f in files:
        if not (f.content or "").strip():
            continue
        if not _is_text_mime(f.mime_type):
            continue
        if len(f.content) > _MAX_PRIMARY_OUTPUT_CHARS:
            continue
        if best is None or f.name < best.name:
            best = f
    return best


def _split_command_line(cmd: str) -> list[str]:
    """Parse a quoted command string into argv tokens.

    Rejects shell metacharacters so callers can safely exec without a shell
    when allowedCmds / deniedCmds are active.
    """
    if not cmd.strip():
        raise ValueError("skill_run: command is empty")
    for ch in _DISALLOWED_SHELL_META:
        if ch in cmd:
            raise ValueError(f"skill_run: shell metacharacter {ch!r} is not allowed when "
                             "command restrictions are enabled. Provide a single executable "
                             "with args only (no redirects/pipes/chaining).")
    args: list[str] = []
    cur: list[str] = []
    in_single = False
    in_double = False
    escaped = False
    for ch in cmd:
        if escaped:
            cur.append(ch)
            escaped = False
            continue
        if not in_single and ch == "\\":
            escaped = True
            continue
        if not in_double and ch == "'":
            in_single = not in_single
            continue
        if not in_single and ch == '"':
            in_double = not in_double
            continue
        if not in_single and not in_double and ch in (" ", "\t"):
            if cur:
                args.append("".join(cur))
                cur = []
            continue
        cur.append(ch)
    if escaped:
        raise ValueError("skill_run: trailing escape")
    if in_single or in_double:
        raise ValueError("skill_run: unterminated quote")
    if cur:
        args.append("".join(cur))
    if not args:
        raise ValueError("skill_run: command is empty")
    return args


def _build_editor_wrapper_script(content_path: str) -> str:
    """Return a POSIX shell script that copies *content_path* into $EDITOR's target."""
    q = shell_quote
    lines = [
        "#!/bin/sh",
        "set -eu",
        "for last do target=\"$last\"; done",
        'if [ -z "${target:-}" ]; then',
        '  echo "editor wrapper: missing target file" >&2',
        "  exit 1",
        "fi",
        f"cat {q(content_path)} > \"$target\"",
    ]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class SkillRunFile(BaseModel):
    """A single output file collected from the skill workspace."""

    name: str = Field(default="", description="Workspace-relative path")
    content: str = Field(default="", description="Inline content (text files only)")
    mime_type: str = Field(default="", description="Detected MIME type")
    size_bytes: int = Field(default=0, description="File size in bytes")
    truncated: bool = Field(default=False, description="True if content was truncated")
    ref: str = Field(
        default="",
        description="Stable workspace:// reference for cross-tool file passing",
    )


class SkillRunInput(BaseModel):
    """Input parameters for skill_run tool."""

    skill: str = Field(..., description="Skill name")
    command: str = Field(..., description="Shell command to execute")
    cwd: str = Field(default="", description="Working directory (relative to skill root)")
    env: dict[str, str] = Field(default_factory=dict, description="Environment variables")
    stdin: str = Field(default="", description="Optional one-shot stdin text passed to the command")
    editor_text: str = Field(
        default="",
        description=("Optional text used to satisfy CLIs that launch $EDITOR. "
                     "When set, skill_run stages a temporary editor wrapper and points EDITOR/VISUAL to it."),
    )
    output_files: list[str] = Field(
        default_factory=list,
        description="Workspace-relative paths/globs to collect and inline text (e.g. out/*.txt). "
        "Do not use workspace:// or artifact:// here.",
    )
    timeout: int = Field(default=0, description="Timeout in seconds")
    save_as_artifacts: bool = Field(default=False, description="Save output files as artifacts")
    omit_inline_content: bool = Field(default=False, description="Omit inline content in response")
    artifact_prefix: str = Field(default="", description="Prefix for artifact names")
    inputs: list[WorkspaceInputSpec] = Field(default_factory=list, description="Declarative inputs")
    outputs: Optional[WorkspaceOutputSpec] = Field(default=None, description="Declarative outputs")


class ArtifactInfo(BaseModel):
    """Artifact files if saved."""

    name: str = Field(default="", description="Artifact name")
    version: int = Field(default=0, description="Artifact version")


class SkillRunOutput(BaseModel):
    """Output result from skill_run tool."""

    stdout: str = Field(default="", description="Standard output (may be truncated; see warnings)")
    stderr: str = Field(default="", description="Standard error (may be truncated; see warnings)")
    exit_code: int = Field(default=0, description="Process exit code; 0 = success")
    timed_out: bool = Field(default=False, description="True if the command timed out")
    duration_ms: int = Field(default=0, description="Execution duration in milliseconds")
    output_files: list[SkillRunFile] = Field(
        default_factory=list,
        description="Collected output files. Text files inlined via content. "
        "Binary outputs omit inline content; access via ref (workspace://...).",
    )
    primary_output: Optional[SkillRunFile] = Field(
        default=None,
        description="Convenience: best small text output file (if any)",
    )
    artifact_files: list[ArtifactInfo] = Field(default_factory=list,
                                               description="Artifact references when save_as_artifacts is enabled")
    warnings: list[str] = Field(
        default_factory=list,
        description="Non-fatal warnings about truncation, persistence, or empty outputs",
    )
    suggested_commands: Optional[list[str]] = Field(
        default=None,
        description=("Suggested runnable commands extracted from SKILL.md when the provided "
                     "command is not found."),
    )
    suggested_tools: Optional[list[str]] = Field(
        default=None,
        description=("Suggested tool names from SKILL.md Tools section when command-not-found "
                     "indicates the request should use tool calls instead of shell execution."),
    )


# ---------------------------------------------------------------------------
# SkillRunTool
# ---------------------------------------------------------------------------


class SkillRunTool(BaseTool):
    """Tool for running commands inside a skill workspace.

    This tool stages the entire skill directory and executes a command,
    aligned with the Go implementation's RunTool.
    """

    def __init__(
        self,
        repository: BaseSkillRepository,
        filters: Optional[List[BaseFilter]] = None,
        *,
        require_skill_loaded: bool = False,
        block_inline_python_rewrite: bool = False,
        force_save_artifacts: bool = False,
        allowed_cmds: Optional[List[str]] = None,
        denied_cmds: Optional[List[str]] = None,
        skill_stager: Optional[Stager] = None,
        **kwargs,
    ):
        """Initialize SkillRunTool.

        Args:
            repository: Skill repository.
            filters: Optional tool filters.
            require_skill_loaded: When True, skill_run raises unless skill_load was called first
                                  for this skill in the current session.
            block_inline_python_rewrite: When True, reject ad-hoc ``python -c`` commands
                                         if SKILL.md already provides script-based python
                                         command examples (for example ``python3 scripts/foo.py``).
            force_save_artifacts: When True, always attempt to persist collected output files
                                  via the artifact service (if available).
            allowed_cmds: When set, only these command names (first token) are allowed.
                          Shell metacharacters are also rejected.
            denied_cmds: When set, commands whose first token is in this list are rejected.
                         Shell metacharacters are also rejected.
            skill_stager: Custom staging strategy.  When ``None`` (default) the
                          built-in ``CopySkillStager`` is used, which copies the
                          skill directory into ``skills/<name>``.  Provide a
                          custom :class:`BaseSkillStager` to use read-only mounts,
                          remote caches, or other strategies.
        """
        super().__init__(
            name="skill_run",
            description=("Run a command inside a skill workspace. Stages the entire skill directory "
                         "and runs a single command. User-uploaded file inputs are staged under "
                         "$WORK_DIR/inputs. Returns stdout/stderr, a primary_output (best small "
                         "text file), and collected output_files with workspace:// refs."),
            filters=filters,
        )
        self._repository = repository
        self._require_skill_loaded = require_skill_loaded
        self._block_inline_python_rewrite = block_inline_python_rewrite
        self._force_save_artifacts = force_save_artifacts
        self._allowed_cmds: frozenset[str] = frozenset(c.strip() for c in (allowed_cmds or []) if c.strip())
        self._denied_cmds: frozenset[str] = frozenset(c.strip() for c in (denied_cmds or []) if c.strip())
        # load from env when not explicitly set
        if not self._allowed_cmds:
            raw = os.environ.get("TRPC_AGENT_SKILL_RUN_ALLOWED_COMMANDS", "")
            self._allowed_cmds = frozenset(p.strip() for p in raw.replace(",", " ").split() if p.strip())
        if not self._denied_cmds:
            raw = os.environ.get("TRPC_AGENT_SKILL_RUN_DENIED_COMMANDS", "")
            self._denied_cmds = frozenset(p.strip() for p in raw.replace(",", " ").split() if p.strip())

        self._kwargs = kwargs
        self._run_tool_kwargs: dict = kwargs.pop("run_tool_kwargs", {})
        self._timeout = self._run_tool_kwargs.pop("timeout", 300.0)

        # Staging strategy: default is copy-based stager (mirrors Go newCopySkillStager)
        self._skill_stager: Stager = skill_stager or CopySkillStager()

    @property
    def skill_stager(self) -> Stager:
        """Get the skill stager."""
        return self._skill_stager

    # ------------------------------------------------------------------
    # Declaration
    # ------------------------------------------------------------------

    @override
    def _get_declaration(self) -> FunctionDeclaration:
        params_schema = _inline_json_schema_refs(SkillRunInput.model_json_schema())
        response_schema = _inline_json_schema_refs(SkillRunOutput.model_json_schema())
        desc = ("Run a command inside a skill workspace. "
                "Use it only for commands required by the skill docs (not for generic shell tasks). "
                "User-uploaded file inputs are staged under $WORK_DIR/inputs (also visible as inputs/). "
                "Returns stdout/stderr, a primary_output (best small text file), and collected "
                "output_files (text inline by default, with workspace:// refs). "
                "Prefer primary_output/output_files content; use output_files[*].ref when "
                "passing a file to other tools.")
        if self._allowed_cmds or self._denied_cmds:
            desc += (" Restrictions enabled: no shell syntax; one executable + args only; "
                     "no > < | ; && ||.")
            if self._allowed_cmds:
                preview = ", ".join(sorted(self._allowed_cmds)[:20])
                desc += f" Allowed commands: {preview}."
        return FunctionDeclaration(
            name="skill_run",
            description=desc,
            parameters=Schema.model_validate(params_schema),
            response=Schema.model_validate(response_schema),
        )

    # ------------------------------------------------------------------
    # Repository access
    # ------------------------------------------------------------------

    def _get_repository(self, context: InvocationContext) -> BaseSkillRepository:
        if self._repository:
            return self._repository
        return context.agent_context.get_metadata(SKILL_REPOSITORY_KEY)

    # ------------------------------------------------------------------
    # Skill-loaded check
    # ------------------------------------------------------------------

    def _is_skill_loaded(self, ctx: InvocationContext, skill_name: str) -> bool:
        """Return True when the skill was loaded in the current session."""
        try:
            key = f"{SKILL_LOADED_STATE_KEY_PREFIX}{skill_name.strip()}"
            val = ctx.session_state.get(key)
            return bool(val)
        except Exception:  # pylint: disable=broad-except
            return True  # default to allowed when state is not accessible

    # ------------------------------------------------------------------
    # Editor helper
    # ------------------------------------------------------------------

    async def _prepare_editor_env(
        self,
        ctx: InvocationContext,
        ws: WorkspaceInfo,
        env: dict[str, str],
        editor_text: str,
    ) -> None:
        """Stage editor helper files and set EDITOR/VISUAL env vars."""
        if not editor_text:
            return
        if _ENV_EDITOR in env:
            raise ValueError(f"editor_text cannot be combined with env.{_ENV_EDITOR}")
        if _ENV_VISUAL in env:
            raise ValueError(f"editor_text cannot be combined with env.{_ENV_VISUAL}")

        content_rel = f"{DIR_WORK}/{_EDITOR_HELPER_DIR}/{_EDITOR_CONTENT_FILE}"
        script_rel = f"{DIR_WORK}/{_EDITOR_HELPER_DIR}/{_EDITOR_SCRIPT_FILE}"
        content_abs = os.path.join(ws.path, content_rel)
        script_abs = os.path.join(ws.path, script_rel)

        # Try using workspace FS (works for container runtimes too)
        try:
            script_content = _build_editor_wrapper_script(content_abs)
            workspace_runtime = self._get_repository(ctx).workspace_runtime
            fs = workspace_runtime.fs(ctx)
            await fs.put_files(
                ws,
                [
                    WorkspacePutFileInfo(path=content_rel, content=editor_text.encode("utf-8"), mode=0o644),
                    WorkspacePutFileInfo(
                        path=script_rel,
                        content=script_content.encode("utf-8"),
                        mode=0o755,
                    ),
                ],
                ctx,
            )
        except Exception:  # pylint: disable=broad-except
            # Fallback: direct filesystem write (local workspaces)
            helper_dir = Path(ws.path) / DIR_WORK / _EDITOR_HELPER_DIR
            helper_dir.mkdir(parents=True, exist_ok=True)
            Path(content_abs).write_text(editor_text, encoding="utf-8")
            script_content = _build_editor_wrapper_script(content_abs)
            sp = Path(script_abs)
            sp.write_text(script_content, encoding="utf-8")
            sp.chmod(0o755)

        env[_ENV_EDITOR] = script_abs
        env[_ENV_VISUAL] = script_abs

    # ------------------------------------------------------------------
    # Command building helpers
    # ------------------------------------------------------------------

    def _wrap_with_venv(self, cmd: str, ws_path: str, skill_cwd_rel: str) -> str:
        """Prepend .venv activation to *cmd* if the skill has a .venv directory."""
        # Derive skill root from cwd (e.g. "skills/my_skill" or "skills/my_skill/sub")
        parts = skill_cwd_rel.replace("\\", "/").split("/")
        if len(parts) >= 2 and parts[0] == DIR_SKILLS:
            skill_root_rel = "/".join(parts[:2])
        else:
            skill_root_rel = skill_cwd_rel

        venv_dir = os.path.join(ws_path, skill_root_rel, _SKILL_DIR_VENV)
        venv_bin = os.path.join(venv_dir, "bin")

        return (f"export {_ENV_PATH}={shell_quote(venv_bin)}:\"${_ENV_PATH}\"; "
                f"if [ -z \"${_ENV_VIRTUAL_ENV}\" ]; then "
                f"export {_ENV_VIRTUAL_ENV}={shell_quote(venv_dir)}; fi; "
                f"{cmd}")

    def _build_command(self, command: str, ws_path: str, skill_cwd_rel: str) -> tuple[str, list[str]]:
        """Return (cmd, args) for WorkspaceRunProgramSpec.

        When command restrictions are active the command is executed directly
        (no shell); otherwise it is wrapped with venv activation and run via
        ``bash -c``.
        """
        if self._allowed_cmds or self._denied_cmds:
            argv = _split_command_line(command)
            base = os.path.basename(argv[0])
            if self._allowed_cmds and base not in self._allowed_cmds and argv[0] not in self._allowed_cmds:
                raise ValueError(f"skill_run: command {argv[0]!r} is not in allowed_commands")
            if base in self._denied_cmds or argv[0] in self._denied_cmds:
                raise ValueError(f"skill_run: command {argv[0]!r} is denied by denied_commands")
            return argv[0], argv[1:]

        wrapped = self._wrap_with_venv(command, ws_path, skill_cwd_rel)
        return "bash", ["-c", wrapped]

    # ------------------------------------------------------------------
    # Output file helpers
    # ------------------------------------------------------------------

    def _to_run_file(self, f: CodeFile) -> SkillRunFile:
        content = f.content if _should_inline_file_content(f) else ""
        return SkillRunFile(
            name=f.name,
            content=content,
            mime_type=f.mime_type,
            size_bytes=f.size_bytes,
            truncated=f.truncated,
            ref=_workspace_ref(f.name),
        )

    def _to_run_files(self, files: list[CodeFile]) -> list[SkillRunFile]:
        return [self._to_run_file(f) for f in files]

    # ------------------------------------------------------------------
    # Auto-export out/**
    # ------------------------------------------------------------------

    async def _auto_export_workspace_out(
        self,
        ctx: InvocationContext,
        ws: WorkspaceInfo,
    ) -> list[CodeFile]:
        """Collect up to _AUTO_EXPORT_MAX files from out/** automatically."""
        try:
            workspace_runtime = self._get_repository(ctx).workspace_runtime
            fs = workspace_runtime.fs(ctx)
            files = await fs.collect(ws, [_AUTO_EXPORT_PATTERN], ctx)
            if not files:
                return []
            return files[:_AUTO_EXPORT_MAX]
        except Exception:  # pylint: disable=broad-except
            return []

    # ------------------------------------------------------------------
    # Main execution
    # ------------------------------------------------------------------

    @override
    async def _run_async_impl(
        self,
        *,
        tool_context: InvocationContext,
        args: Dict[str, Any],
    ) -> Any:
        if self._run_tool_kwargs:
            for k, v in self._run_tool_kwargs.items():
                if k in SkillRunInput.model_fields:
                    args[k] = v
        try:
            inputs = SkillRunInput.model_validate(args)
        except Exception as ex:  # pylint: disable=broad-except
            raise ValueError(f"Invalid skill_run arguments: {ex}") from ex

        # require_skill_loaded gate
        if self._require_skill_loaded and not self._is_skill_loaded(tool_context, inputs.skill):
            raise ValueError(f"skill_run requires skill_load first for {inputs.skill!r}")

        # force_save_artifacts override
        if self._force_save_artifacts:
            if inputs.output_files:
                inputs = inputs.model_copy(update={"save_as_artifacts": True})
            if inputs.outputs and not inputs.output_files:
                inputs = inputs.model_copy(update={"outputs": inputs.outputs.model_copy(update={"save": True})})

        repository = self._get_repository(tool_context)

        session_id = inputs.skill
        if tool_context.session and tool_context.session.id:
            session_id = tool_context.session.id

        workspace_runtime = repository.workspace_runtime
        manager = workspace_runtime.manager(tool_context)
        ws = await manager.create_workspace(session_id, tool_context)

        # Stage skill via the pluggable stager strategy
        stage_result = await self._skill_stager.stage_skill(
            SkillStageRequest(
                skill_name=inputs.skill,
                repository=repository,
                workspace=ws,
                ctx=tool_context,
                engine=workspace_runtime,
                timeout=self._timeout,
            ))
        workspace_skill_dir = stage_result.workspace_skill_dir

        if inputs.inputs:
            fs = workspace_runtime.fs(tool_context)
            await fs.stage_inputs(ws, inputs.inputs, tool_context)

        cwd = self._resolve_cwd(inputs.cwd, workspace_skill_dir)
        result = await self._run_program(tool_context, ws, cwd, inputs)

        # Collect explicit outputs
        files: list[SkillRunFile]
        files, manifest = await self._prepare_outputs(tool_context, ws, inputs)

        # Auto-export out/** only when no explicit outputs requested
        if not files and manifest is None and not inputs.outputs and not inputs.output_files:
            auto_raw = await self._auto_export_workspace_out(tool_context, ws)
            if auto_raw:
                files = self._to_run_files(auto_raw)

        # Truncate stdout/stderr
        warnings: list[str] = []
        stdout, trunc = _truncate_output(result.stdout)
        if trunc:
            warnings.append(_WARN_STDOUT_TRUNCATED)
        stderr, trunc = _truncate_output(result.stderr)
        if trunc:
            warnings.append(_WARN_STDERR_TRUNCATED)

        # Filter empty files on failure
        files, filter_warns = _filter_failed_empty_outputs(result.exit_code, result.timed_out, files)
        warnings.extend(filter_warns)

        # Select primary output
        primary = _select_primary_output(files)
        suggested_commands = self._suggest_commands_for_missing_command(
            result,
            repository,
            inputs.skill,
        )
        suggested_tools = self._suggest_tools_for_missing_command(
            result,
            repository,
            inputs.skill,
        )

        output = SkillRunOutput(
            stdout=stdout,
            stderr=stderr,
            exit_code=result.exit_code,
            timed_out=result.timed_out,
            duration_ms=int(result.duration * 1000),
            output_files=files,
            primary_output=primary,
            warnings=warnings,
            suggested_commands=suggested_commands,
            suggested_tools=suggested_tools,
        )

        await self._attach_artifacts_if_requested(tool_context, ws, inputs, output, files)
        self._merge_manifest_artifact_refs(manifest, output)

        # omit_inline_content
        if inputs.omit_inline_content and output.artifact_files:
            for f in output.output_files:
                f.content = ""
            if output.primary_output:
                output.primary_output.content = ""

        return output.model_dump(exclude_none=True)

    # ------------------------------------------------------------------
    # Program runner
    # ------------------------------------------------------------------

    async def _run_program(
        self,
        ctx: InvocationContext,
        ws: WorkspaceInfo,
        cwd: str,
        input_data: SkillRunInput,
    ) -> WorkspaceRunResult:
        timeout = max(0, float(input_data.timeout)) or self._timeout
        env: dict[str, str] = dict(input_data.env) if input_data.env else {}

        if ENV_SKILL_NAME not in env:
            env[ENV_SKILL_NAME] = input_data.skill

        # Inject skill-specific env from repository (e.g. api_key → primary_env)
        repository = self._get_repository(ctx)
        try:
            skill_env: dict[str, str] = repository.skill_run_env(input_data.skill)
            for k, v in skill_env.items():
                k = k.strip()
                if not k or not v.strip():
                    continue
                if k in env:  # don't override explicit tool-call env
                    continue
                if os.environ.get(k, "").strip():  # don't override host env
                    continue
                if k.upper() in _BLOCKED_SKILL_ENV_KEYS:
                    continue
                env[k] = v
        except Exception:  # pylint: disable=broad-except
            pass

        # Stage editor helper if requested
        await self._prepare_editor_env(ctx, ws, env, input_data.editor_text)

        # Build command (with venv activation or command restrictions)
        blocked = self._precheck_inline_python_rewrite(repository, input_data)
        if blocked is not None:
            return blocked
        cmd, cmd_args = self._build_command(input_data.command, ws.path, cwd)

        workspace_runtime = repository.workspace_runtime
        runner = workspace_runtime.runner(ctx)
        ret = await runner.run_program(
            ws,
            WorkspaceRunProgramSpec(
                cmd=cmd,
                args=cmd_args,
                env=env,
                cwd=cwd,
                stdin=input_data.stdin,
                timeout=timeout,
            ),
            ctx,
        )
        if ret.exit_code != 0:
            ret = self._with_missing_command_hint(ret, input_data)
            ret = self._with_skill_doc_command_hint(ret, repository, input_data)
            ret = self._with_missing_entrypoint_hint(ret, input_data, ws, cwd)
            logger.info("Failed to run program: %s", ret.stderr)
        return ret

    def _precheck_inline_python_rewrite(
        self,
        repository: BaseSkillRepository,
        input_data: SkillRunInput,
    ) -> Optional[WorkspaceRunResult]:
        """Optionally block ad-hoc python -c when SKILL.md has script examples."""
        if not self._block_inline_python_rewrite:
            return None
        cmd = (input_data.command or "").strip()
        if not re.match(r"^python(?:\d+(?:\.\d+)?)?\s+-c\b", cmd):
            return None
        try:
            sk = repository.get(input_data.skill)
        except Exception:  # pylint: disable=broad-except
            return None
        if sk is None:
            return None
        examples = self._extract_shell_examples_from_skill_body(sk.body, limit=8)
        script_examples = [
            e for e in examples if re.match(r"^python(?:\d+(?:\.\d+)?)?\s+scripts/[^\s]+(?:\s|$)", e.strip())
        ]
        if not script_examples:
            return None
        stderr = ("skill_run rejected this ad-hoc inline python command.\n"
                  "This skill already provides script-based Python command examples in SKILL.md.\n"
                  "Use one of these commands exactly instead of `python -c` rewrites:\n" +
                  "\n".join(f"- {c}" for c in script_examples) + "\n")
        return WorkspaceRunResult(
            stdout="",
            stderr=stderr,
            exit_code=2,
            duration=0,
            timed_out=False,
        )

    @staticmethod
    def _with_missing_command_hint(ret: WorkspaceRunResult, input_data: SkillRunInput) -> WorkspaceRunResult:
        """Add a targeted hint when the shell command does not exist."""
        stderr_lower = (ret.stderr or "").lower()
        is_missing_cmd = ret.exit_code == 127 and ("command not found" in stderr_lower or
                                                   "not recognized as an internal or external command" in stderr_lower)
        if not is_missing_cmd:
            return ret
        hint = ("\n\nSkill command hint:\n"
                f"- The command `{input_data.command}` was not found in this skill workspace.\n"
                "- Do not invent command names.\n"
                "- Read the loaded `SKILL.md` and execute one of its exact shell examples.\n"
                "- If needed, call `skill_load` first so the full skill body is injected "
                "before calling `skill_run`.\n")
        return ret.model_copy(update={"stderr": f"{ret.stderr}{hint}"})

    @staticmethod
    def _extract_shell_examples_from_skill_body(body: str, limit: int = 5) -> list[str]:
        """Extract likely runnable shell command lines from SKILL.md body."""
        if not body:
            return []
        out: list[str] = []
        seen: set[str] = set()
        lines = body.splitlines()

        def maybe_add(cmd: str) -> None:
            cmd = re.sub(r"\s+", " ", (cmd or "").strip())
            if not cmd:
                return
            if cmd in seen:
                return
            # Keep only command-like lines.
            if not re.match(r"^[A-Za-z0-9_./$\"'`-]", cmd):
                return
            # Skip function-style examples like tool_name(arg="..."), which are
            # LLM tool calls rather than shell commands.
            if re.match(r"^[A-Za-z_][A-Za-z0-9_]*\s*\(.*\)\s*$", cmd):
                return
            seen.add(cmd)
            out.append(cmd)

        # 1) Parse markdown fenced code blocks.
        in_fence = False
        for raw in lines:
            line = raw.strip()
            if line.startswith("```"):
                in_fence = not in_fence
                continue
            if not in_fence:
                continue
            if not line or line.startswith("#"):
                continue
            if line in ("PY", "EOF") or line.startswith(("-", "*")):
                continue
            maybe_add(line)
            if len(out) >= limit:
                return out

        # 2) Parse "Command:" sections with indented multi-line commands.
        i = 0
        while i < len(lines) and len(out) < limit:
            cur = lines[i].strip()
            if cur.lower() != "command:":
                i += 1
                continue
            i += 1
            block: list[str] = []
            while i < len(lines):
                raw = lines[i]
                s = raw.strip()
                if not s:
                    if block:
                        break
                    i += 1
                    continue
                # Next section marker
                if s.lower() in ("command:", "output files", "overview", "examples", "tools:"):
                    break
                if re.match(r"^\d+\)", s):
                    break
                # Command content is commonly indented in SKILL.md examples.
                if raw.startswith(" ") or raw.startswith("\t"):
                    block.append(s)
                    i += 1
                    continue
                if block:
                    break
                i += 1
            if block:
                merged = " ".join(part.rstrip("\\").strip() for part in block)
                maybe_add(merged)
        return out[:limit]

    def _with_skill_doc_command_hint(
        self,
        ret: WorkspaceRunResult,
        repository: BaseSkillRepository,
        input_data: SkillRunInput,
    ) -> WorkspaceRunResult:
        """Append SKILL.md command examples when command is not found."""
        stderr = ret.stderr or ""
        stderr_lower = stderr.lower()
        is_missing_cmd = ret.exit_code == 127 and ("command not found" in stderr_lower or
                                                   "not recognized as an internal or external command" in stderr_lower)
        if not is_missing_cmd:
            return ret
        try:
            sk = repository.get(input_data.skill)
        except Exception:  # pylint: disable=broad-except
            return ret
        examples = self._extract_shell_examples_from_skill_body(sk.body, limit=5)
        tools = list(getattr(sk, "tools", []) or [])
        if not examples and not tools:
            return ret
        hint_parts: list[str] = []
        if examples:
            hint_parts.append("\n\nSKILL.md command examples:\n" + "\n".join(f"- {cmd}" for cmd in examples) + "\n")
        if tools:
            hint_parts.append("\nSKILL.md tools suggest this is a tool-call workflow:\n" +
                              "\n".join(f"- {name}" for name in tools) + "\n" +
                              "- Do not run these tool names via `skill_run` shell commands.\n" +
                              "- Call those tools directly (e.g. function/tool call) after `skill_load`.\n")
        hint = "".join(hint_parts)
        return ret.model_copy(update={"stderr": f"{stderr}{hint}"})

    @staticmethod
    def _is_missing_command_result(ret: WorkspaceRunResult) -> bool:
        """Return True when stderr indicates command-not-found failure."""
        stderr_lower = (ret.stderr or "").lower()
        return ret.exit_code == 127 and ("command not found" in stderr_lower
                                         or "not recognized as an internal or external command" in stderr_lower)

    def _suggest_commands_for_missing_command(
        self,
        ret: WorkspaceRunResult,
        repository: BaseSkillRepository,
        skill_name: str,
    ) -> Optional[list[str]]:
        """Return SKILL.md command suggestions for missing-command failures."""
        if not self._is_missing_command_result(ret):
            return None
        try:
            sk = repository.get(skill_name)
        except Exception:  # pylint: disable=broad-except
            return None
        suggestions = self._extract_shell_examples_from_skill_body(sk.body, limit=5)
        return suggestions or None

    def _suggest_tools_for_missing_command(
        self,
        ret: WorkspaceRunResult,
        repository: BaseSkillRepository,
        skill_name: str,
    ) -> Optional[list[str]]:
        """Return SKILL.md tool names when command-not-found implies tool calls."""
        if not self._is_missing_command_result(ret):
            return None
        try:
            sk = repository.get(skill_name)
        except Exception:  # pylint: disable=broad-except
            return None
        tools = list(getattr(sk, "tools", []) or [])
        return tools or None

    @staticmethod
    def _extract_command_path_candidates(command: str) -> list[str]:
        """Extract likely relative path tokens from a shell command."""
        try:
            tokens = shlex.split(command, posix=True)
        except ValueError:
            tokens = command.split()
        out: list[str] = []
        seen: set[str] = set()
        for tok in tokens:
            s = (tok or "").strip()
            if not s or s.startswith("-"):
                continue
            # Keep relative path-like tokens only.
            is_path_like = "/" in s or s.endswith((".py", ".sh", ".pl", ".rb", ".js", ".ts"))
            if not is_path_like or os.path.isabs(s):
                continue
            if s in seen:
                continue
            seen.add(s)
            out.append(s)
        return out

    @staticmethod
    def _list_entrypoint_suggestions(run_dir: Path, limit: int = 20) -> list[str]:
        """List likely runnable files from common locations under the current cwd."""
        suggestions: list[str] = []
        seen: set[str] = set()
        roots = [
            run_dir,
            run_dir / "scripts",
            run_dir / "bin",
            run_dir / "tools",
        ]
        for root in roots:
            try:
                if not root.is_dir():
                    continue
                for p in sorted(root.rglob("*")):
                    if len(suggestions) >= limit:
                        return suggestions
                    if not p.is_file():
                        continue
                    rel = p.relative_to(run_dir).as_posix()
                    # Prefer obviously runnable entries.
                    is_runnable = os.access(p.as_posix(), os.X_OK) or rel.endswith(
                        (".py", ".sh", ".pl", ".rb", ".js", ".ts"))
                    if not is_runnable or rel in seen:
                        continue
                    seen.add(rel)
                    suggestions.append(rel)
            except Exception:  # pylint: disable=broad-except
                continue
        return suggestions

    @staticmethod
    def _with_missing_entrypoint_hint(
        ret: WorkspaceRunResult,
        input_data: SkillRunInput,
        ws: WorkspaceInfo,
        cwd: str,
    ) -> WorkspaceRunResult:
        """Add a generic hint when command references missing relative entrypoints."""
        stderr = ret.stderr or ""
        stderr_lower = stderr.lower()
        if ret.exit_code == 0:
            return ret
        if "no such file or directory" not in stderr_lower and "can't open file" not in stderr_lower:
            return ret
        missing_candidates = SkillRunTool._extract_command_path_candidates(input_data.command)
        if not missing_candidates:
            return ret

        run_dir = Path(ws.path) / cwd
        # Show only candidates that are truly missing under cwd.
        missing = [p for p in missing_candidates if not (run_dir / p).exists()]
        if not missing:
            return ret
        available = SkillRunTool._list_entrypoint_suggestions(run_dir, limit=20)
        if not available:
            return ret

        hint = ("\n\nSkill entrypoint hint:\n"
                f"- Missing relative path(s) in command: {', '.join(f'`{m}`' for m in missing)}\n"
                f"- Runnable file candidates from current skill cwd: {', '.join(available)}\n"
                "- Do not invent file names or paths.\n"
                "- Read the loaded `SKILL.md` and run one of those commands exactly.\n")
        return ret.model_copy(update={"stderr": f"{stderr}{hint}"})

    def _resolve_cwd(self, cwd: str, skill_dir: str) -> str:
        """Resolve the working directory relative to the workspace root.

        Args:
            cwd: User-supplied cwd (may be empty, relative, or ``$SKILLS_DIR``-prefixed).
            skill_dir: Workspace-relative skill root returned by the stager
                       (e.g. ``"skills/my_skill"``).

        Returns:
            Workspace-relative working directory path.
        """
        base = skill_dir
        s = cwd.strip()
        if not s:
            return base
        if s.startswith("/"):
            return s
        # Normalize $SKILLS_DIR/... env-var style paths
        if "$SKILLS_DIR" in s or "${SKILLS_DIR}" in s:
            s = s.replace("$SKILLS_DIR", DIR_SKILLS).replace("${SKILLS_DIR}", DIR_SKILLS)
            # If the resolved path points to the skill root itself, use base
            if s == base or s.rstrip("/") == base.rstrip("/"):
                return base
        # Relative path: join under skill root
        return os.path.join(base, cwd)

    # ------------------------------------------------------------------
    # Output collection
    # ------------------------------------------------------------------

    async def _prepare_outputs(
        self,
        ctx: InvocationContext,
        ws: WorkspaceInfo,
        input_data: SkillRunInput,
    ) -> tuple[list[SkillRunFile], Optional[ManifestOutput]]:
        """Collect files via OutputSpec or legacy output_files patterns."""
        workspace_runtime = self._get_repository(ctx).workspace_runtime
        fs = workspace_runtime.fs(ctx)

        if input_data.outputs and not input_data.output_files:
            manifest = await fs.collect_outputs(ws, input_data.outputs, ctx)
            files: list[SkillRunFile] = []
            if input_data.outputs.inline:
                for fr in manifest.files:
                    cf = CodeFile(
                        name=fr.name,
                        content=fr.content,
                        mime_type=fr.mime_type,
                        size_bytes=getattr(fr, "size_bytes", 0),
                        truncated=getattr(fr, "truncated", False),
                    )
                    files.append(self._to_run_file(cf))
            return files, manifest

        if input_data.output_files:
            raw = await fs.collect(ws, input_data.output_files, ctx)
            return self._to_run_files(raw), None

        return [], None

    # ------------------------------------------------------------------
    # Artifact helpers
    # ------------------------------------------------------------------

    def _merge_manifest_artifact_refs(self, manifest: Optional[ManifestOutput], output: SkillRunOutput) -> None:
        """Append artifact refs derived from a manifest (when inline save was used)."""
        if output.artifact_files or manifest is None:
            return
        for fr in manifest.files:
            if fr.saved_as:
                output.artifact_files.append(ArtifactInfo(name=fr.saved_as, version=fr.version))

    async def _attach_artifacts_if_requested(
        self,
        ctx: InvocationContext,
        ws: WorkspaceInfo,
        input_data: SkillRunInput,
        output: SkillRunOutput,
        files: list[SkillRunFile],
    ) -> None:
        """Save files as artifacts when requested."""
        if not files:
            return
        if not (input_data.save_as_artifacts and files):
            return
        if not ctx.artifact_service:
            output.warnings.append(_WARN_SAVE_ARTIFACTS_SKIPPED)
            return
        refs = await self._save_artifacts(ctx, files, input_data.artifact_prefix)
        output.artifact_files = refs

    async def _save_artifacts(
        self,
        ctx: InvocationContext,
        files: list[SkillRunFile],
        prefix: str,
    ) -> list[ArtifactInfo]:
        """Save files as artifacts and return references."""
        refs: list[ArtifactInfo] = []
        for f in files:
            name = (prefix + f.name) if prefix else f.name
            artifact = Part.from_bytes(data=f.content.encode("utf-8"), mime_type=f.mime_type)
            version = await ctx.save_artifact(name, artifact)
            refs.append(ArtifactInfo(name=name, version=version))
        return refs
