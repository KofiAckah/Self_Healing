#!/usr/bin/env bash
# Tear down the stack and remove generated artefacts.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

echo "Stopping the stack and removing volumes..."
docker compose down -v

# Remove the generated token file and demo reports (none are committed).
rm -f monitoring/alertmanager/token rca_report.json rca_report.md
echo "Cleanup complete."
