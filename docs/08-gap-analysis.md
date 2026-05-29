# Guide 08 — Gap Analysis: What Remains?

This guide documents remaining gaps, known issues, and enhancement opportunities as of May 2026.

---

## Status: What Is Working

| Component | Status | Notes |
|-----------|--------|-------|
| Garmin sync (all metrics) | ✅ Operational | Every 30 min, 30 days backfill done |
| HRV sync | ✅ Fixed | sync_hrv runs after sync_sleep to prevent overwrite |
| Sleep data | ✅ Fixed | Query extended to `now-2d` noon to catch BST evening sleep starts |
| Strength sets with correct names | ✅ Operational | Workout plan name mapping via associatedWorkoutId |
| Cronometer sync | ✅ Operational | 06:00 AM daily |
| Daily brief email | ✅ Operational | 06:45 AM, Brevo API, Qwen3.6-27B |
| Treadmill session adaptation | ✅ Operational | Weekly session from guide, adapted by HRV/sleep/body battery |
| Planned vs actual strength | ✅ Operational | March 2026 Caliber plan embedded in system prompt |
| Markdown stripping | ✅ Fixed | AI response cleaned before HTML formatting |
| Week auto-calculation | ✅ Operational | Calculated from plan start date (19 May 2026), no manual update |

---

## Priority 1 — Enhancements Worth Building

### Caliber MCP Integration (Optional — not required)

**Status: Not required — Garmin workout plan mapping is the solution**

The Garmin route fully covers the daily coaching brief:
- Exercise names match Caliber plan exactly (via Garmin workout plan descriptions)
- Reps and weights captured accurately from Fenix 8
- Planned vs actual comparison works via system prompt
- Warm-up set filtering works automatically

**What Caliber MCP would add (nice to have, not essential):**
- Workout ratings (you rated sessions 4-7 in Caliber)
- Notes added in the Caliber app
- Sessions logged in Caliber but not recorded on the Fenix 8

A support email has been sent to Caliber requesting redirect URI registration. If they respond, follow `Caliber_MCP_Integration_Guide.md`. If not, the stack works perfectly without it.

**Requirement:** Always record strength sessions on the Fenix 8 using the `(Gym) Back & Shoulders`, `(Gym) Chest & Arms`, or `(Gym) Legs & Abs` workout plan templates — this is what triggers the exercise name mapping.

---

## Priority 2 — Enhancements Worth Building

### 2.1 Weekly AI Coaching Report

**The gap:** The daily brief covers yesterday's data. There is no automated weekly summary of the 7-day training block, nutrition compliance, and HRV trend.

**Fix:** A second daily-brief variant running every Sunday at 07:00 querying `now() - 7d` and emailing a weekly coaching summary. Same script pattern, different query window and send time.

---

### 2.2 Grafana Strength Panels

**The gap:** StrengthSets data is in InfluxDB with correct exercise names but no Grafana panels display it.

**Panels to add:**
- Weekly volume per exercise (kg total)
- Max weight per exercise over time (progression chart)
- Session frequency (calendar heatmap)
- Per-session set/rep summary table

---

### 2.3 InfluxDB Backup

**The gap:** No backup strategy for InfluxDB data.

**Fix:** Weekly backup task on NAS Task Scheduler (Sunday 03:00):

```bash
docker exec influxdb influxd backup -portable -database GarminStats \
  /var/lib/influxdb/backup/garmin_$(date +%Y%m%d)

docker exec influxdb influxd backup -portable -database CronometerStats \
  /var/lib/influxdb/backup/crono_$(date +%Y%m%d)
```

Mount `/var/lib/influxdb/backup` to a NAS share outside the container volume.

---

### 2.4 HR Zone Calibration

**The gap:** Zone 2 is set at 98-115 bpm based on age (56, max HR 164 bpm). This is a reasonable default but individual zones vary.

**Fix:** In Week 3-4, walk/jog on the treadmill for 20 minutes at a conversational pace. Average HR in the last 10 minutes ≈ Zone 2 ceiling. Update the system prompt with the actual value.

---

### 2.5 Bariatric Care Team

**Not a stack issue but high priority:** At 14+ months post-sleeve, undertaking a 14-week ultra-endurance training programme at a calorie deficit warrants input from your bariatric dietitian before the Build phase (Week 5+). Key items: protein target adequacy, supplement protocol, blood work (B12, iron, ferritin, vitamin D).

---

## Priority 3 — Known Minor Issues

### 3.1 Ollama Auto-Start

Ollama is configured to start on Windows boot. Windows updates can occasionally prevent the model from auto-loading. If the daily brief shows "AI analysis unavailable", open Ollama on Max and load Qwen3.6-27B manually. Consider using Windows Task Scheduler with a 2-minute delay instead of the Startup folder for more reliable auto-start.

### 3.2 Warm-Up Set Detection

The AI system prompt instructs the model to ignore sets below 75% of session max weight as warm-ups. This heuristic works for most exercises but may occasionally misclassify a genuine working drop set. If the AI coaching commentary seems off on strength days, check the raw StrengthSets data.

### 3.3 Post-Challenge Plan

After 29 August the training plan ends at Week 14. The daily brief will continue sending but the treadmill session section will show no match. Update `TREADMILL_TRAINING_GUIDE.md` with a new plan and the system prompt with a new Caliber programme when a new training goal is set.

---

## Summary Table

| Gap | Priority | Effort |
|-----|----------|--------|
| Caliber MCP integration | Low — optional | Waiting on Caliber support if ever needed |
| Weekly AI coaching report | Medium | 2 hours |
| Grafana strength panels | Medium | 1 hour |
| InfluxDB backup | Medium | 20 min |
| HR zone calibration | Medium | 1 hour field test |
| Bariatric dietitian consult | High (personal) | Appointment |
| Ollama auto-start robustness | Low | 15 min |
