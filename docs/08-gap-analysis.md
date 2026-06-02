# Guide 08 — Status & Gap Analysis

Current status of all components as of May 2026, plus remaining items before the event.

---

## What Is Fully Working

| Component | Status | Notes |
|-----------|--------|-------|
| Garmin sync — all metrics | ✅ | Every 30 min, 07:00-07:00 UTC query window |
| HRV sync | ✅ | Runs after sleep sync to prevent field overwrite |
| Sleep data | ✅ | Correct timezone handling for BST |
| Body battery change | ✅ | Calculated as high-low, matches watch display |
| Strength sets with Caliber names | ✅ | Workout plan name mapping via associatedWorkoutId |
| Cronometer nutrition sync | ✅ | 01:00 AM daily |
| Daily coaching email | ✅ | 08:00 AM, Ollama/Qwen3.6-27B, data freshness check |
| Weekly training report | ✅ | Monday 07:00 AM UTC, Mon-Sun data, freshness check |
| Step nudge email | ✅ | 18:00 UTC Mon-Sat, skips Sunday |
| Garmin token age check | ✅ | Monday 06:30 UTC, warns at 25d, critical at 35d |
| Container health monitoring | ✅ | Hourly, email alert if any container down |
| Ollama auto-start | ✅ | Task Scheduler logon trigger, Windows auto-login |
| Open WebUI | ✅ | NAS container :3001, fitness-coach model default |
| Tailscale remote access | ✅ | NAS Tailscale node, access from anywhere |
| Training dashboard | ✅ | NAS container :3002, planned vs actual, click-to-expand |
| InfluxDB backup | ✅ | Weekly Sunday 03:00, /share/backup/influxdb/, 4 weeks |
| Grafana strength panels | ✅ | Scripted via API, 5 panel types |
| Caliber sync | ✅ | Via Anthropic API (Haiku 4.5) + Caliber MCP, 01:00 AM |

---

## Remaining Items — Complete Before August

### High Priority

**Event Day Dashboard** — target early August
A stripped-back Grafana view showing real-time steps, HR, pace, and body battery for 29 August. Something to glance at on a phone during the challenge. Should auto-refresh every 60 seconds.

**Pre-Event Brief** — target mid-August
A special one-off daily brief for 28 August covering:
- Event-day pacing strategy (steps/hour targets per phase)
- Gel timing schedule (half gel every 45-60 min from km 10)
- Tailwind mixing ratios for the bladder
- A readiness summary based on that morning's HRV/sleep/body battery
- Weather check for the route

**Post-Event Recovery Plan** — target August
Update `TRAINING_PLAN_V2.md` and the system prompt with a 2-week recovery protocol after the event. The daily brief will continue firing after 29 August.

### Medium Priority

**HRV Baseline Recalibration** — target late June (after 4 weeks of training data)
After 4 weeks of consistent Zone 2 training (from 1 June), calculate a personal HRV baseline from the rolling 7-day average. Update the system prompt with the actual value rather than the age-based estimate.

**Caliber Sync Improvement** — ongoing
The current Caliber sync uses the Anthropic API (Haiku 4.5) with MCP. Monitor API costs monthly. Consider adding session ratings to the InfluxDB data and exposing them in the weekly report.

---

## Known Issues / Limitations

### Grafana Strength Panels — Sparse Data
The individual exercise panels use weekly grouping (`GROUP BY time(1w)`) and only have April data (3 sessions). Panels will fill in progressively as Caliber sessions resume from 1 June. The `Sled 45° Leg Press` panel uses a double-encoded degree symbol in InfluxDB (`Sled 45Â° Leg Press`) due to a character encoding issue in the Garmin API — the dashboard script accounts for this.

### Ollama Cold Start
First Ollama request after a reboot or idle period (>5 min) takes 30-60 seconds while the model loads into GPU memory. The daily brief has a 600-second timeout which handles this. Max must have auto-login configured and the Task Scheduler `OllamaAutoStart` job active.

### Caliber Sync Auth
The Caliber sync uses OAuth tokens tied to the Claude.ai account. If those tokens expire, the sync will fail silently. Monitor the CaliberStats database weekly — if no new workouts appear after a gym session, check the caliber-sync container logs.

### Garmin Token Expiry
Garmin OAuth tokens are stored in `/share/Container/garmin-direct-sync/tokens/garmin_tokens.json`. The token age check runs weekly and warns at 25 days. To renew: run `garmin_auth.py` on Max and copy the new tokens to the NAS.

### No Body Battery at Rest
The body battery change shown in the daily brief is calculated as `high - low` from DailyStats. On days with very little movement the range may be small even if overnight recovery was good. The watch display is the ground truth.

---

## Completed — No Longer Gaps

These were previously listed as gaps and are now resolved:

- ~~Weekly AI coaching report~~ → Done, Monday 07:00 UTC
- ~~Grafana strength panels~~ → Done, scripted via API
- ~~InfluxDB backup~~ → Done, weekly cron job
- ~~Ollama auto-start reliability~~ → Done, Task Scheduler logon trigger
- ~~LM Studio → Ollama migration~~ → Done, fitness-coach Modelfile
- ~~Step nudge~~ → Done, 18:00 UTC Mon-Sat
- ~~Token expiry notification~~ → Done, weekly check
- ~~Container health monitoring~~ → Done, hourly check
- ~~Training dashboard~~ → Done, :3002
- ~~Open WebUI on NAS~~ → Done, :3001
- ~~Tailscale remote access~~ → Done

---

## Summary — Remaining Before 29 August

| Item | Priority | Target Date |
|------|----------|-------------|
| Event day dashboard | High | 1 August |
| Pre-event brief (28 Aug) | High | 25 August |
| HRV baseline recalibration | Medium | 28 June |
| Post-event recovery plan | Medium | August |
