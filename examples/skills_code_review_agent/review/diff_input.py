# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Input resolution: --diff-file, --repo-path or a named fixture."""
import subprocess
from pathlib import Path

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures"


def load_diff(diff_file=None, repo_path=None, fixture=None):
    """Return (diff_text, input_type, input_ref). Exactly one source required."""
    sources = [s for s in (diff_file, repo_path, fixture) if s]
    if len(sources) != 1:
        raise ValueError("provide exactly one of --diff-file, --repo-path, --fixture")
    if diff_file:
        return Path(diff_file).read_text(encoding="utf-8"), "diff_file", str(diff_file)
    if repo_path:
        out = subprocess.run(["git", "-C", str(repo_path), "diff", "HEAD"],
                             capture_output=True, text=True, check=True)
        return out.stdout, "repo_path", str(repo_path)
    name = fixture if str(fixture).endswith(".diff") else f"{fixture}.diff"
    return (FIXTURES_DIR / name).read_text(encoding="utf-8"), "fixture", name
