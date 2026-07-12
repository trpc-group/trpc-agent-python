"""Unified diff and Git workspace input adapters."""

from __future__ import annotations

import subprocess
from pathlib import Path

from models import ChangedLine


class DiffParser:
    @staticmethod
    def from_file(path: str | Path) -> tuple[str, list[ChangedLine]]:
        text = Path(path).read_text(encoding="utf-8")
        return text, DiffParser.parse(text)

    @staticmethod
    def from_repo(path: str | Path) -> tuple[str, list[ChangedLine]]:
        result = subprocess.run(
            ["git", "diff", "--no-ext-diff", "--unified=3", "HEAD"],
            cwd=Path(path),
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        if result.returncode:
            raise ValueError(f"git diff failed: {result.stderr.strip()}")
        return result.stdout, DiffParser.parse(result.stdout)

    @staticmethod
    def from_paths(paths: list[str | Path]) -> tuple[str, list[ChangedLine]]:
        chunks = []
        for raw_path in paths:
            path = Path(raw_path)
            lines = path.read_text(encoding="utf-8").splitlines()
            chunks.extend([f"diff --git a/{path} b/{path}", f"+++ b/{path}", f"@@ -0,0 +1,{len(lines)} @@"])
            chunks.extend(f"+{line}" for line in lines)
        text = "\n".join(chunks) + "\n"
        return text, DiffParser.parse(text)

    @staticmethod
    def parse(diff: str) -> list[ChangedLine]:
        changed: list[ChangedLine] = []
        current_file = ""
        new_line = 0
        hunk_header = ""
        for raw in diff.splitlines():
            if raw.startswith("+++ "):
                current_file = raw[4:].removeprefix("b/")
                continue
            if raw.startswith("@@"):
                hunk_header = raw
                marker = raw.split("+", 1)[1].split(" ", 1)[0]
                new_line = int(marker.split(",", 1)[0])
                continue
            if raw.startswith("+") and not raw.startswith("+++"):
                changed.append(ChangedLine(current_file, new_line, raw[1:], hunk_header))
                new_line += 1
            elif raw.startswith("-") and not raw.startswith("---"):
                continue
            elif hunk_header and not raw.startswith("\\"):
                new_line += 1
        return changed
