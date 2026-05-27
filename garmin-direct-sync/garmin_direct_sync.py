#!/usr/bin/env python3
"""
Garmin Direct Sync → InfluxDB
Full replacement for garmin-grafana container.

Fetches from Garmin Connect API every 30 minutes:
  - Intraday heart rate, steps, stress, body battery, HRV, breathing rate
  - Daily stats summary (DailyStats — written once per day)
  - Sleep summary and staging
  - Activities with full set/rep/weight detail for strength sessions
  - Body composition (weight)
  - Training status

Writes to GarminStats InfluxDB using the same measurement schemas
as garmin-grafana so existing Grafana dashboards continue to work.

First-time auth: run interactively via SSH (see guide).
Daily operation: runs automatically as a Container Station application.
"""

import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone

from garminconnect import (
    Garmin,
    GarminConnectAuthenticationError,
    GarminConnectConnectionError,
)
from influxdb import InfluxDBClient

# ── Configuration ─────────────────────────────────────────────────────────────

GARMIN_EMAIL     = os.environ.get("GARMIN_EMAIL", "your_email@example.com")
GARMIN_PASSWORD  = os.environ.get("GARMIN_PASSWORD", "")
TOKEN_DIR        = os.environ.get("TOKEN_DIR", "/app/tokens")

INFLUX_HOST      = os.environ.get("INFLUX_HOST", "NAS_IP")
INFLUX_PORT      = int(os.environ.get("INFLUX_PORT", "8086"))
INFLUX_DB        = os.environ.get("INFLUX_DB", "GarminStats")
INFLUX_USER      = os.environ.get("INFLUX_USER", "influxdb_user")
INFLUX_PASS      = os.environ.get("INFLUX_PASS", "YOUR_INFLUX_PASSWORD")

SYNC_DAYS_BACK   = int(os.environ.get("SYNC_DAYS_BACK", "1"))
SYNC_INTERVAL    = int(os.environ.get("SYNC_INTERVAL_SECONDS", "1800"))  # 30 min

DEVICE_NAME      = "fenix 8 - 51mm, AMOLED"
GARMIN_DISPLAY_NAME = os.environ.get("GARMIN_DISPLAY_NAME", "YOUR_GARMIN_DISPLAY_NAME")
DB_NAME          = "GarminStats"

# Activity types to fetch set detail for
STRENGTH_TYPES   = {
    "strength_training", "indoor_cardio", "gym_and_fitness_equipment",
    "fitness_equipment",
}

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger(__name__)

# ── Garmin auth ───────────────────────────────────────────────────────────────

def get_garmin_client():
    client = Garmin(GARMIN_EMAIL, GARMIN_PASSWORD)
    token_file = os.path.join(TOKEN_DIR, "garmin_tokens.json")

    if os.path.exists(token_file):
        try:
            client.client.load(token_file)
            log.info(f"Loaded tokens from {token_file}")

            # Set display name required for many Garmin API endpoints
            client.display_name = GARMIN_DISPLAY_NAME
            log.info(f"Display name set: {client.display_name}")

            return client
        except Exception as e:
            log.warning(f"Token load failed ({e}) - will attempt fresh login")

    if not GARMIN_PASSWORD:
        log.error("No password set and no cached tokens - cannot authenticate")
        log.error("Run the container interactively first to complete auth")
        sys.exit(1)

    try:
        client.login()
        os.makedirs(TOKEN_DIR, exist_ok=True)
        client.client.dump(token_file)
        log.info("Fresh login successful - tokens saved")
        return client
    except GarminConnectAuthenticationError as e:
        log.error(f"Authentication failed: {e}")
        sys.exit(1)


# ── InfluxDB ──────────────────────────────────────────────────────────────────

def get_influx():
    client = InfluxDBClient(
        host=INFLUX_HOST, port=INFLUX_PORT,
        username=INFLUX_USER, password=INFLUX_PASS,
        database=INFLUX_DB,
    )
    client.ping()
    return client


def write(influx, points):
    if points:
        influx.write_points(points)


def base_tags():
    return {"Database_Name": DB_NAME, "Device": DEVICE_NAME}

# ── Date helpers ──────────────────────────────────────────────────────────────

def date_str(d):
    return d.strftime("%Y-%m-%d")


def ts_utc(d, hour=0, minute=0, second=0):
    """Return ISO timestamp string for a date at given UTC time."""
    return datetime(d.year, d.month, d.day, hour, minute, second,
                    tzinfo=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

# ── Sync functions ────────────────────────────────────────────────────────────

def sync_daily_stats(garmin, influx, date):
    """Sync daily summary → DailyStats measurement."""
    try:
        s = garmin.get_stats_and_body(date_str(date))
        if not s:
            s = garmin.get_stats(date_str(date))
        if not s:
            return
        fields = {
            "totalSteps":                  int(s.get("totalSteps") or 0),
            "totalDistanceMeters":         int(s.get("totalDistanceMeters") or 0),
            "activeKilocalories":          float(s.get("activeKilocalories") or 0),
            "bmrKilocalories":             float(s.get("bmrKilocalories") or 0),
            "restingHeartRate":            int(s.get("restingHeartRate") or 0),
            "maxHeartRate":                int(s.get("maxHeartRate") or 0),
            "minHeartRate":                int(s.get("minHeartRate") or 0),
            "maxAvgHeartRate":             int(s.get("maxAvgHeartRate") or 0),
            "minAvgHeartRate":             int(s.get("minAvgHeartRate") or 0),
            "floorsAscended":              float(s.get("floorsAscended") or 0),
            "floorsDescended":             float(s.get("floorsDescended") or 0),
            "floorsAscendedInMeters":      float(s.get("floorsAscendedInMeters") or 0),
            "floorsDescendedInMeters":     float(s.get("floorsDescendedInMeters") or 0),
            "sedentarySeconds":            int(s.get("sedentarySeconds") or 0),
            "activeSeconds":               int(s.get("activeSeconds") or 0),
            "highlyActiveSeconds":         int(s.get("highlyActiveSeconds") or 0),
            "sleepingSeconds":             int(s.get("sleepingSeconds") or 0),
            "moderateIntensityMinutes":    int(s.get("moderateIntensityMinutes") or 0),
            "vigorousIntensityMinutes":    int(s.get("vigorousIntensityMinutes") or 0),
            "bodyBatteryHighestValue":     int(s.get("bodyBatteryHighestValue") or 0),
            "bodyBatteryLowestValue":      int(s.get("bodyBatteryLowestValue") or 0),
            "bodyBatteryChargedValue":     int(s.get("bodyBatteryChargedValue") or 0),
            "bodyBatteryDrainedValue":     int(s.get("bodyBatteryDrainedValue") or 0),
            "bodyBatteryAtWakeTime":       int(s.get("bodyBatteryMostRecentValue") or 0),
            "averageSpo2":                 float(s.get("averageSpO2") or 0),
            "lowestSpo2":                  int(s.get("lowestSpO2") or 0),
            "highStressDuration":          int(s.get("highStressDuration") or 0),
            "mediumStressDuration":        int(s.get("mediumStressDuration") or 0),
            "lowStressDuration":           int(s.get("lowStressDuration") or 0),
            "restStressDuration":          int(s.get("restStressDuration") or 0),
            "stressDuration":              int(s.get("stressDuration") or 0),
            "totalStressDuration":         int(s.get("totalStressDuration") or 0),
            "activityStressDuration":      int(s.get("activityStressDuration") or 0),
            "uncategorizedStressDuration": int(s.get("uncategorizedStressDuration") or 0),
            "highStressPercentage":        float(s.get("highStressPercentage") or 0),
            "mediumStressPercentage":      float(s.get("mediumStressPercentage") or 0),
            "lowStressPercentage":         float(s.get("lowStressPercentage") or 0),
            "restStressPercentage":        float(s.get("restStressPercentage") or 0),
            "stressPercentage":            float(s.get("stressPercentage") or 0),
            "activityStressPercentage":    float(s.get("activityStressPercentage") or 0),
            "uncategorizedStressPercentage": float(s.get("uncategorizedStressPercentage") or 0),
        }
        # Remove zero restingHR — not measured yet today
        if fields["restingHeartRate"] == 0:
            del fields["restingHeartRate"]

        write(influx, [{
            "measurement": "DailyStats",
            "time": ts_utc(date, 23, 0, 0),
            "tags": {**base_tags(), "Device_Name": DEVICE_NAME},
            "fields": fields,
        }])
        log.info(f"  DailyStats: {fields.get('totalSteps', 0):,} steps")
    except Exception as e:
        log.warning(f"  DailyStats failed: {e}")


def sync_heart_rate(garmin, influx, date):
    """Sync intraday heart rate → HeartRateIntraday."""
    try:
        data = garmin.get_heart_rates(date_str(date))
        if not data:
            data = garmin.get_heart_rates_by_date(date_str(date), date_str(date))
        if not data:
            return
        values = data.get("heartRateValues") or []
        points = []
        for entry in values:
            if not entry or len(entry) < 2 or entry[1] is None:
                continue
            ts_ms, hr = entry[0], entry[1]
            ts = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
            points.append({
                "measurement": "HeartRateIntraday",
                "time": ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "tags": base_tags(),
                "fields": {"HeartRate": int(hr)},
            })
        write(influx, points)
        log.info(f"  HeartRateIntraday: {len(points)} points")
    except Exception as e:
        log.warning(f"  HeartRateIntraday failed: {e}")


def sync_steps(garmin, influx, date):
    """Sync intraday steps → StepsIntraday."""
    try:
        data = garmin.get_steps_data(date_str(date))
        if not data:
            return
        # Handle both list and dict response formats
        if isinstance(data, dict):
            entries = data.get("stepsValuesArray") or data.get("steps") or []
        else:
            entries = data
        points = []
        for entry in entries:
            if isinstance(entry, (list, tuple)) and len(entry) >= 2:
                # Array format [timestamp_ms, steps]
                ts_ms, steps = entry[0], entry[1]
                if steps is None or steps == 0:
                    continue
                ts = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
            elif isinstance(entry, dict):
                # Dict format {"startGMT": "2026-05-22T23:00:00.0", "steps": ...}
                ts_str = entry.get("startGMT")
                steps  = entry.get("steps", 0)
                if not ts_str or not steps:
                    continue
                # Handle both "YYYY-MM-DD HH:MM:SS" and "YYYY-MM-DDTHH:MM:SS.0" formats
                ts_str_clean = ts_str[:19].replace("T", " ")
                try:
                    ts = datetime.strptime(ts_str_clean, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                except ValueError:
                    continue
            else:
                continue
            points.append({
                "measurement": "StepsIntraday",
                "time": ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "tags": base_tags(),
                "fields": {"StepsCount": int(steps)},
            })
        write(influx, points)
        log.info(f"  StepsIntraday: {len(points)} points")
    except Exception as e:
        log.warning(f"  StepsIntraday failed: {e}")


def sync_stress(garmin, influx, date):
    """Sync intraday stress and body battery → StressIntraday + BodyBatteryIntraday."""
    try:
        data = garmin.get_stress_data(date_str(date))
        if not data:
            return
        stress_pts = []
        bb_pts     = []

        for entry in (data.get("stressValuesArray") or []):
            if not entry or len(entry) < 2 or entry[1] is None:
                continue
            ts_ms, level = entry[0], entry[1]
            try:
                level = int(level)
            except (ValueError, TypeError):
                continue  # skip non-numeric values like 'MEASURED'
            if level < 0:
                continue
            ts = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
            stress_pts.append({
                "measurement": "StressIntraday",
                "time": ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "tags": base_tags(),
                "fields": {"stressLevel": int(level)},
            })

        bb_values = (data.get("bodyBatteryValuesArray")
                    or data.get("bodyBatteryValues")
                    or [])
        for entry in bb_values:
            if not entry or len(entry) < 3:
                continue
            ts_ms = entry[0]
            # Format: [timestamp_ms, 'MEASURED'/'PREDICTED', battery_level, ...]
            bb = entry[2] if len(entry) > 2 else entry[1]
            try:
                bb = int(bb)
            except (ValueError, TypeError):
                continue
            ts = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
            bb_pts.append({
                "measurement": "BodyBatteryIntraday",
                "time": ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "tags": base_tags(),
                "fields": {"bodyBatteryLevel": int(bb)},
            })

        write(influx, stress_pts)
        write(influx, bb_pts)
        log.info(f"  StressIntraday: {len(stress_pts)} pts  BodyBatteryIntraday: {len(bb_pts)} pts")
    except Exception as e:
        log.warning(f"  Stress/BodyBattery failed: {e}")


def sync_hrv(garmin, influx, date):
    """Sync HRV → HRV_Intraday + avgOvernightHrv to SleepSummary."""
    try:
        data = garmin.get_hrv_data(date_str(date))
        if not data:
            return
        readings = data.get("hrvReadings") or []
        points = []
        for r in readings:
            # Actual field name is readingTimeGMT, not startTimeGMT
            ts_str = r.get("readingTimeGMT")
            hrv    = r.get("hrvValue")
            if not ts_str or hrv is None:
                continue
            # Format: "2026-05-23T22:23:46.0" — strip the .0
            ts_str_clean = ts_str[:19].replace("T", " ")
            try:
                ts = datetime.strptime(ts_str_clean, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            except ValueError:
                continue
            points.append({
                "measurement": "HRV_Intraday",
                "time": ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "tags": base_tags(),
                "fields": {"hrvValue": int(hrv)},
            })
        write(influx, points)

        # Also write avgOvernightHrv to SleepSummary
        summary = data.get("hrvSummary") or {}
        avg_hrv = summary.get("lastNightAvg")
        if avg_hrv:
            sleep_ts = data.get("sleepStartTimestampGMT")
            if sleep_ts:
                ts_str_clean = sleep_ts[:19].replace("T", " ")
                try:
                    ts = datetime.strptime(ts_str_clean, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                    write(influx, [{
                        "measurement": "SleepSummary",
                        "time": ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "tags": base_tags(),
                        "fields": {"avgOvernightHrv": float(avg_hrv)},
                    }])
                except ValueError:
                    pass

        log.info(f"  HRV_Intraday: {len(points)} points, lastNightAvg={avg_hrv} ms")
    except Exception as e:
        log.warning(f"  HRV failed: {e}")


def sync_breathing(garmin, influx, date):
    """Sync intraday respiration → BreathingRateIntraday."""
    try:
        data = garmin.get_respiration_data(date_str(date))
        if not data:
            return
        readings = data.get("respirationValues") or []
        points = []
        for r in readings:
            ts_str = r.get("startTimeGMT")
            rate   = r.get("respirationValue")
            if not ts_str or rate is None or rate < 0:
                continue
            try:
                ts = datetime.strptime(ts_str[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            except ValueError:
                continue
            points.append({
                "measurement": "BreathingRateIntraday",
                "time": ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "tags": base_tags(),
                "fields": {"breathingRate": float(rate)},
            })
        write(influx, points)
        log.info(f"  BreathingRateIntraday: {len(points)} points")
    except Exception as e:
        log.warning(f"  Breathing failed: {e}")


def sync_sleep(garmin, influx, date):
    """Sync sleep summary → SleepSummary."""
    try:
        data = garmin.get_sleep_data(date_str(date))
        if not data or "dailySleepDTO" not in data:
            return
        s = data["dailySleepDTO"]
        fields = {
            "sleepTimeSeconds":      int(s.get("sleepTimeSeconds") or 0),
            "deepSleepSeconds":      int(s.get("deepSleepSeconds") or 0),
            "lightSleepSeconds":     int(s.get("lightSleepSeconds") or 0),
            "remSleepSeconds":       int(s.get("remSleepSeconds") or 0),
            "awakeSleepSeconds":     int(s.get("awakeSleepSeconds") or 0),
            "sleepScore":            int((s.get("sleepScores") or {}).get("overall", {}).get("value") or
                                        s.get("sleepScore") or 0),
            "avgOvernightHrv":       float(s.get("avgOvernightHrv") or 0),
            "restingHeartRate":      int(s.get("restingHeartRate") or 0),
            "averageRespirationValue": float(s.get("averageRespirationValue") or 0),
            "lowestRespirationValue":  float(s.get("lowestRespirationValue") or 0),
            "highestRespirationValue": float(s.get("highestRespirationValue") or 0),
            "bodyBatteryChange":     int(s.get("bodyBatteryChange") or 0),
            "avgSleepStress":        float(s.get("avgSleepStress") or 0),
            "restlessMomentsCount":  int(s.get("restlessMomentsCount") or 0),
            "awakeCount":            int(s.get("awakeCount") or 0),
        }
        # Timestamp: use sleep start time if available
        sleep_start = s.get("sleepStartTimestampGMT")
        if sleep_start:
            ts = datetime.fromtimestamp(sleep_start / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        else:
            ts = ts_utc(date, 0, 0, 0)

        write(influx, [{
            "measurement": "SleepSummary",
            "time": ts,
            "tags": base_tags(),
            "fields": fields,
        }])
        hrs = fields["sleepTimeSeconds"] / 3600
        log.info(f"  SleepSummary: {hrs:.1f}h, score={fields['sleepScore']}")
    except Exception as e:
        log.warning(f"  Sleep failed: {e}")


def sync_body_composition(garmin, influx, date):
    """Sync weight → BodyComposition (stored in grams to match garmin-grafana)."""
    try:
        data = garmin.get_body_composition(date_str(date))
        if not data:
            return
        entries = data.get("totalAverage") or data.get("dateWeightList") or []
        if isinstance(entries, dict):
            entries = [entries]
        points = []
        for entry in entries:
            weight_g = entry.get("weight")
            if weight_g is None:
                continue
            # Garmin returns weight in grams
            ts_str = entry.get("date") or date_str(date)
            try:
                ts = datetime.strptime(ts_str[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
            except ValueError:
                ts = datetime.now(timezone.utc)
            points.append({
                "measurement": "BodyComposition",
                "time": ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "tags": base_tags(),
                "fields": {"weight": float(weight_g)},
            })
        write(influx, points)
        if points:
            kg = points[-1]["fields"]["weight"] / 1000
            log.info(f"  BodyComposition: {kg:.1f} kg")
    except Exception as e:
        log.warning(f"  BodyComposition failed: {e}")



# ── Workout plan exercise name lookup ─────────────────────────────────────────

def get_workout_name_map(garmin, act_id):
    """
    Fetch the associated workout plan for an activity and build a lookup
    mapping Garmin category+exercise keys to human-readable exercise names.
    
    Returns dict: {"CATEGORY EXERCISE_NAME": "Human Name", ...}
    e.g. {"SUSPENSION GLUTE_BRIDGE": "Dumbbell Bench Glute Bridge"}
    """
    try:
        activity = garmin.connectapi(f"/activity-service/activity/{act_id}")
        if not isinstance(activity, dict):
            return {}
        
        workout_id = (activity.get("metadataDTO") or {}).get("associatedWorkoutId")
        if not workout_id:
            return {}
        
        workout = garmin.connectapi(f"/workout-service/workout/{workout_id}")
        if not isinstance(workout, dict):
            return {}
        
        name_map = {}
        for segment in (workout.get("workoutSegments") or []):
            for group in (segment.get("workoutSteps") or []):
                for step in (group.get("workoutSteps") or []):
                    description = step.get("description")
                    category    = step.get("category") or ""
                    ex_name     = step.get("exerciseName") or ""
                    garmin_key  = f"{category} {ex_name}".strip()
                    if description and garmin_key:
                        name_map[garmin_key] = description
        
        if name_map:
            log.info(f"  Loaded {len(name_map)} exercise name mappings from workout plan {workout_id}")
        return name_map

    except Exception as e:
        log.debug(f"Could not load workout plan for activity {act_id}: {e}")
        return {}

def sync_activities(garmin, influx, date):
    """Sync activities → ActivitySummary + StrengthSets."""
    try:
        activities = garmin.get_activities_by_date(date_str(date), date_str(date))
        if not activities:
            return

        act_points  = []
        set_points  = []

        for act in activities:
            act_id   = act.get("activityId")
            act_name = act.get("activityName", "Unknown")
            act_type = (act.get("activityType") or {}).get("typeKey", "unknown")
            start    = act.get("startTimeGMT") or act.get("startTimeLocal", "")

            try:
                ts = datetime.strptime(start[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            except (ValueError, AttributeError):
                continue

            # Fetch activity details for step count
            activity_steps = 0
            try:
                time.sleep(0.3)
                details = garmin.get_activity_details(act_id)
                if isinstance(details, dict):
                    # Steps in activity details
                    activity_steps = int(
                        details.get("summaryDTO", {}).get("steps") or
                        details.get("steps") or 0
                    )
            except Exception as e:
                log.debug(f"Could not get activity details for {act_id}: {e}")

            act_points.append({
                "measurement": "ActivitySummary",
                "time": ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "tags": {
                    **base_tags(),
                    "ActivitySelector": f"{ts.strftime('%Y%m%dT%H%M%S')}UTC-{act_type}",
                },
                "fields": {
                    "Activity_ID":          int(act_id or 0),
                    "activityName":         str(act_name),
                    "activityType":         str(act_type),
                    "calories":             float(act.get("calories") or 0),
                    "averageHR":            float(act.get("averageHR") or 0),
                    "maxHR":                float(act.get("maxHR") or 0),
                    "distance":             float(act.get("distance") or 0),
                    "elapsedDuration":      float(act.get("duration") or 0),
                    "movingDuration":       float(act.get("movingDuration") or 0),
                    "activityTrainingLoad": float(act.get("activityTrainingLoad") or 0),
                    "aerobicTrainingEffect":   float(act.get("aerobicTrainingEffect") or 0),
                    "anaerobicTrainingEffect": float(act.get("anaerobicTrainingEffect") or 0),
                    "hrTimeInZone_1":       int(act.get("hrTimeInZone_1") or 0),
                    "hrTimeInZone_2":       int(act.get("hrTimeInZone_2") or 0),
                    "hrTimeInZone_3":       int(act.get("hrTimeInZone_3") or 0),
                    "hrTimeInZone_4":       int(act.get("hrTimeInZone_4") or 0),
                    "hrTimeInZone_5":       int(act.get("hrTimeInZone_5") or 0),
                    "steps":                int(activity_steps),
                },
            })

            # Strength set detail
            is_strength = act_type in STRENGTH_TYPES or "strength" in act_type.lower()
            if is_strength and act_id:
                try:
                    # Get exercise name mapping from associated workout plan
                    name_map = get_workout_name_map(garmin, act_id)

                    time.sleep(0.5)
                    sets_data = garmin.get_activity_exercise_sets(act_id)
                    sets = []
                    if isinstance(sets_data, dict):
                        sets = sets_data.get("exerciseSets", [])
                    elif isinstance(sets_data, list):
                        sets = sets_data

                    set_num = 0
                    for ex_set in sets:
                        exercises = ex_set.get("exercises", [{}])
                        ex = exercises[0] if exercises else {}
                        garmin_key = (
                            (ex.get("category") or "") + " " + (ex.get("name") or "")
                        ).strip()

                        # Use workout plan name if available, fall back to Garmin key
                        # Also try category-only lookup for variant exercise names
                        category_only = (ex.get("category") or "").strip()
                        ex_name = (
                            name_map.get(garmin_key) or
                            name_map.get(f"{category_only} LEG_CURL") or
                            name_map.get(f"{category_only} LEG_EXTENSIONS") or
                            next((v for k, v in name_map.items() if category_only and k.startswith(category_only)), None) or
                            garmin_key or "Unknown"
                        )
                        source  = "plan" if ex_name != garmin_key else "garmin"

                        reps     = int(ex_set.get("repetitionCount") or 0)
                        weight_g = float(ex_set.get("weight") or 0)
                        weight_kg = weight_g / 1000 if weight_g > 500 else weight_g
                        duration  = float(ex_set.get("duration") or 0)

                        # Skip rest periods and unknown sets (weight=-1 indicates a rest marker)
                        if weight_g < 0 or (reps == 0 and duration == 0):
                            continue
                        if ex_name == "Unknown" or ex_name.strip() == "":
                            continue

                        set_num += 1
                        set_ts = ts + timedelta(seconds=set_num)
                        set_points.append({
                            "measurement": "StrengthSets",
                            "time": set_ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
                            "tags": {
                                **base_tags(),
                                "activity_id": str(act_id),
                                "exercise":    str(ex_name)[:64],
                                "source":      source,
                            },
                            "fields": {
                                "set_number":       int(set_num),
                                "reps":             int(reps),
                                "weight_kg":        float(weight_kg),
                                "volume_kg":        float(weight_kg * reps),
                                "duration_seconds": float(duration),
                            },
                        })

                    if set_num:
                        log.info(f"  StrengthSets: {set_num} sets for '{act_name}'")
                except Exception as e:
                    log.warning(f"  StrengthSets failed for {act_id}: {e}")

        write(influx, act_points)
        if act_points:
            names = [p["fields"]["activityName"] for p in act_points]
            log.info(f"  ActivitySummary: {names}")
        write(influx, set_points)

    except Exception as e:
        log.warning(f"  Activities failed: {e}")


def sync_device(garmin, influx):
    """Write device sync timestamp → DeviceSync."""
    try:
        devices = garmin.get_devices()
        if not devices:
            return
        dev = devices[0]
        write(influx, [{
            "measurement": "DeviceSync",
            "time": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "tags": base_tags(),
            "fields": {
                "Device_Name": str(dev.get("productDisplayName", DEVICE_NAME)),
                "Database_Name": DB_NAME,
            },
        }])
    except Exception as e:
        log.warning(f"  DeviceSync failed: {e}")


# ── Main sync loop ────────────────────────────────────────────────────────────

def run_sync(garmin, influx, days_back):
    today = datetime.now(timezone.utc).date()
    dates = [today - timedelta(days=i) for i in range(days_back + 1)]

    log.info(f"Syncing {len(dates)} day(s): {dates[-1]} → {dates[0]}")
    sync_device(garmin, influx)

    for date in dates:
        log.info(f"── {date} ──────────────────────────────────")
        sync_daily_stats(garmin, influx, date)
        sync_heart_rate(garmin, influx, date)
        sync_steps(garmin, influx, date)
        sync_stress(garmin, influx, date)
        sync_breathing(garmin, influx, date)
        sync_sleep(garmin, influx, date)
        sync_hrv(garmin, influx, date)   # runs after sleep so HRV overwrites sleep's 0 value
        sync_body_composition(garmin, influx, date)
        sync_activities(garmin, influx, date)
        time.sleep(2)  # be polite to Garmin API


def main():
    log.info("=" * 60)
    log.info("Garmin Direct Sync Starting")
    log.info(f"Sync interval: {SYNC_INTERVAL}s  Days back: {SYNC_DAYS_BACK}")
    log.info("=" * 60)

    garmin = get_garmin_client()
    influx = get_influx()

    while True:
        try:
            run_sync(garmin, influx, SYNC_DAYS_BACK)
        except (GarminConnectConnectionError, Exception) as e:
            log.error(f"Sync failed: {e}")
            # Refresh client on connection errors
            try:
                garmin = get_garmin_client()
            except Exception:
                pass

        log.info(f"Sleeping {SYNC_INTERVAL}s until next sync...")
        time.sleep(SYNC_INTERVAL)


if __name__ == "__main__":
    main()
