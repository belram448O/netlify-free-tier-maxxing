# Netlify Free-Tier Scraper

A production-ready batch HTTP scraper running on Netlify's free tier. Processes multiple URLs per function call, with TLS impersonation, queued long-running jobs, and zero-credit blob storage.

## Quick start

```bash
# 1. Install deps
npm install
cd functions && npm install && cd ..

# 2. Install Netlify CLI
npm install -g netlify-cli

# 3. Link to a Netlify site
netlify link

# 4. Set SCRAPE_API_KEY env var (optional — if set, API requires auth)
netlify env:set SCRAPE_API_KEY <your-secret-key>

# 5. Deploy as preview (0 credits!)
netlify deploy

# 6. Disable SSO so function URLs are public (one-time, requires cookies)
#    See docs/dashboard-automation.md for cookie extraction instructions
node tools/netlify-dashboard-api.mjs disable-sso <site_id>

# 7. Use the API
curl -X POST 'https://<deploy-id>--<site>.netlify.app/api/scrape' \
  -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer <your-secret-key>' \
  -d '{"jobs":[{"url":"https://example.com","engine":"fetch"}],"result_mode":"blob"}'
```

## API endpoint

```
POST /api/scrape
```

See `llms.txt` for live service documentation with exact endpoints.
See `PROTOCOL.md` for full protocol spec.

## What's included

```
netlify-project/
├── lib/scraper.mjs                  # Shared library (validation, engines, batch logic)
├── functions/scrape.mjs              # Main function — POST /api/scrape
├── plugins/process-queue/           # Build plugin — processes queued batches (puppeteer support)
├── cli/                              # CLI tools (submit, status, result, list)
├── tools/                            # Dashboard automation tool + sample responses
│   ├── netlify-dashboard-api.mjs    # bb-api automation (SSO disable, bandwidth, etc.)
│   └── samples/                     # 32 sample API responses for mock testing
├── scripts/                          # Helper scripts (trigger-deploy, list-blobs, read-blob, disable-sso)
├── src/                              # Static stub (index.html + llms.txt)
├── netlify.toml                     # Build config
├── package.json                     # Root deps (@netlify/blobs, tls-impersonate, chromium, puppeteer-core)
├── PROTOCOL.md                      # Full API spec
└── llms.txt                         # LLM-friendly API doc with live endpoints
```

## Engines

| Engine | Sync | Queue | JA3 | Notes |
|---|---|---|---|---|
| `fetch` | ✅ | ✅ | Node fingerprint | Fast, all HTTP methods |
| `chrome_impersonate` | ✅ GET/HEAD | ✅ GET/HEAD | Chrome-like | Requires HTTPS, uses tls-impersonate |
| `puppeteer` | ❌ | ✅ | Real Chrome | Supports wait_for, actions, screenshot |

## Result modes

| Mode | Behavior | Cost |
|---|---|---|
| `blob` (default) | Stores body in blob, returns `blob_key` | 0 credits |
| `inline` | Returns body in response (≤32KB) | bandwidth |
| `metadata` | Discards body, returns headers only | ~0 |
| `auto` | Inline if ≤32KB, blob if larger | mixed |

## Free-tier cost model

| Action | Cost |
|---|---|
| Preview deploy | 0 credits |
| Function invocation (200ms) | ~0.0006 credits |
| Blob write/read | 0 credits |
| Function ingress (download) | 0 credits |
| Production deploy | 15 credits (avoid) |

## Docs

- `docs/agent-skill.md` — Dev-session-ready skill file with all findings
- `docs/dashboard-automation.md` — bb-api endpoint reference + SSO disable guide
- `docs/findings-report.md` — Comprehensive research preservation
