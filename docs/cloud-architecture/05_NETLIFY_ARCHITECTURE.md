# Netlify Free-Tier Cloud Architecture — sonicloud.app

> **Purpose**: The Netlify research track's final deliverable. Answers the user's original question: how does Netlify's free tier fit into the custom-domain infra kit's cloud architecture, given the constraints (limited credits, finite grandfathered accounts, need for pod routing + A/B + geo + compute+page split)?
>
> **Authoritative**: This doc consolidates the prior research (`agent-kit/docs/findings-report.md`), the live state (verified 2026-08-17 via `25_full_live_audit.py`), and the corrected architecture (per `03_CORRECTION_ALTERNATE_WORKSTREAM.md`). Read this FIRST.
>
> **Scope**: Netlify-specific. The CF Worker / CF KV / CF Cron / Vercel / R2 components are documented in `04_FINAL_ARCHITECTURE.md` — this doc focuses on what Netlify does, what it costs, and how to allocate the finite credit budget.

---

## TL;DR

1. **Netlify DNS is free + unmetered** — no per-query charge, no per-zone charge, no per-record charge. The apex zone (41 records) and 6 sub-zones (7 records each) all live on Netlify NS1 for zero credits. DNS is not in any of the 6 credit meters.

2. **Netlify Blobs is free + unmetered** — no storage-at-rest charge, no API read/write charge. Verified with 12+ MB transfers = 0 credits. Backend is AWS S3 in us-east-2. Use for cold storage (pod registry snapshots, audit logs, scrape dumps, anything > KV's 25 MB per-key cap).

3. **Netlify build-as-compute is free via preview deploys** — 15 min × 2 vCPU × 4 GB RAM × 9 GB disk per preview deploy. Preview deploys = 0 credits. Production deploys = 15 credits each. Use GitHub Actions cron to trigger preview deploys for batch jobs.

4. **Netlify Functions cost ~0.0006 credits per invocation** (1 GB × 200ms × 10 cr/GB-hr) — but the credit API lags 5-30 min, so real-time measurement is unreliable. The dashboard is authoritative. For planning: ~540K invocations/month to exhaust 300 credits (compute-only).

5. **Netlify Edge Functions are billed as web requests** (2 cr/10K) — so 1M invocations/month = 200 credits = ⅔ of the Free pool. Tight for routing compute. CF Workers (100K req/day = 3M/month, free, dedicated per account) is strictly more cost-effective for routing. Edge Functions CAN'T be deployed via API draft deploys — only via `netlify deploy` CLI or Git push (which runs the build process that picks up `netlify.toml` edge function declarations).

6. **Netlify Free has a WAF** — `firewall_enabled: true, traffic_rules: true, max_traffic_rules: 2, max_rules_per_set: 2, max_ips_per_rule: 3, max_countries_per_rule: 3`. The public REST API returns 404 for rule CRUD; configuration is bb-api-only (needs `_nf-auth` cookie from browser DevTools).

7. **Netlify has Traffic Splits** — a built-in A/B feature. Public API returns 404; bb-api sample returns `[]` (empty). Exact configuration syntax unprobed (needs `_nf-auth` cookie). Could enable native A/B testing for Netlify-hosted sub-zones without a CF Worker.

8. **Grandfathered Netlify accounts (pre-Sep-2025) are ~6.7× more bandwidth-capable** than credit-based Free (~100 GB vs ~15 GB max). The user's "quite a few grandfathered accounts" should be allocated to high-traffic sub-zones (app, users, api) — NOT to DNS (free regardless) or build-as-compute (preview deploys are 0 credits regardless of plan).

9. **One Free team per Netlify user** — hard API-enforced. The multiplier is N distinct Netlify users (separate email + GitHub OAuth each) = N × 300 credits, fully siloed. Heavy operational tax.

10. **The apex is on Netlify DNS (NS migrated from CF)** — CF zone stays "active" even with NS on Netlify, so all zone-level CF features (Worker Routes, KV, Email Routing, WAF, Universal SSL) continue to work. This enables the CNAME → workers.dev pattern for per-account pod isolation (each pod Worker in its own CF account, CNAME'd from Netlify apex zone).

---

## 1. Netlify's role in the architecture

### 1.1 What Netlify IS used for (live on sonicloud.app)

| Role | Status | Cost |
|---|---|---|
| **Apex DNS zone** (`sonicloud.app`) | ✅ Live (NS on Netlify NS1 p02 pool) | Free (no credit meter) |
| **Sub-zone DNS** (users/app/content/corp/api/cdn — 6 zones) | ✅ Live (each on separate NS1 pool) | Free |
| **Blobs cold storage** (scraper account) | ✅ Live (`site:hn-scrapes` store per findings-report) | Free (unmetered) |
| **Build-as-compute** (scraper site preview deploys) | ✅ Live (used for batch scraping) | Free (preview deploys = 0 cr) |
| **Functions** (scraper site /api/scrape endpoint) | ✅ Live (7 functions deployed) | ~0.0006 cr/invocation |
| **Edge Functions** | ❌ Not deployed (API draft deploy doesn't pick up edge function declarations; needs CLI/Git) | Would cost 2 cr/10K invocations |
| **Traffic Splits** (native A/B) | ❌ Not configured | Free feature, bb-api only |
| **WAF / Traffic Rules** | ❌ Not configured (capabilities available: 2 rules, 2 rules/set, 3 IPs/countries per rule) | Free feature, bb-api only |
| **Email Routing** (sub-zones) | ❌ Not configured (CF Email Routing is apex-only; sub-zones on Netlify need ImprovMX or SES) | ImprovMX free / SES $0.10/1K |

### 1.2 What Netlify is NOT used for (and why)

| Role | Why not | What's used instead |
|---|---|---|
| **Edge routing compute** (apex Worker) | Netlify Edge Functions = 2 cr/10K invocations. 1M inv/mo = 200 cr = ⅔ of pool. Too tight. | CF Workers (100K req/day = 3M/mo, free, dedicated per account) |
| **Per-pod compute** | Same — Edge Functions too expensive for routing | Per-pod CF Workers (own CF account, own 100K req/day) |
| **SSR pages** (docs/blog) | Vercel Hobby has better Next.js DX + 100 GB BW free (non-commercial only) | Vercel Hobby |
| **Served static assets** (CDN) | Netlify bandwidth = 20 cr/GB; CF R2 = zero egress | CF R2 (10 GB free) |
| **Hot key-value storage** (pod registry) | Netlify Blobs read latency ~860-900ms (measured from HK container via `06_blobs_pod_registry_test.py`, 3 trials) — too slow for hot path | CF KV (~20ms warm, measured live) |
| **Edge SQL** | Netlify has no equivalent | CF D1 (5 GB free) |
| **Cron triggers** | Netlify scheduled functions didn't reliably execute (per findings-report) | CF Cron Triggers (5 free per account) + GitHub Actions (2000 min/mo free) |
| **Email routing** (apex) | CF Email Routing is already set up on the CF zone (which stays active even with NS on Netlify) | CF Email Routing (free, native) |

### 1.3 The architecture diagram (Netlify's parts highlighted)

```
Registrar (Spaceship)
  │ NS → Netlify NS1 (p02 pool)                    ← NETLIFY: free DNS
  ▼
Netlify apex DNS zone: sonicloud.app               ← NETLIFY: free DNS, 41 records
  │
  │ ALIAS  sonicloud.app         → sonicloud-root-worker.sonicloud.workers.dev
  │ CNAME  www.sonicloud.app     → sonicloud.app
  │ CNAME  app.sonicloud.app      → sonicloud-root-worker.sonicloud.workers.dev  (pod pattern)
  │ CNAME  docs.sonicloud.app    → cname.vercel-dns.com                           ← Vercel Hobby
  │ CNAME  blog.sonicloud.app    → cname.vercel-dns.com                           ← Vercel Hobby
  │ NS     users.sonicloud.app   → dns1-4.p07.nsone.net  ← NETLIFY: per-subdomain isolation
  │ NS     content.sonicloud.app → dns1-4.p07.nsone.net
  │ NS     corp.sonicloud.app    → dns1-4.p05.nsone.net
  │ NS     api.sonicloud.app     → dns1-4.p09.nsone.net
  │ NS     cdn.sonicloud.app     → dns1-4.p08.nsone.net
  │ MX     sonicloud.app         → route1/2/3.mx.cloudflare.net  (CF Email Routing)
  │ TXT    sonicloud.app         → SPF, DMARC, MTA-STS
  │ CAA    sonicloud.app         → cloudflare.com, letsencrypt.org
  │
  ▼
CF zone sonicloud.app (STILL ACTIVE even with NS on Netlify)
  │ Apex Worker: sonicloud-root-worker (v3.0.1)     ← CF: KV router + geo + cron + A/B
  │ KV: POD_REGISTRY (pod routes + ab_config)        ← CF: free, ~20ms reads
  │ Cron: every 5 min (health-check failover)         ← CF: 5 free triggers
  │ Email Routing: enabled                             ← CF: free, native
  │ Universal SSL: covers *.sonicloud.app              ← CF: free
  │
  ▼
Per-account pod Workers (via CNAME → workers.dev)   ← CF: per-account isolation
  Current: app.sonicloud.app → sonicloud-root-worker.sonicloud.workers.dev (same account)
  Target: app-pod-01.sonicloud.app → <pod-01>.<pod-acct>.workers.dev (different account)

SEPARATE (not in request path):
  Netlify Blobs (scraper account)                   ← NETLIFY: free cold storage
    site:scraper-results store — scrape dumps, audit logs
  Netlify build-as-compute (scraper account)         ← NETLIFY: free batch compute
    Preview deploys triggered by GitHub Actions cron
    15 min × 2 vCPU × 4 GB per run, 0 credits
  Netlify Functions (scraper account)                ← NETLIFY: ~0.0006 cr/invocation
    /api/scrape endpoint — on-demand scraping
```

---

## 2. Credit budget — the authoritative numbers

### 2.1 The 6 credit meters (from findings-report, verified via dashboard not API)

> **Important**: The credit API (`GET /accounts/{id}` → `capabilities.credits.used`) lags 5-30+ minutes behind the dashboard. The user confirmed: "netlify api never shows credit correctly so ignore the api it's worthless." The numbers below are from the prior research's dashboard-verified measurements, not API readings.

| Meter | Credit rate | What triggers it | What's FREE |
|---|---|---|---|
| **Production deploys** | 15 credits each | Successful prod deploy (`netlify deploy --prod` or `POST /sites/{id}/deploys` without `draft:true`) | Failed deploys, rollbacks, preview deploys, branch deploys |
| **Compute** | 10 credits / GB-hour | Functions × wall-clock × memory (GB). Background Functions, Scheduled Functions, Agent Runners, DB compute all included | Build process (not metered on credit plans) |
| **AI inference** | 180 credits / $1 USD | Agent Runners model usage, AI Gateway | (not used in this architecture) |
| **Bandwidth (egress)** | 20 credits / GB | Static assets served, function API responses, image CDN, file downloads, DB egress | Ingress (downloads from internet INTO Netlify), Blob API reads/writes |
| **Web requests** | 2 credits / 10,000 | Page views, function API calls, asset requests, **Edge Function invocations** | (no free tier — all requests count) |
| **Forms submissions** | Free / unlimited | Form submissions | All form submissions |

### 2.2 What's NOT metered (verified, no meter exists)

| Activity | Cost | Why |
|---|---|---|
| **DNS zone creation** | 0 credits | No DNS meter in capabilities |
| **DNS queries** | 0 credits | NS1 anycast handles resolution; no per-query charge |
| **DNS record CRUD** | 0 credits | No DNS record meter |
| **Blob storage at-rest** | 0 credits | No `blobs_storage` meter exists |
| **Blob API read/write traffic** | 0 credits | No `blob_operations` meter; 12+ MB transferred = 0 credits (verified) |
| **Function-initiated downloads (ingress)** | 0 credits | ~30 MB downloaded through function, bandwidth meter unchanged |
| **Build minutes** | 0 credits | Not metered on credit plans (legacy had 300 min/mo) |
| **Preview deploys** | 0 credits | Explicit in docs; verified multiple times |
| **Branch deploys** | 0 credits | Explicit in docs |
| **Failed production deploys** | 0 credits | Explicit in docs |
| **Rollbacks** | 0 credits | Explicit in docs |
| **Forms submissions** | 0 credits | Free and unlimited |

### 2.3 Effective free-tier capacity (if all 300 credits spent on one meter)

| Meter-only | Max capacity | Practical implication |
|---|---|---|
| Bandwidth-only | ~15 GB/month (300 cr ÷ 20 cr/GB) | Low for a high-traffic site |
| Web-requests-only | ~1.5M requests/month (300 cr ÷ 2 cr × 10K) | Includes Edge Function invocations |
| Compute-only | ~30 GB-hours (300 cr ÷ 10 cr/GB-hr) | ~540K function invocations at 200ms each |
| Production-deploys-only | 20 deploys/month (300 cr ÷ 15 cr) | Low for CI/CD |

### 2.4 Grandfathered vs credit-based — the user's "finite resource" problem

| Dimension | Grandfathered (pre-Sep-2025) | Credit-based Free (post-Sep-2025) | Multiplier |
|---|---|---|---|
| Bandwidth | 100 GB/month hard cap | ~15 GB max if all 300 credits on bandwidth | **6.7×** |
| Serverless functions | 125K invocations/site/month | ~30 GB-hours shared | Per-site vs shared |
| Edge function invocations | 1M/month | ~1.5M max (if 100% on web requests) | Roughly tied |
| Build minutes | 300 min/month hard | Not metered (but prod deploys cost 15 cr) | Different shape |
| Sites/projects | 500 | 500 (sharing one 300-credit pool) | Same cap, smaller effective budget |
| When limits exceeded | Hard stop on that meter | All projects paused until next cycle | Worse blast radius |

**The user's constraint**: "i do have quite a few grand-fathered netlify with better free tier usage, but those are finite resource and i have more projects / sites than these account can fit."

**Allocation strategy**:

| Workload type | Where to host it | Why |
|---|---|---|
| **High-traffic production site** (app, users, api) | **Grandfathered account** (legacy 100 GB BW) | Bandwidth is the binding constraint; legacy gives 6.7× more |
| **Low-traffic marketing site** (docs, blog) | **Vercel Hobby** (non-commercial) or **credit-based Netlify** | Either works; Vercel has better Next.js DX |
| **DNS zones** (apex + sub-zones) | **Credit-based Netlify** (DNS is free regardless of plan) | Doesn't consume credits; doesn't benefit from grandfathering |
| **Cold storage** (scrape dumps, audit logs) | **Netlify Blobs** (any Netlify account) | Unmetered storage; works on both legacy and credit |
| **Build-as-compute** (batch jobs) | **Credit-based Netlify** preview deploys | 0 credits per preview deploy; doesn't benefit from grandfathering |
| **Edge routing compute** | **CF Workers** (not Netlify) | CF gives 100K req/day free, dedicated per account; Netlify Edge Functions cost 2 cr/10K |
| **Per-pod compute** | **Per-pod CF Workers** (not Netlify) | Each pod gets own 100K req/day budget |
| **Email routing** (apex) | **CF Email Routing** | Free, native to CF zone (which stays active) |
| **Email routing** (sub-zones) | **ImprovMX** (free, 25 aliases) or **AWS SES** ($0.10/1K) | CF Email Routing is apex-only |

**Spread rule**: Don't put more than ~20-30 active sites on one grandfathered account. Spread the rest across credit-based accounts (1 credit-based account per ~5-10 sites, since 300 cr / 5 sites = 60 cr per site = ~3 GB bandwidth per site per month).

---

## 3. Netlify capabilities that are available but not yet used

### 3.1 WAF / Traffic Rules (newly discovered — prior research missed this)

Account capabilities show (verified live 2026-08-17 via `20_credit_state_audit.py` for the 3 fields below; per Netlify pricing page for the 3 `max_*` fields not in the audit JSON):
```
firewall_enabled: { included: true }                  ← verified live
traffic_rules: { included: true }                    ← verified live
max_traffic_rules: { included: 2, used: 0 }           ← verified live (Free: 2 rules)
max_rules_per_set: { included: 2, used: 0 }           ← per Netlify pricing page (Free: 2 rules per set)
max_ips_per_rule: { included: 3, used: 0 }            ← per Netlify pricing page (Free: 3 IPs per rule)
max_countries_per_rule: { included: 3, used: 0 }      ← per Netlify pricing page (Free: 3 countries per rule)
```

Pro plan: 5 rules / 10 rules per set / 50 IPs / 50 countries.

**Status**: Public REST API returns 404 for all rule CRUD endpoints. The `/sites/{id}/traffic_rules_config` path returns 401 (exists but PAT can't read). Configuration is bb-api-only (needs `_nf-auth` cookie from browser DevTools).

**Implication**: If a sub-zone is hosted on Netlify (a Netlify site attached to the sub-zone), you get a basic WAF — 2 traffic rules, 3 IPs/countries per rule. Limited but present. Useful for per-zone rate-limiting or geo-blocking on a budget. NOT a replacement for CF WAF (which gives 1 managed + 5 custom per zone on Free).

### 3.2 Traffic Splits (built-in A/B)

The bb-api has `GET /sites/{id}/traffic_splits` which returns a list (empty in the sample). Public API returns 404.

**Status**: Feature exists. Exact configuration syntax (branch-based? percentage-based?) unprobed. Needs `_nf-auth` cookie to test.

**Implication**: If Traffic Splits turns out to be branch-based A/B, it could simplify the apex Worker for Netlify-hosted sub-zones (no need for the CF Worker's A/B cookie+hash logic — Netlify does it natively). But since the apex router is already on CF Workers (and A/B is already implemented there as of v3.0.1), Traffic Splits would be redundant unless a sub-zone is hosted entirely on Netlify.

### 3.3 Edge Functions (deployed via filesystem convention)

Edge Functions live at `netlify/edge-functions/<name>.ts` and are declared in `netlify.toml`:
```toml
[[edge_functions]]
  function = "router"
  path = "/*"
```

**Key limitation discovered (2026-08-17)**: Edge Functions declared in `netlify.toml` are only picked up by the **build process** (triggered by `netlify deploy` CLI or Git push). API draft deploys (`POST /sites/{id}/deploys` with `draft:true` + file upload) do NOT process `netlify.toml` — the edge function declarations are ignored. Tested live: uploaded `netlify/edge-functions/edge-health.ts` + `netlify.toml` via API draft deploy → deploy succeeded but edge function returned 404.

**Implication**: Edge Functions can only be deployed via CLI or Git — not via API. This is a real limitation for automated provisioning. The `scripts/30_provision_pod.py` flow would need to use `netlify deploy` CLI (via GitHub Actions or local execution) rather than direct API calls for any sub-zone that needs Edge Functions.

---

## 4. Live state (verified 2026-08-17 via `25_full_live_audit.py`)

### 4.1 DNS

- **Apex NS**: `dns1-4.p02.nsone.net` (Netlify NS1, p02 pool) — confirmed at 1.1.1.1, 8.8.8.8, 9.9.9.9
- **Apex A**: `172.67.190.113`, `104.21.19.229` (CF anycast — via ALIAS → workers.dev)
- **app.sonicloud.app CNAME**: `sonicloud-root-worker.sonicloud.workers.dev` → resolves to CF anycast IPs
- **docs/blog CNAME**: `cname.vercel-dns.com` → Vercel
- **Sub-zone NS** (users/content/corp/api/cdn): each on separate NS1 pool (p07/p07/p05/p09/p08)

### 4.2 Cloudflare

- **Zone**: `b09e8c12f3cf7058d42e03d0c6b0d077`, status `active` (even with NS on Netlify)
- **Worker Routes**: 5 routes (sonicloud.app/*, www.sonicloud.app/*, app.sonicloud.app/*, app-test-01.sonicloud.app/*, test-cf-worker.sonicloud.app/*) → all bound to `sonicloud-root-worker` (or `app-test-01-worker`)
- **KV namespaces**: 1 (`POD_REGISTRY`, id `f5c32d0fdd9f4b18b3c508969224f239`)
- **Cron schedules**: 1 (`*/5 * * * *` — every 5 min, created 2026-08-17T09:08:29Z)
- **workers.dev subdomain**: `sonicloud`
- **Email Routing**: enabled, status `ready`
- **Pod registry (KV routes key)**: `/app/` → `app.sonicloud.app` (100% weight, active, regions: ["*"])
- **A/B config**: enabled=false, variant_b_percent=50, salt=sonicloud-ab-salt-v1

### 4.3 Netlify

- **DNS account** (`6a7f8f3637d951add835956d`): credit-free, 300 plan_credits, 0 used (per API — dashboard may differ), 0 sites, 6 DNS zones
- **Scraper account** (`6a7e84d51cdeff620a5cf5a0`): credit-free, 300 plan_credits, 0 used (per API — dashboard may differ), 7 sites, 0 DNS zones
- **DNS zones**: 6 (sonicloud.app apex with 41 records + 5 sub-zones with 7 records each)
- **Both accounts have** (per `20_credit_state_audit.py`): `firewall_enabled: true`, `traffic_rules: true`, `max_traffic_rules: 2`, `swar_auto_topup_credits: 400`

### 4.4 HTTP reachability

| URL | Status | Notes |
|---|---|---|
| `sonicloud.app/` | 200 | Apex Worker HTML landing |
| `sonicloud.app/__health` | 200 | Worker v3.0.1, pod_count=-1 (no admin token) |
| `sonicloud.app/__routes` | 401 | Admin-token gate working |
| `sonicloud.app/app/test` | 302 | → `https://app.sonicloud.app/app/test` |
| `www.sonicloud.app/` | 200 | Worker responds |
| `app.sonicloud.app/__health` | 200 | Worker v3.0.1 via CNAME → workers.dev |
| `docs.sonicloud.app/` | 200 | Vercel |
| `blog.sonicloud.app/` | 200 | Vercel |
| `users.sonicloud.app/` | None | Sub-zone A record = 192.0.2.1 placeholder (no backend) |
| `api.sonicloud.app/` | None | Same — placeholder |
| `cdn.sonicloud.app/` | None | Same |
| `content.sonicloud.app/` | None | Same |
| `corp.sonicloud.app/` | None | Same |

### 4.5 Vercel

- 3 projects: `template`, `sonicloud-blog`, `sonicloud-docs` (all with production target)

---

## 5. Open Netlify-specific questions

### 5.1 Needs user-provided `_nf-auth` cookie (browser access)

1. **Netlify Traffic Splits bb-api shape** — `GET /sites/{id}/traffic_splits` returns `[]` in the sample. Need to create a split via dashboard, capture the request via DevTools Network tab, reverse-engineer the bb-api request body. If branch-based A/B, could simplify routing for Netlify-hosted sub-zones.

2. **Netlify WAF / Traffic Rules CRUD** — capabilities show 2 rules available on Free. Need to probe `POST/PUT/DELETE /access-control/bb-api/api/v1/sites/{id}/traffic_rules` with cookie auth. If usable, adds a free defense layer for Netlify-hosted sub-zones.

### 5.2 Needs user-provided grandfathered account IDs + tokens

3. **Grandfathered account detection** — what does `type_slug` look like for pre-Sep-2025 accounts? Likely `legacy-free` or `starter-free`. Need a known grandfathered account ID + token to verify. Once known, can detect programmatically and integrate into the credit allocation strategy.

4. **Grandfathered account bandwidth reality** — is the 100 GB/month hard cap actually enforced? Does the dashboard show separate bandwidth metering? Need dashboard access to verify.

### 5.3 Needs CLI/Git (can't be done via API)

5. **Edge Function deploy + cost measurement** — Edge Functions can only be deployed via `netlify deploy` CLI or Git push (API draft deploys don't process `netlify.toml`). Need to deploy one via CLI, then measure the actual web-requests meter impact (2 cr/10K claimed, never validated). The credit API lags 5-30 min so dashboard is needed for authoritative state.

### 5.4 Architectural (no user action needed — just decisions)

6. **Should any sub-zone move to Netlify hosting?** Currently all 6 sub-zones have placeholder A records (192.0.2.1). If a sub-zone needs hosting, the options are: (a) Netlify site (on grandfathered account for high traffic, credit-based for low traffic), (b) Vercel (non-commercial only), (c) CF Worker (per-pod), (d) Third-party SaaS CNAME. The choice depends on the sub-zone's traffic profile and commercial status.

7. **Should the apex Worker stay on CF or move to Netlify Edge Functions?** Current: CF Workers (100K req/day free, no credit pool). Alternative: Netlify Edge Functions (2 cr/10K = 200 cr for 1M inv = ⅔ of pool). CF is strictly more cost-effective. Keep on CF.

---

## 6. Summary — what Netlify does in this architecture

**Netlify's role is narrow but critical**: DNS hosting (free, unmetered, per-subdomain isolation) + cold storage via Blobs (free, unmetered) + build-as-compute via preview deploys (free, 0 credits). Netlify is NOT the routing compute layer (CF Workers is — more cost-effective) and NOT the hot storage layer (CF KV is — faster reads). The finite grandfathered accounts should be allocated to high-traffic sub-zones that need bandwidth (legacy 100 GB vs credit-based ~15 GB), NOT to DNS (free regardless) or build-as-compute (preview deploys are 0 credits regardless).

**What's live**: Apex DNS on Netlify (41 records, free), 6 sub-zones on Netlify (free), CF zone still active (Worker Routes + KV + Email Routing + SSL all work), apex Worker v3.0.1 on CF (KV router + geo + cron + A/B + admin gate), pod Worker accessible via CNAME → workers.dev, Vercel docs/blog, scraper site with Functions + Blobs + build-as-compute on a separate Netlify account.

**What's pending**: Per-account pod isolation live test (needs CF SUB scoped token), grandfathered account integration (needs user-provided IDs + tokens), Traffic Splits + WAF bb-api probe (needs `_nf-auth` cookie), Edge Function deploy via CLI (API doesn't work for edge function declarations).
