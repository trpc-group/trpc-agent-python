#
# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#

"""跨平台 Skill 脚本完整性摘要与 manifest 文件闭包校验。"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any


_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True)
class IntegrityFile:
    """保存一个受 manifest 保护的相对脚本路径及其规范化摘要。"""

    path: str
    sha256: str


def canonical_source_bytes(content: bytes | str) -> bytes:
    """把 UTF-8 源码统一为无 BOM、LF 换行字节，消除 Git CRLF 检出差异。"""

    if isinstance(content, str):
        text = content
    elif isinstance(content, bytes):
        text = content.decode("utf-8-sig")
    else:
        raise TypeError("script_content_invalid")
    return text.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")


def canonical_source_sha256(content: bytes | str) -> str:
    """计算跨 Windows/POSIX 检出稳定的 Skill 源码 SHA-256。"""

    return hashlib.sha256(canonical_source_bytes(content)).hexdigest()


def safe_script_relative_path(value: Any) -> str | None:
    """校验 manifest 脚本路径为不含父级穿越的 POSIX 相对路径。"""

    if not isinstance(value, str) or not value:
        return None
    if "\\" in value or any(ord(character) < 32 for character in value):
        return None
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        return None
    return path.as_posix()


def parse_integrity_files(value: Any) -> tuple[IntegrityFile, ...] | None:
    """解析并严格验证 manifest 的完整脚本文件清单。"""

    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return None
    files: list[IntegrityFile] = []
    seen_paths: set[str] = set()
    for item in value:
        if not isinstance(item, Mapping):
            return None
        path = safe_script_relative_path(item.get("path"))
        sha256 = item.get("sha256")
        if (
            path is None
            or path in seen_paths
            or not isinstance(sha256, str)
            or _SHA256_PATTERN.fullmatch(sha256) is None
        ):
            return None
        seen_paths.add(path)
        files.append(IntegrityFile(path=path, sha256=sha256))
    if not files:
        return None
    return tuple(sorted(files, key=lambda item: item.path))


__all__ = [
    "IntegrityFile",
    "canonical_source_bytes",
    "canonical_source_sha256",
    "parse_integrity_files",
    "safe_script_relative_path",
]
