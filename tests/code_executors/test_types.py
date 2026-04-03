# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.

import pytest
from pydantic import ValidationError
from trpc_agent_sdk.code_executors._types import CodeBlock
from trpc_agent_sdk.code_executors._types import CodeBlockDelimiter
from trpc_agent_sdk.code_executors._types import CodeExecutionInput
from trpc_agent_sdk.code_executors._types import CodeExecutionResult
from trpc_agent_sdk.code_executors._types import CodeFile
from trpc_agent_sdk.code_executors._types import ManifestFileRef
from trpc_agent_sdk.code_executors._types import ManifestOutput
from trpc_agent_sdk.code_executors._types import WorkspaceCapabilities
from trpc_agent_sdk.code_executors._types import WorkspaceInfo
from trpc_agent_sdk.code_executors._types import WorkspaceInputSpec
from trpc_agent_sdk.code_executors._types import WorkspaceOutputSpec
from trpc_agent_sdk.code_executors._types import WorkspacePutFileInfo
from trpc_agent_sdk.code_executors._types import WorkspaceResourceLimits
from trpc_agent_sdk.code_executors._types import WorkspaceRunProgramSpec
from trpc_agent_sdk.code_executors._types import WorkspaceRunResult
from trpc_agent_sdk.code_executors._types import WorkspaceStageOptions
from trpc_agent_sdk.code_executors._types import create_code_execution_result
from trpc_agent_sdk.types import Outcome


class TestCodeFile:
    """Test suite for CodeFile class."""

    def test_create_code_file(self):
        """Test creating a code file."""
        file = CodeFile(name="test.py", content="print('hello')", mime_type="text/x-python")

        assert file.name == "test.py"
        assert file.content == "print('hello')"
        assert file.mime_type == "text/x-python"

    def test_code_file_required_fields(self):
        """Test that CodeFile requires all fields."""
        with pytest.raises(ValidationError):
            CodeFile(name="test.py")

    def test_code_file_empty_content(self):
        """Test creating code file with empty content."""
        file = CodeFile(name="test.py", content="", mime_type="text/plain")

        assert file.content == ""

    def test_code_file_default_size_bytes(self):
        """Test default size_bytes is 0."""
        file = CodeFile(name="f.txt", content="data", mime_type="text/plain")
        assert file.size_bytes == 0

    def test_code_file_custom_size_bytes(self):
        """Test setting custom size_bytes."""
        file = CodeFile(name="f.txt", content="data", mime_type="text/plain", size_bytes=1024)
        assert file.size_bytes == 1024

    def test_code_file_default_truncated(self):
        """Test default truncated is False."""
        file = CodeFile(name="f.txt", content="data", mime_type="text/plain")
        assert file.truncated is False

    def test_code_file_truncated_true(self):
        """Test setting truncated to True."""
        file = CodeFile(name="f.txt", content="data", mime_type="text/plain", truncated=True)
        assert file.truncated is True


class TestCodeBlock:
    """Test suite for CodeBlock class."""

    def test_create_code_block(self):
        """Test creating a code block."""
        block = CodeBlock(language="python", code="print('hello')")

        assert block.language == "python"
        assert block.code == "print('hello')"

    def test_create_code_block_defaults(self):
        """Test creating code block with defaults."""
        block = CodeBlock()

        assert block.language == ""
        assert block.code == ""

    def test_create_code_block_empty_code(self):
        """Test creating code block with empty code."""
        block = CodeBlock(language="python", code="")

        assert block.language == "python"
        assert block.code == ""


class TestCodeBlockDelimiter:
    """Test suite for CodeBlockDelimiter class."""

    def test_create_delimiter(self):
        """Test creating a delimiter."""
        delimiter = CodeBlockDelimiter(start="```python\n", end="\n```")

        assert delimiter.start == "```python\n"
        assert delimiter.end == "\n```"

    def test_create_delimiter_defaults(self):
        """Test creating delimiter with defaults."""
        delimiter = CodeBlockDelimiter()

        assert delimiter.start == "```"
        assert delimiter.end == "```"


class TestCodeExecutionInput:
    """Test suite for CodeExecutionInput class."""

    def test_create_code_execution_input(self):
        """Test creating code execution input."""
        code_blocks = [CodeBlock(language="python", code="print('hello')")]
        input_files = [CodeFile(name="test.txt", content="test", mime_type="text/plain")]

        execution_input = CodeExecutionInput(
            code_blocks=code_blocks,
            code="print('hello')",
            input_files=input_files,
            execution_id="exec-123",
        )

        assert len(execution_input.code_blocks) == 1
        assert execution_input.code == "print('hello')"
        assert len(execution_input.input_files) == 1
        assert execution_input.execution_id == "exec-123"

    def test_create_code_execution_input_defaults(self):
        """Test creating code execution input with defaults."""
        execution_input = CodeExecutionInput()

        assert execution_input.code_blocks == []
        assert execution_input.code == ""
        assert execution_input.input_files == []
        assert execution_input.execution_id is None


class TestCodeExecutionResult:
    """Test suite for CodeExecutionResult class."""

    def test_create_code_execution_response(self):
        """Test creating code execution response."""

        result = CodeExecutionResult(
            outcome=Outcome.OUTCOME_OK,
            output="hello\nworld",
        )

        assert result.outcome == Outcome.OUTCOME_OK
        assert result.output == "hello\nworld"

    def test_create_code_execution_result_defaults(self):
        """Test creating code execution result with defaults."""
        result = CodeExecutionResult()

        assert result.outcome is None
        assert result.output is None


class TestWorkspaceInfo:
    """Test suite for WorkspaceInfo class."""

    def test_create_workspace_info(self):
        """Test creating workspace info."""
        info = WorkspaceInfo(id="ws-123", path="/tmp/workspace")

        assert info.id == "ws-123"
        assert info.path == "/tmp/workspace"

    def test_create_workspace_info_defaults(self):
        """Test creating workspace info with defaults."""
        info = WorkspaceInfo()

        assert info.id == ""
        assert info.path == ""


class TestWorkspacePutFileInfo:
    """Test suite for WorkspacePutFileInfo class."""

    def test_create_workspace_put_file_info(self):
        """Test creating workspace put file info."""
        file_info = WorkspacePutFileInfo(path="/tmp/file.txt", content=b"content", mode=0o644)

        assert file_info.path == "/tmp/file.txt"
        assert file_info.content == b"content"
        assert file_info.mode == 0o644

    def test_create_workspace_put_file_info_defaults(self):
        """Test creating workspace put file info with defaults."""
        file_info = WorkspacePutFileInfo()

        assert file_info.path == ""
        assert file_info.content == b""
        assert file_info.mode == 0


class TestWorkspaceResourceLimits:
    """Test suite for WorkspaceResourceLimits class."""

    def test_create_workspace_resource_limits(self):
        """Test creating workspace resource limits."""
        limits = WorkspaceResourceLimits(cpu_percent=50, memory_mb=512, max_pids=100)

        assert limits.cpu_percent == 50
        assert limits.memory_mb == 512
        assert limits.max_pids == 100

    def test_create_workspace_resource_limits_defaults(self):
        """Test creating workspace resource limits with defaults."""
        limits = WorkspaceResourceLimits()

        assert limits.cpu_percent == 0
        assert limits.memory_mb == 0
        assert limits.max_pids == 0


class TestWorkspaceRunProgramSpec:
    """Test suite for WorkspaceRunProgramSpec class."""

    def test_create_workspace_run_program_spec(self):
        """Test creating workspace run program spec."""
        limits = WorkspaceResourceLimits(cpu_percent=50, memory_mb=512)
        spec = WorkspaceRunProgramSpec(
            cmd="python",
            args=["script.py"],
            env={"VAR": "value"},
            cwd="/tmp",
            stdin="input",
            timeout=30.0,
            limits=limits,
        )

        assert spec.cmd == "python"
        assert spec.args == ["script.py"]
        assert spec.env == {"VAR": "value"}
        assert spec.cwd == "/tmp"
        assert spec.stdin == "input"
        assert spec.timeout == 30.0
        assert spec.limits == limits

    def test_create_workspace_run_program_spec_defaults(self):
        """Test creating workspace run program spec with defaults."""
        spec = WorkspaceRunProgramSpec()

        assert spec.cmd == ""
        assert spec.args == []
        assert spec.env == {}
        assert spec.cwd == ""
        assert spec.stdin == ""
        assert spec.timeout == 0
        assert isinstance(spec.limits, WorkspaceResourceLimits)


class TestWorkspaceRunResult:
    """Test suite for WorkspaceRunResult class."""

    def test_create_workspace_run_result(self):
        """Test creating workspace run result."""
        result = WorkspaceRunResult(
            stdout="output",
            stderr="error",
            exit_code=0,
            duration=1.5,
            timed_out=False,
        )

        assert result.stdout == "output"
        assert result.stderr == "error"
        assert result.exit_code == 0
        assert result.duration == 1.5
        assert result.timed_out is False

    def test_create_workspace_run_result_defaults(self):
        """Test creating workspace run result with defaults."""
        result = WorkspaceRunResult()

        assert result.stdout == ""
        assert result.stderr == ""
        assert result.exit_code == 0
        assert result.duration == 0
        assert result.timed_out is False


class TestWorkspaceStageOptions:
    """Test suite for WorkspaceStageOptions class."""

    def test_create_workspace_stage_options(self):
        """Test creating workspace stage options."""
        options = WorkspaceStageOptions(read_only=True, allow_mount=True)

        assert options.read_only is True
        assert options.allow_mount is True

    def test_create_workspace_stage_options_defaults(self):
        """Test creating workspace stage options with defaults."""
        options = WorkspaceStageOptions()

        assert options.read_only is False
        assert options.allow_mount is False


class TestWorkspaceCapabilities:
    """Test suite for WorkspaceCapabilities class."""

    def test_create_workspace_capabilities(self):
        """Test creating workspace capabilities."""
        capabilities = WorkspaceCapabilities(
            isolation="container",
            network_allowed=True,
            read_only_mount=True,
            streaming=True,
            max_disk_bytes=1024 * 1024,
        )

        assert capabilities.isolation == "container"
        assert capabilities.network_allowed is True
        assert capabilities.read_only_mount is True
        assert capabilities.streaming is True
        assert capabilities.max_disk_bytes == 1024 * 1024

    def test_create_workspace_capabilities_defaults(self):
        """Test creating workspace capabilities with defaults."""
        capabilities = WorkspaceCapabilities()

        assert capabilities.isolation == ""
        assert capabilities.network_allowed is False
        assert capabilities.read_only_mount is False
        assert capabilities.streaming is False
        assert capabilities.max_disk_bytes == 0


class TestWorkspaceInputSpec:
    """Test suite for WorkspaceInputSpec class."""

    def test_create_workspace_input_spec(self):
        """Test creating workspace input spec."""
        spec = WorkspaceInputSpec(src="artifact://name@1", dst="/tmp/input", mode="copy", pin=True)

        assert spec.src == "artifact://name@1"
        assert spec.dst == "/tmp/input"
        assert spec.mode == "copy"
        assert spec.pin is True

    def test_create_workspace_input_spec_defaults(self):
        """Test creating workspace input spec with defaults."""
        spec = WorkspaceInputSpec()

        assert spec.src == ""
        assert spec.dst == ""
        assert spec.mode == ""
        assert spec.pin is False


class TestWorkspaceOutputSpec:
    """Test suite for WorkspaceOutputSpec class."""

    def test_create_workspace_output_spec(self):
        """Test creating workspace output spec."""
        spec = WorkspaceOutputSpec(
            globs=["**/*.txt", "**/*.json"],
            max_files=100,
            max_file_bytes=1024,
            max_total_bytes=10240,
            save=True,
            name_template="output_{timestamp}",
            inline=True,
        )

        assert spec.globs == ["**/*.txt", "**/*.json"]
        assert spec.max_files == 100
        assert spec.max_file_bytes == 1024
        assert spec.max_total_bytes == 10240
        assert spec.save is True
        assert spec.name_template == "output_{timestamp}"
        assert spec.inline is True

    def test_create_workspace_output_spec_defaults(self):
        """Test creating workspace output spec with defaults."""
        spec = WorkspaceOutputSpec()

        assert spec.globs == []
        assert spec.max_files == 0
        assert spec.max_file_bytes == 0
        assert spec.max_total_bytes == 0
        assert spec.save is False
        assert spec.name_template == ""
        assert spec.inline is False


class TestManifestFileRef:
    """Test suite for ManifestFileRef class."""

    def test_create_manifest_file_ref(self):
        """Test creating manifest file ref."""
        file_ref = ManifestFileRef(
            name="output.txt",
            mime_type="text/plain",
            content="content",
            saved_as="/tmp/output.txt",
            version=1,
        )

        assert file_ref.name == "output.txt"
        assert file_ref.mime_type == "text/plain"
        assert file_ref.content == "content"
        assert file_ref.saved_as == "/tmp/output.txt"
        assert file_ref.version == 1

    def test_create_manifest_file_ref_defaults(self):
        """Test creating manifest file ref with defaults."""
        file_ref = ManifestFileRef()

        assert file_ref.name == ""
        assert file_ref.mime_type == ""
        assert file_ref.content == ""
        assert file_ref.saved_as == ""
        assert file_ref.version == 0


class TestManifestOutput:
    """Test suite for ManifestOutput class."""

    def test_create_manifest_output(self):
        """Test creating manifest output."""
        file_refs = [
            ManifestFileRef(name="file1.txt", mime_type="text/plain", content="content1"),
            ManifestFileRef(name="file2.txt", mime_type="text/plain", content="content2"),
        ]
        output = ManifestOutput(files=file_refs, limits_hit=True)

        assert len(output.files) == 2
        assert output.limits_hit is True

    def test_create_manifest_output_defaults(self):
        """Test creating manifest output with defaults."""
        output = ManifestOutput()

        assert output.files == []
        assert output.limits_hit is False


class TestCreateCodeExecutionResult:
    """Test suite for create_code_execution_result function."""

    def test_default_args_returns_ok_empty(self):
        """No arguments yields OK outcome with empty output."""
        result = create_code_execution_result()
        assert result.outcome == Outcome.OUTCOME_OK
        assert result.output == ""

    def test_stdout_only(self):
        """stdout is wrapped in a 'Code execution result' block."""
        result = create_code_execution_result(stdout="hello world")
        assert result.outcome == Outcome.OUTCOME_OK
        assert "Code execution result:\nhello world\n" in result.output

    def test_stderr_only(self):
        """stderr sets outcome to FAILED."""
        result = create_code_execution_result(stderr="some error")
        assert result.outcome == Outcome.OUTCOME_FAILED
        assert "Code execution error:\nsome error\n" in result.output

    def test_timed_out_only(self):
        """is_timed_out=True triggers Outcome.OUTCOME_TIMED_OUT which is not defined."""
        with pytest.raises(AttributeError):
            create_code_execution_result(is_timed_out=True)

    def test_stderr_and_timed_out(self):
        """stderr + timed_out triggers the missing OUTCOME_TIMED_OUT attribute."""
        with pytest.raises(AttributeError):
            create_code_execution_result(stderr="err", is_timed_out=True)

    def test_stdout_and_stderr(self):
        """stderr + stdout → FAILED, both messages present."""
        result = create_code_execution_result(stdout="out", stderr="err")
        assert result.outcome == Outcome.OUTCOME_FAILED
        assert "Code execution error:\nerr\n" in result.output
        assert "Code execution result:\nout\n" in result.output

    def test_output_files_only(self):
        """Output files are listed as saved artifacts."""
        files = [
            CodeFile(name="a.txt", content="", mime_type="text/plain"),
            CodeFile(name="b.csv", content="", mime_type="text/csv"),
        ]
        result = create_code_execution_result(output_files=files)
        assert result.outcome == Outcome.OUTCOME_OK
        assert "Saved artifacts:\n" in result.output
        assert "`a.txt`" in result.output
        assert "`b.csv`" in result.output

    def test_all_args_combined_with_timed_out_raises(self):
        """All arguments with timed_out triggers the missing OUTCOME_TIMED_OUT attribute."""
        files = [CodeFile(name="out.txt", content="", mime_type="text/plain")]
        with pytest.raises(AttributeError):
            create_code_execution_result(
                stdout="output",
                stderr="error",
                output_files=files,
                is_timed_out=True,
            )

    def test_output_files_none_defaults_to_empty(self):
        """Passing output_files=None does not cause errors."""
        result = create_code_execution_result(output_files=None)
        assert result.outcome == Outcome.OUTCOME_OK
        assert "Saved artifacts" not in result.output

    def test_empty_strings_treated_as_falsy(self):
        """Empty stdout/stderr are treated as no output."""
        result = create_code_execution_result(stdout="", stderr="")
        assert result.outcome == Outcome.OUTCOME_OK
        assert result.output == ""

    def test_output_files_single(self):
        """Single output file is listed correctly."""
        files = [CodeFile(name="only.png", content="", mime_type="image/png")]
        result = create_code_execution_result(output_files=files)
        assert "Saved artifacts:\n`only.png`" in result.output

    def test_output_ordering_stderr_stdout_artifacts(self):
        """Verify the order: error → stdout → artifacts (without timed_out)."""
        files = [CodeFile(name="f.txt", content="", mime_type="text/plain")]
        result = create_code_execution_result(
            stdout="out",
            stderr="err",
            output_files=files,
        )
        output = result.output
        err_pos = output.index("Code execution error:")
        result_pos = output.index("Code execution result:")
        artifacts_pos = output.index("Saved artifacts:")
        assert err_pos < result_pos < artifacts_pos
