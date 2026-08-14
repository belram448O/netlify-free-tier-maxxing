# Netlify Free-Tier Scraper

A ready-to-deploy scraper toolkit that maximizes Netlify's free tier (post-Sep 2025 credit-based pricing).

## Quick start

```bash
# 1. Install Netlify CLI
npm install -g netlify-cli

# 2. Authenticate
netlify login  # opens browser, OR set NETLIFY_AUTH_TOKEN env var with a PAT

# 3. Link this folder to a Netlify site (or create a new one)
netlify link

# 4. Deploy as PREVIEW (0 credits)
npm run deploy:preview

# 5. Get the deploy URL from the output, then invoke the scraper function:
curl 'https://<deploy-id>--<your-site>.netlify.app/.netlify/functions/scrape?url=https://example.com&return_blob=1'
```

## What's included

```
netlify-project/
├── netlify.toml                          # Build config (zisi bundler + plugin)
├── package.json                          # Scripts: deploy:preview, logs, etc.
├── functions/
│   ├── package.json                      # Functions deps (@netlify/blobs, tls-impersonate)
│   ├── scrape.mjs                        # On-demand scraper (fetch or chrome_impersonate)
│   └── log-exfil.mjs                     # Log-as-output demo (free egress via function logs)
├── plugins/
│   └── store-data/                       # Build plugin: writes /tmp to Blobs after build
│       ├── manifest.yml
│       ├── package.json
│       └── index.js
├── src/
│   ├── index.html                        # Stub (publish dir must have something)
│   └── build-scraper.js                  # Batch scraper (runs in build, 15 min compute)
└── scripts/
    ├── trigger-deploy.js                 # Trigger preview deploy via API
    ├── list-blobs.js                     # List blobs in a site
    ├── read-blob.js                      # Read a blob by key
    └── disable-sso.js                    # Disable SSO so function URLs are public
```

## Free-tier cost model

| Action | Cost | Notes |
|---|---|---|
| Preview deploy | **0 credits** | `netlify deploy` (no `--prod`) |
| Function invocation (200ms) | **~0.0006 credits** | Compute meter, ~540k invocations/month |
| Function-initiated download (ingress) | **0 credits** | Tested: 30 MB ingress, bandwidth meter unchanged |
| Blob write from function | **0 credits** | Blob storage is unmetered |
| Blob read via API | **0 credits** | Tested: 12 MB transfer, 0 credits |
| Function log output (console.log) | **0 credits** | 24h retention, 4KB/invocation |
| Production deploy | **15 credits** | AVOID — use preview deploys |
| Function returning 1 MB to client | **0.02 credits** | Bandwidth (egress), 20 cr/GB |

**Daily free-tier capacity:** ~18,000 function invocations + unlimited blob storage + 24h of log retention

## The 3 free egress channels

1. **Blobs API** (`GET /api/v1/blobs/{site_id}/{store}/{key}`)
   - Free, unmetered, no credit cost
   - Best for: large scraped data, raw JSONL, file dumps
   - Use `return_blob=1` query param on the scrape function to get a blob key back

2. **Function logs** (`netlify logs --json --since 1h --function <name>`)
   - Free, 24h retention, 4KB/invocation (Lambda compatibility mode)
   - Best for: small structured data, audit trail, debug output
   - Use `console.log(JSON.stringify({...}))` in the function

3. **Function HTTP response** (small JSON wrapper)
   - Returns ~500 bytes metadata + blob key
   - Best for: minimal status / pointer response
   - The full scraped data should go to Blobs (free) NOT the response body (which costs 20 cr/GB)

## TLS impersonation

The `scrape.mjs` function supports two methods:

- `?method=fetch` (default): Node's built-in `fetch()`. Fast but JA3 fingerprint is recognizable as Node.js (`1808993db60a053eb8ce0eb1c51750d6`). Most sites won't block this.

- `?method=chrome_impersonate`: Uses `tls-impersonate` with Chrome 120 ClientHello spec. JA3 hash changes to `947eccbc4e2adea862cd37bf77342106` — Chrome-like. Use this for JA3-aware bot-protected sites.

**Note:** `curl-cffi-node` (napi-rs binding to curl-impersonate) does NOT work in Netlify Functions — its native binary requires glibc 2.38 but AWS Lambda runtime has older glibc. Use `tls-impersonate` (pure JS) instead.

## SSO disable (one-time setup)

New Netlify accounts (post-Aug 2026) have "Private by default" enabled, which gates all URLs behind Netlify login. To make function URLs publicly accessible:

1. Log into `app.netlify.com` in your browser
2. Open DevTools → Application → Cookies → `app.netlify.com`
3. Copy all cookies into a single `Cookie:` header value, save to `/tmp/netlify-cookies.txt`
4. Run: `node scripts/disable-sso.js <your-site-id>`

After this, all preview deploy URLs (and prod URLs) become publicly accessible without login. Site-level fix is sufficient — account-level visibility requires Pro plan.

## Triggering deploys programmatically

```bash
# From cron (GitHub Actions, etc.) — triggers a preview deploy (0 credits)
NETLIFY_AUTH_TOKEN=nfp_xxx NETLIFY_SITE_ID=xxx node scripts/trigger-deploy.js "hourly scrape"
```

For GitHub Actions cron:
```yaml
name: Hourly scrape
on:
  schedule:
    - cron: '0 * * * *'
jobs:
  scrape:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: npm install
      - run: node scripts/trigger-deploy.js "hourly scrape"
        env:
          NETLIFY_AUTH_TOKEN: ${{ secrets.NETLIFY_AUTH_TOKEN }}
          NETLIFY_SITE_ID: ${{ secrets.NETLIFY_SITE_ID }}
```

## Reading scraped data back

```bash
# List all stores
NETLIFY_AUTH_TOKEN=$TOKEN NETLIFY_SITE_ID=$SITE node scripts/list-blobs.js

# List blobs in a store
node scripts/list-blobs.js function-scrapes

# Read the latest pointer
curl -s -H "Authorization: Bearer $TOKEN" \
  "https://api.netlify.com/api/v1/blobs/$SITE/site:function-scrapes/latest" | jq .

# Read a specific blob
node scripts/read-blob.js function-scrapes scrape-1786743913760
```

## Build process as free compute (alternative pattern)

The build process gives you 15 min of free compute per preview deploy. Use it for batch scraping:

```toml
# netlify.toml
[build]
  command = "node src/build-scraper.js"  # Your scraper runs here
  publish = "src"
```

The scraper writes to `/tmp/`, then the `store-data` plugin's `onPostBuild` hook writes to Blobs. This gives you 15 min of CPU + 9 GB disk + outbound HTTP — all free.

## Credit counter lag

The Netlify public API (`GET /api/v1/accounts` → `capabilities.credits.used`) lags 5-30+ minutes behind the dashboard. The bandwidth meter (`/accounts/{id}/bandwidth` via bb-api) lags >1 hour. **Trust the dashboard at `app.netlify.com` for real-time state.**

## More docs

See `../docs/` for the full research findings and skill instruction files:
- `findings-report.md` — comprehensive preservation of all discoveries
- `agent-skill.md` — dev-session-ready skill file
