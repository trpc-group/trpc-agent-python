"""Start an isolated gateway, run black-box acceptance, and clean up."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _wait_until_ready(url: str, process: subprocess.Popen[bytes]) -> None:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"gateway exited early with code {process.returncode}")
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                if response.status == 200:
                    return
        except (urllib.error.URLError, TimeoutError):
            time.sleep(0.2)
    raise RuntimeError("gateway did not become ready within 30 seconds")


def main() -> int:
    example_root = Path(__file__).resolve().parents[1]
    repository_root = example_root.parents[1]
    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"

    environment = os.environ.copy()
    environment.update(
        {
            "HOST": "127.0.0.1",
            "PORT": str(port),
            "OFFLINE_ECHO_MODE": "true",
            "AUTO_CREATE_SCHEMA": "true",
            "TENANT_CONFIG_FILE": str(example_root / "config.local.json"),
            "CONTROL_PLANE_DB_URL": "sqlite:///:memory:",
            "TENANT_NAMESPACE_SECRET": "judge-demo-namespace-secret-32-characters",
            "ADMIN_API_TOKEN": "judge-demo-admin-token",
            "ACME_TELEGRAM_WEBHOOK_SECRET": "judge-demo-telegram-secret",
            "ACME_WECOM_CALLBACK_TOKEN": "judge-demo-wecom-token",
        }
    )
    process = subprocess.Popen(
        [sys.executable, "-m", "examples.multi_tenant_im_agent.main"],
        cwd=repository_root,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    try:
        _wait_until_ready(f"{base_url}/readyz", process)
        result = subprocess.run(
            [
                sys.executable,
                str(Path(__file__).with_name("acceptance.py")),
                "--base-url",
                base_url,
            ],
            cwd=repository_root,
            env=environment,
            check=False,
        )
        if result.returncode != 0:
            return result.returncode
    except RuntimeError as exc:
        print(f"JUDGE DEMO FAILED: {exc}", file=sys.stderr)
        return 1
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)

    print("JUDGE DEMO PASSED: isolated server stopped and in-memory database discarded")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
