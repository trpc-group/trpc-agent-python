# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""JSONL replay case loader and minimal validation."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

DEFAULT_CASES_PATH = Path(__file__).resolve().parents[2] / "tests" / "sessions" / "replay_cases" / "session_memory_summary.jsonl"

_REQUIRED_KEYS = {"case_id", "description", "session_id", "operations", "expect"}


def load_replay_cases(path: Path = DEFAULT_CASES_PATH) -> list[dict[str, Any]]:
    """Load and minimally validate JSONL replay cases."""
    cases = []
    with path.open("r", encoding="utf-8") as case_file:
        for line_number, line in enumerate(case_file, start=1):
            if not line.strip():
                continue
            try:
                case = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid replay JSON on line {line_number}: {exc}") from exc
            missing = _REQUIRED_KEYS - set(case)
            if missing:
                raise ValueError(f"Replay case on line {line_number} is missing {sorted(missing)}")
            cases.append(case)

    case_ids = [case["case_id"] for case in cases]
    if len(case_ids) != len(set(case_ids)):
        raise ValueError("Replay case IDs must be unique")
    return cases