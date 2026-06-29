# TechStream UI Enhancement — Development Log

> **Date:** 2026-06-29  
> **Approach:** All changes made in a Multipass VM (`techstream`) — your local files are untouched.  
> **VM specs:** Ubuntu 24.04, 4 CPUs, 4 GB RAM, 20 GB disk  

---

## What Was Changed & Why

### Overview
Inspired by [ai-watch.dev](https://ai-watch.dev/), we enhanced the TechStream dashboard UI with 7 new features while keeping ALL existing backend logic (routes, chaos state, Prometheus metrics) exactly the same. The enhanced file is a **drop-in replacement** for `app/app.py`.

### Files Modified (in the VM only)
| File | What Changed |
|---|---|
| `app/app.py` | Enhanced `INDEX_HTML` template + 3 new API endpoints + health score logic + event system |

### Files NOT Modified
Everything else stays the same: `docker-compose.yml`, `Dockerfile`, `requirements.txt`, monitoring configs, remediation, chaos script, AI analysis scripts, tests.

---

## Feature 1: System Health Score

**What:** A composite 0–100 health score computed from all 5 Golden Signals, displayed as a prominent circular gauge in the header.

**How it works:**
- Each signal contributes 20 points (5 signals × 20 = 100 max)
- Signal above threshold → 0 points
- Signal within 50% of threshold → 10 points  
- Signal healthy → 20 points
- No data → 20 points (defaults to full points so score shows 100 when idle)

**Backend:** New function `_compute_health_score(metrics_dict)` called inside `/api/overview`

**Frontend:** SVG circular gauge with color coding:
- Green (≥ 80): System healthy
- Yellow (≥ 50): Degraded
- Red (< 50): Critical

---

## Feature 2: Incident/Event Timeline

**What:** A scrollable timeline panel showing recent events (chaos injections, alerts, remediations) with timestamps, icons, and color coding.

**How it works:**
- In-memory event log (`_events` list, max 100 entries, thread-safe)
- Events auto-recorded when:
  - Chaos is injected → orange event
  - Chaos is reset → green event
  - Alert detected during polling → red event
- Custom scrollbar styling for the dark theme

**Backend:** 
- `_events` list + `_events_lock` for thread safety
- `_record_event(event_type, description)` helper
- `GET /api/events` endpoint
- Events auto-recorded in existing `/chaos` and `/chaos/reset` routes

**Frontend:** Timeline panel with vertical line connector, colored dots per event type, relative timestamps.

---

## Feature 3: In-Dashboard RCA Trigger

**What:** A "Run AI Analysis" button that triggers the Z-score + IQR anomaly analysis and displays results inline in the dashboard.

**How it works:**
- Reimplements the core logic from `root_cause_analyzer.py` directly in `app.py` (using stdlib `statistics` module)
- Queries Prometheus for each Golden Signal's recent data
- Computes Z-scores and IQR outlier detection
- Builds a causal chain (ordered by first anomaly time)
- Displays: anomalies found, causal chain arrows, likely root cause

**Backend:** `POST /api/rca` endpoint with inline analysis logic

**Frontend:** 
- Button in the dashboard
- Loading spinner while analysis runs
- Styled results panel showing anomalies and causal chain

---

## Feature 4: Dark/Light Theme Toggle

**What:** A toggle button (☀/☾) in the header that switches between dark and light themes, persisted in localStorage.

**How it works:**
- CSS variables for both themes under `:root` (dark, default) and `[data-theme='light']`
- JavaScript toggles `data-theme` attribute on `<html>`
- Preference saved to `localStorage['techstream-theme']`
- Applied before first paint to prevent flash

**Dark theme (unchanged):**
```css
--bg:#0b0e14; --panel:#141a24; --accent:#22d3ee;
```

**Light theme (new):**
```css
--bg:#f8fafc; --panel:#ffffff; --panel2:#f1f5f9; --txt:#1e293b;
```

---

## Feature 5: Visual Polish & Animations

**What:** Smooth transitions, hover effects, and micro-animations.

- **Theme transitions:** 0.3s ease on all color properties
- **Card hover:** `translateY(-2px)` + subtle glow shadow
- **Health score pulse:** Subtle animation when value changes
- **Top gradient bar:** 3px accent gradient line at the top of the page
- **Status pill animations:** Smooth color transitions on state changes

---

## Feature 6: Status API Endpoint

**What:** `GET /api/status` — a normalized JSON endpoint for external consumers.

**Response format:**
```json
{
  "status": "healthy",
  "health_score": 92,
  "uptime_seconds": 9240,
  "signals": {
    "traffic": {"value": 12.3, "status": "ok"},
    "error_rate": {"value": 0.01, "status": "ok"},
    ...
  },
  "chaos_active": false
}
```

---

## Feature 7: Uptime Display

**What:** Shows how long the app has been running in the header (e.g., "Up 2h 34m").

**How it works:**
- `_app_start_time = time.time()` captured at module load
- Uptime included in `/api/overview` response
- Frontend formats it as human-readable duration

---

## Feature 8: Manual Refresh Button

**What:** A circular refresh button (↻) in the header next to the theme toggle to trigger immediate dashboard metric updates.

**How it works:**
- Placed in the `<header>` element.
- Triggers the JS `refresh()` function immediately on click.
- Temporarily adds a CSS `.spinning` class to rotate the icon for a clean micro-animation during reload.

---

## How to View the Changes

1. The VM is accessible at the IP shown by: `multipass info techstream`
2. Open `http://<VM_IP>:5000` in your browser
3. All other services (Prometheus, Grafana, AlertManager) are also accessible

## How to Apply These Changes to Your Local Project (when ready)

```bash
# Copy the enhanced app.py from dev-notes to app/
cp dev-notes/enhanced-app.py app/app.py

# Rebuild and restart just the app container
docker compose up -d --build app
```

---

## VM Commands Reference

```bash
# Shell into the VM
multipass shell techstream

# Check VM IP
multipass info techstream | grep IPv4

# Stop VM (preserves state)
multipass stop techstream

# Start VM
multipass start techstream

# Destroy VM when done
multipass delete techstream && multipass purge
```
