# Local AI Fitness Stack — Project Overview

## What You Are Building

A fully self-hosted fitness intelligence platform combining Garmin wearable data, strength training tracking, nutrition monitoring, a local LLM, and automated coaching emails — all running on your own hardware with zero cloud dependency after setup.

**Primary goal:** 100,000 Steps Challenge — Saturday 29 August 2026 (~70–80 km).

---

## Architecture

```
Remote (iPhone/iPad)
    │
    └── Tailscale VPN
            │
            └── NAS (192.168.1.60) — always on
                  ├── InfluxDB :8086        ← all health + training data
                  ├── Grafana :3000         ← dashboards + strength panels
                  ├── garmin-direct-sync    ← Garmin Connect → InfluxDB (every 30 min)
                  ├── cronometer-sync       ← nutrition → InfluxDB (01:00 AM daily)
                  ├── daily-brief           ← coaching emails (08:00 AM + Mon 07:00 AM)
                  ├── open-webui :3001      ← chat UI → Ollama on Max
                  ├── training-dashboard :3002 ← planned vs actual training view
                  └── caliber-sync          ← Caliber → InfluxDB (01:00 AM daily)
                                                    │
                                            Max (192.168.1.50) — auto-starts on boot
                                            └── Ollama :11434
                                                └── fitness-coach (Qwen3.6-27B Q6_K)
```

---

## Hardware

| Device | Role | Hostname | IP |
|--------|------|----------|----|
| QNAP TS873A | NAS — all containers + data | `nas` | 192.168.1.60 |
| Minisforum MS-S1 Max | Max — Ollama inference | `max` | 192.168.1.50 |

**Max specs:** AMD Ryzen AI Max+, 128GB UMA RAM, Radeon 8060S iGPU. Qwen3.6-27B Q6_K runs entirely on GPU (~23GB VRAM).

---

## Automated Data Flow

```
Every 30 minutes:
  Garmin Connect → garmin-direct-sync → GarminStats (InfluxDB)
    - All health metrics (steps, HRV, sleep, HR, stress, body battery)
    - Strength sessions with Caliber exercise names via workout plan mapping

01:00 AM daily:
  Cronometer.com → cronometer-sync → CronometerStats (InfluxDB)
  Caliber app → caliber-sync (Anthropic API + MCP) → CaliberStats (InfluxDB)

08:00 AM daily (Mon-Sat):
  Data freshness check → wait for Garmin sync → daily coaching email via Ollama

07:00 AM Monday:
  Wait for fresh data → weekly training report email via Ollama

18:00 UTC (19:00 BST) daily (Mon-Sat):
  Check today's steps vs target → send nudge email if 2,000+ steps short

09:00 UTC Monday:
  Check Garmin token age → alert if tokens older than 25 days

Every hour at :30:
  Check all containers running → alert email if any down
```

---

## Containers & Applications

5 Container Station applications (7 containers total):

| Application | Container(s) | Schedule | Purpose |
|-------------|-------------|----------|---------|
| `fitness-stack` | `influxdb` + `grafana` | Always on | Core data stack |
| `garmin-direct-sync` | `garmin-direct-sync` | Every 30 min | Garmin → InfluxDB |
| `cronometer-sync` | `cronometer-sync` | 01:00 AM daily | Nutrition → InfluxDB |
| `daily-brief` | `daily-brief` | Multiple schedules | All coaching emails + monitoring |
| `open-webui` | `open-webui` | Always on | Chat UI → Ollama |
| `training-dashboard` | `training-dashboard` | Always on | Planned vs actual view :3002 |
| `caliber-sync` | `caliber-sync` | 01:00 AM daily | Caliber → InfluxDB via Anthropic API |

---

## Daily Brief — Email Schedule

| Time UTC | BST | What fires |
|----------|-----|-----------|
| 06:30 Mon | 07:30 | Garmin token age check |
| 07:00 Mon | 08:00 | Weekly training report (waits for fresh data, timeout 09:00) |
| 08:00 daily | 09:00 | Daily coaching brief (waits for fresh data, timeout 09:00) |
| 18:00 Mon-Sat | 19:00 | Step nudge if 2,000+ short of daily target |
| Every hour :30 | — | Container health check |

---

## Ports

| Service | URL | Notes |
|---------|-----|-------|
| InfluxDB | `http://nas:8086` | Data API |
| Grafana | `http://nas:3000` | Dashboards |
| Open WebUI | `http://nas:3001` | Chat with fitness-coach model |
| Training Dashboard | `http://nas:3002` | Planned vs actual training |
| Ollama API | `http://max:11434` | LLM inference |

---

## Strength Training Data Pipeline

Strength sessions recorded on Fenix 8 using Garmin workout plan templates:

| Day | Garmin Template | Caliber Plan |
|-----|-----------------|-------------|
| Monday | `(Gym) Legs & Abs` | Legs & Abs |
| Wednesday | `(Gym) Back & Shoulders` | Back & Shoulders |
| Friday | `(Gym) Chest & Arms` | Chest & Arms |

The `garmin-direct-sync` container reads the `associatedWorkoutId` from each activity, fetches the Garmin workout plan, and maps Garmin exercise codes (e.g. `SUSPENSION GLUTE_BRIDGE`) to Caliber names (e.g. `Dumbbell Bench Glute Bridge`).

`dashboard.py` additionally normalises any raw Garmin enum names (e.g. `ROW BENT_OVER_ROW_WITH_DUMBBELL`) to canonical Caliber plan names via the `normalize_exercise()` function, so day cards, modals, and Actual-vs-Plan matching all use consistent names regardless of data source.

Grafana strength progression panels visualise exercise weights over time — see `grafana_strength_panels.md`.

---

## Training Dashboard (:3002) — Key Behaviours

The Flask training dashboard at `http://nas:3002` shows planned vs actual sessions for the current week with click-to-expand modals. Key implementation details:

**InfluxDB query windows by data type:**

| Measurement | Window used | Reason |
|-------------|-------------|--------|
| DailyStats, ActivitySummary, StrengthSets, CoachNotes | `07:00Z today → 07:00Z tomorrow` | Synced during the day |
| SleepSummary / HRV | `20:00Z previous day → 07:00Z tomorrow` | Garmin writes sleep at ~21:00Z previous night |
| BodyComposition | `00:00Z today → 00:00Z tomorrow` | Garmin writes at midnight UTC |

**Exercise name normalisation:** `normalize_exercise()` maps raw Garmin enums to Caliber plan names. Unknown exercises are title-cased rather than shown as ALL_CAPS_UNDERSCORED.

**Coach Override integration:** The dashboard reads `CoachNotes` from InfluxDB and surfaces today's coaching decision on the day card (amber badges) and in the modal (coloured pill badges + italic note text). Structured fields (`caliber_cancelled`, `vo2_cancelled`, `walk_cap_mins`, `step_cap`, `readiness_class`) are written by `daily_brief.py` and read by the dashboard — no free-text parsing required.

**Walk modal:** Shows target steps + target duration (from plan), plus actual steps + actual duration from logged Garmin session if available.

---

## CoachNotes — Structured Override Fields

`daily_brief.py` writes to the `CoachNotes` InfluxDB measurement each morning. Fields written:

| Field | Type | Description |
|-------|------|-------------|
| `note` | string | Human-readable coaching decision (free text) |
| `date` | string | e.g. `Monday 22 June 2026` |
| `readiness_class` | string | `Fully recovered` / `Partially recovered` / `Suppressed` / `Red-flag (do not train)` |
| `caliber_cancelled` | int | `1` if Caliber cancelled today, `0` otherwise |
| `vo2_cancelled` | int | `1` if VO₂ Max session cancelled today, `0` otherwise |
| `walk_cap_mins` | int | Walk duration cap in minutes (`-1` = not applicable) |
| `step_cap` | int | Step ceiling for today (`-1` = not applicable) |

**Re-run behaviour:** `store_coach_note()` deletes all existing `CoachNotes` entries for the current calendar day before writing, so running the daily brief multiple times never leaves conflicting notes. Only the most recent run's note persists.

---

## Guide Index

| File | Contents |
|------|---------|
| `00-overview.md` | This file |
| `01-nas-docker-stack.md` | InfluxDB + Grafana setup on QNAP |
| `02-garmin-sync.md` | garmin-direct-sync + workout plan name mapping |
| `03-cronometer-sync.md` | Cronometer nutrition pipeline |
| `04-ollama-qwen.md` | Ollama + Qwen3.6-27B on Max (auto-login, Task Scheduler) |
| `05-ai-fitness-assistant.md` | System prompt + manual coaching queries |
| `06-100k-steps-challenge.md` | Event preparation and nutrition guide |
| `07-coaching-emails-guide.md` | Daily + weekly coaching email setup |
| `08-gap-analysis.md` | Current status + remaining items |
| `09-open-webui-remote-access.md` | Open WebUI + Tailscale remote access |
| `10-influxdb-backup.md` | Weekly InfluxDB backup to NAS |
| `grafana_strength_panels.md` | Strength progression panels in Grafana |
| `Caliber_MCP_Integration_Guide.md` | Caliber sync via Anthropic API |
| `TRAINING_PLAN_V2.md` | 13-week training plan + hip mobility + footwear |
| `system_prompt.txt` | AI coaching system prompt (loaded into Ollama Modelfile) |
| `garmin_notes_templates.txt` | Exercise name templates for Garmin Connect |

**Reading order for setup:** 01 → 02 → 03 → 04 → 07 → 05 → 06 → TRAINING_PLAN_V2

---

## Key Dates

| Date | Milestone |
|------|-----------|
| 1 June 2026 | Caliber gym sessions resume, 13-week plan starts |
| 29 June 2026 | Week 5 — Saturday walks reach 150 min |
| 3-9 August 2026 | Week 10 — Dress rehearsal week |
| 17 August 2026 | Taper begins |
| 24 August 2026 | Event week — no Caliber, minimal training |
| **29 August 2026** | **100,000 Steps Challenge — Peddars Way** |

---

## Reminders — Complete Before August

- 📋 **Event day dashboard** (Grafana) — build by early August
- 📋 **Pre-event brief** — special AI brief for 28 August with pacing/gel strategy
- 📋 **HRV baseline** — recalibrate after 4 weeks of training data (late June)
- 📋 **Post-event recovery plan** — build in August
