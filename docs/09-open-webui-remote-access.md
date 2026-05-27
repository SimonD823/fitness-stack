# Guide 09 — Open WebUI & Remote Access

This guide covers deploying Open WebUI on the NAS for LAN chat access, adding it as a home screen shortcut on iPhone/iPad, and enabling remote access via Tailscale.

---

## What Is Open WebUI?

Open WebUI is a self-hosted web interface for local LLMs — essentially a ChatGPT-style chat UI that connects to LM Studio's API on Max. Once deployed on the NAS, any device on your local network (iPhone, iPad, other PCs) can chat with Qwen3.6-27B through a browser without needing LM Studio installed on that device.

---

## Architecture

```
Remote (iPhone/iPad)
    │
    └── Tailscale VPN
            │
            └── NAS — always on (100.x.x.x Tailscale IP)
                  ├── Open WebUI :3001 ──► Max :1234 (LM Studio)
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
      OPENAI_API_BASE_URL: "http://max:1234/v1"
      OPENAI_API_KEY: "dummy"
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

## Step 4 — Connect to LM Studio

1. Log in to Open WebUI (if auth is enabled)
2. Click your profile icon → **Admin Panel → Settings → Connections**
3. Confirm OpenAI API URL shows `http://max:1234/v1`
4. Click **Verify connection** — requires LM Studio running on Max with Qwen3.6-27B loaded

If the connection fails check:
- LM Studio API server is running (green indicator in LM Studio)
- LM Studio is listening on `0.0.0.0:1234` not `127.0.0.1:1234` (Developer → Local Server)
- Windows Firewall rule for port 1234 exists on Max

---

## Step 5 — Add Fitness Coach System Prompt

1. In Open WebUI → **Workspace → Modelfiles**
2. Click **New Modelfile**
3. Name it `Fitness Coach`
4. Paste the contents of `docs/system_prompt.txt` into the System Prompt field
5. Save

Select `Fitness Coach` from the model dropdown before starting a coaching chat session.

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

**"Connection failed" to LM Studio:**
- Is Max powered on?
- Is LM Studio running with Qwen3.6-27B loaded?
- Is the API server started in LM Studio (green indicator)?
- Check LM Studio listens on `0.0.0.0` not `127.0.0.1`
- Check Windows Firewall on Max allows port 1234

**Tailscale not connecting remotely:**
- Is Tailscale running on the NAS? Check App Center
- Is Tailscale active on your iPhone? Check the VPN icon in status bar
- Are both devices signed into the same Tailscale account?

**Slow responses:**
- Max must be powered on and not sleeping for LLM inference
- Qwen3.6-27B takes 20-60 seconds to generate a full response depending on length
- Thinking mode must be disabled in LM Studio (see Guide 04)
