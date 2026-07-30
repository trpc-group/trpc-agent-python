"""Durable GitHub review worker with leases, retries, and crash recovery."""

from __future__ import annotations

import asyncio
import logging
import random
import socket
import uuid
from collections.abc import Callable

import httpx
from pydantic import ValidationError

from ..code_review.database import ClaimedReviewJob, ReviewStore
from .client import GitHubApiError
from .models import GitHubPullRequestEvent
from .service import GitHubReviewService

logger = logging.getLogger(__name__)


class GitHubReviewWorker:
    """Poll and execute durable review jobs with bounded retry behavior."""

    def __init__(
        self,
        *,
        store: ReviewStore,
        service: GitHubReviewService,
        worker_id: str | None = None,
        lease_seconds: float = 300,
        poll_seconds: float = 2,
        base_retry_seconds: float = 5,
        max_retry_seconds: float = 300,
        jitter: Callable[[], float] = random.random,
    ):
        if lease_seconds <= 0 or poll_seconds <= 0:
            raise ValueError("lease_seconds and poll_seconds must be positive")
        if base_retry_seconds < 0 or max_retry_seconds < base_retry_seconds:
            raise ValueError("invalid retry delay configuration")
        self.store = store
        self.service = service
        self.worker_id = worker_id or f"{socket.gethostname()}-{uuid.uuid4().hex[:12]}"
        self.lease_seconds = lease_seconds
        self.poll_seconds = poll_seconds
        self.base_retry_seconds = base_retry_seconds
        self.max_retry_seconds = max_retry_seconds
        self.jitter = jitter
        self._stopping = asyncio.Event()

    async def run_forever(self) -> None:
        """Process jobs until stop is requested or the task is cancelled."""
        while not self._stopping.is_set():
            processed = await self.run_once()
            if not processed:
                try:
                    await asyncio.wait_for(self._stopping.wait(), timeout=self.poll_seconds)
                except TimeoutError:
                    pass

    def stop(self) -> None:
        """Request graceful shutdown after the current job."""
        self._stopping.set()

    async def run_once(self) -> bool:
        """Process at most one available job."""
        job = await asyncio.to_thread(
            self.store.claim_github_job,
            worker_id=self.worker_id,
            lease_seconds=self.lease_seconds,
        )
        if job is None:
            return False
        heartbeat = asyncio.create_task(self._heartbeat(job))
        try:
            event = GitHubPullRequestEvent.model_validate(job.event_payload)
            await self.service.process(event)
            completed = await asyncio.to_thread(
                self.store.complete_github_job,
                job.delivery_id,
                worker_id=self.worker_id,
            )
            if not completed:
                raise RuntimeError(f"GitHub review job lease was lost: {job.delivery_id}")
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - job boundary must persist all failures
            retryable = is_retryable_error(exc)
            delay = self._retry_delay(job.attempt_count)
            try:
                state = await asyncio.to_thread(
                    self.store.fail_github_job,
                    job.delivery_id,
                    worker_id=self.worker_id,
                    error_message=str(exc),
                    retry_delay_seconds=delay,
                    retryable=retryable,
                )
            except RuntimeError:
                logger.exception("Unable to fail job after its lease was lost: %s", job.delivery_id)
            else:
                logger.warning(
                    "GitHub review job %s %s after attempt %s/%s: %s",
                    job.delivery_id,
                    "will retry" if state == "queued" else "is dead",
                    job.attempt_count,
                    job.max_attempts,
                    exc,
                )
        finally:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)
        return True

    async def _heartbeat(self, job: ClaimedReviewJob) -> None:
        interval = max(0.1, self.lease_seconds / 3)
        while True:
            await asyncio.sleep(interval)
            renewed = await asyncio.to_thread(
                self.store.renew_github_job_lease,
                job.delivery_id,
                worker_id=self.worker_id,
                lease_seconds=self.lease_seconds,
            )
            if not renewed:
                logger.error("Lost lease for GitHub review job %s", job.delivery_id)
                return

    def _retry_delay(self, attempt_count: int) -> float:
        exponential = min(
            self.max_retry_seconds,
            self.base_retry_seconds * (2 ** max(0, attempt_count - 1)),
        )
        return exponential * (0.5 + self.jitter() * 0.5)


def is_retryable_error(error: Exception) -> bool:
    """Classify transient infrastructure/API failures conservatively."""
    if isinstance(error, GitHubApiError):
        return error.status_code == 429 or error.status_code >= 500
    if isinstance(error, (httpx.TransportError, TimeoutError, OSError)):
        return True
    return not isinstance(error, (ValidationError, ValueError, KeyError))
