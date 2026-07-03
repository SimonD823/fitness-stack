# Guide 08 — Status & Gap Analysis

Current status of all components as of early July 2026 (Week 5 of 13), plus remaining items before the event.

---

## What Is Fully Working

| Component | Status | Notes |
|-----------|--------|-------|
| Garmin sync — all metrics | ✅ | Every 30 min |
| HRV sync | ✅ | Runs after sleep sync to prevent field overwrite |
| Sleep data | ✅ | Query window starts 20:00Z previous night to catch ~21:00Z timestamps |
| Body composition / weight | ✅ | Stored at 00:00:00Z; dashboard queries midnight UTC boundaries |
| Body battery change | ✅ | Calculated as high-low, matches watch display |
| Strength sets with Caliber names | ✅ | Workout plan name mapping via associatedWorkoutId |
| Exercise name normalisation | ✅ | `normalize_exercise()` in dashboard.py maps raw Garmin enums to Caliber plan names |
| Cronometer nutrition sync | ✅ | 06:00 AM London daily |
| Daily coaching email | ✅ | 08:00 AM BST, Ollama/Qwen3.6-27B, data freshness check |
| Weekly training report | ✅ | Monday 07:00 AM UTC, Mon-Sun data, freshness check |
| Step nudge email | ✅ | 18:00 UTC Mon-Sat, skips Sunday |
| Garmin token age check | ✅ | Monday 06:30 UTC, warns at 25d, critical at 35d |
| Container health monitoring | ✅ | Hourly, email alert if any container down |
| Ollama auto-start | ✅ | Task Scheduler logon trigger, Windows auto-login |
| Open WebUI | ✅ | NAS container :3001, fitness-coach model default |
| Tailscale remote access | ✅ | NAS Tailscale node, access from anywhere |
| Training dashboard | ✅ | NAS container :3002, planned vs actual, click-to-expand modals |
| Walk modal with duration | ✅ | Shows target steps + target duration + actual steps + actual duration |
| Coach override display | ✅ | Dashboard reads CoachNotes; surfaces readiness class, cancellations, walk cap, step ceiling |
| CoachNotes structured fields | ✅ | daily_brief.py writes machine-readable override fields alongside free text |
| CoachNotes idempotent re-runs | ✅ | store_coach_note() deletes today's entries before writing — no duplicate notes |
| InfluxDB backup | ✅ | Weekly Sunday 03:00, /share/backup/influxdb/, 4 weeks |
| Grafana strength panels | ✅ | Scripted via API, 5 panel types |
| Garmin sync days back fix | ✅ | SYNC_DAYS_BACK=1 now correctly syncs today only (range(days_back) not range(days_back+1)) |

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
Update `TRAINING_PLAN_V2.md` and the system prompt with a 2-week recovery protocol after the event.

### Medium Priority

**HRV Baseline Recalibration** — target late June (after 4 weeks of training data)
After 4 weeks of consistent Zone 2 training (from 1 June), calculate a personal HRV baseline from the rolling 7-day average. Update the system prompt with the actual value rather than the age-based estimate.

**Code maintenance — `_rc_consequences` duplication**
The `_rc_consequences` dict currently exists in two places within `daily_brief.py`. Recommended future refactor: extract to a single module-level constant to prevent divergence.

---

## Known Issues / Limitations

### Garmin Cold Start on Coach Override Badges
Coach override badges (Suppressed, Caliber cancelled, etc.) appear in the training dashboard modal only from the next daily brief run after deploying the structured fields feature. Entries written before the feature was added contain only the `note` field — badges will not appear for those historical entries but the note text still renders.

### Grafana Strength Panels — Sparse Data
The individual exercise panels use weekly grouping and only have data from April 2026 (3 sessions) and June 2026 onwards. Panels fill progressively as Caliber sessions accumulate.

### Ollama Cold Start
First Ollama request after a reboot or idle period takes 30-60 seconds while the model loads into GPU memory. The daily brief has a 600-second timeout which handles this. Max must have auto-login configured and the Task Scheduler `OllamaAutoStart` job active.

### Caliber MCP — Still Blocked, Not a "sync that sometimes fails"
There is no scheduled `caliber-sync` container and no CaliberStats data pipeline. Caliber's Keycloak OAuth client (`caliber-mcp`) only accepts pre-registered redirect URIs, and Caliber support has not yet provided one — see `Caliber_MCP_Integration_Guide.md` for the full history and the support email template. Caliber data is only reachable interactively through the Claude.ai connector (session-bound, cannot be automated). Strength set/rep/weight detail is captured permanently via the Garmin route instead: `garmin-direct-sync` + workout plan name mapping → `GarminStats.StrengthSets`. This is not a temporary workaround pending Caliber MCP — treat it as the permanent path unless Caliber support resolves the redirect URI issue.

### Garmin Token Expiry
Garmin OAuth tokens are stored in `/share/Container/garmin-direct-sync/tokens/garmin_tokens.json`. The token age check runs weekly and warns at 25 days. To renew: run `garmin_auth.py` on Max and copy the new tokens to the NAS.

---

## Completed — No Longer Gaps

These were previously listed as gaps or bugs and are now resolved:

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
- ~~Exercise names truncated/wrong on day cards~~ → Fixed via normalize_exercise() + CSS ellipsis
- ~~HRV and Sleep showing — in dashboard~~ → Fixed: query window now starts 20:00Z previous night
- ~~Weight trend box empty~~ → Fixed: BodyComposition query now uses midnight UTC boundaries
- ~~Walk modal showing steps only, no duration~~ → Fixed: actual and target duration now shown
- ~~Coach override not surfaced in dashboard~~ → Fixed: CoachNotes structured fields + dashboard display
- ~~Multiple CoachNotes per day from repeated brief runs~~ → Fixed: store_coach_note() deletes before writing
- ~~SYNC_DAYS_BACK=1 syncing two days~~ → Fixed: range(days_back) not range(days_back+1)
- ~~Internal Server Error on future walk day modals~~ → Fixed: d reference guarded with is_future check

---

## Summary — Remaining Before 29 August

| Item | Priority | Target Date |
|------|----------|-------------|
| Event day dashboard | High | 1 August |
| Pre-event brief (28 Aug) | High | 25 August |
| HRV baseline recalibration | Medium | 28 June |
| Post-event recovery plan | Medium | August |
| `_rc_consequences` refactor | Low | Before August |
