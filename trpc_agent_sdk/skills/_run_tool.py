# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Skill run tool for executing commands in skill workspaces.

This module provides the SkillRunTool class which allows LLM to execute commands
inside a skill workspace. It stages the entire skill directory and runs commands,
similar to the Go implementation at:
https://github.com/trpc-group/trpc-agent-go/blob/main/tool/skill/run.go
"""

from __future__ import annotations

import os
import time
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
from trpc_agent_sdk.code_executors import WorkspaceRunProgramSpec
from trpc_agent_sdk.code_executors import WorkspaceRunResult
from trpc_agent_sdk.code_executors import WorkspaceStageOptions
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.filter import BaseFilter
from trpc_agent_sdk.log import logger
from trpc_agent_sdk.tools import BaseTool
from trpc_agent_sdk.types import FunctionDeclaration
from trpc_agent_sdk.types import Part
from trpc_agent_sdk.types import Schema

from ._constants import SKILL_REPOSITORY_KEY
from ._repository import BaseSkillRepository
from ._types import SkillMetadata
from ._utils import compute_dir_digest
from ._utils import ensure_layout
from ._utils import load_metadata
from ._utils import save_metadata
from ._utils import shell_quote


def _inline_json_schema_refs(schema: dict[str, Any]) -> dict[str, Any]:
    """Inline $ref references in JSON Schema by replacing them with actual definitions.

    Args:
        schema: JSON Schema dictionary that may contain $ref references

    Returns:
        Inlined JSON Schema dictionary without $ref references
    """
    defs = schema.get('$defs', {})
    if not defs:
        return schema

    def resolve_ref(obj: Any) -> Any:
        """Recursively resolve $ref references in schema object."""
        if isinstance(obj, dict):
            if '$ref' in obj:
                ref_path = obj['$ref']
                if ref_path.startswith('#/$defs/'):
                    ref_name = ref_path.replace('#/$defs/', '')
                    if ref_name in defs:
                        # Recursively resolve refs in the referenced definition
                        resolved = resolve_ref(defs[ref_name])
                        # Merge any additional properties from the original object
                        merged = {**resolved, **{k: v for k, v in obj.items() if k != '$ref'}}
                        return merged
                return obj
            else:
                return {k: resolve_ref(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [resolve_ref(item) for item in obj]
        else:
            return obj

    # Create a copy and remove $defs
    result = {k: v for k, v in schema.items() if k != '$defs'}
    # Resolve all refs
    result = resolve_ref(result)
    return result


class SkillRunInput(BaseModel):
    """Input parameters for skill_run tool."""
    skill: str = Field(..., description="Skill name")
    command: str = Field(..., description="Shell command to execute")
    cwd: str = Field(default="", description="Working directory (relative to skill root)")
    env: dict[str, str] = Field(default_factory=dict, description="Environment variables")
    output_files: list[str] = Field(default_factory=list, description="Glob patterns to collect output files")
    timeout: int = Field(default=0, description="Timeout in seconds")
    save_as_artifacts: bool = Field(default=False, description="Save output files as artifacts")
    omit_inline_content: bool = Field(default=False, description="Omit inline content in response")
    artifact_prefix: str = Field(default="", description="Prefix for artifact names")
    inputs: list[WorkspaceInputSpec] = Field(default_factory=list, description="Inputs")
    outputs: Optional[WorkspaceOutputSpec] = Field(default=None, description="Outputs")


class ArtifactInfo(BaseModel):
    """Artifact files if saved."""
    name: str = Field(default="", description="Artifact name")
    version: int = Field(default=0, description="Artifact version")


class SkillRunOutput(BaseModel):
    """Output result from skill_run tool."""
    stdout: str = Field(default="", description="Standard output")
    stderr: str = Field(default="", description="Standard error")
    exit_code: int = Field(default=0, description="Exit code")
    timed_out: bool = Field(default=False, description="Whether execution timed out")
    duration_ms: int = Field(default=0, description="Execution duration in milliseconds")
    output_files: list[CodeFile] = Field(default_factory=list, description="Collected output files")
    artifact_files: list[ArtifactInfo] = Field(default_factory=list, description="Artifact files if saved")


class SkillRunTool(BaseTool):
    """Tool for running commands inside a skill workspace.

    This tool stages the entire skill directory and executes a command,
    similar to the Go implementation's RunTool.
    """

    def __init__(self, repository: BaseSkillRepository, filters: Optional[List[BaseFilter]] = None, **kwargs):
        """Initialize SkillRunTool.

        Args:
            repository: Skill repository. If None, will be retrieved from context metadata.
            code_executor: Code executor. If None, will use subprocess execution.
            workspace_base: Base directory for workspaces. If None, uses temp directory.
        """
        super().__init__(
            name="skill_run",
            description=
            "Run a command inside a skill workspace. Stages the entire skill directory and runs a single command.",
            filters=filters,
        )
        self._repository = repository
        self._kwargs = kwargs
        self._run_tool_kwargs: dict = kwargs.pop("run_tool_kwargs", {})
        self._timeout = self._run_tool_kwargs.pop("timeout", 5.0)

    @override
    def _get_declaration(self) -> FunctionDeclaration:
        """Get function declaration for skill_run tool."""
        # Get JSON schemas and inline $ref references
        params_schema = _inline_json_schema_refs(SkillRunInput.model_json_schema())
        response_schema = _inline_json_schema_refs(SkillRunOutput.model_json_schema())

        return FunctionDeclaration(
            name="skill_run",
            description=
            "Run a command inside a skill workspace. Stages the entire skill directory and runs a single command.",
            parameters=Schema.model_validate(params_schema),
            response=Schema.model_validate(response_schema),
        )

    def _get_repository(self, context: InvocationContext) -> BaseSkillRepository:
        """Get repository from context or use instance repository."""
        if self._repository:
            return self._repository
        return context.agent_context.get_metadata(SKILL_REPOSITORY_KEY)

    async def _link_workspace_dirs(
        self,
        ctx: InvocationContext,
        ws: WorkspaceInfo,
        skill_name: str,
    ) -> None:
        """
        Create convenience symlinks under the staged skill root.

        Args:
            ctx: Context object
            eng: Engine instance
            ws: Workspace
            name: Skill name

        Raises:
            Exception: If linking fails
        """
        skill_root = os.path.join(DIR_SKILLS, skill_name)

        # Relative links from skills/<name> to workspace dirs
        to_out = os.path.join("..", "..", DIR_OUT)
        to_work = os.path.join("..", "..", DIR_WORK)
        to_inputs = os.path.join("..", "..", DIR_WORK, "inputs")

        # Build shell command
        cmd_parts = [
            "set -e", f"cd {shell_quote(skill_root)}", f"mkdir -p {shell_quote(to_inputs)}",
            f"ln -sfn {shell_quote(to_out)} out", f"ln -sfn {shell_quote(to_work)} work",
            f"ln -sfn {shell_quote(to_inputs)} inputs"
        ]
        workspace_runtime = self._get_repository(ctx).workspace_runtime
        runner = workspace_runtime.runner(ctx)
        ret = await runner.run_program(
            ws,
            WorkspaceRunProgramSpec(cmd="bash",
                                    args=["-lc", "; ".join(cmd_parts)],
                                    env={},
                                    cwd=".",
                                    timeout=self._timeout), ctx)
        if ret.exit_code != 0:
            logger.info("Failed to link workspace dirs: %s", ret.stderr)

    async def _read_only_except_symlinks(self, ctx: InvocationContext, ws: WorkspaceInfo, dest: str) -> None:
        """
        Remove write bits on all files except symlinks.

        Args:
            ctx: Context object
            eng: Engine instance
            ws: Workspace
            dest: Destination path

        Raises:
            Exception: If chmod fails
        """
        cmd = f"set -e; find {shell_quote(dest)} -type l -prune -o -exec chmod a-w {{}} +"

        workspace_runtime = self._get_repository(ctx).workspace_runtime
        runner = workspace_runtime.runner(ctx)
        ret = await runner.run_program(
            ws, WorkspaceRunProgramSpec(cmd="bash", args=["-lc", cmd], env={}, cwd=".", timeout=self._timeout), ctx)
        if ret.exit_code != 0:
            logger.info("Failed to make everything read-only except symlinks: %s", ret.stderr)

    async def _stage_skill(self, ctx: InvocationContext, ws: WorkspaceInfo, skill_root: str, skill_name: str) -> None:
        """
        Stage a skill directory into the workspace.

        Args:
            ctx: Context object
            eng: Engine instance
            ws: Workspace
            root: Root directory of skill
            name: Skill name

        Raises:
            Exception: If staging fails
        """
        digest = compute_dir_digest(skill_root)
        ensure_layout(ws.path)
        metadata = load_metadata(ws.path)
        if metadata.skills.get(skill_name, None) and metadata.skills[skill_name].digest == digest:
            return
        workspace_runtime = self._get_repository(ctx).workspace_runtime
        fs = workspace_runtime.fs(ctx)
        # Stage as a regular directory
        dst = Path(DIR_SKILLS) / skill_name
        await fs.stage_directory(ws, skill_root, dst.as_posix(), WorkspaceStageOptions(), ctx=ctx)

        # Link workspace-level dirs under the skill root
        await self._link_workspace_dirs(ctx, ws, skill_name)

        # Make everything read-only except symlinks
        await self._read_only_except_symlinks(ctx, ws, dst.as_posix())

        metadata.skills[skill_name] = SkillMetadata(name=skill_name,
                                                    rel_path=dst.as_posix(),
                                                    digest=digest,
                                                    mounted=True,
                                                    staged_at=time.time())

        save_metadata(ws.path, metadata)

    @override
    async def _run_async_impl(
        self,
        *,
        tool_context: InvocationContext,
        args: Dict[str, Any],
    ) -> Any:
        """Execute skill_run tool.
        """
        if self._run_tool_kwargs:
            for k, v in self._run_tool_kwargs.items():
                if k in SkillRunInput.model_fields:
                    args[k] = v
        try:
            inputs = SkillRunInput.model_validate(args)
        except Exception as ex:  # pylint: disable=broad-except
            raise ValueError(f"Invalid skill_run arguments: {ex}") from ex
        repository = self._get_repository(tool_context)
        skill_root = repository.path(inputs.skill)
        session_id = inputs.skill
        if tool_context.session and tool_context.session.id:
            session_id = tool_context.session.id
        workspace_runtime = repository.workspace_runtime
        manager = workspace_runtime.manager(tool_context)
        ws = await manager.create_workspace(session_id, tool_context)
        await self._stage_skill(tool_context, ws, skill_root, inputs.skill)
        if inputs.inputs:
            fs = workspace_runtime.fs(tool_context)
            await fs.stage_inputs(ws, inputs.inputs, tool_context)

        cwd = self._resolve_cwd(inputs.cwd, inputs.skill)
        result = await self._run_program(tool_context, ws, cwd, inputs)
        files, manifest = await self._prepare_outputs(tool_context, ws, inputs)
        output = SkillRunOutput(
            stdout=result.stdout,
            stderr=result.stderr,
            exit_code=result.exit_code,
            timed_out=result.timed_out,
            duration_ms=int(result.duration),
            output_files=files,
        )
        await self._attach_artifacts_if_requested(tool_context, ws, inputs, output, files)
        self._merge_manifest_artifact_refs(manifest, output)
        return output.model_dump()

    def _merge_manifest_artifact_refs(self, manifest: Optional[ManifestOutput], output: SkillRunOutput) -> None:
        """
        Append artifact refs derived from a manifest.

        Args:
            manifest: Output manifest
            output: SkillRun output to modify
        """
        if output.artifact_files or manifest is None:
            return

        for fr in manifest.files:
            if fr.saved_as:
                output.artifact_files.append(fr)

    async def _run_program(self, ctx: InvocationContext, ws: WorkspaceInfo, cwd: str,
                           input_data: SkillRunInput) -> WorkspaceRunResult:
        """
        Run the program.

        Args:
            ctx: Context object
            eng: Engine instance
            ws: Workspace
            cwd: Working directory
            input_data: Run input

        Returns:
            Run result
        """
        timeout = max(0, float(input_data.timeout)) or self._timeout
        env = input_data.env.copy() if input_data.env else {}

        if ENV_SKILL_NAME not in env:
            env[ENV_SKILL_NAME] = input_data.skill
        workspace_runtime = self._get_repository(ctx).workspace_runtime
        runner = workspace_runtime.runner(ctx)
        ret = await runner.run_program(
            ws,
            WorkspaceRunProgramSpec(cmd="bash",
                                    args=["-lc", f"{input_data.command}"],
                                    env=env,
                                    cwd=cwd,
                                    timeout=timeout), ctx)
        if ret.exit_code != 0:
            ret = self._with_missing_command_hint(ret, input_data)
            logger.info("Failed to run program: %s", ret.stderr)
        return ret

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
                "- Read the loaded `SKILL.md` and execute one of its exact shell examples (for example, `curl ...`).\n"
                "- If needed, call `skill_load` first so the full skill body is injected before calling `skill_run`.\n")
        return ret.model_copy(update={"stderr": f"{ret.stderr}{hint}"})

    def _resolve_cwd(self, cwd: str, name: str) -> str:
        """
        Resolve working directory.

        Args:
            cwd: Working directory (may be relative or absolute)
            name: Skill name

        Returns:
            Resolved working directory path
        """
        base = os.path.join(DIR_SKILLS, name)
        s = cwd.strip()

        if not s:
            return base

        if s.startswith("/"):
            return s

        # like $SKILLS_DIR/{name} or ${SKILLS_DIR}/{name} like environment variable format, return base
        if cwd.startswith(f"$SKILLS_DIR/{name}") or cwd.startswith(f"${{SKILLS_DIR}}/{name}"):
            return base

        # like ./{name} or ../{name} like relative path format, return os.path.join(base, cwd)
        if "$SKILLS_DIR" in s or "${SKILLS_DIR}" in s:
            skills_dir_rel = DIR_SKILLS
            s = s.replace("$SKILLS_DIR", skills_dir_rel).replace("${SKILLS_DIR}", skills_dir_rel)
            if s == base or s == os.path.join(DIR_SKILLS, name):
                return base

        return os.path.join(base, cwd)

    async def _prepare_outputs(self, ctx: InvocationContext, ws: WorkspaceInfo,
                               input_data: SkillRunInput) -> tuple[List[CodeFile], Optional[ManifestOutput]]:
        """
        Collect files either through OutputSpec or legacy output_files patterns.

        Args:
            ctx: Context object
            eng: Engine instance
            ws: Workspace
            input_data: Run input

        Returns:
            Tuple of (files, manifest)
        """
        files: List[CodeFile] = []
        manifest: Optional[ManifestOutput] = None

        workspace_runtime = self._get_repository(ctx).workspace_runtime
        fs = workspace_runtime.fs(ctx)
        if input_data.outputs and not input_data.output_files:
            manifest = await fs.collect_outputs(ws, input_data.outputs, ctx)

            if input_data.outputs.inline:
                for fr in manifest.files:
                    files.append(CodeFile(name=fr.name, content=fr.content, mime_type=fr.mime_type))

            return files, manifest
        if input_data.output_files:
            files = await fs.collect(ws, input_data.output_files, ctx)
            return files, None

        return files, None

    async def _attach_artifacts_if_requested(
        self,
        ctx: InvocationContext,
        ws: WorkspaceInfo,
        input_data: SkillRunInput,
        output: SkillRunOutput,
        files: List[CodeFile],
    ) -> None:
        """
        Save files as artifacts when requested.

        Args:
            ctx: Context object
            ws: Workspace
            input_data: Run input
            output: Run output to modify
            files: Files to save

        Raises:
            Exception: If saving fails
        """
        if not files:
            return

        if input_data.save_as_artifacts and ctx.artifact_service:
            refs = await self._save_artifacts(ctx, files, input_data.artifact_prefix)
            output.artifact_files = refs

            if input_data.omit_inline_content:
                for f in output.output_files:
                    f.content = ""

    async def _save_artifacts(self, ctx: InvocationContext, files: List[CodeFile], prefix: str) -> List[ArtifactInfo]:
        """
        Save files as artifacts.

        Args:
            ctx: Context object
            files: Files to save
            prefix: Name prefix

        Returns:
            List of artifact references

        Raises:
            Exception: If saving fails
        """
        refs = []
        for f in files:
            name = f.name
            if prefix:
                name = prefix + name
            artifact = Part.from_bytes(data=f.content.encode('utf-8'), mime_type=f.mime_type)
            version = await ctx.save_artifact(name, artifact)
            refs.append(ArtifactInfo(name=name, version=version))

        return refs
