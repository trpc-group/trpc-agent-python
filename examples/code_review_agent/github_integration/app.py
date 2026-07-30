"""FastAPI webhook entrypoint and environment-backed service construction."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, status
from pydantic import ValidationError

from ..code_review.database import ReviewStore, sqlite_database_url
from .models import GitHubPullRequestEvent
from .security import verify_webhook_signature


def create_webhook_app(
    *,
    store: ReviewStore,
    webhook_secret: str,
    max_payload_bytes: int = 2_000_000,
    max_attempts: int = 5,
    close_resources: bool = False,
) -> FastAPI:
    """Create a receiver that atomically persists accepted webhook jobs."""
    if max_payload_bytes <= 0:
        raise ValueError("max_payload_bytes must be positive")
    if max_attempts <= 0:
        raise ValueError("max_attempts must be positive")

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        if close_resources:
            store.close()

    app = FastAPI(title="tRPC Code Review GitHub App", lifespan=lifespan)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz")
    async def readyz() -> dict[str, str]:
        try:
            await asyncio.to_thread(store.ping)
        except Exception as exc:
            raise HTTPException(status_code=503, detail="Database is unavailable") from exc
        return {"status": "ready"}

    @app.post("/webhooks/github", status_code=status.HTTP_202_ACCEPTED)
    async def github_webhook(request: Request):
        content_length = request.headers.get("content-length")
        if content_length and content_length.isdigit() and int(content_length) > max_payload_bytes:
            raise HTTPException(status_code=413, detail="Webhook payload is too large")
        body = await request.body()
        if len(body) > max_payload_bytes:
            raise HTTPException(status_code=413, detail="Webhook payload is too large")
        try:
            verify_webhook_signature(
                body,
                webhook_secret,
                request.headers.get("x-hub-signature-256"),
            )
        except ValueError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc

        delivery_id = request.headers.get("x-github-delivery", "")
        event_name = request.headers.get("x-github-event", "")
        if (not delivery_id or len(delivery_id) > 128 or "\n" in delivery_id or "\r" in delivery_id or not event_name
                or len(event_name) > 80):
            raise HTTPException(status_code=400, detail="Missing GitHub delivery headers")
        try:
            payload = json.loads(body)
            if not isinstance(payload, dict):
                raise TypeError("payload must be a JSON object")
            event = GitHubPullRequestEvent.from_webhook(
                delivery_id=delivery_id,
                event_name=event_name,
                payload=payload,
            )
        except (KeyError, TypeError, ValueError, ValidationError) as exc:
            raise HTTPException(status_code=400, detail=f"Invalid GitHub webhook payload: {exc}") from exc

        action = str(payload.get("action", ""))
        payload_digest = hashlib.sha256(body).hexdigest()
        if event is None or event.draft:
            claimed = await asyncio.to_thread(
                store.claim_github_delivery,
                delivery_id=delivery_id,
                event_name=event_name,
                action=action,
                payload_sha256=payload_digest,
                repository_full_name=event.repository_full_name if event else "",
                pull_number=event.pull_number if event else None,
                head_sha=event.head_sha if event else "",
                installation_id=event.installation_id if event else None,
            )
        else:
            claimed = await asyncio.to_thread(
                store.enqueue_github_delivery,
                delivery_id=delivery_id,
                event_name=event_name,
                action=action,
                payload_sha256=payload_digest,
                event_payload=event.model_dump(mode="json"),
                repository_full_name=event.repository_full_name,
                pull_number=event.pull_number,
                head_sha=event.head_sha,
                installation_id=event.installation_id,
                max_attempts=max_attempts,
            )
        if not claimed:
            existing = await asyncio.to_thread(store.get_github_delivery, delivery_id)
            if existing is not None and existing["payload_sha256"] != payload_digest:
                raise HTTPException(status_code=409, detail="Delivery ID was reused with a different payload")
            return {"status": "duplicate", "delivery_id": delivery_id}
        if event is None or event.draft:
            await asyncio.to_thread(
                store.update_github_delivery,
                delivery_id,
                status="ignored",
            )
            return {"status": "ignored", "delivery_id": delivery_id}

        return {"status": "queued", "delivery_id": delivery_id}

    return app


def build_app_from_environment() -> FastAPI:
    """Build the durable webhook receiver using environment variables."""
    webhook_secret = _required_env("GITHUB_WEBHOOK_SECRET")
    database_url = (os.getenv("CODE_REVIEW_DATABASE_URL")
                    or sqlite_database_url(Path(".code-review") / "github-reviews.db"))
    store = ReviewStore(database_url)
    return create_webhook_app(
        store=store,
        webhook_secret=webhook_secret,
        max_payload_bytes=int(os.getenv("GITHUB_WEBHOOK_MAX_BYTES", "2000000")),
        max_attempts=int(os.getenv("GITHUB_REVIEW_MAX_ATTEMPTS", "5")),
        close_resources=True,
    )


def _required_env(name: str) -> str:
    value = os.getenv(name, "")
    if not value:
        raise ValueError(f"Missing required environment variable: {name}")
    return value
