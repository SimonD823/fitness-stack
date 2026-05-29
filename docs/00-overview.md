# Local AI Fitness Stack — Project Overview

## What You Are Building

A fully self-hosted fitness intelligence platform that combines your wearable data, strength training data, nutrition tracking, a local large language model, and a daily coaching email — all running on your own hardware, with zero cloud dependency after setup.

**Primary goal: 100,000 Steps Challenge — Saturday 29 August 2026** (~70–80 km in one day). The entire stack supports your 14-week preparation, event-day execution, and recovery analysis.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                          Your Network                               │
│                                                                     │
│   ┌──────────────────────────────────┐   ┌─────────────────────┐  │
│   │      NAS (192.168.1.60)          │   │  Max (192.168.1.50) │  │
│   │      QNAP TS873A                 │   │  MS-S1 Max          │  │
│   │                                  │   │                     │  │
│   │  InfluxDB :8086                  │◄──│  Ollama :11434    │  │
│   │    └─ GarminStats DB             │   │  Qwen3.6-27B        │  │
│   │    └─ CronometerStats DB         │──►│  (daily brief       │  │
│   │                                  │   │   calls LLM API)    │  │
│   │  Grafana :3000                   │   └─────────────────────┘  │
│   │  garmin-direct-sync (container)  │                             │
│   │  cronometer-sync (container)     │                             │
│   │  daily-brief (container)         │                             │
│   └──────────────────────────────────┘                             │
│              ▲                ▲                                     │
│   Garmin Connect API   Cronometer API                               │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Automated Data Flow

```
Every 30 minutes:
  Garmin Connect ──► garmin-direct-sync container ──► InfluxDB (GarminStats)
                     - All health metrics (steps, HRV, sleep, HR, stress)
                     - Strength sessions with exercise names from workout plan
                     - Exercise names mapped from Garmin workout plan descriptions
                       (e.g. SUSPENSION GLUTE_BRIDGE → Dumbbell Bench Glute Bridge)

06:00 AM daily (container):
  Cronometer.com ──► cronometer-sync ──► InfluxDB (CronometerStats)
                     (nutrition macros, calories, biometrics)

06:45 AM daily (container):
  InfluxDB (GarminStats + CronometerStats) ──► daily-brief container
    │
    ├── Queries yesterday's sleep, HRV, steps, activities, strength sets, nutrition
    ├── Loads current week's treadmill session from TREADMILL_TRAINING_GUIDE.md
    ├── Adapts session based on recovery data (HRV + sleep + body battery)
    ├──► Qwen3.6-27B API (192.168.1.50:11434)
    │      AI generates: READINESS | TODAY'S PLAN | KEY FOCUS
    │      Includes planned vs actual strength comparison (March 2026 Caliber plan)
    │
    └──► HTML email via Brevo API → simon_davies@hotmail.com
```

---

## Hardware Summary

| Device | Role | IP |
|--------|------|-----|
| QNAP TS873A (NAS) | InfluxDB, Grafana, all containers | 192.168.1.60 |
| Minisforum MS-S1 Max (Max) | Ollama + Qwen3.6-27B inference server | 192.168.1.50 |

**Max's AI specs:** 128 GB UMA RAM, Radeon 8060S iGPU. Qwen3.6-27B at Q6_K (~22.5 GB VRAM) runs entirely on-GPU.

---

## Ports Used

| Service | Host | Port | Access |
|---------|------|------|--------|
| InfluxDB HTTP API | NAS | 8086 | Internal (sync scripts) |
| Grafana Web UI | NAS | 3000 | Browser on local network |
| Ollama API | Max | 1234 | Daily brief + manual chat |

---

## Running Containers

| Container | Schedule | What it does |
|-----------|----------|-------------|
| `garmin-direct-sync` | Every 30 min | Garmin Connect → GarminStats InfluxDB |
| `cronometer-sync` | 06:00 AM daily | Cronometer → CronometerStats InfluxDB |
| `daily-brief` | 06:45 AM daily | Queries InfluxDB → AI analysis → email |

---

## Strength Training Data Pipeline

Strength sessions are recorded on the Fenix 8 using Garmin workout plan templates that match the Caliber March 2026 plan naming convention. The garmin-direct-sync container:

1. Detects strength activities via `activityType = strength_training`
2. Reads the `associatedWorkoutId` from the activity metadata
3. Fetches the Garmin workout plan and builds a name map from exercise descriptions
4. Uses those descriptions (e.g. `Dumbbell Bench Glute Bridge`) as exercise names in StrengthSets
5. Filters out rest markers (weight = -1) and Unknown entries

The AI coaching brief compares actual StrengthSets data against the embedded March 2026 Caliber plan, identifying warm-up sets (< 75% of session max weight) and flagging deviations from the 3×8-10 targets.

---

## Guide Index

| File | What It Covers |
|------|---------------|
| `00-overview.md` | This file — architecture, data flow, reading order |
| `01-nas-docker-stack.md` | Deploy InfluxDB + Grafana on QNAP; create databases |
| `02-garmin-sync.md` | garmin-direct-sync container: setup, auth, backfill |
| `03-cronometer-sync.md` | Cronometer export → InfluxDB pipeline |
| `04-ollama-qwen.md` | Ollama on Max: model download, API server, thinking mode |
| `05-ai-fitness-assistant.md` | System prompt, manual data queries, coaching prompts |
| `06-100k-steps-challenge.md` | 14-week training + nutrition plan for 29 Aug challenge |
| `07-coaching-emails-guide.md` | Automated daily coaching email via Brevo API |
| `08-gap-analysis.md` | Remaining gaps and enhancement opportunities |
| `Caliber_MCP_Integration_Guide.md` | Caliber MCP OAuth status + test procedures |
| `TREADMILL_TRAINING_GUIDE.md` | Week-by-week NordicTrack T5 session protocols |
| `garmin_notes_templates.txt` | Copy-paste exercise templates for Garmin Connect |
| `system_prompt.txt` | Current AI system prompt — paste into Ollama |

**Reading order for setup:** 01 → 02 → 03 → 04 → 07 → 05 → 06 → TREADMILL

---

## Scheduled Jobs Summary

| Time | Job | What it does |
|------|-----|-------------|
| Every 30 minutes | garmin-direct-sync (NAS container) | Garmin Connect → GarminStats including strength sets with correct exercise names |
| 06:00 AM daily | cronometer-sync (NAS container) | Cronometer nutrition → CronometerStats |
| 06:45 AM daily | daily-brief (NAS container) | Queries InfluxDB + calls Qwen3.6 → sends HTML daily coaching email |
| Monday 02:00 AM | daily-brief (NAS container) | Queries 7-day data → sends HTML weekly training report |

---

## Prerequisites

- QNAP Container Station 3.x installed and running
- SSH access enabled on NAS (Control Panel → Terminal & SNMP)
- Windows 11 Pro on Max with AMD Adrenalin drivers up to date
- Ollama installed on Max (ollama.ai)
- Garmin Connect account with Fenix 8 syncing
- Cronometer account with daily nutrition logging
- Brevo account (free tier) for email delivery
- Garmin workout plan templates created in Garmin Connect matching Caliber plan names

---

## Training Plan Context

- **Plan:** 14 weeks, started Monday 19 May 2026
- **Week structure:** Mon = Back & Shoulders, Tue = Chest & Arms, Fri = Legs & Abs
- **Treadmill:** Zone 2 sessions Mon/Tue/Fri adapted daily by AI based on HRV/sleep/body battery
- **No upper body strength:** until 1 June 2026 (post-lipoma surgery recovery)
- **Event:** Saturday 29 August 2026 — 100,000 Steps Challenge, ~70-80 km
