# Netlify Free-Tier Maxxing — Research & Raw Artifacts

> **This is the SUPERSET repository.** It contains every artifact from the Netlify free-tier investigation: raw HAR files, probe code, saved grep/read outputs, build-environment probes, and the cleaned docs.
>
> **For the lean, reusable version, see:** [github.com/belram448O/netlify-free-tier-agent-kit](https://github.com/belram448O/netlify-free-tier-agent-kit) — that repo contains only the polished skill/docs without the raw research material.

## What's in here

```
netlify-free-tier-maxxing/
├── README.md                          (this file)
├── INDEX.md                           (catalog of every artifact, with purpose)
├── PUSH_INSTRUCTIONS.md               (how to push this repo to GitHub — requires PAT)
├── .gitmodules                        (registers netlify-free-scraper as a real submodule)
│
├── agent-kit/                         ← mirrors the clean agent-kit repo
│   ├── README.md
│   ├── docs/
│   │   ├── agent-skill.md             (dev-session-ready skill file)
│   │   ├── findings-report.md         (comprehensive findings)
│   │   ├── dashboard-automation.md    (bb-api reference)
│   │   └── free-tier-investigation-methodology.md
│   └── netlify-project/               (demo project structure with scraper as submodule)
│
├── netlify-free-scraper/              ← git submodule (own remote on github)
│   └── (the production scraper code — see github.com/belram448O/netlify-free-scraper)
│
├── netlify-probe/                     ← build-environment probe (de-submodule-ized; no own remote)
│   ├── src/build-probe.js             (probes the build container for region/OS/CPU/disk)
│   ├── plugins/probe-build/           (the build plugin that runs the probe)
│   ├── functions/scrape-scheduled.js  (scheduled-function variant)
│   └── netlify.toml, package.json
│
├── netlify-log-probe/                 ← function-logs-as-egress-channel probe
│   ├── functions/scrape.mjs           (the function that does the scrape)
│   ├── functions/test-log.mjs         (log capture validation)
│   ├── plugins/store-data/            (plugin that stores results to blobs)
│   └── src/scrape.js
│
├── upload/                            ← RAW EVIDENCE — HAR files from dashboard
│   ├── app.netlify.visibility.har     (7.9 MB — captured during SSO/visibility toggle)
│   ├── app.netlify.visibility2.har    (272 KB — second capture for verification)
│   └── app.netlify.visibility.har.zip (501 KB — zipped first capture)
│
├── tool-results/                      ← saved grep/read outputs from the investigation
│   ├── grep_*.txt                     (2 files — search results preserved for schema reference)
│   └── read_*.txt                     (5 files — saved file reads for context recovery)
│
├── download/                          ← deliverables produced during the investigation
│   ├── README.md
│   ├── netlify-free-tier-agent-kit.zip (snapshot of the clean agent-kit)
│   └── netlify-free-tier/             (doc copies — agent-skill.md, findings-report.md)
│
└── cross-project-handoffs/            ← NOT netlify-related; preserved here for handoff
    └── sonicloud-ns-architecture.md   (belongs in sonicloud-infra repo, not here)
```

## Relationship to other repos

| Repo | Purpose | Status |
|---|---|---|
| `github.com/belram448O/netlify-free-tier-agent-kit` | Lean, reusable agent kit (docs + skill only) | Pushed, public |
| `github.com/belram448O/netlify-free-scraper` | Production scraper code (own life, own commits) | Pushed, public, also pulled in as submodule here |
| **`github.com/belram448O/netlify-free-tier-maxxing`** | **THIS repo — raw research artifacts + superset** | **TODO: push (see PUSH_INSTRUCTIONS.md)** |
| `github.com/zulfikarbarbora-outl/sonicloud-infra` | Unrelated sonicloud.app infra (private, separate) | Separate — `cross-project-handoffs/` here contains one doc destined for that repo |

## Investigation summary

This repo preserves the full audit trail of the Netlify free-tier investigation performed 2026-08-14 through 2026-08-17.

**Key findings (documented in `agent-kit/docs/findings-report.md`):**

1. Netlify's Sep 4, 2025 pricing revamp replaced per-feature quotas with a single shared pool of 300 credits/month. One site exceeding the pool pauses every site on the account.
2. **Preview deploys (`draft: true` / `netlify deploy` without `--prod`) = 0 credits. Always.**
3. **Netlify Blobs = unmetered storage. Write/read any volume. 0 credits.** Verified with 12+ MB transfers through the API.
4. **Build process = 15 min free compute per preview deploy.** 2 vCPU, 4 GB RAM, 9 GB disk, outbound HTTP, npm install, can run headless Chrome via `@sparticuz/chromium`.
5. **Three free egress channels:** (a) Blobs API, (b) function logs (24h, 4KB/invocation), (c) HTTP response (small JSON, metered bandwidth).
6. **One Free team per Netlify user** (hard API-enforced — Supabase-style per-org rotation does NOT work).
7. **`_nf-auth` cookie is the only auth needed** for the internal bb-api endpoint — no WAF, no browser headers required. Plain curl works for GET and PUT.
8. **API deploys do NOT trigger the build process.** Only `netlify deploy` CLI or Git push runs the build plugin.

## How to use this repo

- **To understand the findings:** start with `agent-kit/docs/findings-report.md`
- **To build something on these patterns:** load `agent-kit/docs/agent-skill.md` as a skill file in your dev session
- **To automate Netlify operations the PAT can't do:** use `agent-kit/netlify-project/scraper/tools/netlify-dashboard-api.mjs` (the bb-api CLI)
- **To see the raw evidence:** browse `upload/*.har` (HAR captures) and `tool-results/*.txt` (saved grep/read outputs)
- **To see the probe code that produced the findings:** look at `netlify-probe/` (build-env probe) and `netlify-log-probe/` (log-egress probe)

## License

Research material. Use freely. No warranty. The HAR files in `upload/` contain session cookies that have been rotated — they are safe to publish but should not be used as auth material.
