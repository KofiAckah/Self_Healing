"""Statistical root-cause analyzer for the TechStream self-healing lab (Phase 4).

Standard-library only. Queries the Prometheus HTTP API for a recent window of
each Golden Signal, flags anomalies with two independent methods (Z-score and
IQR), and orders the flagged signals by the time they first went anomalous to
build a simple causal chain (e.g. "CPU spike -> latency rise -> error spike").

This is the primary, no-external-dependency Phase 4 deliverable. ``analyze.py``
is the optional Claude-API bonus that turns the same data into prose.

Usage
-----
    python root_cause_analyzer.py --prometheus http://localhost:9090
    python root_cause_analyzer.py --watch --interval 60
    python root_cause_analyzer.py --output report.json
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

# Each Golden Signal expressed as a PromQL query. Keep these aligned with the
# alert rules so the analyzer reasons about the same series the alarms watch.
SIGNALS: dict[str, str] = {
    "error_rate": (
        "sum(rate(techstream_requests_total{status=~\"5..\"}[1m]))"
        " / clamp_min(sum(rate(techstream_requests_total[1m])), 0.001)"
    ),
    "latency_p99": (
        "histogram_quantile(0.99,"
        " sum(rate(techstream_request_duration_seconds_bucket[5m])) by (le))"
    ),
    "request_rate": "sum(rate(techstream_requests_total[1m]))",
    "cpu_util": "100 - (avg(rate(node_cpu_seconds_total{mode=\"idle\"}[1m])) * 100)",
    "mem_util": (
        "(1 - (node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes)) * 100"
    ),
}

# A signal needs at least this many samples before we trust its statistics.
MIN_SAMPLES = 10
ZSCORE_THRESHOLD = 2.0


def query_range(
    prometheus: str, query: str, minutes: int, step: int
) -> list[tuple[float, float]]:
    """Run a Prometheus range query, returning [(timestamp, value), ...]."""
    end = time.time()
    start = end - minutes * 60
    params = urllib.parse.urlencode(
        {"query": query, "start": start, "end": end, "step": step}
    )
    url = f"{prometheus.rstrip('/')}/api/v1/query_range?{params}"
    with urllib.request.urlopen(url, timeout=15) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    if body.get("status") != "success":
        raise RuntimeError(f"prometheus query failed: {body}")
    result = body["data"]["result"]
    if not result:
        return []
    # Single aggregated series expected; take the first.
    samples = []
    for ts, value in result[0]["values"]:
        try:
            samples.append((float(ts), float(value)))
        except ValueError:
            # Prometheus returns "NaN" as a string for undefined points.
            continue
    return samples


def zscore_anomaly(values: list[float]) -> tuple[bool, float]:
    """Flag the latest value if its Z-score vs the baseline exceeds threshold."""
    if len(values) < MIN_SAMPLES:
        return False, 0.0
    baseline, latest = values[:-1], values[-1]
    mean = statistics.fmean(baseline)
    stdev = statistics.pstdev(baseline)
    # Use an epsilon floor rather than == 0: a "constant" series of values like
    # 0.1 carries floating-point noise (stdev ~1e-17), which would otherwise
    # divide into a meaningless, enormous Z-score.
    if stdev < 1e-9:
        # No meaningful variation in baseline: flag only a clear departure.
        return (abs(latest - mean) > 1e-6), 0.0
    z = (latest - mean) / stdev
    return abs(z) > ZSCORE_THRESHOLD, z


def iqr_anomaly(values: list[float]) -> bool:
    """Flag the latest value if it falls outside the 1.5*IQR fence."""
    if len(values) < MIN_SAMPLES:
        return False
    baseline, latest = values[:-1], values[-1]
    quantiles = statistics.quantiles(baseline, n=4)  # [Q1, Q2, Q3]
    q1, q3 = quantiles[0], quantiles[2]
    iqr = q3 - q1
    if iqr == 0:
        return False
    lower, upper = q1 - 1.5 * iqr, q3 + 1.5 * iqr
    return latest < lower or latest > upper


def first_anomaly_time(
    timestamps: list[float], values: list[float]
) -> float | None:
    """Earliest timestamp where the rolling Z-score crosses the threshold.

    Used to order signals into a causal chain — the signal that went anomalous
    first is the likeliest root cause.
    """
    for i in range(MIN_SAMPLES, len(values)):
        window = values[: i + 1]
        flagged, _ = zscore_anomaly(window)
        if flagged:
            return timestamps[i]
    return None


def analyze(prometheus: str, minutes: int, step: int) -> dict:
    """Analyze all signals and return a structured report."""
    findings = []
    for name, query in SIGNALS.items():
        samples = query_range(prometheus, query, minutes, step)
        if len(samples) < MIN_SAMPLES:
            continue
        timestamps = [ts for ts, _ in samples]
        values = [v for _, v in samples]
        z_flag, z = zscore_anomaly(values)
        iqr_flag = iqr_anomaly(values)
        if z_flag or iqr_flag:
            findings.append(
                {
                    "signal": name,
                    "latest": round(values[-1], 4),
                    "baseline_mean": round(statistics.fmean(values[:-1]), 4),
                    "zscore": round(z, 2),
                    "zscore_anomaly": z_flag,
                    "iqr_anomaly": iqr_flag,
                    "first_anomaly_at": first_anomaly_time(timestamps, values),
                }
            )

    # Causal chain: anomalies ordered by when they first appeared.
    chain = sorted(
        (f for f in findings if f["first_anomaly_at"] is not None),
        key=lambda f: f["first_anomaly_at"],
    )
    causal_chain = [f["signal"] for f in chain]

    return {
        "generated_at": time.time(),
        "window_minutes": minutes,
        "anomalies": findings,
        "causal_chain": causal_chain,
        "likely_root_cause": causal_chain[0] if causal_chain else None,
    }


def print_report(report: dict) -> None:
    print("=" * 60)
    print("TechStream Root-Cause Analysis (Z-score + IQR)")
    print("=" * 60)
    if not report["anomalies"]:
        print("No anomalies detected in the current window.")
        return
    for f in report["anomalies"]:
        flags = []
        if f["zscore_anomaly"]:
            flags.append(f"z={f['zscore']}")
        if f["iqr_anomaly"]:
            flags.append("IQR")
        print(
            f"  [{', '.join(flags)}] {f['signal']}: "
            f"latest={f['latest']} (baseline≈{f['baseline_mean']})"
        )
    if report["causal_chain"]:
        print("\nCausal chain (earliest anomaly first):")
        print("  " + " -> ".join(report["causal_chain"]))
        print(f"\nLikely root cause: {report['likely_root_cause']}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="TechStream RCA (stdlib)")
    parser.add_argument("--prometheus", default="http://localhost:9090")
    parser.add_argument("--minutes", type=int, default=15,
                        help="lookback window in minutes")
    parser.add_argument("--step", type=int, default=15,
                        help="query resolution in seconds")
    parser.add_argument("--watch", action="store_true",
                        help="run continuously")
    parser.add_argument("--interval", type=int, default=60,
                        help="seconds between runs in --watch mode")
    parser.add_argument("--output", help="write the JSON report to this path")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    while True:
        try:
            report = analyze(args.prometheus, args.minutes, args.step)
        except (urllib.error.URLError, RuntimeError) as exc:
            print(f"[rca] error querying Prometheus: {exc}", file=sys.stderr)
            return 1
        print_report(report)
        if args.output:
            with open(args.output, "w", encoding="utf-8") as fh:
                json.dump(report, fh, indent=2)
            print(f"\n[rca] report written to {args.output}")
        if not args.watch:
            return 0
        time.sleep(args.interval)


if __name__ == "__main__":
    sys.exit(main())
