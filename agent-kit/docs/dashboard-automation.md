# Netlify Dashboard Automation — Agent Skill

> **Load this file when you need to automate Netlify operations that the public API (PAT) cannot do.**
> This covers the internal bb-api (Backend-For-Frontend) that the dashboard uses, accessible via session cookies.

## ⚠️ CRITICAL: Project visibility gates function access

**If project visibility is "private" (SSO enabled), your function URLs will return `HTTP 401` and redirect to `app.netlify.com/edge-access`. No client — including curl, other services, or your own code — can invoke the function.**

This is the #1 reason functions fail after deploy. Check and fix it FIRST:

```bash
# Check current visibility
node tools/netlify-dashboard-api.mjs get-site <site_id>
# Look for: sso_login=true → BAD (functions blocked)
# Look for: sso_login=false → GOOD (functions accessible)

# Fix it (if sso_login=true)
node tools/netlify-dashboard-api.mjs disable-sso <site_id>
# Response should show: sso_login: false
```

After disabling SSO, ALL deploy URLs (preview AND production) become publicly accessible without login. New deploys inherit this setting automatically.

**Why this matters:** New Netlify accounts (post-Aug 2026) have "Private by default" enabled. Every new site starts with SSO ON. Without disabling it, your function is deployed but unreachable. The PAT-based public API (`PATCH /api/v1/sites/{id}`) CANNOT disable SSO — it returns `null` for the `sso_login` field. Only the bb-api (cookie-auth) can do this.

## When to use this

Use the dashboard automation tool when you need to:
- **Disable/enable SSO** on a site (PAT can't do this — `PATCH /api/v1/sites/{id}` returns null)
- **Read real-time bandwidth usage** (PAT lags 5-30 min; bb-api is near-real-time)
- **Read function observability data** (invocation counts, status codes, timeseries)
- **List blob stores** on a site
- **Create build hooks** programmatically
- **List snippets, redirects, deployed branches**
- **Read audit logs**
- **Set environment variables** (alternative to `netlify env:set` CLI)
- **Cancel deploys** (alternative to `POST /api/v1/deploys/{id}/cancel`)

Use the **public API** (PAT) for everything else:
- List/create/delete sites
- Create deploys, upload files
- Read blobs
- List functions
- Invoke functions

## Auth model — NO WAF, NO browser needed

### WAF test results (verified)

| Test | Headers sent | Result |
|---|---|---|
| Plain curl + Cookie only (no UA, no Origin, no Referer) | `Cookie: _nf-auth=nfu_...` | ✅ HTTP 200 — works |
| Cookie + User-Agent (no Origin/Referer) | `Cookie` + `User-Agent` | ✅ HTTP 200 — works |
| Full browser headers (Cookie + Origin + Referer + Sec-Fetch) | All browser headers | ✅ HTTP 200 — works |
| No cookie at all | (none) | ❌ HTTP 401 "Access Denied" |
| Only `connect.sid` (no `_nf-auth`) | `Cookie: connect.sid=...` | ❌ HTTP 401 "Access Denied" |
| Only `_nf-auth` (no `connect.sid`) | `Cookie: _nf-auth=nfu_...` | ✅ HTTP 200 — **this is all you need** |
| PUT (write) with plain curl | `Cookie` + `Content-Type` | ✅ HTTP 200 — writes work too |

**Conclusion: NO WAF, NO Playwright/browser required.** A plain `curl` with just the `_nf-auth` cookie works for both GET and PUT. The `_nf-auth` cookie alone (without `connect.sid` or any other cookies) is sufficient for authentication.

### What you actually need

```
Cookie: _nf-auth=nfu_xxxxxxxxxxxxxxxxxxxxxxx
```

That's it. No `User-Agent`, no `Origin`, no `Referer`, no `Sec-Fetch-*` headers, no `connect.sid`.

### Cookie extraction (simplified)

Since only `_nf-auth` is needed:
1. Open `https://app.netlify.com` → login
2. DevTools → Application → Cookies → `app.netlify.com`
3. Find `_nf-auth` → copy its value (starts with `nfu_`)
4. Use in curl: `curl -H "Cookie: _nf-auth=nfu_xxx" https://app.netlify.com/access-control/bb-api/api/v1/...`

Or save to file for the tool:
```bash
echo "_nf-auth=nfu_xxxxxxxxxxxxxxxxxxxxxxx" > /tmp/netlify-cookies.txt
```

## Cookie extraction instructions

1. Open `https://app.netlify.com` in your browser
2. Login (GitHub OAuth or email)
3. Open DevTools (F12) → Application tab → Cookies → `https://app.netlify.com`
4. Copy ALL cookie key=value pairs
5. Save to a file (one per line: `key=value`)

Or export from a HAR file:
```python
import json
with open('har_file.har') as f:
    har = json.load(f)
for entry in har['log']['entries']:
    if 'bb-api' in entry['request']['url']:
        for h in entry['request']['headers']:
            if h['name'].lower() == 'cookie':
                print(h['value'])  # This is the full Cookie header
        break
```

## Tool usage

```bash
# Set cookie file location (default: /tmp/netlify-cookies.txt)
export NETLIFY_COOKIE_FILE=/path/to/cookies.txt

# Run any command
node tools/netlify-dashboard-api.mjs <command> [args]
```

## Complete endpoint reference

### Account-level endpoints

| Command | Method | Path | Description |
|---|---|---|---|
| `list-accounts` | GET | `/accounts` | List all teams with capabilities, credits, sites count |
| `get-account <id>` | GET | `/accounts/{id}` | Full account details (capabilities, credits, SSO settings) |
| `get-bandwidth <id>` | GET | `/accounts/{id}/bandwidth` | Real-time bandwidth usage (bytes used, period) |
| `get-build-status <slug>` | GET | `/{slug}/builds/status` | Build queue (active, enqueued, pending_concurrency) |
| `audit-log <id>` | GET | `/accounts/{id}/audit` | Audit log entries (actor, action, resource) |
| `raw GET` | GET | `/accounts/{id}/edge_functions` | Edge function usage stats |
| `raw GET` | GET | `/accounts/{id}/compliance` | Compliance settings (private corp repos) |
| `raw GET` | GET | `/accounts/{id}/plans` | Available plans for this account |
| `raw GET` | GET | `/accounts/{id}/env` | Account-level env vars |
| `raw PUT` | PUT | `/accounts/{id}` | Update account settings (SSO, etc.) |

### Site-level endpoints

| Command | Method | Path | Description |
|---|---|---|---|
| `list-sites` | GET | `/sites` | List all sites across all teams |
| `list-sites <slug>` | GET | `/{slug}/sites` | List sites in a specific team |
| `get-site <id>` | GET | `/sites/{id}` | Full site details (SSO, password, functions region, build image) |
| `get-env <id>` | GET | `/sites/{id}/env` | Site environment variables |
| `set-env <id> <k> <v>` | PUT | `/sites/{id}/env/{key}` | Set environment variable |
| `disable-sso <id>` | PUT | `/sites/{id}` | Disable SSO (set `sso_login: false`) |
| `enable-sso <id>` | PUT | `/sites/{id}` | Enable SSO (set `sso_login: true`) |
| `list-build-hooks <id>` | GET | `/sites/{id}/build_hooks` | List build hooks |
| `create-build-hook <id> <title> [branch]` | POST | `/sites/{id}/build_hooks` | Create build hook |
| `list-deploys <id> [n]` | GET | `/sites/{id}/deploys?per_page=n` | List recent deploys |
| `cancel-deploy <deploy_id>` | POST | `/deploys/{id}/cancel` | Cancel a deploy |
| `list-functions <id>` | GET | `/sites/{id}/functions` | List deployed functions (name, memory, region, runtime, schedule) |
| `get-usage <id>` | GET | `/sites/{id}/usage` | Site usage breakdown (per service) |
| `list-snippets <id>` | GET | `/sites/{id}/snippets` | List site snippets |
| `list-blobs <id>` | GET | `/sites/{id}/blobs` | List blob stores and contents |
| `raw GET` | GET | `/sites/{id}/deployed-branches` | List deployed branches |
| `raw GET` | GET | `/sites/{id}/forms` | List forms |
| `raw GET` | GET | `/sites/{id}/submissions` | List form submissions |
| `raw GET` | GET | `/sites/{id}/insights` | Site insights/analytics |
| `raw GET` | GET | `/sites/{id}/traffic_splits` | A/B testing splits |
| `raw GET` | GET | `/sites/{id}/dev_servers/active` | Active dev servers |
| `raw GET` | GET | `/sites/{id}/dns` | DNS records |
| `raw POST` | POST | `/sites/{id}/snippets` | Create snippet |
| `raw POST` | POST | `/sites/{id}/deploys` | Create deploy (preview) |
| `raw POST` | POST | `/sites/{id}/build_hooks` | Create build hook |

### Observability endpoints

| Command | Method | Path | Description |
|---|---|---|---|
| `get-observability <id> counts` | POST | `/sites/{id}/observability/query/counts?from_ts=...&to_ts=...` | Count metrics (status codes, methods, content types, function names) |
| `get-observability <id> timeseries` | POST | `/sites/{id}/observability/query/timeseries?from_ts=...&to_ts=...&interval=60000` | Time series (edge_requests_count, edge_requests_bandwidth) |

### Other endpoints (spark-proxy, app-api)

| Method | Path | Description |
|---|---|---|
| GET | `/spark-proxy/api/v1/knowledge/` | Knowledge base for agent runners |
| GET | `/spark-proxy/api/prompt-templates/team/{id}` | Prompt templates for agent runners |
| GET | `/api/agent-runners/status` | Agent runner status (claude, codex, gemini) |

## Key differences from public API

| Operation | Public API (PAT) | bb-api (cookies) |
|---|---|---|
| Disable SSO | ❌ Returns null | ✅ Works (set `sso_login: false`) |
| Real-time bandwidth | ❌ Lags 5-30 min | ✅ Near-real-time |
| Observability data | ❌ Not available | ✅ Full query API |
| List blob stores | ❌ Not available | ✅ `GET /sites/{id}/blobs` |
| Read audit log | ❌ Not available | ✅ `GET /accounts/{id}/audit` |
| Set env vars | ✅ Works | ✅ Alternative path |
| List deploys | ✅ Works | ✅ Also works |
| Cancel deploy | ✅ Works | ✅ Also works |

## The SSO disable recipe (most important)

```bash
# Disable SSO (make function URLs public)
node tools/netlify-dashboard-api.mjs disable-sso <site_id>

# Verify
node tools/netlify-dashboard-api.mjs get-site <site_id>
# Should show: sso_login: false, account_sso_login: false
```

**Request body (for manual curl):**
```json
{
  "password": "",
  "password_context": "all",
  "sso_login": false,
  "sso_login_context": "all"
}
```

**Critical field:** `sso_login: false` (boolean, NOT the context string). The `sso_login_context` stays `"all"` but is no-op once `sso_login` is `false`.

## Valid enum values for `sso_login_context`

| Value | Behavior |
|---|---|
| `non_production` | Previews public, production private |
| `all` | Everything private (default) |
| `disabled`, `none`, `off`, `production_only` | ❌ Rejected (422 "is not included in the list") |

## Raw API call (power user)

For any endpoint not covered by a named command:
```bash
# GET request
node tools/netlify-dashboard-api.mjs raw GET /accounts/6a7e84d51cdeff620a5cf5a0/bandwidth

# PUT request with body
node tools/netlify-dashboard-api.mjs raw PUT /sites/01c2e47f-3ff6-4e09-b45f-604c49ef90fe '{"sso_login":false,"sso_login_context":"all","password":"","password_context":"all"}'

# POST request
node tools/netlify-dashboard-api.mjs raw POST /sites/01c2e47f-3ff6-4e09-b45f-604c49ef90fe/build_hooks '{"title":"my-hook","branch":"main"}'
```

## Cookie refresh strategy

The `_nf-auth` JWT cookie expires. There's no known API to refresh it via PAT. Options:

1. **Manual refresh:** Re-extract from browser when the tool returns 401
2. **Playwright automation:** Use Playwright to login via GitHub OAuth, then extract cookies automatically (requires GitHub credentials)
3. **HAR file rotation:** Periodically capture a new HAR from the dashboard and extract cookies

## Using with Playwright (for fully automated cookie refresh)

```javascript
import { chromium } from 'playwright';

async function refreshCookies(githubEmail, githubPassword) {
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext();
  const page = await context.newPage();

  // Navigate to Netlify login
  await page.goto('https://app.netlify.com');
  await page.click('text=Log in with GitHub');

  // Fill GitHub login
  await page.fill('#login_field', githubEmail);
  await page.fill('#password', githubPassword);
  await page.click('[name="commit"]');

  // Wait for redirect to Netlify dashboard
  await page.waitForURL('https://app.netlify.com/**');

  // Extract cookies
  const cookies = await context.cookies();
  const cookieHeader = cookies
    .filter(c => c.domain.includes('netlify.com'))
    .map(c => `${c.name}=${c.value}`)
    .join('; ');

  await browser.close();
  return cookieHeader;
}
```

## Limitations

1. **Cookies expire** — no PAT-to-cookie exchange exists
2. **Account-level SSO can't be changed on Free plan** — returns `422 "Account is not eligible to update global access controls"`
3. **Observability requires the site to have had traffic** — empty results for new sites
4. **Some endpoints may change** — bb-api is internal and undocumented; subject to breaking changes without notice
5. **Rate limits not documented** — no known rate limit on bb-api, but abuse may trigger account suspension
