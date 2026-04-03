#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Top-level CLI entrypoint for trpc_agent_sdk.

This module provides:
1) A root Typer app (`trpc-agent` style command tree).
2) Explicit registration API via `register_cli`.
3) Auto-discovery for module CLIs named `*_cli.py`.

To make a module discoverable automatically, define in `<module>/_cli.py`:
- `app = typer.Typer(...)`
- optional `CLI_COMMAND_PATH = ("group", "name")`
- optional `CLI_COMMAND_HELP = "help text"`

If `CLI_COMMAND_PATH` is omitted, command path is derived from the module path.
"""

from __future__ import annotations

import importlib
import pkgutil
from dataclasses import dataclass
from typing import Iterable
from typing import Optional

import typer

_PACKAGE_PREFIX = "trpc_agent_sdk."
_ROOT_CLI_MODULE = "trpc_agent_sdk._cli"


@dataclass(frozen=True)
class _CliRegistration:
    """Metadata for a sub CLI module."""

    module_path: str
    command_path: tuple[str, ...] | None = None
    app_attr: str = "app"
    help_text: str | None = None


_REGISTRATIONS: list[_CliRegistration] = []
_REGISTERED_MODULES: set[str] = set()
_LOAD_ERRORS: list[str] = []


def register_cli(
    module_path: str,
    *,
    command_path: Iterable[str] | None = None,
    app_attr: str = "app",
    help_text: str | None = None,
) -> None:
    """Register a module that exposes a Typer app.

    Args:
        module_path: Python module path,.
        command_path: Optional command hierarchy.
            If omitted, module-level `CLI_COMMAND_PATH` or derived path is used.
        app_attr: Attribute name that points to a `typer.Typer` app.
        help_text: Optional help text shown for the subcommand.
    """
    if module_path in _REGISTERED_MODULES:
        return

    normalized_command_path = tuple(command_path) if command_path is not None else None
    _REGISTRATIONS.append(
        _CliRegistration(
            module_path=module_path,
            command_path=normalized_command_path,
            app_attr=app_attr,
            help_text=help_text,
        ))
    _REGISTERED_MODULES.add(module_path)


def _derive_command_path_from_module(module_path: str) -> tuple[str, ...]:
    name = module_path
    if name.startswith(_PACKAGE_PREFIX):
        name = name[len(_PACKAGE_PREFIX):]
    parts = [part for part in name.split(".") if part]
    if parts and parts[-1] == "_cli":
        parts = parts[:-1]
    command_parts = [part.lstrip("_").replace("_", "-") for part in parts if not part.startswith("__")]
    if not command_parts:
        raise ValueError(f"Cannot derive command path from module: {module_path}")
    return tuple(command_parts)


def _normalize_command_path(path: Iterable[str]) -> tuple[str, ...]:
    normalized = tuple(segment.strip() for segment in path if segment and segment.strip())
    if not normalized:
        raise ValueError("Command path must contain at least one non-empty segment.")
    return normalized


def _auto_discover_cli_modules() -> None:
    """Discover `*_cli.py` modules under trpc_agent_sdk and register them."""
    package = importlib.import_module("trpc_agent_sdk")
    for module_info in pkgutil.walk_packages(package.__path__, prefix=f"{package.__name__}."):
        module_name = module_info.name
        if module_name in {__name__, _ROOT_CLI_MODULE}:
            continue
        if not module_name.endswith("._cli"):
            continue
        register_cli(module_name)


def _add_typer_at_path(
    root_app: typer.Typer,
    group_apps: dict[tuple[str, ...], typer.Typer],
    command_path: tuple[str, ...],
    sub_app: typer.Typer,
    help_text: str | None = None,
) -> None:
    parent = root_app
    parent_key: tuple[str, ...] = ()
    for segment in command_path[:-1]:
        group_key = parent_key + (segment,)
        group = group_apps.get(group_key)
        if group is None:
            group = typer.Typer(no_args_is_help=True, add_completion=False)
            parent.add_typer(group, name=segment)
            group_apps[group_key] = group
        parent = group
        parent_key = group_key
    parent.add_typer(sub_app, name=command_path[-1], help=help_text)


def _make_missing_module_callback(module_path: str, err_text: str):
    def _callback() -> None:
        typer.echo(
            f"[ERROR] Failed to load sub command module '{module_path}': {err_text}",
            err=True,
        )
        raise typer.Exit(code=1)
    return _callback



def _build_app() -> typer.Typer:
    app = typer.Typer(
        help="tRPC Agent SDK command line tools.",
        no_args_is_help=True,
        add_completion=False,
    )
    register_cli(
        "trpc_agent_sdk.server.openclaw._cli",
        command_path=("openclaw",),
        help_text="OpenClaw gateway, chat, ui and deps tools.",
    )
    _auto_discover_cli_modules()

    group_apps: dict[tuple[str, ...], typer.Typer] = {}

    for registration in _REGISTRATIONS:
        raw_path = registration.command_path
        if raw_path is None:
            command_path = _derive_command_path_from_module(registration.module_path)
        else:
            command_path = _normalize_command_path(raw_path)
        help_text = registration.help_text

        try:
            module = importlib.import_module(registration.module_path)
            sub_app = getattr(module, registration.app_attr)
        except Exception as exc:  # pylint: disable=broad-except
            err_text = str(exc)
            _LOAD_ERRORS.append(f"{registration.module_path}: {err_text}")
            failed_app = typer.Typer(
                no_args_is_help=True,
                add_completion=False,
                help=f"Failed to load module: {registration.module_path}",
            )
            failed_app.callback()(_make_missing_module_callback(registration.module_path, err_text))
            _add_typer_at_path(
                app,
                group_apps,
                command_path,
                failed_app,
                help_text=help_text,
            )
            continue

        if not isinstance(sub_app, typer.Typer):
            _LOAD_ERRORS.append(
                f"{registration.module_path}: attribute '{registration.app_attr}' is not a typer.Typer instance")
            continue

        raw_path = registration.command_path or getattr(module, "CLI_COMMAND_PATH", None)
        if raw_path is None:
            command_path = _derive_command_path_from_module(registration.module_path)
        else:
            command_path = _normalize_command_path(raw_path)

        help_text = registration.help_text or getattr(module, "CLI_COMMAND_HELP", None)
        _add_typer_at_path(app, group_apps, command_path, sub_app, help_text=help_text)

    @app.callback(invoke_without_command=True)
    def _root_callback(ctx: typer.Context) -> None:
        if not _LOAD_ERRORS or ctx.invoked_subcommand is not None:
            return
        for err in _LOAD_ERRORS:
            typer.echo(f"[WARN] Failed to load sub command: {err}", err=True)

    return app


app = _build_app()


def main(argv: Optional[list[str]] = None) -> int:
    """CLI entrypoint."""
    try:
        app(args=argv, prog_name="trpc_agent_cmd")
    except SystemExit as exc:
        if isinstance(exc.code, int):
            return exc.code
        return 0 if exc.code is None else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
