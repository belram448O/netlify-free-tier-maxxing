# Netlify Free-Tier Agent Kit

A complete toolkit for maxxing Netlify's free tier (post-Sep 2025 credit-based pricing) for scraping, data pipelines, and background compute.

## What's inside

- **`netlify-project/`** — Ready-to-deploy Netlify project with:
  - On-demand scraper function (`scrape.mjs`) with Chrome TLS impersonation via `tls-impersonate`
  - Log-as-output demo (`log-exfil.mjs`) — free egress via function logs
  - Build-time batch scraper (`src/build-scraper.js`) — 15 min free compute
  - Build plugin (`plugins/store-data/`) — writes /tmp output to Blobs
  - Helper scripts: trigger-deploy, list-blobs, read-blob, disable-sso
- **`docs/`** — Research findings + agent skill file:
  - `findings-report.md` — Comprehensive preservation of all discoveries (43 KB)
  - `agent-skill.md` — Dev-session-ready skill instruction file (42 KB)

## Quick start

```bash
cd netlify-project
npm install -g netlify-cli   # if not already installed
netlify login                # or set NETLIFY_AUTH_TOKEN env var
npm run deploy:preview       # deploys as preview (0 credits!)
```

## Verified free-tier cost model

| Action | Cost |
|---|---|
| Preview deploy | 0 credits |
| Function invocation (200ms) | ~0.0006 credits (negligible) |
| Function-initiated download (ingress) | 0 credits |
| Blob write from function | 0 credits |
| Blob read via API | 0 credits |
| Function logs (console.log) | 0 credits (24h retention) |
| Production deploy | **15 credits** (avoid) |

## The 3 free egress channels

1. **Blobs API** — write/read any volume, 0 credits, no egress meter
2. **Function logs** — `netlify logs --json --since 1h --function <name>`, 4KB/invocation, 24h retention
3. **Function HTTP response** — small JSON wrapper + blob key, ~500 bytes (negligible)

## GitHub

Private repo: https://github.com/belram448O/netlify-free-tier-agent-kit
