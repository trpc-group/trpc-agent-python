# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.

from unittest.mock import AsyncMock
from unittest.mock import Mock
from unittest.mock import patch

import pytest
from trpc_agent_sdk.code_executors import BaseProgramRunner
from trpc_agent_sdk.code_executors import BaseWorkspaceFS
from trpc_agent_sdk.code_executors import BaseWorkspaceManager
from trpc_agent_sdk.code_executors import BaseWorkspaceRuntime
from trpc_agent_sdk.code_executors import CodeFile
from trpc_agent_sdk.code_executors import WorkspaceInfo
from trpc_agent_sdk.code_executors import WorkspaceRunResult
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.skills import BaseSkillRepository
from trpc_agent_sdk.skills.tools import ArtifactInfo
from trpc_agent_sdk.skills.tools import SkillRunFile
from trpc_agent_sdk.skills.tools import SkillRunInput
from trpc_agent_sdk.skills.tools import SkillRunOutput
from trpc_agent_sdk.skills.tools import SkillRunTool
from trpc_agent_sdk.skills.tools._skill_run import _inline_json_schema_refs


class TestInlineJsonSchemaRefs:
    """Test suite for _inline_json_schema_refs function."""

    def test_inline_json_schema_refs_no_refs(self):
        """Test inlining schema with no $ref references."""
        schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string"}
            }
        }

        result = _inline_json_schema_refs(schema)

        assert result == schema

    def test_inline_json_schema_refs_with_refs(self):
        """Test inlining schema with $ref references."""
        schema = {
            "type": "object",
            "properties": {
                "item": {"$ref": "#/$defs/Item"}
            },
            "$defs": {
                "Item": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"}
                    }
                }
            }
        }

        result = _inline_json_schema_refs(schema)

        assert "$defs" not in result
        assert "$ref" not in str(result)
        assert "name" in str(result)

    def test_inline_json_schema_refs_nested_refs(self):
        """Test inlining schema with nested $ref references."""
        schema = {
            "type": "object",
            "properties": {
                "item": {"$ref": "#/$defs/Item"}
            },
            "$defs": {
                "Item": {
                    "type": "object",
                    "properties": {
                        "nested": {"$ref": "#/$defs/Nested"}
                    }
                },
                "Nested": {
                    "type": "string"
                }
            }
        }

        result = _inline_json_schema_refs(schema)

        assert "$defs" not in result
        assert "$ref" not in str(result)


class TestSkillRunInput:
    """Test suite for SkillRunInput class."""

    def test_create_skill_run_input(self):
        """Test creating skill run input."""
        input_data = SkillRunInput(
            skill="test-skill",
            command="python script.py",
            cwd="work",
            env={"VAR": "value"},
            output_files=["out/*.txt"],
            timeout=30,
            save_as_artifacts=True,
        )

        assert input_data.skill == "test-skill"
        assert input_data.command == "python script.py"
        assert input_data.cwd == "work"
        assert input_data.env == {"VAR": "value"}
        assert input_data.output_files == ["out/*.txt"]
        assert input_data.timeout == 30
        assert input_data.save_as_artifacts is True

    def test_create_skill_run_input_defaults(self):
        """Test creating skill run input with defaults."""
        input_data = SkillRunInput(skill="test-skill", command="echo hello")

        assert input_data.skill == "test-skill"
        assert input_data.command == "echo hello"
        assert input_data.cwd == ""
        assert input_data.env == {}
        assert input_data.output_files == []
        assert input_data.timeout == 0
        assert input_data.save_as_artifacts is False


class TestSkillRunOutput:
    """Test suite for SkillRunOutput class."""

    def test_create_skill_run_output(self):
        """Test creating skill run output."""
        output_files = [SkillRunFile(name="output.txt", content="content", mime_type="text/plain")]
        artifact_files = [ArtifactInfo(name="artifact.txt", version=1)]

        output = SkillRunOutput(
            stdout="output",
            stderr="error",
            exit_code=0,
            timed_out=False,
            duration_ms=1000,
            output_files=output_files,
            artifact_files=artifact_files,
        )

        assert output.stdout == "output"
        assert output.stderr == "error"
        assert output.exit_code == 0
        assert output.timed_out is False
        assert output.duration_ms == 1000
        assert len(output.output_files) == 1
        assert len(output.artifact_files) == 1

    def test_create_skill_run_output_defaults(self):
        """Test creating skill run output with defaults."""
        output = SkillRunOutput()

        assert output.stdout == ""
        assert output.stderr == ""
        assert output.exit_code == 0
        assert output.timed_out is False
        assert output.duration_ms == 0
        assert output.output_files == []
        assert output.artifact_files == []


class TestArtifactInfo:
    """Test suite for ArtifactInfo class."""

    def test_create_artifact_info(self):
        """Test creating artifact info."""
        info = ArtifactInfo(name="artifact.txt", version=1)

        assert info.name == "artifact.txt"
        assert info.version == 1

    def test_create_artifact_info_defaults(self):
        """Test creating artifact info with defaults."""
        info = ArtifactInfo()

        assert info.name == ""
        assert info.version == 0


class TestSkillRunTool:
    """Test suite for SkillRunTool class."""

    def setup_method(self):
        """Set up test fixtures before each test."""
        self.mock_repository = Mock(spec=BaseSkillRepository)
        self.mock_runtime = Mock(spec=BaseWorkspaceRuntime)
        self.mock_manager = Mock(spec=BaseWorkspaceManager)
        self.mock_fs = Mock(spec=BaseWorkspaceFS)
        self.mock_runner = Mock(spec=BaseProgramRunner)
        self.mock_repository.workspace_runtime = self.mock_runtime
        self.mock_runtime.manager = Mock(return_value=self.mock_manager)
        self.mock_runtime.fs = Mock(return_value=self.mock_fs)
        self.mock_runtime.runner = Mock(return_value=self.mock_runner)

        self.mock_ctx = Mock(spec=InvocationContext)
        self.mock_ctx.agent_context = Mock()
        self.mock_ctx.agent_context.get_metadata = Mock(return_value=None)
        self.mock_ctx.session = Mock()
        self.mock_ctx.session.id = "session-123"
        self.mock_ctx.actions = Mock()
        self.mock_ctx.actions.state_delta = {}

    def test_init(self):
        """Test SkillRunTool initialization."""
        tool = SkillRunTool(repository=self.mock_repository)

        assert tool.name == "skill_run"
        assert tool._repository == self.mock_repository

    def test_get_declaration(self):
        """Test getting function declaration."""
        tool = SkillRunTool(repository=self.mock_repository)

        declaration = tool._get_declaration()

        assert declaration.name == "skill_run"
        assert declaration.parameters is not None
        assert declaration.response is not None

    def test_get_repository_from_instance(self):
        """Test getting repository from instance."""
        tool = SkillRunTool(repository=self.mock_repository)

        result = tool._get_repository(self.mock_ctx)

        assert result == self.mock_repository

    def test_get_repository_from_context(self):
        """Test getting repository from context."""
        tool = SkillRunTool(repository=None)
        self.mock_ctx.agent_context.get_metadata = Mock(return_value=self.mock_repository)

        result = tool._get_repository(self.mock_ctx)

        assert result == self.mock_repository

    @pytest.mark.asyncio
    async def test_run_async_impl_success(self):
        """Test running skill_run tool successfully."""
        from trpc_agent_sdk.skills.stager import SkillStageResult

        mock_stager = AsyncMock()
        mock_stager.stage_skill = AsyncMock(return_value=SkillStageResult(workspace_skill_dir="skills/test-skill"))
        tool = SkillRunTool(repository=self.mock_repository, skill_stager=mock_stager)

        workspace = WorkspaceInfo(id="ws-123", path="/tmp/workspace")
        self.mock_repository.path = Mock(return_value="/path/to/skill")
        self.mock_repository.skill_run_env = Mock(return_value={})
        self.mock_manager.create_workspace = AsyncMock(return_value=workspace)
        self.mock_fs.stage_directory = AsyncMock()
        self.mock_fs.stage_inputs = AsyncMock()
        self.mock_fs.collect_outputs = AsyncMock(return_value=Mock(files=[]))
        self.mock_runner.run_program = AsyncMock(return_value=WorkspaceRunResult(
            stdout="output",
            stderr="",
            exit_code=0,
            duration=1.0,
            timed_out=False
        ))

        args = {
            "skill": "test-skill",
            "command": "echo hello"
        }
        result = await tool._run_async_impl(tool_context=self.mock_ctx, args=args)

        assert isinstance(result, dict)
        assert result["stdout"] == "output"
        assert result["exit_code"] == 0

    @pytest.mark.asyncio
    async def test_run_async_impl_with_output_files(self):
        """Test running skill_run tool with output files."""
        from trpc_agent_sdk.skills.stager import SkillStageResult

        mock_stager = AsyncMock()
        mock_stager.stage_skill = AsyncMock(return_value=SkillStageResult(workspace_skill_dir="skills/test-skill"))
        tool = SkillRunTool(repository=self.mock_repository, skill_stager=mock_stager)

        workspace = WorkspaceInfo(id="ws-123", path="/tmp/workspace")
        self.mock_repository.path = Mock(return_value="/path/to/skill")
        self.mock_repository.skill_run_env = Mock(return_value={})
        self.mock_manager.create_workspace = AsyncMock(return_value=workspace)
        self.mock_fs.stage_directory = AsyncMock()
        self.mock_fs.stage_inputs = AsyncMock()
        self.mock_fs.collect = AsyncMock(return_value=[CodeFile(name="output.txt", content="content", mime_type="text/plain")])
        self.mock_runner.run_program = AsyncMock(return_value=WorkspaceRunResult(
            stdout="output",
            stderr="",
            exit_code=0,
            duration=1.0,
            timed_out=False
        ))

        from trpc_agent_sdk.code_executors._types import ManifestOutput, ManifestFileRef
        mock_output = ManifestOutput(files=[
            ManifestFileRef(name="output.txt", content="content", mime_type="text/plain")
        ])
        self.mock_fs.collect_outputs = AsyncMock(return_value=mock_output)

        args = {
            "skill": "test-skill",
            "command": "echo hello",
            "output_files": ["out/*.txt"]
        }
        result = await tool._run_async_impl(tool_context=self.mock_ctx, args=args)

        assert len(result["output_files"]) == 1

    @pytest.mark.asyncio
    async def test_run_async_impl_invalid_args(self):
        """Test running skill_run tool with invalid arguments."""
        tool = SkillRunTool(repository=self.mock_repository)

        args = {
            "skill": "test-skill",
            # Missing required 'command' field
        }

        with pytest.raises(ValueError, match="Invalid skill_run arguments"):
            await tool._run_async_impl(tool_context=self.mock_ctx, args=args)

    @pytest.mark.asyncio
    async def test_run_async_impl_with_kwargs(self):
        """Test running skill_run tool with kwargs."""
        from trpc_agent_sdk.skills.stager import SkillStageResult

        mock_stager = AsyncMock()
        mock_stager.stage_skill = AsyncMock(return_value=SkillStageResult(workspace_skill_dir="skills/test-skill"))
        tool = SkillRunTool(repository=self.mock_repository, timeout=30, skill_stager=mock_stager)

        workspace = WorkspaceInfo(id="ws-123", path="/tmp/workspace")
        self.mock_repository.path = Mock(return_value="/path/to/skill")
        self.mock_repository.skill_run_env = Mock(return_value={})
        self.mock_manager.create_workspace = AsyncMock(return_value=workspace)
        self.mock_fs.stage_directory = AsyncMock()
        self.mock_fs.stage_inputs = AsyncMock()
        self.mock_fs.collect_outputs = AsyncMock(return_value=Mock(files=[]))
        self.mock_runner.run_program = AsyncMock(return_value=WorkspaceRunResult(
            stdout="output",
            stderr="",
            exit_code=0,
            duration=1.0,
            timed_out=False
        ))

        args = {
            "skill": "test-skill",
            "command": "echo hello"
        }

        result = await tool._run_async_impl(tool_context=self.mock_ctx, args=args)

        assert isinstance(result, dict)
        assert result["stdout"] == "output"
        assert result["exit_code"] == 0

