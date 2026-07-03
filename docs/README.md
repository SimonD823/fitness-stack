# Local AI Fitness Stack

A fully self-hosted fitness intelligence platform combining Garmin wearable data, strength training tracking, nutrition monitoring, a local LLM, and automated daily coaching emails — all running on your own hardware with no cloud dependency after setup.

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

---

## Quick Start

### 1. Configure environment

```bash
cp .env.example .env
# Edit .env with your actual values
```

### 2. Deploy containers on NAS

Copy each folder's `docker-compose.yml` to `\\nas\Container\<folder-name>\` via Windows Explorer and create the applications in Container Station:

- `fitness-stack/docker-compose.yml` — deploys InfluxDB + Grafana
- `garmin-direct-sync/docker-compose.yml`
- `cronometer-sync/docker-compose.yml`
- `daily-brief/docker-compose.yml`
- `open-webui/docker-compose.yml`
- `training-dashboard/docker-compose.yml`

### 3. First-time Garmin authentication

```powershell
# Run on Max
cd scripts
python garmin_auth.py
# Copy generated tokens to \\nas\Container\garmin-direct-sync\tokens\
```

### 4. Set up Ollama on Max

- Download and install Ollama from `https://ollama.ai`
- Pull the model: `ollama pull batiai/qwen3.6-27b:q6`
- Create `fitness-coach` model from Modelfile (see Guide 04)
- Configure auto-start via Task Scheduler (see Guide 04)

### 5. Configure email (Brevo)

- Create a free account at `https://app.brevo.com`
- Add and verify your sender domain
- Get your API key from SMTP & API → API Keys
- Add `BREVO_API_KEY` and email settings to your `.env`

---

## Containers & Applications

The stack runs as 6 Container Station applications (7 containers total):

| Application | Container(s) | Schedule | Purpose |
|-------------|-------------|----------|---------|
| `fitness-stack` | `influxdb` + `grafana` | Always on | Core data stack — InfluxDB :8086 + Grafana :3000 |
| `garmin-direct-sync` | `garmin-direct-sync` | Every 30 min | Garmin Connect → InfluxDB (GarminStats) |
| `cronometer-sync` | `cronometer-sync` | 01:00 AM daily | Cronometer → InfluxDB (CronometerStats) |
| `daily-brief` | `daily-brief` | 08:00 AM daily + Mon 07:00 AM | Daily coaching email + weekly training report |
| `open-webui` | `open-webui` | Always on | Web chat UI → Ollama on Max |
| `training-dashboard` | `training-dashboard` | Always on | Planned vs actual training view :3002 |
| `caliber-sync` | `caliber-sync` | 01:00 AM daily | Caliber → InfluxDB via Anthropic API |

---

## Training Dashboard (:3002)

The Flask training dashboard at `http://nas:3002` shows the current week's planned vs actual sessions. Click any day card to open a modal with full detail.

**Key features:**
- Day cards show steps, HRV, sleep, and Caliber exercises with correct canonical names
- Walk modals show both target duration (from plan) and actual duration (from Garmin session)
- Coach Override section surfaces today's readiness class and any modifications (Caliber cancelled, walk capped, step ceiling) as coloured pill badges
- Amber badges on day cards when coach has cancelled or capped a session
- "No gym data" warning suppressed on days where Caliber was coach-cancelled
- Weight trend box shows start/end/change for the current week

**InfluxDB query windows used:**

| Data type | Window | Why |
|-----------|--------|-----|
| DailyStats, ActivitySummary, StrengthSets, CoachNotes | `07:00Z → 07:00Z` | Synced during the day |
| SleepSummary / HRV | `20:00Z previous day → 07:00Z tomorrow` | Written at ~21:00Z previous night |
| BodyComposition | `00:00Z → 00:00Z` midnight boundaries | Written at exactly midnight UTC |

---

## Guides

| Guide | Contents |
|-------|---------|
| [00-overview.md](docs/00-overview.md) | Architecture, data flow, CoachNotes schema, reading order |
| [01-nas-docker-stack.md](docs/01-nas-docker-stack.md) | InfluxDB + Grafana setup on QNAP |
| [02-garmin-sync.md](docs/02-garmin-sync.md) | garmin-direct-sync + timestamp patterns + SYNC_DAYS_BACK semantics |
| [03-cronometer-sync.md](docs/03-cronometer-sync.md) | Cronometer nutrition pipeline |
| [04-ollama-qwen.md](docs/04-ollama-qwen.md) | Ollama + Qwen3.6-27B + auto-start |
| [05-ai-fitness-assistant.md](docs/05-ai-fitness-assistant.md) | System prompt + manual coaching queries |
| [06-100k-steps-challenge.md](docs/06-100k-steps-challenge.md) | Event preparation guide |
| [07-coaching-emails-guide.md](docs/07-coaching-emails-guide.md) | Daily + weekly coaching email + CoachNotes + Brevo |
| [08-gap-analysis.md](docs/08-gap-analysis.md) | Status + known issues + remaining items |
| [09-open-webui-remote-access.md](docs/09-open-webui-remote-access.md) | Open WebUI + Tailscale remote access |
| [10-influxdb-backup.md](docs/10-influxdb-backup.md) | Weekly InfluxDB backup |
| [grafana_strength_panels.md](docs/grafana_strength_panels.md) | Strength progression panels in Grafana |
| [Caliber_MCP_Integration_Guide.md](docs/Caliber_MCP_Integration_Guide.md) | Caliber MCP integration |
| [TRAINING_PLAN_V2.md](docs/TRAINING_PLAN_V2.md) | 13-week training plan with hip mobility + footwear |
| [garmin_notes_templates.txt](docs/garmin_notes_templates.txt) | Exercise name templates for Garmin Connect |
| [system_prompt.txt](docs/system_prompt.txt) | AI coaching system prompt for Ollama |

**Reading order for setup:** 01 → 02 → 03 → 04 → 07 → 05 → 06 → TRAINING_PLAN_V2

---

## Strength Training

Sessions are recorded on the Garmin Fenix 8 using workout plan templates that match the Caliber plan:

| Day | Workout |
|-----|---------|
| Monday | `(Gym) Legs & Abs` |
| Wednesday | `(Gym) Back & Shoulders` |
| Friday | `(Gym) Chest & Arms` |

The `garmin-direct-sync` script automatically reads the Garmin workout plan description field and maps exercise codes to human-readable Caliber names. The training dashboard additionally applies `normalize_exercise()` at read time to handle any edge cases where raw Garmin enum names slipped through.

---

## Weekly Structure (13-week plan from 1 June 2026)

| Day | Session |
|-----|---------|
| Monday | Caliber: Legs & Abs + short walk (~5,000 steps) |
| Tuesday | Long Zone 2 walk (treadmill or outdoor, 70+ min, ~8,000 steps) |
| Wednesday | Caliber: Back & Shoulders + short walk |
| Thursday | Long Zone 2 walk (treadmill or outdoor, 70+ min, ~8,000 steps) |
| Friday | Caliber: Chest & Arms + short walk |
| Saturday | Primary long walk (progressively increasing to 4+ hours) |
| Sunday | Rest |

---

## Key Implementation Details

### Exercise Name Normalisation

`normalize_exercise()` in `dashboard.py` maps Garmin raw enum strings to canonical Caliber plan names using keyword rules. First match wins. Canonical names pass through unchanged. Unknown exercises are title-cased. This runs at read time so no historical data needs rewriting.

### CoachNotes Structured Fields

`daily_brief.py` writes both free-text and machine-readable structured fields to `CoachNotes` each morning. `store_coach_note()` deletes any existing entries for the current day before writing — re-running the daily brief never produces duplicate or conflicting notes.

### InfluxDB Timestamp Constraints

- DELETE requires nanosecond integer timestamps, not RFC3339 strings
- Cannot mix aggregate and non-aggregate fields in one query
- `Activity_ID` is a field not a tag — tag-based DELETE syntax fails
- Regex patterns must be pure ASCII (degree symbol breaks queries silently)
- `GROUP BY tag` requires direct series key iteration, not `get_points()`

---

## Repo Structure

```
fitness-stack/
├── .env.example
├── .gitignore
├── README.md
├── docs/
│   ├── 00-overview.md
│   ├── 01-nas-docker-stack.md
│   ├── 02-garmin-sync.md
│   ├── 03-cronometer-sync.md
│   ├── 04-ollama-qwen.md
│   ├── 05-ai-fitness-assistant.md
│   ├── 06-100k-steps-challenge.md
│   ├── 07-coaching-emails-guide.md
│   ├── 08-gap-analysis.md
│   ├── 09-open-webui-remote-access.md
│   ├── 10-influxdb-backup.md
│   ├── Caliber_MCP_Integration_Guide.md
│   ├── TRAINING_PLAN_V2.md
│   ├── garmin_notes_templates.txt
│   ├── grafana_strength_panels.md
│   └── system_prompt.txt
├── fitness-stack/
│   └── docker-compose.yml
├── garmin-direct-sync/
│   ├── docker-compose.yml
│   └── garmin_direct_sync.py
├── cronometer-sync/
│   ├── docker-compose.yml
│   └── cronometer_sync.py
├── daily-brief/
│   ├── docker-compose.yml
│   └── daily_brief.py
├── open-webui/
│   └── docker-compose.yml
├── training-dashboard/
│   ├── docker-compose.yml
│   └── dashboard.py
└── scripts/
    └── garmin_auth.py
```
