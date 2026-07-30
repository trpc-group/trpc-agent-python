"""Validated domain models for supported GitHub webhook payloads."""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

SUPPORTED_PULL_REQUEST_ACTIONS = {
    "opened",
    "reopened",
    "synchronize",
    "ready_for_review",
}


class GitHubPullRequestEvent(BaseModel):
    """Minimal immutable coordinates needed to review one pull request head."""

    delivery_id: str = Field(min_length=1, max_length=128)
    action: str = Field(min_length=1, max_length=80)
    installation_id: int = Field(gt=0)
    repository_full_name: str = Field(pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
    owner: str
    repository: str
    pull_number: int = Field(gt=0)
    base_sha: str = Field(pattern=r"^[0-9a-fA-F]{40,64}$")
    head_sha: str = Field(pattern=r"^[0-9a-fA-F]{40,64}$")
    base_clone_url: str
    head_clone_url: str
    draft: bool = False

    @field_validator("owner", "repository")
    @classmethod
    def validate_repository_component(cls, value: str) -> str:
        if not value or any(character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-"
                            for character in value):
            raise ValueError("invalid GitHub repository component")
        return value

    @classmethod
    def from_webhook(
        cls,
        *,
        delivery_id: str,
        event_name: str,
        payload: dict,
    ) -> GitHubPullRequestEvent | None:
        """Return a supported PR event, or none for events/actions to ignore."""
        if event_name != "pull_request":
            return None
        action = str(payload.get("action", ""))
        if action not in SUPPORTED_PULL_REQUEST_ACTIONS:
            return None
        pull_request = payload["pull_request"]
        repository = payload["repository"]
        installation = payload["installation"]
        full_name = str(repository["full_name"])
        owner, repository_name = full_name.split("/", maxsplit=1)
        return cls(
            delivery_id=delivery_id,
            action=action,
            installation_id=int(installation["id"]),
            repository_full_name=full_name,
            owner=owner,
            repository=repository_name,
            pull_number=int(payload["number"]),
            base_sha=str(pull_request["base"]["sha"]),
            head_sha=str(pull_request["head"]["sha"]),
            base_clone_url=str(pull_request["base"]["repo"]["clone_url"]),
            head_clone_url=str(pull_request["head"]["repo"]["clone_url"]),
            draft=bool(pull_request.get("draft", False)),
        )
