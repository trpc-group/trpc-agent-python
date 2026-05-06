# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Cube/E2B code executor for the trpc-agent-py SDK."""

from __future__ import annotations

from dataclasses import replace
from typing import Awaitable
from typing import Callable
from typing import Optional

from typing_extensions import override

from pydantic import Field
from pydantic import PrivateAttr

from trpc_agent_sdk.context import InvocationContext

from .._base_code_executor import BaseCodeExecutor
from .._types import CodeBlock
from .._types import CodeBlockDelimiter
from .._types import CodeExecutionInput
from .._types import CodeExecutionResult
from .._types import create_code_execution_result
from ._e2b import _import_e2b
from ._sandbox import CubeCommandResult
from ._sandbox import CubeSandboxClient
from ._types import CubeCodeExecutorConfig

_PYTHON_LANGUAGES = frozenset({"python", "py", "python3", ""})
_BASH_LANGUAGES = frozenset({"bash", "sh"})


class CubeCodeExecutor(BaseCodeExecutor):
    """A code executor that runs blocks inside a Cube/E2B remote sandbox.

    Construct directly with an already-open :class:`CubeSandboxClient`::

        executor = CubeCodeExecutor(client, cfg)

    For the typical use case (the SDK opens the sandbox for you) prefer
    the async classmethod factories. All three read the bound sandbox id
    from ``cfg.sandbox_id`` so it is the single source of truth — there
    is no separate positional ``sandbox_id`` argument that could silently
    override the config:

    - :meth:`create` — strict. If ``cfg.sandbox_id`` is set it attaches and
      asserts the sandbox is RUNNING; otherwise it creates a fresh sandbox.
      ``SandboxNotFoundException`` (gone) and ``SandboxException`` (PAUSED)
      propagate so the caller decides whether to clear external locator
      state and retry.
    - :meth:`attach` — explicit attach-only variant; requires
      ``cfg.sandbox_id`` to be set and never creates a fresh sandbox.
    - :meth:`create_or_recreate` — convenience for callers (e.g. hermes)
      that want the "attach, on NotFound run a callback then recreate"
      pattern collapsed into a single call.

    `close()` is a no-op for the remote sandbox (drops the local handle
    only). `destroy()` explicitly kills the remote sandbox; the caller
    must call it when they no longer want the sandbox to outlive the
    executor.
    """

    stateful: bool = Field(default=False, frozen=True, exclude=True)
    optimize_data_file: bool = Field(default=False, frozen=True, exclude=True)

    # Extend the base default (`tool_code` + `python`) with a `bash` fence
    # so text-path extraction matches what ``execute_code`` can actually
    # run (see ``_select_interpreter``). Callers may still override via
    # the ``code_block_delimiters`` field at construction time.
    code_block_delimiters: list[CodeBlockDelimiter] = [
        CodeBlockDelimiter(start="```tool_code\n", end="\n```"),
        CodeBlockDelimiter(start="```python\n", end="\n```"),
        CodeBlockDelimiter(start="```bash\n", end="\n```"),
    ]

    # `_client` is `Optional` because :meth:`close` / :meth:`destroy`
    # legitimately drop the handle post-construction. `_cfg` has no such
    # lifecycle and is set unconditionally in ``__init__``.
    _client: Optional[CubeSandboxClient] = PrivateAttr(default=None)
    _cfg: CubeCodeExecutorConfig = PrivateAttr()

    def __init__(
        self,
        client: CubeSandboxClient,
        cfg: CubeCodeExecutorConfig,
        **data,
    ):
        """Wrap an already-open :class:`CubeSandboxClient`.

        Prefer the async factories :meth:`create`, :meth:`attach`, or
        :meth:`create_or_recreate` for typical use — they encapsulate the
        lazy-import + connect/create plumbing. Direct construction is
        for adapters that already own a :class:`CubeSandboxClient` (or
        for tests that pass a fake).

        Raises:
            ValueError: if the caller tries to enable ``stateful`` or
                ``optimize_data_file`` (this executor does not support
                either; the inherited :class:`Field` is frozen at
                ``False``).
        """
        if data.get("stateful"):
            raise ValueError("CubeCodeExecutor cannot be stateful.")
        if data.get("optimize_data_file"):
            raise ValueError("CubeCodeExecutor cannot enable optimize_data_file.")
        super().__init__(**data)
        self._client = client
        self._cfg = cfg

    @classmethod
    async def create(cls, cfg: CubeCodeExecutorConfig) -> "CubeCodeExecutor":
        """Strict factory. Attaches when ``cfg.sandbox_id`` is set, else creates."""
        if cfg.sandbox_id:
            client = await CubeSandboxClient.open_existing(cfg.sandbox_id, cfg)
        else:
            client = await CubeSandboxClient.open_new(cfg)
        return cls(client, cfg)

    @classmethod
    async def attach(cls, cfg: CubeCodeExecutorConfig) -> "CubeCodeExecutor":
        """Attach-only factory.

        Requires ``cfg.sandbox_id`` to be set. Always connects and asserts
        the sandbox is RUNNING; never creates a fresh sandbox.
        """
        if not cfg.sandbox_id:
            raise ValueError("CubeCodeExecutor.attach requires cfg.sandbox_id to be set; "
                             "use CubeCodeExecutor.create(cfg) to create a fresh sandbox.")
        client = await CubeSandboxClient.open_existing(cfg.sandbox_id, cfg)
        return cls(client, cfg)

    @classmethod
    async def create_or_recreate(
        cls,
        cfg: CubeCodeExecutorConfig,
        *,
        on_stale: Optional[Callable[[], Awaitable[None]]] = None,
    ) -> "CubeCodeExecutor":
        """Attach-then-fall-back-to-create when the bound sandbox has expired.

        When ``cfg.sandbox_id`` is set and the remote sandbox is gone
        (`SandboxNotFoundException`), ``on_stale`` is awaited (callers use
        this to clear their persistent locator) and a fresh sandbox is
        created. PAUSED state and other errors propagate unchanged so that
        operator-managed pauses are not silently overwritten.
        """
        if not cfg.sandbox_id:
            return await cls.create(cfg)
        e2b = _import_e2b()
        try:
            return await cls.create(cfg)
        except e2b.SandboxNotFoundException:
            if on_stale is not None:
                await on_stale()
            return await cls.create(replace(cfg, sandbox_id=None))

    @property
    def sandbox_id(self) -> str:
        """The bound sandbox id. Caller persists it for cross-process reuse."""
        return self.sandbox_client.sandbox_id

    @property
    def sandbox_client(self) -> CubeSandboxClient:
        """The underlying :class:`CubeSandboxClient`.

        Useful for low-level callers (e.g. hermes' ``HarnessSandbox``
        adapter) that need direct file/exec primitives without going
        through the workspace runtime contract. Always returns a live
        client; raises :class:`RuntimeError` if the executor was closed.
        """
        return self._require_client()

    @property
    def config(self) -> CubeCodeExecutorConfig:
        """Configuration this executor was created with."""
        return self._cfg

    async def assert_running(self) -> None:
        """Re-validate the sandbox is RUNNING (e.g. before each turn)."""
        await self._require_client().assert_running()

    def close(self) -> None:
        """Drop the local sandbox handle. Does not kill the remote sandbox."""
        if self._client is not None:
            self._client.close()
            self._client = None

    async def destroy(self) -> None:
        """Explicitly kill the remote sandbox."""
        if self._client is None:
            return
        try:
            await self._client.destroy()
        finally:
            self._client = None

    @override
    async def execute_code(
        self,
        invocation_context: InvocationContext,
        code_execution_input: CodeExecutionInput,
    ) -> CodeExecutionResult:
        """Run each code block in the bound sandbox and aggregate output.

        Code is fed to the interpreter via stdin (heredoc-wrapped by
        :meth:`CubeSandboxClient.commands_run`), which keeps multi-line
        payloads with arbitrary quoting safe without shell-escaping the
        whole block. Bash blocks are executed as a **login shell**
        (``bash -l``) so ``/etc/profile``, ``/etc/profile.d/*`` and
        ``~/.bash_profile`` populate ``PATH`` — Cube/E2B templates
        commonly install toolchains (``uv``/``conda``/``nvm``/``rye``)
        via setup scripts that hook into profile files rather than
        through Dockerfile ``ENV PATH=…``, and a non-login shell would
        silently fail to locate them. Python's ``-c``/stdin paths bypass
        shell profile entirely, so Python blocks use plain ``python3``.
        """
        client = self._require_client()
        cfg = self.config

        blocks = list(code_execution_input.code_blocks)
        if not blocks and code_execution_input.code:
            blocks = [CodeBlock(code=code_execution_input.code, language="python")]

        stdouts: list[str] = []
        stderrs: list[str] = []
        for index, block in enumerate(blocks):
            if not block.code:
                continue
            try:
                interpreter = self._select_interpreter(block.language)
            except ValueError as exc:
                stderrs.append(f"Error in code block {index}: {exc}\n")
                continue
            result = await client.commands_run(
                interpreter,
                stdin=block.code.encode("utf-8"),
                timeout=cfg.execute_timeout,
            )
            self._collect(result, stdouts, stderrs)
        return create_code_execution_result(stdout="".join(stdouts), stderr="".join(stderrs))

    @staticmethod
    def _select_interpreter(language: str) -> str:
        """Pick the remote interpreter command for ``language``.

        Bash dispatches as ``bash -l`` so the block runs in a login
        shell and inherits PATH from ``/etc/profile`` etc. Python uses
        plain ``python3`` since the Python interpreter ignores shell
        profile by design.
        """
        lang = (language or "").lower()
        if lang in _PYTHON_LANGUAGES:
            return "python3"
        if lang in _BASH_LANGUAGES:
            return "bash -l"
        raise ValueError(f"unsupported language: {language!r}")

    @staticmethod
    def _collect(result: CubeCommandResult, stdouts: list[str], stderrs: list[str]) -> None:
        if result.exit_code != 0:
            stderrs.append(f"Process exited with code: {result.exit_code}\n")
        if result.stderr:
            stderrs.append(result.stderr)
        if result.stdout:
            stdouts.append(result.stdout)

    def _require_client(self) -> CubeSandboxClient:
        if self._client is None:
            raise RuntimeError("CubeCodeExecutor sandbox handle was closed; "
                               "construct a fresh executor.")
        return self._client
