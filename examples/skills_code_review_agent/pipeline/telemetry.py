"""Telemetry — monitoring and audit logging for review runs.

Captures execution metrics that map to the tRPC-Agent telemetry
framework when running inside an agent.
"""

import time
from dataclasses import dataclass, field


@dataclass
class TelemetryCollector:
    """Collects execution metrics for a review run."""

    start_time: float = field(default_factory=time.monotonic)
    parse_duration_ms: int = 0
    scan_duration_ms: int = 0
    filter_duration_ms: int = 0
    sandbox_total_duration_ms: int = 0
    sandbox_runs: int = 0
    sandbox_failures: int = 0
    dedup_duration_ms: int = 0
    redact_duration_ms: int = 0
    report_duration_ms: int = 0
    db_write_duration_ms: int = 0
    total_findings_before_dedup: int = 0
    total_findings_after_dedup: int = 0
    filter_intercepts: int = 0
    redaction_count: int = 0
    files_scanned: int = 0
    errors: list[str] = field(default_factory=list)

    def snapshot(self) -> dict:
        """Return a snapshot of collected metrics."""
        total_ms = int((time.monotonic() - self.start_time) * 1000)
        return {
            "total_duration_ms": total_ms,
            "parse_duration_ms": self.parse_duration_ms,
            "scan_duration_ms": self.scan_duration_ms,
            "filter_duration_ms": self.filter_duration_ms,
            "sandbox_total_duration_ms": self.sandbox_total_duration_ms,
            "sandbox_runs": self.sandbox_runs,
            "sandbox_failures": self.sandbox_failures,
            "dedup_duration_ms": self.dedup_duration_ms,
            "redact_duration_ms": self.redact_duration_ms,
            "report_duration_ms": self.report_duration_ms,
            "db_write_duration_ms": self.db_write_duration_ms,
            "findings_before_dedup": self.total_findings_before_dedup,
            "findings_after_dedup": self.total_findings_after_dedup,
            "filter_intercepts": self.filter_intercepts,
            "redaction_count": self.redaction_count,
            "files_scanned": self.files_scanned,
            "error_count": len(self.errors),
        }

    def record_error(self, message: str) -> None:
        """Record an error that occurred during processing."""
        self.errors.append(message)


def timed(collector: TelemetryCollector, attr: str):
    """Context manager to time a code block and store in collector.

    Usage:
        with timed(tel, 'parse_duration_ms'):
            result = parse_diff(text)
    """
    from contextlib import contextmanager

    @contextmanager
    def _timed():
        start = time.monotonic()
        try:
            yield
        finally:
            setattr(collector, attr, int((time.monotonic() - start) * 1000))

    return _timed()
