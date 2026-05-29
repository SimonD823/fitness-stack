# Guide 04 — Ollama + Qwen3.6-27B on Max

This guide covers installing and configuring Ollama on Max (Minisforum MS-S1 Max, Windows 11) as the LLM backend for the daily coaching brief and Open WebUI. Ollama replaces LM Studio and runs as a proper auto-start service.

---

## Why Ollama

| Feature | LM Studio | Ollama |
|---------|-----------|--------|
| Auto-start on boot | Unreliable — needs user login | Yes — via Task Scheduler logon trigger |
| GUI required | Yes | No — headless friendly |
| API compatibility | OpenAI-compatible | OpenAI-compatible + native API |
| Thinking mode control | Jinja template hack | `think: false` in API payload |
| Model management | GUI | CLI (`ollama pull`) |
| Port | 1234 | 11434 |

---

## Step 1 — Enable Windows Auto-Login on Max

Ollama starts via a logon trigger — auto-login ensures it starts after a power cut without physical interaction.

Run as Administrator in PowerShell:

```powershell
reg add "HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon" /v AutoAdminLogon /t REG_SZ /d 1 /f
reg add "HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon" /v DefaultUserName /t REG_SZ /d "Simon" /f
reg add "HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon" /v DefaultPassword /t REG_SZ /d "YOUR_WINDOWS_PASSWORD" /f
reg add "HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon" /v DefaultDomainName /t REG_SZ /d "." /f
```

Verify:

```powershell
Get-ItemProperty "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon" | Select-Object AutoAdminLogon, DefaultUserName
```

Should show `AutoAdminLogon = 1` and `DefaultUserName = Simon`.

---

## Step 2 — Install Ollama

Download and install from `https://ollama.com/download/windows`

The installer places Ollama at:
```
C:\Users\Simon\AppData\Local\Programs\Ollama\ollama.exe
```

---

## Step 3 — Configure Environment Variables

Set Ollama to listen on all network interfaces (so the NAS can reach it) and store models on D:

1. Press **Win+R** → `sysdm.cpl` → **Advanced** → **Environment Variables**
2. Under **System variables** add:

| Variable | Value |
|----------|-------|
| `OLLAMA_HOST` | `0.0.0.0` |
| `OLLAMA_MODELS` | `D:\Ollama` |

3. Click OK on all windows

---

## Step 4 — Add Windows Firewall Rule

Run as Administrator:

```powershell
New-NetFirewallRule -DisplayName "Ollama API" -Direction Inbound -Protocol TCP -LocalPort 11434 -Action Allow -Profile Private
```

---

## Step 5 — Pull the Model

```powershell
ollama pull batiai/qwen3.6-27b:q6
```

This downloads ~23GB (Q6_K quantisation). With 128GB UMA on Max it loads entirely on GPU.

Verify:

```powershell
ollama list
```

---

## Step 6 — Create Fitness Coach Model

Create a Modelfile that bakes the system prompt into the model so Open WebUI and any other client automatically uses the correct coaching context:

1. Save `docs/system_prompt.txt` content to `C:\AdaptiveTraining\Modelfile` (no extension)
2. The Modelfile should look like:

```
FROM batiai/qwen3.6-27b:q6

SYSTEM """
<contents of system_prompt.txt>
"""

PARAMETER temperature 0.7
PARAMETER num_predict 2000
```

3. Create the model:

```powershell
cd C:\AdaptiveTraining
ollama create fitness-coach -f Modelfile
```

4. Verify:

```powershell
ollama list
# Should show both batiai/qwen3.6-27b:q6 and fitness-coach:latest
```

---

## Step 7 — Set Up Auto-Start via Task Scheduler

Ollama must start automatically after reboot without requiring manual login. Use Task Scheduler with a logon trigger:

1. Press **Win+R** → `taskschd.msc` → Enter
2. Click **Create Task** (right panel — not Basic Task)
3. **General tab:**
   - Name: `OllamaAutoStart`
   - Select **Run only when user is logged on**
   - Check **Run with highest privileges**
4. **Triggers tab** → New:
   - Begin the task: **At log on**
   - Specific user: `Simon`
   - Click OK
5. **Actions tab** → New:
   - Program: `C:\Users\Simon\AppData\Local\Programs\Ollama\ollama.exe`
   - Arguments: `serve`
   - Click OK
6. **Settings tab:**
   - Uncheck **Stop the task if it runs longer than**
   - Check **If the task fails, restart every:** 2 minutes, up to 3 times
7. Click **OK**

Test immediately:

```powershell
schtasks /run /tn "OllamaAutoStart"
Start-Sleep -Seconds 15
Get-Process ollama
```

---

## Step 8 — Test from NAS

```bash
# From NAS SSH
curl http://192.168.1.50:11434/api/tags
```

Should return JSON listing `fitness-coach:latest` and `batiai/qwen3.6-27b:q6`.

Test a completion:

```bash
curl -s http://192.168.1.50:11434/api/chat \
  -H "Content-Type: application/json" \
  -d '{"model":"fitness-coach","messages":[{"role":"user","content":"Say hi"}],"think":false,"stream":false}'
```

---

## Step 9 — Verify After Reboot

After any reboot or power cut, verify from NAS SSH within 3-4 minutes of Max powering on:

```bash
curl http://192.168.1.50:11434/api/tags
```

If this returns the model list, Ollama started correctly and the daily brief will work.

---

## Useful Ollama Commands

```powershell
# List downloaded models
ollama list

# Pull a model
ollama pull batiai/qwen3.6-27b:q6

# Remove a model
ollama rm batiai/qwen3.6-27b:q6

# Run a model interactively
ollama run fitness-coach

# Check running models and GPU usage
ollama ps

# Check Ollama process
Get-Process ollama
```

---

## Ports

| Service | Port | Used by |
|---------|------|---------|
| Ollama API | 11434 | NAS daily-brief container, Open WebUI |

---

## Troubleshooting

**NAS can't reach Ollama:**
- Check `OLLAMA_HOST=0.0.0.0` is set in System Environment Variables (not User variables)
- Check Windows Firewall rule for port 11434 exists
- Check Ollama process is running: `Get-Process ollama`

**Ollama didn't start after reboot:**
- Check auto-login is configured: `Get-ItemProperty "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon" | Select-Object AutoAdminLogon`
- Check Task Scheduler: `Get-ScheduledTask -TaskName "OllamaAutoStart"`
- Manually trigger: `schtasks /run /tn "OllamaAutoStart"`

**First request after reboot is slow:**
- Normal — model takes 30-60 seconds to load into GPU memory on first request
- `ollama ps` shows the model loading status
- Subsequent requests are faster

**Daily brief shows AI unavailable:**
- Check Ollama is running on Max
- Check from NAS: `curl http://192.168.1.50:11434/api/tags`
- Check the `fitness-coach` model exists: `ollama list`

**Open WebUI can't connect:**
- Ensure `OLLAMA_BASE_URL: "http://192.168.1.50:11434"` in open-webui compose
- Recreate the open-webui container after changing the URL
