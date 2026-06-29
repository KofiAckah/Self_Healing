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

import json
import os
import random
import statistics
import threading
import time
import urllib.parse
import urllib.request

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
# App start time (for uptime)
# ---------------------------------------------------------------------------
_app_start_time = time.time()

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

# ---------------------------------------------------------------------------
# Event log (in-memory, max 100 entries)
# ---------------------------------------------------------------------------
_events: list[dict] = []
_events_lock = threading.Lock()

# Track previous alert states so we can record changes
_prev_alert_states: dict[str, str] = {}

def _add_event(event_type: str, description: str) -> None:
    """Append an event to the in-memory timeline (capped at 100)."""
    with _events_lock:
        _events.append({
            "type": event_type,
            "description": description,
            "timestamp": time.time(),
        })
        if len(_events) > 100:
            del _events[:len(_events) - 100]

# Prometheus base URL the dashboard proxy queries (reachable over the compose
# network). Querying server-side avoids browser CORS and keeps the dashboard
# working even when only the app port is exposed.
PROM_URL = os.environ.get("PROM_URL", "http://prometheus:9090")

# The Golden Signals as PromQL, kept in step with the alert rules. Each entry:
# (key, query, unit, warn-threshold, higher-is-worse).
DASHBOARD_SIGNALS = [
    ("traffic", "sum(rate(techstream_requests_total[1m]))", "req/s", None, False),
    ("error_rate",
     "sum(rate(techstream_requests_total{status=~\"5..\"}[1m]))"
     " / clamp_min(sum(rate(techstream_requests_total[1m])), 0.001)",
     "ratio", 0.05, True),
    ("latency_p99",
     "histogram_quantile(0.99,"
     " sum(rate(techstream_request_duration_seconds_bucket[5m])) by (le))",
     "s", 1.0, True),
    ("cpu",
     "100 - (avg(rate(node_cpu_seconds_total{mode=\"idle\"}[1m])) * 100)",
     "%", 80.0, True),
    ("memory",
     "(1 - (node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes)) * 100",
     "%", 85.0, True),
]

# ---------------------------------------------------------------------------
# RCA constants (reimplemented from root_cause_analyzer.py, stdlib only)
# ---------------------------------------------------------------------------
_RCA_SIGNALS: dict[str, str] = {
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
_RCA_MIN_SAMPLES = 10
_RCA_ZSCORE_THRESHOLD = 2.0


def _compute_health_score(metrics: dict) -> int:
    """Compute a 0-100 health score from the metrics dict.

    Each of the 5 signals contributes 20 points:
      - breached (above threshold) -> 0
      - within 50% of threshold    -> 10
      - comfortably below          -> 20
      - no data                    -> 15
    """
    score = 0
    for key, _query, _unit, warn, higher_bad in DASHBOARD_SIGNALS:
        m = metrics.get(key, {})
        value = m.get("value")
        if value is None:
            score += 20
            continue
        if warn is None:
            # Traffic has no threshold, so full score if we have data
            score += 20
            continue
        if m.get("breached"):
            score += 0
        elif higher_bad and value > warn * 0.5:
            score += 10
        else:
            score += 20
    return score


# ---------------------------------------------------------------------------
# RCA helper functions (reimplemented inline, stdlib only)
# ---------------------------------------------------------------------------
def _rca_zscore_anomaly(values: list) -> tuple:
    """Flag the latest value if its Z-score vs the baseline exceeds threshold."""
    if len(values) < _RCA_MIN_SAMPLES:
        return False, 0.0
    baseline = values[:-1]
    latest = values[-1]
    mean = statistics.fmean(baseline)
    stdev = statistics.pstdev(baseline)
    if stdev < 1e-9:
        return (abs(latest - mean) > 1e-6), 0.0
    z = (latest - mean) / stdev
    return abs(z) > _RCA_ZSCORE_THRESHOLD, z


def _rca_iqr_anomaly(values: list) -> bool:
    """Flag the latest value if it falls outside the 1.5*IQR fence."""
    if len(values) < _RCA_MIN_SAMPLES:
        return False
    baseline = values[:-1]
    latest = values[-1]
    quantiles = statistics.quantiles(baseline, n=4)
    q1, q3 = quantiles[0], quantiles[2]
    iqr = q3 - q1
    if iqr == 0:
        return False
    lower = q1 - 1.5 * iqr
    upper = q3 + 1.5 * iqr
    return latest < lower or latest > upper


def _rca_first_anomaly_time(timestamps: list, values: list):
    """Earliest timestamp where the rolling Z-score crosses the threshold."""
    for i in range(_RCA_MIN_SAMPLES, len(values)):
        window = values[:i + 1]
        flagged, _ = _rca_zscore_anomaly(window)
        if flagged:
            return timestamps[i]
    return None


def _run_rca() -> dict:
    """Analyze all signals and return a structured RCA report."""
    findings = []
    for name, query in _RCA_SIGNALS.items():
        try:
            samples = _prom_range(query, minutes=15, step=15)
        except OSError:
            continue
        if len(samples) < _RCA_MIN_SAMPLES:
            continue
        timestamps = [s[0] for s in samples]
        values = [s[1] for s in samples]
        z_flag, z = _rca_zscore_anomaly(values)
        iqr_flag = _rca_iqr_anomaly(values)
        if z_flag or iqr_flag:
            findings.append({
                "signal": name,
                "latest": round(values[-1], 4),
                "baseline_mean": round(statistics.fmean(values[:-1]), 4),
                "zscore": round(z, 2),
                "zscore_anomaly": z_flag,
                "iqr_anomaly": iqr_flag,
                "first_anomaly_at": _rca_first_anomaly_time(timestamps, values),
            })
    chain = sorted(
        (f for f in findings if f["first_anomaly_at"] is not None),
        key=lambda f: f["first_anomaly_at"],
    )
    causal_chain = [f["signal"] for f in chain]
    return {
        "generated_at": time.time(),
        "window_minutes": 15,
        "anomalies": findings,
        "causal_chain": causal_chain,
        "likely_root_cause": causal_chain[0] if causal_chain else None,
    }


# Self-contained control panel served at "/". Inline CSS/JS, no external assets,
# so it works on a locked-down box without internet access in the browser.
INDEX_HTML = """<!doctype html>
<html lang="en" data-theme="dark">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>TechStream — Live Monitor</title>
<style>
  :root, [data-theme="dark"] {
    --bg:#0b0e14; --panel:#141a24; --panel2:#1b222e; --line:#26303f;
    --txt:#e6edf3; --muted:#8b98a9; --accent:#22d3ee;
    --ok:#22c55e; --warn:#f59e0b; --bad:#ef4444;
  }
  [data-theme="light"] {
    --bg:#f8fafc; --panel:#ffffff; --panel2:#f1f5f9; --line:#e2e8f0;
    --txt:#1e293b; --muted:#64748b;
    --accent:#22d3ee; --ok:#22c55e; --warn:#f59e0b; --bad:#ef4444;
  }
  * { box-sizing: border-box; }
  *, *::before, *::after {
    transition: background .3s, color .3s, border-color .3s, box-shadow .3s;
  }
  body { margin:0; background:var(--bg); color:var(--txt);
         font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif; line-height:1.45; }
  /* Top gradient accent bar */
  body::before {
    content:''; display:block; width:100%; height:3px;
    background:linear-gradient(90deg, var(--accent), #a78bfa, var(--accent));
    position:fixed; top:0; left:0; z-index:9999;
  }
  .wrap { max-width:1100px; margin:0 auto; padding:1.5rem 1rem 3rem; }
  header { display:flex; flex-wrap:wrap; align-items:center; gap:.75rem;
           border-bottom:1px solid var(--line); padding-bottom:.9rem; margin-bottom:1.25rem; }
  header h1 { font-size:1.25rem; margin:0; letter-spacing:.3px; }
  header .accent { color:var(--accent); }
  .spacer { flex:1; }
  .pill { display:inline-flex; align-items:center; gap:.4rem; padding:.25rem .7rem;
          border-radius:1rem; font-weight:600; font-size:.8rem; }
  .pill.ok{background:rgba(34,197,94,.15);color:var(--ok)}
  .pill.warn{background:rgba(245,158,11,.15);color:var(--warn)}
  .pill.bad{background:rgba(239,68,68,.15);color:var(--bad)}
  .dot{width:.55rem;height:.55rem;border-radius:50%;display:inline-block}
  .dot.ok{background:var(--ok)} .dot.warn{background:var(--warn)} .dot.bad{background:var(--bad)}
  .muted{color:var(--muted);font-size:.8rem}
  .grid{display:grid;gap:.9rem}
  .metrics{grid-template-columns:repeat(auto-fit,minmax(180px,1fr))}
  .cols{grid-template-columns:1fr 1fr}
  @media(max-width:720px){.cols{grid-template-columns:1fr}}
  .panel{background:var(--panel);border:1px solid var(--line);border-radius:.7rem;padding:1rem}
  .panel h2{font-size:.78rem;text-transform:uppercase;letter-spacing:.08em;
            color:var(--muted);margin:0 0 .6rem}
  .metric .label{font-size:.78rem;color:var(--muted);display:flex;
                 justify-content:space-between;align-items:center}
  .metric .val{font-size:1.7rem;font-weight:700;margin:.25rem 0 .1rem}
  .metric .unit{font-size:.85rem;color:var(--muted);font-weight:500}
  .metric.breached{border-color:var(--bad);box-shadow:0 0 0 1px var(--bad) inset}
  .metric{transition:transform .2s ease, box-shadow .2s ease}
  .metric:hover{transform:translateY(-2px);box-shadow:0 4px 12px rgba(0,0,0,.3)}
  .spark{width:100%;height:34px;display:block;margin-top:.3rem}
  .row{display:flex;justify-content:space-between;align-items:center;
       padding:.4rem 0;border-bottom:1px solid var(--line);font-size:.9rem}
  .row:last-child{border-bottom:none}
  .sev{font-size:.7rem;padding:.1rem .5rem;border-radius:.4rem;text-transform:uppercase}
  .sev.critical{background:rgba(239,68,68,.18);color:var(--bad)}
  .sev.warning{background:rgba(245,158,11,.18);color:var(--warn)}
  .sev.none{background:rgba(139,152,169,.18);color:var(--muted)}
  button{font-size:.9rem;padding:.5rem .8rem;margin:.2rem .25rem 0 0;border-radius:.45rem;
         border:1px solid var(--line);background:var(--panel2);color:var(--txt);cursor:pointer}
  button:hover{border-color:var(--accent)}
  button.danger{background:rgba(239,68,68,.15);border-color:rgba(239,68,68,.4);color:#fecaca}
  button.reset{background:rgba(34,197,94,.15);border-color:rgba(34,197,94,.4);color:#bbf7d0}
  button.rca-btn{background:rgba(34,211,238,.12);border-color:rgba(34,211,238,.4);color:var(--accent);font-weight:600}
  button.rca-btn:hover{background:rgba(34,211,238,.22)}
  code{background:var(--panel2);padding:.1rem .35rem;border-radius:.3rem;font-size:.85rem}
  a{color:var(--accent);text-decoration:none} a:hover{text-decoration:underline}
  #log{font-family:ui-monospace,monospace;font-size:.82rem;background:var(--panel2);
       padding:.55rem;border-radius:.4rem;min-height:2.4rem;color:var(--muted)}
  .links a{display:inline-block;margin-right:1.2rem}

  /* Health score gauge */
  .health-gauge { display:flex; align-items:center; gap:.5rem; }
  .health-gauge svg { width:52px; height:52px; }
  .health-gauge .score-text { font-size:1rem; font-weight:700; }
  .health-gauge .score-label { font-size:.7rem; color:var(--muted); text-transform:uppercase; letter-spacing:.06em; }
  @keyframes pulse-score {
    0%{transform:scale(1)} 50%{transform:scale(1.08)} 100%{transform:scale(1)}
  }
  .health-pulse { animation: pulse-score .5s ease; }

  /* Theme toggle */
  .theme-toggle { background:none; border:1px solid var(--line); border-radius:50%;
    width:34px; height:34px; padding:0; margin:0; display:flex; align-items:center;
    justify-content:center; cursor:pointer; font-size:1.1rem; color:var(--muted); }
  .theme-toggle:hover { border-color:var(--accent); color:var(--accent); }
  .spinning { animation: spin 0.6s ease-in-out; }

  /* Uptime badge */
  .uptime-badge { font-size:.78rem; color:var(--muted); display:inline-flex; align-items:center; gap:.3rem; }

  /* Event timeline */
  .timeline-scroll { max-height:300px; overflow-y:auto; }
  .timeline-scroll::-webkit-scrollbar { width:5px; }
  .timeline-scroll::-webkit-scrollbar-track { background:var(--panel2); border-radius:3px; }
  .timeline-scroll::-webkit-scrollbar-thumb { background:var(--line); border-radius:3px; }
  .timeline-scroll::-webkit-scrollbar-thumb:hover { background:var(--muted); }
  .tl-item { display:flex; gap:.55rem; padding:.45rem 0; border-bottom:1px solid var(--line); font-size:.84rem; }
  .tl-item:last-child { border-bottom:none; }
  .tl-icon { width:22px; height:22px; border-radius:50%; display:flex; align-items:center;
    justify-content:center; font-size:.7rem; flex-shrink:0; margin-top:.1rem; }
  .tl-icon.chaos   { background:rgba(245,158,11,.18); color:var(--warn); }
  .tl-icon.alert   { background:rgba(239,68,68,.18); color:var(--bad); }
  .tl-icon.reset   { background:rgba(34,197,94,.18); color:var(--ok); }
  .tl-icon.remed   { background:rgba(96,165,250,.18); color:#60a5fa; }
  .tl-time { color:var(--muted); font-size:.72rem; white-space:nowrap; flex-shrink:0; }
  .tl-desc { flex:1; }

  /* RCA panel */
  .rca-results { margin-top:.7rem; }
  .rca-results .anomaly-item { background:var(--panel2); border-radius:.5rem; padding:.65rem;
    margin-bottom:.5rem; border-left:3px solid var(--warn); }
  .rca-results .anomaly-signal { font-weight:700; font-size:.9rem; }
  .rca-results .anomaly-detail { font-size:.8rem; color:var(--muted); margin-top:.2rem; }
  .rca-chain { background:var(--panel2); border-radius:.5rem; padding:.7rem; margin-top:.5rem;
    font-family:ui-monospace,monospace; font-size:.85rem; display:flex; flex-wrap:wrap; align-items:center; gap:.3rem; }
  .rca-chain .chain-arrow { color:var(--accent); font-weight:700; }
  .rca-chain .chain-signal { color:var(--txt); }
  .rca-root { margin-top:.5rem; padding:.55rem .7rem; border-radius:.5rem;
    background:rgba(239,68,68,.12); border:1px solid rgba(239,68,68,.3);
    font-weight:600; font-size:.88rem; }
  .rca-none { color:var(--muted); font-size:.9rem; padding:.5rem 0; }
  .spinner { display:inline-block; width:18px; height:18px; border:2px solid var(--line);
    border-top-color:var(--accent); border-radius:50%; animation:spin .7s linear infinite;
    vertical-align:middle; margin-right:.4rem; }
  @keyframes spin { to{transform:rotate(360deg)} }
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1><span class="accent">Tech</span>Stream <span class="muted" style="font-weight:400">live monitor</span></h1>
    <span id="status" class="pill warn">connecting…</span>
    <div class="spacer"></div>
    <div id="healthGauge" class="health-gauge"></div>
    <span id="uptimeBadge" class="uptime-badge">&#9650; —</span>
    <button id="themeBtn" class="theme-toggle" title="Toggle theme" onclick="toggleTheme()">&#9790;</button>
    <button id="refreshBtn" class="theme-toggle" title="Refresh metrics" onclick="refresh()">&#x21BB;</button>
    <span class="muted">Prometheus <span id="promDot" class="dot warn"></span></span>
    <span class="muted">updated <span id="updated">—</span></span>
  </header>

  <div class="grid metrics" id="metrics"></div>

  <div class="grid cols" style="margin-top:.9rem">
    <div class="panel">
      <h2>Scrape targets</h2>
      <div id="targets"><span class="muted">loading…</span></div>
    </div>
    <div class="panel">
      <h2>Active alerts</h2>
      <div id="alerts"><span class="muted">loading…</span></div>
    </div>
  </div>

  <div class="grid cols" style="margin-top:.9rem">
    <div class="panel">
      <h2>Inject chaos</h2>
      <button class="danger" onclick="chaos('errors',1.0)">Inject 500 errors</button>
      <button class="danger" onclick="chaos('latency',2.0)">Inject 2s latency</button>
      <button class="danger" onclick="chaos('cpu',2)">Burn CPU</button>
      <button class="reset" onclick="resetChaos()">Reset</button>
      <p class="muted" style="margin:.6rem 0 0">Current: <code id="chaosState">—</code></p>
      <p class="muted" style="margin:.4rem 0 0">"Inject 500 errors" trips
        <code>HighErrorRate</code> after ~1 min; the remediation service then
        auto-restarts this app. Latency/CPU are not auto-healed.</p>
    </div>
    <div class="panel">
      <h2>Probe</h2>
      <button onclick="hit('/api/data')">GET /api/data</button>
      <button onclick="hit('/health')">GET /health</button>
      <div id="log" style="margin-top:.6rem">—</div>
      <h2 style="margin-top:1rem">Dashboards</h2>
      <div class="links">
        <a id="grafana" target="_blank">Grafana</a>
        <a id="prom" target="_blank">Prometheus</a>
        <a id="am" target="_blank">AlertManager</a>
      </div>
    </div>
  </div>

  <div class="grid cols" style="margin-top:.9rem">
    <div class="panel">
      <h2>&#9200; Event timeline</h2>
      <div id="timeline" class="timeline-scroll"><span class="muted">No events yet</span></div>
    </div>
    <div class="panel">
      <h2>&#128269; Root cause analysis</h2>
      <button class="rca-btn" id="rcaBtn" onclick="runRCA()">Run AI Analysis</button>
      <div id="rcaResults" class="rca-results"></div>
    </div>
  </div>
</div>

<script>
  const h = location.hostname;
  grafana.href = `http://${h}:3000`;
  prom.href = `http://${h}:9090`;
  am.href = `http://${h}:9093`;

  const LABELS = {traffic:"Traffic", error_rate:"Error rate", latency_p99:"P99 latency",
                  cpu:"CPU", memory:"Memory"};
  const ORDER = ["traffic","error_rate","latency_p99","cpu","memory"];

  let _lastHealthScore = -1;

  // Theme management
  function initTheme() {
    var saved = localStorage.getItem('techstream-theme');
    if (saved === 'light') {
      document.documentElement.setAttribute('data-theme', 'light');
      document.getElementById('themeBtn').innerHTML = '&#9728;';
    } else {
      document.documentElement.setAttribute('data-theme', 'dark');
      document.getElementById('themeBtn').innerHTML = '&#9790;';
    }
  }
  function toggleTheme() {
    var current = document.documentElement.getAttribute('data-theme');
    if (current === 'light') {
      document.documentElement.setAttribute('data-theme', 'dark');
      document.getElementById('themeBtn').innerHTML = '&#9790;';
      localStorage.setItem('techstream-theme', 'dark');
    } else {
      document.documentElement.setAttribute('data-theme', 'light');
      document.getElementById('themeBtn').innerHTML = '&#9728;';
      localStorage.setItem('techstream-theme', 'light');
    }
  }
  initTheme();

  function fmt(key, v) {
    if (v === null || v === undefined) return "—";
    if (key === "error_rate") return (v*100).toFixed(1) + "%";
    if (key === "latency_p99") return v < 1 ? (v*1000).toFixed(0)+" ms" : v.toFixed(2)+" s";
    if (key === "traffic") return v.toFixed(1);
    return v.toFixed(1) + "%";
  }
  function sparkline(points) {
    if (!points || points.length < 2) return "";
    var ys = points.map(function(p){return p[1]});
    var min = Math.min.apply(null,ys), max = Math.max.apply(null,ys), span = (max-min)||1;
    var W = 100, H = 34;
    var step = W / (points.length - 1);
    var d = points.map(function(p,i){
      return (i*step).toFixed(1)+","+(H - ((p[1]-min)/span)*(H-4) - 2).toFixed(1);
    }).join(" ");
    return '<svg class="spark" viewBox="0 0 '+W+' '+H+'" preserveAspectRatio="none">' +
      '<polyline fill="none" stroke="var(--accent)" stroke-width="1.5" points="'+d+'"/></svg>';
  }
  function log(msg){ document.getElementById('log').textContent =
    new Date().toLocaleTimeString() + '  ' + msg; }

  function formatUptime(seconds) {
    if (seconds === null || seconds === undefined) return '—';
    var s = Math.floor(seconds);
    var d = Math.floor(s / 86400);
    var hr = Math.floor((s % 86400) / 3600);
    var mn = Math.floor((s % 3600) / 60);
    if (d > 0) return 'Up ' + d + 'd ' + hr + 'h';
    if (hr > 0) return 'Up ' + hr + 'h ' + mn + 'm';
    return 'Up ' + mn + 'm';
  }

  function renderHealthGauge(score) {
    var color = score >= 80 ? 'var(--ok)' : (score >= 50 ? 'var(--warn)' : 'var(--bad)');
    var pct = score / 100;
    var r = 20;
    var circ = 2 * Math.PI * r;
    var offset = circ * (1 - pct);
    var pulseClass = (_lastHealthScore !== -1 && _lastHealthScore !== score) ? ' health-pulse' : '';
    _lastHealthScore = score;
    var el = document.getElementById('healthGauge');
    el.innerHTML =
      '<svg viewBox="0 0 52 52" class="' + pulseClass + '">' +
        '<circle cx="26" cy="26" r="' + r + '" fill="none" stroke="var(--line)" stroke-width="4"/>' +
        '<circle cx="26" cy="26" r="' + r + '" fill="none" stroke="' + color + '" stroke-width="4"' +
          ' stroke-dasharray="' + circ.toFixed(1) + '" stroke-dashoffset="' + offset.toFixed(1) + '"' +
          ' stroke-linecap="round" transform="rotate(-90 26 26)"/>' +
        '<text x="26" y="29" text-anchor="middle" fill="' + color + '" font-size="13" font-weight="700">' + score + '</text>' +
      '</svg>' +
      '<div><div class="score-text" style="color:' + color + '">' + score + '/100</div>' +
      '<div class="score-label">Health</div></div>';
  }

  async function chaos(mode, value){
    var r = await fetch('/chaos',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({mode:mode,value:value})});
    log('POST /chaos '+mode+'='+value+' -> '+r.status); refresh(); loadEvents();
  }
  async function resetChaos(){
    var r = await fetch('/chaos/reset',{method:'POST'});
    log('POST /chaos/reset -> '+r.status); refresh(); loadEvents();
  }
  async function hit(path){
    var t0 = performance.now();
    try { var r = await fetch(path);
      log('GET '+path+' -> '+r.status+' ('+(performance.now()-t0).toFixed(0)+' ms)');
    } catch(e){ log('GET '+path+' -> error '+e); }
  }

  async function loadEvents() {
    try {
      var data = await (await fetch('/api/events')).json();
      var el = document.getElementById('timeline');
      if (!data.events || data.events.length === 0) {
        el.innerHTML = '<span class="muted">No events yet</span>';
        return;
      }
      var icons = {chaos:'&#9889;', alert:'&#9888;', reset:'&#10004;', remediation:'&#128736;'};
      var iconCls = {chaos:'chaos', alert:'alert', reset:'reset', remediation:'remed'};
      el.innerHTML = data.events.slice().reverse().map(function(ev) {
        var dt = new Date(ev.timestamp * 1000);
        var ts = dt.toLocaleTimeString();
        var icon = icons[ev.type] || '&#8226;';
        var cls = iconCls[ev.type] || 'chaos';
        return '<div class="tl-item">' +
          '<div class="tl-icon ' + cls + '">' + icon + '</div>' +
          '<div class="tl-desc">' + ev.description + '</div>' +
          '<div class="tl-time">' + ts + '</div>' +
        '</div>';
      }).join('');
    } catch(e) {}
  }

  async function runRCA() {
    var btn = document.getElementById('rcaBtn');
    var results = document.getElementById('rcaResults');
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner"></span>Analyzing…';
    results.innerHTML = '';
    try {
      var data = await (await fetch('/api/rca', {method:'POST'})).json();
      if (!data.anomalies || data.anomalies.length === 0) {
        results.innerHTML = '<div class="rca-none">&#10003; No anomalies detected in the current window.</div>';
      } else {
        var html = '';
        data.anomalies.forEach(function(a) {
          var flags = [];
          if (a.zscore_anomaly) flags.push('Z=' + a.zscore);
          if (a.iqr_anomaly) flags.push('IQR');
          html += '<div class="anomaly-item">' +
            '<div class="anomaly-signal">' + a.signal + ' <span class="muted">[' + flags.join(', ') + ']</span></div>' +
            '<div class="anomaly-detail">Latest: ' + a.latest + ' | Baseline: ~' + a.baseline_mean + '</div>' +
          '</div>';
        });
        if (data.causal_chain && data.causal_chain.length > 0) {
          html += '<div class="rca-chain">';
          data.causal_chain.forEach(function(s, i) {
            if (i > 0) html += '<span class="chain-arrow"> &#8594; </span>';
            html += '<span class="chain-signal">' + s + '</span>';
          });
          html += '</div>';
        }
        if (data.likely_root_cause) {
          html += '<div class="rca-root">&#9888; Likely root cause: ' + data.likely_root_cause + '</div>';
        }
        results.innerHTML = html;
      }
    } catch(e) {
      results.innerHTML = '<div class="rca-none" style="color:var(--bad)">&#10006; RCA failed: ' + e + '</div>';
    }
    btn.disabled = false;
    btn.textContent = 'Run AI Analysis';
  }

  async function refresh(){
    var btn = document.getElementById('refreshBtn');
    if (btn) btn.classList.add('spinning');
    var status = document.getElementById('status');
    try {
      var d = await (await fetch('/api/overview')).json();

      // metric cards
      var mwrap = document.getElementById('metrics');
      mwrap.innerHTML = ORDER.map(function(k){
        var m = d.metrics[k] || {};
        return '<div class="panel metric '+(m.breached?'breached':'')+'">' +
          '<div class="label"><span>'+LABELS[k]+'</span>'+(m.breached?'<span class="dot bad"></span>':'')+'</div>' +
          '<div class="val">'+fmt(k, m.value)+'</div>' +
          sparkline(d.series[k]) +
        '</div>';
      }).join("");

      // targets
      document.getElementById('targets').innerHTML = (d.targets||[]).map(function(t){
        return '<div class="row"><span>'+t.job+'</span>' +
         '<span class="dot '+(t.health==='up'?'ok':'bad')+'"></span></div>';
      }).join("")
        || '<span class="muted">none</span>';

      // alerts
      var alerts = d.alerts || [];
      document.getElementById('alerts').innerHTML = alerts.length ? alerts.map(function(a){
        return '<div class="row"><span>'+a.name+' <span class="muted">('+a.state+')</span></span>' +
         '<span class="sev '+a.severity+'">'+a.severity+'</span></div>';
      }).join("")
        : '<span class="muted">No active alerts &#10003;</span>';

      // chaos + header status
      document.getElementById('chaosState').textContent = JSON.stringify(d.chaos.state);
      var firing = alerts.some(function(a){return a.state === 'firing'});
      if (firing){ status.textContent='alert firing'; status.className='pill bad'; }
      else if (d.chaos.active){ status.textContent='chaos active'; status.className='pill warn'; }
      else { status.textContent='healthy'; status.className='pill ok'; }

      var pd = document.getElementById('promDot');
      pd.className = 'dot ' + (d.prometheus_ok ? 'ok' : 'bad');
      document.getElementById('updated').textContent = new Date().toLocaleTimeString();

      // health score
      if (typeof d.health_score === 'number') {
        renderHealthGauge(d.health_score);
      }

      // uptime
      if (typeof d.uptime_seconds === 'number') {
        document.getElementById('uptimeBadge').innerHTML = '&#9650; ' + formatUptime(d.uptime_seconds);
      }
    } catch(e){
      status.textContent='unreachable'; status.className='pill bad';
    } finally {
      if (btn) {
        setTimeout(function(){ btn.classList.remove('spinning'); }, 600);
      }
    }
  }
  refresh();
  setInterval(refresh, 5000);
  loadEvents();
  setInterval(loadEvents, 10000);
</script>
</body>
</html>
"""


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
    """Serve the control panel (a self-contained HTML page)."""
    return Response(INDEX_HTML, mimetype="text/html")


@app.route("/chaos/status")
def chaos_status() -> Response:
    """Read-only view of the current chaos state, for the control panel."""
    with _chaos_lock:
        state = dict(_chaos_state)
    active = any(v > 0 for v in state.values())
    return jsonify(active=active, state=state)


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

    _add_event("chaos", "Chaos injected: " + str(mode) + " = " + str(value))
    return jsonify(status="chaos injected", mode=mode, value=value)


@app.route("/chaos/reset", methods=["POST"])
def chaos_reset() -> Response:
    """Clear all injected chaos. Called by the remediation service."""
    with _chaos_lock:
        for mode in VALID_MODES:
            _chaos_state[mode] = 0.0
            CHAOS_ACTIVE.labels(mode=mode).set(0)
        _sync_cpu_threads(0)
    _add_event("reset", "All chaos cleared")
    return jsonify(status="chaos cleared")


def _prom_get(path: str) -> dict:
    """GET a Prometheus API path and return the parsed JSON 'data' (or {})."""
    url = f"{PROM_URL.rstrip('/')}{path}"
    with urllib.request.urlopen(url, timeout=5) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    if body.get("status") != "success":
        return {}
    return body.get("data", {})


def _prom_range(query: str, minutes: int = 15, step: int = 30) -> list[list[float]]:
    """Range query → [[ts, value], ...] for one aggregated series."""
    end = time.time()
    params = urllib.parse.urlencode(
        {"query": query, "start": end - minutes * 60, "end": end, "step": step}
    )
    data = _prom_get(f"/api/v1/query_range?{params}")
    result = data.get("result") or []
    if not result:
        return []
    out = []
    for ts, value in result[0].get("values", []):
        try:
            out.append([float(ts), float(value)])
        except ValueError:
            continue  # Prometheus emits "NaN" as a string for undefined points
    return out


@app.route("/api/overview")
def api_overview() -> Response:
    """Aggregated live data for the dashboard: Golden Signals (latest + recent
    series), scrape-target health, active alerts, and chaos state. Queried
    server-side so the browser never talks to Prometheus directly."""
    global _prev_alert_states
    metrics_out: dict[str, dict] = {}
    series_out: dict[str, list] = {}
    prom_ok = True

    for key, query, unit, warn, higher_bad in DASHBOARD_SIGNALS:
        try:
            pts = _prom_range(query)
        except OSError:  # URLError / TimeoutError both subclass OSError
            prom_ok = False
            pts = []
        latest = pts[-1][1] if pts else None
        breached = (
            latest is not None and warn is not None and higher_bad and latest > warn
        )
        metrics_out[key] = {"value": latest, "unit": unit, "warn": warn,
                            "breached": breached}
        series_out[key] = pts[-40:]  # cap points sent to the browser

    targets = []
    alerts = []
    try:
        for t in _prom_get("/api/v1/targets").get("activeTargets", []):
            targets.append({
                "job": t.get("labels", {}).get("job"),
                "health": t.get("health"),
            })
        for a in _prom_get("/api/v1/alerts").get("alerts", []):
            alerts.append({
                "name": a.get("labels", {}).get("alertname"),
                "severity": a.get("labels", {}).get("severity", "none"),
                "state": a.get("state"),
            })
    except OSError:  # URLError / TimeoutError both subclass OSError
        prom_ok = False

    # Detect alert state changes and record events
    current_alert_states: dict[str, str] = {}
    for a in alerts:
        alert_name = a.get("name", "unknown")
        alert_state = a.get("state", "unknown")
        current_alert_states[alert_name] = alert_state
        prev_state = _prev_alert_states.get(alert_name)
        if prev_state != alert_state:
            if alert_state == "firing":
                _add_event("alert", "Alert firing: " + str(alert_name))
            elif alert_state == "pending":
                _add_event("alert", "Alert pending: " + str(alert_name))
            elif prev_state is not None:
                _add_event("remediation", "Alert resolved: " + str(alert_name))
    # Check for alerts that disappeared (resolved)
    for prev_name in _prev_alert_states:
        if prev_name not in current_alert_states:
            _add_event("remediation", "Alert cleared: " + str(prev_name))
    _prev_alert_states = current_alert_states

    with _chaos_lock:
        chaos = {"active": any(v > 0 for v in _chaos_state.values()),
                 "state": dict(_chaos_state)}

    health_score = _compute_health_score(metrics_out)
    uptime_seconds = time.time() - _app_start_time

    return jsonify(
        ts=time.time(),
        prometheus_ok=prom_ok,
        metrics=metrics_out,
        series=series_out,
        targets=targets,
        alerts=alerts,
        chaos=chaos,
        health_score=health_score,
        uptime_seconds=uptime_seconds,
    )


@app.route("/api/events")
def api_events() -> Response:
    """Return the in-memory event timeline."""
    with _events_lock:
        events_copy = list(_events)
    return jsonify(events=events_copy)


@app.route("/api/rca", methods=["POST"])
def api_rca() -> Response:
    """Run inline Z-score + IQR root-cause analysis."""
    try:
        report = _run_rca()
    except Exception as exc:
        return jsonify(error=str(exc)), 500
    return jsonify(report)


@app.route("/api/status")
def api_status() -> Response:
    """Normalized status endpoint for external consumers."""
    # Gather metrics for health score
    metrics_out: dict[str, dict] = {}
    prom_ok = True
    for key, query, unit, warn, higher_bad in DASHBOARD_SIGNALS:
        try:
            pts = _prom_range(query)
        except OSError:
            prom_ok = False
            pts = []
        latest = pts[-1][1] if pts else None
        breached = (
            latest is not None and warn is not None and higher_bad and latest > warn
        )
        metrics_out[key] = {"value": latest, "unit": unit, "warn": warn,
                            "breached": breached}

    health_score = _compute_health_score(metrics_out)

    if health_score >= 80:
        status_label = "healthy"
    elif health_score >= 50:
        status_label = "degraded"
    else:
        status_label = "critical"

    uptime_seconds = time.time() - _app_start_time

    with _chaos_lock:
        chaos_active = any(v > 0 for v in _chaos_state.values())

    signals = {}
    for key in metrics_out:
        m = metrics_out[key]
        signals[key] = {"value": m["value"], "breached": m["breached"]}

    return jsonify(
        status=status_label,
        health_score=health_score,
        uptime_seconds=round(uptime_seconds, 1),
        signals=signals,
        chaos_active=chaos_active,
        prometheus_ok=prom_ok,
    )


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
