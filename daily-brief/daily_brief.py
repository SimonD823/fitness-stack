#!/usr/bin/env python3
"""
Daily Training Brief — Email Generator
Runs at 6:45 AM, queries InfluxDB for yesterday's data,
calls LM Studio for AI coaching analysis, sends HTML email at 7 AM.

Scheduler: runs continuously, sleeps until 6:45 AM each day.
Container: python:3.12-slim on NAS via Container Station.
"""

import datetime
import re
import json
import logging
import os
import sys
import time

import requests
from influxdb import InfluxDBClient

# ── Configuration ─────────────────────────────────────────────────────────────

INFLUX_HOST  = os.environ.get("INFLUX_HOST", "192.168.1.60")
INFLUX_PORT  = int(os.environ.get("INFLUX_PORT", "8086"))
INFLUX_USER  = os.environ.get("INFLUX_USER", "influxdb_user")
INFLUX_PASS  = os.environ.get("INFLUX_PASS", "influxdb_secret_password")

LM_STUDIO_URL = os.environ.get("OLLAMA_URL", os.environ.get("LM_STUDIO_URL", "http://max:11434"))
LM_MODEL      = os.environ.get("LM_MODEL", "fitness-coach")

BREVO_API_KEY   = os.environ.get("BREVO_API_KEY", "")
EMAIL_FROM      = os.environ.get("EMAIL_FROM", "ac7cb9001@smtp-brevo.com")
EMAIL_FROM_NAME = os.environ.get("EMAIL_FROM_NAME", "AI Fitness Coach")
EMAIL_TO        = os.environ.get("EMAIL_TO", "simon_davies@hotmail.com")

SEND_HOUR     = int(os.environ.get("SEND_HOUR", "6"))
SEND_MINUTE   = int(os.environ.get("SEND_MINUTE", "45"))

# Training plan context — update week number weekly
# Auto-calculate training week from plan start date (1 June 2026)
# Override with TRAINING_WEEK env var if set manually
_plan_start     = datetime.date(2026, 6, 1)   # Week 1 starts Monday 1 June
_days_elapsed   = (datetime.date.today() - _plan_start).days
_auto_week      = max(1, min(13, (_days_elapsed // 7) + 1))
TRAINING_WEEK   = int(os.environ.get("TRAINING_WEEK", str(_auto_week)))
TREADMILL_GUIDE = os.environ.get("TREADMILL_GUIDE", "/app/TRAINING_PLAN_V2.md")
TREADMILL_GUIDE    = os.environ.get("TREADMILL_GUIDE",    "/app/TRAINING_PLAN_V2.md")
ATHLETE_NOTES_FILE = os.environ.get("ATHLETE_NOTES_FILE", "/app/athlete_notes.txt")
EVENT_DATE    = "Saturday 29 August 2026"
EVENT_NAME    = "Peddars Way 100,000 Steps Challenge"

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger(__name__)


# ── Treadmill session extraction ──────────────────────────────────────────────

def get_tomorrows_session_type():
    """
    Return a short label for tomorrow's scheduled session type, for use in the
    COACH'S INTERPRETATION conditional outlook. Mirrors the day_type_map in
    get_todays_session() but does not need the treadmill guide detail.
    """
    tomorrow = datetime.date.today() + datetime.timedelta(days=1)
    weekday = tomorrow.weekday()  # 0=Mon ... 6=Sun

    caliber_start = datetime.date(2026, 6, 1)
    caliber_active = tomorrow >= caliber_start

    if not caliber_active:
        pre_caliber_map = {
            0: "Long walk — Zone 2",
            1: "Split session: morning outdoor GPS VO2 Max walk (20 min) + separate afternoon/evening treadmill Zone 2 walk",
            2: "Long walk — Zone 2",
            3: "Split session: morning outdoor GPS VO2 Max walk (20 min) + separate afternoon/evening treadmill Zone 2 walk",
            4: "Long walk — Zone 2",
            5: "Saturday long walk",
            6: "Rest day",
        }
        return pre_caliber_map.get(weekday, "Rest day")

    day_type_map = {
        0: "Caliber Legs & Abs + short walk",
        1: "Split session: morning outdoor GPS VO2 Max walk (20 min) + separate afternoon/evening treadmill Zone 2 walk",
        2: "Caliber Back & Shoulders + short walk",
        3: "Split session: morning outdoor GPS VO2 Max walk (20 min) + separate afternoon/evening treadmill Zone 2 walk",
        4: "Caliber Chest & Arms + short walk",
        5: "Saturday long walk (progressive distance, outdoor preferred)",
        6: "Rest day",
    }
    return day_type_map.get(weekday, "Rest day")


def get_athlete_notes():
    """
    Read athlete_notes.txt — a plain text file the athlete edits manually to
    inform the coach of health events, injuries, medication, or life context
    that Garmin data cannot capture (e.g. infections, antibiotics, travel fatigue).
    Returns the file contents as a string, or None if the file is empty or missing.

    Format: free text, one item per line. Example:
        Horsefly bite on right arm — prescribed flucloxacillin 500mg 4x daily.
        Arm swollen and warm. Systemic inflammation expected.
        Started antibiotics 2026-06-23. Course ends approx 2026-06-30.

    Clear the file (leave blank or delete contents) when the condition resolves.
    """
    try:
        if not os.path.exists(ATHLETE_NOTES_FILE):
            return None
        with open(ATHLETE_NOTES_FILE, "r") as f:
            content = f.read().strip()
        return content if content else None
    except Exception as e:
        log.warning(f"Could not read athlete notes: {e}")
        return None


def get_todays_session():
    """Extract today's scheduled treadmill session from the training guide."""
    today = datetime.date.today()
    weekday = today.weekday()  # 0=Mon, 1=Tue, 4=Fri

    # New 6-day structure from 1 June 2026:
    # Mon/Wed/Fri = Caliber + short walk (~5,000 steps)
    # Tue/Thu = split: morning outdoor GPS VO2 Max walk (20 min) + afternoon/evening treadmill Zone 2 walk
    # Sat = progressive long walk (outdoor preferred)
    # Sun = Rest
    caliber_start = datetime.date(2026, 6, 1)
    caliber_active = datetime.date.today() >= caliber_start

    if not caliber_active:
        # Before 1 June — Caliber not started yet, treadmill only structure
        pre_caliber_map = {
            0: ("long_walk", "Treadmill walk — 70 min Zone 2 (Caliber starts 1 June)"),
            1: ("long_walk", "Treadmill walk — 70 min Zone 2"),
            2: ("long_walk", "Treadmill walk — 70 min Zone 2 (Caliber starts 1 June)"),
            3: ("long_walk", "Treadmill walk — 70 min Zone 2"),
            4: ("long_walk", "Treadmill walk — 70 min Zone 2 (Caliber starts 1 June)"),
            5: ("long_walk", "Saturday long walk — build endurance"),
            6: ("rest", "Rest Day — light movement only based on body battery"),
        }
        day_info = pre_caliber_map.get(weekday, ("rest", "Rest"))
    else:
        day_type_map = {
            0: ("caliber", "Legs & Abs + Short Walk (40-45 min, ~5,000 steps)"),
            1: ("long_walk", "Long Walk — 70+ min, Zone 2, ~8,000-10,000 steps"),
            2: ("caliber", "Back & Shoulders + Short Walk (40-45 min, ~5,000 steps)"),
            3: ("long_walk", "Long Walk — 70+ min, Zone 2, ~8,000-10,000 steps"),
            4: ("caliber", "Chest & Arms + Short Walk (40-45 min, ~5,000 steps)"),
            5: ("long_walk", f"SATURDAY LONG WALK — see Week {TRAINING_WEEK} target in training plan"),
            6: ("rest", "Rest Day — light movement only based on body battery"),
        }
        day_info = day_type_map.get(weekday, ("rest", "Rest"))

    session_type, session_label = day_info

    if not os.path.exists(TREADMILL_GUIDE):
        log.warning(f"Treadmill guide not found at {TREADMILL_GUIDE}")
        return session_type, session_label, None

    try:
        with open(TREADMILL_GUIDE, 'r') as f:
            guide = f.read()

        # Find current week section — plan uses #### Week N headings
        pattern = rf'####\s+\*?\*?Week {TRAINING_WEEK}[\s(]'
        match = re.search(pattern, guide, re.IGNORECASE)
        if not match:
            log.warning(f"Week {TRAINING_WEEK} not found in treadmill guide")
            return session_type, session_label, None

        start = match.start()
        next_week = re.search(r'####\s+\*?\*?Week \d+', guide[start+10:])
        end = start + 10 + next_week.start() if next_week else len(guide)
        week_text = guide[start:end]

        # Extract the specific session
        session_pattern = rf'\*\*Session {session_label[0]}.*?(?=\*\*Session [ABC]|---\s*$|\Z)'
        session_match = re.search(session_pattern, week_text, re.DOTALL)
        if session_match:
            return session_type, session_label, session_match.group(0).strip()

        return session_type, session_label, week_text.strip()

    except Exception as e:
        log.warning(f"Could not load treadmill session: {e}")
        return session_type, session_label, None

# ── InfluxDB queries ──────────────────────────────────────────────────────────

def query_influx(db, q):
    client = InfluxDBClient(
        host=INFLUX_HOST, port=INFLUX_PORT,
        username=INFLUX_USER, password=INFLUX_PASS,
        database=db,
    )
    result = client.query(q)
    client.close()
    points = list(result.get_points())
    return points


def get_weather_forecast():
    """
    Fetch today's weather forecast for Watton, Norfolk using Open-Meteo.
    Free, no API key required. Returns a dict with conditions and a
    pre-computed outdoor suitability note for the daily coaching prompt.
    """
    try:
        url = (
            "https://api.open-meteo.com/v1/forecast"
            "?latitude=52.57&longitude=0.83"
            "&daily=weathercode,temperature_2m_max,temperature_2m_min,"
            "precipitation_sum,windspeed_10m_max,precipitation_probability_max"
            "&hourly=temperature_2m,precipitation_probability,weathercode,windspeed_10m"
            "&timezone=Europe%2FLondon"
            "&forecast_days=1"
        )
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        daily  = data.get("daily", {})
        hourly = data.get("hourly", {})

        wcode     = (daily.get("weathercode") or [0])[0]
        temp_max  = (daily.get("temperature_2m_max") or [0])[0]
        temp_min  = (daily.get("temperature_2m_min") or [0])[0]
        rain_mm   = (daily.get("precipitation_sum") or [0])[0]
        wind_max  = (daily.get("windspeed_10m_max") or [0])[0]
        rain_prob = (daily.get("precipitation_probability_max") or [0])[0]

        wmo_desc = {
            0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
            45: "Foggy", 48: "Icy fog",
            51: "Light drizzle", 53: "Moderate drizzle", 55: "Dense drizzle",
            61: "Slight rain", 63: "Moderate rain", 65: "Heavy rain",
            71: "Slight snow", 73: "Moderate snow", 75: "Heavy snow",
            80: "Slight showers", 81: "Moderate showers", 82: "Violent showers",
            95: "Thunderstorm", 96: "Thunderstorm with hail",
        }
        description = wmo_desc.get(wcode, f"Code {wcode}")

        morning_rain_probs = (hourly.get("precipitation_probability") or [])[8:13]
        morning_rain_max   = max(morning_rain_probs) if morning_rain_probs else rain_prob
        morning_temps      = (hourly.get("temperature_2m") or [])[8:13]
        morning_temp_avg   = round(sum(morning_temps) / len(morning_temps), 1) if morning_temps else temp_max

        if rain_mm > 5 or morning_rain_max >= 70:
            outdoor = "poor — significant rain expected, recommend treadmill"
        elif rain_mm > 2 or morning_rain_max >= 40:
            outdoor = "marginal — light rain possible, treadmill preferred"
        elif wind_max > 40:
            outdoor = "marginal — strong winds forecast"
        elif temp_max > 28:
            outdoor = "caution — high heat risk for Zone 2, start early or use treadmill"
        elif temp_max < 2:
            outdoor = "caution — near-freezing, watch for ice"
        else:
            outdoor = "good — suitable for outdoor Zone 2 walk"

        return {
            "description":         description,
            "temp_max":            temp_max,
            "temp_min":            temp_min,
            "rain_mm":             rain_mm,
            "rain_prob":           rain_prob,
            "wind_max":            wind_max,
            "morning_temp":        morning_temp_avg,
            "morning_rain_pct":    morning_rain_max,
            "outdoor_suitability": outdoor,
        }
    except Exception as e:
        log.warning(f"Weather fetch failed: {e}")
        return {}


def get_yesterday_metrics():
    yesterday = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
    today     = datetime.date.today().isoformat()

    metrics = {}

    # Use 07:00 UTC window — a "training day" runs morning to morning
    # This ensures last night's sleep, HRV, and evening activities all
    # belong to the same reporting day rather than being split at midnight
    window_start = f"{yesterday}T07:00:00Z"
    window_end   = f"{today}T07:00:00Z"

    # Daily stats
    rows = query_influx("GarminStats",
        f"SELECT totalSteps, totalDistanceMeters, restingHeartRate, "
        f"activeKilocalories, bodyBatteryHighestValue, bodyBatteryLowestValue, "
        f"highStressDuration, lowStressDuration, moderateIntensityMinutes, "
        f"vigorousIntensityMinutes "
        f"FROM DailyStats WHERE time >= '{window_start}' "
        f"AND time < '{window_end}' ORDER BY time DESC LIMIT 1"
    )
    if rows:
        r = rows[0]
        metrics["steps"]               = r.get("totalSteps", 0)
        metrics["distance_km"]         = round((r.get("totalDistanceMeters") or 0) / 1000, 2)
        metrics["resting_hr"]          = r.get("restingHeartRate", 0)
        metrics["active_calories"]     = r.get("activeKilocalories", 0)
        metrics["body_battery_high"]   = r.get("bodyBatteryHighestValue", 0)
        metrics["body_battery_low"]    = r.get("bodyBatteryLowestValue", 0)
        metrics["high_stress_mins"]    = round((r.get("highStressDuration") or 0) / 60, 0)
        metrics["low_stress_mins"]     = round((r.get("lowStressDuration") or 0) / 60, 0)
        metrics["moderate_intensity"]  = r.get("moderateIntensityMinutes", 0)
        metrics["vigorous_intensity"]  = r.get("vigorousIntensityMinutes", 0)

    # Sleep
    # Sleep query: 07:00 UTC window captures last night's sleep
    # Sleep starts ~21:00-22:00 UTC yesterday, well within the window
    # Filter out zero-value records with sleepTimeSeconds > 3600
    rows = query_influx("GarminStats",
        f"SELECT sleepTimeSeconds, deepSleepSeconds, remSleepSeconds, "
        f"sleepScore, avgOvernightHrv, restingHeartRate, bodyBatteryChange "
        f"FROM SleepSummary WHERE time >= '{window_start}' "
        f"AND time < '{window_end}' AND sleepTimeSeconds > 3600 "
        f"ORDER BY time DESC LIMIT 1"
    )
    if rows:
        r = rows[0]
        metrics["sleep_hours"]      = round((r.get("sleepTimeSeconds") or 0) / 3600, 1)
        metrics["deep_sleep_mins"]  = round((r.get("deepSleepSeconds") or 0) / 60, 0)
        metrics["rem_sleep_mins"]   = round((r.get("remSleepSeconds") or 0) / 60, 0)
        metrics["sleep_score"]      = r.get("sleepScore", 0)
        metrics["overnight_hrv"]    = round(r.get("avgOvernightHrv") or 0, 1)
        metrics["sleep_rhr"]        = r.get("restingHeartRate", 0)
        api_change = r.get("bodyBatteryChange", 0) or 0
        # bodyBatteryChange from Garmin API often returns 0 even when it changed
        # Calculate from high/low values instead — matches what the watch displays
        bb_high = metrics.get("body_battery_high", 0) or 0
        bb_low  = metrics.get("body_battery_low", 0) or 0
        if api_change and api_change != 0:
            metrics["battery_change"] = api_change
        elif bb_high and bb_low and bb_high > bb_low:
            metrics["battery_change"] = bb_high - bb_low
        else:
            metrics["battery_change"] = 0

    # Weight (most recent within window)
    rows = query_influx("GarminStats",
        f"SELECT last(weight) FROM BodyComposition "
        f"WHERE time >= '{window_start}' AND time < '{window_end}'"
    )
    if not rows:
        # Fall back to most recent ever if none in window
        rows = query_influx("GarminStats", "SELECT last(weight) FROM BodyComposition")
    if rows:
        w = rows[0].get("last", 0)
        metrics["weight_kg"] = round((w / 1000) if w > 1000 else w, 2)

    # Activities in window
    rows = query_influx("GarminStats",
        f"SELECT activityName, activityType, calories, averageHR, "
        f"elapsedDuration, activityTrainingLoad, steps "
        f"FROM ActivitySummary WHERE time >= '{window_start}' "
        f"AND time < '{window_end}' ORDER BY time ASC"
    )
    activities = []
    for r in rows:
        if r.get("activityType") == "No Activity":
            continue
        act_type = r.get("activityType", "")
        act_name = r.get("activityName", "Unknown")
        # Garmin logs treadmill walking sessions as "treadmill_running" / "Treadmill Running",
        # which causes the AI to describe Zone 2 walks as "runs". Relabel for clarity.
        if act_type == "treadmill_running":
            act_name = "Treadmill Walk"
            act_type = "treadmill_walking"
        activities.append({
            "name":          act_name,
            "type":          act_type,
            "calories":      int(r.get("calories") or 0),
            "avg_hr":        int(r.get("averageHR") or 0),
            "duration_mins": int(round((r.get("elapsedDuration") or 0) / 60, 0)),
            "training_load": round(r.get("activityTrainingLoad") or 0, 1),
            "steps":         int(r.get("steps") or 0),
        })
    metrics["activities"] = activities

    # Strength sets in window
    rows = query_influx("GarminStats",
        f"SELECT exercise, reps, weight_kg, volume_kg "
        f"FROM StrengthSets WHERE time >= '{window_start}' "
        f"AND time < '{window_end}' ORDER BY time ASC"
    )
    strength = {}
    for r in rows:
        ex = r.get("exercise", "Unknown")
        if ex not in strength:
            strength[ex] = {"sets": 0, "total_reps": 0, "max_weight": 0, "volume": 0}
        strength[ex]["sets"]       += 1
        strength[ex]["total_reps"] += int(r.get("reps") or 0)
        strength[ex]["max_weight"]  = max(strength[ex]["max_weight"], float(r.get("weight_kg") or 0))
        strength[ex]["volume"]     += float(r.get("volume_kg") or 0)
    metrics["strength"] = strength

    # Caliber workouts in window
    rows = query_influx("CaliberStats",
        f"SELECT workoutTitle, durationSeconds, totalSets, totalVolumeKg, exerciseCount "
        f"FROM CaliberWorkout WHERE time >= '{window_start}' "
        f"AND time < '{window_end}' ORDER BY time ASC"
    )
    caliber_sessions = []
    for r in rows:
        caliber_sessions.append({
            "title":          r.get("workoutTitle", "Unknown"),
            "duration_mins":  int(round((r.get("durationSeconds") or 0) / 60, 0)),
            "exercises":      int(r.get("exerciseCount") or 0),
            "total_sets":     int(r.get("totalSets") or 0),
            "total_volume":   round(r.get("totalVolumeKg") or 0, 1),
        })
    metrics["caliber_sessions"] = caliber_sessions

    # Caliber set detail in window
    rows = query_influx("CaliberStats",
        f"SELECT exercise, reps, weight_kg, volume_kg "
        f"FROM CaliberSets WHERE time >= '{window_start}' "
        f"AND time < '{window_end}' ORDER BY time ASC"
    )
    caliber_sets = {}
    for r in rows:
        ex = r.get("exercise", "Unknown")
        if ex not in caliber_sets:
            caliber_sets[ex] = {"sets": 0, "total_reps": 0, "max_weight": 0, "volume": 0}
        caliber_sets[ex]["sets"]       += 1
        caliber_sets[ex]["total_reps"] += int(r.get("reps") or 0)
        caliber_sets[ex]["max_weight"]  = max(caliber_sets[ex]["max_weight"], float(r.get("weight_kg") or 0))
        caliber_sets[ex]["volume"]     += float(r.get("volume_kg") or 0)
    metrics["caliber_sets"] = caliber_sets

    # Nutrition: keeps 00:00 UTC boundary since Cronometer timestamps at midnight
    rows = query_influx("CronometerStats",
        f"SELECT Energy_kcal, Protein_g, Fat_g, Carbs_g, Net_Carbs_g "
        f"FROM daily_nutrition WHERE time >= '{yesterday}T00:00:00Z' "
        f"AND time < '{today}T00:00:00Z' ORDER BY time DESC LIMIT 1"
    )
    if rows:
        r = rows[0]
        metrics["calories_in"]  = int(r.get("Energy_kcal") or 0)
        metrics["protein_g"]    = round(r.get("Protein_g") or 0, 1)
        metrics["fat_g"]        = round(r.get("Fat_g") or 0, 1)
        metrics["carbs_g"]      = round(r.get("Carbs_g") or 0, 1)
        metrics["net_carbs_g"]  = round(r.get("Net_Carbs_g") or 0, 1)

    # ── ENRICHMENT 1: HRV baseline (30-day rolling average) ──────────────────
    # Without a baseline, Ollama cannot tell if today's HRV is good or bad.
    try:
        thirty_days_ago = (datetime.date.today() - datetime.timedelta(days=31)).isoformat()
        rows = query_influx("GarminStats",
            f"SELECT mean(avgOvernightHrv) AS hrv_avg "
            f"FROM SleepSummary "
            f"WHERE time >= '{thirty_days_ago}T07:00:00Z' "
            f"AND time < '{yesterday}T07:00:00Z' "
            f"AND avgOvernightHrv > 0"
        )
        if rows and rows[0].get("hrv_avg"):
            baseline  = round(rows[0]["hrv_avg"], 1)
            today_hrv = metrics.get("overnight_hrv", 0) or 0
            if baseline > 0 and today_hrv > 0:
                delta_pct = round(((today_hrv - baseline) / baseline) * 100, 1)
                metrics["hrv_baseline"]  = baseline
                metrics["hrv_delta_pct"] = delta_pct
                if delta_pct >= 5:
                    metrics["hrv_status"] = "above baseline — enhanced readiness"
                elif delta_pct >= -5:
                    metrics["hrv_status"] = "within normal range"
                elif delta_pct >= -10:
                    metrics["hrv_status"] = "mildly suppressed — train with caution"
                else:
                    metrics["hrv_status"] = "significantly suppressed — consider deload"
    except Exception as e:
        log.warning(f"HRV baseline query failed: {e}")

    # ── ENRICHMENT 2: Week-to-date step progress ──────────────────────────────
    try:
        today_dt          = datetime.date.today()
        days_since_monday = today_dt.weekday()
        monday            = (today_dt - datetime.timedelta(days=days_since_monday)).isoformat()
        rows = query_influx("GarminStats",
            f"SELECT sum(totalSteps) AS week_steps "
            f"FROM DailyStats "
            f"WHERE time >= '{monday}T07:00:00Z' "
            f"AND time < '{today}T07:00:00Z'"
        )
        if rows and rows[0].get("week_steps") is not None:
            week_steps_so_far = int(rows[0]["week_steps"] or 0)
            weekly_target     = 70000
            day_of_week       = today_dt.weekday()
            days_remaining    = 7 - day_of_week  # includes today (Mon=0 -> 7 days, Sun=6 -> 1 day)
            steps_remaining   = max(0, weekly_target - week_steps_so_far)
            daily_needed      = round(steps_remaining / max(1, days_remaining))
            metrics["week_steps_so_far"]    = week_steps_so_far
            metrics["week_steps_target"]    = weekly_target
            metrics["week_steps_remaining"] = steps_remaining
            metrics["week_days_remaining"]  = days_remaining
            metrics["week_daily_needed"]    = daily_needed
    except Exception as e:
        log.warning(f"Week step progress query failed: {e}")

    # ── ENRICHMENT 3: 7-day rolling averages (weight, nutrition, RHR) ─────────
    try:
        seven_days_ago = (datetime.date.today() - datetime.timedelta(days=8)).isoformat()
        rows = query_influx("GarminStats",
            f"SELECT mean(weight) AS avg_weight FROM BodyComposition "
            f"WHERE time >= '{seven_days_ago}T07:00:00Z' AND time < '{today}T07:00:00Z'"
        )
        if rows and rows[0].get("avg_weight"):
            w = rows[0]["avg_weight"]
            metrics["weight_7d_avg"] = round((w / 1000) if w > 1000 else w, 2)

        rows = query_influx("GarminStats",
            f"SELECT mean(restingHeartRate) AS avg_rhr FROM DailyStats "
            f"WHERE time >= '{seven_days_ago}T07:00:00Z' AND time < '{today}T07:00:00Z' "
            f"AND restingHeartRate > 0"
        )
        if rows and rows[0].get("avg_rhr"):
            metrics["rhr_7d_avg"] = round(rows[0]["avg_rhr"], 1)
            today_rhr = metrics.get("resting_hr", 0) or 0
            if today_rhr > 0:
                rhr_delta = round(today_rhr - metrics["rhr_7d_avg"], 1)
                metrics["rhr_delta"] = rhr_delta
                if rhr_delta >= 5:
                    metrics["rhr_status"] = f"+{rhr_delta} bpm above 7-day avg — fatigue signal"
                elif rhr_delta <= -3:
                    metrics["rhr_status"] = f"{rhr_delta} bpm below 7-day avg — good recovery"
                else:
                    metrics["rhr_status"] = f"{rhr_delta:+.1f} bpm vs 7-day avg — normal"

        rows = query_influx("CronometerStats",
            f"SELECT mean(Energy_kcal) AS avg_kcal, mean(Protein_g) AS avg_protein "
            f"FROM daily_nutrition "
            f"WHERE time >= '{seven_days_ago}T00:00:00Z' AND time < '{today}T00:00:00Z' "
            f"AND Energy_kcal > 0"
        )
        if rows:
            metrics["kcal_7d_avg"]    = round(rows[0].get("avg_kcal") or 0, 0)
            metrics["protein_7d_avg"] = round(rows[0].get("avg_protein") or 0, 1)
    except Exception as e:
        log.warning(f"7-day rolling avg query failed: {e}")

    # ── ENRICHMENT 4: Strength progression vs same session last week ──────────
    try:
        eight_days_ago = (datetime.date.today() - datetime.timedelta(days=8)).isoformat()
        five_days_ago  = (datetime.date.today() - datetime.timedelta(days=5)).isoformat()
        # NOTE: exercise is a TAG in StrengthSets, not a field.
        # InfluxDB 1.x rejects mixing aggregate functions (max, sum) with
        # non-aggregate fields in the same SELECT. Tags returned via GROUP BY
        # appear automatically in r["tags"] — do not list them in SELECT.
        rows = query_influx("GarminStats",
            f"SELECT max(weight_kg) AS max_weight, sum(volume_kg) AS volume "
            f"FROM StrengthSets "
            f"WHERE time >= '{eight_days_ago}T07:00:00Z' "
            f"AND time < '{five_days_ago}T07:00:00Z' "
            f"GROUP BY exercise"
        )
        if rows:
            prior_strength = {}
            for r in rows:
                tags = r.get("tags", {})
                ex = tags.get("exercise") or r.get("exercise", "")
                if ex:
                    prior_strength[ex] = {
                        "max_weight": round(float(r.get("max_weight") or 0), 1),
                        "volume":     round(float(r.get("volume") or 0), 1),
                    }
            metrics["prior_strength"] = prior_strength
    except Exception as e:
        log.warning(f"Strength progression query failed: {e}")

    return metrics


# ── Ollama API ────────────────────────────────────────────────────────────────

def call_lm_studio(prompt, system_prompt=None):
    """Call Ollama native /api/chat endpoint.
    Uses think=false to disable Qwen3.6 thinking mode.
    System prompt optional — baked into fitness-coach model via Modelfile.
    """
    url = f"{LM_STUDIO_URL}/api/chat"
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})

    payload = {
        "model": LM_MODEL,
        "messages": messages,
        "think": False,
        "stream": False,
        "options": {
            "temperature": 0.7,
            "num_predict": 3000,
        },
    }
    try:
        log.debug(f"Calling Ollama at {url} with model {LM_MODEL}")
        resp = requests.post(url, json=payload, timeout=600)
        resp.raise_for_status()
        data = resp.json()
        # Native Ollama /api/chat response format
        content = data.get("message", {}).get("content", "")
        if not content:
            log.error(f"Empty response from Ollama: {data}")
            return None
        return content
    except requests.exceptions.Timeout:
        log.error("Ollama API timed out after 600 seconds")
        return None
    except requests.exceptions.ConnectionError as e:
        log.error(f"Ollama connection error — is Ollama running on Max? {e}")
        return None
    except Exception as e:
        log.error(f"Ollama API call failed: {e}")
        return None


# ── Coach note persistence ────────────────────────────────────────────────────

def extract_coach_note(ai_response, metrics, date_str):
    """
    Ask Ollama to distil today's key coaching decision into a single structured sentence.
    Returns a string like: "Reduced walk target to 45 min: HRV suppressed and sleep score 44."
    Falls back to a metrics-derived note if Ollama is unavailable.
    """
    note_prompt = (
        f"DATE: {date_str}\n"
        f"You just produced this coaching brief for Simon:\n\n"
        f"{ai_response}\n\n"
        f"In ONE sentence (max 25 words), summarise the KEY coaching decision or modification "
        f"you made today. Focus on what changed from the standard plan and WHY.\n"
        f"Format: '[Action]: [reason]'\n"
        f"Example: 'Reduced walk to 45 min: HRV suppressed 12% below baseline and sleep score 44.'\n"
        f"If no modifications were needed, write: 'Standard plan recommended: all recovery metrics "
        f"within normal range.'\n"
        f"Reply with ONLY the sentence — no preamble, no punctuation beyond the colon."
    )
    note = call_lm_studio(note_prompt)
    if note:
        # Strip any accidental extra lines
        note = note.strip().splitlines()[0].strip()
        log.info(f"Coach note extracted: {note}")
        return note

    # Fallback: build a basic note from raw metrics if Ollama unreachable
    hrv   = metrics.get("overnight_hrv", 0)
    sleep = metrics.get("sleep_score", 0)
    steps = metrics.get("steps", 0)
    fallback = f"AI unavailable — raw data: HRV {hrv}ms, sleep score {sleep}/100, steps {steps:,}."
    log.warning(f"Coach note fallback used: {fallback}")
    return fallback


def store_coach_note(note_text, date_str, overrides=None):
    """
    Write today's coaching decision to CoachNotes measurement in GarminStats.
    Deletes any existing entries for today before writing — re-running the daily
    brief never leaves conflicting notes from the same day.
    overrides: optional dict of structured fields for dashboard consumption.
    """
    try:
        client = InfluxDBClient(
            host=INFLUX_HOST, port=INFLUX_PORT,
            username=INFLUX_USER, password=INFLUX_PASS,
            database="GarminStats",
        )

        # Delete existing entries for today — InfluxDB 1.x DELETE requires a
        # nanosecond integer timestamp, not an RFC3339 string.
        today_start = f"{datetime.date.today().isoformat()}T00:00:00Z"
        tomorrow    = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()
        today_end   = f"{tomorrow}T00:00:00Z"
        existing = client.query(
            f"SELECT time, note FROM CoachNotes "
            f"WHERE time >= '{today_start}' AND time < '{today_end}'",
            database="GarminStats",
        )
        deleted = 0
        for point in existing.get_points():
            ts_str = point["time"]
            dt = datetime.datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            ts_ns = int(dt.timestamp() * 1e9)
            client.query(
                f"DELETE FROM CoachNotes WHERE time = {ts_ns}",
                database="GarminStats",
            )
            deleted += 1
        if deleted:
            log.info(f"Deleted {deleted} existing CoachNotes entry/entries for {date_str}")

        fields = {
            "note": note_text,
            "date": date_str,
        }
        if overrides:
            fields["readiness_class"]   = overrides.get("readiness_class", "")
            fields["caliber_cancelled"] = int(overrides.get("caliber_cancelled", 0))
            fields["caliber_reduced"]   = int(overrides.get("caliber_reduced", 0))
            fields["vo2_cancelled"]     = int(overrides.get("vo2_cancelled", 0))
            fields["walk_cap_mins"]     = int(overrides.get("walk_cap_mins", -1))
            fields["step_cap"]          = int(overrides.get("step_cap", -1))

        point = [{
            "measurement": "CoachNotes",
            "tags":   {"source": "daily_brief"},
            "fields": fields,
        }]
        ok = client.write_points(point)
        client.close()
        if ok:
            log.info(f"Coach note stored in InfluxDB for {date_str}")
        else:
            log.warning("Coach note write returned False — check InfluxDB connection")
    except Exception as e:
        log.error(f"Failed to store coach note: {e}")


def get_weekly_coach_notes(week_start, week_end_query):
    """
    Retrieve all CoachNotes written during the week.
    Returns a list of dicts with 'date' and 'note' keys, ordered by time.
    week_start / week_end_query are datetime.date objects.
    """
    ws = week_start.isoformat()
    we = week_end_query.isoformat()
    try:
        rows = query_influx("GarminStats",
            f"SELECT date, note FROM CoachNotes "
            f"WHERE time >= '{ws}T07:00:00Z' AND time < '{we}T07:00:00Z' "
            f"ORDER BY time ASC"
        )
        return [{"date": r.get("date", ""), "note": r.get("note", "")} for r in rows if r.get("note")]
    except Exception as e:
        log.warning(f"Could not retrieve coach notes: {e}")
        return []


def build_coach_notes_block(notes):
    """Format the weekly coach notes into a plain-text block for the weekly prompt."""
    if not notes:
        return ""
    lines = ["DAILY COACHING DECISIONS THIS WEEK:"]
    lines.append("(Use this context when interpreting compliance — decisions marked below were")
    lines.append("made by the AI coach based on real-time recovery data, not athlete choice.)")
    for n in notes:
        date_label = n.get("date", "").strip()
        note_text  = n.get("note", "").strip()
        if date_label and note_text:
            lines.append(f"  {date_label}: {note_text}")
    return "\n".join(lines)


def build_coach_notes_html(notes):
    """Format coach notes as an HTML section for the weekly email."""
    if not notes:
        return ""
    rows = ""
    for n in notes:
        date_label = n.get("date", "").strip()
        note_text  = n.get("note", "").strip()
        if date_label and note_text:
            rows += (
                f'<tr>'
                f'<td style="padding:5px 10px;color:#888;font-size:12px;white-space:nowrap;'
                f'vertical-align:top;width:130px;">{date_label}</td>'
                f'<td style="padding:5px 10px;font-size:13px;">{note_text}</td>'
                f'</tr>'
            )
    if not rows:
        return ""
    return (
        f'<div style="margin-bottom:20px;">'
        f'<h3 style="margin:0 0 8px;color:#1a5276;font-size:13px;text-transform:uppercase;'
        f'letter-spacing:1px;border-bottom:2px solid #1a5276;padding-bottom:4px;">'
        f'Daily Coaching Decisions</h3>'
        f'<p style="font-size:12px;color:#888;margin:0 0 8px;">These adjustments were made by '
        f'your AI coach based on real-time recovery data — not missed sessions.</p>'
        f'<table width="100%" cellpadding="0" cellspacing="0" border="0">{rows}</table>'
        f'</div>'
    )


def classify_readiness(hrv_delta_pct=None, sleep_score=None, body_battery_low=None):
    """
    Classify readiness into one of four categories using HRV-vs-baseline,
    sleep score, and overnight Body Battery low.

    Categories:
      - Fully recovered
      - Partially recovered
      - Suppressed
      - Red-flag (do not train)

    Any input may be None if data is missing — classification degrades
    gracefully using whichever signals are available.

    Priority rules:
      - HRV is the PRIMARY readiness signal. If HRV is >= baseline (delta >= 0),
        the classification CANNOT be Suppressed or Red-flag, regardless of
        Body Battery or sleep score. Secondary signals (sleep, body battery)
        can only downgrade from Fully recovered to Partially recovered.
      - body_battery_low is Garmin's bodyBatteryLowestValue — the daily floor,
        typically hit at the end of a hard training day. A low floor is normal
        after training; threshold is intentionally low (< 10) to avoid false
        suppression after normal training days.
    """
    hrv_delta_pct    = hrv_delta_pct if hrv_delta_pct is not None else 0
    sleep_score      = sleep_score if sleep_score is not None else 100
    body_battery_low = body_battery_low if body_battery_low is not None else 100

    # HRV ABOVE BASELINE — nervous system is recovered.
    # Secondary signals can reduce to Partially recovered but cannot
    # push to Suppressed. Physiologically correct: HRV >= baseline means
    # the ANS has adapted regardless of local/peripheral fatigue signals.
    if hrv_delta_pct >= 0:
        if sleep_score < 70 or body_battery_low < 20:
            return "Partially recovered"
        return "Fully recovered"

    # HRV BELOW BASELINE — secondary signals now co-determine severity.

    # Red-flag: critically low HRV AND poor sleep
    if hrv_delta_pct <= -10 and sleep_score < 50:
        return "Red-flag (do not train)"

    # Suppressed: HRV significantly down, or very poor sleep alone
    # body_battery_low threshold deliberately low (< 10) — daily floor
    # is expected to be low after normal training sessions
    if hrv_delta_pct <= -10 or sleep_score < 50 or body_battery_low < 10:
        return "Suppressed"

    # Partially recovered: mild HRV suppression or below-par sleep/battery
    if hrv_delta_pct <= -5 or sleep_score < 70 or body_battery_low < 30:
        return "Partially recovered"

    return "Fully recovered"


# Cadence and session-duration constants — single source of truth for both
# build_prompt() (daily brief step ceiling) and check_step_nudge() (step nudge
# email). Previously duplicated/out of sync between the two functions.
STEPS_PER_MIN_TREADMILL = 115   # treadmill Zone 2: avg 115 spm across 9 sessions (range 109-122)
STEPS_PER_MIN_OUTDOOR   = 110   # outdoor Zone 2 long walk cadence (108-118 spm observed range)
STEPS_PER_MIN_VO2       = 130   # outdoor VO2 Max walk: 130 spm measured on 20.5-min session
INCIDENTAL_NORMAL       = 4500  # whole-day non-session steps (morning movement + daily living + post-session)
INCIDENTAL_SATURDAY     = 4500  # whole-day non-session steps on long-walk days

SAT_PLAN_MINS = {
    1:  90, 2: 100, 3: 110, 4:  80, 5: 120, 6: 135, 7: 150,
    8: 110, 9: 180, 10: 240, 11: 150, 12: 120, 13:   0,
}

Z2_PLAN_MINS = {
    1:  50, 2:  55, 3:  60, 4:  40, 5:  60, 6:  70, 7:  75,
    8:  55, 9:  90, 10: 70, 11: 75, 12: 30, 13:  0,
}


# Detraining streak detector — counts consecutive low-activity days so Saturday's
# long-walk target can be auto-scaled down even when today's same-day readiness
# reads as fully recovered. classify_readiness() above is a single-day snapshot;
# this adds memory of recent history.
def get_low_activity_streak(threshold=5000, lookback_days=10, max_missing_skip=2):
    """
    Count consecutive days (most recent backward) with totalSteps below `threshold`.

    Missing/None readings (late Garmin sync, sync failure) are SKIPPED rather than
    treated as "not low activity" — a late-syncing day should not silently break a
    real detraining streak and prevent the flag from firing. Up to `max_missing_skip`
    missing days are tolerated within the streak before we stop counting, to avoid
    an extended outage falsely inflating the streak indefinitely.

    Note: this only sees days that have a row in DailyStats at all. A day with zero
    Garmin sync (no row written, not even a null) is indistinguishable here from a
    day outside the lookback window — known limitation, not fixed by this patch.

    On query failure (InfluxDB unreachable etc.), degrades to (0, []) rather than
    raising — a detraining check must never be able to crash daily brief generation.

    Returns (streak_count, daily_steps_list) where daily_steps_list is most-recent-first
    and may contain None entries for skipped/missing days.
    """
    try:
        today = datetime.date.today()
        start = today - datetime.timedelta(days=lookback_days)
        rows = query_influx("GarminStats",
            f"SELECT totalSteps FROM DailyStats "
            f"WHERE time >= '{start.isoformat()}T07:00:00Z' "
            f"AND time < '{today.isoformat()}T07:00:00Z' "
            f"ORDER BY time DESC"
        )
        streak = 0
        missing_skipped = 0
        daily = []
        for row in rows:
            steps = row.get("totalSteps")
            daily.append(steps)
            if steps is None:
                if missing_skipped < max_missing_skip:
                    missing_skipped += 1
                    continue  # don't count, don't break — give the streak the benefit of the doubt
                else:
                    break  # too many missing days in a row — stop trusting the streak
            if steps < threshold:
                streak += 1
            else:
                break
        return streak, daily
    except Exception as e:
        log.warning(f"Detraining streak query failed: {e}")
        return 0, []


def build_prompt(metrics, date_str, yesterday_note=None, weather=None):
    """Build the data context prompt for the AI."""
    m = metrics
    today = datetime.date.today()
    weeks_to_event = (datetime.date(2026, 8, 29) - today).days // 7

    phase      = get_phase(TRAINING_WEEK)
    next_phase = get_phase(TRAINING_WEEK + 1)
    phase_transition = (
        f" — Week {TRAINING_WEEK + 1} moves into {next_phase}"
        if next_phase != phase else ""
    )

    lines = [
        f"DATE: {date_str}",
        f"TRAINING WEEK: {TRAINING_WEEK} of 13 ({weeks_to_event} weeks until {EVENT_NAME} on {EVENT_DATE})",
        f"TRAINING PHASE: {phase}{phase_transition}",
        "",
        "YESTERDAY'S DATA:",
        f"- Steps: {m.get('steps', 'N/A'):,}",
        f"- Distance: {m.get('distance_km', 'N/A')} km",
        f"- Active calories: {m.get('active_calories', 'N/A')} kcal",
        f"- Resting HR: {m.get('resting_hr', 'N/A')} bpm",
        f"- Moderate intensity: {m.get('moderate_intensity', 0)} mins",
        f"- Vigorous intensity: {m.get('vigorous_intensity', 0)} mins",
        "",
        "SLEEP:",
        f"- Duration: {m.get('sleep_hours', 'N/A')} hours",
        f"- Deep sleep: {m.get('deep_sleep_mins', 0)} mins",
        f"- REM sleep: {m.get('rem_sleep_mins', 0)} mins",
        f"- Sleep score: {m.get('sleep_score', 'N/A')}/100",
        f"- Overnight HRV: {m.get('overnight_hrv', 'N/A')} ms",
        f"- Body battery: {m.get('body_battery_low', '?')} (low) → {m.get('body_battery_high', '?')} (high), overnight change: +{m.get('battery_change', 0)} (this is the recovery overnight — if positive the body is recovering well)",
        f"- Body battery range yesterday: {m.get('body_battery_low', 'N/A')} - {m.get('body_battery_high', 'N/A')}",
        f"- High stress duration: {m.get('high_stress_mins', 0)} mins",
    ]

    if m.get("weight_kg"):
        lines.append(f"- Weight: {m['weight_kg']} kg"
                     + (f" (7-day avg: {m['weight_7d_avg']} kg)" if m.get("weight_7d_avg") else ""))

    # Enrichment 1: HRV with baseline context
    if m.get("hrv_baseline"):
        delta = m.get("hrv_delta_pct", 0)
        sign  = "+" if delta >= 0 else ""
        lines.append(f"- HRV baseline (30-day avg): {m['hrv_baseline']} ms | "
                     f"Today: {m.get('overnight_hrv')} ms ({sign}{delta}%) — {m.get('hrv_status', '')}")

    # Pre-computed readiness classification — injected as a hard constraint.
    # Ollama must not re-derive, contradict, soften, or upgrade this value.
    # Consequences are injected later (after day-type flags are computed) so they
    # can be day-type-aware — see "READINESS CONSEQUENCES" block below.
    readiness_class = classify_readiness(
        hrv_delta_pct=m.get("hrv_delta_pct"),
        sleep_score=m.get("sleep_score"),
        body_battery_low=m.get("body_battery_low"),
    )
    lines.append(f"- READINESS CLASSIFICATION (Python pre-computed — DO NOT change, re-derive, or contradict): {readiness_class}")

    # Enrichment 2: Week-to-date step progress
    if m.get("week_steps_so_far") is not None:
        lines.append(f"- Steps this week so far: {m['week_steps_so_far']:,} of "
                     f"{m['week_steps_target']:,} target "
                     f"({m['week_steps_remaining']:,} remaining across {m['week_days_remaining']} days, "
                     f"need ~{m['week_daily_needed']:,}/day to hit target)")

    # Enrichment 3: RHR trend
    if m.get("rhr_status"):
        lines.append(f"- Resting HR trend: {m['resting_hr']} bpm today | "
                     f"7-day avg: {m.get('rhr_7d_avg')} bpm | {m['rhr_status']}")

    if m.get("activities"):
        lines.append("")
        lines.append("ACTIVITIES:")
        for a in m["activities"]:
            steps_str = f", {a['steps']:,} steps" if a.get("steps") else ""
            lines.append(
                f"- {a['name']} ({a['type']}): {a['duration_mins']} mins, "
                f"avg HR {a['avg_hr']} bpm, {a['calories']} kcal"
                f"{steps_str}, training load {a['training_load']}"
            )

    if m.get("caliber_sessions"):
        lines.append("")
        lines.append("STRENGTH TRAINING (from Caliber):")
        for s in m["caliber_sessions"]:
            lines.append(f"- {s['title']}: {s['duration_mins']} mins, "
                        f"{s['exercises']} exercises, {s['total_sets']} sets, "
                        f"{s['total_volume']} kg total volume")
    elif m.get("strength"):
        lines.append("")
        lines.append("STRENGTH TRAINING (from Garmin):")
        for ex, data in m["strength"].items():
            lines.append(
                f"- {ex}: {data['sets']} sets, {data['total_reps']} total reps, "
                f"max {data['max_weight']} kg, volume {round(data['volume'], 1)} kg"
            )

    if m.get("caliber_sets"):
        lines.append("")
        lines.append("EXERCISE DETAIL (Caliber):")
        prior = m.get("prior_strength", {})
        for ex, data in m["caliber_sets"].items():
            base = f"- {ex}: {data['sets']} sets, {data['total_reps']} total reps, max {data['max_weight']} kg, volume {round(data['volume'], 1)} kg"
            if ex in prior and prior[ex]["max_weight"] > 0:
                prev_max = prior[ex]["max_weight"]
                diff = round(data["max_weight"] - prev_max, 1)
                sign = "+" if diff >= 0 else ""
                base += f" | vs last week: {sign}{diff} kg max ({prev_max} kg)"
            lines.append(base)

    if m.get("calories_in"):
        lines.append("")
        lines.append("NUTRITION (Cronometer):")
        kcal_avg_str = f" (7-day avg: {int(m['kcal_7d_avg'])} kcal)" if m.get("kcal_7d_avg") else ""
        lines.append(f"- Energy: {m['calories_in']} kcal{kcal_avg_str}")
        prot_avg_str = f" (7-day avg: {m['protein_7d_avg']}g)" if m.get("protein_7d_avg") else ""
        lines.append(f"- Protein: {m['protein_g']}g{prot_avg_str} | Fat: {m['fat_g']}g | Carbs: {m['carbs_g']}g")
        if m.get("protein_7d_avg") and float(m["protein_7d_avg"]) < 140:
            lines.append(f"  *** PROTEIN TREND ALERT: 7-day average protein {m['protein_7d_avg']}g is below the 140g floor — address this urgently ***")

    # Determine today's session type early so weather guidance can be gated for rest days
    session_type, session_label, session_details = get_todays_session()
    is_rest_day = session_type == "rest"

    # Weather forecast — injected before session planning so Ollama can recommend
    # indoor vs outdoor and adjust intensity for heat/cold/wind
    if weather:
        w = weather
        lines.append("")
        lines.append("TODAY'S WEATHER FORECAST (Watton, Norfolk):")
        lines.append(f"- Conditions: {w.get('description')} | High: {w.get('temp_max')}C | Low: {w.get('temp_min')}C")
        lines.append(f"- Rain: {w.get('rain_mm')}mm expected, {w.get('rain_prob')}% probability | Wind: {w.get('wind_max')} km/h max")
        lines.append(f"- Morning conditions (8-12am): ~{w.get('morning_temp')}C, {w.get('morning_rain_pct')}% rain chance")
        lines.append(f"- Outdoor walk suitability: {w.get('outdoor_suitability')}")
        if is_rest_day:
            lines.append("This is for context only — today is a Rest Day, so do not recommend or plan")
            lines.append("a walk or training session based on this weather data.")
        else:
            lines.append("Use this to recommend indoor (treadmill) vs outdoor walk, and flag any heat or")
            lines.append("cold management considerations for Zone 2 HR control.")

    # Athlete notes — manually edited plain text file for health events, injury,
    # medication, or life context that Garmin data cannot capture.
    # Edit /share/Container/daily-brief/athlete_notes.txt on the NAS.
    # Clear the file when the condition resolves.
    athlete_notes = get_athlete_notes()
    if athlete_notes:
        lines.append("")
        lines.append("ATHLETE HEALTH NOTES (manually entered — take priority over data-derived assumptions):")
        for line in athlete_notes.splitlines():
            if line.strip():
                lines.append(f"  {line.strip()}")
        lines.append("These notes explain context that Garmin cannot capture. Factor them into your")
        lines.append("readiness interpretation, HR expectations, and session recommendations.")

    # Inject yesterday's coach decision so Ollama knows what was actually prescribed
    if yesterday_note:
        yesterday = datetime.date.today() - datetime.timedelta(days=1)
        _yday_map = {
            0: "Caliber Legs & Abs + short walk",
            1: "Zone 2 + VO2 Max walk (Tue/Thu)",
            2: "Caliber Back & Shoulders + short walk",
            3: "Zone 2 + VO2 Max walk (Tue/Thu)",
            4: "Caliber Chest & Arms + short walk",
            5: "Saturday long walk",
            6: "Rest day",
        }
        yesterday_session = _yday_map.get(yesterday.weekday(), "Unknown")
        lines.append("")
        lines.append("YESTERDAY'S COACHING DECISION:")
        lines.append(f"  Day type: {yesterday_session}")
        lines.append(f"  Note: {yesterday_note}")
        lines.append("Use this as factual context about yesterday only. Do not use it to infer")
        lines.append("today's session type — today's scheduled session is stated separately below.")

    # Add today's session context (session_label/session_details/is_rest_day computed earlier)
    # Note: is_caliber_day/is_saturday/is_vo2_day are computed fully in the step ceiling
    # block below — use inline check here to avoid forward-reference error.
    _today_is_caliber = (session_type == "caliber")
    if session_label:
        lines.append("")
        lines.append(f"TODAY'S SCHEDULED SESSION: {session_label}")
        if _today_is_caliber:
            lines.append("IMPORTANT: Today is a Caliber strength day + short walk ONLY.")
            lines.append("VO2 Max sessions are NEVER scheduled on Caliber days (Mon/Wed/Fri).")
            lines.append("DO NOT mention VO2 Max in any section of today's brief.")
            lines.append("VO2 Max sessions occur ONLY on Tuesdays and Thursdays.")
        if session_details:
            lines.append(session_details)
        lines.append("")
        if is_rest_day:
            lines.append("This is a scheduled Rest Day. Do not prescribe a structured walk, training")
            lines.append("session, or step target. Recommend only optional light movement (e.g. gentle")
            lines.append("stretching, short stroll) based on body battery, and prioritise recovery.")
        else:
            lines.append("Adapt based on yesterday's recovery data:")
            lines.append("- Great recovery (HRV at/above baseline, sleep 70+, body battery 60+): train as planned or push upper range")
            lines.append("- Normal recovery: train as planned")
            lines.append("- Reduced recovery (HRV suppressed 5-10%, sleep 50-69, body battery 40-59): reduce walk duration 10-15%, Caliber 2 sets if strength day")
            lines.append("- Poor recovery (HRV >10% below baseline, sleep <50, body battery <40): walk only 30 min easy, no Caliber")
            lines.append("Give specific adapted targets: duration in minutes, incline %, speed km/h if treadmill.")

    # Tomorrow's scheduled session type — for the conditional outlook in
    # COACH'S INTERPRETATION (Ollama must not invent this, it's looked up here)
    tomorrow_session = get_tomorrows_session_type()
    lines.append("")
    lines.append(f"TOMORROW'S SCHEDULED SESSION TYPE: {tomorrow_session}")
    lines.append("Use this as the basis for the conditional outlook in COACH'S INTERPRETATION —")
    lines.append("do not invent or guess what tomorrow's session is.")

    # Pre-compute step cap from readiness classification — prevents Ollama from
    # inventing inconsistent numbers. The cap must always be ABOVE the minimum
    # steps the prescribed session will generate, otherwise the brief contradicts
    # itself (e.g. "walk 45 mins" + "cap at 6,000" when the walk alone generates
    # ~5,000 steps leaving no room for incidental movement).
    #
    # Estimate session floor from day type and readiness:
    #   Outdoor long walk (Saturday): ~110 steps/min at Zone 2 pace (4.6-4.8 km/h)
    #   Treadmill Zone 2: ~140 steps/min (measured cadence 130-150 steps/min)
    #   Incidental steps on Saturday long-walk days: ~3,500
    #     (normal household + pre/post-walk movement before and after session)
    #   Incidental steps on gym/short-walk days: ~2,500
    #   Step ceiling purpose: prevent extra training load, not restrict normal life.
    #   Saturday ceiling must accommodate the full long walk + normal daily movement.

    # Cadence constants (STEPS_PER_MIN_TREADMILL, STEPS_PER_MIN_OUTDOOR,
    # STEPS_PER_MIN_VO2, INCIDENTAL_NORMAL, INCIDENTAL_SATURDAY) and the
    # SAT_PLAN_MINS / Z2_PLAN_MINS week tables are now defined ONCE at module
    # scope (above classify_readiness/build_prompt) so check_step_nudge() uses
    # the exact same values — previously these were duplicated locally here
    # and check_step_nudge() had its own separate, drifted copy.

    # Caliber day walk duration by week.
    # 40 min across standard-structure weeks (Wks 1-3, 5-8).
    # Explicit reductions: Wk4 recovery (30), Wk9 heavier block (45),
    # Wk10 dress-rehearsal protection (35), Wk11 post-rehearsal (30),
    # Wk12 taper (25), Wk13 event week (0).
    CALIBER_WALK_MINS = {
        1:  40,   2:  40,   3:  40,   4:  30,
        5:  40,   6:  40,   7:  40,   8:  40,
        9:  45,   10: 35,   11: 30,   12: 25,   13:  0,
    }

    # Suppressed Zone 2 — always 30 min regardless of week (minimum effective dose)
    sat_plan      = SAT_PLAN_MINS.get(TRAINING_WEEK, 110)
    z2_full       = Z2_PLAN_MINS.get(TRAINING_WEEK, 60)
    caliber_walk  = CALIBER_WALK_MINS.get(TRAINING_WEEK, 40)

    VO2_MINS   = 20   # outdoor GPS VO2 Max walk (fixed throughout plan)
    Z2_FULL    = z2_full
    Z2_SUPP    = 30   # treadmill Zone 2 suppressed (minimum — always 30 min)

    DETRAIN_STREAK_THRESHOLD = 4
    DETRAIN_SCALE_FACTOR     = 0.70   # matches existing "Suppressed" Saturday reduction

    if is_rest_day:
        # Detraining flag is never consulted on a rest day (no session, no Saturday/
        # Caliber/VO2 branch reads it) — skip the query rather than running it for nothing.
        detrain_streak, _detrain_daily = 0, []
    else:
        detrain_streak, _detrain_daily = get_low_activity_streak(threshold=5000, lookback_days=10)
    detraining_flag = detrain_streak >= DETRAIN_STREAK_THRESHOLD
    log.info(f"Detraining check: {detrain_streak} consecutive low-activity days, flag={detraining_flag}")

    is_caliber_day = (session_type == "caliber")
    is_saturday    = (session_type == "long_walk" and datetime.date.today().weekday() == 5)
    # Tue/Thu are "long_walk" type but NOT Saturday — they are the dual VO2 Max + Zone 2 sessions
    is_vo2_day     = (session_type == "long_walk" and not is_saturday)

    # READINESS CONSEQUENCES — day-type-aware, injected here after day-type flags are available.
    # Prevents the model hallucinating sessions not scheduled today (e.g. VO2 Max on a Monday).
    if is_rest_day:
        _suppressed_consequence = "Rest day — no session, light movement only."
    elif is_saturday:
        _suppressed_consequence = (
            "Reduce Saturday long walk to 70% of plan duration. "
            "Step ceiling is pre-computed below. No intensity targets — Zone 2 only."
        )
    elif is_vo2_day:
        _suppressed_consequence = (
            "Cancel the VO2 Max session entirely. Treadmill Zone 2 walk only, "
            "shortened to 30 minutes. Drop step ceiling accordingly."
        )
    elif is_caliber_day:
        _suppressed_consequence = (
            "Drop Caliber to 2 working sets per exercise. Short walk 30 minutes Zone 2 only "
            "(treadmill preferred for HR control, but a flat outdoor route is acceptable if "
            "terrain is flat and HR can be kept strictly 98-115 bpm). "
            "DO NOT mention VO2 Max — it is not scheduled today. "
            "VO2 Max sessions only occur on Tuesdays and Thursdays."
        )
    else:
        _suppressed_consequence = "Shorten Zone 2 by 20-30%. No additional intensity work."

    # Fully-recovered consequence is day-type aware so a same-day "good" HRV/sleep
    # reading doesn't trigger full-intensity re-entry straight after a multi-day
    # detraining gap (e.g. jumping back into full 3x8-10 Caliber sets after 4+
    # consecutive rest days). detraining_flag is computed above from
    # get_low_activity_streak().
    if detraining_flag and is_caliber_day:
        _fully_recovered_consequence = (
            f"Today's readiness reads Fully recovered, but {detrain_streak} consecutive "
            f"low-activity days were detected — treat this as a RE-ENTRY session, not a "
            f"normal training day. Cap Caliber at 2 working sets per exercise (not 3), "
            f"hold or reduce weight from the last completed session rather than progressing, "
            f"and prioritise form over load. Full walk duration is fine. "
            f"This is a coach-directed re-entry cap, not a sign of poor readiness — "
            f"do not describe it as suppressed or partially recovered."
        )
        store_coach_note(
            note_text=(f"Caliber session capped at 2 working sets (re-entry adjustment) "
                       f"despite Fully recovered same-day readiness — {detrain_streak} "
                       f"consecutive low-activity days detected. Coach-directed, not "
                       f"non-compliance or reduced readiness."),
            date_str=date_str
            # No matching dashboard field exists for "sets capped" — note text only.
            # caliber_cancelled would be factually wrong here (session isn't cancelled).
        )
    elif detraining_flag and is_vo2_day:
        _fully_recovered_consequence = (
            f"Today's readiness reads Fully recovered, but {detrain_streak} consecutive "
            f"low-activity days were detected — treat this as a RE-ENTRY session. Complete "
            f"the VO2 Max walk at the LOWER end of the 118-130 bpm main-block range rather "
            f"than pushing pace, and keep the Zone 2 treadmill walk at standard duration "
            f"without adding intensity. This is a coach-directed re-entry pacing note, "
            f"not a sign of poor readiness."
        )
        store_coach_note(
            note_text=(f"VO2 Max session paced at lower end of range (re-entry adjustment) "
                       f"despite Fully recovered same-day readiness — {detrain_streak} "
                       f"consecutive low-activity days detected. Coach-directed, not "
                       f"non-compliance or reduced readiness."),
            date_str=date_str
            # No matching dashboard field exists for "paced lower" — note text only.
            # vo2_cancelled would be factually wrong here (session isn't cancelled).
        )
    else:
        _fully_recovered_consequence = "Train as planned. Push the upper end of all targets."

    _rc_consequences = {
        "Fully recovered":         _fully_recovered_consequence,
        "Partially recovered":     (
            "Train as planned at the LOWER end of ranges. Full sets. Full session. "
            "HR discipline is the only adjustment. Do NOT reduce sets, shorten session, "
            "or cap steps below the normal daily target. "
            + ("DO NOT mention VO2 Max — it is not scheduled today. VO2 Max sessions only occur on Tuesdays and Thursdays."
               if is_caliber_day else "")
        ),
        "Suppressed":              _suppressed_consequence,
        "Red-flag (do not train)": "Full rest or light recovery walk only. No strength work, no Zone 2 session, no step target.",
    }
    lines.append(f"- READINESS CONSEQUENCES (apply exactly — DO NOT apply rules from any other classification): {_rc_consequences.get(readiness_class, '')}")

    # Set inside the Saturday branches below when a detraining auto-adjustment
    # applies. Stored note is written AFTER step_cap is computed (further down)
    # so it can use the real walk_cap_mins/step_cap fields the dashboard already
    # reads, rather than inventing new override keys store_coach_note() ignores.
    _saturday_detrain_pending = None   # None | "partial" | "full"
    adj_mins_for_note         = None

    if readiness_class == "Red-flag (do not train)":
        session_floor = 0
        incidental    = INCIDENTAL_NORMAL
        desc          = "no session"
    elif readiness_class == "Suppressed":
        if is_saturday:
            supp_mins     = max(30, round(sat_plan * 0.70 / 5) * 5)
            session_floor = supp_mins * STEPS_PER_MIN_OUTDOOR
            incidental    = INCIDENTAL_SATURDAY
            desc          = f"{supp_mins}-min outdoor walk (70% of {sat_plan}-min plan)"
        elif is_vo2_day:
            session_floor = Z2_SUPP * STEPS_PER_MIN_TREADMILL
            incidental    = INCIDENTAL_NORMAL
            desc          = f"no VO2 Max + {Z2_SUPP}-min Zone 2 treadmill"
        elif is_caliber_day:
            session_floor = Z2_SUPP * STEPS_PER_MIN_TREADMILL
            incidental    = INCIDENTAL_NORMAL
            desc          = f"{Z2_SUPP}-min treadmill walk (Caliber, suppressed)"
        else:
            session_floor = Z2_SUPP * STEPS_PER_MIN_TREADMILL
            incidental    = INCIDENTAL_NORMAL
            desc          = f"{Z2_SUPP}-min treadmill walk (suppressed)"
    elif readiness_class == "Partially recovered":
        if is_saturday:
            base_factor   = min(0.85, DETRAIN_SCALE_FACTOR) if detraining_flag else 0.85
            partial_mins  = max(30, round(sat_plan * base_factor / 5) * 5)
            session_floor = partial_mins * STEPS_PER_MIN_OUTDOOR
            incidental    = INCIDENTAL_SATURDAY
            desc          = f"{partial_mins}-min outdoor walk ({int(base_factor*100)}% of {sat_plan}-min plan)"
            if detraining_flag:
                _saturday_detrain_pending = "partial"
                adj_mins_for_note = partial_mins
        elif is_vo2_day:
            # Full duration, lower intensity — same steps as fully recovered
            session_floor = (VO2_MINS * STEPS_PER_MIN_VO2
                             + Z2_FULL * STEPS_PER_MIN_TREADMILL)
            incidental    = INCIDENTAL_NORMAL
            desc          = f"{VO2_MINS}-min VO2 Max + {Z2_FULL}-min Zone 2 (lower intensity)"
        elif is_caliber_day:
            session_floor = caliber_walk * STEPS_PER_MIN_TREADMILL
            incidental    = INCIDENTAL_NORMAL
            desc          = f"{caliber_walk}-min treadmill walk (Caliber)"
        else:
            session_floor = Z2_FULL * STEPS_PER_MIN_TREADMILL
            incidental    = INCIDENTAL_NORMAL
            desc          = f"{Z2_FULL}-min treadmill walk"
    else:  # Fully recovered
        if is_saturday:
            if sat_plan == 0:
                session_floor = 0
                incidental    = INCIDENTAL_SATURDAY
                desc          = "event day — no ceiling"
            elif detraining_flag:
                adj_mins      = max(30, round(sat_plan * DETRAIN_SCALE_FACTOR / 5) * 5)
                session_floor = adj_mins * STEPS_PER_MIN_OUTDOOR
                incidental    = INCIDENTAL_SATURDAY
                desc          = (f"{adj_mins}-min outdoor walk (auto-reduced to "
                                  f"{int(DETRAIN_SCALE_FACTOR*100)}% of {sat_plan}-min plan — "
                                  f"{detrain_streak} consecutive days under 5,000 steps)")
                _saturday_detrain_pending = "full"
                adj_mins_for_note         = adj_mins
            else:
                session_floor = sat_plan * STEPS_PER_MIN_OUTDOOR
                incidental    = INCIDENTAL_SATURDAY
                desc          = f"{sat_plan}-min outdoor walk (full plan)"
        elif is_vo2_day:
            session_floor = (VO2_MINS * STEPS_PER_MIN_VO2
                             + Z2_FULL * STEPS_PER_MIN_TREADMILL)
            incidental    = INCIDENTAL_NORMAL
            desc          = f"{VO2_MINS}-min VO2 Max + {Z2_FULL}-min Zone 2"
        elif is_caliber_day:
            session_floor = caliber_walk * STEPS_PER_MIN_TREADMILL
            incidental    = INCIDENTAL_NORMAL
            desc          = f"{caliber_walk}-min treadmill walk (Caliber)"
        else:
            session_floor = None  # no cap — fully recovered, non-Saturday
            incidental    = INCIDENTAL_NORMAL
            desc          = "no cap"

    if session_floor is not None:
        raw_cap  = session_floor + incidental
        step_cap = int((raw_cap + 499) // 500 * 500)

        # Write the deferred Saturday detraining note now that step_cap is known —
        # uses the dashboard's EXISTING schema (walk_cap_mins, step_cap) rather than
        # invented override keys, so it renders the existing amber "Walk capped" /
        # "Step ceiling" badges store_coach_note()/dashboard.py already support.
        if _saturday_detrain_pending == "full":
            store_coach_note(
                note_text=(f"Saturday long walk auto-reduced to {adj_mins_for_note} min "
                           f"({int(DETRAIN_SCALE_FACTOR*100)}% of {sat_plan}-min plan) due to "
                           f"{detrain_streak} consecutive low-activity days (<5,000 steps). "
                           f"Coach-directed adjustment, not non-compliance."),
                date_str=date_str,
                overrides={"walk_cap_mins": adj_mins_for_note, "step_cap": step_cap}
            )
        elif _saturday_detrain_pending == "partial":
            store_coach_note(
                note_text=(f"Saturday long walk capped at {adj_mins_for_note} min "
                           f"(detraining adjustment on top of partial-recovery reduction) — "
                           f"{detrain_streak} consecutive low-activity days."),
                date_str=date_str,
                overrides={"walk_cap_mins": adj_mins_for_note, "step_cap": step_cap}
            )

        # Compute tomorrow's ceiling for context, so Ollama doesn't imply today's
        # ceiling carries forward to tomorrow's long walk day
        tomorrow = datetime.date.today() + datetime.timedelta(days=1)
        tomorrow_weekday = tomorrow.weekday()
        tomorrow_is_saturday = (tomorrow_weekday == 5)
        if tomorrow_is_saturday:
            tom_sat_plan = SAT_PLAN_MINS.get(TRAINING_WEEK, 120)
            # Apply the same detraining check used for today's Saturday logic —
            # otherwise this preview can show a full-plan figure on Friday that
            # gets silently reduced once Saturday's brief actually runs.
            if detraining_flag and tom_sat_plan > 0:
                tom_sat_plan_adj = max(30, round(tom_sat_plan * DETRAIN_SCALE_FACTOR / 5) * 5)
                tom_ceiling  = int((tom_sat_plan_adj * STEPS_PER_MIN_OUTDOOR + INCIDENTAL_SATURDAY + 499) // 500 * 500)
                tomorrow_note = (f"  Tomorrow (Saturday long walk): ceiling will be ~{tom_ceiling:,} steps "
                                 f"({tom_sat_plan_adj}-min walk, auto-reduced from {tom_sat_plan}-min plan "
                                 f"due to {detrain_streak} consecutive low-activity days — subject to "
                                 f"tomorrow's own readiness reading).")
            else:
                tom_ceiling  = int((tom_sat_plan * STEPS_PER_MIN_OUTDOOR + INCIDENTAL_SATURDAY + 499) // 500 * 500)
                tomorrow_note = (f"  Tomorrow (Saturday long walk): ceiling will be ~{tom_ceiling:,} steps "
                                 f"({tom_sat_plan}-min walk at {STEPS_PER_MIN_OUTDOOR} spm + "
                                 f"{INCIDENTAL_SATURDAY:,} incidental).")
        else:
            tomorrow_note = None

        lines.append("")
        lines.append(f"PRE-COMPUTED STEP CEILING: {step_cap:,} steps — TODAY ONLY (not a target,")
        lines.append("  a hard ceiling for today's specific session and readiness). Breakdown:")
        lines.append(f"  Session: {desc} (~{session_floor:,} steps)")
        lines.append(f"  Incidental: ~{incidental:,} steps normal daily movement")
        lines.append("  State this ceiling in TODAY'S PLAN with the phrase 'today only'.")
        lines.append("  Do not apply this number to tomorrow or any other day.")
        if tomorrow_note:
            lines.append(tomorrow_note)
        lines.append("  Do not invent a different number.")
    else:
        step_cap = None

    # Hydration guidance — electrolytes only warranted when BOTH conditions are met:
    # (a) session is genuinely long (≥60 min of walking) AND readiness allows full volume, OR
    # (b) heat stress during the session window (morning temp ≥22°C, not daily max)
    #
    # Using temp_max caused false positives on days where the morning session is cool
    # but the afternoon peaks above 22°C — not relevant for a morning walk.
    # Suppressed/red-flag days always get water-only regardless of temperature,
    # since the session is too short to generate meaningful sweat loss.
    hydration_cue = "Standard hydration — water only, no electrolytes required."
    if not is_rest_day:
        # Use morning temperature (8-12am) if available, fall back to temp_max
        morning_temp = (weather.get("morning_temp") if weather else None)
        session_temp = morning_temp if morning_temp is not None else (
            weather.get("temp_max", 0) if weather else 0
        )
        hot_session = session_temp >= 22

        # Long session = Zone 2/Saturday walk day on a non-suppressed day
        # Caliber days have short walks (~40 min) so never warrant electrolytes on duration alone
        # session_floor is None only for fully recovered non-Saturday days (no cap — walk as planned)
        long_session = (
            readiness_class in ("Fully recovered", "Partially recovered")
            and not is_caliber_day
            and (
                session_floor is None            # fully recovered, no cap — walk as planned
                or (session_floor is not None and session_floor >= 6000)  # ≥~55 min walk
            )
        )

        if hot_session or long_session:
            reason = "forecast heat during session" if hot_session else "long Zone 2 session"
            hydration_cue = (
                f"Electrolytes recommended — add electrolytes to water for today's session ({reason})."
            )

    lines.append("")
    # Label deliberately generic so Ollama doesn't parrot the label name into the
    # output — it should use the cue VALUE, not reference "the guidance cue".
    lines.append(f"HYDRATION INSTRUCTION: {hydration_cue}")

    lines.append("")
    lines.append("Please provide, using EXACTLY these seven section headings in this order")
    lines.append("(uppercase, on their own line):")
    lines.append("")
    lines.append("READINESS")
    lines.append("- First line: state the pre-computed READINESS CLASSIFICATION above verbatim")
    lines.append("  (Fully recovered / Partially recovered / Suppressed / Red-flag (do not train)).")
    lines.append("- Then 3-5 sentences interpreting HRV, sleep, Body Battery, stress minutes, and")
    lines.append("  resting HR — explain what these signals mean physiologically and whether this")
    lines.append("  looks like a one-off or part of a pattern. Address Simon by name,")
    lines.append("  e.g. \"Simon, your recovery...\"")
    lines.append("")
    lines.append("TODAY'S PLAN")
    lines.append("- Exact training prescription (strength, Zone 2 walk, steps) with concrete numbers")
    lines.append("  (duration, incline %, speed km/h if treadmill), consistent with the readiness")
    lines.append("  classification above.")
    lines.append("- When giving HR thresholds for adjusting pace during the session, use the exact")
    lines.append("  values defined in the system prompt: Zone 2 ceiling = 115 bpm (\"slow down if HR")
    lines.append("  rises above 115 bpm\"), alert/break threshold = 125 bpm. Do not substitute")
    lines.append("  other numbers for these thresholds.")
    lines.append("- When giving a step target, phrase it as a hard daily CEILING, not just a")
    lines.append("  target (e.g. \"cap total daily steps at 6,000 — this is a hard ceiling, not a")
    lines.append("  target\"), stated as the TOTAL STEP TARGET FOR THE DAY (not just the walking")
    lines.append("  session), so it's unambiguous against the week-to-date step tracking above.")
    lines.append("- On reduced-volume days, include one sentence on WHY the step cap matters")
    lines.append("  physiologically — e.g. exceeding it increases cumulative load on the lower")
    lines.append("  limbs and prolongs the suppressed-HRV state, undermining today's recovery.")
    lines.append("- Explain WHY each adjustment is being made and what the risk is if Simon trains")
    lines.append("  harder than prescribed today.")
    lines.append("")
    lines.append("KEY FOCUS")
    lines.append("- The single most important thing to protect today (e.g. aerobic base, CNS")
    lines.append("  recovery, HR discipline, carb baseline).")
    lines.append("- Explain why this is the priority today and what happens if it's ignored.")
    lines.append("")
    lines.append("NUTRITION")
    lines.append("- Calorie, protein, carb, and fat targets for TODAY, based on today's training")
    lines.append("  load and the daily nutrition targets defined in the system prompt (e.g. 149g")
    lines.append("  carbohydrates baseline). State these target figures explicitly and exactly as")
    lines.append("  defined — do not substitute yesterday's actual intake as today's target.")
    lines.append("- If yesterday's intake (shown in YESTERDAY'S DATA above) varied from the")
    lines.append("  baseline target, you may note that the variation is acceptable given")
    lines.append("  yesterday's training load — but state this as a separate observation about")
    lines.append("  yesterday, not as today's target.")
    lines.append("- Connect today's targets to today's specific readiness and training load, not")
    lines.append("  just restated as generic daily targets.")
    lines.append("")
    lines.append("HYDRATION")
    lines.append("- State clearly whether electrolytes are required today, using the HYDRATION")
    lines.append("  INSTRUCTION above as the basis for your answer, and connect it to today's")
    lines.append("  session type and weather. Do not reference the label 'HYDRATION INSTRUCTION'")
    lines.append("  in your output — just state the recommendation and your reasoning.")
    lines.append("")
    lines.append("COACH'S INTERPRETATION")
    lines.append("- Block-level context: explain how today fits into the current training week,")
    lines.append("  the current phase (see TRAINING PHASE above), and the broader progression")
    lines.append("  toward the 29 August event. If a phase transition is coming next week,")
    lines.append("  mention how today's session choices support or protect that transition.")
    lines.append("- Trend assessment: is Simon trending toward overreaching or progressing")
    lines.append("  normally? Is the training block on track, or are block-level adjustments")
    lines.append("  needed? State this explicitly (e.g. \"normal fatigue for this point in the")
    lines.append("  block\", \"approaching overreaching\", \"no adjustments required\").")
    lines.append("- Conditional outlook for tomorrow: name TOMORROW'S SCHEDULED SESSION TYPE (from")
    lines.append("  the training structure — strength day, long Zone 2 walk, or VO2 Max protocol)")
    lines.append("  and describe the CONDITIONS that would determine whether it proceeds as")
    lines.append("  planned or is adjusted (e.g. \"if HRV recovers toward baseline, tomorrow's")
    lines.append("  scheduled VO2 Max session can proceed as planned; if HRV remains suppressed,")
    lines.append("  reduce tomorrow's intensity again to protect the block\"), using the readiness")
    lines.append("  classification thresholds defined above.")
    lines.append("  Do NOT invent specific numeric predictions for tomorrow's HRV, sleep, or Body")
    lines.append("  Battery — describe conditions and thresholds only, not forecasts.")
    lines.append("")
    lines.append("RISKS & WATCH-FOR")
    lines.append("- What could go wrong today if the plan isn't followed, specific warning signs")
    lines.append("  to watch for during the session (e.g. HR thresholds, fatigue signs), and how")
    lines.append("  to avoid compounding fatigue into tomorrow.")
    lines.append("- Behavioural guidance: what Simon may be tempted to do today that would")
    lines.append("  undermine the plan (e.g. pushing pace, 'making up' for reduced volume,")
    lines.append("  skipping nutrition due to low appetite), what he must avoid, and the correct")
    lines.append("  mindset for the day.")
    lines.append("")
    lines.append("This is a longer, analytical morning brief — provide depth and coaching")
    lines.append("insight in every section, but ground all reasoning strictly in the data")
    lines.append("provided above. Do not invent numbers, symptoms, or causes not supported")
    lines.append("by the metrics. Use plain text only, no markdown or asterisks.")

    # Structured override dict for dashboard consumption
    overrides = {
        "readiness_class":   readiness_class,
        "caliber_cancelled": int(readiness_class == "Red-flag (do not train)" and is_caliber_day),
        "caliber_reduced":   int(readiness_class == "Suppressed" and is_caliber_day),
        "vo2_cancelled":     int(readiness_class in ("Suppressed", "Red-flag (do not train)") and is_vo2_day),
        "walk_cap_mins":     (Z2_SUPP if readiness_class in ("Suppressed", "Red-flag (do not train)") else -1),
        "step_cap":          (step_cap if step_cap is not None else -1),
    }

    return "\n".join(lines), overrides


SYSTEM_PROMPT = """You are an expert personal fitness and nutrition coach with deep knowledge in:
- Endurance sports training (ultra-endurance walking, running, periodisation)
- Post-bariatric sports nutrition — fuelling with a restricted stomach volume
- Evidence-based nutrition science: macronutrient timing, weight loss during training, event-day fuelling
- Heart rate variability (HRV), training load management, and recovery optimisation
- Data analysis of wearable metrics from Garmin devices

ATHLETE PROFILE:
- Name: Simon Davies
- Age: 56, Male
- Gastric sleeve surgery: 14 March 2025 (14+ months post-op, no dumping syndrome)
- Current weight: 115.1 kg
- Goal: Ongoing healthy weight loss while training for an ultra-endurance event
- Stomach volume: approximately 300-450 ml per sitting (~350-400 kcal per eating occasion)
- Eating pattern: 3 meals per day (breakfast, lunch, dinner) plus 2 snacks (protein shake, cottage cheese). No requirement for 8-11 eating occasions per day.
- Post-sleeve hunger signals are unreliable — the athlete may not feel hungry when they need to eat
- Bariatric supplement protocol should be in place (B12, D3, calcium citrate, multivitamin)
- Estimated TDEE scales with current weight — recalculate as weight changes (~23 kcal/kg as a rough guide)
- Target deficit: ~1,000-1,200 kcal/day on rest days -> ~0.8-1.0 kg/week loss

ATHLETE EQUIPMENT:
- Garmin Fenix 8 watch + Polar Verity Sense heart rate monitor (arm-worn, ANT+ to Fenix 8)
- NordicTrack T5 treadmill (home, max 10% incline)
- Garmin Connect for activity tracking; Cronometer for nutrition logging
- Caliber app for strength training — session data captured automatically via Garmin Connect -> GarminStats (ActivitySummary + StrengthSets measurements)

HEART RATE ZONES (age 56, max HR 164 bpm):
- Zone 2 target: 98-115 bpm (aerobic base, primary training zone)
- Garmin Fenix 8 alert at 116 bpm: slow down
- Garmin Fenix 8 alert at 125 bpm: take a break
- All treadmill sessions should target Zone 2 unless explicitly stated otherwise
- Cardiac drift is expected in sessions over 60 minutes. When heart rate rises despite a stable pace or incline, the model must instruct the athlete to reduce speed or incline to remain within Zone 2. Do not push through cardiac drift — maintaining zone integrity is mandatory for aerobic development, heat management, and sustainable ultra-endurance training.

TRAINING CONTEXT:
- 13-week training plan, started Monday 1 June 2026
- Strength training: Caliber app sessions Mon/Wed/Fri (Legs & Abs / Back & Shoulders / Chest & Arms)
- Weeks 1-2 Caliber sessions ran at 2 working sets (not 3) — medically appropriate introduction
  following lipoma surgery recovery. Full 3-set plan resumed from week 2 onwards.
- Tuesday and Thursday are SPLIT SESSIONS — two separate workouts hours apart:
    Session 1 (morning): 20-min outdoor GPS VO2 Max walk — must be outdoors with GPS active;
    GPS signal is required for Garmin's VO2 Max algorithm. HR target 118-130 bpm main block,
    125-135 bpm finisher intervals. Cannot be substituted with treadmill.
    Session 2 (afternoon/evening): Treadmill Zone 2 walk — separate session, done hours later.
    These are NOT back-to-back. Treat them as independent sessions with recovery time between.
- VO2 MAX SESSIONS OCCUR ONLY ON TUESDAYS AND THURSDAYS. They are NEVER scheduled on Monday,
  Wednesday, Friday, Saturday, or Sunday. Do not mention VO2 Max on any other day. Do not
  describe VO2 Max as "cancelled" or "skipped" on a Caliber day — it was never scheduled.
- Saturday: primary long walk — outdoor preferred, treadmill fallback. Progressive duration
  each week. Coach may reduce Saturday target by up to 15% based on readiness.
- HRV from overnight Garmin data is the primary readiness signal:
  - HRV trending up or stable = train as planned
  - HRV significantly suppressed (>10% below recent baseline) = reduce intensity, do not skip
  - HRV critically low alongside poor sleep score (<50) = consider full rest or light recovery walk only

PRIMARY EVENT: 100,000 Steps Challenge — Saturday 29 August 2026
- ~70-80 km walking, 14-18 hours active, classified as ultra-endurance
- Estimated calorie expenditure at 115.1 kg: 5,000-6,500 kcal
- Requires consuming ~3,000-4,000 kcal DURING the event in ~20-25 sleeve-sized portions
- CRITICAL SLEEVE RISK: cannot compensate for missed fuelling with a large meal — once behind, stays behind
- NO SOLID FOOD during the event day itself — liquid and gel fuelling only (absolute constraint, not a preference)
- NOTE: solid food IS consumed normally during training and daily life — this restriction applies ONLY on event day 29 August 2026
- Must sip on schedule regardless of hunger — post-sleeve hunger suppression is amplified by exercise

EVENT-DAY FUELLING PRODUCTS (all tested in training):
- Primary fuel: Tailwind Endurance Fuel (100 kcal/scoop, 25g carbs, 310mg sodium) — sipped continuously from 2L EVOC bladder in 5.11 Rush 12 pack
- Supplement gel: SIS GO Isotonic (88 kcal, 22g carbs, 10mg sodium) — half gel every 45-60 min as texture break. Contains Acesulfame K — tolerance must be confirmed in training
- Optional upgrade: Maurten Gel 100 (100 kcal, 25g carbs, no artificial sweeteners) for back-half variety
- Bladder target: 4 scoops Tailwind per fill, ~3-4 refills during event

DAILY NUTRITION TARGETS:
- Total: 1,600 kcal (rest days and short sessions — appropriate deficit for weight loss)
- Protein: 150 g/day (1.30 g/kg — near post-bariatric floor; hold constant every day)
- Carbohydrates: 149 g baseline (total carbs, as reported by Cronometer — fibre included) — PRIMARY LEVER, increase on training days
- Fat: 45 g/day — hold roughly constant
- Long training days (40K+ steps, 60+ min hard treadmill): carbs rise to 300-360 g, total ~2,600-3,000 kcal
- Spread across many small portions — cannot eat large meals to catch up
- When recommending liquid nutrition (shakes, drinks), always use ml not grams for volume measurements
- Whey protein shakes are typically 250-300ml per serving; cottage cheese is a solid food measured in grams

WHEN ANALYSING DATA:
- Always check if training load was adequately fuelled given sleeve capacity constraints
- Protein below 140 g on any day is a red flag — flag it explicitly
- On days with long sessions, compare actual carb intake to the training-day target, not the 149 g baseline
- Recommend specific small-portion food strategies, not generic "eat more carbs" advice
- Weight loss is the long-term goal; do not recommend maintaining a large deficit on peak training days
- When HRV data is available, reference it explicitly in the readiness assessment
- This brief is delivered each morning — you are coaching for TODAY based on YESTERDAY's data
- ACTIVITIES now include per-session step counts (e.g. "Treadmill Walk: 45 mins, 5,357 steps").
  These are session steps only, not the daily total. The daily total steps are shown separately
  in the activity summary. Use per-session steps to assess session intensity and volume
  (e.g. a 35-min treadmill walk producing ~4,000 steps vs a 60-min walk producing ~7,000 steps
  tells you about actual movement output, not just time). Do not add per-session steps together
  and present as a daily total — the daily total from DailyStats is the authoritative figure.

READINESS CLASSIFICATION:
- Every daily prompt includes a Python pre-computed READINESS CLASSIFICATION line — one of:
  Fully recovered / Partially recovered / Suppressed / Red-flag (do not train)
- This value is computed from HRV (primary signal), sleep, and body battery BEFORE
  the prompt reaches you. It is authoritative. You MUST NOT re-derive it from the
  raw numbers, soften it, upgrade it, or contradict it. If you think the numbers
  suggest a different classification, you are wrong — the Python computation is correct.
- You MUST state this classification verbatim as the first line of the READINESS section.
- The prompt also includes a READINESS CONSEQUENCES line — apply it exactly, no more,
  no less. Do not import rules from a different classification tier.

CLASSIFICATION RULES (what each classification means for training):
- Red-flag (do not train): full rest or light recovery walk only. No strength work,
  no Zone 2 session, no step target.
- Suppressed: reduce volume per the READINESS CONSEQUENCES line in the prompt — it
  specifies exactly what to cancel or reduce for today's session type. VO2 Max sessions
  only occur on Tuesdays and Thursdays — do not mention them on any other day.
- Partially recovered: train AS PLANNED at the LOWER end of ranges. Full working sets
  (3 sets). Full session duration. Normal step target. HR discipline is the primary
  adjustment — keep strictly within Zone 2, do not push pace. DO NOT reduce sets,
  shorten sessions, or apply Suppressed-tier volume cuts on a Partially recovered day.
  Partially recovered ≠ Suppressed. Do not confuse them.

  EXCEPTION — SATURDAY LONG WALK ONLY: on a Partially recovered Saturday, the
  prescribed walk duration IS reduced to 85% of the full plan target (this is the
  one deliberate exception to "full session duration" above). This is intentional
  and pre-computed — the step ceiling injected into this prompt already reflects
  the 85% reduction. Do NOT argue for full Saturday duration on the grounds that
  HRV is good; HRV being above baseline does not override this rule. Rationale:
  Saturday long walks (90-240 min) carry disproportionately more mechanical and
  cumulative CNS load per session than short weekday Zone 2 walks. The reduction
  protects tendon/joint health and recovery capacity across a 13-week block where
  Saturday volume rises continuously toward the event — it is a structural safety
  margin, not a readiness judgement call, and should not be re-derived from HRV
  alone. State the reduced duration and ceiling exactly as given in the prompt.
- Fully recovered: train as planned or push the upper end of ranges.

DEPTH AND REASONING REQUIREMENTS:
- Do not produce a brief literal summary of the numbers. Interpret them like a
  coach: for each signal (HRV, Body Battery, sleep, stress minutes, resting HR,
  steps, weight trend), explain what it means physiologically and what it implies
  for today's training and recovery — not just what the number is.
- Each of READINESS, TODAY'S PLAN, KEY FOCUS, NUTRITION, and HYDRATION should be
  3-5 sentences of substantive reasoning, not 1-2 sentences restating the data.
- In TODAY'S PLAN, explain WHY each adjustment is being made (e.g. why strength is
  skipped, why Zone 2 is shortened) and what the risk is if Simon trains harder
  than prescribed.
- In KEY FOCUS, explain why this is the single most important priority today and
  what happens physiologically if it's ignored.
- In NUTRITION and HYDRATION, connect the targets to today's specific context
  (training load, readiness, weather) rather than restating the static daily
  targets as generic facts.
- Ground all reasoning in the data provided. Do not invent numbers, symptoms, or
  causes that aren't supported by the metrics in the prompt. Do not speculate
  about tomorrow's data — you only have yesterday's and today's information.

COMMUNICATION STYLE:
- Address Simon directly by name at least once, near the start of the brief (e.g. in the readiness assessment) — this is a personal coaching message, not a generic report
- Direct and specific — use the numbers provided
- No generic advice when data is available
- Always reference the 29 August challenge as the primary training target
- Format responses with EXACTLY these seven section headings, in this order:
  READINESS, TODAY'S PLAN, KEY FOCUS, NUTRITION, HYDRATION, COACH'S INTERPRETATION, RISKS & WATCH-FOR
- NUTRITION must give explicit calorie, protein, carb, and fat targets for today,
  matching the baseline figures defined above (e.g. 149g carbohydrates) exactly —
  never substitute yesterday's logged intake as today's target. Yesterday's
  intake may be referenced separately as an observation if relevant.
- HYDRATION must state clearly whether electrolytes are required today, based on
  the pre-computed hydration instruction in the prompt. Do not reference the
  label name in your output — just state the recommendation and your reasoning.
- COACH'S INTERPRETATION (4-6 sentences) must cover three things:
  (1) Block-level context — how today fits into the current training week, the
  current phase (see TRAINING PHASE in the prompt), and the progression toward
  the 29 August event, including how today's choices protect any upcoming phase
  transition.
  (2) Trend/risk assessment — explicitly state whether Simon is in normal fatigue,
  trending toward overreaching, or at risk of non-functional overreaching, and
  whether any block-level adjustments are needed.
  (3) Conditional outlook — describe the CONDITIONS under which tomorrow's plan
  would change (e.g. "if HRV recovers toward baseline, strength resumes as
  planned; if it remains suppressed, expect another reduced day"), using the
  readiness classification thresholds above. Do NOT invent specific numeric
  predictions for tomorrow's HRV, sleep, or Body Battery.
- RISKS & WATCH-FOR (3-5 sentences) must cover both:
  (1) Physical risks — what could go wrong today if the plan isn't followed,
  specific warning signs during the session (e.g. HR thresholds, fatigue signs),
  and how to avoid compounding fatigue into tomorrow.
  (2) Behavioural guidance — what Simon may be tempted to do today that would
  undermine the plan (e.g. pushing pace, "making up" for reduced volume, skipping
  nutrition due to low appetite), what he must avoid, and the correct mindset.
- This is a longer, more analytical morning brief than a quick summary — depth
  and coaching insight are expected, but every sentence must still be grounded in
  the data provided. Avoid padding or repeating the same point across sections.
- Plain text only — no markdown, no asterisks"""


# ── Email sending ─────────────────────────────────────────────────────────────

def format_html_email(ai_response, metrics, date_str, weather=None):
    m = metrics
    weeks_to_event = (datetime.date(2026, 8, 29) - datetime.date.today()).days // 7

    # Strip markdown from AI response
    import re as _re
    clean = ai_response
    clean = _re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', clean)
    clean = _re.sub(r'\*(.+?)\*', r'<em>\1</em>', clean)
    clean = _re.sub(r'^\* ', '', clean, flags=_re.MULTILINE)
    clean = _re.sub(r'^- ', '', clean, flags=_re.MULTILINE)
    clean = _re.sub(r'^#{1,3} ', '', clean, flags=_re.MULTILINE)

    # Style READINESS / TODAY'S PLAN / KEY FOCUS / NUTRITION / HYDRATION /
    # COACH'S INTERPRETATION / RISKS & WATCH-FOR headings
    # Order matters: "NUTRITION CHECK" must be replaced before "NUTRITION" to avoid
    # the shorter heading matching inside the longer one first.
    for heading in ["READINESS", "TODAY'S PLAN", "KEY FOCUS", "NUTRITION CHECK", "NUTRITION",
                    "HYDRATION", "COACH'S INTERPRETATION", "RISKS & WATCH-FOR"]:
        clean = clean.replace(heading,
            f'</p><p style="margin:10px 0 2px;color:#1F497D;font-size:12px;font-weight:bold;'
            f'text-transform:uppercase;letter-spacing:1px;border-bottom:1px solid #2E75B5;'
            f'padding-bottom:2px;">{heading}</p><p style="margin:4px 0;">')

    # Readiness badge — colour-coded, Python-generated so it's always consistent
    # regardless of how Ollama phrases the READINESS section
    _rc = classify_readiness(
        hrv_delta_pct=m.get("hrv_delta_pct"),
        sleep_score=m.get("sleep_score"),
        body_battery_low=m.get("body_battery_low"),
    )
    _badge_colours = {
        "Fully recovered":       ("#1a7a3c", "#d4edda"),   # green
        "Partially recovered":   ("#856404", "#fff3cd"),   # amber
        "Suppressed":            ("#721c24", "#f8d7da"),   # red
        "Red-flag (do not train)": ("#491217", "#f5c6cb"), # deep red
    }
    _fg, _bg = _badge_colours.get(_rc, ("#333", "#eee"))
    readiness_badge = (
        f'<div style="margin-bottom:14px;padding:10px 14px;background:{_bg};'
        f'border-left:4px solid {_fg};border-radius:3px;">'
        f'<span style="font-size:11px;text-transform:uppercase;letter-spacing:1px;'
        f'color:{_fg};font-weight:bold;">Readiness</span>&nbsp;&nbsp;'
        f'<span style="font-size:16px;font-weight:bold;color:{_fg};">{_rc}</span>'
        f'</div>'
    )

    ai_html = clean.replace("\n\n", "</p><p>").replace("\n", "<br>")
    ai_html = f"<p style='margin:0;'>{ai_html}</p>"

    # Readiness badge — Python-generated, colour-coded, always consistent
    _rc = classify_readiness(
        hrv_delta_pct=m.get("hrv_delta_pct"),
        sleep_score=m.get("sleep_score"),
        body_battery_low=m.get("body_battery_low"),
    )
    _badge_colours = {
        "Fully recovered":           ("#1a7a3c", "#d4edda"),
        "Partially recovered":       ("#856404", "#fff3cd"),
        "Suppressed":                ("#721c24", "#f8d7da"),
        "Red-flag (do not train)":   ("#491217", "#f5c6cb"),
    }
    _fg, _bg = _badge_colours.get(_rc, ("#333", "#eee"))
    readiness_badge = (
        f'<div style="margin-bottom:14px;padding:10px 14px;background:{_bg};'
        f'border-left:4px solid {_fg};border-radius:3px;">'
        f'<span style="font-size:11px;text-transform:uppercase;letter-spacing:1px;'
        f'color:{_fg};font-weight:bold;">Readiness&nbsp;&nbsp;</span>'
        f'<span style="font-size:16px;font-weight:bold;color:{_fg};">{_rc}</span>'
        f'</div>'
    )

    def stat_row(label, value, unit="", red=False):
        colour = "#c0392b" if red else "#222"
        return (f'<tr><td style="padding:5px 10px;color:#888;font-size:13px;width:50%;">{label}</td>'
                f'<td style="padding:5px 10px;font-weight:bold;font-size:13px;color:{colour};">'
                f'{value}{" " + unit if unit else ""}</td></tr>')

    def tbl_section(title, rows):
        return (f'<table width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-bottom:16px;">'
                f'<tr><td style="padding:8px 0 4px;color:#1F497D;font-size:12px;font-weight:bold;'
                f'text-transform:uppercase;letter-spacing:1px;border-bottom:2px solid #2E75B5;">{title}</td></tr>'
                f'<tr><td><table width="100%" cellpadding="0" cellspacing="0" border="0">'
                f'{rows}</table></td></tr></table>') if rows else ""

    # Sleep rows
    sleep_rows = ""
    if m.get("sleep_hours"):
        sleep_rows += stat_row("Duration", m.get("sleep_hours"), "hrs")
        sleep_rows += stat_row("Score", m.get("sleep_score", "—"), "/100")
        sleep_rows += stat_row("Overnight HRV", m.get("overnight_hrv", "—"), "ms")
        sleep_rows += stat_row("Deep sleep", m.get("deep_sleep_mins", 0), "mins")
        sleep_rows += stat_row("REM sleep", m.get("rem_sleep_mins", 0), "mins")
        sleep_rows += stat_row("Battery change", m.get("battery_change", "—"))

    # Activity rows
    act_rows  = stat_row("Steps", f"{m.get('steps', 0):,}")
    if m.get("week_steps_so_far") is not None:
        act_rows += stat_row("Week steps", f"{m['week_steps_so_far']:,} / {m['week_steps_target']:,}",
                             red=m["week_steps_remaining"] > m["week_daily_needed"] * m["week_days_remaining"])
    act_rows += stat_row("Distance", m.get("distance_km", 0), "km")
    act_rows += stat_row("Active calories", m.get("active_calories", 0), "kcal")
    rhr_suffix = f" ({m['rhr_status']})" if m.get("rhr_status") else ""
    act_rows += stat_row("Resting HR", f"{m.get('resting_hr', '—')} bpm{rhr_suffix}")
    if m.get("hrv_baseline"):
        delta = m.get("hrv_delta_pct", 0)
        sign = "+" if delta >= 0 else ""
        act_rows += stat_row("HRV vs baseline",
                             f"{m.get('overnight_hrv')} ms ({sign}{delta}%)",
                             red=delta < -10)
    act_rows += stat_row("Body battery", f"{m.get('body_battery_low','—')} → {m.get('body_battery_high','—')}")
    act_rows += stat_row("High stress", m.get("high_stress_mins", 0), "mins")
    if m.get("weight_kg"):
        w_suffix = f" (7d avg: {m['weight_7d_avg']} kg)" if m.get("weight_7d_avg") else ""
        act_rows += stat_row("Weight", f"{m.get('weight_kg')} kg{w_suffix}")

    # Nutrition rows
    nut_rows = ""
    if m.get("calories_in"):
        nut_rows += stat_row("Calories", m.get("calories_in"), "kcal")
        nut_rows += stat_row("Protein", m.get("protein_g"), "g",
                              red=float(m.get("protein_g") or 0) < 140)
        nut_rows += stat_row("Fat", m.get("fat_g"), "g")
        nut_rows += stat_row("Carbs", m.get("carbs_g"), "g")

    # Weather rows for email
    weather_rows = ""
    if weather:
        w = weather
        outdoor_col = "#c0392b" if "poor" in w.get("outdoor_suitability","") else ("#e67e22" if "marginal" in w.get("outdoor_suitability","") or "caution" in w.get("outdoor_suitability","") else "#27ae60")
        weather_rows += (f'<tr><td style="padding:5px 10px;color:#888;font-size:13px;width:50%;">Conditions</td>'
                         f'<td style="padding:5px 10px;font-weight:bold;font-size:13px;">'
                         f'{w.get("description")} · {w.get("temp_min")}–{w.get("temp_max")}°C</td></tr>')
        weather_rows += (f'<tr><td style="padding:5px 10px;color:#888;font-size:13px;">Rain</td>'
                         f'<td style="padding:5px 10px;font-weight:bold;font-size:13px;">'
                         f'{w.get("rain_mm")}mm · {w.get("rain_prob")}% chance · Wind {w.get("wind_max")} km/h</td></tr>')
        weather_rows += (f'<tr><td style="padding:5px 10px;color:#888;font-size:13px;">Morning (8–12am)</td>'
                         f'<td style="padding:5px 10px;font-weight:bold;font-size:13px;">'
                         f'{w.get("morning_temp")}°C · {w.get("morning_rain_pct")}% rain</td></tr>')
        weather_rows += (f'<tr><td style="padding:5px 10px;color:#888;font-size:13px;">Outdoor walk</td>'
                         f'<td style="padding:5px 10px;font-weight:bold;font-size:13px;color:{outdoor_col};">'
                         f'{w.get("outdoor_suitability","—").capitalize()}</td></tr>')

    # Sessions
    session_rows = ""
    if m.get("activities"):
        for a in m["activities"]:
            steps_str = (f'&nbsp;&middot;&nbsp; {a["steps"]:,} steps'
                        if a.get("steps") else "")
            session_rows += (
                f'<tr><td style="padding:6px 10px;border-bottom:1px solid #eee;">'
                f'<strong style="font-size:13px;">{a["name"]}</strong>'
                f'<span style="color:#888;font-size:12px;margin-left:8px;">{a["duration_mins"]} mins</span>'
                f'<span style="color:#555;font-size:12px;margin-left:8px;">'
                f'Avg HR {a["avg_hr"]} bpm &nbsp;&middot;&nbsp; {a["calories"]} kcal'
                f'{steps_str}'
                f'&nbsp;&middot;&nbsp; Load {a["training_load"]}</span>'
                f'</td></tr>'
            )

    # Strength
    strength_rows = ""
    if m.get("caliber_sets"):
        for ex, data in m["caliber_sets"].items():
            strength_rows += (
                f'<tr><td style="padding:5px 10px;border-bottom:1px solid #eee;">'
                f'<strong style="font-size:13px;">{ex}</strong>'
                f'<span style="color:#555;font-size:12px;margin-left:8px;">'
                f'{data["sets"]} sets · {data["total_reps"]} reps · '
                f'max {data["max_weight"]} kg</span></td></tr>'
            )
    elif m.get("strength"):
        for ex, data in m["strength"].items():
            strength_rows += (
                f'<tr><td style="padding:5px 10px;border-bottom:1px solid #eee;">'
                f'<strong style="font-size:13px;">{ex}</strong>'
                f'<span style="color:#555;font-size:12px;margin-left:8px;">'
                f'{data["sets"]} sets · {data["total_reps"]} reps · '
                f'max {data["max_weight"]} kg</span></td></tr>'
            )

    html = f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0"></head>
<body style="font-family:Arial,sans-serif;margin:0;padding:20px;color:#333;background:#f4f6f8;">
<table width="800" cellpadding="0" cellspacing="0" border="0" align="center">

    <tr><td style="background:#1F497D;padding:20px;">
        <div style="color:white;font-size:20px;font-weight:bold;margin-bottom:4px;">Daily Training Brief</div>
        <div style="color:#aed6f1;font-size:13px;">
            {date_str} &nbsp;&middot;&nbsp; Week {TRAINING_WEEK} of 13
            &nbsp;&middot;&nbsp; {weeks_to_event} weeks to Peddars Way
        </div>
    </td></tr>

    <tr><td style="background:#eaf4fb;padding:18px;border-left:4px solid #2E75B5;font-size:14px;line-height:1.7;">
        {readiness_badge}
        {ai_html}
    </td></tr>

    <tr><td style="background:white;padding:20px;">
        {tbl_section("TODAY'S WEATHER", weather_rows) if weather_rows else ""}
        {tbl_section("SESSIONS", session_rows)}
        {tbl_section("STRENGTH DETAIL", strength_rows)}
        {tbl_section("SLEEP", sleep_rows)}
        {tbl_section("ACTIVITY & RECOVERY", act_rows)}
        {tbl_section("NUTRITION", nut_rows)}
    </td></tr>

    <tr><td style="padding:12px;text-align:center;color:#aaa;font-size:11px;">
        Generated by your local AI fitness stack &middot; {datetime.datetime.now().strftime("%H:%M")}
    </td></tr>

</table>
</body></html>"""
    return html




def send_email(subject, html_body):
    """Send email via Brevo REST API — no SMTP, no domain verification needed."""
    url = "https://api.brevo.com/v3/smtp/email"
    payload = {
        "sender": {
            "name": EMAIL_FROM_NAME,
            "email": EMAIL_FROM,
        },
        "to": [{"email": EMAIL_TO}],
        "subject": subject,
        "htmlContent": html_body,
    }
    headers = {
        "accept": "application/json",
        "content-type": "application/json",
        "api-key": BREVO_API_KEY,
    }
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=30)
        resp.raise_for_status()
        log.info(f"Email sent to {EMAIL_TO} via Brevo API (message ID: {resp.json().get('messageId', 'unknown')})")
    except Exception as e:
        log.error(f"Email failed: {e}")
        if hasattr(e, 'response') and e.response is not None:
            log.error(f"Brevo response: {e.response.text}")
        raise



# ── Data freshness check ──────────────────────────────────────────────────────

def get_last_sync_age_minutes():
    """Return how many minutes ago the last Garmin sync occurred."""
    try:
        rows = query_influx("GarminStats",
            "SELECT last(Database_Name) FROM DeviceSync"
        )
        if not rows:
            return None
        last_sync_str = rows[0].get("time")
        if not last_sync_str:
            return None
        # Parse InfluxDB timestamp
        last_sync = datetime.datetime.strptime(
            last_sync_str[:19], "%Y-%m-%dT%H:%M:%S"
        ).replace(tzinfo=datetime.timezone.utc)
        age = (datetime.datetime.now(datetime.timezone.utc) - last_sync).total_seconds() / 60
        return round(age, 1)
    except Exception as e:
        log.warning(f"Could not check sync age: {e}")
        return None


def send_stale_data_warning():
    """Send a warning email when Garmin data hasn't synced within the timeout."""
    subject = f"⚠️ Training Brief Delayed — Garmin Sync Issue ({datetime.date.today().strftime('%a %d %b %Y')})"
    html = f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0"></head>
<body style="font-family:Arial,sans-serif;margin:0;padding:20px;background:#f4f6f8;">
<table width="800" cellpadding="0" cellspacing="0" border="0" align="center">

    <tr><td style="background:#c0392b;padding:20px;">
        <div style="color:white;font-size:20px;font-weight:bold;">⚠️ Garmin Sync Issue</div>
        <div style="color:#fadbd8;font-size:13px;">{datetime.date.today().strftime("%A %d %B %Y")}</div>
    </td></tr>

    <tr><td style="background:white;padding:20px;font-size:14px;line-height:1.7;">
        <p>Your daily training brief could not be generated because Garmin Connect has not
        synced recently. The last sync was more than 60 minutes ago and the timeout period
        has been reached.</p>

        <p><strong>What to do:</strong></p>
        <ul>
            <li>Open the Garmin Connect app on your phone to force a sync</li>
            <li>Check the Garmin Connect app is running in the background</li>
            <li>Ensure your Fenix 8 is within Bluetooth range of your phone</li>
        </ul>

        <p>The brief will resume automatically tomorrow morning. If you want today's
        coaching advice, open your Garmin Connect app to sync, then check your data
        manually in Grafana at <code>http://nas:3000</code>.</p>

        <p style="color:#888;font-size:12px;">This warning is sent when no Garmin sync
        is detected within 60 minutes after the brief is due to send.</p>
    </td></tr>

    <tr><td style="padding:12px;text-align:center;color:#aaa;font-size:11px;">
        Local AI Fitness Stack · {datetime.datetime.now().strftime("%d %b %Y %H:%M UTC")}
    </td></tr>

</table>
</body></html>"""
    send_email(subject, html)
    log.warning("Stale data warning email sent")


def wait_for_fresh_data(timeout_hour=8, timeout_minute=0, freshness_minutes=60, retry_minutes=10):
    """
    Wait for fresh Garmin data before generating the brief.
    Returns True if fresh data arrived, False if timeout reached.

    timeout_hour/minute: UTC time after which we give up and send warning
    freshness_minutes: data must be this recent to be considered fresh
    retry_minutes: how often to check while waiting
    """
    timeout_time = datetime.datetime.now(datetime.timezone.utc).replace(
        hour=timeout_hour, minute=timeout_minute, second=0, microsecond=0
    )

    while True:
        age = get_last_sync_age_minutes()

        if age is not None and age <= freshness_minutes:
            log.info(f"Fresh data confirmed — last sync {age:.1f} minutes ago")
            return True

        now = datetime.datetime.now(datetime.timezone.utc)
        if now >= timeout_time:
            log.warning(f"Timeout reached — last sync was {age:.1f} min ago (limit: {freshness_minutes} min)")
            return False

        mins_remaining = (timeout_time - now).total_seconds() / 60
        log.info(f"Data stale ({age:.1f} min old) — retrying in {retry_minutes} min "
                 f"({mins_remaining:.0f} min until timeout)")
        time.sleep(retry_minutes * 60)


# ── Scheduler ─────────────────────────────────────────────────────────────────

def seconds_until(hour, minute):
    now = datetime.datetime.now()
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if now >= target:
        target += datetime.timedelta(days=1)
    return (target - now).total_seconds()


def run_daily_brief():
    import fcntl
    _lockfile = open('/tmp/daily_brief.lock', 'w')
    try:
        fcntl.flock(_lockfile, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except IOError:
        log.warning("run_daily_brief() already running — skipping duplicate execution")
        _lockfile.close()
        return
    try:
        yesterday = (datetime.date.today() - datetime.timedelta(days=1)).strftime("%A %d %B %Y")
        today_str = datetime.date.today().strftime("%A %d %B %Y")
        log.info(f"Running daily brief for {today_str}")

        age = get_last_sync_age_minutes()
        if age:
            log.info(f"Garmin data freshness: {age:.1f} minutes since last sync")

        # Fetch data
        log.info("Fetching metrics from InfluxDB...")
        metrics = get_yesterday_metrics()

        log.info("Fetching weather forecast...")
        weather = get_weather_forecast()
        if weather:
            log.info(f"Weather: {weather.get('description')}, max {weather.get('temp_max')}C, "
                     f"outdoor: {weather.get('outdoor_suitability')}")
        log.info(f"Got metrics: steps={metrics.get('steps')}, sleep={metrics.get('sleep_hours')}h, "
                 f"HRV={metrics.get('overnight_hrv')}ms")

        # Fetch yesterday's coach note for context
        log.info("Fetching yesterday's coach note for context...")
        yesterday_date = datetime.date.today() - datetime.timedelta(days=1)
        prev_notes = get_weekly_coach_notes(yesterday_date, datetime.date.today())
        yesterday_note = prev_notes[0].get("note", "") if prev_notes else ""
        if yesterday_note:
            log.info(f"Yesterday's coach note: {yesterday_note}")
        else:
            log.info("No yesterday coach note found")

        # Call AI
        log.info("Calling Ollama for AI analysis...")
        prompt, overrides = build_prompt(metrics, today_str, yesterday_note, weather)
        ai_response = call_lm_studio(prompt, SYSTEM_PROMPT)

        if not ai_response:
            ai_response = (
                "AI ANALYSIS UNAVAILABLE\n\n"
                "LM Studio did not respond within the timeout period. "
                "Check that Max is powered on, LM Studio is running, "
                "and the Qwen3.6-27B model is loaded.\n\n"
                "Your data is shown below — review manually today."
            )
            log.warning("Using fallback message — LM Studio unreachable")

        # Extract and persist today's coaching decision to InfluxDB
        # This gives the weekly report context about WHY things happened
        log.info("Extracting coach note for weekly context...")
        coach_note = extract_coach_note(ai_response, metrics, today_str)
        store_coach_note(coach_note, today_str, overrides=overrides)

        # Build and send email
        subject = f"Training Brief — {today_str}"
        html = format_html_email(ai_response, metrics, today_str, weather)
        send_email(subject, html)
        log.info("Daily brief complete")




    finally:
        fcntl.flock(_lockfile, fcntl.LOCK_UN)
        _lockfile.close()


# ── Weekly summary ────────────────────────────────────────────────────────────

def get_weekly_metrics(week_start, week_end):
    """Query InfluxDB for a full Mon-Sun week of data."""
    ws = week_start.isoformat()
    we = week_end.isoformat()
    m = {}

    # Daily stats aggregated - separate queries to avoid mixing aggregates
    rows = query_influx("GarminStats",
        f"SELECT mean(totalSteps) AS steps FROM DailyStats "
        f"WHERE time >= '{ws}T00:00:00Z' AND time < '{we}T00:00:00Z'"
    )
    if rows:
        m["avg_steps"] = int(rows[0].get("steps") or 0)

    rows = query_influx("GarminStats",
        f"SELECT mean(restingHeartRate) AS rhr FROM DailyStats "
        f"WHERE time >= '{ws}T00:00:00Z' AND time < '{we}T00:00:00Z'"
    )
    if rows:
        m["avg_rhr"] = round(rows[0].get("rhr") or 0, 1)

    rows = query_influx("GarminStats",
        f"SELECT mean(bodyBatteryHighestValue) AS bb FROM DailyStats "
        f"WHERE time >= '{ws}T00:00:00Z' AND time < '{we}T00:00:00Z'"
    )
    if rows:
        m["avg_bb"] = int(rows[0].get("bb") or 0)

    rows = query_influx("GarminStats",
        f"SELECT sum(totalSteps) AS total FROM DailyStats "
        f"WHERE time >= '{ws}T00:00:00Z' AND time < '{we}T00:00:00Z'"
    )
    if rows:
        m["total_steps"] = int(rows[0].get("total") or 0)
    m["avg_stress"] = 0  # calculated separately if needed

    # Sleep aggregated - separate queries
    rows = query_influx("GarminStats",
        f"SELECT mean(sleepTimeSeconds) AS sleep FROM SleepSummary "
        f"WHERE time >= '{ws}T12:00:00Z' AND time < '{we}T23:59:59Z' "
        f"AND sleepTimeSeconds > 3600"
    )
    if rows:
        m["avg_sleep_hrs"] = round((rows[0].get("sleep") or 0) / 3600, 1)

    rows = query_influx("GarminStats",
        f"SELECT mean(sleepScore) AS score FROM SleepSummary "
        f"WHERE time >= '{ws}T12:00:00Z' AND time < '{we}T23:59:59Z' "
        f"AND sleepTimeSeconds > 3600"
    )
    if rows:
        m["avg_sleep_score"] = int(rows[0].get("score") or 0)

    rows = query_influx("GarminStats",
        f"SELECT mean(avgOvernightHrv) AS hrv FROM SleepSummary "
        f"WHERE time >= '{ws}T12:00:00Z' AND time < '{we}T23:59:59Z' "
        f"AND sleepTimeSeconds > 3600"
    )
    if rows:
        m["avg_hrv"] = round(rows[0].get("hrv") or 0, 1)

    # Weight — first and last of the week
    rows = query_influx("GarminStats",
        f"SELECT first(weight) AS first_w, last(weight) AS last_w FROM BodyComposition "
        f"WHERE time >= '{ws}T00:00:00Z' AND time < '{we}T00:00:00Z'"
    )
    if rows:
        r = rows[0]
        fw = r.get("first_w") or 0
        lw = r.get("last_w") or 0
        m["weight_start"] = round(fw / 1000 if fw > 1000 else fw, 2)
        m["weight_end"]   = round(lw / 1000 if lw > 1000 else lw, 2)
        if m["weight_start"] and m["weight_end"]:
            m["weight_change"] = round(m["weight_end"] - m["weight_start"], 2)

    # Activities
    rows = query_influx("GarminStats",
        f"SELECT activityName, activityType, elapsedDuration, calories, averageHR, activityTrainingLoad "
        f"FROM ActivitySummary WHERE time >= '{ws}T00:00:00Z' AND time < '{we}T00:00:00Z' "
        f"ORDER BY time ASC"
    )
    caliber_sessions = []
    walk_sessions    = []
    for r in rows:
        act_type = r.get("activityType", "")
        duration = float(r.get("elapsedDuration") or 0)

        # Weekday (0=Mon .. 6=Sun) of this activity, for VO2 Max session classification
        weekday = None
        act_time = r.get("time")
        if act_time:
            try:
                weekday = datetime.datetime.strptime(act_time[:19], "%Y-%m-%dT%H:%M:%S").weekday()
            except ValueError:
                weekday = None

        if "strength" in act_type.lower():
            caliber_sessions.append({
                "name":     r.get("activityName", "Strength"),
                "duration": round(duration / 60, 0),
                "calories": int(r.get("calories") or 0),
                "load":     round(r.get("activityTrainingLoad") or 0, 1),
            })
        elif act_type in ("treadmill_running", "walking", "indoor_walking") or "walk" in act_type.lower():
            walk_sessions.append({
                "name":     r.get("activityName", "Walk"),
                "duration": round(duration / 60, 0),
                "avg_hr":   int(r.get("averageHR") or 0),
                "calories": int(r.get("calories") or 0),
                "weekday":  weekday,
            })
    m["caliber_sessions"] = caliber_sessions
    m["walk_sessions"]    = walk_sessions

    # Strength sets — per exercise max weight using GROUP BY tag
    rows = query_influx("GarminStats",
        f"SELECT max(weight_kg) AS max_weight FROM StrengthSets "
        f"WHERE time >= '{ws}T00:00:00Z' AND time < '{we}T00:00:00Z' "
        f"AND weight_kg > 0 GROUP BY exercise"
    )
    strength_summary = {}
    # InfluxDB GROUP BY tag returns series with tags dict
    try:
        client_tmp = InfluxDBClient(
            host=INFLUX_HOST, port=INFLUX_PORT,
            username=INFLUX_USER, password=INFLUX_PASS,
            database="GarminStats",
        )
        result = client_tmp.query(
            f"SELECT max(weight_kg) AS max_weight, sum(volume_kg) AS volume "
            f"FROM StrengthSets WHERE time >= '{ws}T00:00:00Z' AND time < '{we}T00:00:00Z' "
            f"AND weight_kg > 0 GROUP BY exercise"
        )
        client_tmp.close()
        for series_key, series_points in result.items():
            # series_key is a tuple (measurement, tags_dict)
            if isinstance(series_key, tuple) and len(series_key) > 1:
                tags = series_key[1]
                ex = tags.get("exercise", "Unknown") if tags else "Unknown"
            else:
                ex = "Unknown"
            if ex and ex != "Unknown":
                pts = list(series_points)
                if pts:
                    strength_summary[ex] = {
                        "max_weight": round(pts[0].get("max_weight") or 0, 1),
                        "volume":     round(pts[0].get("volume") or 0, 1),
                    }
    except Exception as e:
        log.warning(f"Strength summary query failed: {e}")
    m["strength_summary"] = strength_summary

    # Nutrition averages - separate queries
    rows = query_influx("CronometerStats",
        f"SELECT mean(Energy_kcal) AS kcal FROM daily_nutrition "
        f"WHERE time >= '{ws}T00:00:00Z' AND time < '{we}T00:00:00Z'"
    )
    if rows:
        m["avg_kcal"] = int(rows[0].get("kcal") or 0)

    rows = query_influx("CronometerStats",
        f"SELECT mean(Protein_g) AS protein FROM daily_nutrition "
        f"WHERE time >= '{ws}T00:00:00Z' AND time < '{we}T00:00:00Z'"
    )
    if rows:
        m["avg_protein"] = round(rows[0].get("protein") or 0, 1)

    rows = query_influx("CronometerStats",
        f"SELECT mean(Fat_g) AS fat FROM daily_nutrition "
        f"WHERE time >= '{ws}T00:00:00Z' AND time < '{we}T00:00:00Z'"
    )
    if rows:
        m["avg_fat"] = round(rows[0].get("fat") or 0, 1)

    rows = query_influx("CronometerStats",
        f"SELECT mean(Carbs_g) AS carbs FROM daily_nutrition "
        f"WHERE time >= '{ws}T00:00:00Z' AND time < '{we}T00:00:00Z'"
    )
    if rows:
        m["avg_carbs"] = round(rows[0].get("carbs") or 0, 1)

    # Protein red flag days
    rows = query_influx("CronometerStats",
        f"SELECT count(Protein_g) AS low_days FROM daily_nutrition "
        f"WHERE time >= '{ws}T00:00:00Z' AND time < '{we}T00:00:00Z' AND Protein_g < 140"
    )
    m["low_protein_days"] = int(rows[0].get("low_days") or 0) if rows else 0

    # ── HRV baseline (30-day avg ending at week start) ────────────────────────
    try:
        thirty_ago = (week_start - datetime.timedelta(days=31)).isoformat()
        rows = query_influx("GarminStats",
            f"SELECT mean(avgOvernightHrv) AS hrv_avg FROM SleepSummary "
            f"WHERE time >= '{thirty_ago}T07:00:00Z' "
            f"AND time < '{ws}T07:00:00Z' AND avgOvernightHrv > 0"
        )
        if rows and rows[0].get("hrv_avg"):
            m["hrv_baseline"] = round(rows[0]["hrv_avg"], 1)
            avg_hrv = m.get("avg_hrv", 0) or 0
            if avg_hrv > 0 and m["hrv_baseline"] > 0:
                delta = round(((avg_hrv - m["hrv_baseline"]) / m["hrv_baseline"]) * 100, 1)
                m["hrv_vs_baseline_pct"] = delta
    except Exception as e:
        log.warning(f"Weekly HRV baseline query failed: {e}")

    # ── Prior week strength for progression comparison ─────────────────────────
    try:
        prior_start = (week_start - datetime.timedelta(days=7)).isoformat()
        prior_end   = ws
        client_tmp = InfluxDBClient(
            host=INFLUX_HOST, port=INFLUX_PORT,
            username=INFLUX_USER, password=INFLUX_PASS,
            database="GarminStats",
        )
        result = client_tmp.query(
            f"SELECT max(weight_kg) AS max_weight, sum(volume_kg) AS volume "
            f"FROM StrengthSets WHERE time >= '{prior_start}T00:00:00Z' "
            f"AND time < '{prior_end}T00:00:00Z' AND weight_kg > 0 GROUP BY exercise"
        )
        client_tmp.close()
        prior_strength = {}
        for series_key, series_points in result.items():
            if isinstance(series_key, tuple) and len(series_key) > 1:
                tags = series_key[1]
                ex = tags.get("exercise", "") if tags else ""
            else:
                ex = ""
            if ex:
                pts = list(series_points)
                if pts:
                    prior_strength[ex] = {
                        "max_weight": round(pts[0].get("max_weight") or 0, 1),
                        "volume":     round(pts[0].get("volume") or 0, 1),
                    }
        m["prior_strength"] = prior_strength
    except Exception as e:
        log.warning(f"Weekly prior strength query failed: {e}")

    # ── Per-day step breakdown ────────────────────────────────────────────────
    try:
        rows = query_influx("GarminStats",
            f"SELECT totalSteps FROM DailyStats "
            f"WHERE time >= '{ws}T00:00:00Z' AND time < '{we}T00:00:00Z' "
            f"ORDER BY time ASC"
        )
        day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        daily_steps = []
        for i, r in enumerate(rows):
            label = day_names[i] if i < 7 else f"Day{i+1}"
            daily_steps.append({"day": label, "steps": int(r.get("totalSteps") or 0)})
        m["daily_steps"] = daily_steps
    except Exception as e:
        log.warning(f"Weekly daily step breakdown query failed: {e}")

    # ── Zone 2 HR compliance on walks ────────────────────────────────────────
    # Flag walks where avg HR was outside Zone 2 (98-115 bpm).
    #
    # Tue/Thu VO2 Max protocol sessions (~20 min, outdoor GPS, 118-130 bpm main
    # block + 125-135 bpm push intervals per TRAINING_PLAN_V2) are a separate
    # activity from the Zone 2 walks and are NOT held to the 98-115 bpm band —
    # an average HR around 115-140 is expected/correct for that session type.
    # They're tracked separately as "vo2max_sessions" instead.
    try:
        rows = query_influx("GarminStats",
            f"SELECT activityName, averageHR, elapsedDuration "
            f"FROM ActivitySummary "
            f"WHERE time >= '{ws}T00:00:00Z' AND time < '{we}T00:00:00Z' "
            f"AND (activityType = 'walking' OR activityType = 'treadmill_running' "
            f"OR activityType = 'indoor_walking') "
            f"ORDER BY time ASC"
        )
        zone2_compliance = []
        vo2max_sessions  = []
        for r in rows:
            avg_hr   = int(r.get("averageHR") or 0)
            duration = round((r.get("elapsedDuration") or 0) / 60, 0)

            weekday = None
            act_time = r.get("time")
            if act_time:
                try:
                    weekday = datetime.datetime.strptime(act_time[:19], "%Y-%m-%dT%H:%M:%S").weekday()
                except ValueError:
                    weekday = None

            # VO2 Max protocol session: Tue (1) or Thu (3), short (~15-25 min)
            is_vo2max = weekday in (1, 3) and 15 <= duration <= 25

            if is_vo2max:
                if avg_hr > 0:
                    if avg_hr < 110:
                        vo2_status = "below target — push harder in the main block"
                    elif avg_hr <= 140:
                        vo2_status = "VO2 Max protocol — correct"
                    else:
                        vo2_status = "above expected range — check for an outlier reading"
                    vo2max_sessions.append({
                        "name":     r.get("activityName", "VO2 Max walk"),
                        "avg_hr":   avg_hr,
                        "duration": duration,
                        "weekday":  weekday,
                        "status":   vo2_status,
                    })
                continue

            if avg_hr > 0 and duration >= 20:
                if avg_hr < 98:
                    status = "below Zone 2 — too easy"
                elif avg_hr <= 115:
                    status = "Zone 2 — correct"
                elif avg_hr <= 125:
                    status = "above Zone 2 — slightly hard"
                else:
                    status = "well above Zone 2 — too hard"
                zone2_compliance.append({
                    "name":    r.get("activityName", "Walk"),
                    "avg_hr":  avg_hr,
                    "duration": duration,
                    "status":  status,
                })
        m["zone2_compliance"] = zone2_compliance
        m["vo2max_sessions"]  = vo2max_sessions
    except Exception as e:
        log.warning(f"Zone 2 compliance query failed: {e}")

    return m


def calculate_compliance(metrics, week_num, coach_notes=None):
    """Calculate training compliance for the week.

    Score reflects compliance with the COACH-DIRECTED plan, not the original
    static plan. Sessions cancelled or reduced by the daily coach due to
    suppressed readiness are excluded from the denominator — they represent
    correct execution, not missed sessions.
    """
    caliber_done = len(metrics.get("caliber_sessions", []))
    walks_done   = len([w for w in metrics.get("walk_sessions", []) if w["duration"] >= 60])
    short_walks  = len([w for w in metrics.get("walk_sessions", []) if 30 <= w["duration"] < 60])

    # VO2 Max protocol sessions (Tue/Thu, ~20 min, GPS outdoor)
    vo2_sessions  = metrics.get("vo2max_sessions", [])
    vo2_days_done = sorted(set(s["weekday"] for s in vo2_sessions if s.get("weekday") in (1, 3)))
    vo2_done      = len(vo2_days_done)
    vo2_planned   = 2 if week_num > 0 else 0
    weekday_names = {1: "Tuesday", 3: "Thursday"}
    vo2_missed_days = [weekday_names[d] for d in (1, 3) if d not in vo2_days_done] if week_num > 0 else []

    if week_num <= 0:
        planned_caliber = 0
        planned_long    = 6
    else:
        planned_caliber = 3
        planned_long    = 3

    # Count coach-directed cancellations from CoachNotes so we can subtract
    # them from the planned denominator — a cancelled-by-coach session is
    # correct execution, not a missed workout.
    notes = coach_notes or []
    # Keywords indicating a Caliber session was cancelled OR reduced by coach.
    # "Reduced to 2 sets" still counts as coach-compliant — the session happened
    # but was modified. Both full cancellations and reductions should remove the
    # session from the planned denominator if coach-directed.
    caliber_cancel_kw = ["cancelled", "canceled", "cancel", "skipped strength",
                         "skipped legs", "skipped back", "skipped chest",
                         "no caliber", "skipped caliber",
                         "reduced strength", "reduce strength", "2 sets",
                         "2 working sets", "dropped to 2", "cut to 2"]
    walk_cancel_kw    = ["cancelled walk", "no walk", "rest only",
                         "cancelled all training", "auto-reduced", "detraining adjustment",
                         "consecutive low-activity days"]

    coach_cancelled_caliber = len(set(
        n.get("date", "") for n in notes
        if any(w in n.get("note", "").lower() for w in caliber_cancel_kw)
    ))
    coach_cancelled_walks = len(set(
        n.get("date", "") for n in notes
        if any(w in n.get("note", "").lower() for w in walk_cancel_kw)
    ))

    # Adjusted planned counts — cannot drop below what was actually completed
    adj_caliber_planned = max(caliber_done, planned_caliber - coach_cancelled_caliber)
    adj_walk_planned    = max(walks_done,   planned_long    - coach_cancelled_walks)

    total_planned   = adj_caliber_planned + adj_walk_planned
    total_completed = min(caliber_done, adj_caliber_planned) + min(walks_done, adj_walk_planned)
    score = round((total_completed / total_planned * 100) if total_planned else 0, 0)

    return {
        "caliber_planned":         adj_caliber_planned,
        "caliber_done":            caliber_done,
        "long_walks_planned":      adj_walk_planned,
        "long_walks_done":         walks_done,
        "short_walks_done":        short_walks,
        "vo2max_planned":          vo2_planned,
        "vo2max_done":             vo2_done,
        "vo2max_missed_days":      vo2_missed_days,
        "score":                   score,
        "coach_cancelled_caliber": coach_cancelled_caliber,
        "coach_cancelled_walks":   coach_cancelled_walks,
    }


def get_phase(week_num):
    if week_num <= 4:
        return "Phase 1 — Foundation"
    elif week_num <= 8:
        return "Phase 2 — Build"
    elif week_num <= 11:
        return "Phase 3 — Peak"
    else:
        return "Phase 4 — Taper"


def build_causal_analysis(this_week, compliance, coach_notes):
    """
    Pre-compute the full causal chain and emit verbatim verdicts for the
    sections Ollama consistently gets wrong when left to reason from numbers.

    Causal chain: suppressed readiness → reduced volume → reduced steps
                  → reduced TDEE → slower weight loss → expected outcome.
    """
    notes   = coach_notes or []
    n_notes = len(notes)

    cancellations = [n for n in notes if any(w in n.get("note","").lower()
                     for w in ["cancelled","canceled","cancel","skipped strength",
                                "skipped legs","skipped back","skipped chest",
                                "skipped caliber","no caliber"])]
    reductions    = [n for n in notes if any(w in n.get("note","").lower()
                     for w in ["reduced","reduce","cut","shorter","2 sets",
                                "2 working sets","dropped to 2","removed vo2",
                                "capped steps"])]
    standard_days = [n for n in notes if "standard plan" in n.get("note","").lower()]

    deload_days = len(cancellations) + len(reductions)
    deload_week = deload_days >= max(1, n_notes // 2) if n_notes else False

    avg_hrv     = this_week.get("avg_hrv", 0) or 0
    avg_sleep   = this_week.get("avg_sleep_score", 0) or 0
    avg_bb      = this_week.get("avg_bb", 0) or 0
    total_steps = this_week.get("total_steps", 0) or 0
    wc          = this_week.get("weight_change") or 0
    avg_kcal    = this_week.get("avg_kcal", 0) or 0
    avg_protein = this_week.get("avg_protein", 0) or 0

    # Readiness label
    if avg_hrv and avg_hrv < 42:
        readiness = f"chronically suppressed (avg HRV {avg_hrv}ms, sleep score {avg_sleep}/100, Body Battery avg {avg_bb})"
    elif avg_hrv and avg_hrv < 52:
        readiness = f"below baseline for most of the week (avg HRV {avg_hrv}ms, sleep score {avg_sleep}/100)"
    else:
        readiness = f"adequate (avg HRV {avg_hrv}ms, sleep score {avg_sleep}/100)"

    cancel_days    = ", ".join(n["date"] for n in cancellations) if cancellations else None
    reduction_days = ", ".join(n["date"] for n in reductions)    if reductions    else None

    # Weight verdict
    if deload_week:
        if wc > -0.5:
            weight_verdict = (
                f"Weight change of {wc:+.2f} kg is the direct, expected result of the "
                f"coach-prescribed deload. Fewer sessions and shorter walks reduced TDEE, "
                f"which narrowed the calorie deficit, which slowed fat loss. This is correct "
                f"physiology — not a nutrition failure. Do not suggest Simon ate too much, "
                f"and do not blame carbohydrates or calories for this outcome."
            )
        elif wc <= -0.8:
            weight_verdict = (
                f"Weight change of {wc:+.2f} kg is on or above target despite a reduced-volume "
                f"week — an excellent result driven by strong nutrition adherence."
            )
        else:
            weight_verdict = (
                f"Weight change of {wc:+.2f} kg is slightly below the 0.8-1.0 kg target, "
                f"which is the expected and acceptable consequence of reduced training volume "
                f"prescribed by the daily coach. This is not a nutrition failure."
            )
    else:
        if wc and wc <= -0.8:
            weight_verdict = f"Weight change of {wc:+.2f} kg — on target."
        elif wc and wc > -0.4:
            weight_verdict = (
                f"Weight change of {wc:+.2f} kg is below target this week. No coach-directed "
                f"deload explains the shortfall — review nutrition and activity data."
            )
        else:
            weight_verdict = f"Weight change of {wc:+.2f} kg — slightly below target, monitor next week."

    # Compliance verdict
    if deload_week:
        compliance_verdict = (
            "Compliance this week was 100% of the prescribed plan. "
            + (f"The Caliber session on {cancel_days} was cancelled by the daily coach due to "
               f"critically suppressed readiness — this is not a missed workout, it is a correct "
               f"coaching decision that Simon executed exactly as instructed. "
               if cancel_days else "")
            + (f"Walk durations on {reduction_days} were shortened by coach instruction due to "
               f"suppressed HRV and low Body Battery — the lower step counts on those days "
               f"represent compliance, not underperformance. "
               if reduction_days else "")
            + "Simon followed every instruction issued by the daily coach this week. "
              "The words missed, skipped, and fell short must not appear in this section."
        )
    elif not cancellations and not reductions:
        compliance_verdict = (
            "Compliance was full — standard plan followed all week with no coach-directed "
            "modifications required."
        )
    else:
        compliance_verdict = (
            "Compliance was good. Some sessions were adjusted by coach instruction: "
            + (f"cancelled on {cancel_days}; " if cancel_days else "")
            + (f"reduced on {reduction_days}." if reduction_days else "")
        )

    # Pre-computed weekly readiness classification — same four categories as the
    # daily brief, derived from weekly averages (HRV vs 30-day baseline, avg sleep
    # score, avg Body Battery as a proxy for body battery low).
    weekly_readiness_class = classify_readiness(
        hrv_delta_pct=this_week.get("hrv_vs_baseline_pct"),
        sleep_score=avg_sleep,
        body_battery_low=avg_bb,
    )

    # Assemble block
    lines = [
        "════════════════════════════════════════════════════════",
        "PRE-COMPUTED ANALYSIS — READ BEFORE WRITING ANYTHING",
        "════════════════════════════════════════════════════════",
        "",
        "These are factual verdicts derived from the raw data and coach decision log.",
        "You MUST reflect these conclusions in your report sections.",
        "Do NOT reinterpret, soften, or contradict the verdicts below.",
        "",
        f"WEEKLY READINESS CLASSIFICATION (state verbatim in WEEK SUMMARY): {weekly_readiness_class}",
        "",
        f"READINESS: {readiness}.",
        "",
    ]

    if cancel_days:
        lines.append(f"COACH CANCELLATIONS (not athlete choice): {cancel_days}.")
    if reduction_days:
        lines.append(f"COACH REDUCTIONS (not athlete choice): {reduction_days}.")
    if not cancellations and not reductions:
        lines.append("MODIFICATIONS: None — standard plan executed in full.")

    lines += [
        "",
        f"STEPS: {total_steps:,} total. "
        + ("Lower than original plan because walk durations were shortened by coach instruction. "
           "Simon completed every session he was asked to complete. "
           "Do not frame this step count as underperformance."
           if deload_week else "Reflects standard plan execution."),
        "",
        f"WEIGHT & NUTRITION VERDICT: {weight_verdict}",
        "",
        f"COMPLIANCE VERDICT: {compliance_verdict}",
        "",
        "════════════════════════════════════════════════════════",
        "Now write the weekly report using the verdicts above.",
        "For COMPLIANCE: reproduce the COMPLIANCE VERDICT almost verbatim.",
        "For WEIGHT & NUTRITION: reproduce the WEIGHT & NUTRITION VERDICT and do not",
        "contradict it by blaming food choices or implying Simon overate.",
        "For WEEK SUMMARY: lead with the fact that readiness was suppressed and that",
        "all volume reductions were coach-directed — frame it as a deload week executed",
        "correctly, not as a week where targets were missed.",
        "════════════════════════════════════════════════════════",
        "",
    ]

    return "\n".join(lines)


def build_python_sections(this_week, compliance, coach_notes, week_num=1):
    """
    Write WEEK SUMMARY, COMPLIANCE, and WEIGHT & NUTRITION in Python
    from pre-computed facts — these sections must not be delegated to Ollama
    because the model consistently misinterprets causal relationships between
    readiness, volume, steps, TDEE, and weight loss.
    """
    notes   = coach_notes or []
    n_notes = len(notes)

    cancellations = [n for n in notes if any(w in n.get("note","").lower()
                     for w in ["cancelled","canceled","cancel","skipped strength",
                                "skipped legs","skipped back","skipped chest",
                                "skipped caliber","no caliber"])]
    reductions    = [n for n in notes if any(w in n.get("note","").lower()
                     for w in ["reduced","reduce","cut","shorter","2 sets",
                                "2 working sets","dropped to 2","removed vo2",
                                "capped steps"])]

    deload_days = len(cancellations) + len(reductions)
    deload_week = deload_days >= max(1, n_notes // 2) if n_notes else False

    avg_hrv     = this_week.get("avg_hrv", 0) or 0
    avg_sleep   = this_week.get("avg_sleep_score", 0) or 0
    avg_bb      = this_week.get("avg_bb", 0) or 0
    total_steps = this_week.get("total_steps", 0) or 0
    avg_steps   = this_week.get("avg_steps", 0) or 0
    wc          = this_week.get("weight_change") or 0
    avg_kcal    = this_week.get("avg_kcal", 0) or 0
    avg_protein = this_week.get("avg_protein", 0) or 0

    cancel_days    = ", ".join(n["date"] for n in cancellations) if cancellations else None
    reduction_days = ", ".join(n["date"] for n in reductions)    if reductions    else None

    cal_done  = compliance.get("caliber_done", 0)
    cal_plan  = compliance.get("caliber_planned", 0)
    walk_done = compliance.get("long_walks_done", 0)
    walk_plan = compliance.get("long_walks_planned", 0)

    # Same four-category classification used in the daily brief, applied to
    # weekly averages — stated as the first line of WEEK SUMMARY.
    weekly_readiness_class = classify_readiness(
        hrv_delta_pct=this_week.get("hrv_vs_baseline_pct"),
        sleep_score=avg_sleep,
        body_battery_low=avg_bb,
    )

    # ── WEEK SUMMARY ──────────────────────────────────────────────────────────
    if deload_week:
        summary_lines = [
            "WEEK SUMMARY",
            "",
            f"READINESS CLASSIFICATION: {weekly_readiness_class}",
            "",
        ]
        readiness_desc = (
            f"suppressed readiness throughout the week (avg HRV {avg_hrv}ms, "
            f"sleep score {avg_sleep}/100, Body Battery avg {avg_bb})"
            if avg_hrv < 50
            else f"reduced readiness (avg HRV {avg_hrv}ms, sleep score {avg_sleep}/100)"
        )
        hrv_base     = this_week.get("hrv_baseline", 0)
        hrv_vs_base  = this_week.get("hrv_vs_baseline_pct")
        hrv_context  = ""
        if hrv_base and hrv_vs_base is not None:
            sign = "+" if hrv_vs_base >= 0 else ""
            hrv_context = f" HRV averaged {avg_hrv}ms ({sign}{hrv_vs_base}% vs 30-day baseline of {hrv_base}ms)."

        summary_lines.append(
            f"Week {week_num} established consistent training with full compliance with the adjusted plan. "
            f"Due to {readiness_desc}, the daily coach prescribed reduced walk durations"
            + (f" and cancelled the Caliber session on {cancel_days}" if cancel_days else "")
            + f".{hrv_context} As expected, this lowered total step volume and TDEE, resulting in slightly "
              f"slower weight loss — the normal and correct outcome of a recovery-driven deload week."
        )
        week_summary = "\n".join(summary_lines)
    else:
        hrv_base     = this_week.get("hrv_baseline", 0)
        hrv_vs_base  = this_week.get("hrv_vs_baseline_pct")
        hrv_context  = ""
        if hrv_base and hrv_vs_base is not None:
            sign = "+" if hrv_vs_base >= 0 else ""
            hrv_context = f" HRV averaged {avg_hrv}ms ({sign}{hrv_vs_base}% vs 30-day baseline of {hrv_base}ms)."

        if cancellations or reductions:
            # Some modifications occurred but not enough to trigger full deload_week —
            # still acknowledge them rather than claiming standard plan
            mod_summary = (
                (f"The daily coach cancelled sessions on {', '.join(set(n['date'] for n in cancellations))}. " if cancellations else "")
                + (f"Sessions were reduced on {', '.join(set(n['date'] for n in reductions))}. " if reductions else "")
            )
            week_summary = (
                "WEEK SUMMARY\n\n"
                f"READINESS CLASSIFICATION: {weekly_readiness_class}\n\n"
                f"Week {week_num} — coach-directed modifications applied. {mod_summary}"
                f"Average daily steps: {avg_steps:,}.{hrv_context} "
                f"Sleep score {avg_sleep}/100."
            )
        else:
            week_summary = (
                "WEEK SUMMARY\n\n"
                f"READINESS CLASSIFICATION: {weekly_readiness_class}\n\n"
                f"Week {week_num} — standard plan followed with no coach-directed modifications. "
                f"Average daily steps: {avg_steps:,}.{hrv_context} "
                f"Sleep score {avg_sleep}/100."
            )

    # ── COMPLIANCE ────────────────────────────────────────────────────────────
    compliance_lines = ["COMPLIANCE", ""]

    if deload_week:
        compliance_lines.append(
            f"Compliance this week was 100% of the prescribed plan. "
        )
        if cancel_days:
            compliance_lines.append(
                f"The Caliber session on {cancel_days} was cancelled by the daily coach due to "
                f"critically suppressed readiness — this is a correct coaching decision, not a "
                f"missed workout. Simon executed the instruction exactly as given."
            )
        if reduction_days:
            compliance_lines.append(
                f"Walk durations on {reduction_days} were shortened by coach instruction in response "
                f"to suppressed HRV and low Body Battery. The lower step counts on those days "
                f"represent full compliance, not underperformance."
            )
        compliance_lines.append(
            f"Simon followed every instruction issued by the daily coach this week. "
            f"As readiness improves, aim to complete all {cal_plan} Caliber sessions "
            f"and full walk durations — unless recovery metrics indicate otherwise."
        )

    # Zone 2 compliance — append to compliance section
    z2 = this_week.get("zone2_compliance", [])
    if z2:
        z2_ok   = [s for s in z2 if s["status"] == "Zone 2 — correct"]
        z2_hard = [s for s in z2 if "above" in s["status"]]
        z2_easy = [s for s in z2 if "below" in s["status"]]
        compliance_lines.append(
            f"Zone 2 HR compliance: {len(z2_ok)} of {len(z2)} walks stayed within 98–115 bpm."
            + (f" {len(z2_hard)} walk(s) exceeded Zone 2 — "
               f"avg HR {', '.join(str(s['avg_hr']) for s in z2_hard)} bpm. "
               f"Reduce pace earlier when HR drifts above 116 bpm." if z2_hard else "")
            + (" All walks within Zone 2 — excellent HR discipline." if not z2_hard and len(z2_ok) == len(z2) else "")
        )
    else:
        if cal_done >= cal_plan and walk_done >= walk_plan:
            compliance_lines.append(
                f"Full compliance — all {cal_plan} Caliber sessions and {walk_plan} long walks completed."
            )
        else:
            if cal_done < cal_plan:
                compliance_lines.append(
                    f"Caliber: {cal_done} of {cal_plan} sessions completed. "
                    f"Review whether any were coach-directed reductions or unplanned misses."
                )
            if walk_done < walk_plan:
                compliance_lines.append(
                    f"Long walks: {walk_done} of {walk_plan} completed."
                )

    # VO2 Max sessions (Tue/Thu protocol) — separate from Zone 2 walks
    vo2_planned = compliance.get("vo2max_planned", 0)
    vo2_done    = compliance.get("vo2max_done", 0)
    vo2_missed  = compliance.get("vo2max_missed_days", [])
    vo2_sessions = this_week.get("vo2max_sessions", [])
    if vo2_planned:
        if vo2_done >= vo2_planned:
            compliance_lines.append(
                f"VO2 Max sessions: {vo2_done} of {vo2_planned} completed — full protocol adherence."
            )
        else:
            missed_str = " and ".join(vo2_missed) if vo2_missed else "one or more days"
            compliance_lines.append(
                f"VO2 Max sessions: {vo2_done} of {vo2_planned} completed — {missed_str} missed. "
                f"Check whether this was coach-directed or an unplanned miss."
            )
        out_of_range = [s for s in vo2_sessions if s["status"] != "VO2 Max protocol — correct"]
        if out_of_range:
            for s in out_of_range:
                compliance_lines.append(
                    f"VO2 Max session ({['Mon','Tue','Wed','Thu','Fri','Sat','Sun'][s['weekday']]}): "
                    f"avg HR {s['avg_hr']} bpm — {s['status']}."
                )

    # ── WEIGHT & NUTRITION ────────────────────────────────────────────────────
    wn_lines = ["WEIGHT & NUTRITION", ""]

    if deload_week and wc is not None:
        if wc > -0.5:
            wn_lines.append(
                f"Weight change of {wc:+.2f} kg is slightly below the 0.8-1.0 kg weekly target. "
                f"This is the direct, expected result of the coach-prescribed deload — shorter walks "
                f"and the cancelled Caliber session reduced TDEE, which narrowed the calorie deficit, "
                f"which slowed fat loss. This is correct physiology, not a nutrition problem."
            )
        elif wc <= -0.8:
            wn_lines.append(
                f"Weight change of {wc:+.2f} kg — on target despite a reduced-volume week. "
                f"Strong nutrition adherence drove the result."
            )
        else:
            wn_lines.append(
                f"Weight change of {wc:+.2f} kg is slightly below target, expected given the "
                f"coach-prescribed reduction in training volume this week."
            )
    elif wc is not None:
        label = "on target" if wc <= -0.8 else "below target"
        wn_lines.append(f"Weight change of {wc:+.2f} kg — {label} for the week.")

    wn_lines.append(
        f"Calorie intake averaged {avg_kcal} kcal"
        + (" — slightly above the 1,600 kcal target but not meaningfully impacting fat loss rate." if avg_kcal > 1650 else " — within target.")
    )
    wn_lines.append(
        f"Protein averaged {avg_protein}g"
        + (" — exceeding the 150g target, supporting muscle retention." if avg_protein >= 150
           else " — below the 150g target, prioritise protein in coming week." if avg_protein < 140
           else " — on target.")
    )
    if this_week.get("low_protein_days", 0) > 0:
        wn_lines.append(
            f"Red flag: {this_week['low_protein_days']} day(s) with protein below 140g — "
            f"muscle retention is at risk. Identify which days and address in Week {week_num + 1}."
        )

    return {
        "week_summary":   week_summary,
        "compliance":     "\n".join(compliance_lines),
        "weight_nutrition": "\n".join(wn_lines),
    }


def build_weekly_prompt(this_week, last_week, compliance, week_num, week_start, week_end, coach_notes=None):
    """Build the weekly summary prompt for the AI."""
    today       = datetime.date.today()
    event_date  = datetime.date(2026, 8, 29)
    weeks_left  = (event_date - today).days // 7
    next_week   = week_num + 1
    phase       = get_phase(week_num)
    next_phase  = get_phase(next_week)

    # Pre-compute causal chain — hands Ollama facts not just rules
    causal_block = build_causal_analysis(this_week, compliance, coach_notes)
    notes_block  = build_coach_notes_block(coach_notes or [])

    lines = [
        f"WEEKLY TRAINING SUMMARY — {week_start.strftime('%d %b')} to {week_end.strftime('%d %b %Y')},",
        f"Phase: {phase}",
        f"Weeks until 100,000 Steps Challenge (29 Aug 2026): {weeks_left}",
        "",
        causal_block,
    ]

    if notes_block:
        lines.append(notes_block)
        lines.append("")

    # ── Per-day step breakdown string ────────────────────────────────────────
    daily_steps = this_week.get("daily_steps", [])
    if daily_steps:
        step_breakdown = " | ".join(f"{d['day']}: {d['steps']:,}" for d in daily_steps)
    else:
        step_breakdown = "not available"

    # ── Sleep trend direction ─────────────────────────────────────────────────
    sleep_score_this = this_week.get("avg_sleep_score", 0) or 0
    sleep_score_last = last_week.get("avg_sleep_score", 0) or 0
    if sleep_score_last and sleep_score_this:
        sleep_delta = sleep_score_this - sleep_score_last
        sleep_trend = f"{sleep_delta:+.0f} vs last week — {'improving' if sleep_delta > 2 else 'declining' if sleep_delta < -2 else 'stable'}"
    else:
        sleep_trend = "no prior data"

    hrv_this = this_week.get("avg_hrv", 0) or 0
    hrv_last = last_week.get("avg_hrv", 0) or 0
    if hrv_last and hrv_this:
        hrv_delta = hrv_this - hrv_last
        hrv_trend = f"{hrv_delta:+.1f}ms vs last week — {'improving' if hrv_delta > 1 else 'declining' if hrv_delta < -1 else 'stable'}"
    else:
        hrv_trend = "no prior data"

    hrv_baseline   = this_week.get("hrv_baseline", 0)
    hrv_vs_base    = this_week.get("hrv_vs_baseline_pct")
    hrv_base_str   = ""
    if hrv_baseline and hrv_vs_base is not None:
        sign = "+" if hrv_vs_base >= 0 else ""
        hrv_base_str = f" | vs 30-day baseline {hrv_baseline}ms: {sign}{hrv_vs_base}%"

    # ── Zone 2 compliance summary ─────────────────────────────────────────────
    z2 = this_week.get("zone2_compliance", [])
    z2_ok    = [s for s in z2 if s["status"] == "Zone 2 — correct"]
    z2_hard  = [s for s in z2 if "above" in s["status"]]
    z2_easy  = [s for s in z2 if "below" in s["status"]]
    if z2:
        z2_summary = (f"{len(z2_ok)}/{len(z2)} walks in Zone 2"
                      + (f", {len(z2_hard)} too hard (avg HR {', '.join(str(s['avg_hr']) for s in z2_hard)} bpm)" if z2_hard else "")
                      + (f", {len(z2_easy)} too easy" if z2_easy else ""))
    else:
        z2_summary = "no walk HR data available"

    lines += [
        "THIS WEEK vs LAST WEEK:",
        "",
        "STEPS & ACTIVITY:",
        f"- Total steps this week: {this_week.get('total_steps', 0):,} | Last week: {last_week.get('total_steps', 0):,}",
        f"- Average daily steps: {this_week.get('avg_steps', 0):,} | Last week: {last_week.get('avg_steps', 0):,}",
        f"- Per-day breakdown: {step_breakdown}",
        f"- Caliber sessions completed: {compliance['caliber_done']} of {compliance['caliber_planned']} originally planned (see causal analysis above)",
        f"- Long walks (60+ min) completed: {compliance['long_walks_done']} of {compliance['long_walks_planned']} originally planned (see causal analysis above)",
        f"- Short walks on gym days: {compliance['short_walks_done']}",
        f"- Zone 2 HR compliance on walks: {z2_summary}",
        f"- VO2 Max sessions (Tue/Thu protocol): {compliance.get('vo2max_done', 0)} of {compliance.get('vo2max_planned', 0)} completed"
        + (f" — missed: {', '.join(compliance['vo2max_missed_days'])}" if compliance.get('vo2max_missed_days') else ""),
        f"- Raw compliance score vs original plan: {compliance['score']}% (see causal analysis above for true compliance assessment)",
        "",
        "SLEEP & RECOVERY:",
        f"- Average sleep: {this_week.get('avg_sleep_hrs', 0)}h | Last week: {last_week.get('avg_sleep_hrs', 0)}h",
        f"- Average sleep score: {sleep_score_this}/100 ({sleep_trend})",
        f"- Average overnight HRV: {hrv_this}ms ({hrv_trend}){hrv_base_str}",
        f"- Average resting HR: {this_week.get('avg_rhr', 0)} bpm | Last week: {last_week.get('avg_rhr', 0)} bpm",
        "",
        "WEIGHT TREND:",
    ]

    ws = this_week.get("weight_start", 0)
    we = this_week.get("weight_end", 0)
    wc = this_week.get("weight_change", 0)
    notes = coach_notes or []
    deload_week = len([n for n in notes if any(w in n.get("note","").lower()
                       for w in ["cancelled","canceled","reduced","reduce","cut","shorter","2 sets"])]) >= (len(notes) // 2) if notes else False
    if ws and we:
        if deload_week and wc is not None and wc > -0.8:
            weight_note = " — reduced loss expected: lower TDEE from coach-prescribed volume reduction"
        else:
            target_label = "on track" if wc and -1.0 <= wc <= -0.8 else ("above target loss" if wc and wc < -1.0 else "below target loss")
            weight_note = f" — {target_label}"
        lines.append(f"- Start of week: {ws} kg | End of week: {we} kg")
        lines.append(f"- Change: {wc:+.2f} kg (target: -0.8 to -1.0 kg/week{weight_note})")
    else:
        lines.append("- No weight data recorded this week")

    lines.append("")
    lines.append("NUTRITION (average daily vs targets):")
    lines.append(f"- Calories: {this_week.get('avg_kcal', 0)} kcal (target: 1,600 kcal)")
    lines.append(f"- Protein: {this_week.get('avg_protein', 0)}g (target: 150g)")
    lines.append(f"- Carbs: {this_week.get('avg_carbs', 0)}g (target: 149g baseline)")
    lines.append(f"- Fat: {this_week.get('avg_fat', 0)}g (target: 45g)")
    if this_week.get("low_protein_days", 0) > 0:
        lines.append(f"- RED FLAG: {this_week['low_protein_days']} day(s) with protein below 140g")

    if this_week.get("strength_summary"):
        lines.append("")
        lines.append("STRENGTH PROGRESSION (max weight per exercise this week vs prior session):")
        # Use prior_strength from the dedicated prior-week query (more reliable than
        # last_week strength_summary which may cover a different session split)
        prior_strength = this_week.get("prior_strength") or last_week.get("strength_summary", {})
        for ex, data in this_week["strength_summary"].items():
            prior = prior_strength.get(ex, {})
            last_max = prior.get("max_weight", 0)
            if last_max:
                change = round(data["max_weight"] - last_max, 1)
                if change > 0:
                    trend = f"+{change} kg — progression"
                elif change < 0:
                    trend = f"{change} kg — regression, review form/fatigue"
                else:
                    trend = "held — consolidating"
            else:
                trend = "baseline — no prior data"
            lines.append(f"- {ex}: {data['max_weight']} kg ({trend})")

    if this_week.get("low_protein_days", 0) > 0:
        lines.append("")
        lines.append(f"PROTEIN ALERT: {this_week['low_protein_days']} day(s) this week had protein below 140g — "
                     f"muscle retention is at risk. Identify which days and correct in Week {week_num + 1}.")

    lines.append("")
    lines.append(f"COMING WEEK — Week {next_week} of 13 ({next_phase}):")
    lines.append(f"- Mon: Caliber Legs & Abs + short walk (~5,000 steps)")
    lines.append(f"- Tue: Long walk — Zone 2, 70+ min")
    lines.append(f"- Wed: Caliber Back & Shoulders + short walk")
    lines.append(f"- Thu: Long walk — Zone 2, 70+ min")
    lines.append(f"- Fri: Caliber Chest & Arms + short walk")
    lines.append(f"- Sat: Long walk — see Week {next_week} Saturday target in training plan")
    lines.append(f"- Sun: Rest")
    if next_week == 10:
        lines.append("- *** DRESS REHEARSAL WEEK — Saturday is full event simulation ***")
    elif next_week >= 12:
        lines.append("- *** TAPER WEEK — reduce volume, hold frequency ***")

    lines.append("")
    lines.append("Write ONLY these three sections — nothing else:")
    lines.append("HIGHLIGHTS — 3-4 bullet points on what genuinely went well this week")
    lines.append("AREAS TO ADDRESS — 2-3 specific items needing attention next week")
    lines.append("COMING WEEK — 4-5 specific actionable focus points for next week")
    lines.append("")
    lines.append("Do NOT write WEEK SUMMARY, COMPLIANCE, or WEIGHT & NUTRITION sections.")
    lines.append("Those are written separately from pre-computed data.")
    lines.append("Use plain text only — no markdown, no asterisks.")

    return "\n".join(lines)


WEEKLY_SYSTEM_PROMPT = """You are an expert personal fitness and nutrition coach providing a weekly performance review for Simon Davies, 56, male, 115 kg, preparing for the 100,000 Steps Challenge on 29 August 2026. He had gastric sleeve surgery 14 March 2025. His 13-week training plan started 1 June 2026.

Weekly training structure: Mon/Wed/Fri = Caliber gym sessions + short walk. Tue/Thu = SPLIT SESSIONS (morning: 20-min outdoor GPS VO2 Max walk; afternoon/evening: treadmill Zone 2 walk — separate sessions hours apart). Sat = progressive long walk (outdoor preferred). Sun = rest.
Zone 2: 98-115 bpm. Weight loss target: 0.8-1.0 kg/week. Protein target: 150g/day (never below 140g).

CRITICAL — HOW TO INTERPRET COMPLIANCE:
Simon is coached daily by an AI system that issues specific modifications each morning based on HRV, sleep score, and Body Battery. The data you receive includes a DAILY COACHING DECISIONS section listing every modification made that week.

You MUST apply these rules without exception:
- If the daily coach told Simon to skip or cancel a session, it MUST NOT be counted as missed or flagged as non-compliance. It was the correct decision and Simon followed coach instructions.
- If the daily coach told Simon to reduce walk duration or steps, the lower output MUST NOT be described as falling short of targets. Simon did exactly what was asked of him.
- If the daily coach told Simon to reduce Caliber sets, that MUST NOT be framed as incomplete training.
- NEVER use language like "missed", "skipped", "failed to complete", or "fell short" for any session or metric that was modified by a coaching decision.
- ALWAYS attribute modifications to the coaching system using phrases like "as directed by the daily coach", "following coach guidance", "the coach advised".
- Assess compliance against the ADAPTED plan, not the original plan, when coaching modifications were issued.
- Simon's job is to follow the coach. If he followed the coach, his compliance is good — say so clearly.

Be direct and data-driven. Celebrate genuine improvements. Flag real concerns clearly. Keep each section concise. Use plain text only.

In the HIGHLIGHTS section, open by addressing Simon directly by name (e.g. "Simon, this week...") — this is a personal weekly review, not a generic report."""


def format_weekly_html(ai_response, python_sections, this_week, last_week, compliance, week_num, week_start, week_end, coach_notes=None):
    """Format the weekly summary as HTML email."""
    event_date = datetime.date(2026, 8, 29)
    weeks_left = (event_date - datetime.date.today()).days // 7
    phase      = get_phase(week_num)

    import re as _re
    clean = ai_response
    clean = _re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', clean)
    clean = _re.sub(r'\*(.+?)\*', r'<em>\1</em>', clean)
    clean = _re.sub(r'^\* ', '', clean, flags=_re.MULTILINE)
    clean = _re.sub(r'^- ', '', clean, flags=_re.MULTILINE)
    clean = _re.sub(r'^#{1,3} ', '', clean, flags=_re.MULTILINE)
    ai_html = clean.replace("\n\n", "</p><p>").replace("\n", "<br>")
    ai_html = f"<p>{ai_html}</p>"

    def stat_row(label, this_val, last_val, unit="", highlight=False):
        color = "#c0392b" if highlight else "#333"
        return f"""<tr>
            <td style="padding:5px 10px;color:#888;font-size:13px;">{label}</td>
            <td style="padding:5px 10px;font-weight:bold;font-size:13px;color:{color};">{this_val}{' ' + unit if unit else ''}</td>
            <td style="padding:5px 10px;color:#aaa;font-size:12px;">{last_val}{' ' + unit if unit else ''}</td>
        </tr>"""

    def section(title, rows_html):
        return f"""<div style="margin-bottom:20px;">
            <h3 style="margin:0 0 8px;color:#1a5276;font-size:13px;text-transform:uppercase;
                       letter-spacing:1px;border-bottom:2px solid #1a5276;padding-bottom:4px;">{title}</h3>
            <table style="width:100%;border-collapse:collapse;">
                <tr style="background:#f5f5f5;">
                    <th style="padding:4px 10px;text-align:left;font-size:11px;color:#888;">Metric</th>
                    <th style="padding:4px 10px;text-align:left;font-size:11px;color:#888;">This Week</th>
                    <th style="padding:4px 10px;text-align:left;font-size:11px;color:#aaa;">Last Week</th>
                </tr>
                {rows_html}
            </table>
        </div>"""

    # Compliance badge colour
    score = compliance["score"]
    badge_colour = "#27ae60" if score >= 90 else ("#f39c12" if score >= 70 else "#c0392b")

    # Steps
    steps_rows = stat_row("Total steps", f"{this_week.get('total_steps',0):,}", f"{last_week.get('total_steps',0):,}")
    steps_rows += stat_row("Average daily steps", f"{this_week.get('avg_steps',0):,}", f"{last_week.get('avg_steps',0):,}")
    steps_rows += stat_row("Long walks completed", f"{compliance['long_walks_done']} of {compliance['long_walks_planned']}", "—")
    steps_rows += stat_row("Caliber sessions", f"{compliance['caliber_done']} of {compliance['caliber_planned']}", "—")
    z2 = this_week.get("zone2_compliance", [])
    if z2:
        z2_ok = len([s for s in z2 if s["status"] == "Zone 2 — correct"])
        z2_bad = len(z2) - z2_ok
        steps_rows += stat_row("Zone 2 compliance", f"{z2_ok}/{len(z2)} walks", "—", highlight=z2_bad > 0)

    # Sleep
    sleep_score_this = this_week.get("avg_sleep_score", 0) or 0
    sleep_score_last = last_week.get("avg_sleep_score", 0) or 0
    sleep_delta = sleep_score_this - sleep_score_last if sleep_score_last else 0
    sleep_arrow = "▲" if sleep_delta > 2 else "▼" if sleep_delta < -2 else "→"
    hrv_this = this_week.get("avg_hrv", 0) or 0
    hrv_base = this_week.get("hrv_baseline", 0)
    hrv_vs_base = this_week.get("hrv_vs_baseline_pct")
    hrv_base_label = ""
    if hrv_base and hrv_vs_base is not None:
        sign = "+" if hrv_vs_base >= 0 else ""
        hrv_base_label = f" ({sign}{hrv_vs_base}% vs baseline)"
    sleep_rows  = stat_row("Avg sleep", this_week.get("avg_sleep_hrs", 0), last_week.get("avg_sleep_hrs", 0), "hrs")
    sleep_rows += stat_row("Avg sleep score", f"{sleep_score_this}/100 {sleep_arrow}", f"{sleep_score_last}/100")
    sleep_rows += stat_row("Avg overnight HRV", f"{hrv_this}ms{hrv_base_label}", f"{last_week.get('avg_hrv', 0)}ms")
    sleep_rows += stat_row("Avg resting HR", this_week.get("avg_rhr", 0), last_week.get("avg_rhr", 0), "bpm")

    # Weight
    ws = this_week.get("weight_start", 0)
    we = this_week.get("weight_end", 0)
    wc = this_week.get("weight_change", 0)
    on_track = -1.0 <= wc <= -0.8 if wc else False
    weight_rows  = stat_row("Start of week", f"{ws} kg" if ws else "—", "—")
    weight_rows += stat_row("End of week", f"{we} kg" if we else "—", "—")
    weight_rows += stat_row("Change", f"{wc:+.2f} kg" if wc else "—", "Target: -0.8 to -1.0 kg",
                            highlight=bool(wc and wc > -0.5))

    # Nutrition
    nut_rows  = stat_row("Avg calories", this_week.get('avg_kcal',0), "1,600", "kcal",
                          highlight=this_week.get('avg_kcal',0) < 1400)
    nut_rows += stat_row("Avg protein", this_week.get('avg_protein',0), "150", "g",
                          highlight=this_week.get('avg_protein',0) < 140)
    nut_rows += stat_row("Avg carbs", this_week.get('avg_carbs',0), "149", "g")
    nut_rows += stat_row("Avg fat", this_week.get('avg_fat',0), "45", "g")
    if this_week.get('low_protein_days', 0) > 0:
        nut_rows += f"""<tr><td colspan="3" style="padding:5px 10px;color:#c0392b;font-size:12px;">
            ⚠️ {this_week['low_protein_days']} day(s) with protein below 140g</td></tr>"""

    # Strength — prefer prior_strength (dedicated query) over last_week summary
    strength_html = ""
    if this_week.get("strength_summary"):
        last_s = this_week.get("prior_strength") or last_week.get("strength_summary", {})
        for ex, data in this_week["strength_summary"].items():
            last_max = last_s.get(ex, {}).get("max_weight", 0)
            if last_max and data["max_weight"] > last_max:
                badge = f'<span style="background:#27ae60;color:white;padding:1px 5px;border-radius:3px;font-size:11px;">PR</span>'
            else:
                badge = ""
            prev = f"{last_max} kg" if last_max else "—"
            strength_html += f"""<div style="display:flex;justify-content:space-between;padding:5px 0;
                border-bottom:1px solid #f0f0f0;font-size:13px;">
                <span>{ex} {badge}</span>
                <span><strong>{data['max_weight']} kg</strong> <span style="color:#aaa;">vs {prev}</span></span>
            </div>"""

    # Format AI response with styled section headers
    import re as _re2
    def style_sections(text):
        sections = ["WEEK SUMMARY","HIGHLIGHTS","AREAS TO ADDRESS","STRENGTH REPORT",
                    "WEIGHT & NUTRITION","COMPLIANCE","COMING WEEK"]
        for s in sections:
            text = text.replace(s,
                f'</p><p style="margin:12px 0 2px 0;color:#1a5276;font-size:12px;'
                f'font-weight:bold;text-transform:uppercase;letter-spacing:1px;'
                f'border-bottom:1px solid #2e86c1;padding-bottom:3px;">{s}</p><p style="margin:4px 0;">')
        return text

    def plain_to_html(text):
        return text.replace("\n\n", "</p><p>").replace("\n", "<br>")

    # Stitch: Python-written sections come first, then Ollama's HIGHLIGHTS/AREAS/COMING WEEK
    py = python_sections or {}
    week_summary_html   = style_sections(plain_to_html(py.get("week_summary", "")))
    compliance_html     = style_sections(plain_to_html(py.get("compliance", "")))
    weight_nut_html     = style_sections(plain_to_html(py.get("weight_nutrition", "")))

    # Ollama response: strip any WEEK SUMMARY / COMPLIANCE / WEIGHT & NUTRITION
    # it may have written despite instructions, keep only HIGHLIGHTS/AREAS/COMING WEEK
    ollama_clean = ai_response or ""
    for banned in ["WEEK SUMMARY", "COMPLIANCE", "WEIGHT & NUTRITION"]:
        # Remove from that heading to the next known heading or end
        import re as _re3
        ollama_clean = _re3.sub(
            rf'{banned}.*?(?=HIGHLIGHTS|AREAS TO ADDRESS|STRENGTH REPORT|COMING WEEK|$)',
            '', ollama_clean, flags=_re3.DOTALL
        )
    ollama_html = style_sections(plain_to_html(ollama_clean.strip()))

    ai_html_styled = (
        f"<p>{week_summary_html}</p>"
        f"<p>{ollama_html}</p>"
        f"<p>{weight_nut_html}</p>"
        f"<p>{compliance_html}</p>"
    )

    def card(label, value, sub, colour="#333"):
        return f"""<td width="33%" style="padding:4px;">
            <table width="100%" cellpadding="14" cellspacing="0" border="0"
                   style="background:#f0f4f8;border:1px solid #dce6f0;">
                <tr><td align="center">
                    <div style="font-size:10px;color:#888;text-transform:uppercase;
                                letter-spacing:1px;margin-bottom:4px;">{label}</div>
                    <div style="font-size:28px;font-weight:bold;color:{colour};
                                line-height:1.1;">{value}</div>
                    <div style="font-size:11px;color:#888;margin-top:4px;">{sub}</div>
                </td></tr>
            </table>
        </td>"""

    def tbl_section(title, rows_html):
        return f"""<table width="100%" cellpadding="0" cellspacing="0" border="0"
                         style="margin-bottom:16px;">
            <tr><td style="padding:8px 0 4px;color:#1a5276;font-size:12px;font-weight:bold;
                           text-transform:uppercase;letter-spacing:1px;
                           border-bottom:2px solid #1a5276;">
                {title}
            </td></tr>
            <tr><td>
                <table width="100%" cellpadding="0" cellspacing="0" border="0">
                    <tr style="background:#f0f4f8;">
                        <td width="50%" style="padding:4px 8px;font-size:11px;color:#888;font-weight:bold;">Metric</td>
                        <td width="25%" style="padding:4px 8px;font-size:11px;color:#888;font-weight:bold;">This Week</td>
                        <td width="25%" style="padding:4px 8px;font-size:11px;color:#aaa;font-weight:bold;">Last Week</td>
                    </tr>
                    {rows_html}
                </table>
            </td></tr>
        </table>"""

    def tbl_row(label, this_val, last_val, unit="", red=False):
        colour = "#c0392b" if red else "#222"
        return f"""<tr style="border-bottom:1px solid #eee;">
            <td style="padding:6px 8px;font-size:13px;color:#555;">{label}</td>
            <td style="padding:6px 8px;font-size:13px;font-weight:bold;color:{colour};">
                {this_val}{' ' + unit if unit else ''}</td>
            <td style="padding:6px 8px;font-size:12px;color:#aaa;">
                {last_val}{' ' + unit if unit else ''}</td>
        </tr>"""

    steps_rows  = tbl_row("Total steps", f"{this_week.get('total_steps',0):,}", f"{last_week.get('total_steps',0):,}")
    steps_rows += tbl_row("Average daily steps", f"{this_week.get('avg_steps',0):,}", f"{last_week.get('avg_steps',0):,}")
    steps_rows += tbl_row("Long walks (60+ min)", f"{compliance['long_walks_done']} of {compliance['long_walks_planned']}", "—")
    steps_rows += tbl_row("Caliber sessions", f"{compliance['caliber_done']} of {compliance['caliber_planned']}", "—",
                           red=compliance['caliber_done'] < compliance['caliber_planned'])

    sleep_rows  = tbl_row("Avg sleep", this_week.get('avg_sleep_hrs',0), last_week.get('avg_sleep_hrs',0), "hrs")
    sleep_rows += tbl_row("Avg sleep score", this_week.get('avg_sleep_score',0), last_week.get('avg_sleep_score',0), "/100")
    sleep_rows += tbl_row("Avg overnight HRV", this_week.get('avg_hrv',0), last_week.get('avg_hrv',0), "ms")
    sleep_rows += tbl_row("Avg resting HR", this_week.get('avg_rhr',0), last_week.get('avg_rhr',0), "bpm")

    ws = this_week.get("weight_start", 0)
    we = this_week.get("weight_end", 0)
    wc = this_week.get("weight_change", 0)
    on_track = -1.0 <= wc <= -0.8 if wc else False
    weight_rows  = tbl_row("Start of week", f"{ws} kg" if ws else "—", f"{last_week.get('weight_start',0)} kg" if last_week.get('weight_start') else "—")
    weight_rows += tbl_row("End of week", f"{we} kg" if we else "—", f"{last_week.get('weight_end',0)} kg" if last_week.get('weight_end') else "—")
    weight_rows += tbl_row("Change", f"{wc:+.2f} kg" if wc else "—", "Target: -0.8 to -1.0 kg",
                            red=bool(wc and wc > -0.5))

    nut_rows  = tbl_row("Avg calories", f"{this_week.get('avg_kcal',0)}", "1,600", "kcal",
                         red=this_week.get('avg_kcal',0) < 1400)
    nut_rows += tbl_row("Avg protein", f"{this_week.get('avg_protein',0)}", "150", "g",
                         red=this_week.get('avg_protein',0) < 140)
    nut_rows += tbl_row("Avg carbs", f"{this_week.get('avg_carbs',0)}", "149", "g")
    nut_rows += tbl_row("Avg fat", f"{this_week.get('avg_fat',0)}", "45", "g")
    if this_week.get('low_protein_days', 0) > 0:
        nut_rows += f"""<tr><td colspan="3" style="padding:6px 8px;color:#c0392b;font-size:12px;">
            &#9888; {this_week['low_protein_days']} day(s) with protein below 140g</td></tr>"""

    strength_rows = ""
    if this_week.get("strength_summary"):
        last_s = last_week.get("strength_summary", {})
        for ex, data in this_week["strength_summary"].items():
            last_max = last_s.get(ex, {}).get("max_weight", 0)
            pr_badge = ' <span style="background:#27ae60;color:white;padding:1px 4px;font-size:10px;">PR</span>'                        if last_max and data["max_weight"] > last_max else ""
            prev = f"{last_max} kg" if last_max else "—"
            strength_rows += tbl_row(f"{ex}{pr_badge}", f"{data['max_weight']} kg", prev)

    badge_colour = "#27ae60" if score >= 90 else ("#e67e22" if score >= 70 else "#c0392b")
    wc_colour    = "#27ae60" if on_track else "#c0392b"

    week_start_fmt = week_start.strftime("%d %b")
    week_end_fmt   = week_end.strftime("%d %b %Y")

    html = f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0"></head>
<body style="font-family:Arial,sans-serif;margin:0;padding:20px;color:#333;background:#f4f6f8;">
<table width="860" cellpadding="0" cellspacing="0" border="0" align="center">

    <!-- Header -->
    <tr><td style="background:#1a5276;padding:24px;border-radius:8px 8px 0 0;">
        <div style="color:white;font-size:22px;font-weight:bold;margin-bottom:4px;">Weekly Training Report</div>
        <div style="color:#aed6f1;font-size:13px;">
            {week_start_fmt} &ndash; {week_end_fmt} &nbsp;&middot;&nbsp; {phase} &nbsp;&middot;&nbsp; {weeks_left} weeks to Peddars Way
        </div>
    </td></tr>

    <!-- Stat cards -->
    <tr><td style="background:white;padding:16px;">
        <table width="100%" cellpadding="0" cellspacing="0" border="0">
        <tr>
            {card(
                "Compliance",
                f"{int(score)}%",
                (
                    f"{compliance['caliber_done']}/{compliance['caliber_planned']} Caliber"
                    + (f" ({compliance['coach_cancelled_caliber']} coach-cancelled)" if compliance.get('coach_cancelled_caliber') else "")
                    + f" &middot; {compliance['long_walks_done']}/{compliance['long_walks_planned']} Walks"
                    + (f" ({compliance['coach_cancelled_walks']} coach-cancelled)" if compliance.get('coach_cancelled_walks') else "")
                ),
                badge_colour
            )}
            {card("Total Steps", f"{this_week.get('total_steps',0):,}", f"avg {this_week.get('avg_steps',0):,}/day", "#2e86c1")}
            {card("Weight Change", f'{wc:+.2f} kg' if wc else '&mdash;', "target &minus;0.8 to &minus;1.0 kg", wc_colour)}
        </tr>
        </table>
    </td></tr>

    <!-- AI narrative -->
    <tr><td style="background:#eaf4fb;padding:20px;border-left:4px solid #2e86c1;
                   font-size:14px;line-height:1.7;">
        <p style="margin:0;">{ai_html_styled}</p>
    </td></tr>

    <!-- Data tables -->
    <tr><td style="background:white;padding:20px;">
        {tbl_section("ACTIVITY & COMPLIANCE", steps_rows)}
        {tbl_section("SLEEP & RECOVERY", sleep_rows)}
        {tbl_section("WEIGHT TREND", weight_rows)}
        {tbl_section("NUTRITION vs TARGETS", nut_rows)}
        {tbl_section("STRENGTH PROGRESSION", strength_rows) if strength_rows else ""}
        {build_coach_notes_html(coach_notes or [])}
    </td></tr>

    <!-- Footer -->
    <tr><td style="padding:12px;text-align:center;color:#aaa;font-size:11px;">
        Weekly Report &middot; Local AI Fitness Stack &middot; {datetime.datetime.now().strftime("%d %b %Y %H:%M")}
    </td></tr>

</table>
</body></html>"""
    return html


def run_weekly_brief():
    """Generate and send the weekly training summary."""
    today      = datetime.date.today()
    # Find the most recently completed Monday-Sunday week
    # This works correctly whether triggered on Monday or mid-week via RUN_WEEKLY_NOW
    days_since_sunday = (today.weekday() + 1) % 7  # Sun=0, Mon=1, ..., Sat=6
    last_sunday  = today - datetime.timedelta(days=days_since_sunday)
    week_end     = last_sunday                                          # Sun 25 May
    week_start   = last_sunday - datetime.timedelta(days=6)            # Mon 19 May

    # Extend query end to Monday 07:00 UTC to capture Sunday night sleep
    # (sleep starts ~21:00-22:00 UTC Sunday, written after waking Monday morning)
    week_end_query = last_sunday + datetime.timedelta(days=1)          # Mon 26 May

    # Week before that (comparison period)
    prev_end   = week_start - datetime.timedelta(days=1)               # Sun 18 May
    prev_start = prev_end - datetime.timedelta(days=6)                 # Mon 12 May
    prev_end_query = week_start

    log.info(f"Running weekly summary for week {week_start} (Mon) to {week_end} (Sun)")

    # Calculate week number
    plan_start   = datetime.date(2026, 6, 1)
    days_elapsed = (week_start - plan_start).days
    week_num     = max(1, min(13, (days_elapsed // 7) + 1)) if days_elapsed >= 0 else 0

    log.info(f"Weekly period: {week_start} to {week_end} (query extends to {week_end_query})")
    log.info(f"Previous period: {prev_start} to {prev_end}")
    this_week  = get_weekly_metrics(week_start, week_end_query)
    last_week  = get_weekly_metrics(prev_start, prev_end_query)

    # Fetch coach notes first so calculate_compliance can account for
    # coach-directed cancellations in the score
    coach_notes = get_weekly_coach_notes(week_start, week_end_query)
    log.info(f"  Coach notes retrieved: {len(coach_notes)}")

    compliance = calculate_compliance(this_week, week_num, coach_notes)

    log.info(f"  This week: {this_week.get('total_steps',0):,} steps, "
             f"compliance {compliance['score']}%")

    prompt     = build_weekly_prompt(this_week, last_week, compliance, week_num, week_start, week_end, coach_notes)
    ai_response = call_lm_studio(prompt, WEEKLY_SYSTEM_PROMPT)

    if not ai_response:
        ai_response = ("AI analysis unavailable — Ollama may not be running. "
                      "Your weekly data is shown below.")
        log.warning("Weekly summary: LM Studio unreachable")

    # Build the fixed Python sections (summary, compliance, weight/nutrition)
    # that must not be delegated to Ollama
    python_sections = build_python_sections(this_week, compliance, coach_notes, week_num)

    subject    = f"Weekly Training Report — {week_start.strftime('%d %b')} to {week_end.strftime('%d %b %Y')}"
    html       = format_weekly_html(ai_response, python_sections, this_week, last_week, compliance, week_num, week_start, week_end, coach_notes)
    send_email(subject, html)
    log.info("Weekly summary sent")



# ── Step nudge ────────────────────────────────────────────────────────────────

# Step targets by weekday (Mon=0 ... Sat=5, Sun=6 skipped)
STEP_TARGETS = {0: 5000, 1: 8000, 2: 5000, 3: 8000, 4: 5000, 5: 10000}
NUDGE_THRESHOLD = 2000  # Send nudge if this many steps short of target

def check_step_nudge():
    """Send a step nudge email if significantly short of today's target."""
    today   = datetime.date.today()
    weekday = today.weekday()

    if weekday == 6:  # Skip Sunday
        log.info("Step nudge: Sunday — skipping")
        return

    target = STEP_TARGETS.get(weekday, 8000)

    # Also respect the training plan targets from the week number
    plan_start = datetime.date(2026, 6, 1)
    if today >= plan_start:
        days_elapsed = (today - plan_start).days
        week_num = min(13, (days_elapsed // 7) + 1)
        if weekday == 5:  # Saturday — derived from SAT_PLAN_MINS, single source of truth
            sat_mins = SAT_PLAN_MINS.get(week_num, 110)
            target = int((sat_mins * STEPS_PER_MIN_OUTDOOR + INCIDENTAL_SATURDAY + 499) // 500 * 500)

    # Get today's steps
    d    = today.isoformat()
    dnxt = (today + datetime.timedelta(days=1)).isoformat()
    rows = query_influx("GarminStats",
        f"SELECT totalSteps FROM DailyStats "
        f"WHERE time >= \'{d}T07:00:00Z\' AND time < \'{dnxt}T07:00:00Z\' "
        f"ORDER BY time DESC LIMIT 1")

    if not rows:
        log.info("Step nudge: no step data yet today")
        return

    steps   = int(rows[0].get("totalSteps") or 0)
    deficit = target - steps

    if deficit <= NUDGE_THRESHOLD:
        log.info(f"Step nudge: {steps:,} steps — on track (target {target:,}, deficit {deficit:,})")
        return

    log.info(f"Step nudge: {steps:,}/{target:,} steps — sending nudge (deficit {deficit:,})")

    day_names = ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday"]
    day_name  = day_names[weekday]

    subject = f"👟 Step Nudge — {steps:,} of {target:,} steps today"
    html = f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"></head>
<body style="font-family:Arial,sans-serif;max-width:500px;margin:0 auto;padding:20px;color:#333;">
<table width="100%" cellpadding="0" cellspacing="0" border="0">
<tr><td style="background:#1a5276;padding:20px;border-radius:8px 8px 0 0;">
  <div style="color:white;font-size:20px;font-weight:bold;">👟 Step Nudge</div>
  <div style="color:#aed6f1;font-size:13px;">{day_name} {today.strftime('%d %B %Y')}</div>
</td></tr>
<tr><td style="background:white;padding:24px;border:1px solid #eee;border-top:none;border-radius:0 0 8px 8px;">
  <table width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-bottom:20px;">
    <tr>
      <td align="center" style="background:#fef9e7;border-radius:8px;padding:16px;">
        <div style="font-size:11px;color:#888;text-transform:uppercase;letter-spacing:1px;">Current steps</div>
        <div style="font-size:36px;font-weight:bold;color:#e67e22;">{steps:,}</div>
      </td>
      <td width="20"></td>
      <td align="center" style="background:#eafaf1;border-radius:8px;padding:16px;">
        <div style="font-size:11px;color:#888;text-transform:uppercase;letter-spacing:1px;">Target</div>
        <div style="font-size:36px;font-weight:bold;color:#27ae60;">{target:,}</div>
      </td>
      <td width="20"></td>
      <td align="center" style="background:#fdedec;border-radius:8px;padding:16px;">
        <div style="font-size:11px;color:#888;text-transform:uppercase;letter-spacing:1px;">Still needed</div>
        <div style="font-size:36px;font-weight:bold;color:#c0392b;">{deficit:,}</div>
      </td>
    </tr>
  </table>
  <p style="font-size:14px;color:#555;line-height:1.6;">
    You need <strong>{deficit:,} more steps</strong> to hit today's target.
    At 4.7 km/h that's roughly <strong>{int(deficit/100)} minutes</strong> of walking.
  </p>
  <p style="font-size:12px;color:#aaa;margin-top:16px;">
    29 August 2026 · Peddars Way 100,000 Steps Challenge
  </p>
</td></tr>
</table>
</body></html>"""

    send_email(subject, html)
    log.info("Step nudge email sent")


# ── Garmin sync health check ──────────────────────────────────────────────────
# NOTE: a previous version of this check used the garmin_tokens.json file's
# mtime as "token age". That signal is useless — garmin_direct_sync.py rewrites
# the token file on every successful refresh, so mtime-based age is always
# ~0 days while sync is healthy, and the 25/35-day thresholds could never fire
# in a meaningful way. The only signal that actually indicates "tokens have
# expired / sync is broken" is: has a sync actually happened recently?

TOKEN_PATH         = os.environ.get("TOKEN_PATH", "/app/tokens/garmin_tokens.json")
SYNC_STALE_MINUTES = 60 * 48  # Alert if no successful sync in 48 hours

def check_garmin_token_age():
    """Check whether Garmin sync is healthy, based on time since last
    successful sync (not token file mtime, which is not a reliable signal)."""
    try:
        if not os.path.exists(TOKEN_PATH):
            log.warning(f"Token file not found: {TOKEN_PATH}")
            send_token_alert(
                "CRITICAL — token file missing", None,
                "The Garmin token file cannot be found. Garmin sync will fail until tokens are regenerated."
            )
            return

        last_sync_age = get_last_sync_age_minutes()

        if last_sync_age is None:
            log.warning("Could not determine last sync time — DeviceSync query returned no data")
            send_token_alert(
                "WARNING — sync status unknown", last_sync_age,
                "Could not determine when Garmin last synced (no data in DeviceSync). "
                "If sync is actually working, this may just be a reporting issue — check container logs to confirm."
            )
        elif last_sync_age >= SYNC_STALE_MINUTES:
            log.warning(f"Garmin sync appears stale — last sync {last_sync_age:.0f} min ago")
            send_token_alert(
                "CRITICAL — sync appears stalled", last_sync_age,
                "Garmin has not synced in over 48 hours. Tokens may have expired and need regenerating."
            )
        else:
            log.info(f"Garmin sync healthy — last sync {last_sync_age:.0f} min ago, next check in 7 days")

    except Exception as e:
        log.error(f"Garmin sync health check failed: {e}")


def send_token_alert(status, last_sync_age, message):
    """Send a Garmin sync health warning email."""
    is_critical = "CRITICAL" in status
    colour      = "#c0392b" if is_critical else "#e67e22"
    icon        = "🔴" if is_critical else "🟡"

    last_sync_display = (
        f"{last_sync_age / 60:.1f} hrs" if isinstance(last_sync_age, (int, float))
        else "Unknown"
    )

    subject = f"{icon} Garmin Sync {status}"
    html = f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"></head>
<body style="font-family:Arial,sans-serif;max-width:550px;margin:0 auto;padding:20px;color:#333;">
<table width="100%" cellpadding="0" cellspacing="0" border="0">
<tr><td style="background:{colour};padding:20px;border-radius:8px 8px 0 0;">
  <div style="color:white;font-size:20px;font-weight:bold;">{icon} Garmin Sync Alert</div>
  <div style="color:rgba(255,255,255,0.8);font-size:13px;">{status}</div>
</td></tr>
<tr><td style="background:white;padding:24px;border:1px solid #eee;border-top:none;border-radius:0 0 8px 8px;">
  <table width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-bottom:20px;">
    <tr>
      <td style="padding:10px;background:#f8f9fa;border-radius:8px;text-align:center;">
        <div style="font-size:11px;color:#888;text-transform:uppercase;">Last sync</div>
        <div style="font-size:28px;font-weight:bold;color:{colour};">{last_sync_display}</div>
      </td>
    </tr>
  </table>

  <p style="font-size:14px;line-height:1.7;margin-bottom:16px;">
    {message}
  </p>

  <div style="background:#fef9e7;border-left:4px solid #f39c12;padding:14px;border-radius:4px;margin-bottom:16px;">
    <strong style="font-size:13px;">How to renew tokens (if regeneration is needed):</strong>
    <ol style="font-size:13px;margin-top:8px;margin-bottom:0;padding-left:20px;line-height:2;">
      <li>On Max: run <code style="background:#eee;padding:1px 5px;border-radius:3px;">python C:\AdaptiveTraining\scripts\garmin_auth.py</code></li>
      <li>Enter your Garmin email and password when prompted</li>
      <li>Copy <code style="background:#eee;padding:1px 5px;border-radius:3px;">garmin_tokens\garmin_tokens.json</code> to <code style="background:#eee;padding:1px 5px;border-radius:3px;">\\nas\Container\garmin-direct-sync\tokens\</code></li>
      <li>Restart the <strong>garmin-direct-sync</strong> container in Container Station</li>
      <li>Verify sync resumes in the container logs</li>
    </ol>
  </div>

  <p style="font-size:12px;color:#aaa;">
    This check runs every Monday morning. It alerts if no successful Garmin sync
    has occurred in the last {SYNC_STALE_MINUTES // 60} hours, or if sync status cannot be determined.
  </p>
</td></tr>
</table>
</body></html>"""

    send_email(subject, html)
    log.info(f"Sync health alert sent: {status}")


# ── Container health check ────────────────────────────────────────────────────

EXPECTED_CONTAINERS = [
    "influxdb",
    "grafana",
    "garmin-direct-sync",
    "cronometer-sync",
    "daily-brief",
    "open-webui",
    "training-dashboard",
]

_last_container_alert = {}  # Track when we last alerted per container

def check_container_health():
    """Check all expected containers are running via Docker API."""
    import subprocess
    try:
        result = subprocess.run(
            ["docker", "ps", "--format", "{{.Names}}	{{.Status}}"],
            capture_output=True, text=True, timeout=15
        )
        running = {}
        for line in result.stdout.strip().splitlines():
            if "	" in line:
                name, status = line.split("	", 1)
                running[name.strip()] = status.strip()

        problems = []
        for container in EXPECTED_CONTAINERS:
            if container == "daily-brief":
                continue  # Skip self
            if container not in running:
                problems.append((container, "NOT RUNNING"))
            elif "unhealthy" in running[container].lower():
                problems.append((container, f"UNHEALTHY: {running[container]}"))

        if not problems:
            log.debug("Container health: all OK")
            return

        # Only alert once per hour per container
        now = datetime.datetime.now()
        new_problems = []
        for container, status in problems:
            last_alert = _last_container_alert.get(container)
            if not last_alert or (now - last_alert).seconds > 3600:
                new_problems.append((container, status))
                _last_container_alert[container] = now

        if not new_problems:
            return

        log.warning(f"Container health issues: {new_problems}")
        send_container_alert(new_problems)

    except Exception as e:
        log.error(f"Container health check error: {e}")


def send_container_alert(problems):
    """Send container health alert email."""
    subject = f"🔴 Container Alert — {len(problems)} container(s) down"

    rows = ""
    for container, status in problems:
        rows += f"""<tr>
            <td style="padding:8px 12px;font-weight:bold;border-bottom:1px solid #eee;">{container}</td>
            <td style="padding:8px 12px;color:#c0392b;border-bottom:1px solid #eee;">{status}</td>
        </tr>"""

    html = f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"></head>
<body style="font-family:Arial,sans-serif;max-width:500px;margin:0 auto;padding:20px;color:#333;">
<table width="100%" cellpadding="0" cellspacing="0" border="0">
<tr><td style="background:#c0392b;padding:20px;border-radius:8px 8px 0 0;">
  <div style="color:white;font-size:20px;font-weight:bold;">🔴 Container Health Alert</div>
  <div style="color:rgba(255,255,255,0.8);font-size:13px;">{datetime.datetime.now().strftime('%d %b %Y %H:%M UTC')}</div>
</td></tr>
<tr><td style="background:white;padding:24px;border:1px solid #eee;border-top:none;border-radius:0 0 8px 8px;">
  <p style="font-size:14px;margin-bottom:16px;">The following containers are not running correctly:</p>
  <table width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#fafafa;border-radius:8px;overflow:hidden;margin-bottom:16px;">
    <tr style="background:#f0f0f0;">
      <th style="padding:8px 12px;text-align:left;font-size:12px;color:#888;">Container</th>
      <th style="padding:8px 12px;text-align:left;font-size:12px;color:#888;">Status</th>
    </tr>
    {rows}
  </table>
  <div style="background:#fef9e7;border-left:4px solid #f39c12;padding:12px;border-radius:4px;font-size:13px;">
    <strong>To restart a container:</strong><br>
    Open Container Station on the NAS → find the application → click Restart.<br>
    Or from SSH: <code style="background:#eee;padding:1px 5px;border-radius:3px;">docker restart &lt;container-name&gt;</code>
  </div>
</td></tr>
</table>
</body></html>"""

    send_email(subject, html)
    log.warning(f"Container alert sent for: {[p[0] for p in problems]}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    log.info("=" * 60)
    log.info("Daily Brief Scheduler Starting")
    log.info(f"Will send at {SEND_HOUR:02d}:{SEND_MINUTE:02d} every morning")
    log.info("=" * 60)

    # Run immediately on first start (useful for testing)
    if os.environ.get("RUN_NOW", "").lower() == "true":
        log.info("RUN_NOW=true — running daily brief immediately")
        run_daily_brief()

    if os.environ.get("RUN_WEEKLY_NOW", "").lower() == "true":
        log.info("RUN_WEEKLY_NOW=true — running weekly summary immediately")
        run_weekly_brief()

    if os.environ.get("RUN_NUDGE_NOW", "").lower() == "true":
        log.info("RUN_NUDGE_NOW=true — running step nudge check immediately")
        check_step_nudge()

    while True:
        now  = datetime.datetime.now()
        # Step nudge — 18:00 UTC (19:00 BST) Mon-Sat only
        if now.weekday() < 6 and now.hour == 18 and now.minute < 5:
            try:
                log.info("18:00 UTC — checking step nudge")
                check_step_nudge()
            except Exception as e:
                log.error(f"Step nudge failed: {e}")
            time.sleep(300)
            continue

        # Weekly token age check — Monday 06:30 UTC (before weekly brief at 07:00)
        if now.weekday() == 0 and now.hour == 6 and now.minute >= 30 and now.minute < 35:
            try:
                log.info("Monday 09:00 UTC — checking Garmin token age")
                check_garmin_token_age()
            except Exception as e:
                log.error(f"Token age check failed: {e}")
            time.sleep(300)
            continue

        # Container health check — every hour at :30
        if now.minute == 30:
            try:
                check_container_health()
            except Exception as e:
                log.error(f"Container health check failed: {e}")

        secs = seconds_until(SEND_HOUR, SEND_MINUTE)
        wake = datetime.datetime.now() + datetime.timedelta(seconds=secs)
        log.info(f"Next daily brief at {wake.strftime('%Y-%m-%d %H:%M')} "
                 f"(sleeping {int(secs/3600)}h {int((secs%3600)/60)}m)")
        time.sleep(secs)

        # Re-capture now after sleep so weekday check reflects the actual send day
        now = datetime.datetime.now()

        try:
            # Wait for fresh Garmin data before generating brief
            # Timeout at 08:00 UTC (09:00 BST) — retries every 10 min
            fresh = wait_for_fresh_data(
                timeout_hour=8,
                timeout_minute=0,
                freshness_minutes=60,
                retry_minutes=10,
            )
            if fresh:
                run_daily_brief()
                # Run weekly brief on Mondays immediately after daily brief
                if now.weekday() == 0:
                    log.info("Monday — running weekly summary after daily brief")
                    try:
                        run_weekly_brief()
                    except Exception as e:
                        log.error(f"Weekly summary failed: {e}")
            else:
                send_stale_data_warning()
        except Exception as e:
            log.error(f"Daily brief failed: {e}")

        # Sleep 60s to avoid double-firing
        time.sleep(60)


if __name__ == "__main__":
    main()