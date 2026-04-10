# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Types for code executors."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel
from pydantic import Field
from trpc_agent_sdk.types import CodeExecutionResult
from trpc_agent_sdk.types import Outcome


class CodeFile(BaseModel):
    """Code file structure for code execution."""

    name: str
    """File name."""

    content: str
    """File content."""

    mime_type: str
    """MIME type of the file."""

    size_bytes: int = 0
    """File size in bytes."""

    truncated: bool = False
    """Whether the file is truncated."""


class CodeBlock(BaseModel):
    """Represents a single block of code to be executed.

    Attributes:
        code: The code content to execute
        language: Programming language (python, bash, etc.)
    """
    language: str = ""
    """ programming language (python, bash, etc.)"""

    code: str = ""
    """ code content to execute"""


class CodeBlockDelimiter(BaseModel):
    """Defines the start and end delimiters for code blocks.

    Attributes:
        start: Start delimiter (e.g., "```")
        end: End delimiter (e.g., "```")
    """
    start: str = "```"
    """ start delimiter (e.g., "```")"""

    end: str = "```"
    """ end delimiter (e.g., "```")"""


class CodeExecutionInput(BaseModel):
    """Input for code execution."""

    code_blocks: list[CodeBlock] = Field(default_factory=list)
    """ list of code blocks to execute"""

    code: str = ""
    """ code to execute"""

    input_files: list[CodeFile] = Field(default_factory=list)
    """ list of input files for the code execution"""

    execution_id: Optional[str] = None
    """ execution id for stateful code execution"""


class WorkspaceInfo(BaseModel):
    """
    Represents an isolated execution workspace.

    Path is a workspace path.
    """

    id: str = ""
    """ workspace id"""

    path: str = ""
    """ workspace path"""


class WorkspacePutFileInfo(BaseModel):
    """
    Describes a file to place into a workspace.
    """

    path: str = ""
    """ file path"""

    content: bytes = b""
    """ file content"""

    mode: int = 0
    """ file mode"""


class WorkspaceResourceLimits(BaseModel):
    """
    Restricts program execution resources.
    """

    cpu_percent: int = 0
    """ cpu percent"""

    memory_mb: int = 0
    """ memory in mb"""

    max_pids: int = 0
    """ maximum number of pids"""


class WorkspaceRunProgramSpec(BaseModel):
    """
    Describes a program invocation in a workspace.
    """

    cmd: str = ""
    """ command to execute"""

    args: list[str] = Field(default_factory=list)
    """ list of arguments to execute"""

    env: dict[str, str] = Field(default_factory=dict)
    """ environment variables to execute"""

    cwd: str = ""
    """ current working directory"""

    stdin: str = ""
    """ stdin to execute"""

    timeout: float = 0
    """ timeout in seconds"""

    limits: WorkspaceResourceLimits = Field(default_factory=WorkspaceResourceLimits)
    """ resource limits"""

    tty: bool = Field(default=False, description="Allocate pseudo-TTY")
    """ whether to allocate pseudo-TTY"""


class WorkspaceRunResult(BaseModel):
    """
    Captures a single program run result.
    """

    stdout: str = ""
    """ standard output"""

    stderr: str = ""
    """ standard error"""

    exit_code: int = 0
    """ exit code"""

    duration: float = 0
    """ duration in seconds"""

    timed_out: bool = False
    """ whether timed out"""


class WorkspaceStageOptions(BaseModel):
    """
    Controls directory staging behavior.
    """

    read_only: bool = False
    """ whether to read only the mount"""

    allow_mount: bool = False
    """ whether to allow mount"""


class WorkspaceCapabilities(BaseModel):
    """
    Describes workspace capabilities for selection.
    """

    isolation: str = ""
    """ isolation type"""

    network_allowed: bool = False
    """ whether to allow network"""

    read_only_mount: bool = False
    """ whether to read only the mount"""

    streaming: bool = False
    """ whether to allow streaming"""

    max_disk_bytes: int = 0
    """ maximum disk space to use"""


class WorkspaceInputSpec(BaseModel):
    """
    Declares a single input mapping into the workspace.

    From supports schemes:
      - artifact://name[@version]
      - host://abs/path
      - workspace://rel/path
      - skill://name/rel/path

    To is a workspace-relative destination (default: WORK_DIR/inputs/<name>).
    Mode hints the strategy: "link" (symlink/hardlink where possible) or
    "copy" (default fallback when link is not possible).
    """

    src: str = ""
    """ source path"""

    dst: str = ""
    """ destination path"""

    mode: str = ""
    """ mode to use for the input"""

    pin: bool = False
    """ whether to pin the input"""


class WorkspaceOutputSpec(BaseModel):
    """
    Declares outputs to collect and optionally persist.

    Globs are workspace-relative patterns; implementations should
    support ** semantics.
    """

    globs: list[str] = Field(default_factory=list)
    """ list of glob patterns to collect"""

    max_files: int = 0
    """ maximum number of files to collect"""

    max_file_bytes: int = 0
    """ maximum file size to collect"""

    max_total_bytes: int = 0
    """ maximum total size to collect"""

    save: bool = False
    """ whether to save the output"""

    name_template: str = ""
    """ name template for the output"""

    inline: bool = False
    """ whether to inline the output"""


class ManifestFileRef(BaseModel):
    """
    References a file collected from workspace.
    """

    name: str = ""
    """ file name"""

    mime_type: str = ""
    """ mime type"""

    content: str = ""
    """ content"""

    saved_as: str = ""
    """ saved as"""

    version: int = 0
    """ version"""


class ManifestOutput(BaseModel):
    """
    The structured result of CollectOutputs.
    """

    files: list[ManifestFileRef] = Field(default_factory=list)
    """ list of files"""

    limits_hit: bool = False
    """ whether limits hit"""


def create_code_execution_result(stdout: str = '',
                                 stderr: str = '',
                                 output_files: Optional[list[CodeFile]] = None,
                                 is_timed_out: bool = False) -> CodeExecutionResult:
    """Create a code execution result.

    Args:
      stdout: The standard output of the code execution.
      stderr: The standard error of the code execution.
      output_files: The output files of the code execution.
      is_timed_out: Whether the code execution timed out.

    Returns:
      The code execution result.
    """
    if output_files is None:
        output_files = []
    out_str = ''
    outcome = Outcome.OUTCOME_OK
    if stderr:
        out_str = f"Code execution error:\n{stderr}\n"
        outcome = Outcome.OUTCOME_FAILED
    if is_timed_out:
        out_str += "Code execution timed out\n"
        outcome = Outcome.OUTCOME_TIMED_OUT
    if stdout:
        out_str += f"Code execution result:\n{stdout}\n"
    if output_files:
        out_str += "Saved artifacts:\n" + ",".join([f"`{f.name}`" for f in output_files])

    return CodeExecutionResult(outcome=outcome, output=out_str)
