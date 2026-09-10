"""Low-cardinality metrics and OpenTelemetry helpers."""

from __future__ import annotations

import threading
from collections import Counter, defaultdict
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from time import perf_counter


def configure_otel_from_env() -> bool:
    """Enable OTLP export when OTEL_EXPORTER_OTLP_ENDPOINT is configured."""

    import os

    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "")
    if not endpoint:
        return False
    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    provider = TracerProvider(
        resource=Resource.create({"service.name": "trpc-agent-im-gateway"})
    )
    trace_endpoint = (
        endpoint
        if endpoint.rstrip("/").endswith("/v1/traces")
        else endpoint.rstrip("/") + "/v1/traces"
    )
    provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=trace_endpoint))
    )
    trace.set_tracer_provider(provider)
    return True


@dataclass
class TraceResult:
    trace_id: str = ""
    latency_ms: int = 0


@contextmanager
def request_span(
    tenant_id: str, channel: str, session_id: str
) -> Iterator[TraceResult]:
    """Create a safe root span without recording message content or secrets."""

    started = perf_counter()
    result = TraceResult()
    try:
        from opentelemetry import trace
    except ImportError:
        trace = None
    try:
        if trace is None:
            yield result
        else:
            tracer = trace.get_tracer("trpc-agent.multi-tenant-im")
            with tracer.start_as_current_span("im.callback") as span:
                span.set_attribute("tenant.id", tenant_id)
                span.set_attribute("im.channel", channel)
                span.set_attribute("session.id", session_id)
                context = span.get_span_context()
                if context.is_valid:
                    result.trace_id = f"{context.trace_id:032x}"
                yield result
    finally:
        result.latency_ms = int((perf_counter() - started) * 1000)


class GatewayMetrics:
    def __init__(self):
        self._lock = threading.Lock()
        self._requests: Counter[tuple[str, str, str]] = Counter()
        self._delivery: Counter[tuple[str, str]] = Counter()
        self._latency_sum: dict[tuple[str, str], float] = defaultdict(float)
        self._latency_count: Counter[tuple[str, str]] = Counter()
        self._tokens: Counter[str] = Counter()

    def observe_request(
        self, tenant: str, channel: str, status: str, latency_ms: int, tokens: int = 0
    ) -> None:
        with self._lock:
            self._requests[(tenant, channel, status)] += 1
            self._latency_sum[(tenant, channel)] += latency_ms
            self._latency_count[(tenant, channel)] += 1
            self._tokens[tenant] += tokens

    def observe_delivery(self, channel: str, status: str) -> None:
        with self._lock:
            self._delivery[(channel, status)] += 1

    def render_prometheus(self) -> str:
        lines = [
            "# HELP trpc_im_requests_total IM callbacks handled.",
            "# TYPE trpc_im_requests_total counter",
        ]
        with self._lock:
            for (tenant, channel, status), value in sorted(self._requests.items()):
                lines.append(
                    f'trpc_im_requests_total{{tenant="{tenant}",channel="{channel}",status="{status}"}} {value}'
                )
            lines.extend(
                [
                    "# HELP trpc_im_request_latency_ms_sum Total callback latency in milliseconds.",
                    "# TYPE trpc_im_request_latency_ms_sum counter",
                ]
            )
            for (tenant, channel), value in sorted(self._latency_sum.items()):
                labels = f'tenant="{tenant}",channel="{channel}"'
                lines.append(f"trpc_im_request_latency_ms_sum{{{labels}}} {value}")
                lines.append(
                    f"trpc_im_request_latency_ms_count{{{labels}}} {self._latency_count[(tenant, channel)]}"
                )
            for (channel, status), value in sorted(self._delivery.items()):
                lines.append(
                    f'trpc_im_delivery_total{{channel="{channel}",status="{status}"}} {value}'
                )
            for tenant, value in sorted(self._tokens.items()):
                lines.append(f'trpc_im_tokens_total{{tenant="{tenant}"}} {value}')
        return "\n".join(lines) + "\n"
