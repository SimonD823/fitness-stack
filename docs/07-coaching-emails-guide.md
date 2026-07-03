# Guide 07 — Daily Training Brief Email

Runs on the NAS every morning at 08:00 AM BST. Queries InfluxDB for today's health data, calls Ollama on Max for an AI coaching analysis, and emails an HTML daily brief via Brevo.

**Email contains:**
- AI coaching readiness assessment and today's training plan
- Colour-coded readiness badge (Fully recovered / Partially recovered / Suppressed / Red-flag)
- Yesterday's sessions (cardio + strength detail)
- Sleep metrics (score, HRV, body battery)
- Nutrition summary (calories, macros from Cronometer)
- Step ceiling if applicable (pre-computed deterministically in Python, not by the LLM)

---

## Architecture

```
daily_brief.py (NAS container)
    │
    ├── InfluxDB → fetch today's metrics
    ├── classify_readiness() → deterministic Python verdict
    ├── build_prompt() → inject verdicts + consequences + step ceiling
    ├── Ollama (Max :11434) → AI narrative
    ├── extract_coach_note() → second Ollama call for structured summary
    ├── store_coach_note() → write to CoachNotes (InfluxDB)
    │     └── deletes existing today's notes first (idempotent re-runs)
    └── Brevo API → send HTML email
```

---

## Readiness Classification

`classify_readiness()` is a deterministic Python function — the LLM does **not** reclassify readiness. HRV delta vs 30-day baseline is the hard gate:

| Class | Criteria |
|-------|----------|
| Red-flag (do not train) | HRV ≥25% below baseline |
| Suppressed | HRV 10–24% below baseline |
| Partially recovered | HRV mildly suppressed (5–9%) OR sleep score 50–69 OR body battery 40–59 |
| Fully recovered | HRV at/above baseline, sleep ≥70, body battery ≥60 |

The consequence for each class (what to do today) is also pre-computed in Python, injected into the prompt, and the LLM is instructed not to override it.

---

## CoachNotes — Structured Override Fields

Each daily brief run writes one entry to `CoachNotes` in InfluxDB. The entry includes both free-text (`note`) and structured fields for machine consumption by the training dashboard:

| Field | Description |
|-------|-------------|
| `note` | Human-readable coaching decision summary |
| `date` | e.g. `Monday 22 June 2026` |
| `readiness_class` | Verbatim from `classify_readiness()` |
| `caliber_cancelled` | `1` if Caliber cancelled today |
| `vo2_cancelled` | `1` if VO₂ Max cancelled today |
| `walk_cap_mins` | Walk duration cap in minutes (`-1` = not applicable) |
| `step_cap` | Step ceiling for today (`-1` = not applicable) |

**Idempotent re-runs:** `store_coach_note()` deletes all existing `CoachNotes` entries for the current calendar day before writing. Running the daily brief multiple times (e.g. after fixing a bug) leaves only the most recent note — no conflicting entries.

> **Note:** Deletion uses nanosecond integer timestamps internally (InfluxDB 1.x requires this for DELETE WHERE — RFC3339 string timestamps are not accepted in DELETE syntax).

---

## Step 1 — Create the directory in File Station

Open **File Station** → `Container`. Create:

```
Container / daily-brief
```

Copy `daily_brief.py` into `Container / daily-brief /` via Windows Explorer (`\\192.168.1.60\Container\daily-brief`).

---

## Step 2 — Configure Brevo (email provider)

The daily brief sends email via the Brevo transactional API — not SMTP. No app password needed.

1. Create a free account at `https://app.brevo.com`
2. Add and verify your sender domain (`everything-ai.info` is already verified)
3. Go to **SMTP & API → API Keys** and create a key
4. Copy the key — it starts with `xkeysib-`

The sender address is `ai-fitness-coach@everything-ai.info`. The recipient is `simon@everything-virtual.com`.

---

## Step 3 — Create the Docker Compose file

In File Station, create `docker-compose.yml` in `Container / daily-brief /`:

```yaml
version: "3.8"

services:
  daily-brief:
    image: python:3.12-slim
    container_name: daily-brief
    restart: unless-stopped
    environment:
      INFLUX_HOST: "192.168.1.60"
      INFLUX_PORT: "8086"
      INFLUX_USER: "influxdb_user"
      INFLUX_PASS: "influxdb_secret_password"
      OLLAMA_URL: "http://192.168.1.50:11434"
      OLLAMA_MODEL: "fitness-coach"
      BREVO_API_KEY: "YOUR_BREVO_API_KEY"
      EMAIL_FROM: "ai-fitness-coach@everything-ai.info"
      EMAIL_TO: "simon@everything-virtual.com"
      TRAINING_WEEK: "4"
      RUN_NOW: "false"
    volumes:
      - /share/Container/daily-brief/daily_brief.py:/app/daily_brief.py
    network_mode: host
    command: >
      bash -c "pip install --no-cache-dir requests influxdb --quiet &&
               python /app/daily_brief.py"
```

> **TRAINING_WEEK** — update this manually each Monday. The script uses it to determine step ceilings, walk durations, and Caliber set counts appropriate for the current week of the 13-week plan.

---

## Step 4 — Create the application in Container Station

Open **Container Station → Applications → Create**.

- **Application Name:** `daily-brief`
- Paste the compose YAML
- Click **Validate YAML → Create**

---

## Step 5 — Test immediately

To send a test email right now, temporarily change `RUN_NOW: "false"` to `RUN_NOW: "true"` in the compose YAML, delete and recreate the application, check your inbox, then set it back to `"false"`.

---

## Step 6 — Update training week each Monday

Each Monday morning, edit the compose YAML in File Station and increment `TRAINING_WEEK` by 1, then restart the application in Container Station.

| Week | Dates | TRAINING_WEEK value |
|------|-------|---------------------|
| 1 | 1–7 Jun | 1 |
| 2 | 8–14 Jun | 2 |
| 3 | 15–21 Jun | 3 |
| 4 | 22–28 Jun | 4 |
| 5 | 29 Jun – 5 Jul | 5 |
| 6 | 6–12 Jul | 6 |
| 7 | 13–19 Jul | 7 |
| 8 | 20–26 Jul | 8 |
| 9 | 27 Jul – 2 Aug | 9 |
| 10 | 3–9 Aug | 10 |
| 11 | 10–16 Aug | 11 |
| 12 | 17–23 Aug | 12 |
| 13 | 24–29 Aug | 13 |
| **Event** | **Saturday 29 Aug** | — |

---

## Troubleshooting

**Email not arriving:**
- Check Container Station Logs for errors
- Verify the Brevo API key is correct and the sender domain is verified
- Confirm `EMAIL_FROM` and `EMAIL_TO` are correct

**"LM Studio/Ollama unreachable" in email:**
- Max must be on and logged in
- Ollama must be running — check `http://192.168.1.50:11434/api/tags` from browser
- The `fitness-coach` model must exist: check with `ollama list` on Max

**Multiple conflicting CoachNotes for same day:**
- This is fixed in the current version — `store_coach_note()` deletes existing entries before writing
- To manually clear stale entries from before the fix was applied:
  ```bash
  curl "http://192.168.1.60:8086/query?u=admin&p=adminSecretPassword&db=GarminStats&q=SELECT+time,note+FROM+CoachNotes+WHERE+time>='2026-06-22T00:00:00Z'+AND+time<'2026-06-23T00:00:00Z'"
  # Note the nanosecond timestamps, then delete by exact timestamp
  ```

**"invalid operation: time and *influxql.StringLiteral are not compatible":**
- This error occurs if DELETE uses an RFC3339 string timestamp. The current code converts to nanosecond integer before DELETE — if you see this error, ensure you are running the latest `daily_brief.py`.

**TRAINING_WEEK is wrong:**
- Edit the compose YAML in File Station → change the number → restart the container

---

## Weekly Training Report

The `daily-brief` container also sends a weekly training report automatically every **Monday at 07:00 AM UTC**. No additional setup is needed.

### What the weekly report includes

- **3 summary cards** — Training compliance %, total steps, weight change (colour coded)
- **AI narrative** — Week Summary, Highlights, Areas to Address, Strength Report, Weight & Nutrition, Compliance, Coming Week
- **Activity & Compliance table** — this week vs last week comparison
- **Sleep & Recovery table** — average HRV, sleep score, resting HR
- **Weight Trend** — start weight, end weight, change vs -0.8 to -1.0 kg/week target
- **Nutrition vs Targets** — average daily calories, protein, carbs, fat vs targets
- **Strength Progression** — max weight per exercise with PR badges
- **Coach Notes** — daily coaching decisions from the week, sourced from CoachNotes InfluxDB measurement

### Testing the weekly report

To trigger the weekly report immediately, add `RUN_WEEKLY_NOW: "true"` to the compose YAML and recreate the container. Set back to `"false"` after testing.

### Compliance calculation

Coach-directed cancellations (entries in CoachNotes containing keywords like "cancelled", "cancel", "skipped strength") are excluded from the planned session denominator — a coach-cancelled session does not count against compliance. Only sessions the athlete chose to skip count as missed.
