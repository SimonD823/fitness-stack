#!/usr/bin/env python3
"""
Cronometer Sync → InfluxDB
Fetches daily nutrition data from Cronometer and writes to CronometerStats InfluxDB.

Runs daily at 06:00 AM (before the daily brief at 06:45 AM).
Container: python:3.12-slim on NAS via Container Station.

Requires:
  pip install requests influxdb

Environment variables:
  INFLUX_HOST      — NAS hostname or IP (default: nas)
  INFLUX_PORT      — InfluxDB port (default: 8086)
  INFLUX_USER      — InfluxDB app user
  INFLUX_PASS      — InfluxDB app password
  CRONOMETER_USER  — Cronometer account email
  CRONOMETER_PASS  — Cronometer account password
  SYNC_HOUR        — Hour to run daily sync (default: 6)
  SYNC_MINUTE      — Minute to run daily sync (default: 0)
"""

import datetime
import logging
import os
import sys
import time

import requests
from influxdb import InfluxDBClient

# ── Configuration ─────────────────────────────────────────────────────────────

INFLUX_HOST = os.environ.get("INFLUX_HOST", "nas")
INFLUX_PORT = int(os.environ.get("INFLUX_PORT", "8086"))
INFLUX_DB   = "CronometerStats"
INFLUX_USER = os.environ.get("INFLUX_USER", "influxdb_user")
INFLUX_PASS = os.environ.get("INFLUX_PASS", "")

CRONOMETER_USER = os.environ.get("CRONOMETER_USER", "")
CRONOMETER_PASS = os.environ.get("CRONOMETER_PASS", "")

SYNC_HOUR   = int(os.environ.get("SYNC_HOUR", "6"))
SYNC_MINUTE = int(os.environ.get("SYNC_MINUTE", "0"))

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger(__name__)

# ── Cronometer API ────────────────────────────────────────────────────────────

CRONOMETER_API = "https://cronometer.com/api"

def get_cronometer_session():
    """Authenticate with Cronometer and return a session."""
    session = requests.Session()
    resp = session.post(f"{CRONOMETER_API}/auth/login", json={
        "username": CRONOMETER_USER,
        "password": CRONOMETER_PASS,
    })
    resp.raise_for_status()
    return session


def get_nutrition(session, date_str):
    """Fetch daily nutrition summary for a given date (YYYY-MM-DD)."""
    resp = session.get(f"{CRONOMETER_API}/nutrition/daily", params={"date": date_str})
    if resp.status_code == 200:
        return resp.json()
    return None


# ── InfluxDB ──────────────────────────────────────────────────────────────────

def get_influx():
    client = InfluxDBClient(
        host=INFLUX_HOST, port=INFLUX_PORT,
        username=INFLUX_USER, password=INFLUX_PASS,
        database=INFLUX_DB,
    )
    client.ping()
    return client


def write_nutrition(influx, date_str, data):
    """Write daily nutrition data to CronometerStats.daily_nutrition."""
    if not data:
        return

    ts = datetime.datetime.strptime(date_str, "%Y-%m-%d").replace(
        tzinfo=datetime.timezone.utc
    ).strftime("%Y-%m-%dT%H:%M:%SZ")

    fields = {
        "Energy_kcal": float(data.get("energy", {}).get("kcal", 0) or 0),
        "Protein_g":   float(data.get("protein", {}).get("g", 0) or 0),
        "Fat_g":       float(data.get("fat", {}).get("g", 0) or 0),
        "Carbs_g":     float(data.get("carbs", {}).get("g", 0) or 0),
        "Net_Carbs_g": float(data.get("net_carbs", {}).get("g", 0) or 0),
        "Fiber_g":     float(data.get("fiber", {}).get("g", 0) or 0),
        "Sugar_g":     float(data.get("sugar", {}).get("g", 0) or 0),
    }

    influx.write_points([{
        "measurement": "daily_nutrition",
        "time": ts,
        "fields": fields,
    }])
    log.info(f"  Nutrition: {fields['Energy_kcal']:.0f} kcal, "
             f"{fields['Protein_g']:.1f}g protein")


# ── Scheduler ─────────────────────────────────────────────────────────────────

def seconds_until(hour, minute):
    now = datetime.datetime.now()
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if now >= target:
        target += datetime.timedelta(days=1)
    return (target - now).total_seconds()


def run_sync():
    yesterday = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
    log.info(f"Syncing Cronometer nutrition for {yesterday}")

    try:
        session = get_cronometer_session()
        data    = get_nutrition(session, yesterday)
        influx  = get_influx()
        write_nutrition(influx, yesterday, data)
        log.info("Sync complete")
    except Exception as e:
        log.error(f"Sync failed: {e}")


def main():
    log.info("=" * 60)
    log.info("Cronometer Sync Starting")
    log.info(f"Will sync at {SYNC_HOUR:02d}:{SYNC_MINUTE:02d} every morning")
    log.info("=" * 60)

    while True:
        secs = seconds_until(SYNC_HOUR, SYNC_MINUTE)
        wake = datetime.datetime.now() + datetime.timedelta(seconds=secs)
        log.info(f"Next sync at {wake.strftime('%Y-%m-%d %H:%M')} "
                 f"(sleeping {int(secs/3600)}h {int((secs%3600)/60)}m)")
        time.sleep(secs)

        try:
            run_sync()
        except Exception as e:
            log.error(f"Sync failed: {e}")

        time.sleep(60)


if __name__ == "__main__":
    main()
