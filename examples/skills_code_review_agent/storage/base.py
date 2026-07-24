"""Abstract persistence contract for code review data."""

from abc import ABC
from abc import abstractmethod
from datetime import datetime

from reports.models import ReviewReport
from reports.models import ReviewScope


class BaseReviewStore(ABC):
    """Persist and retrieve completed review reports."""

    @abstractmethod
    def initialize(self) -> None:
        """Create the minimal storage schema if needed."""
        raise NotImplementedError

    @abstractmethod
    def start_task(
        self,
        task_id: str,
        created_at: datetime,
        repository: str,
        scope: ReviewScope,
    ) -> None:
        """Persist a running task before model or sandbox execution."""
        raise NotImplementedError

    @abstractmethod
    def mark_task_failed(
        self,
        task_id: str,
        completed_at: datetime,
        conclusion: str,
    ) -> None:
        """Mark an already-started task failed when finalization aborts."""
        raise NotImplementedError

    @abstractmethod
    def save(self, report: ReviewReport) -> None:
        """Persist a completed, normalized report."""
        raise NotImplementedError

    @abstractmethod
    def get(self, task_id: str) -> ReviewReport | None:
        """Retrieve a report by identifier."""
        raise NotImplementedError

    @abstractmethod
    def get_latest_by_input_digest(
        self,
        digest: str,
        review_profile: str,
    ) -> ReviewReport | None:
        """Retrieve the newest report for an exact immutable input digest."""
        raise NotImplementedError

    @abstractmethod
    def get_task_details(self, task_id: str) -> dict[str, object] | None:
        """Retrieve normalized task, run, decision, finding, and metrics rows."""
        raise NotImplementedError
