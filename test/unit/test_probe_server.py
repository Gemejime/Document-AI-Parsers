"""Unit tests for FastAPI operational probe server and OpenAPI 3.1 endpoints."""

from fastapi.testclient import TestClient

from internal.server.probe_server import create_probe_app, set_probe_ready


def test_healthz_liveness_probe() -> None:
    """Verify /healthz returns 200 and healthy status."""
    app = create_probe_app(worker_type="vision")
    client = TestClient(app)

    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}


def test_ready_readiness_probe_transitions() -> None:
    """Verify /ready returns 503 before ready and 200 after ready."""
    app = create_probe_app(worker_type="audio")
    client = TestClient(app)

    set_probe_ready(False, worker_type="audio")
    resp_not_ready = client.get("/ready")
    assert resp_not_ready.status_code == 503
    assert resp_not_ready.json() == {"status": "not_ready", "worker_type": "audio"}

    set_probe_ready(True, worker_type="audio")
    resp_ready = client.get("/ready")
    assert resp_ready.status_code == 200
    assert resp_ready.json() == {"status": "ready", "worker_type": "audio"}


def test_openapi_documentation_endpoints() -> None:
    """Verify OpenAPI 3.1 documentation endpoints (/docs, /redoc, /api/docs, /openapi.json)."""
    app = create_probe_app(worker_type="vision")
    client = TestClient(app)

    # Interactive Swagger UI
    resp_docs = client.get("/docs")
    assert resp_docs.status_code == 200
    assert "swagger" in resp_docs.text.lower() or "html" in resp_docs.headers["content-type"]

    # Interactive ReDoc UI
    resp_redoc = client.get("/redoc")
    assert resp_redoc.status_code == 200
    assert "redoc" in resp_redoc.text.lower() or "html" in resp_redoc.headers["content-type"]

    # Machine-readable OpenAPI schema endpoints
    resp_api_docs = client.get("/api/docs")
    assert resp_api_docs.status_code == 200
    schema = resp_api_docs.json()
    assert "openapi" in schema
    assert schema["openapi"].startswith("3.1")
    assert schema["info"]["title"] == "RAG Document AI Worker API"
    assert "/healthz" in schema["paths"]
    assert "/ready" in schema["paths"]

    resp_openapi_json = client.get("/openapi.json")
    assert resp_openapi_json.status_code == 200
    assert resp_openapi_json.json() == schema


def test_prometheus_metrics_endpoint() -> None:
    """Verify /metrics returns standard Prometheus scrape text."""
    app = create_probe_app(worker_type="vision")
    client = TestClient(app)

    response = client.get("/metrics")
    assert response.status_code == 200
    assert "text/plain" in response.headers["content-type"]
    body = response.text
    assert "doc_ai_pages_processed_total" in body
    assert "doc_ai_heartbeat_delay_seconds" in body
