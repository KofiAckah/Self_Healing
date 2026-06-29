"""TechStream API — a deliberately buggy web server for the self-healing lab.

This Flask application is the monitored workload. It exposes the four Golden
Signals through Prometheus metrics and provides a chaos-injection surface so a
chaos script can make it misbehave on demand:

  * Traffic    -> ``techstream_requests_total`` (counter)
  * Errors     -> the ``status`` label on the request counter (5xx ratio)
  * Latency    -> ``techstream_request_duration_seconds`` (histogram)
  * Saturation -> CPU / memory, scraped from node-exporter & cAdvisor, but the
                  ``/chaos`` endpoint can also burn CPU inside this process.

Chaos state is held in a single in-process dict, so the app must run with a
single worker process (see the Dockerfile / gunicorn config). The remediation
service clears that state via ``/chaos/reset`` before restarting the container.
"""

from __future__ import annotations

import os
import random
import threading
import time

from flask import Flask, Response, jsonify, request
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

app = Flask(__name__)

# ---------------------------------------------------------------------------
# Prometheus metrics (the Golden Signals emitted by the app itself)
# ---------------------------------------------------------------------------
REQUEST_COUNT = Counter(
    "techstream_requests_total",
    "Total HTTP requests processed, labelled by method, endpoint and status.",
    ["method", "endpoint", "status"],
)
REQUEST_LATENCY = Histogram(
    "techstream_request_duration_seconds",
    "HTTP request latency in seconds, labelled by endpoint.",
    ["endpoint"],
    # Buckets chosen so the P99 > 1s alert rule has resolution around 1s.
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)
CHAOS_ACTIVE = Gauge(
    "techstream_chaos_active",
    "1 when a chaos scenario is currently injected, 0 otherwise.",
    ["mode"],
)

# ---------------------------------------------------------------------------
# In-process chaos state. Mutated by /chaos, cleared by /chaos/reset.
# ---------------------------------------------------------------------------
_chaos_lock = threading.Lock()
_chaos_state: dict[str, float] = {
    "errors": 0.0,   # probability [0,1] that /api/data returns HTTP 500
    "latency": 0.0,  # seconds of artificial delay added to /api/data
    "cpu": 0.0,      # number of background CPU-burn threads to keep running
}
_cpu_threads: list[threading.Thread] = []
_cpu_stop = threading.Event()

VALID_MODES = ("errors", "latency", "cpu")


def _cpu_burn() -> None:
    """Spin the CPU until asked to stop — used by the ``cpu`` chaos mode."""
    while not _cpu_stop.is_set():
        # A tight arithmetic loop; the sleep keeps it from being a hard 100%
        # pin so saturation climbs gradually and the alarm has time to fire.
        for _ in range(100_000):
            _ = 7919 * 7919
        time.sleep(0.001)


def _sync_cpu_threads(target: int) -> None:
    """Start or stop CPU-burn threads to match ``target`` count."""
    global _cpu_threads
    if target > 0:
        _cpu_stop.clear()
        while len(_cpu_threads) < target:
            thread = threading.Thread(target=_cpu_burn, daemon=True)
            thread.start()
            _cpu_threads.append(thread)
    else:
        _cpu_stop.set()
        _cpu_threads = []


@app.before_request
def _start_timer() -> None:
    request.environ["_start_time"] = time.perf_counter()


@app.after_request
def _record_metrics(response: Response) -> Response:
    start = request.environ.get("_start_time")
    # The Flask rule (e.g. "/api/data") keeps label cardinality bounded; the
    # raw path would explode it.
    endpoint = request.url_rule.rule if request.url_rule else "unknown"
    if start is not None:
        REQUEST_LATENCY.labels(endpoint=endpoint).observe(time.perf_counter() - start)
    REQUEST_COUNT.labels(
        method=request.method, endpoint=endpoint, status=response.status_code
    ).inc()
    return response


@app.route("/")
def index() -> Response:
    return jsonify(
        service="techstream-api",
        message="TechStream API. See /api/data, /health, /metrics.",
    )


@app.route("/health")
def health() -> Response:
    """Liveness probe. Always healthy unless the process is down."""
    return jsonify(status="ok")


@app.route("/api/data")
def api_data() -> Response:
    """Primary business endpoint. Honours injected chaos."""
    with _chaos_lock:
        error_prob = _chaos_state["errors"]
        latency = _chaos_state["latency"]

    if latency > 0:
        time.sleep(latency)

    if error_prob > 0 and random.random() < error_prob:
        return jsonify(error="internal server error (chaos injected)"), 500

    return jsonify(
        data=[{"id": i, "value": random.randint(1, 100)} for i in range(5)],
        served_at=time.time(),
    )


@app.route("/chaos", methods=["POST"])
def chaos() -> Response:
    """Inject a fault. Body: ``{"mode": "errors"|"latency"|"cpu", "value": n}``.

    * errors  -> value is an error probability in [0, 1]
    * latency -> value is added delay in seconds
    * cpu     -> value is the number of CPU-burn threads to run
    """
    payload = request.get_json(silent=True) or {}
    mode = payload.get("mode")
    value = payload.get("value", 1.0)

    if mode not in VALID_MODES:
        return jsonify(error=f"mode must be one of {VALID_MODES}"), 400

    try:
        value = float(value)
    except (TypeError, ValueError):
        return jsonify(error="value must be a number"), 400

    with _chaos_lock:
        if mode == "errors":
            _chaos_state["errors"] = max(0.0, min(1.0, value))
        elif mode == "latency":
            _chaos_state["latency"] = max(0.0, value)
        elif mode == "cpu":
            _chaos_state["cpu"] = max(0.0, value)
            _sync_cpu_threads(int(_chaos_state["cpu"]))
        CHAOS_ACTIVE.labels(mode=mode).set(1 if _chaos_state[mode] > 0 else 0)

    return jsonify(status="chaos injected", mode=mode, value=value)


@app.route("/chaos/reset", methods=["POST"])
def chaos_reset() -> Response:
    """Clear all injected chaos. Called by the remediation service."""
    with _chaos_lock:
        for mode in VALID_MODES:
            _chaos_state[mode] = 0.0
            CHAOS_ACTIVE.labels(mode=mode).set(0)
        _sync_cpu_threads(0)
    return jsonify(status="chaos cleared")


@app.route("/metrics")
def metrics() -> Response:
    return Response(generate_latest(), mimetype=CONTENT_TYPE_LATEST)


def create_app() -> Flask:
    """Factory used by the tests."""
    return app


if __name__ == "__main__":
    # Local dev only — production runs under gunicorn (see Dockerfile).
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port)
