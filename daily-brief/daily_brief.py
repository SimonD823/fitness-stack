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

LM_STUDIO_URL = os.environ.get("LM_STUDIO_URL", "http://192.168.1.50:1234")
LM_MODEL      = os.environ.get("LM_MODEL", "qwen/qwen3.6-27b")

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

def get_todays_session():
    """Extract today's scheduled treadmill session from the training guide."""
    today = datetime.date.today()
    weekday = today.weekday()  # 0=Mon, 1=Tue, 4=Fri

    # New 6-day structure from 1 June 2026:
    # Mon/Wed/Fri = Caliber + short walk (~5,000 steps)
    # Tue/Thu/Sat = Long walk (70-90+ min)
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
        return session_label, None

    try:
        with open(TREADMILL_GUIDE, 'r') as f:
            guide = f.read()

        # Find current week section
        pattern = rf'###\s+\*?\*?Week {TRAINING_WEEK}[:\s]'
        match = re.search(pattern, guide, re.IGNORECASE)
        if not match:
            log.warning(f"Week {TRAINING_WEEK} not found in treadmill guide")
            return session_label, None

        start = match.start()
        next_week = re.search(r'###\s+\*?\*?Week \d+', guide[start+10:])
        end = start + 10 + next_week.start() if next_week else len(guide)
        week_text = guide[start:end]

        # Extract the specific session
        session_pattern = rf'\*\*Session {session_label[0]}.*?(?=\*\*Session [ABC]|---\s*$|\Z)'
        session_match = re.search(session_pattern, week_text, re.DOTALL)
        if session_match:
            return session_label, session_match.group(0).strip()

        return session_label, week_text.strip()

    except Exception as e:
        log.warning(f"Could not load treadmill session: {e}")
        return session_label, None

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
        metrics["battery_change"]   = r.get("bodyBatteryChange", 0)

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
        f"elapsedDuration, activityTrainingLoad "
        f"FROM ActivitySummary WHERE time >= '{window_start}' "
        f"AND time < '{window_end}' ORDER BY time ASC"
    )
    activities = []
    for r in rows:
        if r.get("activityType") == "No Activity":
            continue
        activities.append({
            "name":          r.get("activityName", "Unknown"),
            "type":          r.get("activityType", ""),
            "calories":      int(r.get("calories") or 0),
            "avg_hr":        int(r.get("averageHR") or 0),
            "duration_mins": round((r.get("elapsedDuration") or 0) / 60, 0),
            "training_load": round(r.get("activityTrainingLoad") or 0, 1),
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
            "duration_mins":  round((r.get("durationSeconds") or 0) / 60, 0),
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

    return metrics


# ── LM Studio API ─────────────────────────────────────────────────────────────

def call_lm_studio(prompt, system_prompt):
    url = f"{LM_STUDIO_URL}/v1/chat/completions"
    payload = {
        "model": LM_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.7,
        "max_tokens": 1500,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    try:
        resp = requests.post(url, json=payload, timeout=300)
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]
    except Exception as e:
        log.error(f"LM Studio call failed: {e}")
        return None


def build_prompt(metrics, date_str):
    """Build the data context prompt for the AI."""
    m = metrics
    today = datetime.date.today()
    weeks_to_event = (datetime.date(2026, 8, 29) - today).days // 7

    lines = [
        f"DATE: {date_str}",
        f"TRAINING WEEK: {TRAINING_WEEK} of 14 ({weeks_to_event} weeks until {EVENT_NAME} on {EVENT_DATE})",
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
        f"- Body battery change during sleep: {m.get('battery_change', 'N/A')}",
        f"- Body battery range yesterday: {m.get('body_battery_low', 'N/A')} - {m.get('body_battery_high', 'N/A')}",
        f"- High stress duration: {m.get('high_stress_mins', 0)} mins",
    ]

    if m.get("weight_kg"):
        lines.append(f"- Weight: {m['weight_kg']} kg")

    if m.get("activities"):
        lines.append("")
        lines.append("ACTIVITIES:")
        for a in m["activities"]:
            lines.append(
                f"- {a['name']} ({a['type']}): {a['duration_mins']} mins, "
                f"avg HR {a['avg_hr']} bpm, {a['calories']} kcal, "
                f"training load {a['training_load']}"
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
        for ex, data in m["caliber_sets"].items():
            lines.append(
                f"- {ex}: {data['sets']} sets, {data['total_reps']} total reps, "
                f"max {data['max_weight']} kg, volume {round(data['volume'], 1)} kg"
            )

    if m.get("calories_in"):
        lines.append("")
        lines.append("NUTRITION (Cronometer):")
        lines.append(f"- Energy: {m['calories_in']} kcal")
        lines.append(f"- Protein: {m['protein_g']}g | Fat: {m['fat_g']}g | Net carbs: {m['net_carbs_g']}g")

    # Add today's session context
    session_label, session_details = get_todays_session()
    if session_label:
        lines.append("")
        lines.append(f"TODAY'S SCHEDULED SESSION: {session_label}")
        if session_details:
            lines.append(session_details)
        lines.append("")
        lines.append("Adapt based on yesterday's recovery data:")
        lines.append("- Great recovery (HRV at/above baseline, sleep 70+, body battery 60+): train as planned or push upper range")
        lines.append("- Normal recovery: train as planned")
        lines.append("- Reduced recovery (HRV suppressed 5-10%, sleep 50-69, body battery 40-59): reduce walk duration 10-15%, Caliber 2 sets if strength day")
        lines.append("- Poor recovery (HRV >10% below baseline, sleep <50, body battery <40): walk only 30 min easy, no Caliber")
        lines.append("Give specific adapted targets: duration in minutes, incline %, speed km/h if treadmill.")

    lines.append("")
    lines.append("Please provide:")
    lines.append("1. A brief recovery and readiness assessment for today (2-3 sentences)")
    lines.append("2. Today's specific training plan with clear adapted targets (exact numbers)")
    lines.append("3. One key focus or tip for today")
    lines.append("Keep the total response concise and actionable — this is a morning email.")

    return "\n".join(lines)


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
- 14-week training plan, started Monday 19 May 2026
- Strength training: Caliber app sessions (Mon/Tue/Fri structure)
- NO upper body strength work until 1 June 2026 (post-lipoma surgery recovery — cleared 1 June)
- Treadmill walking for cardio base building
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
- Carbohydrates: 149 g baseline — PRIMARY LEVER, increase on training days
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

COMMUNICATION STYLE:
- Direct and specific — use the numbers provided
- No generic advice when data is available
- Always reference the 29 August challenge as the primary training target
- Format responses with clear sections: READINESS, TODAY'S PLAN, KEY FOCUS
- Keep responses concise and actionable — this is a morning coaching brief, not a report"""


# ── Email sending ─────────────────────────────────────────────────────────────

def format_html_email(ai_response, metrics, date_str):
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

    # Style READINESS / TODAY'S PLAN / KEY FOCUS headings
    for heading in ["READINESS", "TODAY'S PLAN", "KEY FOCUS", "NUTRITION CHECK"]:
        clean = clean.replace(heading,
            f'</p><p style="margin:10px 0 2px;color:#1F497D;font-size:12px;font-weight:bold;'
            f'text-transform:uppercase;letter-spacing:1px;border-bottom:1px solid #2E75B5;'
            f'padding-bottom:2px;">{heading}</p><p style="margin:4px 0;">')

    ai_html = clean.replace("\n\n", "</p><p>").replace("\n", "<br>")
    ai_html = f"<p style='margin:0;'>{ai_html}</p>"

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
    act_rows += stat_row("Distance", m.get("distance_km", 0), "km")
    act_rows += stat_row("Active calories", m.get("active_calories", 0), "kcal")
    act_rows += stat_row("Resting HR", m.get("resting_hr", "—"), "bpm")
    act_rows += stat_row("Body battery", f"{m.get('body_battery_low','—')} → {m.get('body_battery_high','—')}")
    act_rows += stat_row("High stress", m.get("high_stress_mins", 0), "mins")
    if m.get("weight_kg"):
        act_rows += stat_row("Weight", m.get("weight_kg"), "kg")

    # Nutrition rows
    nut_rows = ""
    if m.get("calories_in"):
        nut_rows += stat_row("Calories", m.get("calories_in"), "kcal")
        nut_rows += stat_row("Protein", m.get("protein_g"), "g",
                              red=float(m.get("protein_g") or 0) < 140)
        nut_rows += stat_row("Fat", m.get("fat_g"), "g")
        nut_rows += stat_row("Net carbs", m.get("net_carbs_g"), "g")

    # Sessions
    session_rows = ""
    if m.get("activities"):
        for a in m["activities"]:
            session_rows += (
                f'<tr><td style="padding:6px 10px;border-bottom:1px solid #eee;">'
                f'<strong style="font-size:13px;">{a["name"]}</strong>'
                f'<span style="color:#888;font-size:12px;margin-left:8px;">{a["duration_mins"]} mins</span>'
                f'<span style="color:#555;font-size:12px;margin-left:8px;">'
                f'Avg HR {a["avg_hr"]} bpm &nbsp;&middot;&nbsp; {a["calories"]} kcal'
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
<html><head><meta charset="UTF-8"></head>
<body style="font-family:Arial,sans-serif;margin:0;padding:0;color:#333;">
<table width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#f4f6f8;">
<tr><td align="center" style="padding:20px;">
<table width="620" cellpadding="0" cellspacing="0" border="0">

    <tr><td style="background:#1F497D;padding:20px;">
        <div style="color:white;font-size:20px;font-weight:bold;margin-bottom:4px;">Daily Training Brief</div>
        <div style="color:#aed6f1;font-size:13px;">
            {date_str} &nbsp;&middot;&nbsp; Week {TRAINING_WEEK} of 13
            &nbsp;&middot;&nbsp; {weeks_to_event} weeks to Peddars Way
        </div>
    </td></tr>

    <tr><td style="background:#eaf4fb;padding:18px;border-left:4px solid #2E75B5;font-size:14px;line-height:1.7;">
        {ai_html}
    </td></tr>

    <tr><td style="background:white;padding:20px;">
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
<html><head><meta charset="UTF-8"></head>
<body style="font-family:Arial,sans-serif;margin:0;padding:0;">
<table width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#f4f6f8;">
<tr><td align="center" style="padding:20px;">
<table width="620" cellpadding="0" cellspacing="0" border="0">

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
    yesterday = (datetime.date.today() - datetime.timedelta(days=1)).strftime("%A %d %B %Y")
    today_str = datetime.date.today().strftime("%A %d %B %Y")
    log.info(f"Running daily brief for {today_str}")

    age = get_last_sync_age_minutes()
    if age:
        log.info(f"Garmin data freshness: {age:.1f} minutes since last sync")

    # Fetch data
    log.info("Fetching metrics from InfluxDB...")
    metrics = get_yesterday_metrics()
    log.info(f"Got metrics: steps={metrics.get('steps')}, sleep={metrics.get('sleep_hours')}h, "
             f"HRV={metrics.get('overnight_hrv')}ms")

    # Call AI
    log.info("Calling LM Studio for AI analysis...")
    prompt = build_prompt(metrics, today_str)
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

    # Build and send email
    subject = f"Training Brief — {today_str}"
    html = format_html_email(ai_response, metrics, today_str)
    send_email(subject, html)
    log.info("Daily brief complete")



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

    return m


def calculate_compliance(metrics, week_num):
    """Calculate training compliance for the week."""
    caliber_done = len(metrics.get("caliber_sessions", []))
    walks_done   = len([w for w in metrics.get("walk_sessions", []) if w["duration"] >= 60])
    short_walks  = len([w for w in metrics.get("walk_sessions", []) if 30 <= w["duration"] < 60])

    # Phase 1 weeks 1-4: 3 Caliber + 3 long walks + 3 short walks
    # All weeks have same structure
    if week_num <= 0:
        # Pre-plan
        planned_caliber = 0
        planned_long    = 6
    else:
        planned_caliber = 3
        planned_long    = 3

    total_planned   = planned_caliber + planned_long
    total_completed = min(caliber_done, planned_caliber) + min(walks_done, planned_long)
    score = round((total_completed / total_planned * 100) if total_planned else 0, 0)

    return {
        "caliber_planned":   planned_caliber,
        "caliber_done":      caliber_done,
        "long_walks_planned": planned_long,
        "long_walks_done":   walks_done,
        "short_walks_done":  short_walks,
        "score":             score,
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


def build_weekly_prompt(this_week, last_week, compliance, week_num):
    """Build the weekly summary prompt for the AI."""
    today       = datetime.date.today()
    event_date  = datetime.date(2026, 8, 29)
    weeks_left  = (event_date - today).days // 7
    next_week   = week_num + 1
    phase       = get_phase(week_num)
    next_phase  = get_phase(next_week)

    lines = [
        f"WEEKLY TRAINING SUMMARY — {week_start.strftime('%d %b')} to {week_end.strftime('%d %b %Y')},",
        f"Phase: {phase}",
        f"Weeks until 100,000 Steps Challenge (29 Aug 2026): {weeks_left}",
        "",
        "THIS WEEK vs LAST WEEK:",
        "",
        "STEPS & ACTIVITY:",
        f"- Total steps this week: {this_week.get('total_steps', 0):,} | Last week: {last_week.get('total_steps', 0):,}",
        f"- Average daily steps: {this_week.get('avg_steps', 0):,} | Last week: {last_week.get('avg_steps', 0):,}",
        f"- Caliber sessions: {compliance['caliber_done']} of {compliance['caliber_planned']} planned",
        f"- Long walks (60+ min): {compliance['long_walks_done']} of {compliance['long_walks_planned']} planned",
        f"- Short walks on gym days: {compliance['short_walks_done']}",
        f"- Training compliance score: {compliance['score']}%",
        "",
        "SLEEP & RECOVERY:",
        f"- Average sleep: {this_week.get('avg_sleep_hrs', 0)}h | Last week: {last_week.get('avg_sleep_hrs', 0)}h",
        f"- Average sleep score: {this_week.get('avg_sleep_score', 0)}/100 | Last week: {last_week.get('avg_sleep_score', 0)}/100",
        f"- Average overnight HRV: {this_week.get('avg_hrv', 0)}ms | Last week: {last_week.get('avg_hrv', 0)}ms",
        f"- Average resting HR: {this_week.get('avg_rhr', 0)} bpm | Last week: {last_week.get('avg_rhr', 0)} bpm",
        "",
        "WEIGHT TREND:",
    ]

    ws = this_week.get("weight_start", 0)
    we = this_week.get("weight_end", 0)
    wc = this_week.get("weight_change", 0)
    if ws and we:
        target = "on track" if -1.0 <= wc <= -0.8 else ("above target loss" if wc < -1.0 else "below target loss")
        lines.append(f"- Start of week: {ws} kg | End of week: {we} kg")
        lines.append(f"- Change: {wc:+.2f} kg (target: -0.8 to -1.0 kg/week — {target})")
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
        lines.append("STRENGTH PROGRESSION (max weight per exercise this week vs last week):")
        last_strength = last_week.get("strength_summary", {})
        for ex, data in this_week["strength_summary"].items():
            last_max = last_strength.get(ex, {}).get("max_weight", 0)
            if last_max:
                change = data["max_weight"] - last_max
                trend  = f"+{change:.1f} kg PR!" if change > 0 else (f"{change:.1f} kg" if change < 0 else "held")
            else:
                trend = "no prior data"
            lines.append(f"- {ex}: {data['max_weight']} kg ({trend})")

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
    lines.append("Please provide a concise weekly report with these sections:")
    lines.append("WEEK SUMMARY — 2-3 sentences on overall performance")
    lines.append("HIGHLIGHTS — what went well this week")
    lines.append("AREAS TO ADDRESS — what needs attention next week")
    lines.append("STRENGTH REPORT — progression, PRs, any concerns")
    lines.append("WEIGHT & NUTRITION — trend assessment vs targets")
    lines.append("COMPLIANCE — honest assessment of training adherence")
    lines.append("COMING WEEK — specific focus and any adjustments to the plan")
    lines.append("Use plain text only — no markdown, no asterisks.")

    return "\n".join(lines)


WEEKLY_SYSTEM_PROMPT = """You are an expert personal fitness and nutrition coach providing a weekly performance review for Simon Davies, 56, male, 115 kg, preparing for the 100,000 Steps Challenge on 29 August 2026. He had gastric sleeve surgery 14 March 2025. His 13-week training plan started 1 June 2026.

Weekly training structure: Mon/Wed/Fri = Caliber gym sessions + short walk. Tue/Thu/Sat = long Zone 2 walks. Sunday = rest.
Zone 2: 98-115 bpm. Weight loss target: 0.8-1.0 kg/week. Protein target: 150g/day (never below 140g).

Be direct and data-driven. Celebrate genuine improvements. Flag real concerns clearly. Keep each section concise. Use plain text only."""


def format_weekly_html(ai_response, this_week, last_week, compliance, week_num):
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

    # Sleep
    sleep_rows  = stat_row("Avg sleep", this_week.get('avg_sleep_hrs',0), last_week.get('avg_sleep_hrs',0), "hrs")
    sleep_rows += stat_row("Avg sleep score", this_week.get('avg_sleep_score',0), last_week.get('avg_sleep_score',0), "/100")
    sleep_rows += stat_row("Avg overnight HRV", this_week.get('avg_hrv',0), last_week.get('avg_hrv',0), "ms")
    sleep_rows += stat_row("Avg resting HR", this_week.get('avg_rhr',0), last_week.get('avg_rhr',0), "bpm")

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

    # Strength
    strength_html = ""
    if this_week.get("strength_summary"):
        last_s = last_week.get("strength_summary", {})
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

    ai_html_styled = style_sections(ai_html)

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
<html><head><meta charset="UTF-8"></head>
<body style="font-family:Arial,sans-serif;margin:0;padding:0;color:#333;">
<table width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#f4f6f8;">
<tr><td align="center" style="padding:20px;">
<table width="660" cellpadding="0" cellspacing="0" border="0">

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
            {card("Compliance", f"{int(score)}%", f"{compliance['caliber_done']}/3 Caliber &middot; {compliance['long_walks_done']}/3 Walks", badge_colour)}
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
    </td></tr>

    <!-- Footer -->
    <tr><td style="padding:12px;text-align:center;color:#aaa;font-size:11px;">
        Weekly Report &middot; Local AI Fitness Stack &middot; {datetime.datetime.now().strftime("%d %b %Y %H:%M")}
    </td></tr>

</table>
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
    compliance = calculate_compliance(this_week, week_num)

    log.info(f"  This week: {this_week.get('total_steps',0):,} steps, "
             f"compliance {compliance['score']}%")

    prompt     = build_weekly_prompt(this_week, last_week, compliance, week_num)
    ai_response = call_lm_studio(prompt, WEEKLY_SYSTEM_PROMPT)

    if not ai_response:
        ai_response = ("AI analysis unavailable — LM Studio may not be running. "
                      "Your weekly data is shown below.")
        log.warning("Weekly summary: LM Studio unreachable")

    subject    = f"Weekly Training Report — {week_start.strftime('%d %b')} to {week_end.strftime('%d %b %Y')}"
    html       = format_weekly_html(ai_response, this_week, last_week, compliance, week_num)
    send_email(subject, html)
    log.info("Weekly summary sent")


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

    while True:
        now  = datetime.datetime.now()
        # Check if it's Monday 10:00 UTC for weekly summary
        # Running at 10:00 UTC (11:00 BST) ensures Sunday night sleep data
        # has had time to sync from Garmin Connect before the report fires
        if now.weekday() == 0 and now.hour == 10 and now.minute < 5:
            try:
                log.info("Monday 10:00 UTC — running weekly summary")
                run_weekly_brief()
            except Exception as e:
                log.error(f"Weekly summary failed: {e}")
            time.sleep(300)  # Sleep 5 min to avoid re-firing
            continue

        secs = seconds_until(SEND_HOUR, SEND_MINUTE)
        wake = datetime.datetime.now() + datetime.timedelta(seconds=secs)
        log.info(f"Next daily brief at {wake.strftime('%Y-%m-%d %H:%M')} "
                 f"(sleeping {int(secs/3600)}h {int((secs%3600)/60)}m)")
        time.sleep(secs)

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
            else:
                send_stale_data_warning()
        except Exception as e:
            log.error(f"Daily brief failed: {e}")

        # Sleep 60s to avoid double-firing
        time.sleep(60)


if __name__ == "__main__":
    main()
