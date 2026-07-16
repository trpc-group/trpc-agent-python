# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""
AI Code Review 流水线效果验证用 demo。

本文件故意包含多类问题，供 AI review 抓取：
- Critical: 凭证硬编码、命令注入、路径穿越、明确逻辑错误
- Warning: 空值/边界、资源未关闭、缺少超时
- Suggestion: 可维护性

注意：不要把这个模块接到正式业务或 CI 执行路径上。
pytest 默认也不会收集本文件（文件名不以 test_ 开头）。
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any


# --- Critical: 硬编码密钥 ---
API_KEY = "sk-live-hardcoded-secret-do-not-use"
DB_PASSWORD = "P@ssw0rd123"


def authenticate(user: str, token: str) -> bool:
    """用硬编码密钥做“鉴权”，任何知道仓库内容的人都能伪造。"""
    return token == API_KEY and user == "admin"


def run_user_command(cmd: str) -> str:
    """Critical: 命令注入 —— 不可信输入直接进入 shell=True。"""
    # 攻击示例: cmd = "ls; rm -rf /"
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    return result.stdout


def read_user_file(base_dir: str, relative_path: str) -> str:
    """Critical: 路径穿越 —— 未规范化/校验就拼路径读文件。"""
    # 攻击示例: relative_path = "../../etc/passwd"
    target = base_dir + "/" + relative_path
    with open(target, "r", encoding="utf-8") as f:
        return f.read()


def apply_discount(price: float, percent: float) -> float:
    """Critical: 逻辑错误 —— 折扣按加法算，导致价格算错。"""
    # 正确应为: price * (1 - percent / 100)
    return price + percent / 100


def is_authorized(role: str | None) -> bool:
    """Critical: 权限绕过 —— None/空字符串也会被当成授权通过。"""
    # 本意是拒绝未登录；实际 `if not role` 为 True 时反而 return True
    if not role:
        return True
    return role in {"admin", "editor"}


def fetch_profile(user_id: str | None) -> dict[str, Any]:
    """Warning: 空值未处理 —— user_id 为 None 时会 TypeError。"""
    return {"id": user_id, "name": user_id.upper()}


def average(scores: list[float]) -> float:
    """Warning: 边界条件 —— 空列表直接 ZeroDivisionError。"""
    return sum(scores) / len(scores)


def load_config(path: str) -> dict[str, str]:
    """Warning: 资源未关闭 —— 打开文件后未 close / 未用 with。"""
    f = open(path, "r", encoding="utf-8")
    data = f.read()
    # 故意不关闭 f
    result: dict[str, str] = {}
    for line in data.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            result[k.strip()] = v.strip()
    return result


def call_remote_api(url: str) -> str:
    """Warning: 缺少超时 —— 外网请求可能永久挂起。"""
    import urllib.request

    with urllib.request.urlopen(url) as resp:  # noqa: S310 - demo only
        return resp.read().decode("utf-8")


def dump_debug(env: dict[str, str] | None = None) -> None:
    """Warning: 敏感信息输出 —— 把环境变量（可能含密钥）直接打印。"""
    data = env if env is not None else dict(os.environ)
    print("DEBUG ENV:", data)


def merge_settings(defaults: dict[str, Any], overrides: dict[str, Any] | None) -> dict[str, Any]:
    """Suggestion: 可维护性 —— 原地修改 defaults，副作用不清晰。"""
    if overrides:
        defaults.update(overrides)
    return defaults


def write_report(output_dir: str, name: str, content: str) -> Path:
    """Suggestion: 可维护性 —— 魔法字符串/弱约束，长期难扩展。"""
    # 没有校验 name，也没有统一路径工具
    p = Path(output_dir) / (name + ".txt")
    p.write_text(content, encoding="utf-8")
    return p


if __name__ == "__main__":
    # 本地手测入口；正式 CI 请勿执行
    print(authenticate("admin", API_KEY))
    print(apply_discount(100, 10))
    print(is_authorized(None))
