# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""CLI entry point for OpenClaw runtime.

This module provides a nanobot-style command layout while staying aligned with
the current trpc_agent_sdk runtime implementation.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Optional

import typer

CLI_COMMAND_PATH = ("openclaw", )
CLI_COMMAND_HELP = "OpenClaw gateway, chat, ui and deps tools."

app = typer.Typer(
    help="trpc_claw gateway and interactive CLI.",
    no_args_is_help=True,
    add_completion=False,
)


@app.command("conf_temp")
def show_config_template_cmd(
        full: bool = typer.Option(
            False,
            "--full",
            help="Print full config template (config_full.temp.yaml).",
        ), ) -> None:
    """Print the openclaw config template YAML."""
    template_name = "config_full.temp.yaml" if full else "config.temp.yaml"
    template_path = Path(__file__).with_name(template_name)
    if not template_path.exists():
        typer.echo(f"Error: template not found: {template_path}", err=True)
        raise typer.Exit(code=1)
    typer.echo(template_path.read_text(encoding="utf-8"))


def _resolve_optional_path(value: Optional[str]) -> Optional[Path]:
    return Path(value).expanduser().resolve() if value else None


def _run_openclaw(workspace: Optional[str], config: Optional[str], force_chat: bool = False) -> None:
    from trpc_agent_sdk.server.openclaw.claw import ClawApplication
    ws = _resolve_optional_path(workspace)
    cfg = _resolve_optional_path(config)

    async def _run() -> None:
        gateway = ClawApplication(workspace=ws, config_path=cfg)
        if force_chat or not gateway.channels.enabled_channels:
            await gateway.run_cli_fallback()
            return
        await gateway.run_gateway()

    asyncio.run(_run())


def _run_openclaw_ui(
    workspace: Optional[str],
    config: Optional[str],
) -> None:
    from trpc_agent_sdk.server.openclaw.ui import run_ui_server
    ws = _resolve_optional_path(workspace)
    cfg = _resolve_optional_path(config)
    run_ui_server(workspace=ws, config_path=cfg)


@app.command("run")
def run_cmd(
        workspace: Optional[str] = typer.Option(
            None,
            "--workspace",
            "-w",
            help="Workspace path override (defaults to config/runtime settings).",
        ),
        config: Optional[str] = typer.Option(
            None,
            "--config",
            "-c",
            help="Config file path (yaml/json).",
        ),
) -> None:
    """Run openclaw in gateway mode when channels are enabled, else CLI fallback."""
    _run_openclaw(workspace=workspace, config=config, force_chat=False)


@app.command("chat")
def chat_cmd(
        workspace: Optional[str] = typer.Option(
            None,
            "--workspace",
            "-w",
            help="Workspace path override (defaults to config/runtime settings).",
        ),
        config: Optional[str] = typer.Option(
            None,
            "--config",
            "-c",
            help="Config file path (yaml/json).",
        ),
) -> None:
    """Force local interactive chat (CLI fallback), ignoring third-party channels for openclaw."""
    _run_openclaw(workspace=workspace, config=config, force_chat=True)


@app.command("ui")
def ui_cmd(
        workspace: Optional[str] = typer.Option(
            None,
            "--workspace",
            "-w",
            help="Workspace path override (defaults to config/runtime settings).",
        ),
        config: Optional[str] = typer.Option(
            None,
            "--config",
            "-c",
            help="Config file path (yaml/json).",
        ),
) -> None:
    """Start UI (macOS desktop, browser on other systems)."""
    _run_openclaw_ui(workspace=workspace, config=config)


@app.command("deps")
def deps_cmd(
        profile: str = typer.Option(
            "",
            "--profile",
            help="Dependency profiles (comma-separated).",
        ),
        skills: str = typer.Option(
            "",
            "--skills",
            "-s",
            help="Comma-separated skill names.",
        ),
        state_dir: str = typer.Option(
            "",
            "--state-dir",
            help="State dir for managed toolchain (reserved for parity).",
        ),
        skills_root: str = typer.Option(
            "",
            "--skills-root",
            help="Skills root directory override.",
        ),
        skills_extra_dirs: str = typer.Option(
            "",
            "--skills-extra-dirs",
            help="Extra skills roots (comma-separated).",
        ),
        skills_allow_bundled: str = typer.Option(
            "",
            "--skills-allow-bundled",
            help="Comma-separated allowlist of bundled skills.",
        ),
        json_output: bool = typer.Option(
            False,
            "--json",
            help="Print machine-readable JSON output.",
        ),
        apply: bool = typer.Option(
            False,
            "--apply",
            help="Execute install plan commands.",
        ),
        continue_on_error: bool = typer.Option(
            True,
            "--continue-on-error/--fail-fast",
            help="Continue executing remaining plan steps when one command fails.",
        ),
        workspace: Optional[str] = typer.Option(
            None,
            "--workspace",
            "-w",
            help="Workspace path override (defaults to config/runtime settings).",
        ),
        config: Optional[str] = typer.Option(
            None,
            "--config",
            "-c",
            help="Config file path (yaml/json).",
        ),
) -> None:
    """Inspect skill dependencies and print install plan suggestions."""
    from trpc_agent_sdk.server.openclaw.skill import inspect_skill_dependencies
    from trpc_agent_sdk.server.openclaw.skill import apply_dependency_plan
    from trpc_agent_sdk.server.openclaw.skill import render_dependency_report
    from trpc_agent_sdk.server.openclaw.skill import report_to_json

    ws = _resolve_optional_path(workspace)
    cfg = _resolve_optional_path(config)
    try:
        report = inspect_skill_dependencies(
            config_path=cfg,
            workspace=ws,
            skills_raw=skills,
            profiles_raw=profile,
            state_dir=state_dir,
            skills_root=skills_root,
            skills_extra_dirs_raw=skills_extra_dirs,
            skills_allow_bundled_raw=skills_allow_bundled,
        )
        report["apply_requested"] = apply
        if apply:
            report["apply_result"] = apply_dependency_plan(
                report,
                continue_on_error=continue_on_error,
            )
    except Exception as exc:  # pylint: disable=broad-except
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1)
    if json_output:
        typer.echo(report_to_json(report))
    else:
        typer.echo(render_dependency_report(report))
    if apply and report.get("apply_result", {}).get("has_failures"):
        raise typer.Exit(code=1)
