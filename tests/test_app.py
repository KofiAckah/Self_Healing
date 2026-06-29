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


def test_index_serves_control_panel(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert resp.mimetype == "text/html"
    body = resp.get_data(as_text=True)
    assert "TechStream" in body
    assert "live monitor" in body
    assert "Inject 500 errors" in body


def test_overview_aggregates_prometheus(client, monkeypatch):
    # Stub the server-side Prometheus calls so the test never hits the network.
    monkeypatch.setattr(app_module, "_prom_range",
                        lambda q, minutes=15, step=30: [[1.0, 2.0], [2.0, 4.0]])
    monkeypatch.setattr(
        app_module, "_prom_get",
        lambda path: {
            "activeTargets": [{"labels": {"job": "techstream-app"}, "health": "up"}],
            "alerts": [{"labels": {"alertname": "HighErrorRate", "severity": "critical"},
                        "state": "firing"}],
        },
    )
    data = client.get("/api/overview").get_json()
    assert data["prometheus_ok"] is True
    # latest value is the last point of the stubbed series
    assert data["metrics"]["traffic"]["value"] == 4.0
    assert data["targets"][0]["job"] == "techstream-app"
    assert data["alerts"][0]["name"] == "HighErrorRate"
    assert "chaos" in data


def test_overview_survives_prometheus_down(client, monkeypatch):
    def boom(*a, **k):
        raise OSError("prometheus unreachable")

    monkeypatch.setattr(app_module, "_prom_range", boom)
    monkeypatch.setattr(app_module, "_prom_get", boom)
    resp = client.get("/api/overview")
    assert resp.status_code == 200  # degrades gracefully, never 500s
    data = resp.get_json()
    assert data["prometheus_ok"] is False
    assert data["metrics"]["traffic"]["value"] is None


def test_chaos_status_reflects_state(client):
    # Healthy by default.
    resp = client.get("/chaos/status")
    assert resp.status_code == 200
    assert resp.get_json()["active"] is False

    # After injecting, status reports active with the right value.
    client.post("/chaos", json={"mode": "latency", "value": 1.5})
    data = client.get("/chaos/status").get_json()
    assert data["active"] is True
    assert data["state"]["latency"] == 1.5
    client.post("/chaos/reset")


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
