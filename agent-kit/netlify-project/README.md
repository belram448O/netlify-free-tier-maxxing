# Netlify Free-Tier Scraper — Production Implementation

A production-ready scraper toolkit that maximizes Netlify's free tier (post-Sep 2025 credit-based pricing).

## Two production services

### 1. Download Service (`/api/download`)

A queued download service with robust status tracking. Handles both short (<30s) and long-running (>30s, up to 15 min) downloads.

**Endpoints:**

| Method | Path | Description |
|---|---|---|
| POST | `/api/download?url=<url>[&async=1][&method=fetch\|chrome_impersonate][&ua=...][&timeout=N]` | Queue a download |
| GET | `/api/download?job_id=<id>` | Get current status |
| GET | `/api/download?job_id=<id>&result=1` | Retrieve downloaded data (raw passthrough) |
| GET | `/api/download?list=1[&status=pending\|complete\|error]` | List jobs |
| DELETE | `/api/download?job_id=<id>` | Cancel a pending job |

**Modes:**

- **Sync mode** (`async=0`, default): Function downloads inline (up to 30s Lambda limit). Returns final status + blob_key. Best for small files.
- **Async mode** (`async=1`): Function queues job to Blobs, returns immediately with `status=pending` + `job_id`. Client polls status until `complete`, then fetches result. Best for large files.

**Long-running downloads (the key architecture):**

Async jobs are processed by the `process-queue` build plugin, which runs in the build process (up to 15 min compute, 0 credits per preview deploy). Triggered on every preview deploy — so a GitHub Actions cron that triggers preview deploys every N minutes will process the queue.

**State machine:**
```
pending → downloading → complete
                    ↘ error
                    ↘ cancelled
```

**Retry logic:** Failed jobs retry up to 3 times (with `attempts` counter incremented in the queue blob). Stale `downloading` jobs (>10 min old) are automatically requeued.

**Storage layout** (Blobs, store=`download-jobs`):
- `queue/pending/{job_id}` — Job spec (URL, method, UA, attempts)
- `status/{job_id}` — Current status JSON
- `result/{job_id}` — Raw downloaded bytes (with metadata)
- `index/latest` — Pointer to most recent job

**Logs (auditable via `netlify logs --json --function download`):**
- `JOB_QUEUED` — job created
- `JOB_START` — download started
- `JOB_COMPLETE` — download finished (includes size, ms, blob_key)
- `JOB_ERROR` — error occurred (includes message, attempts)
- `JOB_CANCELLED` — job cancelled by user
- `JOB_RETRY` — job requeued for retry

### 2. API/Request Proxy (`/api/proxy`)

A configurable HTTP proxy for scraping / request forwarding / CORS bypass. Supports all HTTP methods, custom headers, TLS impersonation, and 3 response modes.

**Endpoints:**

| Method | Path | Description |
|---|---|---|
| ANY | `/api/proxy?url=<url>[&mode=direct\|blob\|metadata][&method=fetch\|chrome_impersonate][&ua=...][&timeout=N][&follow_redirects=0][&h_<header>=<value>]` | Proxy a request |
| GET | `/api/proxy?blob_key=<key>` | Retrieve a previously stored blob (from mode=blob) |
| GET | `/api/proxy?list=1[&limit=N]` | List recent proxy results |

**Response modes:**

| Mode | Behavior | Cost | Best for |
|---|---|---|---|
| `direct` (default) | Returns response body inline (passthrough) | 20 cr/GB egress | Small responses, low-latency |
| `blob` | Stores response in blob, returns metadata + `blob_key` | 0 credits | Large responses, async retrieval |
| `metadata` | Returns ONLY headers + TLS fingerprint, discards body | ~0 | HEAD-like inspection, JA3 verification |

**TLS methods:**

| Method | JA3 hash | When to use |
|---|---|---|
| `fetch` (default) | `1808993db60a053eb8ce0eb1c51750d6` (Node) | Unprotected sites |
| `chrome_impersonate` | `947eccbc4e2adea862cd37bf77342106` (Chrome-like) | JA3-aware bot-protected sites |

Note: `chrome_impersonate` only supports GET/HEAD. Other methods automatically fall back to `fetch`.

**Custom headers:** Pass as query params prefixed with `h_`. Examples:
- `&h_authorization=Bearer+xxx`
- `&h_x_api_key=abc123`
- `&h_accept=application/json`

## Quick start

```bash
# 1. Install Netlify CLI
npm install -g netlify-cli

# 2. Authenticate
netlify login  # OR set NETLIFY_AUTH_TOKEN env var with a PAT

# 3. Link this folder to a Netlify site (or create a new one)
netlify link

# 4. Install functions dependencies
cd functions && npm install && cd ..

# 5. Deploy as PREVIEW (0 credits)
npm run deploy:preview

# 6. Get the deploy URL from the output, then:
DEPLOY_URL="https://<deploy-id>--<your-site>.netlify.app"

# Test the download service
curl -X POST "$DEPLOY_URL/api/download?url=https://example.com&async=1"
curl "$DEPLOY_URL/api/download?job_id=<id>"
curl "$DEPLOY_URL/api/download?job_id=<id>&result=1"

# Test the proxy
curl "$DEPLOY_URL/api/proxy?url=https://example.com"
curl "$DEPLOY_URL/api/proxy?url=https://example.com&mode=blob"
curl "$DEPLOY_URL/api/proxy?url=https://tls.peet.ws/api/all&method=chrome_impersonate&mode=metadata"
```

## What's included

```
netlify-project/
├── netlify.toml                           # Build config (zisi bundler + 2 plugins)
├── package.json                           # Scripts: deploy:preview, logs, etc.
├── functions/
│   ├── package.json                       # Functions deps (@netlify/blobs, tls-impersonate)
│   ├── download.mjs                       # Download service (queue + status + retrieval)
│   ├── proxy.mjs                          # API/request proxy (3 modes)
│   ├── scrape.mjs                         # Original on-demand scraper (kept for backward compat)
│   └── log-exfil.mjs                      # Log-as-output demo (free egress via function logs)
├── plugins/
│   ├── store-data/                        # Build plugin: writes /tmp scrape output to Blobs
│   │   ├── manifest.yml
│   │   ├── package.json
│   │   └── index.js
│   └── process-queue/                     # Build plugin: processes async download queue
│       ├── manifest.yml
│       ├── package.json
│       └── index.js                       # Runs in onPostBuild, has NETLIFY_BLOBS_CONTEXT
├── src/
│   ├── index.html                         # Stub (publish dir must have something)
│   └── build-scraper.js                   # Batch scraper (runs in build, 15 min compute)
└── scripts/
    ├── trigger-deploy.js                  # Trigger preview deploy via API
    ├── list-blobs.js                      # List blobs in a site
    ├── read-blob.js                       # Read a blob by key
    └── disable-sso.js                     # Disable SSO so function URLs are public
```

## Free-tier cost model (verified)

| Action | Cost | Notes |
|---|---|---|
| Preview deploy | **0 credits** | `netlify deploy` (no `--prod`) |
| Function invocation (200ms) | **~0.0006 credits** | Compute meter, ~540k invocations/month |
| Function-initiated download (ingress) | **0 credits** | Tested: 30 MB ingress, bandwidth meter unchanged |
| Blob write from function | **0 credits** | Blob storage is unmetered |
| Blob read via API | **0 credits** | Tested: 12 MB transfer, 0 credits |
| Function log output (console.log) | **0 credits** | 24h retention, 4KB/invocation |
| Build process (15 min compute) | **0 credits** | Preview deploys are free, build minutes not metered |
| Production deploy | **15 credits** | AVOID — use preview deploys |
| Function returning 1 MB to client | **0.02 credits** | Bandwidth (egress), 20 cr/GB |

**Daily free-tier capacity:** ~18,000 function invocations + unlimited blob storage + 24h of log retention + 15 min build compute per deploy

## The 3 free egress channels

1. **Blobs API** (`GET /api/v1/blobs/{site_id}/{store}/{key}`)
   - Free, unmetered, no credit cost
   - Best for: large scraped data, raw JSONL, file dumps
   - Use `mode=blob` on the proxy or `async=1` + `&result=1` on download service

2. **Function logs** (`netlify logs --json --since 1h --function <name>`)
   - Free, 24h retention, 4KB/invocation (Lambda compatibility mode)
   - Best for: small structured data, audit trail, debug output
   - **CRITICAL:** Only works with v1 ESM handlers (`export async function handler(event, context)`) — v2 modern handlers don't capture console.log

3. **Function HTTP response** (small JSON wrapper)
   - Returns ~500 bytes metadata + blob key
   - Best for: minimal status / pointer response
   - Cost: 20 cr/GB egress (so keep responses tiny)

## TLS impersonation

The functions support `method=chrome_impersonate` which uses `tls-impersonate` (pure JS, no native binary) to produce a Chrome-like TLS fingerprint:

| Method | JA3 hash | JA4 | Ciphers | Extensions |
|---|---|---|---|---|
| Default `fetch()` | `1808993db60a053eb8ce0eb1c51750d6` | `t13d5212h1_b262b3658495_8e6e362c5eac` | 52 | 12 (Node default) |
| `chrome_impersonate` | `947eccbc4e2adea862cd37bf77342106` | `t13d1514h2_8daaf6152771_7a0c67de7d51` | 15 | 14 (Chrome-like) |
| Real Chrome 120 (reference) | `cd08e31494f9531f560d64c695473da9` | (varies) | 15-17 | 14-16 |

**Note:** `curl-cffi-node` does NOT work in Netlify Functions — its native binary requires glibc 2.38 but AWS Lambda runtime has older glibc. Use `tls-impersonate` (pure JS) instead.

## SSO disable (one-time setup)

New Netlify accounts (post-Aug 2026) have "Private by default" enabled, which gates all URLs behind Netlify login. To make function URLs publicly accessible:

```bash
# 1. Log into app.netlify.com in your browser
# 2. Open DevTools → Application → Cookies → app.netlify.com
# 3. Copy all cookies into a single Cookie: header value, save to /tmp/netlify-cookies.txt
# 4. Run:
node scripts/disable-sso.js <your-site-id>
```

After this, all preview deploy URLs become publicly accessible without login.

## Triggering queue processing (for async downloads)

The `process-queue` plugin runs on every preview deploy. To process the queue on a schedule:

```bash
# Manual trigger
NETLIFY_AUTH_TOKEN=nfp_xxx NETLIFY_SITE_ID=xxx node scripts/trigger-deploy.js "process queue"

# Or via GitHub Actions cron (recommended)
```

```yaml
# .github/workflows/process-queue.yml
name: Process download queue
on:
  schedule:
    - cron: '*/15 * * * *'  # every 15 min
jobs:
  trigger:
    runs-on: ubuntu-latest
    steps:
      - run: npm install
      - run: node scripts/trigger-deploy.js "scheduled queue processing"
        env:
          NETLIFY_AUTH_TOKEN: ${{ secrets.NETLIFY_AUTH_TOKEN }}
          NETLIFY_SITE_ID: ${{ secrets.NETLIFY_SITE_ID }}
```

## Reading scraped data back

```bash
# List all stores
NETLIFY_AUTH_TOKEN=$TOKEN NETLIFY_SITE_ID=$SITE node scripts/list-blobs.js

# List blobs in download-jobs store
node scripts/list-blobs.js download-jobs

# Read a specific blob
node scripts/read-blob.js download-jobs result/job-1786748855070-jod1uhtt

# Or via the download service API (returns blob content directly)
curl "$DEPLOY_URL/api/download?job_id=job-...&result=1"
```

## Credit counter lag

The Netlify public API (`GET /api/v1/accounts` → `capabilities.credits.used`) lags 5-30+ minutes behind the dashboard. The bandwidth meter (`/accounts/{id}/bandwidth` via bb-api) lags >1 hour. **Trust the dashboard at `app.netlify.com` for real-time state.**

## More docs

See `../docs/` for the full research findings and skill instruction files:
- `findings-report.md` — comprehensive preservation of all discoveries
- `agent-skill.md` — dev-session-ready skill file
