# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Browser-only UI for trpc-claw."""

from __future__ import annotations

import asyncio
import importlib.resources as resources
import json
import os
import queue
import socket
import sys
import threading
import time
import uuid
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs
from urllib.parse import urlparse

import yaml
from nanobot.bus.events import InboundMessage
from trpc_agent_sdk.context import new_agent_context

from .claw import ClawApplication
from .claw import DEFAULT_USER_ID
from .config import DEFAULT_CONFIG_PATH
from .storage import make_memory_key


def _load_browser_html() -> str:
    """Load browser UI HTML template from package data."""
    return resources.files("trpc_claw").joinpath("templates", "ui.html").read_text(encoding="utf-8")


_BROWSER_HTML = _load_browser_html()


class _UiRuntime:
    """Threaded asyncio bridge around ClawApplication."""

    def __init__(self, workspace: Path | None, config_path: Path | None):
        self.app: ClawApplication | None = None
        self.session_id = "ui-default"
        self.user_id = f"ui_{DEFAULT_USER_ID}"
        self.chat_id = "webui"
        self._workspace = workspace
        self._config_path = (config_path.expanduser().resolve()
                             if config_path else DEFAULT_CONFIG_PATH.expanduser().resolve())
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        # ClawApplication initialization creates async cleanup tasks internally,
        # so it must run under an active event loop.
        self.app = self.run(self._create_app(workspace=self._workspace, config_path=self._config_path))

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def run(self, coro: Any) -> Any:
        fut = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return fut.result()

    def close(self) -> None:
        if self.app is not None:
            try:
                self.run(self._close_app())
            except Exception:
                pass
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=1.0)

    async def _create_app(self, workspace: Path | None, config_path: Path | None) -> ClawApplication:
        return ClawApplication(workspace=workspace, config_path=config_path)

    async def _close_app(self) -> None:
        if self.app is None:
            return
        try:
            await self.app.memory_service.close()
        except Exception:
            pass

    @staticmethod
    def _merge_text(current: str, incoming: str) -> str:
        """Merge text chunks while avoiding cumulative duplicates."""
        if not incoming:
            return current
        if not current:
            return incoming
        if incoming.startswith(current):
            return incoming
        if current.endswith(incoming):
            return current
        if incoming in current:
            return current
        if current in incoming:
            return incoming
        return current + incoming

    async def read_config_text(self) -> dict[str, str]:
        """Return current config file path/content for UI editing."""
        path = self._config_path
        if path.exists():
            content = path.read_text(encoding="utf-8")
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            config_data = self.app.config.model_dump(mode="json") if self.app else {}
            content = yaml.safe_dump(config_data, allow_unicode=True, sort_keys=False)
            path.write_text(content, encoding="utf-8")
        return {"path": str(path), "content": content}

    @staticmethod
    def _parse_config_text(text: str, suffix: str) -> tuple[dict[str, Any], str]:
        """Parse config text and return normalized output."""
        if suffix in {".yaml", ".yml"}:
            parsed = yaml.safe_load(text) or {}
        else:
            parsed = json.loads(text) if text.strip() else {}
        if not isinstance(parsed, dict):
            raise ValueError("Config root must be a JSON/YAML object")
        if suffix in {".yaml", ".yml"}:
            normalized = yaml.safe_dump(parsed, allow_unicode=True, sort_keys=False)
        else:
            normalized = json.dumps(parsed, ensure_ascii=False, indent=2)
        return parsed, normalized

    @staticmethod
    def _format_parse_error(exc: Exception, suffix: str) -> dict[str, Any]:
        """Convert parse exception to structured message with line info."""
        if suffix in {".yaml", ".yml"} and isinstance(exc, yaml.YAMLError):
            mark = getattr(exc, "problem_mark", None)
            line = (getattr(mark, "line", -1) + 1) if mark is not None else None
            column = (getattr(mark, "column", -1) + 1) if mark is not None else None
            problem = getattr(exc, "problem", "") or str(exc)
            msg = f"YAML 格式错误: {problem}"
            if line:
                msg += f"（第 {line} 行"
                if column:
                    msg += f"，第 {column} 列"
                msg += "）"
            return {"valid": False, "message": msg, "line": line, "column": column}
        if suffix not in {".yaml", ".yml"} and isinstance(exc, json.JSONDecodeError):
            msg = f"JSON 格式错误: {exc.msg}（第 {exc.lineno} 行，第 {exc.colno} 列）"
            return {"valid": False, "message": msg, "line": exc.lineno, "column": exc.colno}
        return {"valid": False, "message": str(exc), "line": None, "column": None}

    async def validate_config_text(self, content: str) -> dict[str, Any]:
        """Validate config text format before saving."""
        text = content or ""
        suffix = self._config_path.suffix.lower()
        try:
            self._parse_config_text(text, suffix)
            return {"valid": True, "message": "配置格式校验通过"}
        except Exception as exc:  # pylint: disable=broad-except
            return self._format_parse_error(exc, suffix)

    async def save_config_text(self, content: str) -> str:
        """Persist config text and hot-reload application runtime."""
        text = content or ""
        suffix = self._config_path.suffix.lower()
        _, normalized = self._parse_config_text(text, suffix)
        self._config_path.parent.mkdir(parents=True, exist_ok=True)
        self._config_path.write_text(normalized, encoding="utf-8")

        old_app = self.app
        new_app = await self._create_app(workspace=self._workspace, config_path=self._config_path)
        self.app = new_app
        if old_app is not None:
            try:
                await old_app.memory_service.close()
            except Exception:
                pass
        return "配置已保存并重新加载"

    def _detail_doc_path(self, item: str) -> Path:
        allowed = {"AGENTS.md", "SOUL.md", "USER.md", "TOOLS.md"}
        name = (item or "").strip()
        if name not in allowed:
            raise ValueError(f"Unsupported detail item: {name}")
        base = self.app.workspace if self.app is not None else (self._workspace or Path.cwd())
        return Path(base) / name

    async def read_detail_doc(self, item: str) -> dict[str, str]:
        """Read editable detail markdown doc under workspace."""
        path = self._detail_doc_path(item)
        if path.exists():
            content = path.read_text(encoding="utf-8")
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            content = ""
            path.write_text(content, encoding="utf-8")
        return {"path": str(path), "content": content}

    async def save_detail_doc(self, item: str, content: str) -> str:
        """Save editable detail markdown doc under workspace."""
        path = self._detail_doc_path(item)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content or "", encoding="utf-8")
        return f"{item} 已保存"

    def _resolve_short_memory_path(self) -> tuple[Path, list[str]]:
        """Resolve short-memory file path from current active session."""
        if self.app is None:
            raise RuntimeError("UI runtime is not initialized")
        app_name = self.app.config.runtime.app_name
        ui_key = make_memory_key(app_name, self.user_id, self.session_id)
        ui_path = self.app.session_service._get_session_path(ui_key)  # pylint: disable=protected-access
        return ui_path, [str(ui_path)]

    async def _handle_ui_command(self, message: str) -> str | None:
        """Run command handler for UI messages and return command reply text."""
        if self.app is None:
            raise RuntimeError("UI runtime is not initialized")
        msg = InboundMessage(
            channel="ui",
            sender_id=self.user_id,
            chat_id=self.chat_id,
            content=message,
        )
        handled = await self.app.command_handler.handle(msg)  # pylint: disable=protected-access
        if not handled:
            return None

        replies: list[str] = []
        while True:
            try:
                outbound = self.app.bus.outbound.get_nowait()
            except asyncio.QueueEmpty:
                break
            if outbound.channel == "ui" and outbound.chat_id == self.chat_id and outbound.content:
                replies.append(outbound.content)
        return "\n".join(replies).strip()

    async def chat(self, message: str) -> str:
        if self.app is None:
            raise RuntimeError("UI runtime is not initialized")
        command_reply = await self._handle_ui_command(message)
        if command_reply is not None:
            return command_reply
        return await self.app._run_turn(  # pylint: disable=protected-access
            user_id=self.user_id,
            session_id=self.session_id,
            query=message,
            channel="ui",
            chat_id=self.chat_id,
            message_id=uuid.uuid4().hex,
            stream_progress=False,
            passthrough_metadata={"source": "ui"},
        )

    def stream_chat(self, message: str, on_delta: Any) -> None:
        """Run one chat turn and emit incremental assistant text chunks."""

        async def _runner() -> None:
            if self.app is None:
                raise RuntimeError("UI runtime is not initialized")

            command_reply = await self._handle_ui_command(message)
            if command_reply is not None:
                if command_reply:
                    on_delta(command_reply)
                return

            streamed = ""
            saw_progress_delta = False

            def _emit(chunk: str) -> None:
                nonlocal streamed, saw_progress_delta
                merged = self._merge_text(streamed, chunk or "")
                delta = merged[len(streamed):] if merged.startswith(streamed) else merged
                streamed = merged
                if delta:
                    saw_progress_delta = True
                    on_delta(delta)

            final_text = await self.app._run_turn(  # pylint: disable=protected-access
                user_id=self.user_id,
                session_id=self.session_id,
                query=message,
                channel="ui",
                chat_id=self.chat_id,
                message_id=uuid.uuid4().hex,
                stream_progress=True,
                progress_callback=_emit,
                passthrough_metadata={"source": "ui"},
            )
            # If progress chunks were already emitted, do not append final_text again.
            # Some models/providers produce near-identical final outputs, which can
            # cause visible duplicated paragraphs in streaming UIs.
            if not saw_progress_delta:
                merged_final = self._merge_text(streamed, final_text or "")
                final_delta = merged_final[len(streamed):] if merged_final.startswith(streamed) else merged_final
                if final_delta:
                    on_delta(final_delta)

        self.run(_runner())

    async def read_info(self, item: str) -> Any:
        if self.app is None:
            raise RuntimeError("UI runtime is not initialized")
        if item == "config":
            return self.app.config.model_dump(mode="json")
        if item == "model_name":
            return self.app.config.model_name
        if item == "model_url":
            return self.app.config.model_base_url
        if item == "model_key":
            key = self.app.config.model_api_key or ""
            if not key:
                return ""
            if len(key) <= 8:
                return "*" * len(key)
            return f"{key[:4]}***{key[-4:]}"
        if item == "tool":
            names: list[str] = []
            for tool in getattr(self.app.agent, "tools", []) or []:
                tool_name = getattr(tool, "name", "") or tool.__class__.__name__
                names.append(str(tool_name))
            return sorted(set(names))
        if item == "skill":
            repo = getattr(self.app.agent, "skill_repository", None)
            skill_names = repo.skill_list() if repo and hasattr(repo, "skill_list") else []
            return {
                "sandbox_type": self.app.config.skills.sandbox_type,
                "skills_root": self.app.config.skills.skills_root,
                "builtin_skill_roots": self.app.config.skills.builtin_skill_roots,
                "skills": sorted(skill_names),
            }
        if item in {"session", "memory", "short_memory", "history"}:
            app_name = self.app.config.runtime.app_name
            memory_key = make_memory_key(app_name, self.user_id, self.session_id)
            if item == "session":
                session = await self.app.session_service.get_session(
                    app_name=app_name,
                    user_id=self.user_id,
                    session_id=self.session_id,
                    agent_context=new_agent_context(metadata={}),
                )
                if session is None:
                    return {"exists": False}
                return {
                    "exists": True,
                    "id": session.id,
                    "conversation_count": session.conversation_count,
                    "last_update_time": session.last_update_time,
                    "state": session.state,
                    "event_count": len(session.events),
                }
            if item == "memory":
                return await self.app._storage_manager.read_long_term(memory_key)  # pylint: disable=protected-access
            if item == "short_memory":
                short_memory_path, candidates = self._resolve_short_memory_path()
                if not short_memory_path.exists():
                    return {
                        "exists": False,
                        "path": str(short_memory_path),
                        "candidates": candidates,
                        "content": "",
                    }
                return {
                    "exists": True,
                    "path": str(short_memory_path),
                    "candidates": candidates,
                    "content": short_memory_path.read_text(encoding="utf-8"),
                }
            history_key = self.app._storage_manager._history_content_key(memory_key)  # pylint: disable=protected-access
            history = await self.app._storage_manager._get_value(history_key)  # pylint: disable=protected-access
            return history or ""
        return f"unknown item: {item}"


class _BrowserUiHandler(BaseHTTPRequestHandler):
    """HTTP handler for browser UI mode."""

    runtime: _UiRuntime

    def _write_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _write_text(self, status: int, body: str, content_type: str = "text/plain; charset=utf-8") -> None:
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._write_text(HTTPStatus.OK, _BROWSER_HTML, "text/html; charset=utf-8")
            return
        if parsed.path == "/api/meta":
            self._write_json(
                HTTPStatus.OK,
                {
                    "session_id": self.runtime.session_id,
                    "user_id": self.runtime.user_id
                },
            )
            return
        if parsed.path == "/api/info":
            query = parse_qs(parsed.query or "")
            item = (query.get("item", [""])[0] or "").strip()
            try:
                data = self.runtime.run(self.runtime.read_info(item))
                self._write_json(HTTPStatus.OK, {"data": data})
            except Exception as exc:  # pylint: disable=broad-except
                self._write_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})
            return
        if parsed.path == "/api/config/edit":
            try:
                data = self.runtime.run(self.runtime.read_config_text())
                self._write_json(HTTPStatus.OK, data)
            except Exception as exc:  # pylint: disable=broad-except
                self._write_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})
            return
        if parsed.path == "/api/detail/edit":
            query = parse_qs(parsed.query or "")
            item = (query.get("item", [""])[0] or "").strip()
            try:
                data = self.runtime.run(self.runtime.read_detail_doc(item))
                self._write_json(HTTPStatus.OK, data)
            except Exception as exc:  # pylint: disable=broad-except
                self._write_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return
        self._write_text(HTTPStatus.NOT_FOUND, "not found")

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path not in {
                "/api/chat",
                "/api/chat/stream",
                "/api/config/validate",
                "/api/config/save",
                "/api/detail/save",
        }:
            self._write_text(HTTPStatus.NOT_FOUND, "not found")
            return
        if parsed.path == "/api/chat":
            self.send_response(HTTPStatus.TEMPORARY_REDIRECT)
            self.send_header("Location", "/api/chat/stream")
            self.end_headers()
            return
        try:
            raw_len = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raw_len = 0
        body = self.rfile.read(raw_len) if raw_len > 0 else b"{}"
        try:
            payload = json.loads(body.decode("utf-8"))
        except Exception:
            payload = {}
        if parsed.path == "/api/chat/stream":
            message = str(payload.get("message", "")).strip()
            if not message:
                self._write_json(HTTPStatus.BAD_REQUEST, {"error": "message is required"})
                return

            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()

            q: queue.Queue[tuple[str, str]] = queue.Queue()
            stream_error: list[Exception | None] = [None]

            def _on_delta(chunk: str) -> None:
                q.put(("delta", chunk))

            def _worker() -> None:
                try:
                    self.runtime.stream_chat(message, _on_delta)
                except Exception as exc:  # pylint: disable=broad-except
                    stream_error[0] = exc
                finally:
                    q.put(("done", ""))

            worker = threading.Thread(target=_worker, daemon=True)
            worker.start()

            sent_text = ""
            try:
                while True:
                    typ, value = q.get()
                    if typ == "delta":
                        merged = self.runtime._merge_text(sent_text, value or "")  # pylint: disable=protected-access
                        delta = merged[len(sent_text):] if merged.startswith(sent_text) else merged
                        sent_text = merged
                        if not delta:
                            continue
                        data = {"type": "delta", "content": delta}
                    else:
                        if stream_error[0] is not None:
                            data = {"type": "error", "message": str(stream_error[0])}
                        else:
                            data = {"type": "done"}
                    raw = (json.dumps(data, ensure_ascii=False) + "\n").encode("utf-8")
                    self.wfile.write(raw)
                    self.wfile.flush()
                    if typ == "done":
                        break
            except (BrokenPipeError, ConnectionResetError):
                return
            return

        content = payload.get("content")
        if not isinstance(content, str):
            self._write_json(HTTPStatus.BAD_REQUEST, {"error": "content must be a string"})
            return

        if parsed.path == "/api/detail/save":
            item = str(payload.get("item", "")).strip()
            if not item:
                self._write_json(HTTPStatus.BAD_REQUEST, {"error": "item is required"})
                return
            try:
                message = self.runtime.run(self.runtime.save_detail_doc(item, content))
                self._write_json(HTTPStatus.OK, {"message": message})
            except Exception as exc:  # pylint: disable=broad-except
                self._write_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return

        if parsed.path == "/api/config/validate":
            try:
                result = self.runtime.run(self.runtime.validate_config_text(content))
                self._write_json(HTTPStatus.OK, result)
            except Exception as exc:  # pylint: disable=broad-except
                self._write_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})
            return

        try:
            message = self.runtime.run(self.runtime.save_config_text(content))
            self._write_json(HTTPStatus.OK, {"message": message})
        except Exception as exc:  # pylint: disable=broad-except
            error_data = self.runtime.run(self.runtime.validate_config_text(content))
            self._write_json(
                HTTPStatus.BAD_REQUEST,
                {
                    "error": str(exc),
                    "line": error_data.get("line"),
                    "column": error_data.get("column"),
                },
            )

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        return


def _run_browser_mode(
    runtime: _UiRuntime,
    *,
    host: str = "127.0.0.1",
    port: int = 0,
    open_browser: bool = True,
    config_path: Path | None = None,
) -> None:
    _run_browser_mode_with_bind(
        runtime,
        host=host,
        port=port,
        open_browser=open_browser,
        watch_config_path=config_path,
    )


def _run_browser_mode_with_bind(
    runtime: _UiRuntime,
    *,
    host: str,
    port: int,
    open_browser: bool,
    watch_config_path: Path | None = None,
) -> None:
    _BrowserUiHandler.runtime = runtime
    server = ThreadingHTTPServer((host, port), _BrowserUiHandler)
    host, port = server.server_address
    url = f"http://{host}:{port}"
    last_mtime = _file_mtime(watch_config_path) if watch_config_path else -1.0

    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    try:
        print(f"trpc-claw browser UI: {url}")  # noqa: T201
        if open_browser:
            try:
                webbrowser.open(url, new=1, autoraise=True)
            except Exception:
                pass
        print("Press Ctrl+C to stop UI server.")  # noqa: T201
        while True:
            time.sleep(1)
            if watch_config_path is not None:
                current_mtime = _file_mtime(watch_config_path)
                if current_mtime != last_mtime:
                    print("Config file changed, restarting UI process...")  # noqa: T201
                    _restart_current_process()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        server.server_close()
        if server_thread.is_alive():
            server_thread.join(timeout=1.0)


def _pick_free_port(host: str = "127.0.0.1") -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])


def _file_mtime(path: Path) -> float:
    if path is None:
        return -1.0
    try:
        return float(path.stat().st_mtime)
    except FileNotFoundError:
        return -1.0


def _restart_current_process() -> None:
    """Restart current process via execv for full reload."""
    os.environ["TRPC_CLAW_UI_RESTARTING"] = "1"
    os.execv(sys.executable, [sys.executable, *sys.argv])


def run_ui_server(*, workspace: Path | None, config_path: Path | None) -> None:
    """Run browser UI with in-process auto-restart on config changes."""
    cfg = config_path.expanduser().resolve() if config_path else DEFAULT_CONFIG_PATH.expanduser().resolve()
    host = "127.0.0.1"
    configured_port = os.getenv("TRPC_CLAW_UI_PORT", "").strip()
    if configured_port:
        try:
            port = int(configured_port)
        except ValueError:
            port = _pick_free_port(host)
    else:
        port = _pick_free_port(host)
    os.environ["TRPC_CLAW_UI_PORT"] = str(port)
    open_browser = os.getenv("TRPC_CLAW_UI_RESTARTING", "") != "1"
    os.environ["TRPC_CLAW_UI_RESTARTING"] = "0"

    runtime = _UiRuntime(workspace=workspace, config_path=cfg)
    try:
        _run_browser_mode(runtime, host=host, port=port, open_browser=open_browser, config_path=cfg)
    finally:
        runtime.close()
