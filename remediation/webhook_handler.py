"""Remediation webhook for the TechStream self-healing lab.

AlertManager POSTs firing alerts here. For a critical ``HighErrorRate`` alert
the handler performs the self-healing action:

  1. Clears the injected chaos on the app (``POST /chaos/reset``).
  2. Restarts the app container.

Security
--------
* The webhook is **bearer-token authenticated** — AlertManager sends
  ``Authorization: Bearer <REMEDIATION_TOKEN>`` and we reject anything else.
  This is not an open "restart the app" endpoint.
* We do **not** mount the raw Docker socket. Instead we talk to a
  docker-socket-proxy that only exposes container restart, so a compromise of
  this service cannot drive arbitrary Docker API calls. ``DOCKER_HOST`` points
  at the proxy (e.g. ``tcp://docker-socket-proxy:2375``).
"""

from __future__ import annotations

import logging
import os

import docker
from flask import Flask, jsonify, request

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
)
log = logging.getLogger("remediation")

app = Flask(__name__)

APP_BASE_URL = os.environ.get("APP_BASE_URL", "http://app:5000")
APP_CONTAINER = os.environ.get("APP_CONTAINER_NAME", "techstream-app")
# The token is injected from the environment (sourced from .env / SSM). It has
# no default on purpose: an unset token disables the webhook rather than
# silently accepting a well-known value.
REMEDIATION_TOKEN = os.environ.get("REMEDIATION_TOKEN", "")
# Only this alert triggers a restart; everything else is acknowledged but
# ignored so a noisy warning rule can't bounce the app.
REMEDIATION_ALERT = os.environ.get("REMEDIATION_ALERT", "HighErrorRate")


def _authorized() -> bool:
    if not REMEDIATION_TOKEN:
        log.error("REMEDIATION_TOKEN is not set; refusing all requests.")
        return False
    header = request.headers.get("Authorization", "")
    expected = f"Bearer {REMEDIATION_TOKEN}"
    # Length-aware comparison; tokens are short and not secret-length critical,
    # but we still avoid leaking match position.
    return header == expected


def _reset_chaos() -> None:
    """Best-effort clear of injected chaos before the restart."""
    import urllib.request

    req = urllib.request.Request(f"{APP_BASE_URL}/chaos/reset", method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            log.info("chaos reset -> HTTP %s", resp.status)
    except OSError as exc:
        log.warning("chaos reset failed: %s", exc)


def _restart_app() -> bool:
    """Restart the app container via the Docker socket proxy."""
    try:
        client = docker.from_env()  # reads DOCKER_HOST (the proxy)
        container = client.containers.get(APP_CONTAINER)
        container.restart(timeout=10)
        log.info("restarted container %s", APP_CONTAINER)
        return True
    except docker.errors.NotFound:
        log.error("container %s not found", APP_CONTAINER)
    except docker.errors.APIError as exc:
        log.error("docker API error restarting %s: %s", APP_CONTAINER, exc)
    return False


@app.route("/health")
def health():
    return jsonify(status="ok")


@app.route("/webhook", methods=["POST"])
def webhook():
    if not _authorized():
        return jsonify(error="unauthorized"), 401

    payload = request.get_json(silent=True) or {}
    alerts = payload.get("alerts", [])
    actioned = []

    for alert in alerts:
        name = alert.get("labels", {}).get("alertname")
        status = alert.get("status")
        log.info("received alert name=%s status=%s", name, status)
        if status == "firing" and name == REMEDIATION_ALERT:
            log.warning("REMEDIATING: %s is firing", name)
            _reset_chaos()
            if _restart_app():
                actioned.append(name)

    return jsonify(status="ok", remediated=actioned)


def create_app() -> Flask:
    return app


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8081"))
    app.run(host="0.0.0.0", port=port)
