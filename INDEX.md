# Artifact Index — netlify-free-tier-maxxing

> Complete catalog of every file in this repo, with purpose. Use this to find anything without spelunking.

---

## Top-level documentation

| Path | Purpose | Origin |
|---|---|---|
| `README.md` | Repo overview, structure, relationship to other repos | Written 2026-08-17 |
| `INDEX.md` | This file — full artifact catalog | Written 2026-08-17 |
| `PUSH_INSTRUCTIONS.md` | Step-by-step push procedure for the next session/user | Written 2026-08-17 |
| `.gitmodules` | Registers `netlify-free-scraper` as a proper submodule | Written 2026-08-17 |
| `.gitignore` | Excludes `skills/` (skill cache) and `node_modules/` | Pre-existing |
| `.env` | Local DATABASE_URL only — no secrets | Pre-existing (auto-generated) |

---

## `agent-kit/` — the CLEANED docs (mirrors the lean GitHub repo)

This subdirectory mirrors what's published at `github.com/belram448O/netlify-free-tier-agent-kit`. The maxxing repo keeps a copy here so the superset is self-contained.

| Path | Purpose |
|---|---|
| `agent-kit/README.md` | Top-level README for the clean kit |
| `agent-kit/docs/agent-skill.md` | Dev-session-ready skill file (1345 lines). Load this at the start of any dev session that uses Netlify free tier. |
| `agent-kit/docs/findings-report.md` | Comprehensive findings report (973 lines). The single source of truth for what was discovered. |
| `agent-kit/docs/dashboard-automation.md` | bb-api endpoint reference + WAF test results (283 lines). For operations the PAT can't do. |
| `agent-kit/docs/free-tier-investigation-methodology.md` | Generalized methodology for investigating any cloud platform's free tier (248 lines). |
| `agent-kit/netlify-project/README.md` | Demo project structure showing how to wire the scraper |
| `agent-kit/netlify-project/netlify.toml` | Build config for the demo project |
| `agent-kit/netlify-project/llms.txt` | LLM-friendly API doc with exact endpoints, tokens, IDs for the LIVE deployment |
| `agent-kit/netlify-project/src/llms.txt` | Shorter version of the llms.txt |
| `agent-kit/netlify-project/src/index.html` | Demo stub HTML |
| `agent-kit/netlify-project/scripts/disable-sso.js` | Script: disable SSO on a Netlify site via bb-api |
| `agent-kit/netlify-project/scripts/list-blobs.js` | Script: list blobs in a site's blob store |
| `agent-kit/netlify-project/scripts/read-blob.js` | Script: read a blob by key |
| `agent-kit/netlify-project/scripts/trigger-deploy.js` | Script: trigger a deploy via API |
| `agent-kit/netlify-project/scraper/...` | Submodule → github.com/belram448O/netlify-free-scraper |

---

## `netlify-free-scraper/` — git submodule

The production scraper code. Has its own remote and life. Pulled in here as a submodule so the maxxing repo references the exact commit that the investigation used.

- **Remote:** `https://github.com/belram448O/netlify-free-scraper.git`
- **Pinned commit:** `9e5e906cce24525709c3b54a226217bdaf8cec16` ("Fix docs: API deploys do NOT trigger builds")
- **Contents (20 tracked files):**
  - `lib/scraper.mjs` — shared library (validation, SSRF, engines, batch logic)
  - `functions/scrape.mjs` — 6-endpoint API function
  - `plugins/process-queue/index.js` — puppeteer-capable queue processor (15-min budget)
  - `cli/{submit,status,result,list,resume}.mjs` — 5 CLI tools
  - `tools/netlify-dashboard-api.mjs` — bb-api automation CLI (32 sample responses)
  - `PROTOCOL.md` — full API spec
  - `README.md`, `netlify.toml`, `package.json`

---

## `netlify-probe/` — build-environment probe (regular directory, no own remote)

Code that probes the Netlify build container to discover its actual runtime characteristics. Originally a separate git repo with one commit ("v2"), now absorbed into the maxxing repo as a regular directory because it had no remote and the broken submodule registration was causing issues.

### Key findings this probe produced

| Field | Value | How it was discovered |
|---|---|---|
| OS | Linux x64 (Ubuntu "noble" 24.04) | `platform: linux`, `build_image: noble` |
| Container | Kata Containers VM | `KATA_CONTAINER=true` env var |
| Region | `cn-hongkong` (Alibaba Cloud Function Compute) | `FC_REGION=cn-hongkong` env var |
| Memory | 4096 MB allocated | `FC_FUNCTION_MEMORY_SIZE=4096` |
| CPU | 2 vCPU Intel Xeon (shared) | `os.cpus().length = 2` |
| Disk | 9.9 GB total, 8.1 GB free | `df -h /` |
| Node | v24.18.0 | `process.version` |
| Build duration cap | 15 min (Free) | Documented + verified |
| Concurrent builds | 1 (Free) | Documented + verified |
| Egress IP | `47.57.232.232` / `47.57.242.119` (Alibaba HK) | `api.ipify.org` |
| `@sparticuz/chromium` | ✅ 190.6 MB binary downloaded in 2.5s | Probed |
| Outbound HTTP | ✅ Allowed | Probed (fetch to httpbin, postman-echo, ipify, GitHub API) |
| Build log volume | ✅ Up to 10 MB writes per build | Probed |

### Files

| Path | Purpose |
|---|---|
| `netlify-probe/src/build-probe.js` | The probe script. Writes discovered env vars + system info to stdout. |
| `netlify-probe/src/index.html` | Demo stub HTML |
| `netlify-probe/plugins/probe-build/index.js` | Build plugin that runs the probe in `onPostBuild` |
| `netlify-probe/plugins/probe-build/manifest.yml` | Plugin manifest (registers `onPostBuild` hook) |
| `netlify-probe/plugins/probe-build/package.json` | Plugin deps |
| `netlify-probe/functions/scrape-scheduled.js` | Scheduled-function variant (probe on a cron) |
| `netlify-probe/netlify.toml` | Build config (registers the plugin) |
| `netlify-probe/package.json` | Top-level deps |
| `netlify-probe/package-lock.json` | Lockfile |
| `netlify-probe/deno.lock` | Deno lockfile (alternative runtime experiment) |
| `netlify-probe/.gitignore` | Excludes `.netlify/` (build artifacts) |

> **Note:** The `.netlify/db/` PostgreSQL binaries and `.netlify/functions/*.zip` build artifacts were stripped during the de-submodule-ization. They were derivatives of `netlify dev` runs, not research material. The probe's source code is what's preserved.

---

## `netlify-log-probe/` — function-logs-as-egress-channel probe

Code that validates using Netlify function logs as a third free egress channel (alongside Blobs API and HTTP response). Proves that `console.log()` output can be retrieved via `netlify logs --json` for free (no credit cost), with 24h retention and 4KB/invocation cap.

### Key findings this probe produced

| Output type | Captured? | Notes |
|---|---|---|
| `console.log('HELLO')` | ✅ | Plain string |
| `console.log(JSON.stringify({...}))` | ✅ | JSON-parseable per line |
| `console.error('...')` | ✅ | Captured with `level: "error"` |
| Auto-generated Duration/Memory line | ✅ | `level: "info"`, format: `Duration: X ms\tMemory Usage: Y MB` |
| Stack traces on errors | ✅ | Multi-line, captured per line |
| Function `Response.json()` body | ❌ | Not logged (only `console.*` output) |

**Critical:** v1 ESM handlers (`.mjs` + `export async function handler(event, context)`) capture logs. v2 modern handlers (`export default async function handler(req, context)`) do NOT — empty INFO line only.

### Files

| Path | Purpose |
|---|---|
| `netlify-log-probe/src/scrape.js` | The scraper logic |
| `netlify-log-probe/src/index.html` | Demo stub HTML |
| `netlify-log-probe/functions/scrape.mjs` | Function that does the scrape + writes to logs |
| `netlify-log-probe/functions/test-log.mjs` | Log capture validation (proves the channel works) |
| `netlify-log-probe/plugins/store-data/index.js` | Plugin that stores scrape results to Blobs |
| `netlify-log-probe/plugins/store-data/manifest.yml` | Plugin manifest |
| `netlify-log-probe/plugins/store-data/package.json` | Plugin deps |
| `netlify-log-probe/netlify.toml` | Build config |
| `netlify-log-probe/package.json` | Top-level deps |
| `netlify-log-probe/package-lock.json` | Lockfile |
| `netlify-log-probe/.netlify/functions/*.zip` | 8 pre-built function zips (preserved as evidence of what was deployed) |
| `netlify-log-probe/.netlify/netlify.toml` | Generated config |
| `netlify-log-probe/.netlify/state.json` | Site binding (siteId) |

---

## `upload/` — RAW EVIDENCE: HAR files

HAR captures from the Netlify dashboard. These are the primary evidence for the bb-api findings (no WAF, `_nf-auth` cookie sufficiency, observability endpoints). Captured manually via browser DevTools during the investigation.

| Path | Size | Purpose |
|---|---|---|
| `upload/app.netlify.visibility.har` | 7.9 MB | Full capture during SSO/visibility toggle. Contains the exact request/response sequence for `disable-sso`, including the `_nf-auth` cookie and the `sso_login: false` body. |
| `upload/app.netlify.visibility2.har` | 272 KB | Second capture for verification — confirms the pattern is reproducible. |
| `upload/app.netlify.visibility.har.zip` | 501 KB | Zipped first capture (for easier transfer). |
| `upload/__MACOSX/` | small | macOS zip artifact (can be deleted) |

> **Privacy note:** The HAR files contain session cookies that have since been rotated. They are safe to publish but should not be used as auth material.

---

## `tool-results/` — saved grep/read outputs

Outputs from search and read operations during the investigation. Preserved for two reasons:
1. **Schema reference** — what the actual API responses look like (mock testing)
2. **Context recovery** — if the source files are lost, the saved reads preserve the content

| Path | Size | Origin |
|---|---|---|
| `tool-results/grep_1786676694813_344cb3d27ae1.txt` | 51 KB | Grep output searching for `dns_zone|NS delegation|cloudflare.*worker` |
| `tool-results/grep_1786676777461_43c3ae1ea3fe.txt` | 47 KB | Grep output searching for `nsone` |
| `tool-results/read_1786676518794_12e81444374f.txt` | 61 KB | Saved read of `findings-report.md` (early version) |
| `tool-results/read_1786762980564_0c17283557db.txt` | 68 KB | Saved read of `dashboard-automation.md` |
| `tool-results/read_1786762981611_c7d2726eb7ac.txt` | 57 KB | Saved read of `findings-report.md` (later version) |
| `tool-results/read_1786926241031_c7d2726eb7ac.txt` | 57 KB | Saved read (re-read of same file, second pass) |
| `tool-results/read_1786926249155_a62c76e72f68.txt` | 66 KB | Saved read with offset (continuation) |

---

## `download/` — deliverables produced during the investigation

| Path | Purpose |
|---|---|
| `download/README.md` | One-line description |
| `download/netlify-free-tier-agent-kit.zip` | Snapshot of the clean agent-kit repo (113 KB, zipped on 2026-08-15) |
| `download/netlify-free-tier/agent-skill.md` | Copy of the agent skill doc |
| `download/netlify-free-tier/findings-report.md` | Copy of the findings report |

---

## `cross-project-handoffs/` — NOT netlify-related

Files that were produced during sessions that touched multiple projects. Preserved here so the next session can move them to their proper home.

| Path | Purpose | Proper home |
|---|---|---|
| `cross-project-handoffs/sonicloud-ns-architecture.md` | The sonicloud.app NS/DNS architecture + the design discussion about evolving it to a CF Worker edge router + pod fleet | Should be moved to `github.com/zulfikarbarbora-outl/sonicloud-infra` (private, separate repo) |
