"""Claude-powered RCA report for the TechStream self-healing lab (Phase 4 bonus).

Reuses the statistical analysis from ``root_cause_analyzer.py`` to pull the
Golden-Signal anomalies out of Prometheus, then asks the Claude API to turn that
structured data into a human-readable root-cause report: likely cause, affected
signals, the causal timeline, and recommended remediation.

Requires the ``anthropic`` package and an ``ANTHROPIC_API_KEY`` environment
variable (sourced from .env locally, or from SSM Parameter Store on the EC2).

Usage
-----
    export ANTHROPIC_API_KEY=sk-ant-...
    python analyze.py --prometheus http://localhost:9090
    python analyze.py --output rca_report.md
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import anthropic

import root_cause_analyzer as rca

# Sonnet 4.6 is plenty capable for summarizing a handful of metric series and
# keeps the lab's API spend low (Cost Optimization). Override with --model.
DEFAULT_MODEL = "claude-sonnet-4-6"

SYSTEM_PROMPT = (
    "You are a senior SRE performing root-cause analysis for the TechStream "
    "service. You are given statistical anomaly findings (Z-score and IQR) for "
    "the four Golden Signals derived from live Prometheus data. Produce a "
    "concise, actionable RCA in Markdown with these sections: Summary, Likely "
    "Root Cause, Affected Signals, Causal Timeline, Recommended Remediation. "
    "Base every claim on the supplied data; do not invent metrics."
)


def build_prompt(report: dict) -> str:
    return (
        "Here is the anomaly analysis as JSON. Write the RCA report.\n\n"
        f"```json\n{json.dumps(report, indent=2)}\n```"
    )


def generate_report(report: dict, model: str) -> str:
    """Call the Claude API and return the Markdown RCA text."""
    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from the env
    response = client.messages.create(
        model=model,
        max_tokens=2048,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": build_prompt(report)}],
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("Claude declined to produce the report.")
    return "".join(block.text for block in response.content if block.type == "text")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="TechStream RCA via Claude API")
    parser.add_argument("--prometheus", default="http://localhost:9090")
    parser.add_argument("--minutes", type=int, default=15)
    parser.add_argument("--step", type=int, default=15)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--output", help="write the Markdown report to this path")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY is not set; cannot call the Claude API.",
              file=sys.stderr)
        return 1

    try:
        report = rca.analyze(args.prometheus, args.minutes, args.step)
    except (rca.urllib.error.URLError, RuntimeError) as exc:
        print(f"[analyze] Prometheus error: {exc}", file=sys.stderr)
        return 1

    if not report["anomalies"]:
        print("[analyze] No anomalies detected — nothing to analyze. "
              "Run the chaos script first.")
        return 0

    try:
        markdown = generate_report(report, args.model)
    except anthropic.APIError as exc:
        print(f"[analyze] Claude API error: {exc}", file=sys.stderr)
        return 1

    print(markdown)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(markdown)
        print(f"\n[analyze] report written to {args.output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
