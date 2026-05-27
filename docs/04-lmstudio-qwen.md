# Guide 04 — LM Studio + Qwen3 on Max (MS-S1)

**Target machine:** Max — MAX_IP (Minisforum MS-S1 Max, Windows 11 Pro)

This guide configures the MS-S1 Max as your local LLM inference server using LM Studio and the Qwen3.6-27B model. With 128 GB of UMA RAM — up to 96 GB allocatable as VRAM — this machine runs Qwen3.6-27B at Q6_K quality entirely on-GPU, giving you fast, high-quality responses with zero cloud dependency.

---

## Hardware Context

The MS-S1 Max has a unified memory architecture (UMA): the CPU and the Radeon 8060S iGPU share the same physical RAM pool. By allocating a large chunk of that pool to the GPU in BIOS, LM Studio treats it as VRAM and runs the entire model on the GPU rather than splitting it across CPU+RAM, which is significantly faster.

| Config | VRAM available to GPU | RAM left for Windows + apps |
|--------|-----------------------|------------------------------|
| Default BIOS | ~512 MB – 2 GB | ~126 GB |
| Recommended | 64 GB | ~64 GB |
| Maximum | 96 GB | ~32 GB |

For Qwen3.6-27B at Q6_K (~22.5 GB), 64 GB VRAM is more than sufficient and leaves plenty of headroom. 96 GB allocation is not needed for this model but does not hurt if you want maximum future flexibility.

---

## Step 1 — BIOS: Set UMA Frame Buffer Size

1. Restart Max and press **Delete** or **F2** during POST to enter BIOS/UEFI
2. Navigate to **Advanced → AMD CBS → NBIO Common Options → GFX Configuration**
3. Find **UMA Frame Buffer Size** and set it to **64G** (or 96G if you want maximum model headroom)
4. Save and exit (F10)

Windows will restart. Check Device Manager → Display Adapters → AMD Radeon 8060S → Properties → Resources — you will see the new dedicated VRAM figure.

> **Note:** If you cannot find the exact BIOS path, search for "UMA Frame Buffer" or "iGPU Memory" in your BIOS. Minisforum has released BIOS updates for the MS-S1 Max; make sure you are on the latest version (v1.03 or later) for best stability.

---

## Step 2 — Update AMD Adrenalin Drivers

LM Studio uses ROCm/HIP on AMD GPUs via the Adrenalin driver stack. Make sure drivers are current:

1. Open **AMD Software: Adrenalin Edition**
2. Go to **Home → Check for Updates**
3. Install any available driver updates and restart

Alternatively, download the latest from: `https://www.amd.com/en/support/download/drivers.html`
Select: Processors → AMD Ryzen AI Max series → Radeon 8060S

---

## Step 3 — Install LM Studio

Download LM Studio from `https://lmstudio.ai` — select the **Windows** installer.

Run the `.exe` installer and accept defaults. LM Studio installs to `%LOCALAPPDATA%\LM-Studio`.

After installation, open LM Studio and let it complete its initial setup.

---

## Step 4 — Configure LM Studio for AMD GPU

Before downloading models, configure LM Studio to use your GPU:

1. Open LM Studio
2. Click the **Settings** gear icon (bottom left)
3. Under **Hardware**:
   - Set **GPU Offload** to **Max** (or drag the slider all the way right)
   - Confirm the detected GPU shows **AMD Radeon 8060S** with your allocated VRAM
4. Under **Server**:
   - Enable **Start server on app launch**: **On**
   - Set **Server port**: `1234`
   - Set **Cross-Origin Requests (CORS)**: **On** (needed for the NAS to call the API)
   - Set **Server bind address**: `0.0.0.0` (listens on all network interfaces, not just localhost)

---

## Step 5 — Download Qwen3.6-27B

This is the recommended model for your use case. It is a **dense 27B model** — all 27 billion parameters are active on every token, giving substantially better reasoning quality than the same-size MoE models used in earlier versions of this guide.

**Why Qwen3.6-27B:**
- Dense architecture: 27B active parameters per token (not 3B like Qwen3-30B-A3B)
- Flagship-level reasoning — 87.8 on GPQA Diamond, competitive with models several times its size
- Native tool/function calling for Caliber MCP integration
- Thinking and non-thinking modes in a single checkpoint
- 262K token context window — far more than needed for fitness data
- Multimodal (vision + text) — useful if you want to analyse activity maps or form photos later
- Apache 2.0 licence

**Recommended quantisation: Q6_K (~22.5 GB VRAM)**
With 96 GB available VRAM on the MS-S1, Q6_K gives near-lossless quality with excellent speed. Q4_K_M (~16.8 GB) is the fallback if you want maximum tokens/sec; Q8_0 (~28.6 GB) is available if you want the highest possible fidelity.

**In LM Studio:**
1. Press `Ctrl+Shift+M` to open the model search
2. Search for: `qwen3.6-27b`
3. Look for **Qwen3.6-27B-GGUF** (the Unsloth or bartowski version)
4. Select the **Q6_K** quantisation
5. Click **Download**

Download size is approximately 22.5 GB. At typical home network speeds this takes 30–75 minutes.

**Alternative quantisations on your hardware:**
- `Q4_K_M` (~16.8 GB) — faster, still very good quality
- `Q8_0` (~28.6 GB) — highest local quality, still fits easily in 64 GB VRAM allocation

---

## Step 6 — Load the model and configure inference parameters

Once downloaded:

1. Click **My Models** in the left sidebar
2. Click **Load** next to Qwen3.6-27B-Q6_K (or whichever quantisation you downloaded)
3. In the load dialog, set:
   - **Context Length**: `65536` (64K — well within the 262K native window; enough for several weeks of data + conversation)
   - **GPU Layers**: `99` (offloads all layers to GPU)
   - **Flash Attention**: **On**
4. Click **Load Model**

Wait for the model to finish loading (~30 seconds on the first load).

---

## Step 7 — Configure the Fitness Coach Preset

In this version of LM Studio, presets store the system prompt and selected custom fields (such as thinking mode). Temperature and sampling parameters have been moved out of the preset UI and use model defaults, which are well-suited for coaching conversations.

**Create the preset:**

1. In the Chat view, confirm the right sidebar is showing **Model Parameters** (click the sliders icon at the top of the right panel if not)
2. In the **Preset** field, type `Fitness-Coach` and press Enter — this names your preset
3. Under **Custom Fields**, find **Enable Thinking** and set it to **Off** (toggle grey) — this is the default for normal coaching chat
4. Click **System Prompt** to expand it — paste the system prompt from Guide 05 here
5. Click **Save**

**When to use Thinking Mode:**
- **Off** (default): daily check-ins, nutrition questions, quick coaching queries — faster responses
- **On**: race planning, interpreting complex multi-week data, designing training blocks — click the **Think** button at the bottom of the chat input to toggle it on for that session

Guide 05 provides the full system prompt to paste into the System Prompt section.

---

## Step 8 — Verify the API server

With the model loaded, open a browser on any device on your network and go to:

```
http://MAX_IP:1234/v1/models
```

You should see a JSON response listing the loaded model. This is the OpenAI-compatible endpoint that other tools on your network can call.

Test with curl from the NAS SSH session:

```bash
curl -s http://MAX_IP:1234/v1/models
```

If you see the model listed, the API is working correctly and accessible from the NAS.

---

## Step 9 — Configure LM Studio to start on boot (Windows)

To have LM Studio always available without manually opening it:

1. Press `Win+R`, type `shell:startup`, press Enter — this opens the Windows Startup folder
2. Create a shortcut to `LM Studio.exe` in that folder
3. In the shortcut properties, set **Run** to `Minimized`

LM Studio will start minimised to the system tray on Windows boot. The current version does not have a built-in autoload model feature — use the Task Scheduler approach below to load the model automatically.

---

## Step 9b — Auto-load the model on login (Windows Task Scheduler)

LM Studio ships with a CLI tool (`lms`) that can load models programmatically. First confirm it is available — open PowerShell on Max:

```powershell
lms version
```

If it returns a version number, proceed. If not found, try the full path:
```powershell
& "$env:LOCALAPPDATA\LM-Studio\lms.exe" version
```

**Create the scheduled task:**

1. Open **Task Scheduler** on Max (search Start menu)
2. Click **Create Task** (not "Create Basic Task")
3. **General tab:** Name `LM Studio Load Model` — tick **Run only when user is logged on**
4. **Triggers tab:** New → **At log on** → tick **Delay task for 1 minute** → OK
5. **Actions tab:** New → **Start a program**
   - Program/script: `lms`
   - Arguments: `load qwen/qwen3.6-27b`
   - Click OK
6. Click **OK** to save

On next login, Windows starts LM Studio via the Startup shortcut, waits 1 minute, then loads the model automatically. The API will be ready at `http://MAX_IP:1234` within a couple of minutes of logging in.

> **If `lms` is not found by Task Scheduler:** use the full path in the Program/script field — e.g. `C:\Users\Simon\AppData\Local\LM-Studio\lms.exe`

---

## Step 10 — Open Windows Firewall for LM Studio API

The NAS (NAS_IP) needs to reach LM Studio on port 1234 to generate the daily dashboard. By default Windows Firewall will block this inbound connection.

1. Open **Windows Security** → **Firewall & network protection** → **Advanced settings**
2. In the left panel, click **Inbound Rules** → **New Rule…** (right panel)
3. Select **Port** → Next
4. Select **TCP**, Specific local ports: `1234` → Next
5. Select **Allow the connection** → Next
6. Check **Domain** and **Private** only (uncheck Public) → Next
7. Name the rule: `LM Studio API (local network)` → Finish

Verify from the NAS SSH session:
```bash
curl -s http://MAX_IP:1234/v1/models
```
You should see the loaded model listed in the JSON response.

---

## Step 11 — MCP Servers

LM Studio supports MCP (Model Context Protocol) client connections, allowing the local model to call external tools. MCP servers are configured via the **Program** tab in the right sidebar → **Edit mcp.json**.

> **Why Qwen3.6-27B works for MCP:** Qwen3.6 has native tool/function calling support built into its architecture. As a dense 27B model it generates well-structured tool call JSON reliably.

> **Thinking Mode for tool calling:** Keep this **OFF** during MCP sessions — it adds latency to every tool call.

> **Caliber note:** The Caliber MCP server does not support Dynamic Client Registration, so OAuth authentication fails with both LM Studio and Claude.ai. This is not a problem — Caliber workouts recorded on the Fenix 8 sync to Garmin Connect automatically and are already captured in the `ActivitySummary` measurement in GarminStats via the garmin-direct-sync container. No separate Caliber integration is needed.

For other MCP servers that support standard OAuth or API keys, add them to `mcp.json` via the Program tab. The format follows Cursor's mcp.json notation as documented at `lmstudio.ai/docs/app/mcp`.

### Configure the Caliber MCP server

MCP servers are configured via the **Program** tab in the LM Studio chat view:

1. In the Chat view, look at the right sidebar — at the top there are two tab icons. Click the **Program** tab (terminal/code icon)
2. Click **Install → Edit mcp.json** — this opens LM Studio's MCP configuration file in the in-app editor
3. Add the Caliber server entry:

```json
{
  "mcpServers": {
    "caliber": {
      "url": "https://api.caliberstrong.com/mcp"
    }
  }
}
```

4. Save the file (Ctrl+S) — LM Studio detects the change automatically, no restart needed

### Authenticate with Caliber

1. After saving mcp.json, the Caliber server should appear in the Program tab
2. Click **Connect** next to Caliber
3. A browser window will open for the Caliber OAuth login — enter your Caliber email and password
4. Grant the requested read permissions (all 12 tools)
5. LM Studio stores the OAuth token — authentication persists across restarts

### Verify tool access

With Qwen3.6-27B loaded and the Caliber MCP server connected, open a new chat and ask:

```
Show me my last 3 Caliber workouts with exercise details, sets, reps, and weights.
```

The model will call `get_workouts` via the Caliber MCP and return your actual workout data. If it returns structured data you can read, the integration is working.

### Troubleshooting MCP connection

**Caliber server not appearing in LM Studio:**
Confirm the mcp.json is valid (no trailing commas). Open the Program tab → Edit mcp.json to check the contents. Saving the file should trigger automatic detection — if not, restart LM Studio.

**OAuth window does not open:**
Ensure your default browser is set and not blocked. Try clicking Connect again — LM Studio will re-initiate the OAuth flow.

**Model calls tools but returns empty results:**
Your Caliber account may have no data in the requested date range. Try asking for a broader range: "Show me all workouts from the last 30 days."

**Tool calls fail silently:**
Check that Thinking Mode is OFF — some model configurations with thinking enabled can interfere with tool call generation. Also confirm the model is fully loaded (not in a degraded state from a previous context overflow).

---

## Performance Expectations

On the MS-S1 Max with 64 GB VRAM allocated and Qwen3.6-27B Q6_K:

| Metric | Expected |
|--------|----------|
| Time to first token | 2–4 seconds |
| Generation speed | 20–25 tokens/sec |
| Context length (practical) | 64K tokens (262K native maximum) |
| Memory used | ~22–24 GB VRAM |

For comparison, a 7-day summary of Garmin + Cronometer data is roughly 2,000–4,000 tokens — well within the context window. The 262K native context means you could feed months of data if needed.

---

## Troubleshooting

**GPU not detected by LM Studio:**
Confirm AMD Adrenalin drivers are installed and up to date. Check that the GPU appears in Device Manager with the correct VRAM allocation.

**Model loads but runs slowly (only ~2 tokens/sec):**
This means layers are being run on CPU instead of GPU. Go to LM Studio Settings → Hardware and ensure GPU offload is set to Max. Also confirm your BIOS VRAM allocation is large enough for the model.

**API not reachable from other machines on the network:**
Confirm the Windows Firewall rule was added in Step 10. Check that you created the rule under Inbound Rules (not Outbound) and that the Domain and Private network profiles are checked.

**Out of memory when loading:**
Drop to Q4_K_M (~16.8 GB) first. If still an issue, reduce Context Length from 65536 to 32768. If you have set BIOS VRAM allocation below 32 GB, increase it to 64 GB as per Step 1.

---

**Next step → Guide 05: AI fitness assistant setup and prompting**  
**See also → Caliber_MCP_Integration_Guide.md: Full Caliber data export workflow using LM Studio**

---

## Disabling Thinking Mode (Required)

Qwen3.6-27B has a built-in "thinking" mode that generates extended reasoning before answering. For the daily brief this is wasteful — it uses 1,000+ tokens for reasoning and the actual response is empty until thinking completes (2+ minutes).

**Disable thinking mode via Jinja template (most reliable):**

1. In LM Studio → My Models → click the gear icon (⚙) on Qwen3.6 27B
2. Click the **Inference** tab
3. Scroll down to **Prompt Template** → click **Template (Jinja)**
4. Add this as the **very first line** of the template:
   ```
   {%- set enable_thinking = false %}
   ```
5. Save

**Disable thinking mode in the chat UI:**

1. In LM Studio chat, look at the bottom of the input box
2. The **Think** button should be grey/inactive (not highlighted blue)
3. In the right panel → **Custom Fields** → **Enable Thinking** should be toggled off

**Verify thinking is disabled:**

```bash
curl -s http://MAX_IP:1234/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"qwen/qwen3.6-27b","messages":[{"role":"user","content":"Say hi"}],"max_tokens":20,"chat_template_kwargs":{"enable_thinking":false}}'
```

Correct response: `content` has actual text, `reasoning_tokens: 0`.

The daily brief script also passes `"chat_template_kwargs": {"enable_thinking": false}` in every API call as a belt-and-braces measure.

---

## API Verification

Verify the LM Studio API is working from the NAS:

```bash
curl -s http://MAX_IP:1234/v1/models
```

Expected response includes `"id": "qwen/qwen3.6-27b"`. Note: the root URL `/` returns an error — always use `/v1/models` or `/v1/chat/completions`.

The model ID in API calls must be `qwen/qwen3.6-27b` (with the `qwen/` prefix), matching exactly what `/v1/models` returns.
