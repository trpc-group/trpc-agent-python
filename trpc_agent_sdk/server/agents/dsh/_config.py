# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Framework-level configuration for the DSH agent."""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field

from ._protocol import DSHModelSpec


@dataclass
class DSHModel:
    """Model selection and gateway settings for one DSH runtime.

    DSH owns the actual model call. This object only tells the runtime which
    provider/model to select and how to reach the gateway; the framework does
    not call the model itself.

    Example:
        >>> DSHModel(
        ...     provider="openai",
        ...     model="deepseek-chat",
        ...     base_url="https://gateway.example/v1",
        ...     api_key=os.environ["GATEWAY_API_KEY"],
        ... )
    """

    provider: str
    """DSH provider id.

    ``openai`` and ``anthropic`` generate an ``llm-pi-ai`` route
    automatically; other ids must already be configured in the DSH profile.
    """

    model: str
    """Model id passed to DSH, for example ``deepseek-chat`` or
    ``claude-sonnet-4``."""

    reasoning_effort: str | None = None
    """Optional reasoning effort forwarded to DSH as ``reasoningEffort``."""

    max_tokens: int | None = None
    """Optional output-token limit forwarded to DSH as ``maxTokens``."""

    base_url: str | None = None
    """Gateway endpoint for the generated route.

    The runtime also exports it as ``DEEPSEEK_BASE_URL`` to the DSH
    subprocess. Omit it when the DSH profile already knows the endpoint or
    when the provider is profile-managed.
    """

    api_key: str | None = field(default=None, repr=False)
    """Gateway credential for the generated route.

    The runtime also exports it as ``DEEPSEEK_API_KEY`` to the DSH subprocess.
    Kept out of ``repr``; prefer ``DSHConfig.env`` when the secret should come
    from the process environment instead of a Python object.
    """

    def __post_init__(self) -> None:
        if not self.provider.strip():
            raise ValueError("DSHModel.provider must not be empty")
        if not self.model.strip():
            raise ValueError("DSHModel.model must not be empty")
        if self.reasoning_effort is not None and not self.reasoning_effort.strip():
            raise ValueError("DSHModel.reasoning_effort must not be empty")
        if self.max_tokens is not None and self.max_tokens <= 0:
            raise ValueError("DSHModel.max_tokens must be positive")

    def to_spec(self) -> DSHModelSpec:
        """Return the wire model selection used by ``session/trpc-prompt``."""

        spec: DSHModelSpec = {"provider": self.provider, "model": self.model}
        if self.reasoning_effort is not None:
            spec["reasoningEffort"] = self.reasoning_effort
        if self.max_tokens is not None:
            spec["maxTokens"] = self.max_tokens
        return spec


@dataclass
class DSHConfig:
    """Configuration for one DSHAgent and its DSH subprocess.

    The launch fields mirror the DSH SDK's ``HarnessConfig``. Model selection
    is grouped under :attr:`model` so there is exactly one source of truth.

    Example:
        >>> DSHConfig(
        ...     profile="custom",
        ...     dsh_home="/data/dsh-home",
        ...     model=DSHModel(
        ...         provider="openai",
        ...         model="deepseek-chat",
        ...         base_url="https://gateway.example/v1",
        ...         api_key=os.environ["GATEWAY_API_KEY"],
        ...     ),
        ... )
    """

    dsh_bin: str | None = None
    """Optional path or command name for the ``dsh`` executable.

    ``None`` uses the runtime bundled with the DeepSeek Harness Python SDK.
    """

    profile: str = "sdk"
    """DSH profile name, not a path.

    The runtime resolves the profile directory as
    ``<dsh_home>/profiles/<profile>``. For example, ``profile="custom"`` with
    ``dsh_home="/data/dsh-home"`` loads ``/data/dsh-home/profiles/custom``.
    """

    patches: tuple[str, ...] = ()
    """Extra DSH patch files.

    They are applied after the user's profile and before the framework's
    temporary patch. Relative paths are resolved against the Python process's
    current directory.
    """

    dsh_home: str | None = None
    """DSH home directory.

    ``None`` falls back to ``env["DSH_HOME"]`` and then to the ``DSH_HOME``
    environment variable. If none is set, ``DSHRuntime`` raises
    ``ValueError``. Relative paths are resolved against the Python process's
    current directory.
    """

    env: dict[str, str] = field(default_factory=dict)
    """Extra environment variables for the DSH subprocess.

    The SDK starts from ``os.environ``, applies this mapping, and then the
    runtime overwrites ``DEEPSEEK_BASE_URL`` / ``DEEPSEEK_API_KEY`` from
    :attr:`model` when those values are set. Use this for provider credentials
    that are not part of ``DSHModel``.
    """

    initialize_timeout_seconds: float = 30.0
    """Timeout for the initial ``initialize`` handshake, in seconds."""

    request_timeout_seconds: float | None = None
    """Timeout for ``session/trpc-prompt`` and ``session/trpc-snapshot``
    JSON-RPC responses, in seconds.

    It measures how long the runtime takes to accept the prompt or return the
    snapshot, not how long the LLM generates. The prompt receipt is returned
    before the turn finishes; the turn continues through notifications until
    ``session.status idle``. Configure the DSH profile's provider timeout for
    the LLM call itself. ``None`` waits indefinitely.
    """

    shutdown_timeout_seconds: float | None = 1.0
    """Timeout for the ``shutdown`` handshake before the subprocess is killed,
    in seconds."""

    runtime_cwd: str | None = None
    """Working directory of the DSH Node.js process.

    It is also the default session cwd for a new DSH session when
    ``custom_dsh_input`` is not configured or leaves ``DSHRunInput.cwd``
    unchanged. ``None`` uses the Python process's cwd.
    """

    model: DSHModel | None = None
    """Model selection and gateway settings.

    ``None`` lets the DSH profile choose its default model; no ``llm-pi-ai``
    route is generated.
    """

    def __post_init__(self) -> None:
        if not self.profile.strip():
            raise ValueError("DSHConfig.profile must not be empty")
