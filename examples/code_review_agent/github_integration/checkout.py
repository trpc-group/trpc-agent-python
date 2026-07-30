"""Isolated Git checkout for one GitHub pull request delivery."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlparse

from .models import GitHubPullRequestEvent


class CheckoutError(RuntimeError):
    """Raised when an authenticated Git checkout cannot be prepared safely."""


class GitHubWorkspaceManager:
    """Fetch exact webhook commits without placing credentials in command arguments."""

    def __init__(
            self,
            root: str | Path,
            *,
            allowed_hosts: tuple[str, ...] = ("github.com", ),
            command_timeout: float = 300.0,
    ):
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.allowed_hosts = {host.casefold() for host in allowed_hosts}
        if not self.allowed_hosts:
            raise ValueError("at least one GitHub clone host must be allowed")
        if command_timeout <= 0:
            raise ValueError("Git command timeout must be positive")
        self.command_timeout = command_timeout

    def checkout(self, event: GitHubPullRequestEvent, token: str) -> Path:
        """Create a detached worktree at the exact PR head SHA."""
        base_url = self._validate_clone_url(event.base_clone_url)
        head_url = self._validate_clone_url(event.head_clone_url)
        workspace = self.root / f"delivery-{hashlib.sha256(event.delivery_id.encode()).hexdigest()[:24]}"
        if workspace.exists():
            raise CheckoutError(f"Workspace already exists for delivery {event.delivery_id}")
        workspace.mkdir(mode=0o700)
        environment = self._git_environment(token)
        try:
            self._git(workspace, environment, "init", "--quiet")
            self._git(workspace, environment, "remote", "add", "base", base_url)
            self._git(workspace, environment, "remote", "add", "head", head_url)
            self._git(
                workspace,
                environment,
                "fetch",
                "--quiet",
                "--no-tags",
                "--filter=blob:none",
                "base",
                f"+{event.base_sha}:refs/code-review/base",
            )
            self._git(
                workspace,
                environment,
                "fetch",
                "--quiet",
                "--no-tags",
                "--filter=blob:none",
                "head",
                f"+{event.head_sha}:refs/code-review/head",
            )
            self._git(workspace, environment, "checkout", "--quiet", "--detach", "refs/code-review/head")
            resolved_head = self._git(workspace, environment, "rev-parse", "HEAD").strip()
            if resolved_head.casefold() != event.head_sha.casefold():
                raise CheckoutError("Fetched head commit does not match the signed webhook payload")
            return workspace
        except Exception:
            self.cleanup(workspace)
            raise

    def cleanup(self, workspace: str | Path) -> None:
        """Remove only delivery workspaces directly beneath the configured root."""
        path = Path(workspace).resolve()
        if path.parent != self.root or not path.name.startswith("delivery-"):
            raise CheckoutError(f"Refusing to remove unsafe workspace path: {path}")
        if path.exists():
            shutil.rmtree(path)

    def _validate_clone_url(self, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme != "https" or not parsed.hostname:
            raise CheckoutError("GitHub clone URL must use HTTPS")
        if parsed.username or parsed.password:
            raise CheckoutError("GitHub clone URL must not contain credentials")
        if parsed.hostname.casefold() not in self.allowed_hosts:
            raise CheckoutError(f"GitHub clone host is not allowed: {parsed.hostname}")
        if not parsed.path.endswith(".git") or parsed.query or parsed.fragment:
            raise CheckoutError("Invalid GitHub clone URL")
        return value

    def _git(self, workspace: Path, environment: dict[str, str], *arguments: str) -> str:
        command = ["git", "-C", str(workspace), *arguments]
        try:
            result = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=self.command_timeout,
                env=environment,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise CheckoutError(f"Unable to execute Git checkout command: {exc}") from exc
        if result.returncode != 0:
            message = result.stderr.strip()
            raise CheckoutError(message or f"Git exited with status {result.returncode}")
        return result.stdout

    @staticmethod
    def _git_environment(token: str) -> dict[str, str]:
        if not token or "\n" in token or "\r" in token:
            raise CheckoutError("Invalid GitHub installation token")
        environment = os.environ.copy()
        environment.update({
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "http.extraHeader",
            "GIT_CONFIG_VALUE_0": f"Authorization: Bearer {token}",
        })
        return environment
