"""Chaos script for the TechStream self-healing lab.

Drives the TechStream API into an unhealthy state so the monitoring +
remediation pipeline can be observed end to end. It does two things:

  1. Toggles server-side fault injection via the app's ``/chaos`` endpoint.
  2. Generates concurrent traffic against ``/api/data`` so the injected fault
     actually shows up in the Prometheus metrics (an idle server has no error
     rate to alarm on).

Scenarios
---------
  errors   inject HTTP 500s (drives the HighErrorRate alert + remediation)
  latency  inject artificial request latency (drives the HighLatency alert)
  cpu      burn CPU inside the app process (drives the saturation alerts)
  memory   allocate memory client-side pressure via load (saturation)
  load     pure traffic flood, no injected fault
  full     errors + latency + cpu together

Usage
-----
    python chaos_script.py --scenario errors --duration 120 --workers 20
    python chaos_script.py --scenario cpu --cpu-threads 4 --duration 180

By design the ``errors`` and ``full`` scenarios do NOT reset chaos when they
finish — the remediation service is expected to clear it. Every other scenario
resets on exit so repeated manual runs start clean.
"""

from __future__ import annotations

import argparse
import sys
import threading
import time
import urllib.error
import urllib.request

# Scenarios whose injected fault is deliberately left for the remediation
# service to clear, rather than cleaned up by this script.
SELF_HEALING_SCENARIOS = frozenset({"errors", "full"})


def post_json(url: str, payload: dict, timeout: float = 5.0) -> int:
    """POST a JSON body and return the HTTP status code (0 on failure)."""
    import json

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status
    except urllib.error.HTTPError as exc:
        return exc.code
    except OSError:
        return 0


def get(url: str, timeout: float = 5.0) -> int:
    """GET a URL and return the HTTP status code (0 on connection failure)."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.status
    except urllib.error.HTTPError as exc:
        return exc.code
    except OSError:
        return 0


def inject(target: str, scenario: str, cpu_threads: int, latency: float) -> None:
    """Apply the server-side fault(s) for ``scenario``."""
    chaos_url = f"{target}/chaos"
    if scenario in ("errors", "full"):
        post_json(chaos_url, {"mode": "errors", "value": 1.0})
    if scenario in ("latency", "full"):
        post_json(chaos_url, {"mode": "latency", "value": latency})
    if scenario in ("cpu", "full"):
        post_json(chaos_url, {"mode": "cpu", "value": cpu_threads})


def reset(target: str) -> None:
    post_json(f"{target}/chaos/reset", {})


def traffic_worker(target: str, stop: threading.Event, counter: dict) -> None:
    """Hammer /api/data until ``stop`` is set, tallying status codes."""
    url = f"{target}/api/data"
    while not stop.is_set():
        status = get(url)
        bucket = "2xx" if 200 <= status < 300 else "5xx" if status >= 500 else "err"
        counter[bucket] = counter.get(bucket, 0) + 1


def run(args: argparse.Namespace) -> int:
    target = args.target.rstrip("/")
    print(f"[chaos] scenario={args.scenario} target={target} "
          f"duration={args.duration}s workers={args.workers}")

    if args.scenario not in SCENARIOS:
        print(f"[chaos] unknown scenario {args.scenario!r}", file=sys.stderr)
        return 2

    inject(target, args.scenario, args.cpu_threads, args.latency)

    stop = threading.Event()
    counter: dict[str, int] = {}
    threads = [
        threading.Thread(target=traffic_worker, args=(target, stop, counter), daemon=True)
        for _ in range(args.workers)
    ]
    for thread in threads:
        thread.start()

    try:
        time.sleep(args.duration)
    except KeyboardInterrupt:
        print("\n[chaos] interrupted")
    finally:
        stop.set()
        for thread in threads:
            thread.join(timeout=2)

    total = sum(counter.values())
    print(f"[chaos] sent {total} requests: {counter}")

    if args.scenario in SELF_HEALING_SCENARIOS and not args.force_reset:
        print("[chaos] leaving fault injected — remediation service should heal it.")
    else:
        reset(target)
        print("[chaos] chaos reset.")
    return 0


SCENARIOS = ("errors", "latency", "cpu", "memory", "load", "full")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="TechStream chaos injector")
    parser.add_argument(
        "--scenario", required=True, choices=SCENARIOS,
        help="which fault to inject",
    )
    parser.add_argument(
        "--target", default="http://localhost:5000",
        help="base URL of the TechStream API",
    )
    parser.add_argument(
        "--duration", type=int, default=120,
        help="how long to sustain the scenario, in seconds",
    )
    parser.add_argument(
        "--workers", type=int, default=20,
        help="number of concurrent traffic-generating threads",
    )
    parser.add_argument(
        "--cpu-threads", type=int, default=4,
        help="CPU-burn threads for the cpu/full scenarios",
    )
    parser.add_argument(
        "--latency", type=float, default=2.0,
        help="injected latency in seconds for the latency/full scenarios",
    )
    parser.add_argument(
        "--force-reset", action="store_true",
        help="reset chaos on exit even for self-healing scenarios",
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    sys.exit(run(parse_args()))
