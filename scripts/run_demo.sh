#!/usr/bin/env bash
# End-to-end self-healing demo:
#   1. Inject HTTP 500 chaos + traffic.
#   2. Wait for the HighErrorRate alert to fire and remediation to restart app.
#   3. Run the statistical RCA (and the Claude RCA if ANTHROPIC_API_KEY is set).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

APP="${APP_URL:-http://localhost:5000}"
PROM="${PROM_URL:-http://localhost:9090}"
DURATION="${DURATION:-150}"

echo "==> Step 1: injecting error chaos for ${DURATION}s (HighErrorRate fires after ~1m)"
python3 chaos/chaos_script.py --scenario errors --target "$APP" --duration "$DURATION" --workers 20 &
CHAOS_PID=$!

echo "==> Step 2: watching the remediation service log for the restart"
echo "    (in another terminal: docker compose logs -f remediation)"
docker compose logs -f remediation &
LOG_PID=$!

# Let the alert fire (1m 'for') + AlertManager group_wait + restart.
sleep "$((DURATION + 20))"
kill "$LOG_PID" 2>/dev/null || true
wait "$CHAOS_PID" 2>/dev/null || true

echo
echo "==> Step 3: statistical root-cause analysis"
python3 ai_analysis/root_cause_analyzer.py --prometheus "$PROM" --output rca_report.json || true

if [[ -n "${ANTHROPIC_API_KEY:-}" ]]; then
  echo
  echo "==> Step 3b: Claude RCA report (bonus)"
  python3 ai_analysis/analyze.py --prometheus "$PROM" --output rca_report.md || true
else
  echo "(ANTHROPIC_API_KEY not set — skipping the Claude RCA bonus.)"
fi

echo
echo "Demo complete. Check Grafana (http://localhost:3000) returning to green,"
echo "and AlertManager (http://localhost:9093) for the resolved alert."
