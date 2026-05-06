# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Configuration types for the Cube/E2B code executor."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

DEFAULT_REMOTE_WORKSPACE = "/workspace/cube_agent"
DEFAULT_EXECUTE_TIMEOUT = 60.0
DEFAULT_IDLE_TIMEOUT = 3600

ENV_API_URL = "E2B_API_URL"
ENV_API_KEY = "E2B_API_KEY"
ENV_TEMPLATE = "CUBE_TEMPLATE_ID"


@dataclass
class CubeCodeExecutorConfig:
    """Configuration for :class:`CubeCodeExecutor`.

    Holds only the sandbox-lifecycle and command-execution settings the
    bare code executor consumes. Workspace-runtime knobs (e.g. the
    remote workspace root) live in :class:`CubeWorkspaceRuntimeConfig`
    so executor-only callers never see fields they don't use (ISP).

    The optional ``e2b-code-interpreter`` dependency must be installed
    (it transitively pulls in ``e2b``). Credentials may be supplied here
    or through ``E2B_API_URL`` / ``E2B_API_KEY``. The Cube template id
    may be supplied here or through ``CUBE_TEMPLATE_ID``.
    """

    template: Optional[str] = None
    """Cube template id for new sandboxes. Falls back to ``CUBE_TEMPLATE_ID``."""

    api_url: Optional[str] = None
    """E2B-compatible Cube API URL. Falls back to ``E2B_API_URL``."""

    api_key: Optional[str] = None
    """E2B API key. Falls back to ``E2B_API_KEY``."""

    sandbox_id: Optional[str] = None
    """Existing remote sandbox id. When set, factories attach instead of create."""

    execute_timeout: float = DEFAULT_EXECUTE_TIMEOUT
    """Default per-command timeout in seconds.

    ``float`` because per-command latency can legitimately be sub-second
    (short shell commands, tight test loops). Shared by the bare
    executor and (transitively) by :class:`CubeWorkspaceRuntime`, since
    the runtime drives commands through the same
    :class:`CubeSandboxClient` and therefore inherits its default. Stays
    on the executor cfg because the client itself reads it during
    construction.
    """

    idle_timeout: int = DEFAULT_IDLE_TIMEOUT
    """Sandbox idle lifetime in seconds; renewed on every command.

    ``int`` (not ``float``) because the underlying e2b APIs
    (``AsyncSandbox.create(timeout=...)`` and ``sbx.set_timeout(...)``)
    take integer seconds — sub-second precision is meaningless for a
    sandbox lifetime measured in minutes/hours. Typing the field as
    ``int`` lets static checkers reject ``idle_timeout=0.9`` at the call
    site instead of silently truncating it to ``0`` (which most vendor
    APIs interpret as "no timeout" or "expire immediately").
    """

    def __post_init__(self) -> None:
        if not isinstance(self.idle_timeout, int) or isinstance(self.idle_timeout, bool):
            raise TypeError(f"idle_timeout must be an int (seconds), got "
                            f"{type(self.idle_timeout).__name__}: {self.idle_timeout!r}")
        if self.idle_timeout < 1:
            raise ValueError(f"idle_timeout must be >= 1 second, got {self.idle_timeout}")
        if self.execute_timeout <= 0:
            raise ValueError(f"execute_timeout must be > 0 seconds, got {self.execute_timeout}")

    def resolve_template(self) -> str:
        value = self.template or os.getenv(ENV_TEMPLATE)
        if not value:
            raise ValueError(f"Cube sandbox requires `template` or {ENV_TEMPLATE} env.")
        return value

    def resolve_api_url(self) -> str:
        value = self.api_url or os.getenv(ENV_API_URL)
        if not value:
            raise ValueError(f"Cube sandbox requires `api_url` or {ENV_API_URL} env.")
        return value

    def resolve_api_key(self) -> str:
        value = self.api_key or os.getenv(ENV_API_KEY)
        if not value:
            raise ValueError(f"Cube sandbox requires `api_key` or {ENV_API_KEY} env.")
        return value


@dataclass
class CubeWorkspaceRuntimeConfig:
    """Configuration for :class:`CubeWorkspaceRuntime`.

    Carries the workspace-only settings the bare :class:`CubeCodeExecutor`
    does not consume. Kept distinct from :class:`CubeCodeExecutorConfig`
    so:

    - executor-only callers (e.g. an agent that just runs code blocks)
      never see workspace knobs in their type signatures, and
    - future workspace-only fields (``max_upload_size``, custom subdir
      names, stage timeouts, ...) can be added here without polluting
      the executor cfg.
    """

    remote_workspace: str = DEFAULT_REMOTE_WORKSPACE
    """Remote root under which :class:`CubeWorkspaceManager` creates
    ``ws_<exec_id>_<suffix>`` subtrees. Defaults to
    :data:`DEFAULT_REMOTE_WORKSPACE`.
    """
