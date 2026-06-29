#!/usr/bin/env bash
# Bring up the TechStream self-healing stack locally (or on the lab EC2).
#
# - Validates that .env exists with the required secrets.
# - Materialises the AlertManager bearer-token file from REMEDIATION_TOKEN.
# - Builds and starts the Docker Compose stack.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

if [[ ! -f .env ]]; then
  echo "ERROR: .env not found. Copy .env.example to .env and fill it in." >&2
  exit 1
fi

# Load .env so we can read the token (export every var defined there).
set -a
# shellcheck disable=SC1091
source .env
set +a

: "${REMEDIATION_TOKEN:?REMEDIATION_TOKEN must be set in .env}"
: "${GF_SECURITY_ADMIN_PASSWORD:?GF_SECURITY_ADMIN_PASSWORD must be set in .env}"

# AlertManager reads the bearer token from a file (keeps it out of git and out
# of the committed config). Write it with restrictive permissions.
TOKEN_FILE="monitoring/alertmanager/token"
printf '%s' "$REMEDIATION_TOKEN" > "$TOKEN_FILE"
# Must be readable by the AlertManager container user (runs as uid 65534
# 'nobody'), which differs from the host user — a 600 file owned by the host
# user is unreadable inside the container and AlertManager fails to send the
# webhook. 644 is fine here: this is a low-value internal webhook token (the
# API key, the real secret, lives in .env/SSM at 600), and the box is
# single-tenant with the port firewalled to one IP.
chmod 644 "$TOKEN_FILE"
echo "Wrote AlertManager token file: $TOKEN_FILE"

echo "Building and starting the stack..."
docker compose up -d --build

echo
echo "Stack is starting. Endpoints (lock these to your IP in the security group):"
echo "  App:          http://localhost:5000"
echo "  Prometheus:   http://localhost:9090"
echo "  AlertManager: http://localhost:9093"
echo "  Grafana:      http://localhost:3000  (admin / \$GF_SECURITY_ADMIN_PASSWORD)"
echo
echo "Run scripts/run_demo.sh to drive the chaos -> alert -> heal demo."
