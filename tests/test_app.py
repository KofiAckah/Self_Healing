"""Unit tests for the TechStream Flask app (Phase 1 + Phase 2 surface)."""

from __future__ import annotations

import pytest

import app as app_module


@pytest.fixture
def client():
    flask_app = app_module.create_app()
    flask_app.config.update(TESTING=True)
    with flask_app.test_client() as c:
        yield c
    # Always clear chaos so tests don't leak state into each other.
    with flask_app.test_client() as c:
        c.post("/chaos/reset")


def test_health_ok(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "ok"


def test_api_data_returns_payload(client):
    resp = client.get("/api/data")
    assert resp.status_code == 200
    assert "data" in resp.get_json()


def test_metrics_endpoint_exposes_prometheus(client):
    # Generate at least one request so the counter has a sample.
    client.get("/api/data")
    resp = client.get("/metrics")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "techstream_requests_total" in body
    assert "techstream_request_duration_seconds" in body


def test_chaos_errors_forces_500(client):
    # Probability 1.0 means every request must fail.
    resp = client.post("/chaos", json={"mode": "errors", "value": 1.0})
    assert resp.status_code == 200
    assert client.get("/api/data").status_code == 500


def test_chaos_reset_restores_health(client):
    client.post("/chaos", json={"mode": "errors", "value": 1.0})
    assert client.get("/api/data").status_code == 500
    client.post("/chaos/reset")
    assert client.get("/api/data").status_code == 200


def test_chaos_latency_adds_delay(client):
    import time

    client.post("/chaos", json={"mode": "latency", "value": 0.3})
    start = time.perf_counter()
    client.get("/api/data")
    elapsed = time.perf_counter() - start
    assert elapsed >= 0.3
    client.post("/chaos/reset")


def test_chaos_rejects_unknown_mode(client):
    resp = client.post("/chaos", json={"mode": "explode", "value": 1})
    assert resp.status_code == 400


def test_chaos_rejects_non_numeric_value(client):
    resp = client.post("/chaos", json={"mode": "errors", "value": "lots"})
    assert resp.status_code == 400
