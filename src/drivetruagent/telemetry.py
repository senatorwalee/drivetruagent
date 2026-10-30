"""Sending traces to Arize Phoenix, when asked to.

With TRACING_ENDPOINT unset (the default) nothing is set up and every span in
agent/tracing.py is a no-op. With it set, spans go out over OTLP HTTP in
batches, off the request path.

    make phoenix                                   # Phoenix on localhost:6006
    TRACING_ENDPOINT=http://localhost:6006/v1/traces

FastAPI traces every request it serves on its own (FastAPI(telemetry=...)).
APP_TELEMETRY keeps that to the requests worth reading: see untraced.
"""

from collections.abc import MutableMapping
from typing import Any

from fastapi.telemetry import TelemetryConfig
from openinference.semconv.resource import ResourceAttributes
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter

from drivetruagent.config import Settings


def setup_tracing(settings: Settings) -> TracerProvider | None:
    """Install a tracer provider that exports to Phoenix. Returns it, or None
    when tracing is off."""
    # Unset or blank: off.
    if not settings.tracing_endpoint:
        return None
    provider = build_provider(
        settings, OTLPSpanExporter(endpoint=settings.tracing_endpoint)
    )
    trace.set_tracer_provider(provider)
    return provider


def build_provider(settings: Settings, exporter: SpanExporter) -> TracerProvider:
    """A provider sending spans to `exporter` in batches, labelled with the
    service and the Phoenix project. Not installed."""
    provider = TracerProvider(
        resource=Resource.create(
            {
                SERVICE_NAME: "drivetruagent",
                # Phoenix files the traces under this project.
                ResourceAttributes.PROJECT_NAME: settings.tracing_project,
            }
        )
    )
    provider.add_span_processor(BatchSpanProcessor(exporter))
    return provider


def untraced(scope: MutableMapping[str, Any]) -> bool:
    """Whether FastAPI leaves a request out of the traces: health checks and
    other reads. Docker checks health every few seconds and the screens poll
    for costs, summaries and calls, so traced reads bury the customer turns.
    Requests that change something, and voice connections, stay in."""
    if scope["type"] != "http":
        return False
    return scope["path"].startswith("/health/") or scope["method"] in {
        "GET",
        "HEAD",
        "OPTIONS",
    }


APP_TELEMETRY: TelemetryConfig = {"exclude": untraced}
