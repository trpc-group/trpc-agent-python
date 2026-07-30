"""Translate normalized findings into GitHub Checks and review comments."""

from __future__ import annotations

import hashlib
from datetime import timezone
from typing import Any

from ..code_review.models import Finding, ReviewRun, ReviewStatus, Severity
from .client import GitHubClient
from .models import GitHubPullRequestEvent

_ANNOTATION_BATCH_SIZE = 50
_SEVERE = {Severity.CRITICAL, Severity.HIGH}


class GitHubReviewPublisher:
    """Publish one check run and a bounded number of right-side PR comments."""

    def __init__(
        self,
        client: GitHubClient,
        *,
        check_name: str = "tRPC Code Review",
        publish_comments: bool = True,
        max_comments: int = 20,
    ):
        self.client = client
        self.check_name = check_name
        self.publish_comments = publish_comments
        if not 0 <= max_comments <= 100:
            raise ValueError("max_comments must be between 0 and 100")
        self.max_comments = max_comments

    async def create_started_check(self, event: GitHubPullRequestEvent) -> int:
        response = await self.client.create_check_run(
            event.owner,
            event.repository,
            {
                "name": self.check_name,
                "head_sha": event.head_sha,
                "status": "in_progress",
                "external_id": event.delivery_id,
                "started_at": _now_iso(),
                "output": {
                    "title": "Code review in progress",
                    "summary": "The signed pull request delivery is being reviewed.",
                },
            },
        )
        return int(response["id"])

    async def find_existing_check(self, event: GitHubPullRequestEvent) -> int | None:
        """Recover a check created before a worker crash persisted its ID."""
        check_runs = await self.client.list_check_runs_for_ref(
            event.owner,
            event.repository,
            event.head_sha,
            check_name=self.check_name,
        )
        for check_run in check_runs:
            if str(check_run.get("external_id", "")) == event.delivery_id:
                return int(check_run["id"])
        return None

    async def complete_check(
        self,
        event: GitHubPullRequestEvent,
        review_run: ReviewRun,
        check_run_id: int,
    ) -> None:
        annotations = _annotations(review_run)
        summary = _summary(review_run)
        batches = [
            annotations[index:index + _ANNOTATION_BATCH_SIZE]
            for index in range(0, len(annotations), _ANNOTATION_BATCH_SIZE)
        ] or [[]]
        for batch in batches[:-1]:
            await self.client.update_check_run(
                event.owner,
                event.repository,
                check_run_id,
                {
                    "status": "in_progress",
                    "output": {
                        "title": "Code review findings",
                        "summary": summary,
                        "annotations": batch,
                    },
                },
            )
        await self.client.update_check_run(
            event.owner,
            event.repository,
            check_run_id,
            {
                "status": "completed",
                "conclusion": _conclusion(review_run),
                "completed_at": _now_iso(),
                "output": {
                    "title": "Code review completed",
                    "summary": summary,
                    "annotations": batches[-1],
                },
            },
        )

    async def fail_check(
        self,
        event: GitHubPullRequestEvent,
        check_run_id: int,
        message: str,
    ) -> None:
        await self.client.update_check_run(
            event.owner,
            event.repository,
            check_run_id,
            {
                "status": "completed",
                "conclusion": "failure",
                "completed_at": _now_iso(),
                "output": {
                    "title": "Code review failed",
                    "summary": _truncate_utf8(message, 65_535),
                },
            },
        )

    async def publish_line_comments(
        self,
        event: GitHubPullRequestEvent,
        review_run: ReviewRun,
    ) -> int:
        if not self.publish_comments:
            return 0
        existing_comments = await self.client.list_review_comments(
            event.owner,
            event.repository,
            event.pull_number,
        )
        existing_bodies = {
            str(comment.get("body", ""))
            for comment in existing_comments
            if isinstance(comment, dict)
        }
        published = 0
        considered = 0
        for finding, line in _publishable_locations(review_run):
            if considered >= self.max_comments:
                break
            considered += 1
            marker = _comment_marker(review_run.id, finding, line)
            if any(marker in body for body in existing_bodies):
                continue
            await self.client.create_review_comment(
                event.owner,
                event.repository,
                event.pull_number,
                {
                    "body": _comment_body(review_run.id, finding, line),
                    "commit_id": event.head_sha,
                    "path": finding.file_path,
                    "line": line,
                    "side": "RIGHT",
                },
            )
            published += 1
        return published


def _annotations(review_run: ReviewRun) -> list[dict[str, Any]]:
    annotations = []
    for finding, line in _publishable_locations(review_run):
        annotations.append({
            "path": finding.file_path,
            "start_line": line,
            "end_line": line,
            "annotation_level": _annotation_level(finding.severity),
            "title": finding.title[:255],
            "message": _truncate_utf8(finding.description, 65_535),
            "raw_details": _truncate_utf8(finding.suggestion, 65_535),
        })
    return annotations


def _publishable_locations(review_run: ReviewRun):
    changed_lines = {changed_file.path: changed_file.changed_new_lines for changed_file in review_run.changed_files}
    for finding in review_run.output.findings:
        if not finding.publishable or finding.start_line is None or finding.end_line is None:
            continue
        valid_lines = changed_lines.get(finding.file_path, set())
        line = next(
            (candidate for candidate in range(finding.start_line, finding.end_line + 1) if candidate in valid_lines),
            None,
        )
        if line is not None:
            yield finding, line


def _summary(review_run: ReviewRun) -> str:
    publishable = sum(finding.publishable for finding in review_run.output.findings)
    summary = review_run.output.summary or "No model summary was produced."
    return _truncate_utf8(
        f"{summary}\n\n"
        f"- Findings: {len(review_run.output.findings)}\n"
        f"- Line annotations: {publishable}\n"
        f"- Review run: `{review_run.id}`",
        65_535,
    )


def _conclusion(review_run: ReviewRun) -> str:
    if review_run.status != ReviewStatus.COMPLETED:
        return "failure"
    if any(finding.publishable and finding.severity in _SEVERE for finding in review_run.output.findings):
        return "failure"
    if review_run.output.findings:
        return "neutral"
    return "success"


def _annotation_level(severity: Severity) -> str:
    if severity in _SEVERE:
        return "failure"
    if severity == Severity.MEDIUM:
        return "warning"
    return "notice"


def _comment_body(run_id: str, finding: Finding, line: int) -> str:
    marker = _comment_marker(run_id, finding, line)
    suggestion = f"\n\n**Suggestion:** {finding.suggestion}" if finding.suggestion else ""
    return _truncate_utf8(
        f"{marker}\n"
        f"**[{finding.severity.value.upper()}] {finding.title}**\n\n"
        f"{finding.description}{suggestion}\n\n"
        f"`{finding.rule_id}` · confidence {finding.confidence:.2f}",
        65_000,
    )


def _comment_marker(run_id: str, finding: Finding, line: int) -> str:
    identity = "\0".join(
        (run_id, finding.rule_id, finding.file_path, str(line), finding.title)
    )
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
    return f"<!-- trpc-code-review:{digest} -->"


def _now_iso() -> str:
    from datetime import datetime

    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _truncate_utf8(value: str, max_bytes: int) -> str:
    encoded = value.encode("utf-8")
    if len(encoded) <= max_bytes:
        return value
    return encoded[:max_bytes].decode("utf-8", errors="ignore")
