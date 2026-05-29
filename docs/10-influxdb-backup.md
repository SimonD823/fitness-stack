# Guide 10 — InfluxDB Backup

This guide covers automated weekly backup of InfluxDB databases to a local NAS share. Cloud/offsite sync can be configured separately once the local backup is working.

---

## What Gets Backed Up

| Database | Contents |
|---------|---------|
| GarminStats | Steps, HRV, sleep, activities, strength sets, body composition |
| CronometerStats | Daily nutrition data |

CaliberStats is not backed up (empty until Caliber sync is configured).

---

## Backup Structure

```
/share/Backup/influxdb/
├── backup.log              ← running log of all backup operations
├── 2026-06-01/
│   ├── GarminStats/        ← portable backup files
│   └── CronometerStats/
├── 2026-06-08/
│   └── ...
└── (4 weeks retained, older removed automatically)
```

---

## Step 1 — Enable InfluxDB Backup Port

The InfluxDB backup command requires port 8088. Add to your `fitness-stack` docker-compose.yml under the influxdb service:

```yaml
services:
  influxdb:
    environment:
      INFLUXDB_BIND_ADDRESS: ":8088"
    ports:
      - "8086:8086"
      - "8088:8088"
```

Recreate the fitness-stack application after this change.

---

## Step 2 — Create Backup Share on NAS

Via Windows Explorer create:
```
\\nas\Backup\influxdb\
```

If a `Backup` share doesn't exist, create it in QNAP:
1. Open **Control Panel → Shared Folders**
2. Click **Create** → name it `Backup`
3. Set permissions as needed

---

## Step 3 — Copy Backup Script to NAS

Copy `influxdb_backup.sh` to:
```
\\nas\Container\scripts\influxdb_backup.sh
```

Make it executable from NAS SSH:

```bash
chmod +x /share/Container/scripts/influxdb_backup.sh
```

---

## Step 4 — Test the Script

Run manually from NAS SSH to verify it works before scheduling:

```bash
/share/Container/scripts/influxdb_backup.sh
```

Check the output — should show each database backing up and copying successfully. Verify the backup folder was created:

```bash
ls -la /share/Backup/influxdb/
```

---

## Step 5 — Schedule Weekly Backup

1. In QNAP web interface go to **Control Panel → Task Scheduler**
2. Click **Create → Scheduled Task**
3. Configure:
   - **Task name:** `InfluxDB Weekly Backup`
   - **Run as:** `admin`
   - **Schedule:** Weekly, **Sunday at 03:00 AM**
   - **Command:** `/share/Container/scripts/influxdb_backup.sh`
4. Click **OK**

Running Sunday at 03:00 AM ensures a full week of data is captured before the weekly report fires Monday at 10:00 UTC.

---

## Restoring from Backup

To restore a database from a backup:

```bash
# SSH into NAS
ssh admin@nas

# Copy backup files into the container
docker cp /share/Backup/influxdb/2026-06-01/GarminStats influxdb:/var/lib/influxdb/restore/

# Restore
docker exec influxdb influxd restore \
  -portable \
  -database GarminStats \
  /var/lib/influxdb/restore/GarminStats

# Verify
curl -s "http://localhost:8086/query?u=admin&p=YOUR_ADMIN_PASSWORD&q=SHOW+DATABASES"
```

---

## Checking Backup Status

From NAS SSH:

```bash
# View last backup log entries
tail -50 /share/Backup/influxdb/backup.log

# Check backup sizes
du -sh /share/Backup/influxdb/*/

# List all backups
ls -lt /share/Backup/influxdb/
```

---

## Future: Offsite Backup

Once the local backup is working reliably, options for offsite sync include:

- **Synology Cloud Sync** — if you have a Synology NAS, rsync the backup folder there and let Synology sync to OneDrive/Google Drive
- **rclone** — install on NAS, configure for OneDrive Personal, add a second scheduled task after the backup runs
- **QNAP HBS 3** — supports some cloud providers (check current support list as it changes)
