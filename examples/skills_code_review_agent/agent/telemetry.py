"""Small OpenTelemetry helpers for the code-review pipeline.

The SDK tracer safely becomes a no-op when no exporter is configured, so the
deterministic example can always emit spans without adding a deployment
requirement.  The optional console exporter is installed lazily for demos.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from trpc_agent_sdk.telemetry import tracer

from .redaction import redact_text


def _attribute_value(value: Any) -> str | bool | int | float:
    """Convert arbitrary review metadata into an OpenTelemetry-safe value."""
    if isinstance(value, (str, bool, int, float)):
        return value
    if value is None:
        return ""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def span_attributes(**attributes: Any) -> dict[str, str | bool | int | float]:
    """Normalize and omit absent attributes before sending them to OTel."""
    return {key: _attribute_value(value) for key, value in attributes.items() if value is not None}


def set_span_attributes(span: Any, **attributes: Any) -> None:
    """Attach normalized attributes to a span-like object."""
    for key, value in span_attributes(**attributes).items():
        span.set_attribute(key, value)


@contextmanager
def review_span(name: str, **attributes: Any) -> Iterator[Any]:
    """Create one named review span and record failures before re-raising."""
    # Disable OTel's automatic exception/status handling: it serializes the
    # original exception and traceback after this generator re-raises, which
    # would bypass the explicit redaction below.
    with tracer.start_as_current_span(
        name,
        attributes=span_attributes(**attributes),
        record_exception=False,
        set_status_on_exception=False,
    ) as span:
        try:
            yield span
        except Exception as exc:
            redacted_message, _ = redact_text(str(exc))
            redacted_message = redacted_message[:500]
            set_span_attributes(span, error_type=type(exc).__name__, error_message=redacted_message)
            record_exception = getattr(span, "record_exception", None)
            if callable(record_exception):
                # OTel's normal record_exception path serializes the original
                # exception message and traceback. Record a new traceback-free,
                # redacted exception so credentials cannot leave via telemetry.
                record_exception(RuntimeError(f"{type(exc).__name__}: {redacted_message}"))
            set_status = getattr(span, "set_status", None)
            if callable(set_status):
                from opentelemetry.trace import Status, StatusCode

                set_status(Status(StatusCode.ERROR, f"{type(exc).__name__}: {redacted_message}"))
            raise


def configure_console_exporter() -> None:
    """Install a process-local console span exporter once.

    ``opentelemetry-sdk`` is an optional package.  A clear error is preferable
    to silently ignoring ``--telemetry-console`` when it is unavailable.
    Existing providers (for example a service-owned OTLP provider) are reused
    rather than replaced.
    """
    try:
        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import (
            ConsoleSpanExporter,
            SimpleSpanProcessor,
        )
    except ImportError as exc:  # pragma: no cover - depends on optional extras
        raise RuntimeError(
            "--telemetry-console requires the opentelemetry-sdk package"
        ) from exc

    provider = trace.get_tracer_provider()
    if not hasattr(provider, "add_span_processor"):
        provider = TracerProvider()
        trace.set_tracer_provider(provider)

    marker = "_code_review_console_exporter_installed"
    if getattr(provider, marker, False):
        return
    provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
    setattr(provider, marker, True)
