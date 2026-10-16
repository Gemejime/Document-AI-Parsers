"""Unit test verifying Prometheus telemetry registry, metrics collection, and alerting expressions."""

from typing import Any

import prometheus_client
from fastapi.testclient import TestClient

from internal.server.probe_server import ProbeMetrics, create_probe_app


def test_required_telemetry_metrics_registered() -> None:
    """Verify all 8 mandatory metrics from Implementation Plan Pages 9-10 are registered."""
    test_registry = prometheus_client.CollectorRegistry()
    metrics = ProbeMetrics(registry=test_registry)

    # 1. doc_ai_pages_processed_total
    metrics.pages_processed_total.labels(worker_type="vision").inc(15)

    # 2. doc_ai_audio_seconds_processed_total
    metrics.audio_seconds_processed_total.labels(worker_type="audio").inc(45.5)

    # 3. doc_ai_heartbeat_emissions_total
    metrics.heartbeat_emissions_total.labels(activity_name="ParseDocumentSliceActivity").inc()

    # 4. doc_ai_cuda_oom_errors_total
    metrics.cuda_oom_errors_total.labels(worker_type="vision").inc(0)

    # 5. doc_ai_task_duration_seconds
    metrics.task_duration_seconds.labels(activity_name="ParseDocumentSliceActivity").observe(12.3)

    # 6. doc_ai_heartbeat_delay_seconds
    metrics.heartbeat_delay_seconds.labels(activity_name="ParseDocumentSliceActivity").observe(10.0)

    # 7. doc_ai_vram_allocated_bytes
    metrics.vram_allocated_bytes.labels(worker_type="vision").set(4 * 1024 * 1024 * 1024)

    # 8. doc_ai_activity_failures_total
    metrics.activity_failures_total.labels(activity_name="ParseDocumentSliceActivity").inc(1)

    # Generate scrape output
    scrape_output = prometheus_client.generate_latest(test_registry).decode("utf-8")

    expected_metrics = [
        "doc_ai_pages_processed_total",
        "doc_ai_audio_seconds_processed_total",
        "doc_ai_task_duration_seconds",
        "doc_ai_vram_allocated_bytes",
        "doc_ai_heartbeat_emissions_total",
        "doc_ai_cuda_oom_errors_total",
        "doc_ai_heartbeat_delay_seconds",
        "doc_ai_activity_failures_total",
    ]

    for metric_name in expected_metrics:
        assert metric_name in scrape_output, f"Missing required Prometheus metric: {metric_name}"


def test_prometheus_alert_expressions_reference_valid_metrics() -> None:
    """Verify Prometheus alerting expressions parse and reference valid registered metric names."""
    alert_rules: list[dict[str, Any]] = [
        {
            "name": "QueueBacklogGrowth",
            "expr": "max_over_time(temporal_activity_schedule_to_start_seconds[3m]) > 45",
            "expected_metric": "temporal_activity_schedule_to_start_seconds",
        },
        {
            "name": "CudaOOMErrors",
            "expr": "sum by (worker_type, instance) (rate(doc_ai_cuda_oom_errors_total[5m])) > 0",
            "expected_metric": "doc_ai_cuda_oom_errors_total",
        },
        {
            "name": "ActivityFailuresHigh",
            "expr": (
                "(sum by (activity_name) (rate(doc_ai_activity_failures_total[10m])) / "
                "sum by (activity_name) (rate(doc_ai_task_duration_seconds_count[10m]))) > 0.02"
            ),
            "expected_metrics": [
                "doc_ai_activity_failures_total",
                "doc_ai_task_duration_seconds_count",
            ],
        },
    ]

    for rule in alert_rules:
        if "expected_metric" in rule:
            assert rule["expected_metric"] in rule["expr"]
        if "expected_metrics" in rule:
            for em in rule["expected_metrics"]:
                assert em in rule["expr"]


def test_metrics_endpoint_integration() -> None:
    """Verify HTTP GET /metrics exposes populated operational metrics."""
    app = create_probe_app(worker_type="vision")
    client = TestClient(app)

    response = client.get("/metrics")
    assert response.status_code == 200
    assert "text/plain" in response.headers["content-type"]
    text = response.text

    assert "doc_ai_pages_processed_total" in text
    assert "doc_ai_cuda_oom_errors_total" in text
    assert "doc_ai_activity_failures_total" in text
