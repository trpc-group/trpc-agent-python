# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for trpc_agent_sdk.server.openclaw.ui."""

from __future__ import annotations

import asyncio
import io
import json
import os
import sys
import threading
from http import HTTPStatus
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest
import yaml

# Patch importlib.resources.files so the module-level _load_browser_html()
# call in ui.py does not require the trpc_claw package to be installed.
import importlib.resources as _res

_orig_files = _res.files


def _patched_files(pkg):
    if pkg == "trpc_claw":
        m = MagicMock()
        m.joinpath.return_value.read_text.return_value = "<html>mock</html>"
        return m
    return _orig_files(pkg)


with patch.object(_res, "files", _patched_files):
    from trpc_agent_sdk.server.openclaw.ui import (
        _BrowserUiHandler,
        _file_mtime,
        _pick_free_port,
        _restart_current_process,
        _run_browser_mode,
        _run_browser_mode_with_bind,
        _UiRuntime,
        run_ui_server,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_runtime(
    *,
    workspace: Path | None = None,
    config_path: Path | None = None,
    config_suffix: str = ".yaml",
    app: MagicMock | None = None,
) -> _UiRuntime:
    """Create a _UiRuntime bypassing __init__."""
    rt = object.__new__(_UiRuntime)
    rt.app = app
    rt.session_id = "ui-default"
    rt.user_id = "ui_test_user"
    rt.chat_id = "webui"
    rt._workspace = workspace
    rt._config_path = config_path or Path(f"/tmp/fake_config{config_suffix}")
    rt._loop = MagicMock()
    rt._thread = MagicMock()
    return rt


def _make_handler(runtime: _UiRuntime | None = None, *, path: str = "/") -> _BrowserUiHandler:
    """Create a _BrowserUiHandler without binding to a real socket."""
    handler = object.__new__(_BrowserUiHandler)
    handler.runtime = runtime or _make_runtime()
    handler.wfile = io.BytesIO()
    handler.rfile = io.BytesIO()
    handler.path = path
    handler.headers = {}
    handler.requestline = f"GET {path} HTTP/1.1"
    handler.request_version = "HTTP/1.1"
    handler.command = "GET"
    handler.client_address = ("127.0.0.1", 12345)
    handler.close_connection = True
    handler.responses = {v: (v.phrase, v.description) for v in HTTPStatus}
    handler._headers_buffer = []
    return handler


# =========================================================================
# _merge_text
# =========================================================================

class TestMergeText:

    def test_empty_incoming_returns_current(self):
        assert _UiRuntime._merge_text("hello", "") == "hello"

    def test_none_incoming_returns_current(self):
        assert _UiRuntime._merge_text("hello", None) == "hello"

    def test_empty_current_returns_incoming(self):
        assert _UiRuntime._merge_text("", "world") == "world"

    def test_none_current_returns_incoming(self):
        assert _UiRuntime._merge_text(None, "world") == "world"

    def test_both_empty(self):
        assert _UiRuntime._merge_text("", "") == ""

    def test_incoming_starts_with_current(self):
        assert _UiRuntime._merge_text("hel", "hello") == "hello"

    def test_current_ends_with_incoming(self):
        assert _UiRuntime._merge_text("hello", "llo") == "hello"

    def test_incoming_contained_in_current(self):
        assert _UiRuntime._merge_text("abcdef", "cde") == "abcdef"

    def test_current_contained_in_incoming(self):
        assert _UiRuntime._merge_text("cd", "abcdef") == "abcdef"

    def test_no_overlap_concatenates(self):
        assert _UiRuntime._merge_text("abc", "xyz") == "abcxyz"

    def test_identical_strings(self):
        assert _UiRuntime._merge_text("same", "same") == "same"

    def test_incoming_is_prefix_of_current(self):
        assert _UiRuntime._merge_text("hello world", "hello") == "hello world"

    def test_unicode_merge(self):
        assert _UiRuntime._merge_text("你好", "世界") == "你好世界"

    def test_unicode_overlap(self):
        assert _UiRuntime._merge_text("你好世界", "世界") == "你好世界"


# =========================================================================
# _parse_config_text
# =========================================================================

class TestParseConfigText:

    def test_yaml_valid(self):
        text = "key: value\nnumber: 42\n"
        parsed, normalized = _UiRuntime._parse_config_text(text, ".yaml")
        assert parsed == {"key": "value", "number": 42}
        assert isinstance(normalized, str)
        assert "key" in normalized

    def test_yml_suffix_treated_as_yaml(self):
        text = "a: 1\n"
        parsed, _ = _UiRuntime._parse_config_text(text, ".yml")
        assert parsed == {"a": 1}

    def test_json_valid(self):
        text = '{"key": "value", "number": 42}'
        parsed, normalized = _UiRuntime._parse_config_text(text, ".json")
        assert parsed == {"key": "value", "number": 42}
        reparsed = json.loads(normalized)
        assert reparsed == parsed

    def test_json_empty_string(self):
        parsed, normalized = _UiRuntime._parse_config_text("   ", ".json")
        assert parsed == {}
        assert normalized == "{}"

    def test_yaml_empty_returns_empty_dict(self):
        parsed, _ = _UiRuntime._parse_config_text("", ".yaml")
        assert parsed == {}

    def test_yaml_none_content_returns_empty_dict(self):
        parsed, _ = _UiRuntime._parse_config_text("null", ".yaml")
        assert parsed == {}

    def test_json_normalized_indent(self):
        text = '{"a":1}'
        _, normalized = _UiRuntime._parse_config_text(text, ".json")
        assert normalized == json.dumps({"a": 1}, ensure_ascii=False, indent=2)

    def test_yaml_normalized_round_trip(self):
        text = "b: 2\na: 1\n"
        parsed, normalized = _UiRuntime._parse_config_text(text, ".yaml")
        reparsed = yaml.safe_load(normalized)
        assert reparsed == parsed

    def test_non_dict_yaml_raises(self):
        with pytest.raises(ValueError, match="Config root must be a JSON/YAML object"):
            _UiRuntime._parse_config_text("- item1\n- item2\n", ".yaml")

    def test_non_dict_json_raises(self):
        with pytest.raises(ValueError, match="Config root must be a JSON/YAML object"):
            _UiRuntime._parse_config_text("[1, 2, 3]", ".json")

    def test_invalid_yaml_raises(self):
        with pytest.raises(yaml.YAMLError):
            _UiRuntime._parse_config_text("key: :\n  bad: [", ".yaml")

    def test_invalid_json_raises(self):
        with pytest.raises(json.JSONDecodeError):
            _UiRuntime._parse_config_text("{invalid json}", ".json")

    def test_yaml_unicode_preserved(self):
        text = "name: 你好\n"
        parsed, normalized = _UiRuntime._parse_config_text(text, ".yaml")
        assert parsed["name"] == "你好"
        assert "你好" in normalized

    def test_json_unicode_preserved(self):
        text = '{"name": "你好"}'
        _, normalized = _UiRuntime._parse_config_text(text, ".json")
        assert "你好" in normalized


# =========================================================================
# _format_parse_error
# =========================================================================

class TestFormatParseError:

    def test_yaml_error_with_mark(self):
        try:
            yaml.safe_load("key: :\n  bad: [")
        except yaml.YAMLError as exc:
            result = _UiRuntime._format_parse_error(exc, ".yaml")
            assert result["valid"] is False
            assert "YAML 格式错误" in result["message"]
            assert result["line"] is not None
            return
        pytest.fail("Expected yaml.YAMLError")

    def test_yaml_error_with_yml_suffix(self):
        try:
            yaml.safe_load(":\n  - [unclosed")
        except yaml.YAMLError as exc:
            result = _UiRuntime._format_parse_error(exc, ".yml")
            assert result["valid"] is False
            assert "YAML" in result["message"]
            return
        pytest.fail("Expected yaml.YAMLError")

    def test_yaml_error_without_mark(self):
        exc = yaml.YAMLError("something went wrong")
        result = _UiRuntime._format_parse_error(exc, ".yaml")
        assert result["valid"] is False
        assert result["line"] is None
        assert result["column"] is None

    def test_json_decode_error(self):
        try:
            json.loads("{bad json}")
        except json.JSONDecodeError as exc:
            result = _UiRuntime._format_parse_error(exc, ".json")
            assert result["valid"] is False
            assert "JSON 格式错误" in result["message"]
            assert result["line"] is not None
            assert result["column"] is not None
            return
        pytest.fail("Expected json.JSONDecodeError")

    def test_generic_exception_yaml_suffix(self):
        exc = ValueError("generic error")
        result = _UiRuntime._format_parse_error(exc, ".yaml")
        assert result["valid"] is False
        assert result["message"] == "generic error"
        assert result["line"] is None
        assert result["column"] is None

    def test_generic_exception_json_suffix(self):
        exc = ValueError("generic error")
        result = _UiRuntime._format_parse_error(exc, ".json")
        assert result["valid"] is False
        assert result["message"] == "generic error"
        assert result["line"] is None

    def test_json_error_with_yaml_suffix_falls_through(self):
        try:
            json.loads("{bad}")
        except json.JSONDecodeError as exc:
            result = _UiRuntime._format_parse_error(exc, ".yaml")
            assert result["valid"] is False
            assert result["line"] is None
            return
        pytest.fail("Expected json.JSONDecodeError")

    def test_yaml_error_with_json_suffix_falls_through(self):
        exc = yaml.YAMLError("yaml problem")
        result = _UiRuntime._format_parse_error(exc, ".json")
        assert result["valid"] is False
        assert result["line"] is None

    def test_yaml_error_with_line_but_no_column(self):
        exc = yaml.YAMLError("problem")
        mark = MagicMock()
        mark.line = 4
        mark.column = -1
        exc.problem_mark = mark
        exc.problem = "unexpected key"
        result = _UiRuntime._format_parse_error(exc, ".yaml")
        assert result["valid"] is False
        assert result["line"] == 5
        assert result["column"] == 0
        assert "第 5 行" in result["message"]


# =========================================================================
# _pick_free_port
# =========================================================================

class TestPickFreePort:

    def test_returns_positive_int(self):
        port = _pick_free_port()
        assert isinstance(port, int)
        assert port > 0

    def test_returns_different_ports(self):
        ports = {_pick_free_port() for _ in range(5)}
        assert len(ports) >= 2

    def test_custom_host(self):
        port = _pick_free_port("0.0.0.0")
        assert isinstance(port, int)
        assert port > 0


# =========================================================================
# _file_mtime
# =========================================================================

class TestFileMtime:

    def test_existing_file(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("content")
        mtime = _file_mtime(f)
        assert isinstance(mtime, float)
        assert mtime > 0

    def test_nonexistent_file(self, tmp_path):
        f = tmp_path / "nonexistent.txt"
        assert _file_mtime(f) == -1.0

    def test_none_path(self):
        assert _file_mtime(None) == -1.0


# =========================================================================
# _detail_doc_path
# =========================================================================

class TestDetailDocPath:

    def test_agents_md(self, tmp_path):
        rt = _make_runtime(workspace=tmp_path)
        result = rt._detail_doc_path("AGENTS.md")
        assert result == tmp_path / "AGENTS.md"

    def test_soul_md(self, tmp_path):
        rt = _make_runtime(workspace=tmp_path)
        assert rt._detail_doc_path("SOUL.md") == tmp_path / "SOUL.md"

    def test_user_md(self, tmp_path):
        rt = _make_runtime(workspace=tmp_path)
        assert rt._detail_doc_path("USER.md") == tmp_path / "USER.md"

    def test_tools_md(self, tmp_path):
        rt = _make_runtime(workspace=tmp_path)
        assert rt._detail_doc_path("TOOLS.md") == tmp_path / "TOOLS.md"

    def test_unsupported_item_raises(self):
        rt = _make_runtime()
        with pytest.raises(ValueError, match="Unsupported detail item"):
            rt._detail_doc_path("README.md")

    def test_empty_string_raises(self):
        rt = _make_runtime()
        with pytest.raises(ValueError, match="Unsupported detail item"):
            rt._detail_doc_path("")

    def test_none_raises(self):
        rt = _make_runtime()
        with pytest.raises(ValueError, match="Unsupported detail item"):
            rt._detail_doc_path(None)

    def test_whitespace_stripped(self, tmp_path):
        rt = _make_runtime(workspace=tmp_path)
        assert rt._detail_doc_path("  AGENTS.md  ") == tmp_path / "AGENTS.md"

    def test_uses_app_workspace_when_available(self, tmp_path):
        mock_app = MagicMock()
        mock_app.workspace = tmp_path / "app_ws"
        rt = _make_runtime(workspace=tmp_path / "fallback", app=mock_app)
        result = rt._detail_doc_path("SOUL.md")
        assert result == tmp_path / "app_ws" / "SOUL.md"

    def test_falls_back_to_workspace_when_no_app(self, tmp_path):
        rt = _make_runtime(workspace=tmp_path / "ws_dir", app=None)
        result = rt._detail_doc_path("TOOLS.md")
        assert result == tmp_path / "ws_dir" / "TOOLS.md"

    def test_falls_back_to_cwd_when_no_workspace(self):
        rt = _make_runtime(workspace=None, app=None)
        result = rt._detail_doc_path("AGENTS.md")
        assert result == Path.cwd() / "AGENTS.md"


# =========================================================================
# read_detail_doc
# =========================================================================

class TestReadDetailDoc:

    async def test_existing_file(self, tmp_path):
        doc = tmp_path / "AGENTS.md"
        doc.write_text("# Agents\nContent here", encoding="utf-8")
        rt = _make_runtime(workspace=tmp_path)
        result = await rt.read_detail_doc("AGENTS.md")
        assert result["path"] == str(doc)
        assert result["content"] == "# Agents\nContent here"

    async def test_missing_file_creates_empty(self, tmp_path):
        rt = _make_runtime(workspace=tmp_path)
        result = await rt.read_detail_doc("SOUL.md")
        path = tmp_path / "SOUL.md"
        assert path.exists()
        assert result["content"] == ""

    async def test_invalid_item_raises(self, tmp_path):
        rt = _make_runtime(workspace=tmp_path)
        with pytest.raises(ValueError):
            await rt.read_detail_doc("EVIL.md")


# =========================================================================
# save_detail_doc
# =========================================================================

class TestSaveDetailDoc:

    async def test_saves_content(self, tmp_path):
        rt = _make_runtime(workspace=tmp_path)
        result = await rt.save_detail_doc("TOOLS.md", "# Tools\nNew content")
        assert "已保存" in result
        saved = (tmp_path / "TOOLS.md").read_text(encoding="utf-8")
        assert saved == "# Tools\nNew content"

    async def test_saves_empty_content(self, tmp_path):
        rt = _make_runtime(workspace=tmp_path)
        await rt.save_detail_doc("USER.md", None)
        assert (tmp_path / "USER.md").read_text(encoding="utf-8") == ""

    async def test_creates_parent_directories(self, tmp_path):
        ws = tmp_path / "deep" / "nested"
        rt = _make_runtime(workspace=ws)
        await rt.save_detail_doc("AGENTS.md", "content")
        assert (ws / "AGENTS.md").exists()

    async def test_invalid_item_raises(self, tmp_path):
        rt = _make_runtime(workspace=tmp_path)
        with pytest.raises(ValueError):
            await rt.save_detail_doc("BAD.md", "content")


# =========================================================================
# validate_config_text
# =========================================================================

class TestValidateConfigText:

    async def test_valid_yaml(self, tmp_path):
        cfg = tmp_path / "config.yaml"
        rt = _make_runtime(config_path=cfg)
        result = await rt.validate_config_text("key: value\n")
        assert result["valid"] is True
        assert "校验通过" in result["message"]

    async def test_valid_json(self, tmp_path):
        cfg = tmp_path / "config.json"
        rt = _make_runtime(config_path=cfg)
        result = await rt.validate_config_text('{"key": "value"}')
        assert result["valid"] is True

    async def test_invalid_yaml(self, tmp_path):
        cfg = tmp_path / "config.yaml"
        rt = _make_runtime(config_path=cfg)
        result = await rt.validate_config_text("key: :\n  bad: [")
        assert result["valid"] is False
        assert "YAML" in result["message"]

    async def test_invalid_json(self, tmp_path):
        cfg = tmp_path / "config.json"
        rt = _make_runtime(config_path=cfg)
        result = await rt.validate_config_text("{bad json}")
        assert result["valid"] is False
        assert "JSON" in result["message"]

    async def test_empty_content(self, tmp_path):
        cfg = tmp_path / "config.yaml"
        rt = _make_runtime(config_path=cfg)
        result = await rt.validate_config_text("")
        assert result["valid"] is True

    async def test_none_content(self, tmp_path):
        cfg = tmp_path / "config.yaml"
        rt = _make_runtime(config_path=cfg)
        result = await rt.validate_config_text(None)
        assert result["valid"] is True

    async def test_non_dict_root_yaml(self, tmp_path):
        cfg = tmp_path / "config.yaml"
        rt = _make_runtime(config_path=cfg)
        result = await rt.validate_config_text("- item\n- item2\n")
        assert result["valid"] is False


# =========================================================================
# read_config_text
# =========================================================================

class TestReadConfigText:

    async def test_existing_config(self, tmp_path):
        cfg = tmp_path / "config.yaml"
        cfg.write_text("model: gpt-4\n", encoding="utf-8")
        rt = _make_runtime(config_path=cfg)
        result = await rt.read_config_text()
        assert result["path"] == str(cfg)
        assert "model" in result["content"]

    async def test_missing_config_creates_file(self, tmp_path):
        cfg = tmp_path / "new_dir" / "config.yaml"
        mock_app = MagicMock()
        mock_app.config.model_dump.return_value = {"model": "default"}
        rt = _make_runtime(config_path=cfg, app=mock_app)
        result = await rt.read_config_text()
        assert cfg.exists()
        assert result["path"] == str(cfg)
        assert "model" in result["content"]

    async def test_missing_config_no_app(self, tmp_path):
        cfg = tmp_path / "config.yaml"
        rt = _make_runtime(config_path=cfg, app=None)
        result = await rt.read_config_text()
        assert cfg.exists()
        content = yaml.safe_load(result["content"])
        assert content == {} or content is None


# =========================================================================
# _BrowserUiHandler._write_json
# =========================================================================

class TestWriteJson:

    def test_writes_json_body(self):
        handler = _make_handler()
        handler.send_response = MagicMock()
        handler.send_header = MagicMock()
        handler.end_headers = MagicMock()

        handler._write_json(200, {"key": "value"})

        handler.send_response.assert_called_once_with(200)
        written = handler.wfile.getvalue()
        assert json.loads(written.decode("utf-8")) == {"key": "value"}

    def test_unicode_preserved(self):
        handler = _make_handler()
        handler.send_response = MagicMock()
        handler.send_header = MagicMock()
        handler.end_headers = MagicMock()

        handler._write_json(200, {"msg": "你好"})

        written = json.loads(handler.wfile.getvalue().decode("utf-8"))
        assert written["msg"] == "你好"

    def test_content_length_header(self):
        handler = _make_handler()
        handler.send_response = MagicMock()
        handler.send_header = MagicMock()
        handler.end_headers = MagicMock()

        handler._write_json(200, {"a": 1})

        body = json.dumps({"a": 1}, ensure_ascii=False).encode("utf-8")
        handler.send_header.assert_any_call("Content-Length", str(len(body)))
        handler.send_header.assert_any_call("Content-Type", "application/json; charset=utf-8")


# =========================================================================
# _BrowserUiHandler._write_text
# =========================================================================

class TestWriteText:

    def test_writes_text_body(self):
        handler = _make_handler()
        handler.send_response = MagicMock()
        handler.send_header = MagicMock()
        handler.end_headers = MagicMock()

        handler._write_text(200, "hello world")

        written = handler.wfile.getvalue().decode("utf-8")
        assert written == "hello world"

    def test_custom_content_type(self):
        handler = _make_handler()
        handler.send_response = MagicMock()
        handler.send_header = MagicMock()
        handler.end_headers = MagicMock()

        handler._write_text(200, "<html>hi</html>", "text/html; charset=utf-8")

        handler.send_header.assert_any_call("Content-Type", "text/html; charset=utf-8")

    def test_default_content_type(self):
        handler = _make_handler()
        handler.send_response = MagicMock()
        handler.send_header = MagicMock()
        handler.end_headers = MagicMock()

        handler._write_text(200, "text")

        handler.send_header.assert_any_call("Content-Type", "text/plain; charset=utf-8")


# =========================================================================
# _BrowserUiHandler.do_GET routing
# =========================================================================

class TestDoGet:

    def _get_handler(self, path: str, runtime: _UiRuntime | None = None) -> _BrowserUiHandler:
        handler = _make_handler(runtime, path=path)
        handler.send_response = MagicMock()
        handler.send_header = MagicMock()
        handler.end_headers = MagicMock()
        return handler

    def test_root_serves_html(self):
        handler = self._get_handler("/")
        handler.do_GET()
        written = handler.wfile.getvalue().decode("utf-8")
        assert "<html" in written

    def test_api_meta(self):
        rt = _make_runtime()
        handler = self._get_handler("/api/meta", rt)
        handler.do_GET()
        data = json.loads(handler.wfile.getvalue().decode("utf-8"))
        assert data["session_id"] == "ui-default"
        assert data["user_id"] == "ui_test_user"

    def test_api_info_success(self):
        rt = _make_runtime()
        rt.run = MagicMock(return_value={"model": "gpt-4"})
        handler = self._get_handler("/api/info?item=config", rt)
        handler.do_GET()
        data = json.loads(handler.wfile.getvalue().decode("utf-8"))
        assert data["data"] == {"model": "gpt-4"}

    def test_api_info_error(self):
        rt = _make_runtime()
        rt.run = MagicMock(side_effect=RuntimeError("not initialized"))
        handler = self._get_handler("/api/info?item=config", rt)
        handler.do_GET()
        data = json.loads(handler.wfile.getvalue().decode("utf-8"))
        assert "error" in data
        handler.send_response.assert_called_with(HTTPStatus.INTERNAL_SERVER_ERROR)

    def test_api_config_edit_success(self):
        rt = _make_runtime()
        rt.run = MagicMock(return_value={"path": "/cfg.yaml", "content": "key: val"})
        handler = self._get_handler("/api/config/edit", rt)
        handler.do_GET()
        data = json.loads(handler.wfile.getvalue().decode("utf-8"))
        assert data["path"] == "/cfg.yaml"

    def test_api_config_edit_error(self):
        rt = _make_runtime()
        rt.run = MagicMock(side_effect=FileNotFoundError("nope"))
        handler = self._get_handler("/api/config/edit", rt)
        handler.do_GET()
        data = json.loads(handler.wfile.getvalue().decode("utf-8"))
        assert "error" in data
        handler.send_response.assert_called_with(HTTPStatus.INTERNAL_SERVER_ERROR)

    def test_api_detail_edit_success(self):
        rt = _make_runtime()
        rt.run = MagicMock(return_value={"path": "/ws/AGENTS.md", "content": "# Agents"})
        handler = self._get_handler("/api/detail/edit?item=AGENTS.md", rt)
        handler.do_GET()
        data = json.loads(handler.wfile.getvalue().decode("utf-8"))
        assert data["content"] == "# Agents"

    def test_api_detail_edit_bad_item(self):
        rt = _make_runtime()
        rt.run = MagicMock(side_effect=ValueError("Unsupported detail item: BAD.md"))
        handler = self._get_handler("/api/detail/edit?item=BAD.md", rt)
        handler.do_GET()
        data = json.loads(handler.wfile.getvalue().decode("utf-8"))
        assert "error" in data
        handler.send_response.assert_called_with(HTTPStatus.BAD_REQUEST)

    def test_404_unknown_path(self):
        handler = self._get_handler("/api/nonexistent")
        handler.do_GET()
        written = handler.wfile.getvalue().decode("utf-8")
        assert written == "not found"
        handler.send_response.assert_called_with(HTTPStatus.NOT_FOUND)


# =========================================================================
# _BrowserUiHandler.do_POST routing
# =========================================================================

class TestDoPost:

    def _post_handler(
        self,
        path: str,
        body: dict | None = None,
        runtime: _UiRuntime | None = None,
        content_length: str | None = None,
    ) -> _BrowserUiHandler:
        handler = _make_handler(runtime or _make_runtime(), path=path)
        handler.command = "POST"
        raw = json.dumps(body or {}).encode("utf-8")
        handler.rfile = io.BytesIO(raw)
        handler.headers = {"Content-Length": content_length or str(len(raw))}
        handler.send_response = MagicMock()
        handler.send_header = MagicMock()
        handler.end_headers = MagicMock()
        return handler

    def test_unknown_post_path_returns_404(self):
        handler = self._post_handler("/api/unknown")
        handler.do_POST()
        written = handler.wfile.getvalue().decode("utf-8")
        assert written == "not found"

    def test_chat_redirects_to_stream(self):
        handler = self._post_handler("/api/chat")
        handler.do_POST()
        handler.send_response.assert_called_with(HTTPStatus.TEMPORARY_REDIRECT)
        handler.send_header.assert_any_call("Location", "/api/chat/stream")

    def test_chat_stream_missing_message(self):
        handler = self._post_handler("/api/chat/stream", {"message": ""})
        handler.do_POST()
        data = json.loads(handler.wfile.getvalue().decode("utf-8"))
        assert data["error"] == "message is required"

    def test_detail_save_missing_item(self):
        handler = self._post_handler(
            "/api/detail/save",
            {"content": "stuff", "item": ""},
        )
        handler.do_POST()
        data = json.loads(handler.wfile.getvalue().decode("utf-8"))
        assert data["error"] == "item is required"

    def test_detail_save_success(self):
        rt = _make_runtime()
        rt.run = MagicMock(return_value="AGENTS.md 已保存")
        handler = self._post_handler(
            "/api/detail/save",
            {"content": "new content", "item": "AGENTS.md"},
            runtime=rt,
        )
        handler.do_POST()
        data = json.loads(handler.wfile.getvalue().decode("utf-8"))
        assert "已保存" in data["message"]

    def test_detail_save_error(self):
        rt = _make_runtime()
        rt.run = MagicMock(side_effect=ValueError("Unsupported detail item: EVIL.md"))
        handler = self._post_handler(
            "/api/detail/save",
            {"content": "stuff", "item": "EVIL.md"},
            runtime=rt,
        )
        handler.do_POST()
        data = json.loads(handler.wfile.getvalue().decode("utf-8"))
        assert "error" in data
        handler.send_response.assert_called_with(HTTPStatus.BAD_REQUEST)

    def test_config_validate_success(self):
        rt = _make_runtime()
        rt.run = MagicMock(return_value={"valid": True, "message": "ok"})
        handler = self._post_handler(
            "/api/config/validate",
            {"content": "key: value"},
            runtime=rt,
        )
        handler.do_POST()
        data = json.loads(handler.wfile.getvalue().decode("utf-8"))
        assert data["valid"] is True

    def test_config_validate_error(self):
        rt = _make_runtime()
        rt.run = MagicMock(side_effect=Exception("boom"))
        handler = self._post_handler(
            "/api/config/validate",
            {"content": "bad"},
            runtime=rt,
        )
        handler.do_POST()
        data = json.loads(handler.wfile.getvalue().decode("utf-8"))
        assert "error" in data
        handler.send_response.assert_called_with(HTTPStatus.INTERNAL_SERVER_ERROR)

    def test_config_save_success(self):
        rt = _make_runtime()
        rt.run = MagicMock(return_value="配置已保存并重新加载")
        handler = self._post_handler(
            "/api/config/save",
            {"content": "key: value"},
            runtime=rt,
        )
        handler.do_POST()
        data = json.loads(handler.wfile.getvalue().decode("utf-8"))
        assert "已保存" in data["message"]

    def test_config_save_error_includes_validation(self):
        rt = _make_runtime()
        call_count = [0]

        def _side_effect(coro):
            call_count[0] += 1
            if call_count[0] == 1:
                raise ValueError("parse error")
            return {"valid": False, "line": 3, "column": 5}

        rt.run = MagicMock(side_effect=_side_effect)
        handler = self._post_handler(
            "/api/config/save",
            {"content": "bad: ["},
            runtime=rt,
        )
        handler.do_POST()
        data = json.loads(handler.wfile.getvalue().decode("utf-8"))
        assert "error" in data
        assert data["line"] == 3
        assert data["column"] == 5
        handler.send_response.assert_called_with(HTTPStatus.BAD_REQUEST)

    def test_content_not_string_returns_error(self):
        handler = self._post_handler(
            "/api/config/save",
            {"content": 123},
        )
        handler.do_POST()
        data = json.loads(handler.wfile.getvalue().decode("utf-8"))
        assert data["error"] == "content must be a string"

    def test_invalid_content_length_treated_as_zero(self):
        handler = self._post_handler(
            "/api/config/validate",
            {"content": "key: val"},
            content_length="not_a_number",
        )
        handler.do_POST()
        data = json.loads(handler.wfile.getvalue().decode("utf-8"))
        assert data["error"] == "content must be a string"


# =========================================================================
# _BrowserUiHandler.log_message — suppressed
# =========================================================================

class TestLogMessage:

    def test_returns_none(self):
        handler = _make_handler()
        result = handler.log_message("GET %s", "/")
        assert result is None


# =========================================================================
# _UiRuntime.__init__
# =========================================================================

class TestUiRuntimeInit:

    @patch("trpc_agent_sdk.server.openclaw.ui.ClawApplication")
    @patch("trpc_agent_sdk.server.openclaw.ui.DEFAULT_CONFIG_PATH", new=Path("/tmp/default_config.yaml"))
    def test_init_defaults(self, mock_claw_cls, tmp_path):
        mock_app = MagicMock()
        mock_claw_cls.return_value = mock_app

        rt = object.__new__(_UiRuntime)
        rt.app = None
        rt.session_id = "ui-default"
        rt.user_id = "ui_test_user"
        rt.chat_id = "webui"
        rt._workspace = tmp_path
        rt._config_path = tmp_path / "cfg.yaml"
        rt._loop = asyncio.new_event_loop()
        rt._thread = threading.Thread(target=rt._run_loop, daemon=True)
        rt._thread.start()

        async def _mock_create_app(**kwargs):
            return mock_app

        with patch.object(type(rt), '_create_app', new=_mock_create_app):
            rt.app = rt.run(_mock_create_app(workspace=tmp_path, config_path=tmp_path / "cfg.yaml"))

        assert rt.app is mock_app
        rt._loop.call_soon_threadsafe(rt._loop.stop)
        rt._thread.join(timeout=2.0)

    @patch("trpc_agent_sdk.server.openclaw.ui.DEFAULT_CONFIG_PATH", new=Path("/tmp/fallback.yaml"))
    def test_init_no_config_uses_default(self):
        """When config_path is None, DEFAULT_CONFIG_PATH is used."""
        rt = object.__new__(_UiRuntime)
        rt._config_path = None
        config_path = None
        resolved = (Path("/tmp/fallback.yaml").expanduser().resolve()
                     if config_path is None else config_path)
        assert resolved == Path("/tmp/fallback.yaml").resolve()


# =========================================================================
# _UiRuntime._run_loop
# =========================================================================

class TestRunLoop:

    def test_run_loop_sets_event_loop(self):
        loop = asyncio.new_event_loop()
        rt = _make_runtime()
        rt._loop = loop

        captured_loop = []

        original_run_forever = loop.run_forever

        def _spy_run_forever():
            captured_loop.append(asyncio.get_event_loop())
            loop.call_soon(loop.stop)
            original_run_forever()

        with patch.object(loop, "run_forever", _spy_run_forever):
            rt._run_loop()

        assert captured_loop[0] is loop
        loop.close()


# =========================================================================
# _UiRuntime.run
# =========================================================================

class TestRun:

    def test_run_submits_to_loop(self):
        loop = asyncio.new_event_loop()
        thread = threading.Thread(target=lambda: (asyncio.set_event_loop(loop), loop.run_forever()), daemon=True)
        thread.start()

        rt = _make_runtime()
        rt._loop = loop

        async def _coro():
            return 42

        result = rt.run(_coro())
        assert result == 42

        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=2.0)
        loop.close()


# =========================================================================
# _UiRuntime.close
# =========================================================================

class TestClose:

    def test_close_with_app(self):
        mock_app = MagicMock()
        mock_app.memory_service.close = AsyncMock()
        rt = _make_runtime(app=mock_app)
        rt.run = MagicMock(return_value=None)
        rt.close()
        rt.run.assert_called_once()
        rt._loop.call_soon_threadsafe.assert_called_once_with(rt._loop.stop)
        rt._thread.join.assert_called_once_with(timeout=1.0)

    def test_close_without_app(self):
        rt = _make_runtime(app=None)
        rt.run = MagicMock()
        rt.close()
        rt.run.assert_not_called()
        rt._loop.call_soon_threadsafe.assert_called_once_with(rt._loop.stop)

    def test_close_app_error_swallowed(self):
        mock_app = MagicMock()
        rt = _make_runtime(app=mock_app)
        rt.run = MagicMock(side_effect=RuntimeError("close failed"))
        rt.close()
        rt._loop.call_soon_threadsafe.assert_called_once_with(rt._loop.stop)
        rt._thread.join.assert_called_once_with(timeout=1.0)


# =========================================================================
# _UiRuntime._create_app
# =========================================================================

class TestCreateApp:

    @patch("trpc_agent_sdk.server.openclaw.ui.ClawApplication")
    async def test_create_app(self, mock_cls, tmp_path):
        mock_cls.return_value = MagicMock(name="app_instance")
        rt = _make_runtime()
        result = await rt._create_app(workspace=tmp_path, config_path=tmp_path / "c.yaml")
        mock_cls.assert_called_once_with(workspace=tmp_path, config_path=tmp_path / "c.yaml")
        assert result is mock_cls.return_value


# =========================================================================
# _UiRuntime._close_app
# =========================================================================

class TestCloseApp:

    async def test_close_app_success(self):
        mock_app = MagicMock()
        mock_app.memory_service.close = AsyncMock()
        rt = _make_runtime(app=mock_app)
        await rt._close_app()
        mock_app.memory_service.close.assert_awaited_once()

    async def test_close_app_none(self):
        rt = _make_runtime(app=None)
        await rt._close_app()

    async def test_close_app_exception_swallowed(self):
        mock_app = MagicMock()
        mock_app.memory_service.close = AsyncMock(side_effect=RuntimeError("fail"))
        rt = _make_runtime(app=mock_app)
        await rt._close_app()


# =========================================================================
# _UiRuntime.save_config_text
# =========================================================================

class TestSaveConfigText:

    async def test_save_yaml(self, tmp_path):
        cfg = tmp_path / "config.yaml"
        old_app = MagicMock()
        old_app.memory_service.close = AsyncMock()
        new_app = MagicMock(name="new_app")

        rt = _make_runtime(config_path=cfg, app=old_app)

        async def _fake_create_app(workspace=None, config_path=None):
            return new_app

        with patch.object(rt, "_create_app", side_effect=_fake_create_app):
            result = await rt.save_config_text("model: gpt-4\n")

        assert "已保存" in result
        assert cfg.exists()
        content = yaml.safe_load(cfg.read_text(encoding="utf-8"))
        assert content == {"model": "gpt-4"}
        assert rt.app is new_app
        old_app.memory_service.close.assert_awaited_once()

    async def test_save_json(self, tmp_path):
        cfg = tmp_path / "config.json"
        rt = _make_runtime(config_path=cfg, app=None)
        new_app = MagicMock(name="new_app")

        async def _fake_create_app(workspace=None, config_path=None):
            return new_app

        with patch.object(rt, "_create_app", side_effect=_fake_create_app):
            result = await rt.save_config_text('{"key": "value"}')

        assert "已保存" in result
        assert cfg.exists()
        assert json.loads(cfg.read_text(encoding="utf-8")) == {"key": "value"}
        assert rt.app is new_app

    async def test_save_creates_parent_dirs(self, tmp_path):
        cfg = tmp_path / "deep" / "nested" / "config.yaml"
        rt = _make_runtime(config_path=cfg, app=None)
        new_app = MagicMock()

        async def _fake_create_app(**kw):
            return new_app

        with patch.object(rt, "_create_app", side_effect=_fake_create_app):
            await rt.save_config_text("a: 1\n")

        assert cfg.exists()

    async def test_save_old_app_close_error_swallowed(self, tmp_path):
        cfg = tmp_path / "config.yaml"
        old_app = MagicMock()
        old_app.memory_service.close = AsyncMock(side_effect=RuntimeError("boom"))
        rt = _make_runtime(config_path=cfg, app=old_app)
        new_app = MagicMock()

        async def _fake_create_app(**kw):
            return new_app

        with patch.object(rt, "_create_app", side_effect=_fake_create_app):
            result = await rt.save_config_text("x: 1\n")

        assert "已保存" in result
        assert rt.app is new_app

    async def test_save_empty_content(self, tmp_path):
        cfg = tmp_path / "config.yaml"
        rt = _make_runtime(config_path=cfg, app=None)
        new_app = MagicMock()

        async def _fake_create_app(**kw):
            return new_app

        with patch.object(rt, "_create_app", side_effect=_fake_create_app):
            await rt.save_config_text("")

        assert cfg.exists()

    async def test_save_invalid_yaml_raises(self, tmp_path):
        cfg = tmp_path / "config.yaml"
        rt = _make_runtime(config_path=cfg, app=None)
        with pytest.raises(yaml.YAMLError):
            await rt.save_config_text("key: :\n  bad: [")


# =========================================================================
# _UiRuntime._resolve_short_memory_path
# =========================================================================

class TestResolveShortMemoryPath:

    def test_resolves_path(self, tmp_path):
        mock_app = MagicMock()
        mock_app.config.runtime.app_name = "myapp"
        expected_path = tmp_path / "session.json"
        mock_app.session_service._get_session_path.return_value = expected_path
        rt = _make_runtime(app=mock_app)

        with patch("trpc_agent_sdk.server.openclaw.ui.make_memory_key", return_value="myapp:ui_test_user:ui-default"):
            path, candidates = rt._resolve_short_memory_path()

        assert path == expected_path
        assert str(expected_path) in candidates

    def test_raises_when_no_app(self):
        rt = _make_runtime(app=None)
        with pytest.raises(RuntimeError, match="not initialized"):
            rt._resolve_short_memory_path()


# =========================================================================
# _UiRuntime._handle_ui_command
# =========================================================================

class TestHandleUiCommand:

    async def test_handled_command_returns_reply(self):
        mock_app = MagicMock()
        mock_app.command_handler.handle = AsyncMock(return_value=True)
        outbound_msg = MagicMock()
        outbound_msg.channel = "ui"
        outbound_msg.chat_id = "webui"
        outbound_msg.content = "Command executed!"

        q = asyncio.Queue()
        await q.put(outbound_msg)
        mock_app.bus.outbound = q

        rt = _make_runtime(app=mock_app)
        result = await rt._handle_ui_command("/some_command")

        assert result == "Command executed!"
        mock_app.command_handler.handle.assert_awaited_once()

    async def test_unhandled_command_returns_none(self):
        mock_app = MagicMock()
        mock_app.command_handler.handle = AsyncMock(return_value=False)
        rt = _make_runtime(app=mock_app)
        result = await rt._handle_ui_command("hello")
        assert result is None

    async def test_no_app_raises(self):
        rt = _make_runtime(app=None)
        with pytest.raises(RuntimeError, match="not initialized"):
            await rt._handle_ui_command("test")

    async def test_multiple_replies_joined(self):
        mock_app = MagicMock()
        mock_app.command_handler.handle = AsyncMock(return_value=True)

        q = asyncio.Queue()
        for text in ["line1", "line2"]:
            msg = MagicMock()
            msg.channel = "ui"
            msg.chat_id = "webui"
            msg.content = text
            await q.put(msg)
        mock_app.bus.outbound = q

        rt = _make_runtime(app=mock_app)
        result = await rt._handle_ui_command("/cmd")
        assert result == "line1\nline2"

    async def test_filters_other_channels(self):
        mock_app = MagicMock()
        mock_app.command_handler.handle = AsyncMock(return_value=True)

        q = asyncio.Queue()
        other_msg = MagicMock()
        other_msg.channel = "slack"
        other_msg.chat_id = "webui"
        other_msg.content = "should be filtered"
        await q.put(other_msg)

        ui_msg = MagicMock()
        ui_msg.channel = "ui"
        ui_msg.chat_id = "webui"
        ui_msg.content = "visible"
        await q.put(ui_msg)

        mock_app.bus.outbound = q
        rt = _make_runtime(app=mock_app)
        result = await rt._handle_ui_command("/cmd")
        assert result == "visible"

    async def test_empty_queue_returns_empty(self):
        mock_app = MagicMock()
        mock_app.command_handler.handle = AsyncMock(return_value=True)
        mock_app.bus.outbound = asyncio.Queue()
        rt = _make_runtime(app=mock_app)
        result = await rt._handle_ui_command("/cmd")
        assert result == ""


# =========================================================================
# _UiRuntime.chat
# =========================================================================

class TestChat:

    async def test_chat_command(self):
        mock_app = MagicMock()
        rt = _make_runtime(app=mock_app)

        with patch.object(rt, "_handle_ui_command", new_callable=AsyncMock, return_value="cmd result"):
            result = await rt.chat("/help")

        assert result == "cmd result"

    async def test_chat_normal_message(self):
        mock_app = MagicMock()
        mock_app._run_turn = AsyncMock(return_value="model reply")
        rt = _make_runtime(app=mock_app)

        with patch.object(rt, "_handle_ui_command", new_callable=AsyncMock, return_value=None):
            result = await rt.chat("hello")

        assert result == "model reply"
        mock_app._run_turn.assert_awaited_once()
        kwargs = mock_app._run_turn.call_args.kwargs
        assert kwargs["user_id"] == "ui_test_user"
        assert kwargs["session_id"] == "ui-default"
        assert kwargs["query"] == "hello"
        assert kwargs["channel"] == "ui"
        assert kwargs["stream_progress"] is False

    async def test_chat_no_app_raises(self):
        rt = _make_runtime(app=None)
        with pytest.raises(RuntimeError, match="not initialized"):
            await rt.chat("hi")


# =========================================================================
# _UiRuntime.stream_chat
# =========================================================================

class TestStreamChat:

    @staticmethod
    def _run_sync(coro):
        """Run a coroutine synchronously with a fresh event loop."""
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()

    def test_stream_chat_command_reply(self):
        mock_app = MagicMock()
        rt = _make_runtime(app=mock_app)
        rt._handle_ui_command = AsyncMock(return_value="cmd output")
        rt.run = self._run_sync

        deltas = []
        rt.stream_chat("/cmd", lambda chunk: deltas.append(chunk))
        assert "cmd output" in deltas

    def test_stream_chat_normal_with_progress(self):
        mock_app = MagicMock()
        rt = _make_runtime(app=mock_app)
        rt._handle_ui_command = AsyncMock(return_value=None)

        async def _fake_run_turn(**kwargs):
            cb = kwargs.get("progress_callback")
            if cb:
                cb("hello ")
                cb("hello world")
            return "hello world"

        mock_app._run_turn = _fake_run_turn
        rt.run = self._run_sync

        deltas = []
        rt.stream_chat("hi", lambda chunk: deltas.append(chunk))
        assert len(deltas) >= 1
        assert "".join(deltas) == "hello world"

    def test_stream_chat_no_progress_emits_final(self):
        mock_app = MagicMock()
        rt = _make_runtime(app=mock_app)
        rt._handle_ui_command = AsyncMock(return_value=None)

        async def _fake_run_turn(**kwargs):
            return "final answer"

        mock_app._run_turn = _fake_run_turn
        rt.run = self._run_sync

        deltas = []
        rt.stream_chat("hi", lambda chunk: deltas.append(chunk))
        assert "final answer" in "".join(deltas)

    def test_stream_chat_no_app_raises(self):
        rt = _make_runtime(app=None)
        rt.run = self._run_sync
        with pytest.raises(RuntimeError, match="not initialized"):
            rt.stream_chat("hi", lambda c: None)

    def test_stream_chat_empty_command_reply(self):
        mock_app = MagicMock()
        rt = _make_runtime(app=mock_app)
        rt._handle_ui_command = AsyncMock(return_value="")
        rt.run = self._run_sync

        deltas = []
        rt.stream_chat("/cmd", lambda chunk: deltas.append(chunk))
        assert deltas == []


# =========================================================================
# _UiRuntime.read_info
# =========================================================================

class TestReadInfo:

    async def test_read_info_config(self):
        mock_app = MagicMock()
        mock_app.config.model_dump.return_value = {"model": "gpt-4"}
        rt = _make_runtime(app=mock_app)
        result = await rt.read_info("config")
        assert result == {"model": "gpt-4"}
        mock_app.config.model_dump.assert_called_once_with(mode="json")

    async def test_read_info_model_name(self):
        mock_app = MagicMock()
        mock_app.config.model_name = "gpt-4-turbo"
        rt = _make_runtime(app=mock_app)
        result = await rt.read_info("model_name")
        assert result == "gpt-4-turbo"

    async def test_read_info_model_url(self):
        mock_app = MagicMock()
        mock_app.config.model_base_url = "https://api.openai.com/v1"
        rt = _make_runtime(app=mock_app)
        result = await rt.read_info("model_url")
        assert result == "https://api.openai.com/v1"

    async def test_read_info_model_key_long(self):
        mock_app = MagicMock()
        mock_app.config.model_api_key = "sk-1234567890abcdef"
        rt = _make_runtime(app=mock_app)
        result = await rt.read_info("model_key")
        assert result == "sk-1***cdef"

    async def test_read_info_model_key_short(self):
        mock_app = MagicMock()
        mock_app.config.model_api_key = "12345678"
        rt = _make_runtime(app=mock_app)
        result = await rt.read_info("model_key")
        assert result == "********"

    async def test_read_info_model_key_very_short(self):
        mock_app = MagicMock()
        mock_app.config.model_api_key = "abc"
        rt = _make_runtime(app=mock_app)
        result = await rt.read_info("model_key")
        assert result == "***"

    async def test_read_info_model_key_empty(self):
        mock_app = MagicMock()
        mock_app.config.model_api_key = ""
        rt = _make_runtime(app=mock_app)
        result = await rt.read_info("model_key")
        assert result == ""

    async def test_read_info_model_key_none(self):
        mock_app = MagicMock()
        mock_app.config.model_api_key = None
        rt = _make_runtime(app=mock_app)
        result = await rt.read_info("model_key")
        assert result == ""

    async def test_read_info_tool(self):
        tool1 = MagicMock()
        tool1.name = "search"
        tool2 = MagicMock()
        tool2.name = "calculator"
        tool3 = MagicMock(spec=[])
        tool3.__class__ = type("CustomTool", (), {})
        mock_app = MagicMock()
        mock_app.agent.tools = [tool1, tool2, tool3]
        rt = _make_runtime(app=mock_app)
        result = await rt.read_info("tool")
        assert "calculator" in result
        assert "search" in result

    async def test_read_info_tool_empty(self):
        mock_app = MagicMock()
        mock_app.agent.tools = []
        rt = _make_runtime(app=mock_app)
        result = await rt.read_info("tool")
        assert result == []

    async def test_read_info_tool_none_list(self):
        mock_app = MagicMock()
        mock_app.agent.tools = None
        rt = _make_runtime(app=mock_app)
        result = await rt.read_info("tool")
        assert result == []

    async def test_read_info_skill(self):
        mock_app = MagicMock()
        repo = MagicMock()
        repo.skill_list.return_value = ["skill_b", "skill_a"]
        mock_app.agent.skill_repository = repo
        mock_app.config.skills.sandbox_type = "docker"
        mock_app.config.skills.skills_root = "/skills"
        mock_app.config.skills.builtin_skill_roots = ["/builtin"]
        rt = _make_runtime(app=mock_app)
        result = await rt.read_info("skill")
        assert result["sandbox_type"] == "docker"
        assert result["skills"] == ["skill_a", "skill_b"]

    async def test_read_info_skill_no_repo(self):
        mock_app = MagicMock()
        mock_app.agent.skill_repository = None
        mock_app.config.skills.sandbox_type = "local"
        mock_app.config.skills.skills_root = ""
        mock_app.config.skills.builtin_skill_roots = []
        rt = _make_runtime(app=mock_app)
        result = await rt.read_info("skill")
        assert result["skills"] == []

    async def test_read_info_session_exists(self):
        mock_app = MagicMock()
        mock_app.config.runtime.app_name = "testapp"
        session = MagicMock()
        session.id = "sess-123"
        session.conversation_count = 5
        session.last_update_time = 1234567890
        session.state = {"key": "val"}
        session.events = [1, 2, 3]
        mock_app.session_service.get_session = AsyncMock(return_value=session)
        rt = _make_runtime(app=mock_app)

        with patch("trpc_agent_sdk.server.openclaw.ui.make_memory_key", return_value="k"):
            result = await rt.read_info("session")

        assert result["exists"] is True
        assert result["id"] == "sess-123"
        assert result["conversation_count"] == 5
        assert result["event_count"] == 3

    async def test_read_info_session_not_exists(self):
        mock_app = MagicMock()
        mock_app.config.runtime.app_name = "testapp"
        mock_app.session_service.get_session = AsyncMock(return_value=None)
        rt = _make_runtime(app=mock_app)

        with patch("trpc_agent_sdk.server.openclaw.ui.make_memory_key", return_value="k"):
            result = await rt.read_info("session")

        assert result == {"exists": False}

    async def test_read_info_memory(self):
        mock_app = MagicMock()
        mock_app.config.runtime.app_name = "app"
        mock_app._storage_manager.read_long_term = AsyncMock(return_value={"data": "mem"})
        rt = _make_runtime(app=mock_app)

        with patch("trpc_agent_sdk.server.openclaw.ui.make_memory_key", return_value="k"):
            result = await rt.read_info("memory")

        assert result == {"data": "mem"}

    async def test_read_info_short_memory_exists(self, tmp_path):
        mock_app = MagicMock()
        mock_app.config.runtime.app_name = "app"
        sm_path = tmp_path / "short_mem.json"
        sm_path.write_text("short memory content", encoding="utf-8")
        rt = _make_runtime(app=mock_app)

        with patch.object(rt, "_resolve_short_memory_path", return_value=(sm_path, [str(sm_path)])):
            with patch("trpc_agent_sdk.server.openclaw.ui.make_memory_key", return_value="k"):
                result = await rt.read_info("short_memory")

        assert result["exists"] is True
        assert result["content"] == "short memory content"
        assert result["path"] == str(sm_path)

    async def test_read_info_short_memory_not_exists(self, tmp_path):
        mock_app = MagicMock()
        mock_app.config.runtime.app_name = "app"
        sm_path = tmp_path / "nonexistent.json"
        rt = _make_runtime(app=mock_app)

        with patch.object(rt, "_resolve_short_memory_path", return_value=(sm_path, [str(sm_path)])):
            with patch("trpc_agent_sdk.server.openclaw.ui.make_memory_key", return_value="k"):
                result = await rt.read_info("short_memory")

        assert result["exists"] is False
        assert result["content"] == ""

    async def test_read_info_history(self):
        mock_app = MagicMock()
        mock_app.config.runtime.app_name = "app"
        mock_app._storage_manager._history_content_key.return_value = "hist_key"
        mock_app._storage_manager._get_value = AsyncMock(return_value="history data")
        rt = _make_runtime(app=mock_app)

        with patch("trpc_agent_sdk.server.openclaw.ui.make_memory_key", return_value="k"):
            result = await rt.read_info("history")

        assert result == "history data"

    async def test_read_info_history_empty(self):
        mock_app = MagicMock()
        mock_app.config.runtime.app_name = "app"
        mock_app._storage_manager._history_content_key.return_value = "hist_key"
        mock_app._storage_manager._get_value = AsyncMock(return_value=None)
        rt = _make_runtime(app=mock_app)

        with patch("trpc_agent_sdk.server.openclaw.ui.make_memory_key", return_value="k"):
            result = await rt.read_info("history")

        assert result == ""

    async def test_read_info_unknown_item(self):
        mock_app = MagicMock()
        rt = _make_runtime(app=mock_app)
        result = await rt.read_info("nonexistent")
        assert result == "unknown item: nonexistent"

    async def test_read_info_no_app_raises(self):
        rt = _make_runtime(app=None)
        with pytest.raises(RuntimeError, match="not initialized"):
            await rt.read_info("config")


# =========================================================================
# _run_browser_mode
# =========================================================================

class TestRunBrowserMode:

    def test_delegates_to_with_bind(self):
        rt = _make_runtime()
        with patch("trpc_agent_sdk.server.openclaw.ui._run_browser_mode_with_bind") as mock_bind:
            _run_browser_mode(rt, host="0.0.0.0", port=8080, open_browser=False, config_path=Path("/cfg.yaml"))
            mock_bind.assert_called_once_with(
                rt,
                host="0.0.0.0",
                port=8080,
                open_browser=False,
                watch_config_path=Path("/cfg.yaml"),
            )

    def test_defaults(self):
        rt = _make_runtime()
        with patch("trpc_agent_sdk.server.openclaw.ui._run_browser_mode_with_bind") as mock_bind:
            _run_browser_mode(rt)
            mock_bind.assert_called_once_with(
                rt,
                host="127.0.0.1",
                port=0,
                open_browser=True,
                watch_config_path=None,
            )


# =========================================================================
# _run_browser_mode_with_bind
# =========================================================================

class TestRunBrowserModeWithBind:

    @patch("trpc_agent_sdk.server.openclaw.ui.webbrowser")
    @patch("trpc_agent_sdk.server.openclaw.ui.ThreadingHTTPServer")
    def test_starts_server_and_opens_browser(self, mock_server_cls, mock_wb):
        mock_server = MagicMock()
        mock_server.server_address = ("127.0.0.1", 9999)
        mock_server_cls.return_value = mock_server

        rt = _make_runtime()
        call_count = [0]

        def _sleep_then_interrupt(seconds):
            call_count[0] += 1
            if call_count[0] >= 1:
                raise KeyboardInterrupt()

        with patch("trpc_agent_sdk.server.openclaw.ui.time.sleep", side_effect=_sleep_then_interrupt):
            with patch("builtins.print"):
                _run_browser_mode_with_bind(
                    rt, host="127.0.0.1", port=9999, open_browser=True
                )

        mock_server_cls.assert_called_once()
        mock_wb.open.assert_called_once_with("http://127.0.0.1:9999", new=1, autoraise=True)
        mock_server.shutdown.assert_called_once()
        mock_server.server_close.assert_called_once()

    @patch("trpc_agent_sdk.server.openclaw.ui.webbrowser")
    @patch("trpc_agent_sdk.server.openclaw.ui.ThreadingHTTPServer")
    def test_no_open_browser(self, mock_server_cls, mock_wb):
        mock_server = MagicMock()
        mock_server.server_address = ("127.0.0.1", 8888)
        mock_server_cls.return_value = mock_server

        rt = _make_runtime()

        with patch("trpc_agent_sdk.server.openclaw.ui.time.sleep", side_effect=KeyboardInterrupt):
            with patch("builtins.print"):
                _run_browser_mode_with_bind(
                    rt, host="127.0.0.1", port=8888, open_browser=False
                )

        mock_wb.open.assert_not_called()

    @patch("trpc_agent_sdk.server.openclaw.ui._restart_current_process")
    @patch("trpc_agent_sdk.server.openclaw.ui.webbrowser")
    @patch("trpc_agent_sdk.server.openclaw.ui.ThreadingHTTPServer")
    def test_config_change_triggers_restart(self, mock_server_cls, mock_wb, mock_restart):
        mock_server = MagicMock()
        mock_server.server_address = ("127.0.0.1", 7777)
        mock_server_cls.return_value = mock_server

        rt = _make_runtime()
        cfg_path = Path("/fake/config.yaml")

        mtime_calls = [0]

        def _changing_mtime(path):
            mtime_calls[0] += 1
            if mtime_calls[0] <= 1:
                return 100.0
            return 200.0

        mock_restart.side_effect = KeyboardInterrupt

        with (
            patch("trpc_agent_sdk.server.openclaw.ui.time.sleep"),
            patch("trpc_agent_sdk.server.openclaw.ui._file_mtime", side_effect=_changing_mtime),
            patch("builtins.print"),
        ):
            _run_browser_mode_with_bind(
                rt, host="127.0.0.1", port=7777, open_browser=False,
                watch_config_path=cfg_path,
            )

        mock_restart.assert_called_once()

    @patch("trpc_agent_sdk.server.openclaw.ui.webbrowser")
    @patch("trpc_agent_sdk.server.openclaw.ui.ThreadingHTTPServer")
    def test_server_thread_join_on_cleanup(self, mock_server_cls, mock_wb):
        mock_server = MagicMock()
        mock_server.server_address = ("127.0.0.1", 6666)
        mock_server_cls.return_value = mock_server

        rt = _make_runtime()

        with patch("trpc_agent_sdk.server.openclaw.ui.time.sleep", side_effect=KeyboardInterrupt):
            with patch("builtins.print"):
                _run_browser_mode_with_bind(
                    rt, host="127.0.0.1", port=6666, open_browser=False
                )

        mock_server.shutdown.assert_called_once()


# =========================================================================
# _restart_current_process
# =========================================================================

class TestRestartCurrentProcess:

    @patch("trpc_agent_sdk.server.openclaw.ui.os.execv")
    def test_sets_env_and_calls_execv(self, mock_execv):
        with patch.dict(os.environ, {}, clear=False):
            _restart_current_process()
            assert os.environ["TRPC_CLAW_UI_RESTARTING"] == "1"
            mock_execv.assert_called_once_with(sys.executable, [sys.executable, *sys.argv])


# =========================================================================
# run_ui_server
# =========================================================================

class TestRunUiServer:

    @patch("trpc_agent_sdk.server.openclaw.ui._run_browser_mode")
    @patch("trpc_agent_sdk.server.openclaw.ui._UiRuntime")
    @patch("trpc_agent_sdk.server.openclaw.ui._pick_free_port", return_value=5555)
    def test_basic_invocation(self, mock_port, mock_rt_cls, mock_run_browser):
        mock_rt = MagicMock()
        mock_rt_cls.return_value = mock_rt

        with patch.dict(os.environ, {"TRPC_CLAW_UI_PORT": "", "TRPC_CLAW_UI_RESTARTING": ""}, clear=False):
            run_ui_server(workspace=Path("/ws"), config_path=Path("/ws/config.yaml"))

        mock_rt_cls.assert_called_once()
        mock_run_browser.assert_called_once()
        kwargs = mock_run_browser.call_args.kwargs
        assert kwargs["host"] == "127.0.0.1"
        assert kwargs["port"] == 5555
        assert kwargs["open_browser"] is True
        mock_rt.close.assert_called_once()

    @patch("trpc_agent_sdk.server.openclaw.ui._run_browser_mode")
    @patch("trpc_agent_sdk.server.openclaw.ui._UiRuntime")
    def test_configured_port(self, mock_rt_cls, mock_run_browser):
        mock_rt_cls.return_value = MagicMock()

        with patch.dict(os.environ, {"TRPC_CLAW_UI_PORT": "9876", "TRPC_CLAW_UI_RESTARTING": ""}, clear=False):
            run_ui_server(workspace=None, config_path=None)

        kwargs = mock_run_browser.call_args.kwargs
        assert kwargs["port"] == 9876

    @patch("trpc_agent_sdk.server.openclaw.ui._run_browser_mode")
    @patch("trpc_agent_sdk.server.openclaw.ui._UiRuntime")
    @patch("trpc_agent_sdk.server.openclaw.ui._pick_free_port", return_value=4444)
    def test_invalid_port_falls_back(self, mock_port, mock_rt_cls, mock_run_browser):
        mock_rt_cls.return_value = MagicMock()

        with patch.dict(os.environ, {"TRPC_CLAW_UI_PORT": "not_a_number", "TRPC_CLAW_UI_RESTARTING": ""}, clear=False):
            run_ui_server(workspace=None, config_path=None)

        kwargs = mock_run_browser.call_args.kwargs
        assert kwargs["port"] == 4444

    @patch("trpc_agent_sdk.server.openclaw.ui._run_browser_mode")
    @patch("trpc_agent_sdk.server.openclaw.ui._UiRuntime")
    @patch("trpc_agent_sdk.server.openclaw.ui._pick_free_port", return_value=3333)
    def test_restarting_skips_browser(self, mock_port, mock_rt_cls, mock_run_browser):
        mock_rt_cls.return_value = MagicMock()

        with patch.dict(os.environ, {"TRPC_CLAW_UI_PORT": "", "TRPC_CLAW_UI_RESTARTING": "1"}, clear=False):
            run_ui_server(workspace=None, config_path=None)

        kwargs = mock_run_browser.call_args.kwargs
        assert kwargs["open_browser"] is False

    @patch("trpc_agent_sdk.server.openclaw.ui._run_browser_mode", side_effect=RuntimeError("crash"))
    @patch("trpc_agent_sdk.server.openclaw.ui._UiRuntime")
    @patch("trpc_agent_sdk.server.openclaw.ui._pick_free_port", return_value=2222)
    def test_runtime_closed_on_error(self, mock_port, mock_rt_cls, mock_run_browser):
        mock_rt = MagicMock()
        mock_rt_cls.return_value = mock_rt

        with patch.dict(os.environ, {"TRPC_CLAW_UI_PORT": "", "TRPC_CLAW_UI_RESTARTING": ""}, clear=False):
            with pytest.raises(RuntimeError, match="crash"):
                run_ui_server(workspace=None, config_path=None)

        mock_rt.close.assert_called_once()

    @patch("trpc_agent_sdk.server.openclaw.ui._run_browser_mode")
    @patch("trpc_agent_sdk.server.openclaw.ui._UiRuntime")
    @patch("trpc_agent_sdk.server.openclaw.ui._pick_free_port", return_value=1111)
    @patch("trpc_agent_sdk.server.openclaw.ui.DEFAULT_CONFIG_PATH", new=Path("/default/config.yaml"))
    def test_no_config_uses_default(self, mock_port, mock_rt_cls, mock_run_browser):
        mock_rt_cls.return_value = MagicMock()

        with patch.dict(os.environ, {"TRPC_CLAW_UI_PORT": "", "TRPC_CLAW_UI_RESTARTING": ""}, clear=False):
            run_ui_server(workspace=None, config_path=None)

        init_kwargs = mock_rt_cls.call_args.kwargs
        assert init_kwargs["config_path"] == Path("/default/config.yaml").expanduser().resolve()


# =========================================================================
# _BrowserUiHandler.do_POST /api/chat/stream (integration with stream_chat)
# =========================================================================

class TestDoPostChatStream:
    """Test the JSON decode exception path and the streaming POST endpoint."""

    def _post_handler(
        self,
        path: str,
        body: dict | bytes | None = None,
        runtime: _UiRuntime | None = None,
    ) -> _BrowserUiHandler:
        handler = _make_handler(runtime or _make_runtime(), path=path)
        handler.command = "POST"
        if isinstance(body, bytes):
            raw = body
        else:
            raw = json.dumps(body or {}).encode("utf-8")
        handler.rfile = io.BytesIO(raw)
        handler.headers = {"Content-Length": str(len(raw))}
        handler.send_response = MagicMock()
        handler.send_header = MagicMock()
        handler.end_headers = MagicMock()
        return handler

    def test_malformed_json_body_treated_as_empty(self):
        handler = self._post_handler("/api/config/save", b"not json at all")
        handler.do_POST()
        data = json.loads(handler.wfile.getvalue().decode("utf-8"))
        assert data["error"] == "content must be a string"

    def test_stream_success(self):
        rt = _make_runtime(app=MagicMock())

        def _fake_stream_chat(message, on_delta):
            on_delta("hello ")
            on_delta("hello world")

        rt.stream_chat = _fake_stream_chat
        handler = self._post_handler("/api/chat/stream", {"message": "hi"}, runtime=rt)
        handler.do_POST()

        handler.send_response.assert_called_with(HTTPStatus.OK)
        handler.send_header.assert_any_call("Content-Type", "application/x-ndjson; charset=utf-8")

        raw_output = handler.wfile.getvalue().decode("utf-8")
        lines = [json.loads(line) for line in raw_output.strip().split("\n") if line.strip()]
        types = [l["type"] for l in lines]
        assert "delta" in types
        assert types[-1] == "done"

    def test_stream_error(self):
        rt = _make_runtime(app=MagicMock())

        def _failing_stream(message, on_delta):
            raise RuntimeError("model failed")

        rt.stream_chat = _failing_stream
        handler = self._post_handler("/api/chat/stream", {"message": "hi"}, runtime=rt)
        handler.do_POST()

        raw_output = handler.wfile.getvalue().decode("utf-8")
        lines = [json.loads(line) for line in raw_output.strip().split("\n") if line.strip()]
        error_lines = [l for l in lines if l["type"] == "error"]
        assert len(error_lines) == 1
        assert "model failed" in error_lines[0]["message"]

    def test_stream_broken_pipe(self):
        rt = _make_runtime(app=MagicMock())

        def _fake_stream_chat(message, on_delta):
            on_delta("some text")

        rt.stream_chat = _fake_stream_chat

        handler = self._post_handler("/api/chat/stream", {"message": "hi"}, runtime=rt)
        original_wfile = handler.wfile

        def _broken_write(data):
            raise BrokenPipeError("client disconnected")

        original_wfile.write = _broken_write
        handler.do_POST()

    def test_stream_duplicate_delta_skipped(self):
        rt = _make_runtime(app=MagicMock())

        def _fake_stream_chat(message, on_delta):
            on_delta("hello")
            on_delta("hello")

        rt.stream_chat = _fake_stream_chat
        handler = self._post_handler("/api/chat/stream", {"message": "hi"}, runtime=rt)
        handler.do_POST()

        raw_output = handler.wfile.getvalue().decode("utf-8")
        lines = [json.loads(line) for line in raw_output.strip().split("\n") if line.strip()]
        delta_lines = [l for l in lines if l["type"] == "delta"]
        assert len(delta_lines) == 1


# =========================================================================
# _run_browser_mode_with_bind – additional edge cases
# =========================================================================

class TestRunBrowserModeWithBindEdgeCases:

    @patch("trpc_agent_sdk.server.openclaw.ui.webbrowser")
    @patch("trpc_agent_sdk.server.openclaw.ui.ThreadingHTTPServer")
    def test_webbrowser_error_swallowed(self, mock_server_cls, mock_wb):
        mock_server = MagicMock()
        mock_server.server_address = ("127.0.0.1", 5555)
        mock_server_cls.return_value = mock_server
        mock_wb.open.side_effect = OSError("no browser")

        rt = _make_runtime()

        with patch("trpc_agent_sdk.server.openclaw.ui.time.sleep", side_effect=KeyboardInterrupt):
            with patch("builtins.print"):
                _run_browser_mode_with_bind(
                    rt, host="127.0.0.1", port=5555, open_browser=True
                )

        mock_wb.open.assert_called_once()
        mock_server.shutdown.assert_called_once()

    @patch("trpc_agent_sdk.server.openclaw.ui.webbrowser")
    @patch("trpc_agent_sdk.server.openclaw.ui.ThreadingHTTPServer")
    def test_server_thread_join_when_alive(self, mock_server_cls, mock_wb):
        mock_server = MagicMock()
        mock_server.server_address = ("127.0.0.1", 4444)
        mock_server_cls.return_value = mock_server

        alive_called = []
        join_called = []

        real_thread_init = threading.Thread.__init__

        class _MockThread:
            def __init__(self, target=None, daemon=False):
                self._target = target
                self.daemon = daemon

            def start(self):
                pass

            def is_alive(self):
                alive_called.append(True)
                return True

            def join(self, timeout=None):
                join_called.append(timeout)

        rt = _make_runtime()

        with (
            patch("trpc_agent_sdk.server.openclaw.ui.threading.Thread", _MockThread),
            patch("trpc_agent_sdk.server.openclaw.ui.time.sleep", side_effect=KeyboardInterrupt),
            patch("builtins.print"),
        ):
            _run_browser_mode_with_bind(
                rt, host="127.0.0.1", port=4444, open_browser=False
            )

        assert len(alive_called) == 1
        assert join_called == [1.0]
