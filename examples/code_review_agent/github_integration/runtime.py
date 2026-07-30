"""Environment-backed construction shared by GitHub worker processes."""

from __future__ import annotations

import os
from dataclasses import asdict
from pathlib import Path

from ..code_review.database import ReviewStore, sqlite_database_url
from ..code_review.orchestrator import ReviewConfig
from ..code_review.static_analysis import StaticAnalysisConfig, StaticAnalyzer
from .checkout import GitHubWorkspaceManager
from .client import GitHubAppTokenProvider, StaticTokenProvider
from .service import GitHubReviewService


def database_url_from_environment() -> str:
    """Return the shared synchronous database URL."""
    return (
        os.getenv("CODE_REVIEW_DATABASE_URL")
        or sqlite_database_url(Path(".code-review") / "github-reviews.db")
    )


def build_store_from_environment() -> ReviewStore:
    """Open the durable review store."""
    return ReviewStore(database_url_from_environment())


def build_service_from_environment(store: ReviewStore) -> GitHubReviewService:
    """Build checkout, analyzer, model, and publication dependencies."""
    api_url = os.getenv("GITHUB_API_URL", "https://api.github.com")
    publish_comments = env_bool("GITHUB_REVIEW_PUBLISH_COMMENTS", True)
    token = os.getenv("GITHUB_TOKEN", "")
    if token:
        token_provider = StaticTokenProvider(token)
    else:
        token_provider = GitHubAppTokenProvider(
            app_id=required_env("GITHUB_APP_ID"),
            private_key=_load_private_key(),
            api_url=api_url,
            permissions={
                "contents": "read",
                "checks": "write",
                "pull_requests": "write" if publish_comments else "read",
            },
        )

    allowed_hosts = tuple(
        host.strip()
        for host in os.getenv("GITHUB_CLONE_HOSTS", "github.com").split(",")
        if host.strip()
    )
    workspace_manager = GitHubWorkspaceManager(
        os.getenv("GITHUB_REVIEW_WORKSPACE_ROOT", ".code-review/workspaces"),
        allowed_hosts=allowed_hosts,
        command_timeout=float(os.getenv("GITHUB_REVIEW_GIT_TIMEOUT", "300")),
    )
    static_config = StaticAnalysisConfig(
        runtime=os.getenv("GITHUB_REVIEW_STATIC_RUNTIME", "docker"),
        run_tests=env_bool("GITHUB_REVIEW_RUN_TESTS", False),
        strict_tools=env_bool("GITHUB_REVIEW_STRICT_TOOLS", False),
        timeout_seconds=float(os.getenv("GITHUB_REVIEW_STATIC_TIMEOUT", "120")),
        docker_image=os.getenv("GITHUB_REVIEW_DOCKER_IMAGE", "trpc-code-review:latest"),
    )
    analyzer = StaticAnalyzer(static_config)
    reviewer = None
    model_name = ""
    if not env_bool("GITHUB_REVIEW_NO_LLM", False):
        from ..agent.reviewer import review_with_llm

        reviewer = review_with_llm
        model_name = os.getenv("TRPC_AGENT_MODEL_NAME", "")
    return GitHubReviewService(
        store=store,
        token_provider=token_provider,
        workspace_manager=workspace_manager,
        reviewer=reviewer,
        static_analyzer=analyzer.analyze,
        review_config=ReviewConfig(
            minimum_confidence=float(
                os.getenv("GITHUB_REVIEW_MINIMUM_CONFIDENCE", "0.75")
            ),
        ),
        model_name=model_name,
        execution_config={
            "static_analysis": asdict(static_config),
            "model_base_url": os.getenv("TRPC_AGENT_BASE_URL", "") if reviewer else "",
        },
        api_url=api_url,
        publish_comments=publish_comments,
        max_comments=int(os.getenv("GITHUB_REVIEW_MAX_COMMENTS", "20")),
    )


def required_env(name: str) -> str:
    value = os.getenv(name, "")
    if not value:
        raise ValueError(f"Missing required environment variable: {name}")
    return value


def env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().casefold()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean value")


def _load_private_key() -> str:
    inline = os.getenv("GITHUB_APP_PRIVATE_KEY", "")
    if inline:
        return inline.replace("\\n", "\n")
    path = required_env("GITHUB_APP_PRIVATE_KEY_PATH")
    return Path(path).expanduser().read_text(encoding="utf-8")
