# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.

import os
from unittest.mock import MagicMock

import pytest
from trpc_agent_sdk.skills._common import loaded_state_key
from trpc_agent_sdk.skills.tools._common import inline_json_schema_refs
from trpc_agent_sdk.skills.tools._skill_run import ArtifactInfo
from trpc_agent_sdk.skills.tools._skill_run import SkillRunFile
from trpc_agent_sdk.skills.tools._skill_run import SkillRunInput
from trpc_agent_sdk.skills.tools._skill_run import SkillRunOutput
from trpc_agent_sdk.skills.tools._skill_run import SkillRunTool
from trpc_agent_sdk.skills.tools._skill_run import _build_editor_wrapper_script
from trpc_agent_sdk.skills.tools._skill_run import _filter_failed_empty_outputs
from trpc_agent_sdk.skills.tools._skill_run import _is_text_mime
from trpc_agent_sdk.skills.tools._skill_run import _select_primary_output
from trpc_agent_sdk.skills.tools._skill_run import _should_inline_file_content
from trpc_agent_sdk.skills.tools._skill_run import _split_command_line
from trpc_agent_sdk.skills.tools._skill_run import _truncate_output
from trpc_agent_sdk.skills.tools._skill_run import _workspace_ref


def _make_tool() -> SkillRunTool:
    repo = MagicMock()
    repo.workspace_runtime = MagicMock()
    return SkillRunTool(repository=repo)


class TestSchemaHelpers:
    def test_inline_json_schema_refs(self):
        schema = {"$defs": {"X": {"type": "string"}}, "properties": {"x": {"$ref": "#/$defs/X"}}}
        out = inline_json_schema_refs(schema)
        assert "$defs" not in out
        assert out["properties"]["x"]["type"] == "string"


class TestModuleHelpers:
    def test_is_text_mime(self):
        assert _is_text_mime("text/plain") is True
        assert _is_text_mime("application/json") is True
        assert _is_text_mime("image/png") is False

    def test_should_inline_file_content(self):
        from trpc_agent_sdk.code_executors import CodeFile
        f = CodeFile(name="a.txt", content="ok", mime_type="text/plain", size_bytes=2)
        assert _should_inline_file_content(f) is True

    def test_truncate_output(self):
        s, truncated = _truncate_output("x" * 20000)
        assert truncated is True
        assert len(s) <= 16 * 1024

    def test_workspace_ref(self):
        assert _workspace_ref("a.txt") == "workspace://a.txt"

    def test_filter_failed_empty_outputs(self):
        files = [SkillRunFile(name="a.txt", content="", size_bytes=0)]
        kept, warns = _filter_failed_empty_outputs(1, False, files)
        assert kept == []
        assert warns

    def test_select_primary_output(self):
        files = [
            SkillRunFile(name="b.txt", content="2", mime_type="text/plain"),
            SkillRunFile(name="a.txt", content="1", mime_type="text/plain"),
        ]
        best = _select_primary_output(files)
        assert best is not None
        assert best.name == "a.txt"

    def test_split_command_line(self):
        assert _split_command_line("python run.py") == ["python", "run.py"]
        with pytest.raises(ValueError):
            _split_command_line("a | b")

    def test_build_editor_wrapper_script(self):
        script = _build_editor_wrapper_script("/tmp/file")
        assert script.startswith("#!/bin/sh")
        assert "/tmp/file" in script


class TestModels:
    def test_run_models(self):
        inp = SkillRunInput(skill="s", command="echo hi")
        out = SkillRunOutput()
        art = ArtifactInfo(name="a.txt", version=1)
        assert inp.skill == "s"
        assert out.exit_code == 0
        assert art.version == 1


class TestSkillRunToolBasics:
    def test_resolve_cwd(self):
        tool = _make_tool()
        assert tool._resolve_cwd("", "skills/x") == "skills/x"
        assert tool._resolve_cwd("sub", "skills/x") == os.path.join("skills/x", "sub")

    def test_build_command(self):
        tool = _make_tool()
        cmd, args = tool._build_command("python run.py", "/tmp/ws", "skills/x")
        assert cmd == "bash"
        assert "-c" in args

    def test_get_repository(self):
        repo = MagicMock()
        repo.workspace_runtime = MagicMock()
        tool = SkillRunTool(repository=repo)
        ctx = MagicMock()
        assert tool._get_repository(ctx) is repo

    def test_is_skill_loaded(self):
        tool = _make_tool()
        ctx = MagicMock()
        ctx.agent_name = ""
        ctx.actions = MagicMock()
        key = loaded_state_key(ctx, "test")
        ctx.actions.state_delta = {key: True}
        ctx.session_state = {}
        assert tool._is_skill_loaded(ctx, "test") is True
