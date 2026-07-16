from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any


API_KEY = "sk-live-hardcoded-secret-do-not-use"
DB_PASSWORD = "P@ssw0rd123"


def authenticate(user: str, token: str) -> bool:
    return token == API_KEY and user == "admin"


def run_user_command(cmd: str) -> str:
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    return result.stdout


def read_user_file(base_dir: str, relative_path: str) -> str:
    target = base_dir + "/" + relative_path
    with open(target, "r", encoding="utf-8") as f:
        return f.read()


def apply_discount(price: float, percent: float) -> float:
    return price + percent / 100


def is_authorized(role: str | None) -> bool:
    if not role:
        return True
    return role in {"admin", "editor"}


def fetch_profile(user_id: str | None) -> dict[str, Any]:
    return {"id": user_id, "name": user_id.upper()}


def average(scores: list[float]) -> float:
    return sum(scores) / len(scores)


def load_config(path: str) -> dict[str, str]:
    f = open(path, "r", encoding="utf-8")
    data = f.read()
    result: dict[str, str] = {}
    for line in data.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            result[k.strip()] = v.strip()
    return result


def call_remote_api(url: str) -> str:
    import urllib.request

    with urllib.request.urlopen(url) as resp:
        return resp.read().decode("utf-8")


def dump_debug(env: dict[str, str] | None = None) -> None:
    data = env if env is not None else dict(os.environ)
    print("DEBUG ENV:", data)


def merge_settings(defaults: dict[str, Any], overrides: dict[str, Any] | None) -> dict[str, Any]:
    if overrides:
        defaults.update(overrides)
    return defaults


def write_report(output_dir: str, name: str, content: str) -> Path:
    p = Path(output_dir) / (name + ".txt")
    p.write_text(content, encoding="utf-8")
    return p
