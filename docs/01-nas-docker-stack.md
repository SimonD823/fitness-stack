# Guide 01 — NAS Docker Stack: InfluxDB + Grafana

**Target machine:** NAS — NAS_IP (QNAP TS873A running QTS)

This guide deploys InfluxDB (time-series database) and Grafana (dashboards) as a single Container Station application using one YAML file. All steps use the QNAP web interface — no SSH or terminal access required.

**QNAP apps used:**
- **File Station** — create folders and provisioning files
- **Container Station** — deploy the Docker Compose stack and run commands in containers

---

## Step 1 — Create the directory structure in File Station

Open **File Station** from the QNAP desktop or the main menu.

Navigate to `Container` in the left panel (this maps to `/share/Container` on the NAS). If you don't see a `Container` folder, create one.

Create the following folder structure by right-clicking and selecting **Create Folder** at each level:

```
Container/
└── fitness-stack/
    ├── influxdb-data/
    ├── grafana-data/
    └── grafana-provisioning/
        ├── datasources/
        └── dashboards/
```

Work top-down — create `fitness-stack` first, then its children, then the `grafana-provisioning` subfolders.

---

## Step 2 — Create the Grafana datasource provisioning file

This file tells Grafana how to connect to InfluxDB automatically on first boot — no manual datasource setup needed in the Grafana UI.

In File Station, navigate to:
```
Container / fitness-stack / grafana-provisioning / datasources
```

Right-click inside the folder → **New File**. Name it exactly:
```
influxdb.yaml
```

Right-click the new file → **Open With** → **Text Editor**. Paste the following content in full, then click **Save**:

```yaml
apiVersion: 1

datasources:
  - name: InfluxDB-Garmin
    type: influxdb
    access: proxy
    url: http://influxdb:8086
    database: GarminStats
    user: influxdb_user
    secureJsonData:
      password: "YOUR_INFLUX_PASSWORD"
    jsonData:
      httpMode: GET
      queryLanguage: InfluxQL
    isDefault: true
    editable: true

  - name: InfluxDB-Cronometer
    type: influxdb
    access: proxy
    url: http://influxdb:8086
    database: CronometerStats
    user: influxdb_user
    secureJsonData:
      password: "YOUR_INFLUX_PASSWORD"
    jsonData:
      httpMode: GET
      queryLanguage: InfluxQL
    isDefault: false
    editable: true
```

> **Note:** The passwords here match the credentials set in the Docker Compose YAML in Step 4. If you change them, update them consistently in both places.

---

## Step 3 — Create the Grafana dashboard provisioning file

In File Station, navigate to:
```
Container / fitness-stack / grafana-provisioning / dashboards
```

Right-click → **New File**. Name it:
```
dashboard.yaml
```

Right-click → **Open With** → **Text Editor**. Paste and save:

```yaml
apiVersion: 1

providers:
  - name: 'default'
    orgId: 1
    folder: 'Fitness'
    type: file
    disableDeletion: false
    updateIntervalSeconds: 30
    options:
      path: /var/lib/grafana/dashboards
```

---

## Step 4 — Deploy the stack via Container Station

Open **Container Station** from the QNAP desktop or main menu.

1. Click **Applications** in the left sidebar
2. Click **Create**
3. In the **Application Name** field enter: `fitness-stack`
4. Click the **YAML** tab (not the Quick Create option)
5. Paste the following YAML in full into the editor:

```yaml
version: "3.8"

services:

  influxdb:
    image: influxdb:1.11
    container_name: influxdb
    restart: unless-stopped
    stdin_open: true
    tty: true
    environment:
      INFLUXDB_DB: GarminStats
      INFLUXDB_USER: influxdb_user
      INFLUXDB_USER_PASSWORD: YOUR_INFLUX_PASSWORD
      INFLUXDB_ADMIN_ENABLED: "true"
      INFLUXDB_ADMIN_USER: admin
      INFLUXDB_ADMIN_PASSWORD: YOUR_INFLUX_ADMIN_PASSWORD
      INFLUXDB_HTTP_AUTH_ENABLED: "true"
    ports:
      - "8086:8086"
    volumes:
      - /share/Container/fitness-stack/influxdb-data:/var/lib/influxdb
    networks:
      - fitness-net
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8086/ping"]
      interval: 30s
      timeout: 10s
      retries: 3

  grafana:
    image: grafana/grafana:latest
    container_name: grafana
    restart: unless-stopped
    depends_on:
      influxdb:
        condition: service_healthy
    environment:
      GF_SECURITY_ADMIN_USER: admin
      GF_SECURITY_ADMIN_PASSWORD: YOUR_GRAFANA_PASSWORD
      GF_USERS_ALLOW_SIGN_UP: "false"
      GF_SERVER_ROOT_URL: "http://NAS_IP:3000"
      GF_INSTALL_PLUGINS: ""
    ports:
      - "3000:3000"
    volumes:
      - /share/Container/fitness-stack/grafana-data:/var/lib/grafana
      - /share/Container/fitness-stack/grafana-provisioning:/etc/grafana/provisioning
      - /share/Container/fitness-stack/grafana-dashboards:/var/lib/grafana/dashboards
    networks:
      - fitness-net

networks:
  fitness-net:
    driver: bridge
```

6. Click **Validate YAML** — Container Station will confirm the syntax is correct
7. Click **Create**

Both containers will pull their images and start. InfluxDB starts first; Grafana waits for its health check to pass before starting. This takes 2–3 minutes on first run.

You can watch progress in the **Applications** list — both containers will show a green **Running** status when ready.

---

## Step 5 — Create the CronometerStats and GarminStats databases

InfluxDB auto-creates the `GarminStats` database from the environment variable set in the YAML. The other two databases need to be created manually.

### Method A — PowerShell on Max (recommended)

InfluxDB requires POST requests for write operations such as `CREATE DATABASE`. Open **PowerShell** on Max and run each command in turn:

```powershell
Invoke-RestMethod -Method POST -Uri "http://NAS_IP:8086/query?u=admin&p=YOUR_INFLUX_ADMIN_PASSWORD" -Body "q=CREATE DATABASE CronometerStats"
```

```powershell
Invoke-RestMethod -Method POST -Uri "http://NAS_IP:8086/query?u=admin&p=YOUR_INFLUX_ADMIN_PASSWORD" -Body "q=CREATE DATABASE GarminStats"
```

**Grant the app user permissions on both new databases:**

```powershell
Invoke-RestMethod -Method POST -Uri "http://NAS_IP:8086/query?u=admin&p=YOUR_INFLUX_ADMIN_PASSWORD" -Body "q=GRANT ALL ON CronometerStats TO influxdb_user"
```

```powershell
Invoke-RestMethod -Method POST -Uri "http://NAS_IP:8086/query?u=admin&p=YOUR_INFLUX_ADMIN_PASSWORD" -Body "q=GRANT ALL ON GarminStats TO influxdb_user"
```

> **Why this is needed:** InfluxDB automatically grants `influxdb_user` access to `GarminStats` because that database is created by the container's own `INFLUXDB_DB` environment variable during startup. The `CronometerStats` database created manually by the admin account does not inherit those permissions — it must be granted explicitly.

Verify all three databases exist (GET is fine for read-only queries):

```powershell
Invoke-RestMethod -Uri "http://NAS_IP:8086/query?u=admin&p=YOUR_INFLUX_ADMIN_PASSWORD&q=SHOW%20DATABASES"
```

The response should list `GarminStats`, `CronometerStats`, and `GarminStats`. You can also paste the SHOW DATABASES URL directly into any browser to verify.

---

### Method B — Container Station Terminal

The YAML in Step 4 includes `stdin_open: true` and `tty: true` on the influxdb service, which enables terminal access in Container Station. If Method A does not work for any reason:

1. In Container Station, click **Containers** in the left sidebar
2. Find `influxdb` and click on it
3. Click the **Terminal** tab (may also be labelled **Console** or **Execute**)
4. Run each command in turn:

```
influx -username admin -password YOUR_INFLUX_ADMIN_PASSWORD -execute "CREATE DATABASE CronometerStats"
```
```
influx -username admin -password YOUR_INFLUX_ADMIN_PASSWORD -execute "CREATE DATABASE GarminStats"
```
```
influx -username admin -password YOUR_INFLUX_ADMIN_PASSWORD -execute "SHOW DATABASES"
```

> **If the terminal refuses to attach:** The container was started without `stdin_open`/`tty` enabled. Stop the container, edit the application YAML in Container Station to confirm those two lines are present under the `influxdb` service, and recreate. Your data is safe — it persists in the volume at `/share/Container/fitness-stack/influxdb-data`.

---

## Step 6 — Verify access

Open a browser on any device on your local network and go to:

- **Grafana:** `http://NAS_IP:3000` — log in with `admin` / `YOUR_GRAFANA_PASSWORD`
- **InfluxDB:** paste this in your browser to confirm it is running and all three databases exist:
  ```
  http://NAS_IP:8086/query?u=admin&p=YOUR_INFLUX_ADMIN_PASSWORD&q=SHOW%20DATABASES
  ```
  You should see `GarminStats`, `CronometerStats`, and `GarminStats` in the JSON response.

> **Note:** The `/ping` endpoint returns HTTP 204 No Content — a deliberately empty response that browsers display as a blank white page. Use the SHOW DATABASES URL above instead for visible confirmation.

In Grafana, go to **Connections → Data Sources** and confirm both `InfluxDB-Garmin` and `InfluxDB-Cronometer` appear. Click each one and press **Save & Test** — both should report "datasource is working."

> The `GarminStats` database is populated by the weekly Caliber MCP export (see `Caliber_MCP_Integration_Guide.md`) and does not need a Grafana datasource at this stage unless you want to build dedicated strength dashboards later.

---

## Credential Reference

| Service | Username | Password |
|---------|----------|----------|
| InfluxDB admin | admin | YOUR_INFLUX_ADMIN_PASSWORD |
| InfluxDB app user | influxdb_user | YOUR_INFLUX_PASSWORD |
| Grafana admin | admin | YOUR_GRAFANA_PASSWORD |

> Change these to your own secure passwords before deploying. If you do, update them consistently in the YAML (Step 4), the datasource provisioning file (Step 2), and any sync scripts in Guides 02 and 03.

---

## Troubleshooting

**Grafana cannot connect to InfluxDB:**
Both containers must be on the same `fitness-net` network. Inside Docker Compose, containers refer to each other by service name (`influxdb`), not by IP. If you see a connection error in Grafana's datasource test, check that both containers are listed under the same `fitness-stack` application in Container Station.

**InfluxDB port 8086 not reachable from other devices on the network:**
Go to **Control Panel → Security → Firewall** in the QNAP web interface. Add rules to allow inbound TCP on ports 8086 and 3000 from your local subnet (192.168.1.0/24).

**Container Station shows YAML as invalid:**
YAML is whitespace-sensitive. Make sure you pasted the file without altering indentation. Use the **Validate YAML** button before clicking Create — it will highlight the first problem line.

**Grafana starts but datasources show as red/failed:**
This usually means Grafana started before the provisioning files were in place, or the files have a typo. Re-check the content of `influxdb.yaml` in File Station, correct any issues, then restart the `grafana` container from Container Station (click the container → **Restart**).

---

**Next step → Guide 02: Garmin sync setup**
