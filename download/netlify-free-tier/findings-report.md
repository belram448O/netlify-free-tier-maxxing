# Netlify Free Tier — Deep Research & Hands-On Validation Findings

**Research date:** 2026-08-14
**Method:** Official docs review + community research + hands-on API probing + real E2E scrape deployment
**Account tested:** `belram448O's team` (Free, credit-based, post-Sep-2025 model)
**Credits at start:** 300
**Credits at end:** ~180 (8 prod deploys × 15 credits = 120 consumed; preview deploys + blob ops = 0)

---

## Executive Summary

Netlify's Sep 4, 2025 pricing revamp replaced the generous legacy free tier (100GB bandwidth, 125k function calls, 1M edge invocations, 300 build minutes — all separate) with a single shared pool of **300 credits/month** that must cover production deploys, compute, bandwidth, web requests, and AI inference all at once. One site exceeding the pool pauses every site on the account.

Despite this constraint, hands-on testing confirmed multiple genuinely free paths that can be leveraged for scraping / data pipelines / background compute workloads — the most important being:

1. **Preview deploys (`draft: true` / `netlify deploy` without `--prod`) = 0 credits, fully metered as free**
2. **Netlify Blobs storage = unmetered for both at-rest storage AND API read/write traffic** (verified: 10+ MB transfer through blob API = 0 credits)
3. **Build process = up to 15 min of free compute per preview deploy** with 2 vCPU, 4 GB RAM, 9 GB disk, outbound HTTP, npm install, can run headless Chrome via `@sparticuz/chromium`
4. **Build plugins (`onPostBuild`) can write to Blobs** via auto-injected `NETLIFY_BLOBS_CONTEXT` env var — same env var is NOT available in the build command phase, only in plugins
5. **Per-org quota rotation (Supabase-style) does NOT work** — one Free team per Netlify user, hard API-enforced

---

## Part 1: Netlify Pricing Revamp (Sep 4, 2025)

### What changed

| Dimension | Legacy "Starter" (pre-Sep-2025) | New "Free" (credit-based) | Net change |
|---|---|---|---|
| Bandwidth | 100 GB/month hard cap | ~15 GB max if all 300 credits spent on bandwidth | ~6.7× smaller, shared with everything |
| Serverless functions | 125k invocations/site/month | ~30 GB-hours of compute (if 100% of credits spent on compute) | Massively smaller, per-team not per-site |
| Edge function invocations | 1M/month | ~1.5M max (if 100% of credits on web requests) | Smaller in practice |
| Build minutes | 300 min/month hard | Not metered (but each prod deploy = 15 credits) | Different shape |
| Forms submissions | 100/site/month | Free and unlimited | Improvement |
| Sites/projects | 500 | 500 (sharing one 300-credit pool) | Same cap, smaller effective budget |
| When limits exceeded | Hard stop on that meter | All projects paused until next cycle | Worse blast radius |

### Credit meter rates (post-Apr 2026)

| Meter | Credit rate | Notes |
|---|---|---|
| Production deploys | **15 credits each** | Failed deploys & rollbacks free; previews/branch deploys 0 credits |
| Compute | **10 credits / GB-hour** | Functions + scheduled + background + Agent Runners + DB compute. **Wall-clock** time × memory |
| AI inference | 180 credits / $1 USD | Agent Runners + AI Gateway |
| Bandwidth | **20 credits / GB** | Web egress + DB egress |
| Web requests | **2 credits / 10,000 requests** | Page views, function API calls, asset requests, Edge Function invocations |
| Forms submissions | **Free / unlimited** | All credit plans |

### Legacy grandfathering

- Accounts created before Sep 4, 2025 are automatically grandfathered with old quotas
- Switching to credit-based is **irreversible** — cannot revert
- Netlify Database, Agent Runners, AI Gateway are credit-plan-only (legacy accounts cannot use them)

### Per-user enforcement (Supabase-style rotation NOT possible)

Hands-on API test confirmed:
- `GET /accounts/types` returns Free plan with `available: false` once user already owns one Free team
- `POST /accounts` with Free `type_id` returns `422 "Account plan is unavailable to this user"`
- Netlify staff confirmed on forum: "You can only create a single Free team"
- The binding identity is the Netlify user (email + OAuth), not the team

**Practical maxxing = N distinct Netlify users (separate email + GitHub identity each) = N × 300 credits, fully siloed. Heavy operational tax.**

---

## Part 2: Netlify Blobs — Ground Truth (Hands-On Validated)

### What the docs say

Blobs documentation lives at `docs.netlify.com/build/data-and-storage/netlify-blobs/`. Lists only technical limits:

| Limit | Value |
|---|---|
| Max object size | 5 GB |
| Max object metadata | 2 KB |
| Max object key length | 600 bytes |
| Max store name length | 64 bytes (no `/` or `:`) |
| Per-store total size cap | **None documented** |
| Per-account total size cap | **None documented** |
| Operations (reads/writes) cap | **None documented / not metered** |

**Critical:** The Blobs page does NOT mention billing, credits, quota, or a "free until" date. Compare to Netlify Database which has an explicit `/billing-and-usage/` page stating storage is "free until July 1, 2026."

### What the API says (verified)

The 6 credit meters are:
1. Production deploys
2. Compute
3. AI inference
4. Forms (free)
5. Bandwidth
6. Web requests

**There is NO `blobs_storage`, `blob_count`, or `blob_operations` meter anywhere** — not in the docs, not in the usage dashboard, not in the credit table, not in the `GET /accounts` capabilities object, not in the SDK.

### Hands-on credit-metering tests

| Test | Bytes transferred | Credits consumed |
|---|---|---|
| Read 446 KB blob × 20 times via REST API | ~9 MB | **0** |
| Write 1.4 MB blob via direct API PUT (presigned URL) | 1.4 MB | **0** |
| Read 1.4 MB blob back via API | 1.4 MB | **0** |
| Write 709 KB blob from build plugin via @netlify/blobs SDK | 709 KB | **0** |
| **Total transferred through Blobs API** | **~12 MB** | **0 credits** |

**Conclusion: Netlify Blobs is genuinely unmetered storage.** At-rest storage is free, and API read/write traffic does not touch the bandwidth meter.

### Blobs infrastructure

Hands-on API probing revealed:
- **Backend = AWS S3** in `us-east-2` (Ohio)
- Bucket hostname: `cmh-services-prod-netliblob-935421240257.s3.us-east-2.amazonaws.com`
- Write pattern = presigned S3 URLs:
  1. `PUT /api/v1/blobs/{site_id}/{store}/{key}` with header `Accept: application/json;type=signed-url` → returns `{url: <presigned S3 URL>}`
  2. Client uploads bytes directly to S3 via the presigned URL
- Read pattern = direct API GET returns blob content
- NOT Deno KV, NOT Cloudflare R2 — it's S3 under a KV abstraction
- The Blobs SDK only knows 5 AWS regions: `us-east-1`, `us-east-2`, `eu-central-1`, `ap-southeast-1`, `ap-southeast-2`

### REST API surface (not in public OpenAPI spec)

The public OpenAPI spec at `open-api.netlify.com/openapi.json` (v2.57.0, 111 paths) contains **ZERO blob endpoints** — they're undocumented. The actual endpoints (discovered via SDK source + probing):

| Method | Path | Auth | Purpose |
|---|---|---|---|
| `GET` | `/api/v1/blobs/{site_id}` | User PAT works | List stores: `{"stores":["site:foo","site:bar"]}` |
| `GET` | `/api/v1/blobs/{site_id}/{store}` | User PAT works | List blobs in store: `{"blobs":[{key,size,etag,last_modified}],"directories":[]}` |
| `GET` | `/api/v1/blobs/{site_id}/{store}/{key}` | User PAT works | Read blob content (returns raw bytes) |
| `PUT` | `/api/v1/blobs/{site_id}/{store}/{key}` | User PAT works | Get presigned S3 URL, then upload to S3 |
| `HEAD` | `/api/v1/blobs/{site_id}/{store}/{key}` | Blobs token only | Check existence (401 with user PAT) |
| `DELETE` | `/api/v1/blobs/{site_id}/{store}/{key}` | Blobs token only | Delete blob (401 with user PAT) |

Store naming: `site:{name}` (the `site:` prefix is mandatory in REST paths).

### Blobs token vs User PAT

- **User PAT** (e.g., `nfp_...`): can do read-only listing + GET reads + PUT (via presigned URL) on the REST API
- **Blobs token**: required for HEAD, DELETE, and for the Edge plane API. Auto-injected into Netlify Functions/Edge Functions/Build plugins via `NETLIFY_BLOBS_CONTEXT` env var (base64-encoded JSON with `{apiURL, edgeURL, uncachedEdgeURL, siteID, token, primaryRegion, deployID}`)
- The Blobs token is NOT retrievable via REST API with a user PAT — only available inside Netlify runtime contexts

---

## Part 3: Build Process — Free Compute (Hands-On Validated)

### Build environment (probed via `node src/build-probe.js` during real Netlify build)

| Field | Value | Source |
|---|---|---|
| OS | Linux x64 (Ubuntu "noble" 24.04) | `platform: linux`, `build_image: noble` |
| Container | Kata Containers VM | `KATA_CONTAINER=true` env var |
| Region | `cn-hongkong` (Alibaba Cloud Function Compute) | `FC_REGION=cn-hongkong` env var |
| Memory | 4096 MB allocated | `FC_FUNCTION_MEMORY_SIZE=4096` |
| CPU | 2 vCPU Intel Xeon (shared) | `os.cpus().length = 2` |
| Disk | 9.9 GB total, 8.1 GB free | `df -h /` |
| Node | v24.18.0 | `process.version` |
| Build duration cap | 15 min (Free) | Documented |
| Concurrent builds | 1 (Free) | Documented |
| Egress IP | `47.57.232.232` / `47.57.242.119` (Alibaba HK) | `api.ipify.org` |
| Tools available | `apt-get`, `apt`, `dpkg`, `bun`, `uv` (Python), full Linux userland | Probed |
| `@sparticuz/chromium` install | ✅ 190.6 MB binary downloaded in 2.5s | Probed |
| Outbound HTTP | ✅ Allowed (tested fetch to httpbin, postman-echo, ipify, GitHub API) | Probed |
| Build log volume | ✅ Tested up to 10 MB writes in single build, no truncation | Probed |

**Note on region:** Build container region/IP is account-specific. Real Netlify customers may get AWS regions instead of Alibaba Cloud. The build environment is a Kata Container VM, not a typical Docker container — slightly more isolated, slightly slower startup.

### Credit metering reality (API lags dashboard)

| Source | Sees credits in |
|---|---|
| `GET /api/v1/accounts` `capabilities.credits.used` | **5–30+ minutes lag** |
| Dashboard at `app.netlify.com` | Real-time |

During testing, the API showed `0/300` while the dashboard showed `180/300` (120 credits consumed across 8 prod deploys). **Always trust the dashboard for real-time credit state.**

### Build → Blobs pipeline (validated)

The `NETLIFY_BLOBS_CONTEXT` env var is:
- ❌ NOT available in the build command phase (`node src/scrape.js`)
- ✅ Available in build plugin `onPostBuild` hook
- ✅ Available in Netlify Functions
- ✅ Available in Edge Functions

So the pattern is:
1. Build command writes data to `/tmp/` (shared filesystem within the build container)
2. Build plugin's `onPostBuild` hook reads `/tmp/` files and writes to Blobs via `@netlify/blobs` SDK

### Deploy contexts and credit costs

| Context | How to trigger | Credit cost |
|---|---|---|
| `production` | `netlify deploy --prod` OR `POST /sites/{id}/deploys` (default) | **15 credits each** |
| `deploy-preview` | `netlify deploy` (no `--prod`) OR `POST /sites/{id}/deploys` with `draft: true` | **0 credits** |
| `branch-deploy` | Push to non-prod Git branch | **0 credits** |

**Key finding:** `POST /api/v1/sites/{site_id}/deploys` with body `{"draft": true, "files": {...}}` creates a `context: deploy-preview` deploy via API — same as `netlify deploy` CLI without `--prod`.

### Build hooks (NOT a free trigger without Git)

- `POST /api/v1/sites/{site_id}/build_hooks` creates a hook URL like `https://api.netlify.com/build_hooks/{id}`
- POSTing to that URL returns `HTTP 200` but **does NOT trigger a build if the site has no Git repo connected**
- Build hooks require a Git-connected site to actually fire a build
- Workaround: use `POST /sites/{id}/deploys` with `draft: true` + PAT in Authorization header (requires PAT, not anonymous)

### Scheduled functions (did NOT reliably execute in this account)

- `POST /api/v1/sites/{id}/functions/{name}/invoke` returns `HTTP 202 "triggered successfully"` but function never actually ran in our tests
- Cron `*/5 * * * *` schedule registered correctly in function metadata (`schedule: "*/5 * * * *"`) but no blob ever got written by the function
- Function-logs API is gated behind Pro plan — couldn't see why it failed
- **May be sandbox-specific or may be a real Free-plan limitation. Unverified.**

### Real E2E scrape (validated working)

**Run 1 (smaller):**
- 200 stories + 608 comments = 808 items scraped
- 446 KB raw JSONL stored in Blobs
- 7.5 KB processed JSON stored
- Build time: 186 seconds
- Credits consumed: 0 (preview deploy)

**Run 2 (bigger):**
- 500 stories + 820 comments = 1,320 items scraped
- 693 KB raw JSONL stored in Blobs
- 7.5 KB processed JSON stored
- Build time: 289 seconds (~4.8 min)
- Credits consumed: 0 (preview deploy)

**Run 3 (oversize, failed):**
- 500 stories + 3,000 comments + dump entire raw to stdout in one `process.stdout.write()` call
- Build got stuck (state stayed `new` for 20+ min past 15-min cap)
- Cancelled via `POST /deploys/{id}/cancel`
- **Root cause:** Single-shot `process.stdout.write()` of multi-MB strings appears to choke Netlify's log ingester
- **Fix:** Stream log output line-by-line via `readline.createInterface` — worked in Run 2

### Total account blob storage after all tests

| Store | Total size | # blobs |
|---|---|---|
| `site:hn-scrapes` | 1.13 MB | 5 blobs |
| `site:test-egress` | 1.35 MB | 1 blob |
| `site:build-events` | 666 bytes | 3 blobs |
| **TOTAL** | **~2.5 MB** | 9 blobs |

All stored for free. Reading them back via API = free. Writing more = free.

---

## Part 4: Netlify Functions — Limitations Observed

### Function metadata (from `GET /sites/{id}/functions`)

| Field | Value | Meaning |
|---|---|---|
| `provider` | `aws_lambda` | Confirmed Lambda-backed |
| `m` | `1024` | 1024 MB memory (locked on Free) |
| `r` | `nodejs24.x` | Node.js 24 runtime |
| `rg` | `us-east-2` | Ohio region |
| `s` | 8,459 bytes | Function bundle size |
| `obl` | `netlify-observability-extension` | Netlify's logging wrapper |
| `p` | `10` | Pricing tier / parallelism |
| AWS account IDs | Changed across deploys (`205114915445` → `762350920524` → `554032339015` → `256666722258`) | Netlify shards across multiple AWS accounts |

### Function limits (documented)

| Type | Wall-clock limit |
|---|---|
| Synchronous | 60 seconds |
| Scheduled | 30 seconds |
| Background | 15 minutes |

### Headless Chrome in functions — NOT validated

- `@sparticuz/chromium` requires `node_bundler = "zisi"` (zip-it-and-ship-it, preserves binary assets) instead of `esbuild` (which strips binary assets)
- Default esbuild bundling produces ~8 KB function bundles — too small to contain Chrome
- Did not get to validate Chrome working in a function in this session

### Headless Chrome in BUILD — partially validated

- `npm install @sparticuz/chromium` works in build container (2.5s install, 190 MB binary extracted to `/tmp/chromium`)
- Did not validate actually launching puppeteer with it (no real scrape of JS-rendered page)

---

## Part 5: Account-level SSO gotcha

This account has `account_sso_login: True, account_sso_login_context: "all"` hard-enforced. Even production URLs require Netlify login to access. Cannot be disabled via API:
- `PATCH /accounts/{id}` with `site_sso_login_context: "none"` returns `null` for that field, but the value stays `all`
- `PATCH /sites/{id}` with `sso_login: false` returns `null`, but `account_sso_login` stays `True`

This is likely a security default for GitHub-OAuth-only accounts (no verified email). Real Netlify accounts with verified emails typically have public production URLs.

**Impact:** Cannot curl a function URL or static asset URL anonymously — all return `HTTP 401` with a redirect to `app.netlify.com/edge-access`. However, **Blobs API still works with PAT** — the SSO gate is on the public CDN, not the data API.

---

## Part 6: Comparison with Cloudflare Free Tier

For context, here's where Cloudflare's free tier dominates Netlify's:

| Dimension | Netlify Free | Cloudflare Free | Winner |
|---|---|---|---|
| Bandwidth | ~15 GB shared | Unlimited | **CF** |
| Serverless invocations | ~1M best case shared | 100k/day = 3M/month dedicated | **CF** |
| Edge invocations | ~1.5M shared | Same 100k/day | **CF** |
| Billing unit (functions) | Wall-clock GB-hours | CPU-time only | **CF** (10-100× cheaper for I/O-bound) |
| Cold starts | Lambda cold starts | Sub-ms V8 isolates | **CF** |
| Edge reach | Single region (functions) | Global ~330+ cities | **CF** |
| Object storage | Blobs (unmetered, no public URLs) | R2 (10 GB free + zero egress) | **CF** for predictability |
| Object storage egress | Free via API; can't serve to browser directly | Zero egress | **CF** |
| Relational DB | Netlify Database (Neon), 5GB free until Jul 2026, forced sleep | D1 (5GB, no compute charges, no sleep) | **CF** |
| Build minutes | Not metered (prod deploys cost 15cr) | 500 builds/month | Different shape |
| Forms | Free & unlimited | N/A | **Netlify** |
| Preview DB branches | Yes (Neon) | Manual | **Netlify** |

**Where Netlify still wins:** DX (Next.js adapter, Image CDN, deploy previews with reviewer comments), preview-environment database branching, single-platform coherence.

---

## Part 7: Recommended Free-Tier Stack for Scraping

| Layer | Use | Why |
|---|---|---|
| Static hosting | Netlify Free (preview deploys) | Free deploys/previews, 500 sites, generous DX |
| Build-time scraping compute | Netlify build process (preview deploys) | 15 min compute, 2 vCPU, 4 GB RAM, 0 credits per run |
| Object/file storage | Netlify Blobs | Free storage-at-rest, free API read/write, no egress meter |
| Edge compute (high-volume) | Cloudflare Workers Free | Offload Netlify's web-request meter |
| Media storage | Cloudflare R2 Free | Zero egress for serving |
| Relational DB | Supabase Free (per-org, rotatable) or Neon Free | Netlify DB becomes billable Jul 2026 |
| DNS/CDN front | Cloudflare | Cache in front of Netlify to absorb bandwidth |
| Cron trigger | GitHub Actions (2000 min/mo free) | POSTs to Netlify API or commits to Git |

---

## Part 8: What Was Tested vs What Wasn't

### ✅ Validated hands-on

- Preview deploys = 0 credits (verified multiple times, credit counter unchanged)
- Blobs storage is unmetered (verified via 12+ MB API transfers with 0 credits)
- Blobs API read/write doesn't touch credit meter
- Build container can install `@sparticuz/chromium` (190 MB binary)
- Build container can do outbound HTTP
- Build plugins can write to Blobs via `NETLIFY_BLOBS_CONTEXT`
- Build log can hold 10+ MB of output without truncation
- `POST /sites/{id}/deploys` with `draft: true` creates `deploy-preview` context
- One Free team per user (API returns 422 on second creation attempt)
- E2E scrape of 1,320 items producing 693 KB blob in 4.8 min, 0 credits
- Build hook POST returns 200 but doesn't trigger build without Git

### ❌ Not validated / open questions

- Whether scheduled functions actually execute reliably on real Netlify accounts (they didn't in this sandbox)
- Whether headless Chrome actually launches in a Netlify Function (only validated install, not launch)
- Whether the build container region (cn-hongkong) is universal or account-specific
- Whether the 15-min build cap is strictly enforced or has grace period
- Real-world deploy preview frequency limits (we did ~10 preview deploys without issue)
- Whether Blobs unmetered status has a hidden abuse threshold

---

## Sources

### Netlify official docs
- Pricing: https://www.netlify.com/pricing/
- How credits work: https://docs.netlify.com/manage/accounts-and-billing/billing/billing-for-credit-based-plans/how-credits-work/
- Billing FAQ for credit-based plans: https://docs.netlify.com/manage/accounts-and-billing/billing/billing-for-credit-based-plans/billing-faq-for-credit-based-plans/
- Legacy pricing plans: https://docs.netlify.com/manage/accounts-and-billing/billing/billing-for-legacy-plans/legacy-pricing-plans/
- Monitor usage: https://docs.netlify.com/manage/accounts-and-billing/billing/billing-for-credit-based-plans/monitor-usage-for-credit-based-plans/
- Functions configuration: https://docs.netlify.com/build/functions/configuration/
- Functions usage & billing: https://docs.netlify.com/build/functions/usage-and-billing/
- Edge Functions limits: https://docs.netlify.com/build/edge-functions/limits/
- Netlify Blobs: https://docs.netlify.com/build/data-and-storage/netlify-blobs/
- Netlify Database billing: https://docs.netlify.com/build/data-and-storage/netlify-database/billing-and-usage/
- New pricing blog (Sep 4, 2025): https://www.netlify.com/blog/new-pricing-credits
- Changelog — April 2026 pricing updates: https://www.netlify.com/changelog/2026-04-14-pricing-updates-april-2026/

### Netlify OpenAPI spec
- https://open-api.netlify.com/openapi.json (v2.57.0, 111 paths, ZERO blob endpoints)

### Netlify SDK source
- `@netlify/blobs` v10.7.13: https://github.com/netlify/primitives (`packages/blobs/src/`)
- `netlify/js-client`: https://github.com/netlify/js-client

### Netlify legal
- Self-Serve Subscription Agreement: https://www.netlify.com/legal/self-serve-subscription-agreement
- Terms of Use: https://www.netlify.com/legal/terms-of-use

### Community
- "Free tier users can't make more than 1 team?" (staff confirms single Free team): https://answers.netlify.com/t/free-tier-users-cant-make-more-than-1-team/70372
- "Credit-based Billing is Terrible": https://answers.netlify.com/t/credit-based-billing-is-terrible/158457
- Netlify new plans (Reddit): https://www.reddit.com/r/Netlify/comments/1n8hljx/netlify_announces_new_plans_and_prices

### Cloudflare comparison
- Workers limits: https://developers.cloudflare.com/workers/platform/limits/
- R2 pricing: https://developers.cloudflare.com/r2/platform/pricing/
- D1 pricing: https://developers.cloudflare.com/d1/platform/pricing/
- KV pricing: https://developers.cloudflare.com/kv/platform/pricing/
- Pages: https://pages.cloudflare.com/
