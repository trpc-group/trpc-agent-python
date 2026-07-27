"""Tests for the example's no-op-safe telemetry wrapper."""

from __future__ import annotations

from contextlib import contextmanager

import pytest

from examples.skills_code_review_agent.agent import telemetry


class _Span:
    def __init__(self) -> None:
        self.attributes = {}
        self.exceptions = []

    def set_attribute(self, key, value) -> None:
        self.attributes[key] = value

    def record_exception(self, error) -> None:
        self.exceptions.append(error)


class _Tracer:
    def __init__(self) -> None:
        self.name = ""
        self.names = []
        self.initial_attributes = {}
        self.span = _Span()

    @contextmanager
    def start_as_current_span(self, name, attributes, **kwargs):
        self.name = name
        self.names.append(name)
        self.initial_attributes = attributes
        yield self.span


def test_review_span_normalizes_attributes_and_records_runtime_values(monkeypatch):
    fake = _Tracer()
    monkeypatch.setattr(telemetry, "tracer", fake)

    with telemetry.review_span("code_review.rules", task_id="task-1", details={"b": 2}) as span:
        telemetry.set_span_attributes(span, finding_count=3, skipped=None)

    assert fake.name == "code_review.rules"
    assert fake.initial_attributes == {"task_id": "task-1", "details": '{"b": 2}'}
    assert fake.span.attributes == {"finding_count": 3}


def test_review_span_records_exception_and_reraises(monkeypatch):
    fake = _Tracer()
    monkeypatch.setattr(telemetry, "tracer", fake)

    with pytest.raises(ValueError, match="bad input"), telemetry.review_span("code_review.parse_diff"):
        raise ValueError("bad input")

    assert fake.span.attributes["error_type"] == "ValueError"
    assert fake.span.attributes["error_message"] == "bad input"
    assert isinstance(fake.span.exceptions[0], RuntimeError)
    assert str(fake.span.exceptions[0]) == "ValueError: bad input"


def test_review_span_never_records_raw_secrets_from_exceptions(monkeypatch):
    fake = _Tracer()
    monkeypatch.setattr(telemetry, "tracer", fake)
    secret = "ghp_abcdefghijklmnopqrstuvwxyz123456"

    with pytest.raises(RuntimeError, match="request failed"), telemetry.review_span("code_review.persist"):
        raise RuntimeError(f"request failed token={secret}")

    recorded = f"{fake.span.attributes} {fake.span.exceptions}"
    assert secret not in recorded
    assert "<REDACTED>" in recorded


def test_real_otel_context_manager_cannot_auto_record_the_raw_exception(monkeypatch):
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    provider = TracerProvider()
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(telemetry, "tracer", provider.get_tracer("code-review-test"))
    secret = "ghp_abcdefghijklmnopqrstuvwxyz123456"

    with pytest.raises(RuntimeError, match="request failed"), telemetry.review_span("code_review.persist"):
        raise RuntimeError(f"request failed token={secret}")

    finished = exporter.get_finished_spans()
    assert len(finished) == 1
    span = finished[0]
    serialized = repr((span.attributes, span.events, span.status))
    assert secret not in serialized
    assert "<REDACTED>" in serialized


def test_review_pipeline_emits_root_and_phase_spans(monkeypatch, tmp_path):
    from examples.skills_code_review_agent.agent.review_engine import (
        ReviewConfig,
        run_review,
    )

    fake = _Tracer()
    monkeypatch.setattr(telemetry, "tracer", fake)

    run_review(
        ReviewConfig(
            fixture="no_issue",
            output_dir=tmp_path / "out",
            db_path=tmp_path / "review.sqlite3",
            dry_run=True,
            task_id="telemetry-test",
        ))

    assert {
        "code_review.review",
        "code_review.load_input",
        "code_review.parse_diff",
        "code_review.sandbox",
        "code_review.sandbox_run",
        "code_review.rules",
        "code_review.context_suppression",
        "code_review.persist",
        "code_review.report",
    }.issubset(fake.names)
