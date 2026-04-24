# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for trpc_agent_sdk.server.openclaw._cli."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import typer
from typer.testing import CliRunner

from trpc_agent_sdk.server.openclaw._cli import (
    _resolve_optional_path,
    app,
)

runner = CliRunner()


# ---------------------------------------------------------------------------
# _resolve_optional_path
# ---------------------------------------------------------------------------
class TestResolveOptionalPath:

    def test_none_returns_none(self):
        assert _resolve_optional_path(None) is None

    def test_empty_string_returns_none(self):
        assert _resolve_optional_path("") is None

    def test_string_returns_resolved_path(self):
        result = _resolve_optional_path("/tmp/some/path")
        assert isinstance(result, Path)
        assert result == Path("/tmp/some/path").expanduser().resolve()

    def test_tilde_expanded(self):
        result = _resolve_optional_path("~/mydir")
        assert isinstance(result, Path)
        assert "~" not in str(result)

    def test_relative_path_resolved(self):
        result = _resolve_optional_path("relative/path")
        assert isinstance(result, Path)
        assert result.is_absolute()


# ---------------------------------------------------------------------------
# show_config_template_cmd (conf_temp)
# ---------------------------------------------------------------------------
class TestShowConfigTemplateCmd:

    def test_template_found_prints_content(self, tmp_path):
        template_file = tmp_path / "config.temp.yaml"
        template_file.write_text("key: value\n", encoding="utf-8")

        fake_file = MagicMock()
        fake_file.with_name.return_value = template_file

        with patch("trpc_agent_sdk.server.openclaw._cli.Path") as mock_path:
            mock_path.return_value = fake_file
            # Path(__file__) returns our fake, .with_name returns the real tmp file
            result = runner.invoke(app, ["conf_temp"])

        assert result.exit_code == 0
        assert "key: value" in result.output

    def test_template_not_found_exits_1(self, tmp_path):
        missing = tmp_path / "config.temp.yaml"

        fake_file = MagicMock()
        fake_file.with_name.return_value = missing

        with patch("trpc_agent_sdk.server.openclaw._cli.Path") as mock_path:
            mock_path.return_value = fake_file
            result = runner.invoke(app, ["conf_temp"])

        assert result.exit_code == 1
        assert "Error" in result.output

    def test_full_flag_selects_full_template(self, tmp_path):
        template_file = tmp_path / "config_full.temp.yaml"
        template_file.write_text("full: yes\n", encoding="utf-8")

        fake_file = MagicMock()
        fake_file.with_name.return_value = template_file

        with patch("trpc_agent_sdk.server.openclaw._cli.Path") as mock_path:
            mock_path.return_value = fake_file
            result = runner.invoke(app, ["conf_temp", "--full"])

        assert result.exit_code == 0
        fake_file.with_name.assert_called_with("config_full.temp.yaml")


# ---------------------------------------------------------------------------
# run_cmd
# ---------------------------------------------------------------------------
class TestRunCmd:

    @patch("trpc_agent_sdk.server.openclaw._cli._run_openclaw")
    def test_run_default(self, mock_run):
        result = runner.invoke(app, ["run"])
        assert result.exit_code == 0
        mock_run.assert_called_once_with(workspace=None, config=None, force_chat=False)

    @patch("trpc_agent_sdk.server.openclaw._cli._run_openclaw")
    def test_run_with_options(self, mock_run):
        result = runner.invoke(app, ["run", "--workspace", "/ws", "--config", "/cfg.yaml"])
        assert result.exit_code == 0
        mock_run.assert_called_once_with(workspace="/ws", config="/cfg.yaml", force_chat=False)


# ---------------------------------------------------------------------------
# chat_cmd
# ---------------------------------------------------------------------------
class TestChatCmd:

    @patch("trpc_agent_sdk.server.openclaw._cli._run_openclaw")
    def test_chat_forces_chat_mode(self, mock_run):
        result = runner.invoke(app, ["chat"])
        assert result.exit_code == 0
        mock_run.assert_called_once_with(workspace=None, config=None, force_chat=True)

    @patch("trpc_agent_sdk.server.openclaw._cli._run_openclaw")
    def test_chat_with_workspace(self, mock_run):
        result = runner.invoke(app, ["chat", "-w", "/my/ws"])
        assert result.exit_code == 0
        mock_run.assert_called_once_with(workspace="/my/ws", config=None, force_chat=True)


# ---------------------------------------------------------------------------
# ui_cmd
# ---------------------------------------------------------------------------
class TestUiCmd:

    @patch("trpc_agent_sdk.server.openclaw._cli._run_openclaw_ui")
    def test_ui_default(self, mock_ui):
        result = runner.invoke(app, ["ui"])
        assert result.exit_code == 0
        mock_ui.assert_called_once_with(workspace=None, config=None)

    @patch("trpc_agent_sdk.server.openclaw._cli._run_openclaw_ui")
    def test_ui_with_options(self, mock_ui):
        result = runner.invoke(app, ["ui", "-w", "/ws", "-c", "/cfg.yaml"])
        assert result.exit_code == 0
        mock_ui.assert_called_once_with(workspace="/ws", config="/cfg.yaml")


# ---------------------------------------------------------------------------
# deps_cmd — local imports inside the function require patching at source
# ---------------------------------------------------------------------------
class TestDepsCmd:

    @patch("trpc_agent_sdk.server.openclaw.skill.render_dependency_report", return_value="Report OK")
    @patch("trpc_agent_sdk.server.openclaw.skill.report_to_json", return_value='{"ok": true}')
    @patch("trpc_agent_sdk.server.openclaw.skill.apply_dependency_plan")
    @patch("trpc_agent_sdk.server.openclaw.skill.inspect_skill_dependencies", return_value={"deps": []})
    def test_deps_default_text_output(self, mock_inspect, mock_apply, mock_json, mock_render):
        result = runner.invoke(app, ["deps"])
        assert result.exit_code == 0
        mock_inspect.assert_called_once()
        mock_apply.assert_not_called()
        mock_render.assert_called_once()
        assert "Report OK" in result.output

    @patch("trpc_agent_sdk.server.openclaw.skill.render_dependency_report")
    @patch("trpc_agent_sdk.server.openclaw.skill.report_to_json", return_value='{"ok": true}')
    @patch("trpc_agent_sdk.server.openclaw.skill.apply_dependency_plan")
    @patch("trpc_agent_sdk.server.openclaw.skill.inspect_skill_dependencies", return_value={"deps": []})
    def test_deps_json_output(self, mock_inspect, mock_apply, mock_json, mock_render):
        result = runner.invoke(app, ["deps", "--json"])
        assert result.exit_code == 0
        mock_json.assert_called_once()
        mock_render.assert_not_called()
        assert '{"ok": true}' in result.output

    @patch("trpc_agent_sdk.server.openclaw.skill.render_dependency_report", return_value="done")
    @patch("trpc_agent_sdk.server.openclaw.skill.report_to_json")
    @patch("trpc_agent_sdk.server.openclaw.skill.apply_dependency_plan",
           return_value={"has_failures": False})
    @patch("trpc_agent_sdk.server.openclaw.skill.inspect_skill_dependencies", return_value={"deps": []})
    def test_deps_apply(self, mock_inspect, mock_apply, mock_json, mock_render):
        result = runner.invoke(app, ["deps", "--apply"])
        assert result.exit_code == 0
        mock_apply.assert_called_once()

    @patch("trpc_agent_sdk.server.openclaw.skill.render_dependency_report", return_value="done")
    @patch("trpc_agent_sdk.server.openclaw.skill.report_to_json")
    @patch("trpc_agent_sdk.server.openclaw.skill.apply_dependency_plan",
           return_value={"has_failures": True})
    @patch("trpc_agent_sdk.server.openclaw.skill.inspect_skill_dependencies", return_value={"deps": []})
    def test_deps_apply_with_failures_exits_1(self, mock_inspect, mock_apply, mock_json, mock_render):
        result = runner.invoke(app, ["deps", "--apply"])
        assert result.exit_code == 1

    @patch("trpc_agent_sdk.server.openclaw.skill.render_dependency_report")
    @patch("trpc_agent_sdk.server.openclaw.skill.report_to_json")
    @patch("trpc_agent_sdk.server.openclaw.skill.apply_dependency_plan")
    @patch("trpc_agent_sdk.server.openclaw.skill.inspect_skill_dependencies",
           side_effect=RuntimeError("boom"))
    def test_deps_exception_exits_1(self, mock_inspect, mock_apply, mock_json, mock_render):
        result = runner.invoke(app, ["deps"])
        assert result.exit_code == 1
        assert "Error" in result.output
