# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""演示用：本地 JSON 文件模拟远端配置中心。

真实业务下这一层换成七彩石 / Apollo / Nacos / 自研 KV 的 SDK 即可，
对外暴露的 read(key) / write(key, value) 同步 API 保持不变。

数据 schema
-----------
store.json 是一个 {key: value} 字典，本 example 用两个固定 key：
    "system_prompt:production"   生产 namespace 的 prompt
    "system_prompt:sandbox"      沙箱 namespace 的 prompt（优化器读写）
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class FakeKVStore:
    """JSON 文件持久化的 KV，简化版的远端配置中心。"""

    def __init__(self, path: Path) -> None:
        self._path = path
        if not self._path.exists():
            self._path.write_text("{}", encoding="utf-8")

    def _load(self) -> dict[str, Any]:
        return json.loads(self._path.read_text(encoding="utf-8"))

    def _save(self, data: dict[str, Any]) -> None:
        self._path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def read(self, key: str) -> str:
        """读 KV；缺失时抛 KeyError，模拟远端"配置不存在"。"""
        data = self._load()
        if key not in data:
            raise KeyError(f"prompt key not found in store: {key}")
        return str(data[key])

    def write(self, key: str, value: str) -> None:
        """覆盖式写入。"""
        data = self._load()
        data[key] = value
        self._save(data)
