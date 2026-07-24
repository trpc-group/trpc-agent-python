# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Loaders for replay consistency test cases."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .constants import REPLAY_CASES_DIR
from .models import ReplayCase


def load_jsonl_records(path: Path) -> list[dict[str, Any]]:
    """Load records from a JSONL file."""
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def load_replay_cases() -> list[ReplayCase]:
    """Load all replay cases from JSONL files."""
    cases = []
    for path in sorted(REPLAY_CASES_DIR.glob("*.jsonl")):
        records = load_jsonl_records(path)
        if not records:
            raise ValueError(f"Replay case file is empty: {path}")

        case_record = records[0]
        if case_record.get("record_type") != "case":
            raise ValueError(f"First record must be a case record: {path}")

        event_records = []
        memory_search_records = []
        for index, record in enumerate(records[1:], start=2):
            record_type = record.get("record_type")
            if record_type == "event":
                event_records.append(record)
            elif record_type == "memory_search":
                memory_search_records.append(record)
            else:
                raise ValueError(f"Unsupported record type at line {index}: {path}")

        cases.append(ReplayCase(
            name=case_record["name"],
            app_name=case_record["app_name"],
            user_id=case_record["user_id"],
            session_id=case_record["session_id"],
            session_config=case_record.get("session_config", {}),
            memory_config=case_record.get("memory_config", {}),
            summary_points=case_record.get("summary_points", []),
            event_records=event_records,
            memory_search_records=memory_search_records,
        ))
    return cases


REPLAY_CASES = load_replay_cases()
MEMORY_REPLAY_CASES = [replay_case for replay_case in REPLAY_CASES if replay_case.memory_search_records]
