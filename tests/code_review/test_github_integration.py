"""GitHub webhook security, API, checkout, and orchestration tests."""

from __future__ import annotations

import hashlib
import hmac
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from examples.code_review_agent.code_review.database import (
    ReviewStore,
    sqlite_database_url,
)
from examples.code_review_agent.code_review.models import (
    Finding,
    ReviewOutput,
    Severity,
)
from examples.code_review_agent.github_integration.app import create_webhook_app
from examples.code_review_agent.github_integration.checkout import (
    CheckoutError,
    GitHubWorkspaceManager,
)
from examples.code_review_agent.github_integration.client import (
    GITHUB_API_VERSION,
    GitHubApiError,
    GitHubAppTokenProvider,
    GitHubClient,
    StaticTokenProvider,
)
from examples.code_review_agent.github_integration.models import GitHubPullRequestEvent
from examples.code_review_agent.github_integration.security import (
    verify_webhook_signature,
)
from examples.code_review_agent.github_integration.service import GitHubReviewService
from examples.code_review_agent.github_integration.worker import (
    GitHubReviewWorker,
    is_retryable_error,
)


def _payload(base: str, head: str, *, action: str = "opened", draft: bool = False) -> dict:
    return {
        "action": action,
        "number": 17,
        "installation": {
            "id": 123
        },
        "repository": {
            "full_name": "octo/demo"
        },
        "pull_request": {
            "draft": draft,
            "base": {
                "sha": base,
                "repo": {
                    "clone_url": "https://github.com/octo/demo.git"
                },
            },
            "head": {
                "sha": head,
                "repo": {
                    "clone_url": "https://github.com/contributor/demo.git"
                },
            },
        },
    }


def _signature(body: bytes, secret: str) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_signature_matches_github_official_vector() -> None:
    verify_webhook_signature(
        b"Hello, World!",
        "It's a Secret to Everybody",
        "sha256=757107ea0eb2509fc211221cce984b8a37570b6d7586c22c46f4379c8b043e17",
    )
    with pytest.raises(ValueError, match="mismatch"):
        verify_webhook_signature(b"tampered", "secret", "sha256=" + "0" * 64)


def test_parses_only_supported_pull_request_actions() -> None:
    base, head = "a" * 40, "b" * 40
    event = GitHubPullRequestEvent.from_webhook(
        delivery_id="delivery-1",
        event_name="pull_request",
        payload=_payload(base, head),
    )

    assert event is not None
    assert event.repository_full_name == "octo/demo"
    assert event.pull_number == 17
    assert GitHubPullRequestEvent.from_webhook(
        delivery_id="delivery-2",
        event_name="pull_request",
        payload=_payload(base, head, action="closed"),
    ) is None


def test_webhook_authenticates_deduplicates_and_ignores_drafts(tmp_path: Path) -> None:
    secret = "webhook-secret"
    store = ReviewStore(sqlite_database_url(tmp_path / "reviews.db"))
    app = create_webhook_app(store=store, webhook_secret=secret)
    base, head = "a" * 40, "b" * 40
    payload = _payload(base, head)
    body = json.dumps(payload).encode()
    headers = {
        "X-Hub-Signature-256": _signature(body, secret),
        "X-GitHub-Delivery": "delivery-1",
        "X-GitHub-Event": "pull_request",
        "Content-Type": "application/json",
    }

    with TestClient(app) as client:
        ready = client.get("/readyz")
        accepted = client.post("/webhooks/github", content=body, headers=headers)
        duplicate = client.post("/webhooks/github", content=body, headers=headers)
        conflicting_body = json.dumps(_payload(base, "c" * 40)).encode()
        conflict = client.post(
            "/webhooks/github",
            content=conflicting_body,
            headers={
                **headers,
                "X-Hub-Signature-256": _signature(conflicting_body, secret),
            },
        )
        rejected = client.post(
            "/webhooks/github",
            content=body,
            headers={
                **headers, "X-Hub-Signature-256": "sha256=" + "0" * 64
            },
        )
        draft_body = json.dumps(_payload(base, head, draft=True)).encode()
        ignored = client.post(
            "/webhooks/github",
            content=draft_body,
            headers={
                **headers,
                "X-GitHub-Delivery": "delivery-2",
                "X-Hub-Signature-256": _signature(draft_body, secret),
            },
        )

    assert ready.json() == {"status": "ready"}
    assert accepted.status_code == 202
    assert accepted.json()["status"] == "queued"
    assert duplicate.json()["status"] == "duplicate"
    assert conflict.status_code == 409
    assert rejected.status_code == 401
    assert ignored.json()["status"] == "ignored"
    assert store.get_github_job("delivery-1")["status"] == "queued"
    assert store.get_github_delivery("delivery-2")["status"] == "ignored"
    store.close()


@pytest.mark.asyncio
async def test_worker_completes_retries_and_permanently_fails_jobs(tmp_path: Path) -> None:
    store = ReviewStore(sqlite_database_url(tmp_path / "reviews.db"))
    base, head = "a" * 40, "b" * 40

    class RecordingService:

        def __init__(self):
            self.events = []
            self.failures = []

        async def process(self, event):
            self.events.append(event.delivery_id)
            if self.failures:
                raise self.failures.pop(0)

    service = RecordingService()

    def enqueue(delivery_id: str, max_attempts: int = 3) -> None:
        event = GitHubPullRequestEvent.from_webhook(
            delivery_id=delivery_id,
            event_name="pull_request",
            payload=_payload(base, head),
        )
        assert store.enqueue_github_delivery(
            delivery_id=delivery_id,
            event_name="pull_request",
            action=event.action,
            payload_sha256="f" * 64,
            event_payload=event.model_dump(mode="json"),
            repository_full_name=event.repository_full_name,
            pull_number=event.pull_number,
            head_sha=event.head_sha,
            installation_id=event.installation_id,
            max_attempts=max_attempts,
        )

    enqueue("worker-success")
    worker = GitHubReviewWorker(
        store=store,
        service=service,
        worker_id="test-worker",
        lease_seconds=30,
        base_retry_seconds=0,
        max_retry_seconds=0,
        jitter=lambda: 0,
    )
    assert await worker.run_once() is True
    assert store.get_github_job("worker-success")["status"] == "succeeded"

    enqueue("worker-retry", max_attempts=2)
    service.failures = [httpx.ConnectError("offline"), httpx.ConnectError("offline")]
    assert await worker.run_once() is True
    assert store.get_github_job("worker-retry")["status"] == "queued"
    assert store.get_github_delivery("worker-retry")["status"] == "retrying"
    assert await worker.run_once() is True
    assert store.get_github_job("worker-retry")["status"] == "dead"
    assert store.get_github_delivery("worker-retry")["status"] == "failed"

    enqueue("worker-permanent")
    service.failures = [ValueError("invalid configuration")]
    assert await worker.run_once() is True
    assert store.get_github_job("worker-permanent")["status"] == "dead"
    assert is_retryable_error(ValueError("bad")) is False
    assert is_retryable_error(
        GitHubApiError("rate limited", status_code=429)
    ) is True
    store.close()


@pytest.mark.asyncio
async def test_github_client_pins_api_version_and_handles_errors() -> None:
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/comments"):
            return httpx.Response(422, text="invalid line", headers={"x-github-request-id": "req-1"})
        return httpx.Response(201, json={"id": 99})

    http_client = httpx.AsyncClient(
        base_url="https://api.github.test",
        transport=httpx.MockTransport(handler),
    )
    client = GitHubClient(
        token_provider=StaticTokenProvider("installation-token"),
        installation_id=123,
        api_url="https://api.github.test",
        http_client=http_client,
    )

    result = await client.create_check_run("octo", "demo", {"head_sha": "a" * 40})
    with pytest.raises(GitHubApiError, match="req-1"):
        await client.create_review_comment("octo", "demo", 17, {"line": 3})

    assert result["id"] == 99
    assert requests[0].headers["x-github-api-version"] == GITHUB_API_VERSION
    assert requests[0].headers["authorization"] == "Bearer installation-token"
    await http_client.aclose()


@pytest.mark.asyncio
async def test_app_token_provider_requests_least_privilege_and_caches(monkeypatch) -> None:
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            201,
            json={
                "token": "ghs_new_variable_length_token",
                "expires_at": "2099-01-01T00:00:00Z",
            },
        )

    monkeypatch.setitem(sys.modules, "jwt", SimpleNamespace(encode=lambda *_args, **_kwargs: "app-jwt"))
    http_client = httpx.AsyncClient(
        base_url="https://api.github.test",
        transport=httpx.MockTransport(handler),
    )
    provider = GitHubAppTokenProvider(
        app_id="123",
        private_key="test-key",
        api_url="https://api.github.test",
        permissions={
            "contents": "read",
            "checks": "write",
            "pull_requests": "read",
        },
        http_client=http_client,
    )

    first = await provider.get_token(42)
    second = await provider.get_token(42)
    request_payload = json.loads(requests[0].content)

    assert first == second == "ghs_new_variable_length_token"
    assert len(requests) == 1
    assert request_payload["permissions"]["pull_requests"] == "read"
    assert requests[0].headers["authorization"] == "Bearer app-jwt"
    await http_client.aclose()


def test_checkout_keeps_token_out_of_arguments_and_rejects_hosts(tmp_path: Path) -> None:

    class RecordingWorkspace(GitHubWorkspaceManager):

        def __init__(self):
            super().__init__(tmp_path)
            self.calls = []

        def _git(self, workspace, environment, *arguments):
            self.calls.append((arguments, environment))
            return "b" * 40 if arguments[:2] == ("rev-parse", "HEAD") else ""

    manager = RecordingWorkspace()
    event = GitHubPullRequestEvent.from_webhook(
        delivery_id="delivery-1",
        event_name="pull_request",
        payload=_payload("a" * 40, "b" * 40),
    )
    workspace = manager.checkout(event, "secret-token")

    assert all("secret-token" not in argument for call, _environment in manager.calls for argument in call)
    assert all(environment["GIT_CONFIG_VALUE_0"] == "Authorization: Bearer secret-token"
               for _call, environment in manager.calls)
    manager.cleanup(workspace)

    unsafe = event.model_copy(update={"head_clone_url": "https://evil.example/demo.git"})
    with pytest.raises(CheckoutError, match="not allowed"):
        manager.checkout(unsafe, "secret-token")


@pytest.mark.asyncio
async def test_service_reviews_persists_and_publishes_exact_added_line(
    sample_repository: tuple[Path, str, str],
    tmp_path: Path,
) -> None:
    repository, base, head = sample_repository
    store = ReviewStore(sqlite_database_url(tmp_path / "reviews.db"))
    event = GitHubPullRequestEvent.from_webhook(
        delivery_id="delivery-service",
        event_name="pull_request",
        payload=_payload(base, head),
    )
    store.claim_github_delivery(
        delivery_id=event.delivery_id,
        event_name="pull_request",
        action=event.action,
        payload_sha256="f" * 64,
        repository_full_name=event.repository_full_name,
        pull_number=event.pull_number,
        head_sha=event.head_sha,
        installation_id=event.installation_id,
    )

    async def reviewer(_context):
        return ReviewOutput(
            summary="Found a contract issue.",
            findings=[
                Finding(
                    rule_id="python.correctness.none",
                    severity=Severity.HIGH,
                    confidence=0.9,
                    category="correctness",
                    file_path="app.py",
                    start_line=3,
                    title="Do not return None",
                    description="This changes the function contract.",
                )
            ],
        )

    class FakeWorkspace:

        def __init__(self):
            self.cleaned = False

        def checkout(self, _event, _token):
            return repository

        def cleanup(self, _workspace):
            self.cleaned = True

    class FakeClient:

        def __init__(self):
            self.updates = []
            self.comments = []

        async def create_check_run(self, _owner, _repository, _payload):
            return {"id": 77}

        async def update_check_run(self, _owner, _repository, _check_run_id, payload):
            self.updates.append(payload)
            return {"id": 77}

        async def create_review_comment(self, _owner, _repository, _pull_number, payload):
            self.comments.append(payload)
            return {"id": 88}

        async def list_check_runs_for_ref(
            self,
            _owner,
            _repository,
            _ref,
            *,
            check_name,
        ):
            del check_name
            return []

        async def list_review_comments(self, _owner, _repository, _pull_number):
            return self.comments

        async def close(self):
            return None

    fake_client = FakeClient()
    workspace_manager = FakeWorkspace()
    service = GitHubReviewService(
        store=store,
        token_provider=StaticTokenProvider("token"),
        workspace_manager=workspace_manager,
        reviewer=reviewer,
        client_factory=lambda _event: fake_client,
    )

    await service.process(event)

    delivery = store.get_github_delivery(event.delivery_id)
    assert delivery["status"] == "completed"
    assert delivery["check_run_id"] == 77
    assert store.get_run(delivery["review_run_id"]).repository_path == "github://octo/demo"
    assert fake_client.updates[-1]["conclusion"] == "failure"
    assert fake_client.comments[0]["line"] == 3
    assert fake_client.comments[0]["side"] == "RIGHT"
    publication = store.get_github_publication(event.delivery_id)
    assert publication["check_completed"] is True
    assert publication["comments_completed"] is True
    assert store.list_github_deliveries(status="completed")[0]["delivery_id"] == event.delivery_id
    assert workspace_manager.cleaned is True

    repeated_event = event.model_copy(update={"delivery_id": "delivery-service-repeat"})
    store.claim_github_delivery(
        delivery_id=repeated_event.delivery_id,
        event_name="pull_request",
        action=repeated_event.action,
        payload_sha256="e" * 64,
        repository_full_name=repeated_event.repository_full_name,
        pull_number=repeated_event.pull_number,
        head_sha=repeated_event.head_sha,
        installation_id=repeated_event.installation_id,
    )
    await service.process(repeated_event)
    assert len(fake_client.comments) == 1
    assert store.count_runs() == 1
    store.close()
