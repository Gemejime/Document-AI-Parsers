"""Unit test verifying Prometheus telemetry registry, metrics collection, and alerting expressions."""

import re
from pathlib import Path

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
    """Verify Prometheus alerting expressions in templates/prometheusrule.yaml reference registered metrics."""
    rule_file = (
        Path(__file__).resolve().parents[2]
        / "deployments"
        / "helm"
        / "templates"
        / "prometheusrule.yaml"
    )
    assert rule_file.is_file(), f"PrometheusRule template not found at {rule_file}"

    content = rule_file.read_text(encoding="utf-8")
    rule_matches = re.findall(
        r"-\s*alert:\s*([A-Za-z0-9_-]+)\s+expr:\s*(.*?)(?=\n\s*(?:for|labels|annotations):)",
        content,
        re.DOTALL,
    )
    alert_rules = {name.strip(): " ".join(expr.split()) for name, expr in rule_matches}

    expected_validations: dict[str, list[str]] = {
        "QueueBacklogGrowth": ["temporal_activity_schedule_to_start_seconds"],
        "CudaOOMErrors": ["doc_ai_cuda_oom_errors_total"],
        "ActivityFailuresHigh": [
            "doc_ai_activity_failures_total",
            "doc_ai_task_duration_seconds_count",
        ],
    }

    assert set(expected_validations.keys()).issubset(alert_rules.keys()), (
        f"Missing alert rules in template: {set(expected_validations.keys()) - set(alert_rules.keys())}"
    )

    for alert_name, expected_metrics in expected_validations.items():
        expr = alert_rules[alert_name]
        for metric in expected_metrics:
            assert metric in expr, (
                f"Alert '{alert_name}' expression '{expr}' does not reference metric '{metric}'"
            )


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
