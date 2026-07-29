#
# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2025 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#
"""sha256 规范化测试：Windows CRLF 与 Unix LF 内容应产生相同 hash。"""

from __future__ import annotations

import re
from pathlib import Path

from runner import new_run_id, sha256_file, sha256_text


def test_sha256_text_crlf_matches_lf() -> None:
    lf_text = "步骤：1\n答案：1 个\n"
    crlf_text = "步骤：1\r\n答案：1 个\r\n"
    assert sha256_text(lf_text) == sha256_text(crlf_text)


def test_sha256_text_lf_unchanged() -> None:
    text = "hello\nworld\n"
    # 规范化后与原文本一致 → 与裸 hashlib 一致
    import hashlib

    expected = hashlib.sha256(text.encode("utf-8")).hexdigest()
    assert sha256_text(text) == expected


def test_sha256_file_crlf_matches_lf(tmp_path: Path) -> None:
    lf_file = tmp_path / "lf.md"
    crlf_file = tmp_path / "crlf.md"
    lf_file.write_bytes("步骤：1\n答案：1 个\n".encode("utf-8"))
    crlf_file.write_bytes("步骤：1\r\n答案：1 个\r\n".encode("utf-8"))
    assert sha256_file(lf_file) == sha256_file(crlf_file)


def test_run_id_uses_utc_microseconds_and_random_suffix() -> None:
    run_ids = [new_run_id() for _ in range(200)]
    assert len(set(run_ids)) == len(run_ids)
    assert all(
        re.fullmatch(
            r"\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}\.\d{6}Z-[0-9a-f]{8}",
            run_id,
        )
        for run_id in run_ids
    )
