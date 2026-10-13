"""FastAPI ASGI Operational Server: OpenAPI 3.1 Docs, Health Probes & Prometheus Metrics."""

from dataclasses import dataclass
from typing import Any

import prometheus_client
from fastapi import FastAPI, Response, status
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram


@dataclass
class ProbeState:
    """Thread-safe mutable readiness state for worker probes."""

    is_ready: bool = False
    worker_type: str = "generic"


probe_state = ProbeState()


def set_probe_ready(ready: bool, worker_type: str = "generic") -> None:
    """Update global readiness state from worker bootstrap loops."""
    probe_state.is_ready = ready
    probe_state.worker_type = worker_type


class ProbeMetrics:
    """Prometheus telemetry registry and collectors for document and audio processing."""

    def __init__(self, registry: prometheus_client.CollectorRegistry | None = None) -> None:
        self.registry = registry or prometheus_client.REGISTRY

        # Counters
        self.pages_processed_total = Counter(
            "doc_ai_pages_processed_total",
            "Total number of document pages processed by vision workers",
            ["worker_type"],
            registry=self.registry,
        )
        self.audio_seconds_processed_total = Counter(
            "doc_ai_audio_seconds_processed_total",
            "Total seconds of audio processed by audio workers",
            ["worker_type"],
            registry=self.registry,
        )
        self.heartbeat_emissions_total = Counter(
            "doc_ai_heartbeat_emissions_total",
            "Total number of Temporal activity heartbeats successfully emitted",
            ["activity_name"],
            registry=self.registry,
        )
        self.cuda_oom_errors_total = Counter(
            "doc_ai_cuda_oom_errors_total",
            "Total count of CUDA Out of Memory errors encountered",
            ["worker_type"],
            registry=self.registry,
        )

        # Histograms
        self.task_duration_seconds = Histogram(
            "doc_ai_task_duration_seconds",
            "Execution duration per slice or window activity in seconds",
            ["activity_name"],
            buckets=(1.0, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0, 600.0),
            registry=self.registry,
        )
        self.heartbeat_delay_seconds = Histogram(
            "doc_ai_heartbeat_delay_seconds",
            "Delay interval observed between background heartbeat emissions",
            ["activity_name"],
            buckets=(1.0, 5.0, 9.0, 10.0, 10.5, 12.0, 15.0, 20.0, 30.0),
            registry=self.registry,
        )

        # Gauge
        self.vram_allocated_bytes = Gauge(
            "doc_ai_vram_allocated_bytes",
            "Current GPU VRAM allocated in bytes",
            ["worker_type"],
            registry=self.registry,
        )


# Singleton metrics instance
_METRICS_INSTANCE: ProbeMetrics | None = None


def get_probe_metrics(registry: prometheus_client.CollectorRegistry | None = None) -> ProbeMetrics:
    """Retrieve or initialize the singleton ProbeMetrics instance."""
    global _METRICS_INSTANCE
    if _METRICS_INSTANCE is None:
        _METRICS_INSTANCE = ProbeMetrics(registry=registry)
    return _METRICS_INSTANCE


def create_probe_app(worker_type: str = "generic") -> FastAPI:
    """Create and configure the FastAPI application for operational probes."""
    probe_state.worker_type = worker_type
    metrics = get_probe_metrics()

    app = FastAPI(
        title="RAG Document AI Worker API",
        description="Internal HTTP Documentation, Health, and Operational API for rag-document-ai-worker",
        version="0.1.0",
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )

    @app.get(
        "/healthz",
        summary="Liveness Probe",
        response_model=dict[str, str],
        tags=["Probes"],
    )
    async def healthz() -> dict[str, str]:
        """Return healthy if the HTTP server event loop is operational."""
        return {"status": "healthy"}

    @app.get(
        "/ready",
        summary="Readiness Probe",
        response_model=dict[str, str],
        tags=["Probes"],
    )
    async def ready() -> JSONResponse:
        """Verify Temporal queue connectivity and worker initialization."""
        if probe_state.is_ready:
            return JSONResponse(
                status_code=status.HTTP_200_OK,
                content={
                    "status": "ready",
                    "worker_type": probe_state.worker_type,
                },
            )
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={
                "status": "not_ready",
                "worker_type": probe_state.worker_type,
            },
        )

    @app.get(
        "/api/docs",
        summary="OpenAPI Documentation Schema",
        tags=["Documentation"],
    )
    async def api_docs() -> dict[str, Any]:
        """Return raw machine-readable OpenAPI 3.1 JSON specification."""
        return app.openapi()

    @app.get(
        "/metrics",
        summary="Prometheus Metrics",
        tags=["Observability"],
    )
    async def prometheus_metrics() -> Response:
        """Prometheus scrape endpoint."""
        body = prometheus_client.generate_latest(metrics.registry)
        return Response(content=body, media_type=CONTENT_TYPE_LATEST)

    return app
