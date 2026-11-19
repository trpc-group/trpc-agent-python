# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""DSH runtime lifecycle and per-turn orchestration."""

from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import yaml

from ._client import DshClient
from ._client import SnapshotResult
from ._client import TRANSPORT_CLOSED_METHOD
from ._client import TrpcPromptResult
from ._config import DSHConfig
from ._protocol import DSHRunInput
from ._protocol import Frame
from ._sdk import HarnessConfig

_PLUGIN_NAMES = (
    "trpc-session-backend.mjs",
    "trpc-request-context.mjs",
    "trpc-jsonrpc-server.mjs",
)

# DSH provider id -> llm-pi-ai wire API. Providers outside this map must be
# configured by the user profile.
_PROVIDER_APIS = {
    "openai": "openai-completions",
    "anthropic": "anthropic-messages",
}


class DSHRuntime:
    """Own one DSH subprocess and translate prompts into tRPC frames."""

    def __init__(self, dsh_conf: DSHConfig) -> None:
        self.dsh_conf = dsh_conf
        # Match the SDK precedence: an explicit ``dsh_home`` wins, then
        # ``env['DSH_HOME']``, then the parent process environment.
        if dsh_conf.dsh_home is not None:
            if not dsh_conf.dsh_home.strip():
                raise ValueError("DSHConfig.dsh_home must not be empty")
            dsh_home = dsh_conf.dsh_home
        else:
            dsh_home = dsh_conf.env.get("DSH_HOME", os.environ.get("DSH_HOME", ""))
            if not dsh_home.strip():
                raise ValueError(
                    "DSHConfig requires dsh_home, env['DSH_HOME'], "
                    "or a non-empty DSH_HOME")

        self.dsh_home = Path(dsh_home).expanduser().resolve()
        self.profile_dir = self.dsh_home / "profiles" / dsh_conf.profile
        self.runtime_cwd = Path(dsh_conf.runtime_cwd or Path.cwd()).expanduser().resolve()

        env = dict(dsh_conf.env)
        model = dsh_conf.model
        if model is not None:
            if model.base_url is not None:
                env["DEEPSEEK_BASE_URL"] = model.base_url
            if model.api_key is not None:
                env["DEEPSEEK_API_KEY"] = model.api_key
        self.env = env

        self._client: DshClient | None = None
        self._framework_dir: Path | None = None
        self._initialized = False

    @property
    def initialized(self) -> bool:
        return self._initialized

    async def init(self) -> None:
        if self._initialized:
            return
        if not self.profile_dir.is_dir():
            raise FileNotFoundError(f"DSH profile directory does not exist: {self.profile_dir}")
        if not self.runtime_cwd.is_dir():
            raise FileNotFoundError(f"DSH runtime_cwd does not exist: {self.runtime_cwd}")

        model = self.dsh_conf.model
        model_spec = model.to_spec() if model is not None else None

        framework_dir = Path(tempfile.mkdtemp(prefix=".trpc-agent-", dir=str(self.profile_dir)))
        try:
            plugin_dir = framework_dir / "plugins"
            plugin_dir.mkdir()
            source_dir = Path(__file__).resolve().parent / "plugins"
            for name in _PLUGIN_NAMES:
                shutil.copy2(source_dir / name, plugin_dir / name)

            framework_patch = framework_dir / "framework.patch.yml"
            framework_patch.write_text(
                self._framework_patch(plugin_dir, self._model_route()),
                encoding="utf-8",
            )

            harness_config = HarnessConfig(
                dsh_bin=self.dsh_conf.dsh_bin,
                profile=self.dsh_conf.profile,
                patches=(*self.dsh_conf.patches, str(framework_patch)),
                dsh_home=str(self.dsh_home),
                cwd=str(self.runtime_cwd),
                env=self.env,
                initialize_timeout_seconds=self.dsh_conf.initialize_timeout_seconds,
                request_timeout_seconds=self.dsh_conf.request_timeout_seconds,
                shutdown_timeout_seconds=self.dsh_conf.shutdown_timeout_seconds,
            )
            client = DshClient(harness_config)
            await client.start()
            try:
                await client.initialize(
                    cwd=str(self.runtime_cwd),
                    model=model_spec,
                    timeout_seconds=self.dsh_conf.initialize_timeout_seconds,
                )
            except BaseException:
                await client.close()
                raise
            self._client = client
            self._framework_dir = framework_dir
            self._initialized = True
        except BaseException:
            shutil.rmtree(framework_dir, ignore_errors=True)
            raise

    async def run(
        self,
        run_input: DSHRunInput,
        *,
        cancel_event: asyncio.Event | None = None,
    ) -> AsyncIterator[Frame]:
        if not self._initialized or self._client is None:
            raise RuntimeError("DSHRuntime is not initialized; call DSHAgent.init() first")

        session_id = run_input.session_id
        async with self._client.subscribe(session_id) as queue:
            receipt = await self._client.request(
                "session/trpc-prompt",
                run_input.to_params(),
                response_model=TrpcPromptResult,
                timeout_seconds=self.dsh_conf.request_timeout_seconds,
            )
            if receipt.jsonl is not None and receipt.version is not None:
                yield Frame("snapshot", {"jsonl": receipt.jsonl, "version": receipt.version})

            cancel_task: asyncio.Task[None] | None = None
            if cancel_event is not None:
                cancel_task = asyncio.create_task(self._watch_cancel(session_id, cancel_event))
            turn_error: str | None = None
            try:
                while True:
                    notification = await queue.get()
                    method = notification.method
                    if method == TRANSPORT_CLOSED_METHOD:
                        if turn_error is not None:
                            break
                        raise RuntimeError("DSH runtime transport closed during the turn")
                    payload = notification.payload
                    if method == "session.assistant.delta":
                        yield Frame("delta", payload)
                    elif method == "session.event":
                        event = payload.get("event")
                        if isinstance(event, dict):
                            yield Frame("event", event)
                            if turn_error is None:
                                turn_error = _turn_error(event)
                    elif method == "session.status":
                        yield Frame("status", payload)
                        if payload.get("status") == "idle":
                            break
            finally:
                if cancel_task is not None:
                    cancel_task.cancel()

            # The live notification stream can miss a persistence-only event
            # (for example the seed marker or a post-turn record). Reconcile
            # against the runtime's authoritative snapshot before returning.
            try:
                snapshot = await self._client.request(
                    "session/trpc-snapshot",
                    {"sessionId": session_id},
                    response_model=SnapshotResult,
                    timeout_seconds=self.dsh_conf.request_timeout_seconds,
                )
            except Exception:
                snapshot = None
            if snapshot is not None:
                yield Frame("snapshot", snapshot.model_dump())

            if turn_error is not None:
                raise RuntimeError(f"DSH turn failed: {turn_error}")

    async def cancel(self, session_id: str) -> None:
        if self._client is None:
            return
        self._client.notify("session/trpc-cancel", {"sessionId": session_id})

    async def snapshot(self, session_id: str) -> SnapshotResult:
        if self._client is None:
            raise RuntimeError("DSHRuntime is not initialized")
        return await self._client.request(
            "session/trpc-snapshot",
            {"sessionId": session_id},
            response_model=SnapshotResult,
            timeout_seconds=self.dsh_conf.request_timeout_seconds,
        )

    async def destroy(self) -> None:
        client = self._client
        self._client = None
        framework_dir = self._framework_dir
        self._framework_dir = None
        self._initialized = False
        try:
            if client is not None:
                await client.close()
        finally:
            if framework_dir is not None:
                shutil.rmtree(framework_dir, ignore_errors=True)

    async def _watch_cancel(self, session_id: str, cancel_event: asyncio.Event) -> None:
        await cancel_event.wait()
        try:
            await self.cancel(session_id)
        except Exception:
            return

    def _model_route(self) -> dict[str, Any] | None:
        """Build the ``llm-pi-ai`` route for ``DSHConfig.model``.

        ``openai`` and ``anthropic`` map to a built-in route. A model with a
        provider outside ``_PROVIDER_APIS`` leaves routing to the user's DSH
        profile.
        """

        model = self.dsh_conf.model
        if model is None:
            return None
        api = _PROVIDER_APIS.get(model.provider)
        if api is None:
            return None

        provider: dict[str, Any] = {"api": api}
        base_url = model.base_url or self.env.get("DEEPSEEK_BASE_URL")
        if base_url:
            provider["baseURL"] = base_url
        if model.api_key is not None or self.env.get("DEEPSEEK_API_KEY"):
            provider["apiKeyEnv"] = "DEEPSEEK_API_KEY"

        model_entry: dict[str, Any] = {"id": model.model}
        if isinstance(model.max_tokens, int) and model.max_tokens > 0:
            model_entry["maxTokens"] = model.max_tokens
        provider["models"] = [model_entry]

        return {
            "id": "llm-pi-ai",
            "config": {
                "providers": {
                    model.provider: provider,
                },
            },
        }

    @staticmethod
    def _framework_patch(
        plugin_dir: Path,
        model_route: dict[str, Any] | None = None,
    ) -> str:
        def uri(name: str) -> str:
            return (plugin_dir / name).resolve().as_uri()

        patch: list[dict[str, Any]] = [
            {"id": "sdk-jsonrpc-server", "disabled": True},
            {"id": "session-persistence-jsonl", "disabled": True},
            {
                "insert": [
                    {"id": "trpc-session-backend", "name": uri("trpc-session-backend.mjs")},
                    {"id": "trpc-request-context", "name": uri("trpc-request-context.mjs")},
                    {"id": "trpc-jsonrpc-server", "name": uri("trpc-jsonrpc-server.mjs")},
                ],
            },
        ]
        if model_route is not None:
            patch.append(model_route)
        return yaml.safe_dump(patch, sort_keys=False, width=10000)


def _turn_error(event: dict[str, Any]) -> str | None:
    """Return the diagnostic of a failed ``turn/end``, or ``None`` for normal events."""

    if event.get("type") != "turn/end":
        return None
    data = event.get("data")
    if not isinstance(data, dict):
        return None
    reason = data.get("reason")
    if not isinstance(reason, dict) or reason.get("kind") != "error":
        return None
    error = reason.get("error")
    if isinstance(error, dict):
        message = error.get("message")
        if isinstance(message, str) and message:
            return message
    return "unknown DSH turn error"
