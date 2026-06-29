"""Unit tests for the chaos script (Phase 2)."""

from __future__ import annotations

import chaos_script


def test_all_scenarios_known():
    assert set(chaos_script.SCENARIOS) == {
        "errors", "latency", "cpu", "memory", "load", "full"
    }


def test_self_healing_scenarios_are_not_reset_by_default():
    # errors/full must leave chaos for the remediation service to clear.
    assert chaos_script.SELF_HEALING_SCENARIOS == {"errors", "full"}


def test_parse_args_defaults():
    args = chaos_script.parse_args(["--scenario", "errors"])
    assert args.scenario == "errors"
    assert args.target == "http://localhost:5000"
    assert args.duration == 120
    assert args.workers == 20


def test_parse_args_overrides():
    args = chaos_script.parse_args(
        ["--scenario", "full", "--duration", "30", "--workers", "5",
         "--cpu-threads", "2", "--latency", "1.5"]
    )
    assert args.duration == 30
    assert args.workers == 5
    assert args.cpu_threads == 2
    assert args.latency == 1.5


def test_inject_errors_posts_error_chaos(monkeypatch):
    calls = []
    monkeypatch.setattr(
        chaos_script, "post_json",
        lambda url, payload, timeout=5.0: calls.append((url, payload)) or 200,
    )
    chaos_script.inject("http://app:5000", "errors", cpu_threads=4, latency=2.0)
    assert calls == [("http://app:5000/chaos", {"mode": "errors", "value": 1.0})]


def test_inject_full_posts_all_three(monkeypatch):
    modes = []
    monkeypatch.setattr(
        chaos_script, "post_json",
        lambda url, payload, timeout=5.0: modes.append(payload["mode"]) or 200,
    )
    chaos_script.inject("http://app:5000", "full", cpu_threads=2, latency=1.0)
    assert modes == ["errors", "latency", "cpu"]
