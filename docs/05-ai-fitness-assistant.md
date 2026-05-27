# Guide 05 — AI Fitness Assistant: System Prompt, Prompting, and Daily Workflow

**LLM endpoint:** Max — http://MAX_IP:1234  
**Data source:** InfluxDB on NAS — http://NAS_IP:8086

This guide covers three things: the system prompt that turns Qwen3 into a knowledgeable fitness coach, how to extract your data from InfluxDB to include in conversations, and practical prompting patterns for your three core goals — nutrition coaching, training load analysis, and race planning.

> **Primary event:** 100,000 Steps Challenge — Saturday 29 August 2026 (14 weeks away from setup date). See **Guide 06** for the dedicated training and nutrition plan for this event.

---

## Part 1 — System Prompt

Paste this into LM Studio's System Prompt field (Chat view → click the system prompt area at the top, or in your Fitness Coach preset):

```
You are an expert personal fitness and nutrition coach with deep knowledge in:
- Endurance sports training (ultra-endurance walking, running, periodisation)
- Post-bariatric sports nutrition — fuelling with a restricted stomach volume
- Evidence-based nutrition science: macronutrient timing, weight loss during training, event-day fuelling
- Heart rate variability (HRV), training load management, and recovery optimisation
- Data analysis of wearable metrics from Garmin devices

ATHLETE PROFILE:
- Gastric sleeve surgery: 14 March 2025 (14+ months post-op)
- Current weight: [resolved automatically from latest Garmin/Cronometer data — update this value when pasting fresh data]
- Goal: Ongoing healthy weight loss while training for an ultra-endurance event
- Stomach volume: approximately 100–150 ml per sitting (~150–200 kcal per eating occasion)
- Hitting 1,600 kcal requires 8–11 eating occasions per day — eating must be scheduled, not hunger-driven
- Post-sleeve hunger signals are unreliable — the athlete may not feel hungry when they need to eat
- Bariatric supplement protocol should be in place (B12, D3, calcium citrate, multivitamin)
- Estimated TDEE scales with current weight — recalculate as weight changes (~23 kcal/kg as a rough guide)
- Target deficit: ~1,000–1,200 kcal/day on rest days → ~0.8–1.0 kg/week loss

ATHLETE EQUIPMENT:
- Garmin Fenix 8 watch + Polar Verity Sense heart rate monitor (arm-worn)
- NordicTrack T5 treadmill (home, max 12% incline)
- Garmin Connect for activity tracking; Cronometer for nutrition logging
- Caliber app for strength training — session summaries in GarminStats.ActivitySummary; set/rep/weight detail in GarminStats.StrengthSets (populated by garmin_direct_sync.py (handles strength automatically) running daily on Max)

PRIMARY EVENT: 100,000 Steps Challenge — Saturday 29 August 2026
- ~70–80 km walking, 14–18 hours active, classified as ultra-endurance
- Estimated calorie expenditure at 116 kg: 5,000–6,500 kcal
- Requires consuming ~3,000–4,000 kcal DURING the event in ~20–25 sleeve-sized portions
- CRITICAL SLEEVE RISK: cannot compensate for missed fuelling with a large meal — once behind, stays behind
- Liquid calories (sports drinks, shakes, diluted juice) are essential to supplement solid food
- 14-week training plan, started 19 May 2026

EVENT-DAY FUELLING PRODUCTS (all tested in training):
- Primary fuel: Tailwind Endurance Fuel (100 kcal/scoop, 25g carbs, 310mg sodium) — sipped continuously from 2L EVOC bladder in 5.11 Rush 12 pack
- Supplement gel: SIS GO Isotonic (88 kcal, 22g carbs, 10mg sodium) — half gel every 45-60 min as texture break. Contains Acesulfame K — tolerance must be confirmed in training.
- Optional upgrade: Maurten Gel 100 (100 kcal, 25g carbs — no artificial sweeteners) for back-half variety
- Bladder target: 4 scoops Tailwind per fill, ~3-4 refills during event
- No solid food planned — liquid/gel fuelling only
- MUST sip on schedule regardless of hunger — post-sleeve hunger suppression is amplified by exercise
- Total: 1,600 kcal (rest days and short sessions — appropriate deficit for weight loss)
- Protein: 150 g / day (1.29 g/kg — near post-bariatric floor; hold constant every day)
- Carbohydrates: 149 g baseline — PRIMARY LEVER, increase on training days
- Fat: 45 g / day — hold roughly constant
- Long training days (40K+ steps, 60+ min hard treadmill): carbs rise to 300–360 g, total ~2,600–3,000 kcal
- Spread across many small portions — cannot eat large meals to catch up

When analysing data:
- Always check if training load was adequately fuelled given sleeve capacity constraints
- Protein below 140 g on any day is a red flag — flag it explicitly
- On days with long sessions, compare actual carb intake to the training-day target, not the 149 g baseline
- Recommend specific small-portion food strategies, not generic "eat more carbs" advice
- Weight loss is the long-term goal; do not recommend maintaining a large deficit on peak training days

Be direct and specific. Use the numbers provided. No generic advice when data is available.
Always reference the 29 August challenge as the primary training target.
```

---

## Part 2 — Extracting Data from InfluxDB

You need to pull data out of InfluxDB and paste it into your conversations as context. All queries run from the **NAS SSH session** (`ssh admin@NAS_IP`) and use `docker exec` to reach inside the influxdb container.

**How to use the output:** Copy the text from the terminal and paste it directly into your LM Studio chat before your question. The AI uses it as context for that session.

> **Weight note:** Your current weight is 115.1 kg. Include this when prompting manually so the AI uses accurate calorie and macro targets.

---

**Last 7 days of daily summary (steps, resting HR, sleep, stress, body battery):**
```bash
docker exec influxdb influx   -username admin   -password YOUR_INFLUX_ADMIN_PASSWORD   -database GarminStats   -format csv   -execute "SELECT totalSteps, restingHeartRate, bodyBatteryHighestValue, bodyBatteryLowestValue, highStressDuration, lowStressDuration FROM DailyStats WHERE time >= now() - 7d ORDER BY time ASC"
```

**Last 7 days of sleep:**
```bash
docker exec influxdb influx   -username admin   -password YOUR_INFLUX_ADMIN_PASSWORD   -database GarminStats   -format csv   -execute "SELECT sleepTimeSeconds, sleepScore, avgOvernightHrv, deepSleepSeconds, remSleepSeconds, bodyBatteryChange FROM SleepSummary WHERE time >= now() - 7d ORDER BY time ASC"
```

**Last 7 days of nutrition:**
```bash
docker exec influxdb influx   -username admin   -password YOUR_INFLUX_ADMIN_PASSWORD   -database CronometerStats   -format csv   -execute "SELECT Energy_kcal, Protein_g, Fat_g, Carbs_g, Net_Carbs_g FROM daily_nutrition WHERE time >= now() - 7d ORDER BY time ASC"
```

**Recent activities (last 10):**
```bash
docker exec influxdb influx   -username admin   -password YOUR_INFLUX_ADMIN_PASSWORD   -database GarminStats   -format csv   -execute "SELECT activityName, activityType, calories, averageHR, elapsedDuration, activityTrainingLoad FROM ActivitySummary ORDER BY time DESC LIMIT 10"
```

**Recent strength sessions — activity summaries (last 5 strength sessions):**
```bash
docker exec influxdb influx   -username admin   -password YOUR_INFLUX_ADMIN_PASSWORD   -database GarminStats   -format csv   -execute "SELECT activityName, calories, averageHR, elapsedDuration, activityTrainingLoad FROM ActivitySummary WHERE activityType = 'strength_training' ORDER BY time DESC LIMIT 5"
```

**Recent strength set detail (last session):**
```bash
docker exec influxdb influx   -username admin   -password YOUR_INFLUX_ADMIN_PASSWORD   -database GarminStats   -format csv   -execute "SELECT exercise, reps, weight_kg, volume_kg FROM StrengthSets ORDER BY time DESC LIMIT 20"
```

**Latest weight:**
```bash
docker exec influxdb influx   -username admin   -password YOUR_INFLUX_ADMIN_PASSWORD   -database GarminStats   -format csv   -execute "SELECT last(weight)/1000 FROM BodyComposition"
```

---

## Part 3 — Prompting Patterns by Goal

### Goal 1: Daily Nutrition & Macro Coaching

**Template:**
```
Here is my nutrition and training data for the past week:

[PASTE OUTPUT FROM get_fitness_context.py]

My goals:
- Body weight: [your weight] kg
- Training goal: [e.g. marathon in 14 weeks / general fitness / lose X kg]
- Current training volume: approximately [X hours/week]

Questions:
1. Is my protein intake adequate for my training load?
2. Are there any days where my calorie deficit/surplus looks problematic given my training?
3. What should I adjust this week?
```

**Follow-up for a specific day:**
```
On [date] I did a 90-minute long run at zone 2. My calories that day were [X] kcal 
and protein was [Y]g. Was my post-run fuelling adequate? What would you recommend 
I eat in the 2-hour window after this type of session?
```

---

### Goal 2: Training Load & Recovery Analysis

**Template:**
```
[PASTE DATA CONTEXT]

My training block this week consisted of:
[list each session: type, duration, perceived effort 1-10]

Looking at my HRV trend, Body Battery, and resting heart rate over the week:
1. Is my recovery keeping pace with my training load?
2. What are the warning signs in this data I should pay attention to?
3. Should I adjust this coming week — more load, maintenance, or a recovery week?
```

**HRV trend question:**
```
My HRV values over the past 14 days are: [paste the HRV column from your data]
My baseline HRV (30-day average) is approximately [X] ms.

Interpret this trend in the context of my training. Am I showing signs of 
accumulated fatigue or adequate adaptation?
```

---

### Goal 3: Race / Event Planning & Periodisation

**Template:**
```
I am planning for [race name / event type] on [date].
That is [X] weeks away from today.

My current fitness context:
[PASTE DATA CONTEXT]

My recent training history:
- Average weekly volume (last 4 weeks): [hours or km]
- Longest session in the last month: [type + duration]
- Any recent injuries or setbacks: [none / describe]

Please create a high-level periodisation plan from now until race day, including:
1. Phase breakdown (base / build / peak / taper)
2. Recommended weekly volume targets per phase
3. Key session types each week (long run, intervals, tempo, recovery)
4. Nutrition priorities during each phase
```

**Race week fuelling question:**
```
My race is [distance] on [date]. 
My weight is [X] kg and my expected finish time is approximately [Y] hours.

Based on my recent nutrition data [paste context], design my carbohydrate 
loading strategy for the 3 days before the race and an on-course fuelling plan.
```

---

## Part 4 — Thinking Mode for Deep Analysis

Qwen3 supports a "thinking" mode where it reasons through a problem step by step before answering. Turn this on in LM Studio for complex multi-factor questions (race planning, interpreting conflicting signals in your data). Turn it off for quick daily check-ins — it is slower but more thorough.

In LM Studio: toggle the **Thinking** 💡 switch in the chat toolbar.

Use thinking mode for:
- Race plans (multiple phases, many variables)
- "Why is my performance declining?" type investigations
- Designing a 12-week training block

Use standard mode for:
- "What should I eat before tomorrow's tempo run?"
- "My sleep score was 68 last night — what does this mean?"
- Quick macro calculations

---

## Part 5 — Accessing LM Studio From Other Devices

The LM Studio API is OpenAI-compatible, which means any tool that supports a custom OpenAI endpoint can talk to it.

**From a browser on any device on your network:**
```
http://MAX_IP:1234/v1/models
```
This opens the LM Studio web chat interface.

**From a terminal on any machine:**
```bash
curl http://MAX_IP:1234/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen3.6-27b",
    "messages": [
      {"role": "user", "content": "What is my training load telling me?"}
    ]
  }'
```

**Open WebUI (optional — adds a polished multi-model chat UI):**
If you want a more feature-rich chat interface than LM Studio's built-in one, Open WebUI is a popular open-source option that connects to the LM Studio API. It can be deployed as a container on the NAS alongside Grafana.

```yaml
# Add this service to your fitness-stack docker-compose.yml on the NAS
  open-webui:
    image: ghcr.io/open-webui/open-webui:main
    container_name: open-webui
    restart: unless-stopped
    ports:
      - "8080:8080"
    environment:
      OPENAI_API_BASE_URL: "http://MAX_IP:1234/v1"
      OPENAI_API_KEY: "not-needed"
    volumes:
      - /share/Container/fitness-stack/open-webui:/app/backend/data
    networks:
      - fitness-net
```

Access at `http://NAS_IP:8080`.

---

## Part 6 — Keeping the Stack Running

**Daily routine:**
1. Morning: Garmin sync runs automatically every 6 hours — no action needed
2. Morning: Cronometer sync task runs at 7 AM — no action needed
3. When you want coaching: run `get_fitness_context.py`, paste output into LM Studio, ask your question

**Weekly:**
- Check Grafana dashboards (`http://NAS_IP:3000`) for trend views
- Review the Garmin Stats dashboard for HRV and Body Battery trends
- Check the nutrition dashboard for macro compliance over the week

**Monthly:**
- Run `docker compose pull` in each sync directory to update to the latest container images
- Check LM Studio for model updates — newer Qwen3 quantisations occasionally improve quality

---

## Putting It All Together — Example Morning Check-In

1. Open a terminal and run:
   ```bash
   ssh admin@NAS_IP python3 /share/Container/get_fitness_context.py
   ```
   Copy the output.

2. Open LM Studio on Max (or verify the API at `http://MAX_IP:1234/v1/models`)

3. Select the **Fitness Coach** preset

4. Paste the data context and type:
   ```
   Here is my data from the past week. Yesterday I did a 75-minute zone 2 run.
   My HRV this morning dropped 12ms below my baseline. I have a hard interval 
   session planned for today. Should I proceed, modify it, or take a recovery day?
   ```

5. Get a data-informed recommendation in seconds, entirely privately on your local network.
