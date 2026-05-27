# Guide 03 — Cronometer Nutrition Sync to InfluxDB

**Target machine:** NAS — NAS_IP (script runs as a scheduled container)

This guide uses the **cronometer-export** CLI tool by jrmycanady to pull daily nutrition data from Cronometer and write it to InfluxDB. It runs as a scheduled Docker container on the NAS.

GitHub: `https://github.com/jrmycanady/cronometer-export`

The tool exports five data types: `daily-nutrition` (calories, macros, micronutrients totals per day), `servings` (individual food entries), `exercises`, `notes`, and `biometrics`. For coaching and macro tracking you primarily need `daily-nutrition`, `servings`, and `biometrics` (which provides weight data for automatic athlete weight detection in the dashboard).

All steps use the QNAP web interface — no SSH required.

**QNAP apps used:**
- **File Station** — create all folders and files
- **Container Station** — create and manage the application

---

## Architecture

Because `cronometer-export` outputs CSV rather than speaking directly to InfluxDB, a small Python converter script bridges the two. The full pipeline is:

```
Cronometer API → cronometer-export (Go binary) → CSV → Python parser → InfluxDB
```

This runs as a continuously running Docker container on the NAS, syncing once every 24 hours.

> **No Docker build required:** QNAP's bundled Docker CLI does not support image building from the command line. This guide uses the official `python:3.12-slim` image directly, with the cronometer-export binary and Python script mounted as volumes. The binary is downloaded once to the NAS filesystem via SSH.

---

## Step 1 — Create the working directory in File Station

Open **File Station** from the QNAP desktop or main menu.

Navigate to `Container` in the left panel. Right-click → **Create Folder**, name it:
```
cronometer-sync
```

---

## Step 2 — Create the Python converter script

In File Station, navigate to:
```
Container / cronometer-sync
```

Right-click → **New File**. Name it exactly:
```
cronometer_to_influx.py
```

Right-click the file → **Open With** → **Text Editor**. Paste the full script below, then click **Save**:

```python
#!/usr/bin/env python3
"""
Cronometer daily nutrition export → InfluxDB writer.
Reads CRONOMETER_USER, CRONOMETER_PASS, INFLUX_HOST, INFLUX_PORT,
INFLUX_DB, INFLUX_USER, INFLUX_PASS from environment variables.
"""

import os
import csv
import subprocess
import sys
from datetime import datetime, timedelta
from influxdb import InfluxDBClient

# --- Config from environment ---
CRONO_USER  = os.environ["CRONOMETER_USER"]
CRONO_PASS  = os.environ["CRONOMETER_PASS"]
INFLUX_HOST = os.environ.get("INFLUX_HOST", "NAS_IP")
INFLUX_PORT = int(os.environ.get("INFLUX_PORT", "8086"))
INFLUX_DB   = os.environ.get("INFLUX_DB", "CronometerStats")
INFLUX_USER = os.environ.get("INFLUX_USER", "influxdb_user")
INFLUX_PASS = os.environ.get("INFLUX_PASS", "YOUR_INFLUX_PASSWORD")

# --- Date range ---
days_back = int(os.environ.get("CRONO_DAYS_BACK", "2"))
end_date   = datetime.utcnow().strftime("%Y-%m-%dT00:00:00Z")
start_date = (datetime.utcnow() - timedelta(days=days_back)).strftime("%Y-%m-%dT00:00:00Z")

def fetch_cronometer_csv(export_type: str) -> str:
    cmd = [
        "/usr/local/bin/cronometer-export",
        "-t", export_type,
        "-s", start_date,
        "-e", end_date,
        "-u", CRONO_USER,
        "-p", CRONO_PASS,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return result.stdout


def parse_daily_nutrition(csv_text: str) -> list:
    points = []
    reader = csv.DictReader(csv_text.splitlines())
    for row in reader:
        try:
            ts = datetime.strptime(row["Date"], "%Y-%m-%d")
        except (KeyError, ValueError):
            continue
        fields = {}
        for key, value in row.items():
            if key == "Date" or key == "":
                continue
            try:
                fields[key.strip().replace(" ", "_").replace("(", "").replace(")", "").replace("/", "_")] = float(value)
            except (ValueError, TypeError):
                pass
        if not fields:
            continue
        points.append({
            "measurement": "daily_nutrition",
            "tags": {"source": "cronometer"},
            "time": ts.strftime("%Y-%m-%dT00:00:00Z"),
            "fields": fields,
        })
    return points


def parse_servings(csv_text: str) -> list:
    points = []
    reader = csv.DictReader(csv_text.splitlines())
    for row in reader:
        try:
            ts = datetime.strptime(row["Day"], "%Y-%m-%d")
        except (KeyError, ValueError):
            continue
        food_name = row.get("Food Name", "Unknown").strip()
        amount    = row.get("Amount", "")
        unit      = row.get("Unit", "")
        fields = {"food_name_tag": food_name, "amount_str": f"{amount} {unit}".strip()}
        for key, value in row.items():
            if key in ("Day", "Food Name", "Amount", "Unit", ""):
                continue
            try:
                fields[key.strip().replace(" ", "_").replace("(", "").replace(")", "")] = float(value)
            except (ValueError, TypeError):
                pass
        points.append({
            "measurement": "food_servings",
            "tags": {"source": "cronometer", "food": food_name[:64]},
            "time": ts.strftime("%Y-%m-%dT12:00:00Z"),
            "fields": fields,
        })
    return points


def parse_biometrics(csv_text: str) -> list:
    """
    Parse biometrics CSV into InfluxDB points.
    Normalises weight to kg regardless of whether Cronometer is set to kg or lbs.
    The Weight_kg field is used by the dashboard generator for automatic weight detection.
    """
    points = []
    reader = csv.DictReader(csv_text.splitlines())
    for row in reader:
        try:
            ts = datetime.strptime(row["Day"], "%Y-%m-%d")
        except (KeyError, ValueError):
            continue
        metric_type = row.get("Type", "").strip()
        unit        = row.get("Unit", "").strip()
        try:
            amount = float(row.get("Amount", ""))
        except (ValueError, TypeError):
            continue
        amount_kg = None
        if "weight" in metric_type.lower():
            amount_kg = round(amount * 0.453592, 2) if unit.lower() in ("lbs", "lb") else round(amount, 2)
        field_key = metric_type.replace(" ", "_").replace("/", "_").replace("(", "").replace(")", "")
        if not field_key:
            continue  # Skip entries where metric type is empty or unrecognised
        fields: dict = {field_key: amount}
        if amount_kg is not None:
            fields["Weight_kg"] = amount_kg
        points.append({
            "measurement": "biometrics",
            "tags": {"source": "cronometer", "type": metric_type, "unit": unit},
            "time": ts.strftime("%Y-%m-%dT12:00:00Z"),
            "fields": fields,
        })
    return points


def write_to_influx(points: list, client: InfluxDBClient):
    if not points:
        print("  No points to write.")
        return
    client.write_points(points, batch_size=500)
    print(f"  Wrote {len(points)} points to InfluxDB.")


def main():
    client = InfluxDBClient(
        host=INFLUX_HOST, port=INFLUX_PORT,
        username=INFLUX_USER, password=INFLUX_PASS,
        database=INFLUX_DB,
    )
    print(f"Syncing Cronometer data: {start_date} → {end_date}")

    print("Fetching daily-nutrition...")
    write_to_influx(parse_daily_nutrition(fetch_cronometer_csv("daily-nutrition")), client)

    print("Fetching servings...")
    write_to_influx(parse_servings(fetch_cronometer_csv("servings")), client)

    print("Fetching biometrics (weight and body measurements)...")
    write_to_influx(parse_biometrics(fetch_cronometer_csv("biometrics")), client)

    print("Done.")


if __name__ == "__main__":
    main()
```

---

## Step 3 — Download the cronometer-export binary

The cronometer-export binary needs to be downloaded once to the NAS filesystem. Enable SSH on the NAS (Control Panel → Terminal & SNMP → Enable SSH Service → Apply), then connect from PowerShell on Max:

```powershell
ssh admin@NAS_IP
```

In the SSH session, download the zip, extract it, move the binary into place, and clean up:

```bash
curl -sSL -o /tmp/crono.zip "https://github.com/jrmycanady/cronometer-export/releases/download/v1.1.1/cronometer-export-linux-amd64.zip"
```

Verify the download looks correct (should be several MB):

```bash
ls -lh /tmp/crono.zip
```

Extract, move the binary into the cronometer-sync folder, and clean up:

```bash
busybox unzip /tmp/crono.zip -d /share/Container/cronometer-sync/

mv /share/Container/cronometer-sync/cronometer-export-linux-amd64-v1.1.1/cronometer-export /share/Container/cronometer-sync/cronometer-export

rm -rf /share/Container/cronometer-sync/cronometer-export-linux-amd64-v1.1.1/

rm /tmp/crono.zip
```

Verify:

```bash
ls -lh /share/Container/cronometer-sync/
```

You should see `cronometer-export` (~9.2 MB) alongside `cronometer_to_influx.py` and `docker-compose.yml`. Exit SSH when done:

```bash
exit
```

---

## Step 4 — Create the scheduler script

The container uses a small Python scheduler that runs the sync immediately on startup, then waits until exactly 6am each day. Create this as a separate file to keep the compose file clean.

In File Station, navigate to:
```
Container / cronometer-sync
```

Right-click → **New File**. Name it:
```
scheduler.py
```

Right-click → **Open With** → **Text Editor**. Paste and save:

```python
import subprocess
import time
import datetime

while True:
    print("Starting Cronometer sync...", flush=True)
    subprocess.run(["python", "/app/cronometer_to_influx.py"])
    now = datetime.datetime.now()
    next_run = now.replace(hour=6, minute=0, second=0, microsecond=0)
    if now >= next_run:
        next_run += datetime.timedelta(days=1)
    secs = (next_run - now).total_seconds()
    print(
        f"Next run at {next_run.strftime('%Y-%m-%d %H:%M')} — "
        f"sleeping {int(secs/3600)}h {int((secs%3600)/60)}m",
        flush=True
    )
    time.sleep(secs)
```

---

## Step 5 — Create the Docker Compose file

In File Station, navigate to:
```
Container / cronometer-sync
```

Right-click → **New File**. Name it:
```
docker-compose.yml
```

Right-click → **Open With** → **Text Editor**. Paste and save:

```yaml
version: "3.8"

services:
  cronometer-sync:
    image: python:3.12-slim
    container_name: cronometer-sync
    environment:
      CRONOMETER_USER: "your.cronometer@email.com"
      CRONOMETER_PASS: "YourCronometerPassword"
      INFLUX_HOST: "NAS_IP"
      INFLUX_PORT: "8086"
      INFLUX_DB: "CronometerStats"
      INFLUX_USER: "influxdb_user"
      INFLUX_PASS: "YOUR_INFLUX_PASSWORD"
      CRONO_DAYS_BACK: "2"
      TZ: "Europe/London"
    volumes:
      - /share/Container/cronometer-sync/cronometer_to_influx.py:/app/cronometer_to_influx.py
      - /share/Container/cronometer-sync/cronometer-export:/usr/local/bin/cronometer-export
      - /share/Container/cronometer-sync/scheduler.py:/app/scheduler.py
    network_mode: host
    restart: unless-stopped
    command: bash -c "pip install --no-cache-dir influxdb==5.3.2 --quiet && python /app/scheduler.py"
```

Fill in your actual Cronometer credentials.

> **How this works:** On first start the container installs the Python InfluxDB client, runs the sync immediately via `scheduler.py`, then calculates exactly how many seconds until 6am and sleeps until then. Repeats daily. No system packages required.

> **Timezone:** `TZ: "Europe/London"` ensures 6am means 6am London time (BST in summer, GMT in winter) rather than UTC.

> **Why `network_mode: host`?** Bridge networking prevents the container from reaching InfluxDB at `NAS_IP:8086` — the same issue as the garmin-direct-sync container.

## Step 6 — Create the application in Container Station

Open **Container Station** → **Applications** → **Create**.

- **Application Name:** `cronometer-sync`
- Click the **YAML** tab
- Paste the docker-compose.yml content from Step 4 with your Cronometer credentials filled in
- Click **Validate YAML**, then **Create**

Container Station will pull the `python:3.12-slim` image (~50 MB) and start the container. It installs the InfluxDB Python package (~20 seconds) then runs the sync. The container exits when done — this is expected.

Check the **Logs** tab — you should see:

```
Syncing Cronometer data: 2026-05-19 → 2026-05-21
Fetching daily-nutrition...
  Wrote 2 points to InfluxDB.
Fetching servings...
  Wrote 14 points to InfluxDB.
Fetching biometrics (weight and body measurements)...
  Wrote 2 points to InfluxDB.
Done.
```

**Verify the data landed in InfluxDB** by opening this URL in your browser:

```
http://NAS_IP:8086/query?u=admin&p=YOUR_INFLUX_ADMIN_PASSWORD&db=CronometerStats&q=SHOW%20MEASUREMENTS
```

The JSON response should list `daily_nutrition`, `food_servings`, and `biometrics`.

---

## Step 7 — Historical backfill (optional)

To import your full Cronometer history, edit the `cronometer-sync` application in Container Station:

1. Container Station → **Applications** → click `cronometer-sync` → **Edit**
2. Add an environment variable:
   - **Key:** `CRONO_DAYS_BACK`
   - **Value:** `730` (2 years — adjust to match how long you have used Cronometer)
3. Save — Container Station will restart the container with the new value
4. Watch the Logs tab — the sync will run through all requested days
5. When complete, edit again and remove the `CRONO_DAYS_BACK` override (the default of 2 days resumes)

---

## Step 8 — Build a Grafana nutrition dashboard

Open Grafana at `http://NAS_IP:3000` and log in.

---

### Create a new dashboard

1. Click the **Dashboards** icon in the left sidebar (looks like four squares)
2. Click the **New** button (top right of the dashboards page)
3. Select **New dashboard** from the dropdown
4. Click **+ Add visualization**
5. A data source picker appears — click **InfluxDB-Cronometer**

You are now in the panel editor. The screen is split: the chart preview is at the top, query editor at the bottom, and panel options in a right sidebar.

---

### Panel 1 — Daily Calories

**Switch to raw query mode:**

In the query editor at the bottom, look for the **pencil icon** in the upper-right corner of the query editor area. Click it to switch from the visual query builder to raw InfluxQL mode. You will see a text input field appear.

Paste this query:

```sql
SELECT mean("Energy_kcal") FROM "daily_nutrition"
WHERE $timeFilter GROUP BY time(1d) fill(null)
```

Press **Shift+Enter** or click outside the field to run the query. You should see a chart appear in the preview above.

**Set panel options** (right sidebar):

- Scroll down to **Panel options** → set **Title** to `Daily Calories`
- Scroll down to **Standard options** → set **Unit** — type `kcal` in the search box and select **Kilocalories (kcal)**

Click **Save dashboard** (top right), name it `Cronometer Nutrition`, click **Save**.

---

### Panel 2 — Daily Protein

Click the **back arrow** (top left of panel editor) to return to the dashboard.

1. Click **Add** → **Visualization** (top right of the dashboard)
2. Select **InfluxDB-Cronometer** from the data source picker
3. Click the **pencil icon** in the query editor to switch to raw mode
4. Paste:

```sql
SELECT mean("Protein_g") FROM "daily_nutrition"
WHERE $timeFilter GROUP BY time(1d) fill(null)
```

5. Set **Title** to `Daily Protein` and **Unit** to `Grams (g)` in the right sidebar
6. Click **Save dashboard**

---

### Panel 3 — Macros (Protein / Fat / Carbs)

Repeat the same process — **Add → Visualization → InfluxDB-Cronometer → pencil icon** — then paste:

```sql
SELECT mean("Protein_g") AS "Protein", mean("Fat_g") AS "Fat", mean("Carbs_g") AS "Carbs"
FROM "daily_nutrition"
WHERE $timeFilter GROUP BY time(1d) fill(null)
```

This produces three series on one chart. Set **Title** to `Macros`. Save.

---

### Set the time range

In the top right of the dashboard, click the time picker — it likely shows **Last 6 hours**. Change it to **Last 7 days** or **Last 30 days** to see your data. Click **Save dashboard** again to preserve the time range.

---

### If no data appears

**Check the time range first** — the data only goes back to when you first ran the sync. Set the picker to cover that date range.

**Verify data is in InfluxDB** by pasting this URL in your browser:

```
http://NAS_IP:8086/query?u=admin&p=YOUR_INFLUX_ADMIN_PASSWORD&db=CronometerStats&q=SELECT%20*%20FROM%20daily_nutrition%20LIMIT%205
```

You should see rows of nutrition data in the JSON response. If data is there but Grafana shows nothing, confirm the pencil/raw mode is active and that `$timeFilter` is included in the query — without it Grafana cannot apply the time range.

---

---

## Step 9 — Add a weight panel (optional)

Weight syncs to Garmin Connect from your Garmin-connected scale and is stored in the `BodyComposition` measurement in GarminStats. The weight panel therefore uses the Garmin datasource rather than Cronometer.

Click **Add → Visualization**, select **InfluxDB-Garmin** as the data source, click the pencil icon to switch to raw query mode, and paste:

```sql
SELECT mean("weight") FROM "BodyComposition"
WHERE $timeFilter GROUP BY time(1d) fill(null)
```

Set **Alias by** to `Weight`.

In the right sidebar:
- Set **Title** to `Weight (kg)`
- Under **Standard options → Unit** type `kg` and select **Mass → Kilograms (kg)**
- Under **Graph styles** set **Style** to **Lines** — weight trends better as a line than bars

Click **Save**.


## Troubleshooting

**cronometer-export exits with login error:**
Confirm your credentials at cronometer.com. If you use Google sign-in for Cronometer, set a separate password via Account Settings → Security in the Cronometer web app.

**Empty CSV returned:**
If you have no data for the requested date range, the CSV will have headers but no rows and the script will write 0 points. This is normal behaviour.

**Build fails — binary not found:**
In Container Station, click the application → **Rebuild** (or delete and recreate). A flaky network connection during the binary download is the most common cause.

**Logs show "Wrote 0 points" for biometrics:**
This means no weight or body measurements are logged in Cronometer for the synced date range. Start logging weight in Cronometer and it will appear on the next sync.

---

**Next step → Guide 04: LM Studio + Qwen3.6-27B on Max**
