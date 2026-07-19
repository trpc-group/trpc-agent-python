"""Configuration helpers for the skills code review example."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

DEFAULT_RUNTIME_TYPE = "container"
DEFAULT_DB_PATH = "review_agent.db"
DEFAULT_OUTPUT_DIR = "review_outputs"


@dataclass(slots=True, frozen=True)
class ReviewAgentConfig:
    """Runtime configuration for the code review agent."""

    diff_file: str | None = None
    repo_path: str | None = None
    fixture_path: str | None = None
    output_dir: Path = Path(DEFAULT_OUTPUT_DIR)
    db_path: Path = Path(DEFAULT_DB_PATH)
    runtime: str = DEFAULT_RUNTIME_TYPE
    dry_run: bool = False
    fake_model: bool = False

    def __post_init__(self) -> None:
        """Validate mutually exclusive input sources and normalize paths."""

        provided_sources = [
            value
            for value in (self.diff_file, self.repo_path, self.fixture_path)
            if value is not None
        ]
        if len(provided_sources) != 1:
            raise ValueError(
                "exactly one of diff_file, repo_path, or fixture_path must be provided"
            )
