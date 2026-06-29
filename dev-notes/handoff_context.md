# TechStream Self-Healing Lab — Handoff Context

> **Handoff Target:** Load this file into any fresh AI session to immediately resume the lab context.

---

## 1. Environment & Setup State

- **Host Machine**: Ubuntu 24.04 (31 GB RAM, 8 Cores, 248 GB disk space).
- **Simulated Infrastructure (Multipass VM)**: 
  - Name: `techstream`
  - IP: `10.183.79.17`
  - Specs: Ubuntu 24.04, 4 CPUs, 4 GB RAM, 20 GB Disk
  - Role: Acts as a local EC2 instance running the Docker Compose stack.
  - Deployment Command (inside `/home/ubuntu/Self-Healing`): `bash scripts/deploy_local.sh`

---

## 2. Docker Compose Containers (Running in VM)

All 8 containers are up and running:
- **`techstream-app`** (`:5000`): Flask app containing metrics, chaos toggle, and the enhanced dashboard UI.
- **`techstream-prometheus`** (`:9090`): Metric scraper.
- **`techstream-grafana`** (`:3000`): Admin UI (password: `TechStream2024!`).
- **`techstream-alertmanager`** (`:9093`): Alert routing.
- **`techstream-node-exporter`** (`:9100`): Host metrics.
- **`techstream-cadvisor`** (`:8080`): Container metrics.
- **`techstream-docker-proxy`** (`:2375`): Secure Docker API access.
- **`techstream-remediation`** (`:8081`): Webhook receiver that auto-restarts the app.

---

## 3. UI Enhancements Implemented (In the VM only)

The original code on the host remains **untouched** for safety. All UI updates were written to `dev-notes/enhanced-app.py` and deployed inside the VM's `app/app.py`:

1. **System Health Score (100/100 Gauge)**: A composite score based on the 5 Golden Signals. Idle/no-data signals default to a healthy score (20/20) so it reads 100/100 when clean.
2. **Incident & Event Timeline**: In-memory event logs capturing chaos triggers, resets, and alerts, rendered as a scrollable component.
3. **In-Dashboard RCA Trigger**: Run statistical Z-score + IQR analysis directly from the UI and see anomalies and the causal timeline chain.
4. **Dark/Light Theme Toggle (☀/☾)**: CSS variable switcher persisted in `localStorage`.
5. **Manual Refresh Button (↻)**: Added to the header with a rotation spin micro-animation to fetch instant updates.
6. **Uptime Counter**: Displays duration since Flask module boot (e.g. `▲ Up 1h 25m`).
7. **Status API**: Exposed `/api/status` returning normalized health details.

---

## 4. Directory Structure (Host)

Your local development folder contains:
- `dev-notes/`
  - [enhanced-app.py](file:///home/joel-livingstone-kofi-ackah/Desktop/Labs/Self-Healing/dev-notes/enhanced-app.py) (The complete codebase with all new UI changes)
  - [CHANGELOG.md](file:///home/joel-livingstone-kofi-ackah/Desktop/Labs/Self-Healing/dev-notes/CHANGELOG.md) (Log of every implemented feature)
  - [cloud-init.yml](file:///home/joel-livingstone-kofi-ackah/Desktop/Labs/Self-Healing/dev-notes/cloud-init.yml) (Automated Docker provisioning for VM)
  - [handoff_context.md](file:///home/joel-livingstone-kofi-ackah/Desktop/Labs/Self-Healing/dev-notes/handoff_context.md) (This file)

---

## 5. Copy-Paste Handoff Prompt

When starting a new chat, copy and paste the following message:

> "I am working on the TechStream Self-Healing Lab. We created a Multipass VM named `techstream` at `10.183.79.17` running 8 containers. We designed and implemented 8 UI enhancements inspired by `ai-watch.dev` inside `dev-notes/enhanced-app.py` and deployed them in the VM. The original host codebase remains untouched. Read `/home/joel-livingstone-kofi-ackah/Desktop/Labs/Self-Healing/dev-notes/handoff_context.md` and `/home/joel-livingstone-kofi-ackah/Desktop/Labs/Self-Healing/dev-notes/CHANGELOG.md` to see what we've done, then help me with my next steps."
