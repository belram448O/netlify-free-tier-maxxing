# Netlify Free-Tier Agent Kit

Demo project + research findings for maxxing Netlify's free tier.

## Structure

- `netlify-project/scraper/` — Production scraper (imported as git submodule from [netlify-free-scraper](https://github.com/belram448O/netlify-free-scraper))
- `netlify-project/src/` — Demo stub HTML
- `netlify-project/netlify.toml` — Build config that wires the scraper's functions + plugins
- `docs/` — Research findings + skill docs

## Quick start

```bash
git clone --recursive https://github.com/belram448O/netlify-free-tier-agent-kit.git
cd netlify-free-tier-agent-kit/netlify-project
npm install
cd scraper && npm install && cd ..
netlify link
netlify deploy
```

## Scraper package

The actual scraper code lives in a separate repo: [netlify-free-scraper](https://github.com/belram448O/netlify-free-scraper)

Features: batch processing, TLS impersonation, queued processing, puppeteer, blob storage, resume support.

## Docs

- `docs/agent-skill.md` — Dev-session-ready skill file
- `docs/dashboard-automation.md` — bb-api endpoint reference + WAF test results
- `docs/findings-report.md` — Comprehensive research preservation
- `docs/free-tier-investigation-methodology.md` — Generalized methodology for any cloud platform
