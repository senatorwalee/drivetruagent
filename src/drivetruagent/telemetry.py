"""Sending traces to Arize Phoenix, when asked to.

With TRACING_ENDPOINT unset (the default) nothing is set up and every span in
agent/tracing.py is a no-op. With it set, spans go out over OTLP HTTP in
batches, off the request path.

    make phoenix                                   # Phoenix on localhost:6006
    TRACING_ENDPOINT=http://localhost:6006/v1/traces
"""

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
    if settings.tracing_endpoint is None:
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
