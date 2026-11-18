"""
OpenTelemetry setup — W3C TraceContext propagation across all spans.
Exports to OTLP gRPC collector if reachable; falls back to no-op silently.
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Generator

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource, SERVICE_NAME
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
from opentelemetry.trace import Span, Status, StatusCode

logger = logging.getLogger(__name__)

_tracer: trace.Tracer | None = None


def setup_otel(service_name: str, otlp_endpoint: str) -> None:
    global _tracer

    resource = Resource(attributes={SERVICE_NAME: service_name})
    provider = TracerProvider(resource=resource)

    try:
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        exporter = OTLPSpanExporter(endpoint=otlp_endpoint, insecure=True)
        provider.add_span_processor(BatchSpanProcessor(exporter))
        logger.info("OTel OTLP exporter configured → %s", otlp_endpoint)
    except Exception:
        # Collector not running — use console in dev, silent in prod
        provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
        logger.debug("OTel: OTLP unavailable, using console exporter")

    trace.set_tracer_provider(provider)
    _tracer = trace.get_tracer(service_name)


def get_tracer() -> trace.Tracer:
    if _tracer is None:
        return trace.get_tracer("unreliable-assistant")
    return _tracer


@contextmanager
def span(name: str, attributes: dict | None = None) -> Generator[Span, None, None]:
    tracer = get_tracer()
    with tracer.start_as_current_span(name) as s:
        if attributes:
            for k, v in attributes.items():
                s.set_attribute(k, str(v))
        try:
            yield s
        except Exception as exc:
            s.set_status(Status(StatusCode.ERROR, str(exc)))
            raise


def record_latency(span_ref: Span, latency_ms: float) -> None:
    span_ref.set_attribute("latency_ms", latency_ms)


def record_kpi(span_ref: Span, kpi: str, value: float) -> None:
    span_ref.set_attribute(f"kpi.{kpi}", value)
