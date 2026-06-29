# TechStream Self-Healing — Lab Report

> Fill the screenshot placeholders as you run the demo. Each phase below maps to
> a grading dimension; capture the evidence the rubric asks for.

## Scenario & objective

TechStream wants to cut MTTR. This lab implements Golden-Signal monitoring,
injects an incident, and **automatically remediates** it before an engineer is
paged — with simulated AI root-cause analysis.

## Design decisions

- **Local Docker on a single EC2**, provisioned by Terraform with an **S3 remote
  backend (native `use_lockfile` locking, no DynamoDB)** — team-safe, CI-usable
  state rather than a local file.
- **No Amazon DevOps Guru** (restricted lab account). Phase 4 is a stdlib
  Z-score + IQR analyzer plus an optional Claude-API RCA report.
- **No AWS Lambda/EventBridge/SSM remediation path.** Self-healing happens
  inside Docker Compose: AlertManager → token-auth webhook → restart.

## Phase 1 — Monitoring stack

The app exposes the four Golden Signals; node-exporter and cAdvisor supply host
saturation. Grafana auto-provisions the datasource and dashboard.

- [ ] **Screenshot:** Grafana "Golden Signals" dashboard, all panels green.
- [ ] **Screenshot:** Prometheus targets page (`/targets`) all UP.

## Phase 2 — Anomaly injection

`chaos_script.py --scenario errors` injects HTTP 500s and drives traffic.

- [ ] **Screenshot:** Grafana Errors panel climbing past 5%.
- [ ] **Command run:** `python chaos/chaos_script.py --scenario errors --duration 150`

## Phase 3 — Alerting + automated remediation

`HighErrorRate` fires after 1 min → AlertManager → remediation service clears
chaos and restarts the app container → metrics recover → alert resolves.

- [ ] **Screenshot:** AlertManager showing `HighErrorRate` firing.
- [ ] **Screenshot:** remediation service log (`docker compose logs remediation`)
      showing "REMEDIATING" + "restarted container techstream-app".
- [ ] **Screenshot:** Grafana returning to green after the restart.

## Phase 4 — AI root-cause analysis

- [ ] **Output:** `root_cause_analyzer.py` flagging the injected anomaly and the
      causal chain (paste `rca_report.json` excerpt).
- [ ] **Output (bonus):** `analyze.py` Claude RCA report (paste `rca_report.md`).

## Verification

- [ ] `pytest` — unit suite passing (app, chaos, analyzer, remediation).
- [ ] `RUN_E2E=1 pytest tests/test_integration_e2e.py` — live self-heal passing.
- [ ] CI run green (lint + tests + image builds + terraform validate).

## Cost notes

- Single `t3.small` (eu-west-1), stopped when not demoing.
- S3 state bucket + no DynamoDB; Claude RCA uses `claude-sonnet-4-6` (low spend).
- `terraform destroy` removes all chargeable resources.

## Cleanup

```bash
scripts/cleanup.sh            # stop the stack
cd terraform && terraform destroy
```
