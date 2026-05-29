#!/bin/bash
# InfluxDB Backup Script
# Exports GarminStats and CronometerStats to /share/Backup/influxdb/
# Schedule: Sunday 03:00 via QNAP Task Scheduler
# Retains 4 weeks of backups locally

# ── Configuration ─────────────────────────────────────────────────────────────

INFLUX_CONTAINER="influxdb"
BACKUP_ROOT="/share/Backup/influxdb"
DATABASES=("GarminStats" "CronometerStats")
KEEP_WEEKS=4

# ── Logging ───────────────────────────────────────────────────────────────────

LOG_FILE="${BACKUP_ROOT}/backup.log"
mkdir -p "${BACKUP_ROOT}"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "${LOG_FILE}"; }

log "========================================"
log "Starting InfluxDB backup"
log "========================================"

# ── Export databases ──────────────────────────────────────────────────────────

DATE=$(date +%Y-%m-%d)
BACKUP_DIR="${BACKUP_ROOT}/${DATE}"
mkdir -p "${BACKUP_DIR}"

for DB in "${DATABASES[@]}"; do
    log "Backing up ${DB}..."

    # Create backup inside container
    docker exec "${INFLUX_CONTAINER}" influxd backup \
        -portable \
        -database "${DB}" \
        -host 127.0.0.1:8088 \
        /var/lib/influxdb/backup/${DATE}/${DB} 2>&1 | tee -a "${LOG_FILE}"

    if [ ${PIPESTATUS[0]} -eq 0 ]; then
        log "${DB} backup complete"
    else
        log "ERROR: ${DB} backup failed"
    fi
done

# ── Copy from container to NAS share ──────────────────────────────────────────

log "Copying backup files from container to NAS..."
docker cp "${INFLUX_CONTAINER}:/var/lib/influxdb/backup/${DATE}" "${BACKUP_DIR}/"

if [ $? -eq 0 ]; then
    SIZE=$(du -sh "${BACKUP_DIR}" | cut -f1)
    log "Backup copied — size: ${SIZE}"
else
    log "ERROR: Failed to copy backup from container"
    exit 1
fi

# ── Clean up container staging area ───────────────────────────────────────────

docker exec "${INFLUX_CONTAINER}" rm -rf /var/lib/influxdb/backup/${DATE}
log "Container staging area cleaned"

# ── Rotate old backups ────────────────────────────────────────────────────────

log "Rotating old backups (keeping last ${KEEP_WEEKS})..."
ls -dt "${BACKUP_ROOT}"/20* 2>/dev/null | tail -n +$((KEEP_WEEKS + 1)) | while read OLD; do
    log "Removing: ${OLD}"
    rm -rf "${OLD}"
done

# ── Summary ───────────────────────────────────────────────────────────────────

TOTAL=$(du -sh "${BACKUP_ROOT}" | cut -f1)
log "Backup complete — total size: ${TOTAL}"
log "Backups stored in: ${BACKUP_ROOT}"
log "========================================"
