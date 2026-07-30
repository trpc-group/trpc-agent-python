"""SQLite persistence and idempotency integration tests."""

import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from examples.code_review_agent.code_review.database import (
    SCHEMA_VERSION,
    GitHubReviewJobRecord,
    ReviewSchemaVersion,
    ReviewStore,
    sqlite_database_url,
)
from examples.code_review_agent.code_review.models import ReviewStatus
from examples.code_review_agent.code_review.orchestrator import run_review


@pytest.mark.asyncio
async def test_round_trips_complete_review_graph(
    sample_repository: tuple[Path, str, str],
    tmp_path: Path,
) -> None:
    repository, base, head = sample_repository
    review_run = await run_review(
        repository=repository,
        base_revision=base,
        head_revision=head,
    )
    store = ReviewStore(sqlite_database_url(tmp_path / "reviews.db"))

    saved = store.save_run(review_run)
    loaded = store.get_run(review_run.id)

    assert saved.created is True
    assert loaded is not None
    assert loaded.model_dump(mode="json") == saved.review_run.model_dump(mode="json")
    assert len(loaded.changed_files) == 2
    assert loaded.changed_files[0].hunks
    assert loaded.resolved_head_revision == head
    store.close()


@pytest.mark.asyncio
async def test_duplicate_idempotency_key_returns_existing_run(
    sample_repository: tuple[Path, str, str],
    tmp_path: Path,
) -> None:
    repository, base, head = sample_repository
    first_run = await run_review(
        repository=repository,
        base_revision=base,
        head_revision=head,
    )
    second_run = await run_review(
        repository=repository,
        base_revision=base,
        head_revision=head,
    )
    store = ReviewStore(sqlite_database_url(tmp_path / "reviews.db"))

    first = store.save_run(first_run)
    duplicate = store.save_run(second_run)

    assert first.created is True
    assert duplicate.created is False
    assert duplicate.review_run.id == first_run.id
    assert first_run.idempotency_key == second_run.idempotency_key
    assert store.count_runs() == 1
    store.close()


@pytest.mark.asyncio
async def test_lists_and_filters_runs(
    sample_repository: tuple[Path, str, str],
    tmp_path: Path,
) -> None:
    repository, base, head = sample_repository
    completed = await run_review(
        repository=repository,
        base_revision=base,
        head_revision=head,
    )
    failed = await run_review(
        repository=repository,
        base_revision="missing-revision",
        head_revision=head,
    )
    store = ReviewStore(sqlite_database_url(tmp_path / "reviews.db"))
    store.save_run(completed)
    store.save_run(failed)

    failed_runs = store.list_runs(
        repository_path=str(repository),
        status=ReviewStatus.FAILED,
    )

    assert [run.id for run in failed_runs] == [failed.id]
    assert failed_runs[0].error_message
    assert store.get_by_idempotency_key(completed.idempotency_key).id == completed.id
    store.close()


def test_initializes_schema_version_and_validates_limits(tmp_path: Path) -> None:
    database_url = sqlite_database_url(tmp_path / "nested" / "reviews.db")
    store = ReviewStore(database_url)

    with Session(store.engine) as session:
        marker = session.scalar(select(ReviewSchemaVersion))

    assert marker is not None
    assert marker.version == SCHEMA_VERSION
    with pytest.raises(ValueError):
        store.list_runs(limit=0)
    store.close()

    with pytest.raises(ValueError, match="synchronous"):
        ReviewStore("sqlite+aiosqlite:///:memory:")

    version_store = ReviewStore(database_url)
    with Session(version_store.engine) as session, session.begin():
        session.get(ReviewSchemaVersion, 1).version = 1
    version_store.close()
    migrated_store = ReviewStore(database_url)
    with Session(migrated_store.engine) as session:
        assert session.get(ReviewSchemaVersion, 1).version == SCHEMA_VERSION
    migrated_store.close()

    version_store = ReviewStore(database_url)
    with Session(version_store.engine) as session, session.begin():
        session.get(ReviewSchemaVersion, 1).version = SCHEMA_VERSION + 1
    version_store.close()
    with pytest.raises(RuntimeError, match="Unsupported"):
        ReviewStore(database_url)


def test_durable_job_lease_recovery_and_replay(tmp_path: Path) -> None:
    store = ReviewStore(sqlite_database_url(tmp_path / "reviews.db"))
    event_payload = {
        "delivery_id": "delivery-job",
        "action": "opened",
        "installation_id": 123,
        "repository_full_name": "octo/demo",
        "owner": "octo",
        "repository": "demo",
        "pull_number": 17,
        "base_sha": "a" * 40,
        "head_sha": "b" * 40,
        "base_clone_url": "https://github.com/octo/demo.git",
        "head_clone_url": "https://github.com/octo/demo.git",
        "draft": False,
    }
    assert store.enqueue_github_delivery(
        delivery_id="delivery-job",
        event_name="pull_request",
        action="opened",
        payload_sha256="f" * 64,
        event_payload=event_payload,
        repository_full_name="octo/demo",
        pull_number=17,
        head_sha="b" * 40,
        installation_id=123,
        max_attempts=2,
    )
    assert not store.enqueue_github_delivery(
        delivery_id="delivery-job",
        event_name="pull_request",
        action="opened",
        payload_sha256="f" * 64,
        event_payload=event_payload,
        repository_full_name="octo/demo",
        pull_number=17,
        head_sha="b" * 40,
        installation_id=123,
    )

    first = store.claim_github_job(worker_id="worker-a", lease_seconds=30)
    assert first is not None
    assert first.attempt_count == 1
    assert store.renew_github_job_lease(
        "delivery-job",
        worker_id="worker-a",
        lease_seconds=30,
    )
    assert store.claim_github_job(worker_id="worker-b") is None

    with Session(store.engine) as session, session.begin():
        session.execute(
            update(GitHubReviewJobRecord)
            .where(GitHubReviewJobRecord.delivery_id == "delivery-job")
            .values(lease_expires_at=datetime.now(timezone.utc) - timedelta(seconds=1))
        )
    recovered = store.claim_github_job(worker_id="worker-b", lease_seconds=30)
    assert recovered is not None
    assert recovered.attempt_count == 2
    assert not store.complete_github_job("delivery-job", worker_id="worker-a")
    assert (
        store.fail_github_job(
            "delivery-job",
            worker_id="worker-b",
            error_message="still failing",
            retry_delay_seconds=0,
        )
        == "dead"
    )
    assert store.replay_github_job("delivery-job")
    replayed = store.claim_github_job(worker_id="worker-c", lease_seconds=30)
    assert replayed is not None
    assert replayed.attempt_count == 1
    assert store.complete_github_job("delivery-job", worker_id="worker-c")
    assert store.get_github_job("delivery-job")["status"] == "succeeded"
    store.close()


def test_cli_persists_and_inspects_idempotent_run(
    sample_repository: tuple[Path, str, str],
    tmp_path: Path,
) -> None:
    repository, base, head = sample_repository
    database_url = sqlite_database_url(tmp_path / "reviews.db")
    output_directory = tmp_path / "reports"
    run_command = [
        sys.executable,
        "examples/code_review_agent/run_review.py",
        "--repo",
        str(repository),
        "--base",
        base,
        "--head",
        head,
        "--output-dir",
        str(output_directory),
        "--database-url",
        database_url,
        "--no-llm",
        "--no-static-analysis",
    ]

    first = subprocess.run(run_command, check=True, capture_output=True, text=True)
    second = subprocess.run(run_command, check=True, capture_output=True, text=True)
    listed = subprocess.run(
        [
            sys.executable,
            "examples/code_review_agent/inspect_reviews.py",
            "--database-url",
            database_url,
            "list",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert "Persistence: created" in first.stdout
    assert "Persistence: reused existing idempotent run" in second.stdout
    assert "completed" in listed.stdout
    store = ReviewStore(database_url)
    assert store.count_runs() == 1
    store.close()
