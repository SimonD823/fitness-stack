# Caliber MCP Integration Guide

> **Status: Pending OAuth resolution.** Caliber's Keycloak OAuth server requires a pre-registered redirect URI that we do not yet have. Until Caliber support provides this, OAuth authentication will fail with any MCP client. See Section 3 for the support email template.

> **Current workaround:** Strength data (set/rep/weight detail) is captured automatically via `garmin_direct_sync.py` → GarminStats.StrengthSets. This covers sessions recorded on the Fenix 8 but lacks Caliber-specific metadata (RPE, planned vs actual, programme structure, exercise notes).

---

## Why Caliber MCP Is Preferred Over Garmin Route

| Factor | Garmin Route (current) | Caliber MCP (target) |
|--------|----------------------|---------------------|
| Data source | Indirect — Caliber → Fenix 8 → Garmin Connect → API | Direct from Caliber |
| Missing sessions | Any session not recorded on Fenix 8 is lost | All sessions regardless of watch |
| Caliber metadata | RPE, notes, planned sets lost in transit | Full access |
| Historical data | Only since garmin_direct_sync started | Full Caliber history |
| Programme context | None | Planned programme vs actual |
| Reliability | 3 failure points | 1 failure point |

---

## Section 1 — What We Know About the Caliber MCP Server

**MCP server URL:** `https://api.caliberstrong.com/mcp`

**Client ID:** `caliber-mcp`

**Auth type:** Keycloak OAuth 2.0 with PKCE

**Known OAuth endpoints (discovered via .well-known):**
- Authorization: `https://auth.caliberstrong.com/realms/caliber/protocol/openid-connect/auth`
- Token: `https://auth.caliberstrong.com/realms/caliber/protocol/openid-connect/token`

**The blocker:** Keycloak validates the `redirect_uri` parameter against a whitelist registered for the `caliber-mcp` client. Any URI not on that list returns `Invalid parameter: redirect_uri`. We do not know what URIs are registered.

**What we have tried:**
- LM Studio MCP client — rejected (Dynamic Client Registration not supported)
- Claude.ai connector — rejected (redirect URI not registered)
- mcp-remote proxy — rejected (redirect URI not registered)
- Docker MCP Gateway — wrong tool (designed for Docker's own catalog)
- Python script with various redirect URIs — all rejected

---

## Section 2 — How to Test When the Redirect URI Is Known

Once Caliber support provides the registered redirect URI, test in this order:

### Step 1 — Verify the redirect URI manually

Open this URL in a browser (replace `REDIRECT_URI` with the confirmed value):

```
https://auth.caliberstrong.com/realms/caliber/protocol/openid-connect/auth?client_id=caliber-mcp&response_type=code&redirect_uri=REDIRECT_URI&scope=openid
```

If the redirect URI is valid, Garmin's login page will appear. If invalid, you'll see "Invalid parameter: redirect_uri" immediately — try the next URI.

### Step 2 — Test via Claude.ai web UI

1. Go to `https://claude.ai` and sign in
2. Click your profile → **Settings → Integrations** (or **Connectors**)
3. Click **Add integration** or **Connect MCP server**
4. Enter the server URL: `https://api.caliberstrong.com/mcp`
5. Complete the OAuth flow when prompted
6. If authentication succeeds, test with: *"Show me my last 3 Caliber workouts"*

Claude.ai's OAuth redirect URI is likely `https://claude.ai/oauth/callback` — this needs to be registered by Caliber.

### Step 3 — Test via LM Studio (if Claude.ai fails)

In LM Studio on Max:
1. Open **Program tab** → **Edit mcp.json**
2. Add:
```json
{
  "mcpServers": {
    "caliber": {
      "type": "url",
      "url": "https://api.caliberstrong.com/mcp",
      "name": "Caliber"
    }
  }
}
```
3. Restart LM Studio
4. Go to **Developer → MCP Servers** → Connect Caliber
5. Complete OAuth flow

LM Studio's redirect URI is typically `http://localhost:PORT/callback` — the exact port varies and needs to be registered by Caliber.

### Step 4 — Test via Python script (most controllable)

If you have the exact registered redirect URI, use this script on Max to complete the OAuth flow manually and get an access token:

```python
# Save as C:\AdaptiveTraining\caliber_oauth_test.py
import webbrowser
import urllib.parse
import http.server
import threading
import secrets

CLIENT_ID    = "caliber-mcp"
REDIRECT_URI = "REPLACE_WITH_REGISTERED_URI"
AUTH_URL     = "https://auth.caliberstrong.com/realms/caliber/protocol/openid-connect/auth"

state = secrets.token_urlsafe(16)
code_verifier = secrets.token_urlsafe(32)

params = {
    "client_id": CLIENT_ID,
    "response_type": "code",
    "redirect_uri": REDIRECT_URI,
    "scope": "openid",
    "state": state,
}

auth_url = AUTH_URL + "?" + urllib.parse.urlencode(params)
print(f"Opening browser to: {auth_url}")
webbrowser.open(auth_url)
print("Complete login in browser, then paste the 'code' parameter from the redirect URL here:")
code = input("Auth code: ").strip()
print(f"Got code: {code}")
print("Next step: exchange this code for a token")
```

Run:
```powershell
cd C:\AdaptiveTraining
C:\Users\Simon\AppData\Local\Programs\Python\Python311\python.exe caliber_oauth_test.py
```

---

## Section 3 — Support Email Template

Send this to Caliber support if they haven't responded to the first email:

---

**Subject: Developer question — OAuth redirect URIs for caliber-mcp client**

Hi Caliber support,

I'm trying to connect to the Caliber MCP server (`https://api.caliberstrong.com/mcp`) using third-party MCP clients including Claude.ai and LM Studio.

The OAuth flow fails at the authorization step with "Invalid parameter: redirect_uri" regardless of which redirect URI I use. I believe this is because the `caliber-mcp` OAuth client in your Keycloak instance only allows specific pre-registered redirect URIs.

Could you please tell me:
1. What redirect URIs are currently registered for the `caliber-mcp` client?
2. Is there a way to request additional redirect URIs be added (e.g. `https://claude.ai/oauth/callback` for Claude.ai, or `http://localhost:*/callback` for local tools)?
3. Is there developer documentation for the Caliber MCP server?

I'm a paying Caliber subscriber trying to use my workout data with AI coaching tools. Any help would be greatly appreciated.

Thank you

---

## Section 4 — If Caliber MCP Never Becomes Available

The garmin_direct_sync.py approach is a solid permanent fallback:

- All sessions **must** be recorded as structured workouts on the Fenix 8
- Exercises, sets, reps, and weights entered on the watch or edited on Garmin Connect website are captured
- Data lands in `GarminStats.StrengthSets` within 30 minutes of the next sync cycle
- The daily brief AI already uses this data for coaching

The only data permanently lost via this route is Caliber-specific metadata that doesn't sync to Garmin: RPE ratings, workout notes, planned programme structure.
