#!/usr/bin/env python3
"""Batch run example run_agent.py scripts from a start directory.

Features:
- Traverse `examples/*/run_agent.py` in sorted directory order.
- Start from a given directory name (default: llmagent_with_streaming_tool_complex).
- Overwrite each target example's `.env` before running.
- For each example directory, write normal logs to local `out.txt`.
- For each example directory, write failures/stderr to local `error.txt`.
- For each example directory, scan Python files and write Chinese-hit file names to local `info.txt`.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import time
from pathlib import Path

ENV_CONTENT = (
    "TRPC_AGENT_API_KEY=yhNrIaLLjibwZ4PHdenwiFEw@4462\n"
    "TRPC_AGENT_BASE_URL=http://v2.open.venus.woa.com/llmproxy\n"
    "TRPC_AGENT_MODEL_NAME=deepseek-v3-local-II\n"
)

CHINESE_PATTERN = re.compile(r"[\u4e00-\u9fff]")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run examples/*/run_agent.py from a start directory, "
            "write outputs to out.txt/error.txt/info.txt."
        )
    )
    parser.add_argument(
        "--repo-root",
        default=".",
        help="Repository root path. Default: current directory.",
    )
    parser.add_argument(
        "--examples-dir",
        default="examples",
        help="Examples directory relative to repo root. Default: examples.",
    )
    parser.add_argument(
        "--start-dir",
        default="llmagent_with_streaming_tool_complex",
        help="Start from this example directory (inclusive).",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=180,
        help="Timeout per run_agent.py execution. Default: 180.",
    )
    parser.add_argument(
        "--out-file",
        default="out.txt",
        help="Per-example output filename written under each example directory.",
    )
    parser.add_argument(
        "--error-file",
        default="error.txt",
        help="Per-example error filename written under each example directory.",
    )
    parser.add_argument(
        "--info-file",
        default="info.txt",
        help="Per-example info filename written under each example directory.",
    )
    return parser.parse_args()


def collect_example_run_scripts(examples_dir: Path) -> list[tuple[str, Path]]:
    items: list[tuple[str, Path]] = []
    for child in examples_dir.iterdir():
        if not child.is_dir():
            continue
        run_agent = child / "run_agent.py"
        if run_agent.is_file():
            items.append((child.name, run_agent))
    items.sort(key=lambda x: x[0])
    return items


def ensure_start_index(items: list[tuple[str, Path]], start_dir: str) -> int:
    for idx, (name, _) in enumerate(items):
        if name == start_dir:
            return idx
    raise ValueError(f"Start directory not found under examples: {start_dir}")


def write_env_file(example_dir: Path) -> None:
    env_path = example_dir / ".env"
    env_path.write_text(ENV_CONTENT, encoding="utf-8")


def scan_chinese_python_files(example_dir: Path) -> list[Path]:
    found: list[Path] = []
    for py_file in sorted(example_dir.rglob("*.py")):
        try:
            content = py_file.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            content = py_file.read_text(encoding="utf-8", errors="ignore")
        if CHINESE_PATTERN.search(content):
            found.append(py_file)
    return found


def append_text(path: Path, text: str) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(text)


def run_single_example(
    repo_root: Path,
    example_name: str,
    run_agent_path: Path,
    timeout_seconds: int,
    out_filename: str,
    error_filename: str,
    info_filename: str,
) -> None:
    example_dir = run_agent_path.parent
    out_file = example_dir / out_filename
    error_file = example_dir / error_filename
    info_file = example_dir / info_filename

    # Reset per-example output files.
    out_file.write_text("", encoding="utf-8")
    error_file.write_text("", encoding="utf-8")
    info_file.write_text("", encoding="utf-8")

    write_env_file(example_dir)

    chinese_files = [py_file.name for py_file in scan_chinese_python_files(example_dir)]
    if chinese_files:
        info_file.write_text("\n".join(sorted(set(chinese_files))) + "\n", encoding="utf-8")
    else:
        info_file.write_text("No Python files with Chinese characters found.\n", encoding="utf-8")

    header = (
        "\n"
        "============================================================\n"
        f"[START] {example_name}\n"
        f"[PATH] {run_agent_path.relative_to(repo_root)}\n"
        f"[TIME] {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
        "============================================================\n"
    )
    append_text(out_file, header)

    try:
        completed = subprocess.run(
            [sys.executable, str(run_agent_path.name)],
            cwd=str(example_dir),
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        timeout_msg = (
            "\n"
            "--------------------\n"
            f"[FAILED] {example_name}\n"
            f"Reason: timeout after {timeout_seconds} seconds\n"
            f"Stdout (partial):\n{exc.stdout or ''}\n"
            f"Stderr (partial):\n{exc.stderr or ''}\n"
            "--------------------\n"
        )
        append_text(error_file, timeout_msg)
        append_text(out_file, f"[END] {example_name} (timeout)\n")
        return

    if completed.stdout:
        append_text(out_file, completed.stdout)
        if not completed.stdout.endswith("\n"):
            append_text(out_file, "\n")

    append_text(out_file, f"[END] {example_name} (exit_code={completed.returncode})\n")

    if completed.returncode != 0 or completed.stderr:
        fail_msg = (
            "\n"
            "--------------------\n"
            f"[FAILED] {example_name}\n"
            f"Exit code: {completed.returncode}\n"
            f"Path: {run_agent_path.relative_to(repo_root)}\n"
            f"Stderr:\n{completed.stderr or '(empty)'}\n"
            "--------------------\n"
        )
        append_text(error_file, fail_msg)


def main() -> int:
    args = parse_args()
    repo_root = Path(args.repo_root).resolve()
    examples_dir = (repo_root / args.examples_dir).resolve()

    if not examples_dir.is_dir():
        print(f"Examples directory not found: {examples_dir}", file=sys.stderr)
        return 1

    items = collect_example_run_scripts(examples_dir)
    if not items:
        print(f"No run_agent.py found under: {examples_dir}", file=sys.stderr)
        return 1

    try:
        start_idx = ensure_start_index(items, args.start_dir)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    selected = items[start_idx:]
    for example_name, run_agent_path in selected:
        run_single_example(
            repo_root=repo_root,
            example_name=example_name,
            run_agent_path=run_agent_path,
            timeout_seconds=args.timeout_seconds,
            out_filename=args.out_file,
            error_filename=args.error_file,
            info_filename=args.info_file,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
