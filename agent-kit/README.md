# Netlify Free-Tier Agent Kit

A complete toolkit for maxxing Netlify's free tier (post-Sep 2025 credit-based pricing) for scraping, data pipelines, and background compute.

## What's inside

- **`netlify-project/`** — Ready-to-deploy Netlify project with:
  - Batch scraper function with TLS impersonation (`tls-impersonate`)
  - Queue processor build plugin (supports `puppeteer` for real Chrome)
  - CLI tools (submit, status, result, list) — zero function calls for queue management
  - Dashboard automation tool (SSO disable, bandwidth, observability)
  - 32 sample API responses for mock testing
- **`docs/`** — Research findings + skill instruction files:
  - `findings-report.md` — Comprehensive preservation of all discoveries
  - `agent-skill.md` — Dev-session-ready skill instruction file
  - `dashboard-automation.md` — bb-api endpoint reference + WAF test results

## Quick start

```bash
cd netlify-project
npm install
cd functions && npm install && cd ..
npm install -g netlify-cli
netlify link
netlify deploy
```

## GitHub

Repo: https://github.com/belram448O/netlify-free-tier-agent-kit
