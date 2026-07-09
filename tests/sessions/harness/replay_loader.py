# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Replay case loader that parses JSONL operation sequences."""

from __future__ import annotations

import json
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path
from typing import Any


@dataclass
class Operation:
    """A single replay operation from a JSONL case file.

    Supported operations:
        create_session  - create a new session
        append_event    - append an event to a session
        get_session     - retrieve a session
        update_session  - update a session in storage
        delete_session  - delete a session
        store_memory    - store session events into memory
        search_memory   - search memory by key and query
        create_summary  - create a session summary
        get_summary     - retrieve a session summary
    """

    op: str
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class ReplayCase:
    """A complete replay case containing a sequence of operations.

    Attributes:
        name: Case name derived from the file stem.
        operations: Ordered list of operations to replay.
    """

    name: str
    operations: list[Operation]


class ReplayLoader:
    """Loads replay cases from JSONL files.

    Each line is a JSON object with an "op" field and optional parameters.
    Lines starting with "#" are treated as comments.
    Empty lines are skipped.
    """

    @staticmethod
    def load(file_path: str | Path) -> ReplayCase:
        """Load a single replay case from a JSONL file.

        Args:
            file_path: Path to the .jsonl replay case file.

        Returns:
            ReplayCase with parsed operations.
        """
        path = Path(file_path)
        operations: list[Operation] = []

        with open(path, "r", encoding="utf-8") as f:
            for line_num, line in enumerate(f, 1):
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                try:
                    data = json.loads(stripped)
                    op_name = data.pop("op")
                    operations.append(Operation(op=op_name, params=data))
                except (json.JSONDecodeError, KeyError) as e:
                    raise ValueError(
                        f"Invalid replay case line {line_num} in {path}: {e}"
                    ) from e

        return ReplayCase(name=path.stem, operations=operations)

    @staticmethod
    def load_all(directory: str | Path) -> list[ReplayCase]:
        """Load all replay cases from a directory.

        Args:
            directory: Path to directory containing .jsonl files.

        Returns:
            List of ReplayCase objects sorted by filename.
        """
        dir_path = Path(directory)
        cases: list[ReplayCase] = []
        for jsonl_file in sorted(dir_path.glob("*.jsonl")):
            cases.append(ReplayLoader.load(jsonl_file))
        return cases