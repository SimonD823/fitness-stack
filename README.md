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
            └── NAS (always on)
                  ├── InfluxDB :8086      ← all health + training data
                  ├── Grafana :3000       ← dashboards
                  ├── garmin-direct-sync  ← Garmin Connect → InfluxDB (every 30 min)
                  ├── cronometer-sync     ← nutrition → InfluxDB (06:00 AM daily)
                  ├── daily-brief         ← AI coaching emails (06:45 AM daily and weekly Monday 2AM)
                  └── open-webui :3001    ← chat UI → Max LM Studio
                                                  │
                                          Max (on demand)
                                          └── LM Studio :1234
                                              └── Qwen3.6-27B
```

---

## Hardware

| Device | Role | Hostname |
|--------|------|----------|
| QNAP TS873A | NAS — InfluxDB, Grafana, all containers | `nas` |
| Minisforum MS-S1 Max | Max — LM Studio, Qwen3.6-27B inference | `max` |

---

## Quick Start

### 1. Configure environment

```bash
cp .env.example .env
# Edit .env with your actual values
```

### 2. Set up hostnames

On your router, assign `nas` to your NAS IP and `max` to your Max machine. Alternatively add entries to your NAS `/etc/hosts`.

### 3. Deploy containers on NAS

Copy each folder's `docker-compose.yml` to `\\nas\Container\<folder-name>\` via Windows Explorer and create the applications in Container Station:

- `garmin-direct-sync/docker-compose.yml`
- `cronometer-sync/docker-compose.yml`
- `daily-brief/docker-compose.yml`
- `open-webui/docker-compose.yml`

### 4. First-time Garmin authentication

```powershell
# Run on Max
cd scripts
python garmin_auth.py
# Copy generated tokens to \\nas\Container\garmin-direct-sync\tokens\
```

### 5. Set up LM Studio on Max

- Download and install LM Studio from `https://lmstudio.ai`
- Download `qwen/qwen3.6-27b` (Q6_K quantisation)
- Disable thinking mode (see Guide 04)
- Create a `Fitness-Coach` preset and paste `docs/system_prompt.txt` as the system prompt
- Start the API server on port 1234

### 6. Configure email (Brevo)

- Create a free account at `https://app.brevo.com`
- Add and verify your sender domain
- Get your API key from SMTP & API → API Keys
- Add `BREVO_API_KEY` and email settings to your `.env`

---

## Containers

| Container | Schedule | Purpose |
|-----------|----------|---------|
| `grafana` | Every 30 min | Presents data from InfluxDB (GarminStats\CronometerStats) |
| `influxdb` | Always on | Garmin Connect and Cronometer → InfluxDB (GarminStats\CronometerStats) |
| `garmin-direct-sync` | Every 30 min | Garmin Connect → InfluxDB (GarminStats) |
| `cronometer-sync` | 06:00 AM daily | Cronometer → InfluxDB (CronometerStats) |
| `daily-brief` | 07:00 AM daily | Queries InfluxDB → AI coaching → email |
| `open-webui` | Always on | Chat UI connecting to LM Studio on Max |

---

## Guides

| Guide | Contents |
|-------|---------|
| [00-overview.md](docs/00-overview.md) | Architecture, data flow, reading order |
| [01-nas-docker-stack.md](docs/01-nas-docker-stack.md) | InfluxDB + Grafana setup on QNAP |
| [02-garmin-sync.md](docs/02-garmin-sync.md) | garmin-direct-sync container + workout plan name mapping |
| [03-cronometer-sync.md](docs/03-cronometer-sync.md) | Cronometer nutrition pipeline |
| [04-lmstudio-qwen.md](docs/04-lmstudio-qwen.md) | LM Studio + Qwen3.6-27B + thinking mode |
| [05-ai-fitness-assistant.md](docs/05-ai-fitness-assistant.md) | System prompt + manual coaching queries |
| [06-100k-steps-challenge.md](docs/06-100k-steps-challenge.md) | Event preparation guide |
| [07-daily-brief-guide.md](docs/07-daily-brief-guide.md) | Daily + weekly coaching email setup |
| [08-gap-analysis.md](docs/08-gap-analysis.md) | Known gaps + enhancement roadmap |
| [09-open-webui-remote-access.md](docs/09-open-webui-remote-access.md) | Open WebUI + Tailscale remote access |
| [Caliber_MCP_Integration_Guide.md](docs/Caliber_MCP_Integration_Guide.md) | Caliber MCP integration (optional) |
| [TRAINING_PLAN_V2.md](docs/TRAINING_PLAN_V2.md) | 13-week training plan with hip mobility + footwear |
| [garmin_notes_templates.txt](docs/garmin_notes_templates.txt) | Exercise name templates for Garmin Connect |
| [system_prompt.txt](docs/system_prompt.txt) | AI coaching system prompt for LM Studio |

**Reading order for setup:** 01 → 02 → 03 → 04 → 07 → 05 → 06 → TRAINING_PLAN_V2

---

## Strength Training

Sessions are recorded on the Garmin Fenix 8 using workout plan templates that match your Caliber plan:

| Day | Workout |
|-----|---------|
| Monday | `(Gym) Legs & Abs` |
| Wednesday | `(Gym) Back & Shoulders` |
| Friday | `(Gym) Chest & Arms` |

The `garmin-direct-sync` script automatically reads the Garmin workout plan description field and maps exercise codes (e.g. `SUSPENSION GLUTE_BRIDGE`) to human-readable Caliber names (e.g. `Dumbbell Bench Glute Bridge`). No manual data entry needed.

See `docs/garmin_notes_templates.txt` for the correct exercise names to use in your Garmin workout plan descriptions.

---

## Weekly Structure (6-day plan from 1 June 2026)

| Day | Session |
|-----|---------|
| Monday | Caliber: Legs & Abs + short walk (~5,000 steps) |
| Tuesday | Long Zone 2 walk (70+ min, ~8,000 steps) |
| Wednesday | Caliber: Back & Shoulders + short walk |
| Thursday | Long Zone 2 walk (70+ min, ~8,000 steps) |
| Friday | Caliber: Chest & Arms + short walk |
| Saturday | Primary long walk (progressively increasing) |
| Sunday | Rest |

---

## Security

- All secrets stored in `.env` — never committed to git
- `.gitignore` excludes `.env`, tokens, logs, and generated files
- See `.env.example` for all required variables

---

## Repo Structure

```
fitness-stack/
├── .env.example                       # Template — copy to .env
├── .gitignore
├── README.md
├── docs/
│   ├── 00-overview.md
│   ├── 01-nas-docker-stack.md
│   ├── 02-garmin-sync.md
│   ├── 03-cronometer-sync.md
│   ├── 04-lmstudio-qwen.md
│   ├── 05-ai-fitness-assistant.md
│   ├── 06-100k-steps-challenge.md
│   ├── 07-daily-brief-guide.md
│   ├── 08-gap-analysis.md
│   ├── 09-open-webui-remote-access.md
│   ├── Caliber_MCP_Integration_Guide.md
│   ├── TRAINING_PLAN_V2.md
│   ├── garmin_notes_templates.txt
│   └── system_prompt.txt
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
└── scripts/
    └── garmin_auth.py                 # First-time Garmin authentication
```
