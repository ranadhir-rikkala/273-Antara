from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from temporalio.contrib.opentelemetry import TracingInterceptor
from temporalio.runtime import PrometheusConfig, Runtime, TelemetryConfig

from common.config import settings

_configured = False
_runtime: Runtime | None = None


def setup_tracing() -> None:
    global _configured
    if _configured or not settings.otel_endpoint:
        return
    provider = TracerProvider(resource=Resource.create({"service.name": settings.service_name}))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=settings.otel_endpoint, insecure=True)))
    trace.set_tracer_provider(provider)
    HTTPXClientInstrumentor().instrument()
    _configured = True


def temporal_interceptors() -> list[TracingInterceptor]:
    return [TracingInterceptor()] if settings.otel_endpoint else []


def temporal_runtime() -> Runtime | None:
    global _runtime
    if _runtime is None and settings.temporal_metrics_port:
        _runtime = Runtime(
            telemetry=TelemetryConfig(
                metrics=PrometheusConfig(bind_address=f"0.0.0.0:{settings.temporal_metrics_port}")
            )
        )
    return _runtime
