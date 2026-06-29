"""End-to-end self-healing integration test.

Skipped unless RUN_E2E=1 and the full Docker Compose stack is running. It drives
the real pipeline: inject 500s -> Prometheus scrapes -> HighErrorRate fires ->
AlertManager -> remediation restarts the app -> error rate recovers.

    RUN_E2E=1 pytest tests/test_integration_e2e.py -v
"""

from __future__ import annotations

import os
import time
import urllib.error
import urllib.request

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_E2E") != "1",
    reason="set RUN_E2E=1 with the stack running to exercise the live pipeline",
)

APP = os.environ.get("APP_URL", "http://localhost:5000")
PROM = os.environ.get("PROM_URL", "http://localhost:9090")


def _get_status(url: str) -> int:
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            return resp.status
    except urllib.error.HTTPError as exc:
        return exc.code


def _post(url: str, payload: dict) -> int:
    import json

    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        return resp.status


def test_stack_is_up():
    assert _get_status(f"{APP}/health") == 200


def test_self_healing_round_trip():
    # 1. Baseline healthy.
    assert _get_status(f"{APP}/api/data") == 200

    # 2. Inject errors + drive traffic so the error rate climbs.
    _post(f"{APP}/chaos", {"mode": "errors", "value": 1.0})
    for _ in range(40):
        _get_status(f"{APP}/api/data")

    # 3. Wait for the alert (1m 'for') + remediation to restart the container,
    #    which clears chaos. Poll until /api/data is healthy again.
    deadline = time.time() + 180
    healed = False
    while time.time() < deadline:
        if _get_status(f"{APP}/api/data") == 200:
            healed = True
            break
        time.sleep(5)

    assert healed, "app did not self-heal within the timeout"
