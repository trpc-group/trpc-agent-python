"""Small async GitHub REST client and GitHub App token provider."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol
from urllib.parse import quote

import httpx

GITHUB_API_VERSION = "2026-03-10"


class GitHubApiError(RuntimeError):
    """Raised when GitHub returns a non-success response."""

    def __init__(self, message: str, *, status_code: int):
        super().__init__(message)
        self.status_code = status_code


class TokenProvider(Protocol):
    """Resolve a token scoped to one GitHub App installation."""

    async def get_token(self, installation_id: int) -> str:
        """Return a currently valid token."""


@dataclass(frozen=True)
class StaticTokenProvider:
    """Use a pre-generated token for development and local smoke tests."""

    token: str

    async def get_token(self, installation_id: int) -> str:
        del installation_id
        if not self.token:
            raise ValueError("GitHub token is not configured")
        return self.token


@dataclass
class _CachedToken:
    token: str
    expires_at: datetime


class GitHubAppTokenProvider:
    """Mint and cache one-hour GitHub App installation access tokens."""

    def __init__(
        self,
        *,
        app_id: str,
        private_key: str,
        api_url: str = "https://api.github.com",
        permissions: dict[str, str] | None = None,
        http_client: httpx.AsyncClient | None = None,
    ):
        if not app_id or not private_key:
            raise ValueError("GitHub App ID and private key are required")
        self.app_id = app_id
        self.private_key = private_key
        self.api_url = api_url.rstrip("/")
        self.permissions = permissions or {
            "contents": "read",
            "checks": "write",
            "pull_requests": "write",
        }
        self._client = http_client or httpx.AsyncClient(base_url=self.api_url, timeout=30.0)
        self._owns_client = http_client is None
        self._cache: dict[int, _CachedToken] = {}
        self._lock = asyncio.Lock()

    async def get_token(self, installation_id: int) -> str:
        now = datetime.now(timezone.utc)
        cached = self._cache.get(installation_id)
        if cached is not None and cached.expires_at - timedelta(seconds=60) > now:
            return cached.token
        async with self._lock:
            cached = self._cache.get(installation_id)
            if cached is not None and cached.expires_at - timedelta(seconds=60) > now:
                return cached.token
            token = await self._mint_token(installation_id, now)
            self._cache[installation_id] = token
            return token.token

    async def _mint_token(self, installation_id: int, now: datetime) -> _CachedToken:
        try:
            import jwt
        except ImportError as exc:
            raise RuntimeError("Install PyJWT[crypto] to authenticate as a GitHub App") from exc
        app_jwt = jwt.encode(
            {
                "iat": int((now - timedelta(seconds=60)).timestamp()),
                "exp": int((now + timedelta(minutes=9)).timestamp()),
                "iss": self.app_id,
            },
            self.private_key,
            algorithm="RS256",
        )
        response = await self._client.post(
            f"/app/installations/{installation_id}/access_tokens",
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {app_jwt}",
                "X-GitHub-Api-Version": GITHUB_API_VERSION,
            },
            json={"permissions": self.permissions},
        )
        _raise_for_github(response)
        payload = response.json()
        expires_at = datetime.fromisoformat(str(payload["expires_at"]).replace("Z", "+00:00"))
        return _CachedToken(token=str(payload["token"]), expires_at=expires_at)

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()


class GitHubClient:
    """Only the GitHub endpoints required by the review publisher."""

    def __init__(
        self,
        *,
        token_provider: TokenProvider,
        installation_id: int,
        api_url: str = "https://api.github.com",
        http_client: httpx.AsyncClient | None = None,
    ):
        self.token_provider = token_provider
        self.installation_id = installation_id
        self.api_url = api_url.rstrip("/")
        self._client = http_client or httpx.AsyncClient(base_url=self.api_url, timeout=30.0)
        self._owns_client = http_client is None

    async def create_check_run(
        self,
        owner: str,
        repository: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        return await self._request("POST", f"/repos/{_segment(owner)}/{_segment(repository)}/check-runs", payload)

    async def update_check_run(
        self,
        owner: str,
        repository: str,
        check_run_id: int,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        path = f"/repos/{_segment(owner)}/{_segment(repository)}/check-runs/{check_run_id}"
        return await self._request("PATCH", path, payload)

    async def create_review_comment(
        self,
        owner: str,
        repository: str,
        pull_number: int,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        path = f"/repos/{_segment(owner)}/{_segment(repository)}/pulls/{pull_number}/comments"
        return await self._request("POST", path, payload)

    async def list_check_runs_for_ref(
        self,
        owner: str,
        repository: str,
        ref: str,
        *,
        check_name: str,
    ) -> list[dict[str, Any]]:
        """List recent matching check runs for external-id recovery."""
        path = (
            f"/repos/{_segment(owner)}/{_segment(repository)}/commits/"
            f"{_segment(ref)}/check-runs"
        )
        payload = await self._request(
            "GET",
            path,
            params={"check_name": check_name, "filter": "latest", "per_page": "100"},
        )
        return list(payload.get("check_runs", []))

    async def list_review_comments(
        self,
        owner: str,
        repository: str,
        pull_number: int,
    ) -> list[dict[str, Any]]:
        """List the latest comments used to suppress retry duplicates."""
        path = f"/repos/{_segment(owner)}/{_segment(repository)}/pulls/{pull_number}/comments"
        payload = await self._request(
            "GET",
            path,
            params={
                "per_page": "100",
                "sort": "created",
                "direction": "desc",
            },
        )
        return list(payload)

    async def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        params: dict[str, str] | None = None,
    ) -> Any:
        token = await self.token_provider.get_token(self.installation_id)
        response = await self._client.request(
            method,
            path,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
                "X-GitHub-Api-Version": GITHUB_API_VERSION,
            },
            json=payload,
            params=params,
        )
        _raise_for_github(response)
        return response.json()

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()


def _segment(value: str) -> str:
    return quote(value, safe="")


def _raise_for_github(response: httpx.Response) -> None:
    if response.is_success:
        return
    request_id = response.headers.get("x-github-request-id", "")
    body = response.text[:2_000]
    raise GitHubApiError(
        f"GitHub API {response.status_code} request_id={request_id!r}: {body}",
        status_code=response.status_code,
    )
