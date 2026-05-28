# Local AI Fitness Stack

A fully self-hosted fitness intelligence platform combining Garmin wearable data, strength training tracking, nutrition monitoring, and a local LLM — all running on your own hardware.

**Primary goal:** 100,000 Steps Challenge — Saturday 29 August 2026 (~70–80 km).

---

## Hardware

| Device | Role | Hostname |
|--------|------|----------|
| QNAP TS873A | NAS — InfluxDB, Grafana, containers | `nas` |
| Minisforum MS-S1 Max | Max — LM Studio, Qwen3.6-27B | `max` |

---

## Quick Start

### 1. Configure environment

```bash
cp .env.example .env
# Edit .env with your actual values
```

### 2. Set up NAS hostname

On your router, assign the hostname `nas` to `192.168.1.x` (your NAS IP) and `max` to your Max machine. Or add entries to your NAS `/etc/hosts`.

### 3. Deploy containers

Copy each `docker-compose.yml` to the NAS via Windows Explorer (`\\nas\Container\`) and create the applications in Container Station.

### 4. First-time Garmin auth

```powershell
# On Max
python garmin_auth.py
# Copy generated tokens to \\nas\Container\garmin-direct-sync\tokens\
```

### 5. Load LM Studio

- Open LM Studio on Max
- Load `qwen/qwen3.6-27b` (Q6_K)
- Disable thinking mode (see Guide 04)
- Paste `system_prompt.txt` into the Fitness-Coach preset
- Start the API server on port 1234

---

## Containers

| Container | Schedule | Purpose |
|-----------|----------|---------|
| `garmin-direct-sync` | Every 30 min | Garmin Connect → InfluxDB (GarminStats) |
| `cronometer-sync` | 06:00 AM | Cronometer → InfluxDB (CronometerStats) |
| `daily-brief` | 06:45 AM | Queries InfluxDB → AI coaching → email |

---

## Guides

| Guide | Contents |
|-------|---------|
| [00-overview.md](docs/00-overview.md) | Architecture, data flow, reading order |
| [01-nas-docker-stack.md](docs/01-nas-docker-stack.md) | InfluxDB + Grafana setup on QNAP |
| [02-garmin-sync.md](docs/02-garmin-sync.md) | garmin-direct-sync container |
| [03-cronometer-sync.md](docs/03-cronometer-sync.md) | Cronometer nutrition pipeline |
| [04-lmstudio-qwen.md](docs/04-lmstudio-qwen.md) | LM Studio + Qwen3.6-27B setup |
| [05-ai-fitness-assistant.md](docs/05-ai-fitness-assistant.md) | System prompt + manual coaching |
| [06-100k-steps-challenge.md](docs/06-100k-steps-challenge.md) | 14-week training plan |
| [07-coaching-emails-guide.md](docs/07-coaching-emails-guide.md) | Daily coaching email setup |
| [08-gap-analysis.md](docs/08-gap-analysis.md) | Known gaps + enhancements |
| [Caliber_MCP_Integration_Guide.md](docs/Caliber_MCP_Integration_Guide.md) | Caliber MCP (optional) |

---

## Strength Training

Strength sessions are recorded on the Fenix 8 using Garmin workout plan templates:
- `(Gym) Back & Shoulders` — Mondays
- `(Gym) Chest & Arms` — Tuesdays  
- `(Gym) Legs & Abs` — Fridays

The sync script automatically maps Garmin exercise codes to Caliber exercise names using the workout plan descriptions. See `garmin_notes_templates.txt` for the correct exercise names.

---

## Security

- All secrets are stored in `.env` (never committed)
- `.gitignore` excludes `.env`, tokens, and logs
- See `.env.example` for required variables

---

## Key Files

```
├── .env.example                    # Template — copy to .env
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
│   ├── 07-coaching-emails-guide.md
│   ├── 08-gap-analysis.md
│   ├── Caliber_MCP_Integration_Guide.md
│   ├── TREADMILL_TRAINING_GUIDE.md
│   ├── garmin_notes_templates.txt
│   └── system_prompt.txt
├── garmin-direct-sync/
│   ├── docker-compose.yml
│   └── garmin_direct_sync.py
├── daily-brief/
│   ├── docker-compose.yml
│   └── daily_brief.py
└── cronometer-sync/
    └── docker-compose.yml
```
