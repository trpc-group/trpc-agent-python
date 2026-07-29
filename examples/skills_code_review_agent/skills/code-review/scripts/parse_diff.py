#
# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#

"""Sandbox entry point that emits a non-sensitive unified-diff summary."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from lib.diff_parser import (
    ChangeSet,
    parse_unified_diff,
    restore_change_set_context,
)


_INPUT_PATH = Path("work") / "inputs" / "diff.json"
_OUTPUT_PATH = Path("out") / "parsed.json"


def _load_change_set(input_path: Path) -> ChangeSet:
    """读取受控 diff 载荷，但不打印其中任何原始内容。"""

    payload = json.loads(input_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("input payload must be an object")
    source_kind = payload.get("source_kind", "diff_file")
    input_sha256 = payload.get("input_sha256")
    diff_text = payload.get("diff")
    full_files = payload.get("full_files")
    file_metadata = payload.get("file_metadata")
    if source_kind not in {"diff_file", "repo_path", "files", "fixture"}:
        raise ValueError("input source_kind is invalid")
    if not isinstance(diff_text, str):
        raise ValueError("input diff must be a string")
    if (
        input_sha256 is None
        and full_files is None
        and file_metadata is None
    ):
        return parse_unified_diff(diff_text, source_kind=source_kind)
    if (
        not isinstance(input_sha256, str)
        or not isinstance(full_files, dict)
        or not all(
            isinstance(path, str) and isinstance(content, str)
            for path, content in full_files.items()
        )
        or not isinstance(file_metadata, dict)
        or not all(
            isinstance(path, str) and isinstance(metadata, dict)
            for path, metadata in file_metadata.items()
        )
    ):
        raise ValueError("input context metadata is invalid")
    parsed_source_kind = (
        source_kind
        if source_kind in {"diff_file", "repo_path", "fixture"}
        else "fixture"
    )
    change_set = parse_unified_diff(
        diff_text,
        source_kind=parsed_source_kind,
    )
    return restore_change_set_context(
        change_set,
        source_kind=source_kind,
        input_sha256=input_sha256,
        full_files=full_files,
        file_metadata=file_metadata,
    )


def _summary(change_set: ChangeSet) -> Dict[str, Any]:
    """仅返回元数据，禁止原始代码和 hunk 内容离开沙箱。"""

    return {
        "schema_version": "1.0.0",
        "source_kind": change_set.source_kind,
        "input_sha256": change_set.input_sha256,
        "file_count": change_set.file_count,
        "hunk_count": change_set.hunk_count,
        "additions": change_set.additions,
        "deletions": change_set.deletions,
        "parse_warning_count": len(change_set.parse_warnings),
        "files": [
            {
                "path": file_change.normalized_path,
                "status": file_change.status,
                "review_scope": file_change.review_scope,
                "is_binary": file_change.is_binary,
                "analysis_mode": file_change.analysis_mode,
                "hunk_count": len(file_change.hunks),
            }
            for file_change in change_set.files
        ],
    }


def main() -> int:
    """读取固定 workspace 输入，并写入一份规范化解析摘要。"""

    change_set = _load_change_set(_INPUT_PATH)
    _OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    _OUTPUT_PATH.write_text(
        json.dumps(_summary(change_set), ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
