"""Gemini-powered RCA report for the TechStream self-healing lab (Phase 4 bonus).

Identical in purpose to ``analyze.py`` but uses the Google Gemini API instead of
Claude — handy when an Anthropic API key isn't available but a (free) Google AI
Studio key is. Reuses the statistical analysis from ``root_cause_analyzer.py`` to
pull the Golden-Signal anomalies out of Prometheus, then asks Gemini to turn that
structured data into a human-readable root-cause report.

Requires the ``google-genai`` package and a ``GEMINI_API_KEY`` environment
variable. Get a free key at https://aistudio.google.com (Get API key) — the free
tier needs no billing.

Usage
-----
    export GEMINI_API_KEY=...
    python analyze_gemini.py --prometheus http://localhost:9090
    python analyze_gemini.py --output rca_report.md
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from google import genai
from google.genai import types

import root_cause_analyzer as rca

# Flash is fast, capable enough for summarizing a handful of metric series, and
# sits comfortably inside the free tier. Override with --model.
DEFAULT_MODEL = "gemini-2.5-flash"

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
    """Call the Gemini API and return the Markdown RCA text."""
    client = genai.Client()  # reads GEMINI_API_KEY from the env
    response = client.models.generate_content(
        model=model,
        contents=build_prompt(report),
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            max_output_tokens=4096,
            # 2.5-flash burns "thinking" tokens by default, which can eat the
            # whole budget before the report finishes. Disable it so the full
            # output budget goes to the RCA text.
            thinking_config=types.ThinkingConfig(thinking_budget=0),
        ),
    )
    return response.text or ""


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="TechStream RCA via Gemini API")
    parser.add_argument("--prometheus", default="http://localhost:9090")
    parser.add_argument("--minutes", type=int, default=15)
    parser.add_argument("--step", type=int, default=15)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--output", help="write the Markdown report to this path")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    if not (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")):
        print("GEMINI_API_KEY is not set; cannot call the Gemini API.",
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
    except Exception as exc:  # google-genai raises provider-specific errors
        print(f"[analyze] Gemini API error: {exc}", file=sys.stderr)
        return 1

    print(markdown)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(markdown)
        print(f"\n[analyze] report written to {args.output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
