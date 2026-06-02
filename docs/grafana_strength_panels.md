# Grafana Strength Progression Panels

Strength data from your Garmin sessions is stored in `GarminStats.StrengthSets` with Caliber exercise names. This guide covers the automated dashboard creation script and manual panel queries.

---

## Automated Setup (Recommended)

The dashboard is created via a Python script that calls the Grafana API — no manual panel creation needed.

### Run the script

Copy `create_grafana_strength_dashboard.py` to `\\nas\Container\daily-brief\`, then from NAS SSH:

```bash
docker exec -i daily-brief python3 - < /share/Container/daily-brief/create_grafana_strength_dashboard.py
```

This creates (or overwrites) the **Strength Progression — Peddars Way** dashboard with all panels.

### What gets created

| Panel | Type | Description |
|-------|------|-------------|
| Training Summary | Stat | Total sessions and sets in selected period |
| Weekly Strength Volume | Bar chart | Total kg lifted per week |
| Max Weight Per Exercise | Time series | All exercises colour coded over time |
| 6 key exercises | Bar chart | Individual progression per exercise |
| Personal Records | Table | Max weight per exercise |

---

## Known Issues

### Sparse data (before June 2026)
Only 3 sessions exist (April 20, 22, 26). The individual exercise bar charts use weekly grouping (`GROUP BY time(1w)`) which shows correctly with sparse data. Panels fill progressively as training resumes from 1 June.

### Sled Leg Press encoding
The exercise `Sled 45° Leg Press` is stored in InfluxDB as `Sled 45Â° Leg Press` due to a character encoding issue in the Garmin API (UTF-8 bytes misinterpreted as Latin-1). The dashboard script accounts for this automatically.

---

## Manual Panel Queries

If you need to add individual panels manually in Grafana, use these InfluxQL queries. Go to **Grafana → Explore**, select the `InfluxDB-Garmin` datasource, and enable **Raw query**.

### Max weight per exercise over time
```sql
SELECT max(weight_kg) FROM "StrengthSets"
WHERE $timeFilter AND exercise = 'Machine Seated Leg Curl' AND weight_kg > 0
GROUP BY time(7d) fill(none)
```

### Weekly total volume
```sql
SELECT sum(volume_kg) FROM "StrengthSets"
WHERE $timeFilter AND volume_kg > 0
GROUP BY time(7d) fill(none)
```

### Personal records (max weight ever)
```sql
SELECT max(weight_kg) FROM "StrengthSets"
WHERE $timeFilter AND weight_kg > 0
GROUP BY exercise fill(none)
```

### All exercises in one query (time series panel)
```sql
SELECT max(weight_kg) FROM "StrengthSets"
WHERE $timeFilter AND weight_kg > 0
GROUP BY time(7d), exercise fill(none)
```

### Session count per week
```sql
SELECT count(reps) FROM "StrengthSets"
WHERE $timeFilter AND weight_kg > 0
GROUP BY time(7d) fill(none)
```

---

## Exercise Names in InfluxDB

All exercises use Caliber plan names matched via the Garmin workout plan description field:

**Legs & Abs (Monday)**
- Dumbbell Bench Glute Bridge
- Sled 45Â° Leg Press ← note encoding
- Kettlebell Goblet Squat
- Machine Leg Extension
- Cable Pallof Press
- Machine Seated Leg Curl
- Machine Seated Calf Press
- Alternating Leg Raise
- Lying Leg-Hip Raise

**Back & Shoulders (Wednesday)**
- Dumbbell Bent-Over Row
- Cable Seated Row
- Cable Lat Pulldown
- Dumbbell Shoulder Press
- Dumbbell Lateral Raise
- Dumbbell Shrug

**Chest & Arms (Friday)**
- Dumbbell Bench Press
- Dumbbell Incline Bench Press
- Dumbbell Floor Fly
- Dumbbell Curl
- Dumbbell Tricep Extension

---

## Verify Data in InfluxDB

From NAS SSH or the InfluxDB query URL:

```bash
curl -s "http://192.168.1.60:8086/query?u=admin&p=YOUR_ADMIN_PASSWORD&db=GarminStats&q=SHOW+TAG+VALUES+FROM+StrengthSets+WITH+KEY%3Dexercise"
```

This lists all exercise names stored — useful for debugging panel queries.

---

## Dashboard URL

After running the creation script:

```
http://192.168.1.60:3000/d/strength-progression/
```

Set the time range to **Last 6 months** to see all April data. As training progresses from June, switch to **Last 90 days** for a cleaner view.
