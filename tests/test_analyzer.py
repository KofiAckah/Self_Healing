"""Unit tests for the statistical RCA analyzer (Phase 4)."""

from __future__ import annotations

import root_cause_analyzer as rca


def test_zscore_anomaly_flags_outlier():
    # 20 stable values around 1.0, then a clear spike.
    values = [1.0 + (i % 2) * 0.01 for i in range(20)] + [50.0]
    flagged, z = rca.zscore_anomaly(values)
    assert flagged is True
    assert z > rca.ZSCORE_THRESHOLD


def test_zscore_anomaly_ignores_stable_series():
    values = [1.0, 1.01, 0.99, 1.0, 1.02, 0.98, 1.0, 1.01, 0.99, 1.0, 1.0]
    flagged, _ = rca.zscore_anomaly(values)
    assert flagged is False


def test_zscore_needs_minimum_samples():
    flagged, _ = rca.zscore_anomaly([1.0, 100.0])
    assert flagged is False


def test_iqr_anomaly_flags_outlier():
    # Baseline must have spread (non-zero IQR); the final value is a clear outlier.
    values = [10.0, 11.0, 9.0, 10.5, 9.5, 10.0, 11.0, 9.0, 10.0, 10.2] * 2 + [1000.0]
    assert rca.iqr_anomaly(values) is True


def test_iqr_anomaly_ignores_stable_series():
    values = [10.0, 11.0, 9.0, 10.5, 9.5, 10.0, 11.0, 9.0, 10.0, 10.0, 10.2]
    assert rca.iqr_anomaly(values) is False


def test_first_anomaly_time_returns_timestamp_of_spike():
    timestamps = list(range(20))
    values = [1.0] * 15 + [99.0] * 5
    t = rca.first_anomaly_time([float(x) for x in timestamps], values)
    assert t is not None
    assert t == 15  # the spike begins at index 15


def test_analyze_builds_causal_chain(monkeypatch):
    # Fake Prometheus over 30 samples. The anomaly is recent (in the tail) so
    # the latest value is still anomalous vs the baseline, and the onsets are
    # staggered: cpu (t=25) -> latency (t=26) -> error_rate (t=27).
    def fake_query_range(prometheus, query, minutes, step):
        ts = [float(i) for i in range(30)]
        if query == rca.SIGNALS["cpu_util"]:
            return list(zip(ts, [10.0] * 25 + [95.0] * 5))
        if query == rca.SIGNALS["latency_p99"]:
            return list(zip(ts, [0.1] * 26 + [5.0] * 4))
        if query == rca.SIGNALS["error_rate"]:
            return list(zip(ts, [0.0] * 27 + [0.8] * 3))
        # flat series -> no anomaly
        return list(zip(ts, [1.0] * 30))

    monkeypatch.setattr(rca, "query_range", fake_query_range)
    report = rca.analyze("http://prom:9090", minutes=15, step=15)

    assert report["causal_chain"][0] == "cpu_util"
    assert report["likely_root_cause"] == "cpu_util"
    # cpu before latency before error_rate
    chain = report["causal_chain"]
    assert chain.index("cpu_util") < chain.index("latency_p99") < chain.index("error_rate")


def test_analyze_reports_no_anomalies_on_flat_data(monkeypatch):
    monkeypatch.setattr(
        rca, "query_range",
        lambda prometheus, query, minutes, step:
            list(zip(range(20), [1.0] * 20)),
    )
    # request_rate uses a flat series too; nothing should be flagged.
    report = rca.analyze("http://prom:9090", minutes=15, step=15)
    assert report["anomalies"] == []
    assert report["likely_root_cause"] is None
