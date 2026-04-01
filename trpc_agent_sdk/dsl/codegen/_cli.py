# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Command line interface for DSL codegen."""

import re
import shutil
import subprocess
from enum import Enum
from pathlib import Path
from typing import Optional

import typer

from ._render import render_workflow_files
from ._workflow import load_workflow_definition
from ._workflow import load_workflow_definition_from_json_text

_YAPF_STYLE = "{based_on_style: pep8, column_limit: 120, indent_width: 4, split_before_logical_operator: true}"


def _format_generated_python_files(target_dir: Path, written: dict[str, Path]) -> None:
    python_files = sorted(str(path) for path in written.values() if path.suffix == ".py")
    if not python_files:
        return

    yapf_executable = shutil.which("yapf")
    if yapf_executable is None:
        raise RuntimeError("yapf is required to format generated project but was not found in PATH.")

    command = [
        yapf_executable,
        "--in-place",
        "--style",
        _YAPF_STYLE,
        *python_files,
    ]
    try:
        subprocess.run(command, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        error_message = exc.stderr.strip() or exc.stdout.strip() or str(exc)
        raise RuntimeError(f"yapf formatting failed for generated project at {target_dir}: {error_message}") from exc


def _default_output_dir_name(workflow_name: str) -> str:
    sanitized = re.sub(r"[^A-Za-z0-9._-]+", "_", workflow_name.strip())
    sanitized = sanitized.strip("._-")
    if sanitized:
        return sanitized
    return "workflow"


def generate_project(
    workflow_json_path: str | Path | None = None,
    dsl_text: str | None = None,
    output_dir: str | Path | None = None,
    *,
    overwrite: bool = False,
    service: str | None = None,
) -> dict[str, Path]:
    """Generate Python files from workflow JSON file path or raw JSON text."""
    if workflow_json_path is None and dsl_text is None:
        raise ValueError("Either workflow_json_path or dsl_text must be provided.")
    if workflow_json_path is not None and dsl_text is not None:
        raise ValueError("Provide only one of workflow_json_path or dsl_text.")

    default_target_dir: Path
    workflow_raw_json: str
    if workflow_json_path is not None:
        workflow_path = Path(workflow_json_path).expanduser().resolve()
        workflow = load_workflow_definition(workflow_path)
        workflow_raw_json = workflow_path.read_text(encoding="utf-8")
        default_target_dir = workflow_path.parent / workflow_path.stem
    else:
        workflow_raw_json = dsl_text or ""
        workflow = load_workflow_definition_from_json_text(workflow_raw_json)
        default_target_dir = Path.cwd() / _default_output_dir_name(workflow.name)

    rendered_files = render_workflow_files(workflow, service_mode=service)

    if output_dir is None:
        target_dir = default_target_dir
    else:
        target_dir = Path(output_dir).expanduser().resolve()

    if target_dir.exists():
        if not target_dir.is_dir():
            raise ValueError(f"Output path exists and is not a directory: {target_dir}")
        has_content = any(target_dir.iterdir())
        if has_content and not overwrite:
            raise ValueError(f"Output directory is not empty: {target_dir}. Use --overwrite to replace existing files.")
    else:
        target_dir.mkdir(parents=True, exist_ok=True)

    written: dict[str, Path] = {}
    for file_name, content in rendered_files.items():
        file_path = target_dir / file_name
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")
        written[file_name] = file_path

    workflow_copy_path = target_dir / "workflow.json"
    workflow_copy_path.write_text(workflow_raw_json, encoding="utf-8")
    written["workflow.json"] = workflow_copy_path

    _format_generated_python_files(target_dir, written)

    return written


class ServiceMode(str, Enum):
    """Supported service integration modes."""

    HTTP = "http"
    A2A = "a2a"
    AGUI = "agui"


app = typer.Typer(
    help="Generate Python Graph code from a DSL workflow json file or raw json text.",
    no_args_is_help=True,
    add_completion=False,
)


@app.command()
def generate(
    workflow_json: Path | None = typer.Argument(
        None,
        exists=True,
        file_okay=True,
        dir_okay=False,
        resolve_path=True,
        help="Path to workflow json file. Optional when --dsl-text is provided.",
    ),
    dsl_text: str | None = typer.Option(
        None,
        "--dsl-text",
        help="Raw DSL json text. Use this when WORKFLOW_JSON is not provided.",
    ),
    output_dir: Path | None = typer.Option(
        None,
        "--output-dir",
        "-o",
        file_okay=False,
        resolve_path=True,
        help=
        "Output directory for generated files. Default: <workflow_stem> for file input, or sanitized workflow name for json text input.",
    ),
    overwrite: bool = typer.Option(
        False,
        "--overwrite",
        help="Allow writing into a non-empty output directory.",
    ),
    service: ServiceMode | None = typer.Option(
        None,
        "--service",
        help="Generated service integration mode. If omitted, generate local-only project without service files.",
    ),
) -> None:
    """Generate a project from DSL workflow json."""
    if workflow_json is None and dsl_text is None:
        raise typer.BadParameter("Either WORKFLOW_JSON or --dsl-text must be provided.")
    if workflow_json is not None and dsl_text is not None:
        raise typer.BadParameter("Provide only one of WORKFLOW_JSON or --dsl-text.")

    generated = generate_project(
        workflow_json_path=workflow_json,
        dsl_text=dsl_text,
        output_dir=output_dir,
        overwrite=overwrite,
        service=service.value if service is not None else None,
    )

    ordered_names = sorted(generated.keys())
    typer.echo("Code generation completed.")
    for name in ordered_names:
        typer.echo(f"  - {name}: {generated[name]}")


def main(argv: Optional[list[str]] = None) -> int:
    """CLI entrypoint."""
    try:
        app(args=argv, prog_name="python -m trpc_agent_dsl.codegen")
    except SystemExit as exc:
        if isinstance(exc.code, int):
            return exc.code
        return 0 if exc.code is None else 1
    return 0
