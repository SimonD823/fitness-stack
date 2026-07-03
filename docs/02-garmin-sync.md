# Guide 02 — Garmin Direct Sync

**Target machine:** NAS — NAS_IP (Container Station)

This guide sets up `garmin-direct-sync` — a Python-based container that pulls all Garmin health data directly from the Garmin Connect API. It replaces garmin-grafana entirely and adds strength training set/rep/weight detail not available in any pre-built solution.

**Key advantages over garmin-grafana:**
- Full historical backfill for all measurements including DailyStats and ActivitySummary
- Strength training set detail (StrengthSets measurement)
- Reliable data parsing — no silent field name bugs
- Transparent logging — every measurement shows point counts

**QNAP apps used:**
- **File Station** — create folders and copy script files
- **Container Station** — deploy and manage the application
- **Control Panel → Terminal & SNMP** — enable SSH for first-time auth

---

## What Data Is Collected

| Measurement | Description |
|-------------|-------------|
| `DailyStats` | Steps, distance, calories, floors, resting HR, stress durations, SpO2, body battery summary |
| `HeartRateIntraday` | Per-minute heart rate throughout the day |
| `StepsIntraday` | Step counts at 15-minute intervals |
| `StressIntraday` | Stress level readings throughout the day |
| `BodyBatteryIntraday` | Body battery readings throughout the day |
| `HRV_Intraday` | Overnight HRV readings (populates after sleep) |
| `BreathingRateIntraday` | Respiration rate (populates after sleep) |
| `SleepSummary` | Duration, stages (deep/light/REM/awake), score, overnight HRV |
| `BodyComposition` | Weight in grams at midnight UTC (divide by 1000 for kg) |
| `ActivitySummary` | All activity types with HR zones, calories, training load |
| `StrengthSets` | Per-set exercise detail: name, reps, weight (kg), volume (kg) |
| `DeviceSync` | Sync timestamp |

---

## InfluxDB Timestamp Patterns

Different Garmin measurements land at different timestamps — important for dashboard query windows:

| Measurement | Timestamp stored | Query window to use |
|-------------|-----------------|---------------------|
| DailyStats, ActivitySummary, StrengthSets | During the day (varies) | `07:00Z today → 07:00Z tomorrow` |
| SleepSummary, HRV | ~21:00–22:00Z previous night | `20:00Z previous day → 07:00Z tomorrow` |
| BodyComposition | `00:00:00Z` exactly (midnight UTC) | `00:00Z today → 00:00Z tomorrow` |

> **Important:** The `00:00:00Z` entries in SleepSummary are zero-value placeholder rows — filter them with `AND avgOvernightHrv > 0 AND sleepTimeSeconds > 3600`.

---

## Step 1 — Create directories in File Station

Open **File Station** → `Container`. Create:

```
Container / garmin-direct-sync
Container / garmin-direct-sync / tokens
```

---

## Step 2 — Copy files to the NAS

Via Windows Explorer, navigate to `\\NAS_IP\Container\garmin-direct-sync` and copy:

- `garmin_direct_sync.py` — the sync script
- `docker-compose.yml` — the container definition

Verify the script copied correctly — it must be a text file not a directory:

```bash
wc -l /share/Container/garmin-direct-sync/garmin_direct_sync.py
head -3 /share/Container/garmin-direct-sync/garmin_direct_sync.py
```

---

## Step 3 — Set folder permissions

Enable SSH (Control Panel → Terminal & SNMP → Enable SSH Service → Apply). Connect from Max:

```powershell
ssh admin@NAS_IP
```

```bash
chmod 777 /share/Container/garmin-direct-sync/tokens
```

---

## Step 4 — First-time authentication (one-time only)

Authentication uses a two-step process on Max — the garminconnect library requires an interactive terminal to handle Garmin's MFA requirement.

### Step 4a — Authenticate on Max

Install the auth dependencies on Max:

```powershell
C:\Users\Simon\AppData\Local\Programs\Python\Python311\python.exe -m pip install garminconnect
```

Save this as `C:\AdaptiveTraining\garmin_auth.py`:

```python
from garminconnect import Garmin
import os

os.makedirs('C:/AdaptiveTraining/garmin_tokens', exist_ok=True)

client = Garmin('your_email@example.com', 'YOUR_GARMIN_PASSWORD')
client.client.login(
    'your_email@example.com',
    'YOUR_GARMIN_PASSWORD',
    prompt_mfa=lambda: input('Enter MFA code: ')
)
client.client.dump('C:/AdaptiveTraining/garmin_tokens')
print('Tokens saved successfully')
```

Run it from PowerShell:

```powershell
cd C:\AdaptiveTraining
C:\Users\Simon\AppData\Local\Programs\Python\Python311\python.exe garmin_auth.py
```

When prompted, check your email for the Garmin MFA code and enter it. On success you will see `Tokens saved successfully` and a file at `C:\AdaptiveTraining\garmin_tokens\garmin_tokens.json`.

> **Rate limiting:** If Garmin returns 429 errors, wait 30–60 minutes before retrying. Multiple failed attempts trigger longer lockouts.

### Step 4b — Copy tokens to NAS

In Windows Explorer, navigate to `\\NAS_IP\Container\garmin-direct-sync\tokens` and copy `garmin_tokens.json` from `C:\AdaptiveTraining\garmin_tokens\` into it.

Verify on the NAS:

```bash
ls -la /share/Container/garmin-direct-sync/tokens/
```

You should see `garmin_tokens.json` (~2KB).

---

## Step 5 — Create the application in Container Station

Open **Container Station → Applications → Create**.

- **Application Name:** `garmin-direct-sync`
- Click the **YAML** tab
- Paste the `docker-compose.yml` content below
- Click **Validate YAML** → **Create**

```yaml
version: "3.8"

services:
  garmin-direct-sync:
    image: python:3.12-slim
    container_name: garmin-direct-sync
    restart: unless-stopped
    environment:
      GARMIN_EMAIL: "your_email@example.com"
      GARMIN_PASSWORD: ""
      GARMIN_DISPLAY_NAME: "YOUR_GARMIN_DISPLAY_NAME"
      INFLUX_HOST: "NAS_IP"
      INFLUX_PORT: "8086"
      INFLUX_DB: "GarminStats"
      INFLUX_USER: "influxdb_user"
      INFLUX_PASS: "YOUR_INFLUX_PASSWORD"
      SYNC_DAYS_BACK: "1"
      SYNC_INTERVAL_SECONDS: "1800"
      TOKEN_DIR: "/app/tokens"
    volumes:
      - /share/Container/garmin-direct-sync/garmin_direct_sync.py:/app/garmin_direct_sync.py
      - /share/Container/garmin-direct-sync/tokens:/app/tokens
    network_mode: host
    command: >
      bash -c "pip install --no-cache-dir garminconnect influxdb --quiet &&
               python /app/garmin_direct_sync.py"
```

> **GARMIN_PASSWORD is empty** — authentication uses the saved token in the tokens folder. The password is only needed to regenerate tokens if they expire.

> **GARMIN_DISPLAY_NAME** — your Garmin Connect profile display name, visible at `https://connect.garmin.com/app/profile/YOUR_GARMIN_DISPLAY_NAME`. Required for DailyStats, HeartRate, and Steps API calls.

---

## SYNC_DAYS_BACK Semantics

The `SYNC_DAYS_BACK` environment variable controls how many days are synced per cycle:

| Value | Days synced | Use case |
|-------|-------------|----------|
| `1` | Today only | Normal ongoing sync (default) |
| `2` | Today + yesterday | Catch up after a missed sync |
| `7` | Today + last 6 days | Short backfill |
| `143` | Full history to 1 Jan 2026 | Initial historical backfill |

> **Note:** `SYNC_DAYS_BACK=1` syncs **today only**. The script uses `range(days_back)` internally, so the value is not incremented. `BACKFILL_DAYS` uses the same semantics.

---

## Step 6 — Historical backfill

To populate data back to 1 January 2026, delete the `garmin-direct-sync` application and recreate it with `SYNC_DAYS_BACK: "143"`. Leave it running overnight — it processes each day sequentially with a 2-second pause. Once the logs show it has caught up to today, delete and recreate with `SYNC_DAYS_BACK: "1"` to resume normal 30-minute syncing.

> **Re-running is safe** — InfluxDB overwrites duplicate points rather than creating extras.

---

## Step 7 — Verify data in InfluxDB

**Measurements present:**
```
http://NAS_IP:8086/query?u=admin&p=YOUR_INFLUX_ADMIN_PASSWORD&db=GarminStats&q=SHOW%20MEASUREMENTS
```

**Daily stats (last 5 days):**
```
http://NAS_IP:8086/query?u=admin&p=YOUR_INFLUX_ADMIN_PASSWORD&db=GarminStats&q=SELECT%20%22totalSteps%22%2C%22restingHeartRate%22%20FROM%20%22DailyStats%22%20ORDER%20BY%20time%20DESC%20LIMIT%205
```

**Recent activities:**
```
http://NAS_IP:8086/query?u=admin&p=YOUR_INFLUX_ADMIN_PASSWORD&db=GarminStats&q=SELECT%20%22activityName%22%2C%22activityType%22%2C%22calories%22%20FROM%20%22ActivitySummary%22%20ORDER%20BY%20time%20DESC%20LIMIT%2010
```

**Strength set detail:**
```
http://NAS_IP:8086/query?u=admin&p=YOUR_INFLUX_ADMIN_PASSWORD&db=GarminStats&q=SELECT%20%22exercise%22%2C%22reps%22%2C%22weight_kg%22%20FROM%20%22StrengthSets%22%20ORDER%20BY%20time%20DESC%20LIMIT%2020
```

**Sleep data (check timestamps):**
```
http://NAS_IP:8086/query?u=admin&p=YOUR_INFLUX_ADMIN_PASSWORD&db=GarminStats&q=SELECT+time,sleepTimeSeconds,avgOvernightHrv,sleepScore+FROM+SleepSummary+ORDER+BY+time+DESC+LIMIT+5
```

**Body composition (stored at midnight UTC):**
```
http://NAS_IP:8086/query?u=admin&p=YOUR_INFLUX_ADMIN_PASSWORD&db=GarminStats&q=SELECT+time,weight+FROM+BodyComposition+ORDER+BY+time+DESC+LIMIT+5
```

---

## Troubleshooting

**Container starts but immediately exits:**
Check the Logs tab. Most likely the script file is missing or is a directory. Verify with `wc -l /share/Container/garmin-direct-sync/garmin_direct_sync.py`.

**"No password set and no cached tokens":**
The `garmin_tokens.json` file is missing from the tokens folder. Repeat Step 4.

**Token expired (after several months):**
Delete `garmin_tokens.json` from the tokens folder in File Station. Stop the container. Repeat Steps 4a and 4b to re-authenticate, then restart the container.

**Garmin rate limiting (429 errors) during auth:**
Too many login attempts in a short period. Wait at least 60 minutes then retry. Each failed attempt extends the lockout.

**HRV and BreathingRate always 0:**
These only populate from overnight sleep data. They will show non-zero values the morning after a night of wearing the watch during sleep.

**StrengthSets empty after a strength session:**
The session must be recorded as a structured workout on the Fenix 8. If the activity appears in ActivitySummary but StrengthSets is empty, the watch recording didn't include structured set data for that session.

**Sync covers more days than expected:**
Check `SYNC_DAYS_BACK` in the compose YAML. `1` = today only. The script uses `range(days_back)` — the value is not incremented internally.

---

**Next step → Guide 03: Cronometer sync setup**

---

## Workout Plan Exercise Name Mapping

When garmin-direct-sync processes a strength activity, it automatically fetches the associated Garmin workout plan (if one exists) and uses the exercise descriptions as the names stored in StrengthSets.

**How it works:**
1. The activity metadata contains `associatedWorkoutId` — the ID of the Garmin workout plan used
2. The sync fetches that plan from `/workout-service/workout/{workoutId}`
3. Each exercise step has a `description` field containing the human-readable name (e.g. `Dumbbell Bench Glute Bridge`)
4. Garmin's own exercise key (`SUSPENSION GLUTE_BRIDGE`) is replaced with this description
5. If no workout plan is associated, the Garmin category+exercise key is used as fallback

The training dashboard (`dashboard.py`) additionally applies `normalize_exercise()` at read time, which maps any remaining raw Garmin enum names to canonical Caliber plan names. This covers cases where the workout plan mapping didn't fire or data was written before the mapping was in place.

**Setting up workout plans in Garmin Connect:**
1. Go to `connect.garmin.com` → Training → Workouts
2. Create three workouts matching the Caliber plan:
   - `(Gym) Back & Shoulders`
   - `(Gym) Chest & Arms`
   - `(Gym) Legs & Abs`
3. For each exercise in the workout, set the **Description** field to the Caliber exercise name
4. Use these workout plans when recording sessions on the Fenix 8

**Template exercise names:** See `garmin_notes_templates.txt` for the correct exercise names for each workout.

**Cleaning up duplicate/incorrect records:**

If StrengthSets contains incorrect data, delete and resync:

```bash
curl -s -X POST "http://NAS_IP:8086/query?u=admin&p=YOUR_INFLUX_ADMIN_PASSWORD&db=GarminStats" \
  --data-urlencode "q=DELETE FROM StrengthSets WHERE time >= '2026-04-26T00:00:00Z' AND time < '2026-04-27T00:00:00Z'"
```

Then set `SYNC_DAYS_BACK` to cover the date range and restart the container.

---

## Sync Order (Important)

The sync functions run in this order for each date:

```
sync_daily_stats → sync_heart_rate → sync_steps → sync_stress →
sync_breathing → sync_sleep → sync_hrv → sync_body_composition → sync_activities
```

`sync_hrv` runs **after** `sync_sleep` intentionally — both write to `SleepSummary` but with different fields. Running HRV last ensures `avgOvernightHrv` from the HRV API overwrites the 0 value that the sleep API returns for this field.
