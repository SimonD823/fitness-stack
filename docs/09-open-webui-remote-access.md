# Guide 09 — Open WebUI & Remote Access

This guide covers deploying Open WebUI on the NAS for LAN chat access, adding it as a home screen shortcut on iPhone/iPad, and enabling remote access via Tailscale.

---

## What Is Open WebUI?

Open WebUI is a self-hosted web interface for local LLMs — essentially a ChatGPT-style chat UI that connects to Ollama's API on Max. Once deployed on the NAS, any device on your local network (iPhone, iPad, other PCs) can chat with the `fitness-coach` model through a browser without needing anything installed on that device.

> **Note:** This guide originally documented LM Studio (port 1234) as the backend. Ollama has since replaced LM Studio entirely (see Guide 04) — Open WebUI now points at Ollama on port 11434. If you still see references to LM Studio or port 1234 anywhere, they're stale.

---

## Architecture

```
Remote (iPhone/iPad)
    │
    └── Tailscale VPN
            │
            └── NAS — always on (100.x.x.x Tailscale IP)
                  ├── Open WebUI :3001 ──► Max :11434 (Ollama — fitness-coach model)
                  ├── Grafana :3000
                  └── InfluxDB :8086
```

Max only needs to be powered on when you want to use the LLM. The NAS runs 24/7 and handles all remote access.

---

## Step 1 — Create Folder on NAS

Via Windows Explorer, create:
```
\\nas\Container\open-webui\
\\nas\Container\open-webui\data\
```

---

## Step 2 — Deploy Open WebUI Container

In Container Station → **Applications → Create**, paste:

```yaml
version: "3.8"
services:
  open-webui:
    image: ghcr.io/open-webui/open-webui:main
    container_name: open-webui
    restart: unless-stopped
    environment:
      OPENAI_API_BASE_URL: "http://192.168.1.50:11434/v1"
      OPENAI_API_KEY: "not-needed"
      WEBUI_AUTH: "false"
    volumes:
      - /share/Container/open-webui/data:/app/backend/data
    ports:
      - "3001:8080"
```

Click **Create**. Wait 2-3 minutes for the ~1GB image to download.

> **Note:** `WEBUI_AUTH: "false"` disables login — anyone on your network can use it. This is fine for a private home network. If you want login protection, remove that line and create an admin account on first launch.

---

## Step 3 — Verify on Local Network

Open in browser:
```
http://nas:3001
```
or
```
http://NAS_IP:3001
```

---

## Step 4 — Connect to Ollama

1. Log in to Open WebUI (if auth is enabled)
2. Click your profile icon → **Admin Panel → Settings → Connections**
3. Confirm OpenAI API URL shows `http://192.168.1.50:11434/v1`
4. Click **Verify connection** — requires Ollama running on Max with `fitness-coach` pulled

If the connection fails check:
- Ollama is running on Max: `Get-Process ollama`
- `OLLAMA_HOST=0.0.0.0` is set in System (not User) environment variables on Max
- Windows Firewall rule for port 11434 exists on Max
- From NAS SSH: `curl http://192.168.1.50:11434/api/tags` returns the model list

---

## Step 5 — Select the Fitness Coach Model

No separate system prompt setup is needed here — the `fitness-coach` Ollama model already has the system prompt baked in via its Modelfile (Guide 04, Step 6), and `system_prompt.txt` is the single canonical copy of that prompt.

1. In Open WebUI, open the model dropdown at the top of a new chat
2. Select `fitness-coach` (not the base `batiai/qwen3.6-27b:q6` model — that has no system prompt attached)
3. Start chatting

If you need to change the coaching behaviour, edit `system_prompt.txt` and recreate the `fitness-coach` model on Max (`ollama create fitness-coach -f Modelfile`) rather than pasting a prompt into Open WebUI directly — that avoids the prompt drifting out of sync between the two.

---

## Step 6 — Install Tailscale on NAS

Tailscale is already installed on the NAS. It provides a stable private IP address for remote access without exposing any ports to the internet.

**Your NAS Tailscale IP:** stored privately — do not commit to GitHub.

To find it at any time:
- Open the Tailscale app on the NAS
- Or check `https://login.tailscale.com/admin/machines`

---

## Step 7 — Install Tailscale on iPhone/iPad

1. Install **Tailscale** from the App Store
2. Sign in with the same Tailscale account used on the NAS
3. When away from home, enable Tailscale to access NAS services remotely

Remote URLs (replace `your-nas-tailscale-ip` with your actual Tailscale IP):

| Service | Remote URL |
|---------|-----------|
| Open WebUI | `http://your-nas-tailscale-ip:3001` |
| Grafana | `http://your-nas-tailscale-ip:3000` |
| InfluxDB API | `http://your-nas-tailscale-ip:8086` |

---

## Step 8 — Add to iPhone/iPad Home Screen

**At home (local network):**
1. Open Safari and go to `http://nas:3001` or `http://NAS_IP:3001`

**Away from home (via Tailscale):**
1. Enable Tailscale on iPhone
2. Open Safari and go to `http://your-nas-tailscale-ip:3001`

**Add to home screen:**
1. Tap the **Share** button (box with arrow)
2. Tap **Add to Home Screen**
3. Name it `AI Chat`
4. Tap **Add**

> **Tip:** Create two shortcuts — one using the local IP (faster at home) and one using the Tailscale IP (works anywhere).

---

## Keeping Open WebUI Updated

In Container Station, stop and delete the `open-webui` application, then recreate it with the same compose YAML. The `/share/Container/open-webui/data` volume preserves all settings and chat history across updates.

---

## Ports Summary

| Service | Local URL | Tailscale Remote URL |
|---------|-----------|---------------------|
| Open WebUI | `http://nas:3001` | `http://your-nas-tailscale-ip:3001` |
| Grafana | `http://nas:3000` | `http://your-nas-tailscale-ip:3000` |
| InfluxDB | `http://nas:8086` | `http://your-nas-tailscale-ip:8086` |

---

## Troubleshooting

**Open WebUI won't load:**
- Check Container Station — is `open-webui` running?
- Try the IP address directly: `http://NAS_IP:3001`

**"Connection failed" to Ollama:**
- Is Max powered on and logged in (auto-login required for Ollama's Task Scheduler trigger to fire)?
- Is Ollama running? `Get-Process ollama` on Max
- Check `OLLAMA_HOST=0.0.0.0` is set in System environment variables, not User
- Check Windows Firewall on Max allows port 11434
- From NAS SSH: `curl http://192.168.1.50:11434/api/tags` should list `fitness-coach:latest`

**Tailscale not connecting remotely:**
- Is Tailscale running on the NAS? Check App Center
- Is Tailscale active on your iPhone? Check the VPN icon in status bar
- Are both devices signed into the same Tailscale account?

**Slow responses:**
- Max must be powered on and not sleeping for LLM inference
- First request after a reboot or idle period takes 30-60 seconds while the model loads into GPU memory — normal, `ollama ps` shows load status
- For quick responses, ensure Ollama's `think` mode is off (`"think": false` in the API payload) — see Guide 04
