# Guide 05 — AI Fitness Assistant: System Prompt, Prompting, and Daily Workflow

**LLM endpoint:** Max — http://192.168.1.50:11434  
**Data source:** InfluxDB on NAS — http://192.168.1.60:8086

This guide covers three things: the system prompt that turns Qwen3 into a knowledgeable fitness coach, how to extract your data from InfluxDB to include in conversations, and practical prompting patterns for your three core goals — nutrition coaching, training load analysis, and race planning.

> **Primary event:** 100,000 Steps Challenge — Saturday 29 August 2026 (13 weeks away from setup date). See **Guide 06** for the dedicated training and nutrition plan for this event.

---

## Part 1 — System Prompt

The system prompt lives in exactly one place: **`system_prompt.txt`**, which is baked directly into the `fitness-coach` Ollama model via the Modelfile (Guide 04, Step 6). Paste its contents into Ollama's System Prompt field only if you're testing a variant outside the baked-in model — otherwise the `fitness-coach` model already carries it.

> **Why this guide doesn't duplicate the prompt inline:** an earlier version of this guide carried its own copy of the system prompt, which drifted out of sync with `system_prompt.txt` (different stomach volume, different eating pattern, stale weight and equipment). Keeping a single canonical copy in `system_prompt.txt` avoids that failure mode. If you need to change the athlete profile, targets, or equipment list, edit `system_prompt.txt` and recreate the `fitness-coach` model — don't edit a copy here.

Current headline facts baked into `system_prompt.txt` as of this update (see that file for the full prompt):
- Weight, macro targets, and equipment (Garmin Fenix 8, Wahoo TICKR X chest strap + Polar Verity Sense armband with automatic fallback, NordicTrack T5 max 10% incline) should be kept current in `system_prompt.txt` itself
- Primary event: 100,000 Steps Challenge, Saturday 29 August 2026, Watton → Holme-next-the-Sea via the Peddars Way (~66 km, contingency to ~70 km at Thornham Deli)
- No solid food on event day — gels and liquids only, per the post-sleeve nutrition strategy

---

## Part 2 — Extracting Data from InfluxDB

You need to pull data out of InfluxDB and paste it into your conversations as context. All queries run from the **NAS SSH session** (`ssh admin@192.168.1.60`) and use `docker exec` to reach inside the influxdb container.

**How to use the output:** Copy the text from the terminal and paste it directly into your Ollama chat before your question. The AI uses it as context for that session.

> **Weight note:** Weight as of the last update (25 June 2026) is 113 kg, longer-term target 95 kg. This isn't pulled live — check `system_prompt.txt` for the value currently baked into the model, and include your latest weight when prompting manually so the AI uses accurate calorie and macro targets.

---

**Last 7 days of daily summary (steps, resting HR, sleep, stress, body battery):**
```bash
docker exec influxdb influx   -username admin   -password adminSecretPassword   -database GarminStats   -format csv   -execute "SELECT totalSteps, restingHeartRate, bodyBatteryHighestValue, bodyBatteryLowestValue, highStressDuration, lowStressDuration FROM DailyStats WHERE time >= now() - 7d ORDER BY time ASC"
```

**Last 7 days of sleep:**
```bash
docker exec influxdb influx   -username admin   -password adminSecretPassword   -database GarminStats   -format csv   -execute "SELECT sleepTimeSeconds, sleepScore, avgOvernightHrv, deepSleepSeconds, remSleepSeconds, bodyBatteryChange FROM SleepSummary WHERE time >= now() - 7d ORDER BY time ASC"
```

**Last 7 days of nutrition:**
```bash
docker exec influxdb influx   -username admin   -password adminSecretPassword   -database CronometerStats   -format csv   -execute "SELECT Energy_kcal, Protein_g, Fat_g, Carbs_g, Net_Carbs_g FROM daily_nutrition WHERE time >= now() - 7d ORDER BY time ASC"
```

**Recent activities (last 10):**
```bash
docker exec influxdb influx   -username admin   -password adminSecretPassword   -database GarminStats   -format csv   -execute "SELECT activityName, activityType, calories, averageHR, elapsedDuration, activityTrainingLoad FROM ActivitySummary ORDER BY time DESC LIMIT 10"
```

**Recent strength sessions — activity summaries (last 5 strength sessions):**
```bash
docker exec influxdb influx   -username admin   -password adminSecretPassword   -database GarminStats   -format csv   -execute "SELECT activityName, calories, averageHR, elapsedDuration, activityTrainingLoad FROM ActivitySummary WHERE activityType = 'strength_training' ORDER BY time DESC LIMIT 5"
```

**Recent strength set detail (last session):**
```bash
docker exec influxdb influx   -username admin   -password adminSecretPassword   -database GarminStats   -format csv   -execute "SELECT exercise, reps, weight_kg, volume_kg FROM StrengthSets ORDER BY time DESC LIMIT 20"
```

**Latest weight:**
```bash
docker exec influxdb influx   -username admin   -password adminSecretPassword   -database GarminStats   -format csv   -execute "SELECT last(weight)/1000 FROM BodyComposition"
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
- Training goal: [e.g. marathon in 13 weeks / general fitness / lose X kg]
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

Qwen3 supports a "thinking" mode where it reasons through a problem step by step before answering. Turn this on in Ollama for complex multi-factor questions (race planning, interpreting conflicting signals in your data). Turn it off for quick daily check-ins — it is slower but more thorough.

In Ollama: toggle the **Thinking** 💡 switch in the chat toolbar.

Use thinking mode for:
- Race plans (multiple phases, many variables)
- "Why is my performance declining?" type investigations
- Designing a 12-week training block

Use standard mode for:
- "What should I eat before tomorrow's tempo run?"
- "My sleep score was 68 last night — what does this mean?"
- Quick macro calculations

---

## Part 5 — Accessing Ollama From Other Devices

The Ollama API is OpenAI-compatible, which means any tool that supports a custom OpenAI endpoint can talk to it.

**From a browser on any device on your network:**
```
http://192.168.1.50:11434/v1/models
```
This opens the Ollama web chat interface.

**From a terminal on any machine:**
```bash
curl http://192.168.1.50:11434/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen3.6-27b",
    "messages": [
      {"role": "user", "content": "What is my training load telling me?"}
    ]
  }'
```

**Open WebUI (optional — adds a polished multi-model chat UI):**
If you want a more feature-rich chat interface than Ollama's built-in one, Open WebUI is a popular open-source option that connects to the Ollama API. It can be deployed as a container on the NAS alongside Grafana.

```yaml
# Add this service to your fitness-stack docker-compose.yml on the NAS
  open-webui:
    image: ghcr.io/open-webui/open-webui:main
    container_name: open-webui
    restart: unless-stopped
    ports:
      - "8080:8080"
    environment:
      OPENAI_API_BASE_URL: "http://192.168.1.50:11434/v1"
      OPENAI_API_KEY: "not-needed"
    volumes:
      - /share/Container/fitness-stack/open-webui:/app/backend/data
    networks:
      - fitness-net
```

Access at `http://192.168.1.60:8080`.

---

## Part 6 — Keeping the Stack Running

**Daily routine:**
1. Morning: Garmin sync runs automatically every 6 hours — no action needed
2. Morning: Cronometer sync task runs at 7 AM — no action needed
3. When you want coaching: run `get_fitness_context.py`, paste output into Ollama, ask your question

**Weekly:**
- Check Grafana dashboards (`http://192.168.1.60:3000`) for trend views
- Review the Garmin Stats dashboard for HRV and Body Battery trends
- Check the nutrition dashboard for macro compliance over the week

**Monthly:**
- Run `docker compose pull` in each sync directory to update to the latest container images
- Check Ollama for model updates — newer Qwen3 quantisations occasionally improve quality

---

## Putting It All Together — Example Morning Check-In

1. Open a terminal and run:
   ```bash
   ssh admin@192.168.1.60 python3 /share/Container/get_fitness_context.py
   ```
   Copy the output.

2. Open Ollama on Max (or verify the API at `http://192.168.1.50:11434/v1/models`)

3. Select the **Fitness Coach** preset

4. Paste the data context and type:
   ```
   Here is my data from the past week. Yesterday I did a 75-minute zone 2 run.
   My HRV this morning dropped 12ms below my baseline. I have a hard interval 
   session planned for today. Should I proceed, modify it, or take a recovery day?
   ```

5. Get a data-informed recommendation in seconds, entirely privately on your local network.
