"""Orchestrate checkout, review, persistence, and GitHub publication."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from ..code_review.database import ReviewStore
from ..code_review.orchestrator import (
    ReviewCallable,
    ReviewConfig,
    StaticAnalysisCallable,
    run_review,
)
from .checkout import GitHubWorkspaceManager
from .client import GitHubClient, TokenProvider
from .models import GitHubPullRequestEvent
from .publisher import GitHubReviewPublisher

GitHubClientFactory = Callable[[GitHubPullRequestEvent], GitHubClient]
logger = logging.getLogger(__name__)


class GitHubReviewService:
    """Run the existing deterministic pipeline for one validated PR event."""

    def __init__(
        self,
        *,
        store: ReviewStore,
        token_provider: TokenProvider,
        workspace_manager: GitHubWorkspaceManager,
        reviewer: ReviewCallable | None = None,
        static_analyzer: StaticAnalysisCallable | None = None,
        review_config: ReviewConfig | None = None,
        model_name: str = "",
        execution_config: dict[str, Any] | None = None,
        api_url: str = "https://api.github.com",
        publish_comments: bool = True,
        max_comments: int = 20,
        client_factory: GitHubClientFactory | None = None,
    ):
        self.store = store
        self.token_provider = token_provider
        self.workspace_manager = workspace_manager
        self.reviewer = reviewer
        self.static_analyzer = static_analyzer
        self.review_config = review_config or ReviewConfig()
        self.model_name = model_name
        self.execution_config = execution_config or {}
        self.api_url = api_url
        self.publish_comments = publish_comments
        self.max_comments = max_comments
        self.client_factory = client_factory

    async def process(self, event: GitHubPullRequestEvent) -> None:
        """Process a claimed delivery and persist every terminal outcome."""
        await asyncio.to_thread(
            self.store.update_github_delivery,
            event.delivery_id,
            status="processing",
        )
        client = (self.client_factory(event) if self.client_factory is not None else GitHubClient(
            token_provider=self.token_provider,
            installation_id=event.installation_id,
            api_url=self.api_url,
        ))
        publisher = GitHubReviewPublisher(
            client,
            publish_comments=self.publish_comments,
            max_comments=self.max_comments,
        )
        workspace = None
        delivery = await asyncio.to_thread(
            self.store.get_github_delivery,
            event.delivery_id,
        )
        publication = await asyncio.to_thread(
            self.store.get_github_publication,
            event.delivery_id,
        )
        check_completed = publication["check_completed"]
        comments_completed = publication["comments_completed"]
        check_run_id = delivery["check_run_id"] if delivery is not None else None
        review_run_id = None
        try:
            if check_run_id is None:
                check_run_id = await publisher.find_existing_check(event)
            if check_run_id is None:
                check_run_id = await publisher.create_started_check(event)
            await asyncio.to_thread(
                self.store.update_github_delivery,
                event.delivery_id,
                status="processing",
                check_run_id=check_run_id,
                error_message="",
            )
            token = await self.token_provider.get_token(event.installation_id)
            workspace = await asyncio.to_thread(self.workspace_manager.checkout, event, token)
            review_run = await run_review(
                repository=workspace,
                repository_identity=f"github://{event.repository_full_name}",
                base_revision=event.base_sha,
                head_revision=event.head_sha,
                config=self.review_config,
                reviewer=self.reviewer,
                static_analyzer=self.static_analyzer,
                model_name=self.model_name,
                execution_config={
                    **self.execution_config,
                    "source": "github_webhook",
                    "repository": event.repository_full_name,
                    "pull_number": event.pull_number,
                },
            )
            save_result = await asyncio.to_thread(self.store.save_run, review_run)
            persisted_run = save_result.review_run
            review_run_id = persisted_run.id
            if not check_completed:
                await publisher.complete_check(event, persisted_run, check_run_id)
                await asyncio.to_thread(
                    self.store.update_github_publication,
                    event.delivery_id,
                    check_completed=True,
                )
                check_completed = True
            if not comments_completed:
                await publisher.publish_line_comments(event, persisted_run)
                await asyncio.to_thread(
                    self.store.update_github_publication,
                    event.delivery_id,
                    comments_completed=True,
                )
            await asyncio.to_thread(
                self.store.update_github_delivery,
                event.delivery_id,
                status="completed",
                review_run_id=review_run_id,
                check_run_id=check_run_id,
                error_message="",
            )
        except Exception as exc:
            if check_run_id is not None and not check_completed:
                try:
                    await publisher.fail_check(event, check_run_id, str(exc))
                except Exception:
                    logger.warning(
                        "Unable to mark GitHub check run %s as failed",
                        check_run_id,
                        exc_info=True,
                    )
            await asyncio.to_thread(
                self.store.update_github_delivery,
                event.delivery_id,
                status="failed",
                review_run_id=review_run_id,
                check_run_id=check_run_id,
                error_message=str(exc)[:10_000],
            )
            raise
        finally:
            if workspace is not None:
                await asyncio.to_thread(self.workspace_manager.cleanup, workspace)
            await client.close()
