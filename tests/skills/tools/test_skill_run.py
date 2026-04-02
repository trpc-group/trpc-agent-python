# -*- coding: utf-8 -*-
#
# Copyright @ 2025 Tencent.com
"""Unit tests for trpc_agent_sdk.skills.tools._skill_run.

Covers:
- Module-level helpers:
  _inline_json_schema_refs, _is_text_mime, _should_inline_file_content,
  _truncate_output, _workspace_ref, _filter_failed_empty_outputs,
  _select_primary_output, _split_command_line, _build_editor_wrapper_script
- Pydantic models: SkillRunFile, SkillRunInput, SkillRunOutput, ArtifactInfo
- SkillRunTool:
  _resolve_cwd, _build_command, _wrap_with_venv, _is_skill_loaded,
  _get_repository, _is_missing_command_result, _extract_command_path_candidates,
  _extract_shell_examples_from_skill_body, _with_missing_command_hint
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from trpc_agent_sdk.skills.tools._skill_run import (
    ArtifactInfo,
    SkillRunFile,
    SkillRunInput,
    SkillRunOutput,
    SkillRunTool,
    _build_editor_wrapper_script,
    _filter_failed_empty_outputs,
    _inline_json_schema_refs,
    _is_text_mime,
    _select_primary_output,
    _should_inline_file_content,
    _split_command_line,
    _truncate_output,
    _workspace_ref,
)


# ---------------------------------------------------------------------------
# _inline_json_schema_refs
# ---------------------------------------------------------------------------

class TestInlineJsonSchemaRefs:
    def test_no_refs(self):
        schema = {"type": "object", "properties": {"name": {"type": "string"}}}
        result = _inline_json_schema_refs(schema)
        assert result == schema

    def test_with_refs(self):
        schema = {
            "$defs": {"Foo": {"type": "object", "properties": {"x": {"type": "integer"}}}},
            "properties": {"foo": {"$ref": "#/$defs/Foo"}},
        }
        result = _inline_json_schema_refs(schema)
        assert "$defs" not in result
        assert result["properties"]["foo"]["type"] == "object"

    def test_nested_refs(self):
        schema = {
            "$defs": {"Bar": {"type": "string"}},
            "properties": {"items": {"type": "array", "items": {"$ref": "#/$defs/Bar"}}},
        }
        result = _inline_json_schema_refs(schema)
        assert result["properties"]["items"]["items"]["type"] == "string"


# ---------------------------------------------------------------------------
# _is_text_mime
# ---------------------------------------------------------------------------

class TestIsTextMime:
    def test_text_plain(self):
        assert _is_text_mime("text/plain") is True

    def test_text_html(self):
        assert _is_text_mime("text/html") is True

    def test_application_json(self):
        assert _is_text_mime("application/json") is True

    def test_application_yaml(self):
        assert _is_text_mime("application/yaml") is True

    def test_application_xml(self):
        assert _is_text_mime("application/xml") is True

    def test_image_png(self):
        assert _is_text_mime("image/png") is False

    def test_empty_string_is_text(self):
        assert _is_text_mime("") is True

    def test_with_charset(self):
        assert _is_text_mime("application/json; charset=utf-8") is True

    def test_octet_stream(self):
        assert _is_text_mime("application/octet-stream") is False


# ---------------------------------------------------------------------------
# _should_inline_file_content
# ---------------------------------------------------------------------------

class TestShouldInlineFileContent:
    def test_text_file(self):
        from trpc_agent_sdk.code_executors import CodeFile
        f = CodeFile(name="test.txt", content="hello", mime_type="text/plain", size_bytes=5)
        assert _should_inline_file_content(f) is True

    def test_binary_file(self):
        from trpc_agent_sdk.code_executors import CodeFile
        f = CodeFile(name="test.png", content="data", mime_type="image/png", size_bytes=4)
        assert _should_inline_file_content(f) is False

    def test_null_bytes_rejected(self):
        from trpc_agent_sdk.code_executors import CodeFile
        f = CodeFile(name="test.txt", content="hello\x00world", mime_type="text/plain", size_bytes=11)
        assert _should_inline_file_content(f) is False

    def test_empty_content(self):
        from trpc_agent_sdk.code_executors import CodeFile
        f = CodeFile(name="empty.txt", content="", mime_type="text/plain", size_bytes=0)
        assert _should_inline_file_content(f) is True


# ---------------------------------------------------------------------------
# _truncate_output
# ---------------------------------------------------------------------------

class TestTruncateOutput:
    def test_short_string(self):
        s, truncated = _truncate_output("hello")
        assert s == "hello"
        assert truncated is False

    def test_long_string(self):
        s, truncated = _truncate_output("x" * 20000)
        assert truncated is True
        assert len(s) <= 16 * 1024

    def test_exact_limit(self):
        s, truncated = _truncate_output("x" * (16 * 1024))
        assert truncated is False


# ---------------------------------------------------------------------------
# _workspace_ref
# ---------------------------------------------------------------------------

class TestWorkspaceRef:
    def test_with_name(self):
        assert _workspace_ref("out/file.txt") == "workspace://out/file.txt"

    def test_empty_name(self):
        assert _workspace_ref("") == ""


# ---------------------------------------------------------------------------
# _filter_failed_empty_outputs
# ---------------------------------------------------------------------------

class TestFilterFailedEmptyOutputs:
    def test_success_no_filter(self):
        files = [SkillRunFile(name="a.txt", content="data", size_bytes=4)]
        result, warns = _filter_failed_empty_outputs(0, False, files)
        assert len(result) == 1
        assert warns == []

    def test_failure_removes_empty(self):
        files = [
            SkillRunFile(name="a.txt", content="data", size_bytes=4),
            SkillRunFile(name="empty.txt", content="", size_bytes=0),
        ]
        result, warns = _filter_failed_empty_outputs(1, False, files)
        assert len(result) == 1
        assert result[0].name == "a.txt"
        assert len(warns) == 1

    def test_failure_all_have_content(self):
        files = [SkillRunFile(name="a.txt", content="data", size_bytes=4)]
        result, warns = _filter_failed_empty_outputs(1, False, files)
        assert len(result) == 1
        assert warns == []

    def test_timeout_removes_empty(self):
        files = [SkillRunFile(name="e.txt", content="", size_bytes=0)]
        result, warns = _filter_failed_empty_outputs(0, True, files)
        assert len(result) == 0
        assert len(warns) == 1


# ---------------------------------------------------------------------------
# _select_primary_output
# ---------------------------------------------------------------------------

class TestSelectPrimaryOutput:
    def test_picks_smallest_text_file(self):
        files = [
            SkillRunFile(name="b.txt", content="bbb", mime_type="text/plain"),
            SkillRunFile(name="a.txt", content="aaa", mime_type="text/plain"),
        ]
        result = _select_primary_output(files)
        assert result.name == "a.txt"

    def test_skips_empty_content(self):
        files = [SkillRunFile(name="a.txt", content="", mime_type="text/plain")]
        result = _select_primary_output(files)
        assert result is None

    def test_skips_binary(self):
        files = [SkillRunFile(name="a.png", content="data", mime_type="image/png")]
        result = _select_primary_output(files)
        assert result is None

    def test_empty_files(self):
        assert _select_primary_output([]) is None

    def test_skips_too_large(self):
        content = "x" * (32 * 1024 + 1)
        files = [SkillRunFile(name="big.txt", content=content, mime_type="text/plain")]
        assert _select_primary_output(files) is None


# ---------------------------------------------------------------------------
# _split_command_line
# ---------------------------------------------------------------------------

class TestSplitCommandLine:
    def test_simple_command(self):
        assert _split_command_line("python run.py") == ["python", "run.py"]

    def test_quoted_args(self):
        result = _split_command_line("echo 'hello world'")
        assert result == ["echo", "hello world"]

    def test_double_quoted(self):
        result = _split_command_line('echo "hello world"')
        assert result == ["echo", "hello world"]

    def test_empty_raises(self):
        with pytest.raises(ValueError, match="empty"):
            _split_command_line("")

    def test_whitespace_only_raises(self):
        with pytest.raises(ValueError, match="empty"):
            _split_command_line("   ")

    def test_shell_metachar_rejected(self):
        with pytest.raises(ValueError, match="metacharacter"):
            _split_command_line("cmd1 | cmd2")

    def test_semicolon_rejected(self):
        with pytest.raises(ValueError, match="metacharacter"):
            _split_command_line("cmd1; cmd2")

    def test_redirect_rejected(self):
        with pytest.raises(ValueError, match="metacharacter"):
            _split_command_line("cmd > file")

    def test_unterminated_quote_raises(self):
        with pytest.raises(ValueError, match="unterminated"):
            _split_command_line("echo 'hello")

    def test_trailing_escape_raises(self):
        with pytest.raises(ValueError, match="trailing"):
            _split_command_line("echo hello\\")

    def test_escaped_char(self):
        result = _split_command_line("echo hello\\ world")
        assert result == ["echo", "hello world"]

    def test_newline_rejected(self):
        with pytest.raises(ValueError, match="metacharacter"):
            _split_command_line("cmd1\ncmd2")


# ---------------------------------------------------------------------------
# _build_editor_wrapper_script
# ---------------------------------------------------------------------------

class TestBuildEditorWrapperScript:
    def test_contains_shebang(self):
        script = _build_editor_wrapper_script("/tmp/content.txt")
        assert script.startswith("#!/bin/sh")

    def test_contains_content_path(self):
        script = _build_editor_wrapper_script("/tmp/content.txt")
        assert "/tmp/content.txt" in script


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class TestSkillRunModels:
    def test_skill_run_file_defaults(self):
        f = SkillRunFile()
        assert f.name == ""
        assert f.content == ""
        assert f.size_bytes == 0

    def test_skill_run_input_required_fields(self):
        inp = SkillRunInput(skill="test", command="python run.py")
        assert inp.skill == "test"
        assert inp.command == "python run.py"
        assert inp.env == {}
        assert inp.output_files == []

    def test_skill_run_output_defaults(self):
        out = SkillRunOutput()
        assert out.stdout == ""
        assert out.exit_code == 0
        assert out.timed_out is False
        assert out.output_files == []

    def test_artifact_info(self):
        a = ArtifactInfo(name="test.txt", version=1)
        assert a.name == "test.txt"
        assert a.version == 1


# ---------------------------------------------------------------------------
# SkillRunTool — helpers
# ---------------------------------------------------------------------------

class TestSkillRunToolHelpers:
    def _make_run_tool(self, **kwargs):
        repo = MagicMock()
        repo.workspace_runtime = MagicMock()
        return SkillRunTool(repository=repo, **kwargs)

    def test_resolve_cwd_empty(self):
        tool = self._make_run_tool()
        assert tool._resolve_cwd("", "skills/test") == "skills/test"

    def test_resolve_cwd_relative(self):
        tool = self._make_run_tool()
        result = tool._resolve_cwd("sub/dir", "skills/test")
        assert result == os.path.join("skills/test", "sub/dir")

    def test_resolve_cwd_absolute(self):
        tool = self._make_run_tool()
        assert tool._resolve_cwd("/abs/path", "skills/test") == "/abs/path"

    def test_resolve_cwd_skills_dir_env(self):
        tool = self._make_run_tool()
        result = tool._resolve_cwd("$SKILLS_DIR/test", "skills/test")
        assert "skills" in result

    def test_build_command_no_restrictions(self):
        tool = self._make_run_tool()
        cmd, args = tool._build_command("python run.py", "/ws", "skills/test")
        assert cmd == "bash"
        assert "-c" in args

    def test_build_command_with_allowed_cmds(self):
        tool = self._make_run_tool(allowed_cmds=["python"])
        cmd, args = tool._build_command("python run.py", "/ws", "skills/test")
        assert cmd == "python"
        assert args == ["run.py"]

    def test_build_command_denied_cmd_raises(self):
        tool = self._make_run_tool(denied_cmds=["rm"])
        with pytest.raises(ValueError, match="denied"):
            tool._build_command("rm -rf /", "/ws", "skills/test")

    def test_build_command_not_in_allowed_raises(self):
        tool = self._make_run_tool(allowed_cmds=["python"])
        with pytest.raises(ValueError, match="not in allowed"):
            tool._build_command("bash script.sh", "/ws", "skills/test")

    def test_is_skill_loaded_true(self):
        tool = self._make_run_tool()
        ctx = MagicMock()
        ctx.session_state = {"temp:skill:loaded:test": True}
        assert tool._is_skill_loaded(ctx, "test") is True

    def test_is_skill_loaded_false(self):
        tool = self._make_run_tool()
        ctx = MagicMock()
        ctx.session_state = {}
        assert tool._is_skill_loaded(ctx, "test") is False

    def test_is_skill_loaded_exception_defaults_true(self):
        tool = self._make_run_tool()
        ctx = MagicMock()
        ctx.session_state = MagicMock()
        ctx.session_state.get = MagicMock(side_effect=RuntimeError("oops"))
        assert tool._is_skill_loaded(ctx, "test") is True

    def test_get_repository_from_tool(self):
        repo = MagicMock()
        repo.workspace_runtime = MagicMock()
        tool = SkillRunTool(repository=repo)
        ctx = MagicMock()
        assert tool._get_repository(ctx) is repo

    def test_is_missing_command_result_true(self):
        from trpc_agent_sdk.code_executors import WorkspaceRunResult
        ret = WorkspaceRunResult(
            stdout="", stderr="bash: foo: command not found",
            exit_code=127, duration=0, timed_out=False,
        )
        assert SkillRunTool._is_missing_command_result(ret) is True

    def test_is_missing_command_result_false(self):
        from trpc_agent_sdk.code_executors import WorkspaceRunResult
        ret = WorkspaceRunResult(
            stdout="", stderr="error", exit_code=1, duration=0, timed_out=False,
        )
        assert SkillRunTool._is_missing_command_result(ret) is False


# ---------------------------------------------------------------------------
# SkillRunTool — _extract_shell_examples_from_skill_body
# ---------------------------------------------------------------------------

class TestExtractShellExamplesFromBody:
    def test_fenced_code_block(self):
        body = "# Usage\n```\npython scripts/run.py --input data.csv\n```\n"
        result = SkillRunTool._extract_shell_examples_from_skill_body(body)
        assert any("python" in r for r in result)

    def test_command_section(self):
        body = "Command:\n  python scripts/analyze.py\n\nOverview"
        result = SkillRunTool._extract_shell_examples_from_skill_body(body)
        assert len(result) >= 1

    def test_empty_body(self):
        assert SkillRunTool._extract_shell_examples_from_skill_body("") == []

    def test_limit(self):
        body = ""
        for i in range(10):
            body += f"```\ncmd_{i}\n```\n"
        result = SkillRunTool._extract_shell_examples_from_skill_body(body, limit=3)
        assert len(result) <= 3

    def test_skips_function_calls(self):
        body = "```\nmy_function(arg='value')\n```\n"
        result = SkillRunTool._extract_shell_examples_from_skill_body(body)
        assert not any("my_function" in r for r in result)


# ---------------------------------------------------------------------------
# SkillRunTool — _extract_command_path_candidates
# ---------------------------------------------------------------------------

class TestExtractCommandPathCandidates:
    def test_relative_path(self):
        result = SkillRunTool._extract_command_path_candidates("python scripts/run.py")
        assert "scripts/run.py" in result

    def test_no_path_like_tokens(self):
        result = SkillRunTool._extract_command_path_candidates("ls -la")
        assert result == []

    def test_absolute_path_excluded(self):
        result = SkillRunTool._extract_command_path_candidates("python /abs/path.py")
        assert result == []

    def test_flags_excluded(self):
        result = SkillRunTool._extract_command_path_candidates("python --version")
        assert result == []

    def test_script_extensions(self):
        result = SkillRunTool._extract_command_path_candidates("bash setup.sh")
        assert "setup.sh" in result


# ---------------------------------------------------------------------------
# SkillRunTool — _with_missing_command_hint
# ---------------------------------------------------------------------------

class TestWithMissingCommandHint:
    def test_adds_hint_for_missing_command(self):
        from trpc_agent_sdk.code_executors import WorkspaceRunResult
        ret = WorkspaceRunResult(
            stdout="", stderr="bash: nonexist: command not found",
            exit_code=127, duration=0, timed_out=False,
        )
        inp = SkillRunInput(skill="test", command="nonexist")
        updated = SkillRunTool._with_missing_command_hint(ret, inp)
        assert "hint" in updated.stderr.lower()

    def test_no_hint_for_success(self):
        from trpc_agent_sdk.code_executors import WorkspaceRunResult
        ret = WorkspaceRunResult(
            stdout="ok", stderr="", exit_code=0, duration=0, timed_out=False,
        )
        inp = SkillRunInput(skill="test", command="python run.py")
        updated = SkillRunTool._with_missing_command_hint(ret, inp)
        assert updated.stderr == ""


# ---------------------------------------------------------------------------
# SkillRunTool — skill_stager property
# ---------------------------------------------------------------------------

class TestSkillRunToolProperties:
    def test_skill_stager_property(self):
        repo = MagicMock()
        repo.workspace_runtime = MagicMock()
        tool = SkillRunTool(repository=repo)
        assert tool.skill_stager is not None

    def test_custom_stager(self):
        repo = MagicMock()
        repo.workspace_runtime = MagicMock()
        custom_stager = MagicMock()
        tool = SkillRunTool(repository=repo, skill_stager=custom_stager)
        assert tool.skill_stager is custom_stager

    def test_declaration(self):
        repo = MagicMock()
        repo.workspace_runtime = MagicMock()
        tool = SkillRunTool(repository=repo)
        decl = tool._get_declaration()
        assert decl.name == "skill_run"

    def test_declaration_with_allowed_cmds(self):
        repo = MagicMock()
        repo.workspace_runtime = MagicMock()
        tool = SkillRunTool(repository=repo, allowed_cmds=["python", "bash"])
        decl = tool._get_declaration()
        assert "python" in decl.description

    def test_declaration_with_denied_cmds(self):
        repo = MagicMock()
        repo.workspace_runtime = MagicMock()
        tool = SkillRunTool(repository=repo, denied_cmds=["rm"])
        decl = tool._get_declaration()
        assert "Restrictions" in decl.description


# ---------------------------------------------------------------------------
# SkillRunTool — _wrap_with_venv
# ---------------------------------------------------------------------------

class TestWrapWithVenv:
    def _make_tool(self):
        repo = MagicMock()
        repo.workspace_runtime = MagicMock()
        return SkillRunTool(repository=repo)

    def test_wraps_command(self):
        tool = self._make_tool()
        result = tool._wrap_with_venv("python run.py", "/ws", "skills/test")
        assert "VIRTUAL_ENV" in result
        assert "python run.py" in result
        assert ".venv" in result

    def test_non_skills_cwd(self):
        tool = self._make_tool()
        result = tool._wrap_with_venv("echo hi", "/ws", "work/custom")
        assert "echo hi" in result


# ---------------------------------------------------------------------------
# SkillRunTool — _with_skill_doc_command_hint
# ---------------------------------------------------------------------------

class TestWithSkillDocCommandHint:
    def _make_tool(self):
        repo = MagicMock()
        repo.workspace_runtime = MagicMock()
        return SkillRunTool(repository=repo)

    def test_adds_hint_for_missing_command(self):
        from trpc_agent_sdk.code_executors import WorkspaceRunResult
        tool = self._make_tool()
        ret = WorkspaceRunResult(
            stdout="", stderr="bash: nonexist: command not found",
            exit_code=127, duration=0, timed_out=False,
        )
        skill = MagicMock()
        skill.body = "```\npython scripts/run.py\n```\n"
        skill.tools = ["get_weather"]
        repo = MagicMock()
        repo.get = MagicMock(return_value=skill)
        inp = SkillRunInput(skill="test", command="nonexist")
        updated = tool._with_skill_doc_command_hint(ret, repo, inp)
        assert "SKILL.md" in updated.stderr

    def test_no_hint_for_success(self):
        from trpc_agent_sdk.code_executors import WorkspaceRunResult
        tool = self._make_tool()
        ret = WorkspaceRunResult(
            stdout="ok", stderr="", exit_code=0, duration=0, timed_out=False,
        )
        repo = MagicMock()
        inp = SkillRunInput(skill="test", command="python run.py")
        updated = tool._with_skill_doc_command_hint(ret, repo, inp)
        assert updated.stderr == ""

    def test_repo_exception_returns_unchanged(self):
        from trpc_agent_sdk.code_executors import WorkspaceRunResult
        tool = self._make_tool()
        ret = WorkspaceRunResult(
            stdout="", stderr="bash: x: command not found",
            exit_code=127, duration=0, timed_out=False,
        )
        repo = MagicMock()
        repo.get = MagicMock(side_effect=RuntimeError("fail"))
        inp = SkillRunInput(skill="test", command="x")
        updated = tool._with_skill_doc_command_hint(ret, repo, inp)
        assert updated is ret


# ---------------------------------------------------------------------------
# SkillRunTool — _suggest_commands/_suggest_tools
# ---------------------------------------------------------------------------

class TestSuggestMethods:
    def _make_tool(self):
        repo = MagicMock()
        repo.workspace_runtime = MagicMock()
        return SkillRunTool(repository=repo)

    def test_suggest_commands_for_missing(self):
        from trpc_agent_sdk.code_executors import WorkspaceRunResult
        tool = self._make_tool()
        ret = WorkspaceRunResult(
            stdout="", stderr="command not found",
            exit_code=127, duration=0, timed_out=False,
        )
        skill = MagicMock()
        skill.body = "```\npython run.py\n```\n"
        repo = MagicMock()
        repo.get = MagicMock(return_value=skill)
        result = tool._suggest_commands_for_missing_command(ret, repo, "test")
        assert result is not None

    def test_suggest_commands_no_missing(self):
        from trpc_agent_sdk.code_executors import WorkspaceRunResult
        tool = self._make_tool()
        ret = WorkspaceRunResult(
            stdout="ok", stderr="", exit_code=0, duration=0, timed_out=False,
        )
        repo = MagicMock()
        result = tool._suggest_commands_for_missing_command(ret, repo, "test")
        assert result is None

    def test_suggest_tools_for_missing(self):
        from trpc_agent_sdk.code_executors import WorkspaceRunResult
        tool = self._make_tool()
        ret = WorkspaceRunResult(
            stdout="", stderr="command not found",
            exit_code=127, duration=0, timed_out=False,
        )
        skill = MagicMock()
        skill.tools = ["get_data"]
        repo = MagicMock()
        repo.get = MagicMock(return_value=skill)
        result = tool._suggest_tools_for_missing_command(ret, repo, "test")
        assert result == ["get_data"]

    def test_suggest_tools_no_tools(self):
        from trpc_agent_sdk.code_executors import WorkspaceRunResult
        tool = self._make_tool()
        ret = WorkspaceRunResult(
            stdout="", stderr="command not found",
            exit_code=127, duration=0, timed_out=False,
        )
        skill = MagicMock()
        skill.tools = []
        repo = MagicMock()
        repo.get = MagicMock(return_value=skill)
        result = tool._suggest_tools_for_missing_command(ret, repo, "test")
        assert result is None

    def test_suggest_commands_repo_error(self):
        from trpc_agent_sdk.code_executors import WorkspaceRunResult
        tool = self._make_tool()
        ret = WorkspaceRunResult(
            stdout="", stderr="command not found",
            exit_code=127, duration=0, timed_out=False,
        )
        repo = MagicMock()
        repo.get = MagicMock(side_effect=RuntimeError("fail"))
        result = tool._suggest_commands_for_missing_command(ret, repo, "test")
        assert result is None


# ---------------------------------------------------------------------------
# SkillRunTool — _precheck_inline_python_rewrite
# ---------------------------------------------------------------------------

class TestPrecheckInlinePythonRewrite:
    def test_not_blocked_when_disabled(self):
        repo = MagicMock()
        repo.workspace_runtime = MagicMock()
        tool = SkillRunTool(repository=repo, block_inline_python_rewrite=False)
        inp = SkillRunInput(skill="test", command="python -c 'print(1)'")
        assert tool._precheck_inline_python_rewrite(repo, inp) is None

    def test_not_python_c(self):
        repo = MagicMock()
        repo.workspace_runtime = MagicMock()
        tool = SkillRunTool(repository=repo, block_inline_python_rewrite=True)
        inp = SkillRunInput(skill="test", command="python run.py")
        assert tool._precheck_inline_python_rewrite(repo, inp) is None

    def test_blocked_with_script_examples(self):
        repo = MagicMock()
        repo.workspace_runtime = MagicMock()
        skill = MagicMock()
        skill.body = "```\npython3 scripts/analyze.py --input data\n```\n"
        repo.get = MagicMock(return_value=skill)
        tool = SkillRunTool(repository=repo, block_inline_python_rewrite=True)
        inp = SkillRunInput(skill="test", command="python -c 'import sys; print(sys.argv)'")
        result = tool._precheck_inline_python_rewrite(repo, inp)
        assert result is not None
        assert result.exit_code == 2

    def test_not_blocked_without_script_examples(self):
        repo = MagicMock()
        repo.workspace_runtime = MagicMock()
        skill = MagicMock()
        skill.body = "```\necho hello\n```\n"
        repo.get = MagicMock(return_value=skill)
        tool = SkillRunTool(repository=repo, block_inline_python_rewrite=True)
        inp = SkillRunInput(skill="test", command="python -c 'print(1)'")
        result = tool._precheck_inline_python_rewrite(repo, inp)
        assert result is None

    def test_repo_error_returns_none(self):
        repo = MagicMock()
        repo.workspace_runtime = MagicMock()
        repo.get = MagicMock(side_effect=RuntimeError("fail"))
        tool = SkillRunTool(repository=repo, block_inline_python_rewrite=True)
        inp = SkillRunInput(skill="test", command="python -c 'print(1)'")
        result = tool._precheck_inline_python_rewrite(repo, inp)
        assert result is None


# ---------------------------------------------------------------------------
# SkillRunTool — _list_entrypoint_suggestions
# ---------------------------------------------------------------------------

class TestListEntrypointSuggestions:
    def test_finds_scripts(self, tmp_path):
        scripts = tmp_path / "scripts"
        scripts.mkdir()
        (scripts / "run.py").write_text("#!/usr/bin/env python")
        result = SkillRunTool._list_entrypoint_suggestions(tmp_path)
        assert any("run.py" in r for r in result)

    def test_empty_dir(self, tmp_path):
        assert SkillRunTool._list_entrypoint_suggestions(tmp_path) == []

    def test_limit(self, tmp_path):
        scripts = tmp_path / "scripts"
        scripts.mkdir()
        for i in range(30):
            (scripts / f"script_{i}.py").write_text(f"# script {i}")
        result = SkillRunTool._list_entrypoint_suggestions(tmp_path, limit=5)
        assert len(result) <= 5


# ---------------------------------------------------------------------------
# SkillRunTool — _with_missing_entrypoint_hint
# ---------------------------------------------------------------------------

class TestWithMissingEntrypointHint:
    def test_adds_hint_for_missing_file(self, tmp_path):
        from trpc_agent_sdk.code_executors import WorkspaceRunResult
        scripts = tmp_path / "skills" / "test" / "scripts"
        scripts.mkdir(parents=True)
        (scripts / "real.py").write_text("# real")
        ws = MagicMock()
        ws.path = str(tmp_path)
        ret = WorkspaceRunResult(
            stdout="", stderr="No such file or directory",
            exit_code=1, duration=0, timed_out=False,
        )
        inp = SkillRunInput(skill="test", command="python scripts/missing.py")
        updated = SkillRunTool._with_missing_entrypoint_hint(ret, inp, ws, "skills/test")
        assert "hint" in updated.stderr.lower() or "entrypoint" in updated.stderr.lower()

    def test_no_hint_for_success(self):
        from trpc_agent_sdk.code_executors import WorkspaceRunResult
        ret = WorkspaceRunResult(
            stdout="ok", stderr="", exit_code=0, duration=0, timed_out=False,
        )
        ws = MagicMock()
        ws.path = "/tmp/ws"
        inp = SkillRunInput(skill="test", command="python run.py")
        updated = SkillRunTool._with_missing_entrypoint_hint(ret, inp, ws, "skills/test")
        assert updated is ret


# ---------------------------------------------------------------------------
# SkillRunTool — _merge_manifest_artifact_refs
# ---------------------------------------------------------------------------

class TestMergeManifestArtifactRefs:
    def _make_tool(self):
        repo = MagicMock()
        repo.workspace_runtime = MagicMock()
        return SkillRunTool(repository=repo)

    def test_none_manifest_noop(self):
        tool = self._make_tool()
        output = SkillRunOutput()
        tool._merge_manifest_artifact_refs(None, output)
        assert output.artifact_files == []

    def test_already_has_artifacts_noop(self):
        tool = self._make_tool()
        output = SkillRunOutput(artifact_files=[ArtifactInfo(name="x", version=1)])
        manifest = MagicMock()
        tool._merge_manifest_artifact_refs(manifest, output)
        assert len(output.artifact_files) == 1

    def test_merges_from_manifest(self):
        tool = self._make_tool()
        output = SkillRunOutput()
        manifest = MagicMock()
        fr = MagicMock()
        fr.saved_as = "artifact.txt"
        fr.version = 2
        manifest.files = [fr]
        tool._merge_manifest_artifact_refs(manifest, output)
        assert len(output.artifact_files) == 1
        assert output.artifact_files[0].name == "artifact.txt"

    def test_skips_unsaved(self):
        tool = self._make_tool()
        output = SkillRunOutput()
        manifest = MagicMock()
        fr = MagicMock()
        fr.saved_as = ""
        manifest.files = [fr]
        tool._merge_manifest_artifact_refs(manifest, output)
        assert output.artifact_files == []


# ---------------------------------------------------------------------------
# SkillRunTool — _to_run_file / _to_run_files
# ---------------------------------------------------------------------------

class TestToRunFile:
    def _make_tool(self):
        repo = MagicMock()
        repo.workspace_runtime = MagicMock()
        return SkillRunTool(repository=repo)

    def test_text_file(self):
        from trpc_agent_sdk.code_executors import CodeFile
        tool = self._make_tool()
        cf = CodeFile(name="test.txt", content="hello", mime_type="text/plain", size_bytes=5)
        rf = tool._to_run_file(cf)
        assert rf.name == "test.txt"
        assert rf.content == "hello"
        assert rf.ref == "workspace://test.txt"

    def test_binary_file_omits_content(self):
        from trpc_agent_sdk.code_executors import CodeFile
        tool = self._make_tool()
        cf = CodeFile(name="img.png", content="binary", mime_type="image/png", size_bytes=100)
        rf = tool._to_run_file(cf)
        assert rf.content == ""

    def test_to_run_files(self):
        from trpc_agent_sdk.code_executors import CodeFile
        tool = self._make_tool()
        files = [
            CodeFile(name="a.txt", content="a", mime_type="text/plain", size_bytes=1),
            CodeFile(name="b.txt", content="b", mime_type="text/plain", size_bytes=1),
        ]
        result = tool._to_run_files(files)
        assert len(result) == 2


# ---------------------------------------------------------------------------
# SkillRunTool — _prepare_editor_env
# ---------------------------------------------------------------------------

class TestPrepareEditorEnv:
    def _make_tool(self):
        repo = MagicMock()
        repo.workspace_runtime = MagicMock()
        return SkillRunTool(repository=repo)

    async def test_empty_editor_text_noop(self):
        tool = self._make_tool()
        ctx = MagicMock()
        ws = MagicMock()
        ws.path = "/tmp/ws"
        env = {}
        await tool._prepare_editor_env(ctx, ws, env, "")
        assert "EDITOR" not in env

    async def test_editor_env_conflict_raises(self):
        tool = self._make_tool()
        ctx = MagicMock()
        ws = MagicMock()
        ws.path = "/tmp/ws"
        env = {"EDITOR": "/usr/bin/vim"}
        with pytest.raises(ValueError, match="editor_text cannot be combined"):
            await tool._prepare_editor_env(ctx, ws, env, "some text")

    async def test_visual_env_conflict_raises(self):
        tool = self._make_tool()
        ctx = MagicMock()
        ws = MagicMock()
        ws.path = "/tmp/ws"
        env = {"VISUAL": "/usr/bin/vim"}
        with pytest.raises(ValueError, match="editor_text cannot be combined"):
            await tool._prepare_editor_env(ctx, ws, env, "some text")

    async def test_stages_editor_files(self, tmp_path):
        repo = MagicMock()
        fs = MagicMock()
        fs.put_files = AsyncMock()
        runtime = MagicMock()
        runtime.fs = MagicMock(return_value=fs)
        repo.workspace_runtime = runtime
        tool = SkillRunTool(repository=repo)

        ctx = MagicMock()
        ws = MagicMock()
        ws.path = str(tmp_path)
        env = {}
        await tool._prepare_editor_env(ctx, ws, env, "editor content")
        assert "EDITOR" in env
        assert "VISUAL" in env

    async def test_fallback_to_local_write(self, tmp_path):
        repo = MagicMock()
        fs = MagicMock()
        fs.put_files = AsyncMock(side_effect=RuntimeError("workspace unavailable"))
        runtime = MagicMock()
        runtime.fs = MagicMock(return_value=fs)
        repo.workspace_runtime = runtime
        tool = SkillRunTool(repository=repo)

        ctx = MagicMock()
        ws = MagicMock()
        ws.path = str(tmp_path)
        env = {}
        await tool._prepare_editor_env(ctx, ws, env, "editor content")
        assert "EDITOR" in env


# ---------------------------------------------------------------------------
# SkillRunTool — _attach_artifacts_if_requested
# ---------------------------------------------------------------------------

class TestAttachArtifacts:
    def _make_tool(self):
        repo = MagicMock()
        repo.workspace_runtime = MagicMock()
        return SkillRunTool(repository=repo)

    async def test_no_files_noop(self):
        tool = self._make_tool()
        ctx = MagicMock()
        ws = MagicMock()
        inp = SkillRunInput(skill="test", command="echo", save_as_artifacts=True)
        output = SkillRunOutput()
        await tool._attach_artifacts_if_requested(ctx, ws, inp, output, [])

    async def test_not_requested_noop(self):
        tool = self._make_tool()
        ctx = MagicMock()
        ws = MagicMock()
        inp = SkillRunInput(skill="test", command="echo", save_as_artifacts=False)
        output = SkillRunOutput()
        files = [SkillRunFile(name="a.txt", content="hello")]
        await tool._attach_artifacts_if_requested(ctx, ws, inp, output, files)
        assert output.artifact_files == []

    async def test_no_artifact_service_warns(self):
        tool = self._make_tool()
        ctx = MagicMock()
        ctx.artifact_service = None
        ws = MagicMock()
        inp = SkillRunInput(skill="test", command="echo", save_as_artifacts=True)
        output = SkillRunOutput()
        files = [SkillRunFile(name="a.txt", content="hello")]
        await tool._attach_artifacts_if_requested(ctx, ws, inp, output, files)
        assert any("not configured" in w for w in output.warnings)

    async def test_save_artifacts(self):
        tool = self._make_tool()
        ctx = MagicMock()
        ctx.artifact_service = MagicMock()
        ctx.save_artifact = AsyncMock(return_value=1)
        ws = MagicMock()
        inp = SkillRunInput(skill="test", command="echo", save_as_artifacts=True)
        output = SkillRunOutput()
        files = [SkillRunFile(name="a.txt", content="hello", mime_type="text/plain")]
        await tool._attach_artifacts_if_requested(ctx, ws, inp, output, files)
        assert len(output.artifact_files) == 1
        assert output.artifact_files[0].name == "a.txt"

    async def test_save_artifacts_with_prefix(self):
        tool = self._make_tool()
        ctx = MagicMock()
        ctx.artifact_service = MagicMock()
        ctx.save_artifact = AsyncMock(return_value=1)
        ws = MagicMock()
        inp = SkillRunInput(skill="test", command="echo", save_as_artifacts=True, artifact_prefix="out/")
        output = SkillRunOutput()
        files = [SkillRunFile(name="a.txt", content="hello", mime_type="text/plain")]
        await tool._attach_artifacts_if_requested(ctx, ws, inp, output, files)
        assert output.artifact_files[0].name == "out/a.txt"


# ---------------------------------------------------------------------------
# SkillRunTool — _prepare_outputs
# ---------------------------------------------------------------------------

class TestPrepareOutputs:
    def _make_tool(self):
        repo = MagicMock()
        fs = MagicMock()
        runtime = MagicMock()
        runtime.fs = MagicMock(return_value=fs)
        repo.workspace_runtime = runtime
        return SkillRunTool(repository=repo), fs

    async def test_no_outputs(self):
        tool, fs = self._make_tool()
        ctx = MagicMock()
        ws = MagicMock()
        inp = SkillRunInput(skill="test", command="echo")
        files, manifest = await tool._prepare_outputs(ctx, ws, inp)
        assert files == []
        assert manifest is None

    async def test_output_files_patterns(self):
        tool, fs = self._make_tool()
        from trpc_agent_sdk.code_executors import CodeFile
        fs.collect = AsyncMock(return_value=[
            CodeFile(name="out/a.txt", content="hello", mime_type="text/plain", size_bytes=5)
        ])
        ctx = MagicMock()
        ws = MagicMock()
        inp = SkillRunInput(skill="test", command="echo", output_files=["out/*.txt"])
        files, manifest = await tool._prepare_outputs(ctx, ws, inp)
        assert len(files) == 1
        assert manifest is None
