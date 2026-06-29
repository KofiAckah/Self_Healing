"""Unit tests for the remediation webhook (Phase 3)."""

from __future__ import annotations

import pytest

import webhook_handler as wh

TOKEN = "test-secret-token"


@pytest.fixture
def client(monkeypatch):
    # Pin a known token and stub out the side effects (chaos reset + restart)
    # so the tests never touch Docker or the network.
    monkeypatch.setattr(wh, "REMEDIATION_TOKEN", TOKEN)
    monkeypatch.setattr(wh, "_reset_chaos", lambda: None)
    flask_app = wh.create_app()
    flask_app.config.update(TESTING=True)
    with flask_app.test_client() as c:
        yield c


def _auth():
    return {"Authorization": f"Bearer {TOKEN}"}


def test_health_ok(client):
    assert client.get("/health").status_code == 200


def test_webhook_rejects_missing_token(client):
    resp = client.post("/webhook", json={"alerts": []})
    assert resp.status_code == 401


def test_webhook_rejects_wrong_token(client):
    resp = client.post(
        "/webhook", json={"alerts": []},
        headers={"Authorization": "Bearer wrong"},
    )
    assert resp.status_code == 401


def test_firing_high_error_rate_triggers_restart(client, monkeypatch):
    restarts = []
    monkeypatch.setattr(wh, "_restart_app", lambda: restarts.append(True) or True)
    payload = {
        "alerts": [
            {"status": "firing", "labels": {"alertname": "HighErrorRate"}}
        ]
    }
    resp = client.post("/webhook", json=payload, headers=_auth())
    assert resp.status_code == 200
    assert resp.get_json()["remediated"] == ["HighErrorRate"]
    assert restarts == [True]


def test_non_remediation_alert_is_ignored(client, monkeypatch):
    restarts = []
    monkeypatch.setattr(wh, "_restart_app", lambda: restarts.append(True) or True)
    payload = {
        "alerts": [
            {"status": "firing", "labels": {"alertname": "HighMemory"}}
        ]
    }
    resp = client.post("/webhook", json=payload, headers=_auth())
    assert resp.status_code == 200
    assert resp.get_json()["remediated"] == []
    assert restarts == []


def test_resolved_alert_does_not_restart(client, monkeypatch):
    restarts = []
    monkeypatch.setattr(wh, "_restart_app", lambda: restarts.append(True) or True)
    payload = {
        "alerts": [
            {"status": "resolved", "labels": {"alertname": "HighErrorRate"}}
        ]
    }
    resp = client.post("/webhook", json=payload, headers=_auth())
    assert resp.get_json()["remediated"] == []
    assert restarts == []


def test_unset_token_denies_all(client, monkeypatch):
    monkeypatch.setattr(wh, "REMEDIATION_TOKEN", "")
    resp = client.post("/webhook", json={"alerts": []}, headers=_auth())
    assert resp.status_code == 401
