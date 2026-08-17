# Cloud Architecture — sonicloud.app Custom Domain Infra Kit

> **Companion docs**: `01_GROUND_TRUTH.md` (everything I verified before writing this), `../netlify-ns-handoff.md` (DNS-specific carry-over note), `domain-infra-kit-main.zip` → `infra/ARCHITECTURE.md` (current state), `infra/FLEET.md` (the originally-planned target).
>
> **Purpose**: This document is the cloud-architecture deep dive the user asked for. It covers (a) where the apex site lives and why, (b) the multi-pod re-routing pattern for free-tier-account spreading, (c) geo-routing for regional pods, (d) A/B landing test patterns, (e) the compute+page split given Netlify's limited credits, and (f) the credit budget allocation strategy for the user's mix of grandfathered vs credit-based Netlify accounts. Every claim here is either (i) cited from the prior research in `01_GROUND_TRUTH.md` or (ii) live-tested against sonicloud.app on 2026-08-17.
>
> **Audience**: The next session operator who picks up this work. Read `01_GROUND_TRUTH.md` first.

---

## TL;DR

1. **Keep apex on Cloudflare, not Netlify.** FLEET.md's planned pivot to Netlify apex was the right call when CF's Section 2.8 "non-HTML ban" was a risk; that ban was removed in the 2026 TOS, so the case for moving apex is now materially weaker. CF at apex gives: KV/D1/R2 native, no bandwidth meter, 100K req/day Worker free, IATA-code geo (`request.cf.colo`) which is strictly finer than Netlify's `context.geo.subdivision.code`, more WAF rules per zone, native Email Routing. The TOS-safety argument is moot.

2. **The pod fleet uses Worker Routes on the apex zone** (NOT two-level NS delegation — that pattern is BLOCKED by CF error 1116 on Free tier; subdomain zones require Enterprise $5K+/mo). Per-pod Workers (e.g., `app-test-01-worker`) are deployed to CF MAIN account, each bound via Worker Routes to a hostname like `app-test-01.sonicloud.app` with A record `192.0.2.1` proxied=true (canonical CF pattern). This gives per-Worker isolation (own KV/D1/R2 bindings, own logs). Per-ACCOUNT isolation requires a separately-registered domain (e.g., `sonicloud-pods.com`) — one domain covers the whole pod fleet, ~$10/yr. Validated live 2026-08-17: pod Worker responds at `app-test-01.sonicloud.app/__health` with `{"pod":"app-test-01",...}`, apex Worker 302-redirects `sonicloud.app/app/test` to it.

3. **The edge router is a CF Worker at the apex** (`sonicloud-root-worker` v2.1.0, deployed live 2026-08-17), bound via Worker Routes to `sonicloud.app/*`. It reads a pod registry from CF KV namespace `POD_REGISTRY` (id `f5c32d0fdd9f4b18b3c508969224f239`) on each `/app/*` request, picks a pod via weighted random, and 302-redirects. **Caveats (v2.1.0)**: (a) NO automatic failover — Phase 3 Cron Trigger not yet implemented; pod outage = visible user errors. (b) NO geo-routing — `pickPod` uses weighted random only (ignores `request.cf.colo` for selection; the field IS read for telemetry in `/__health`). Geo-routing is Phase 3. (c) NO A/B stickiness — uses `Math.random()`, not the A2+A3 cookie+hash hybrid (§4.2). That's Phase 4. (d) `/__routes` and `/__health` (verbose fields like `pod_count`) require `x-admin-token` header — gated in v2.1.0 (per opus peer review WAVE-1 P1-1). Live-measured latency: KV read adds ~20ms over no-KV baseline (44ms → 65ms); 302 hop adds ~40ms over pod direct (40ms → 85ms). KV writes are 1K/day free — enough for pod registry updates + every-5-min health-check Cron (288 writes/day fits; per-minute Cron at 1440 writes/day would exceed, would need Workers Paid $5/mo).

4. **Netlify free tier is best used for DNS + Blobs + build-as-compute, NOT for routing compute.** Netlify Edge Functions are billed as web requests (2 credits / 10K), so 1M invocations/month = 200 credits = ⅔ of the Free pool. That's tight. CF Workers (100K req/day = 3M/month, free) is strictly more cost-effective for routing. Netlify Blobs (unmetered storage + free API reads, verified 12 MB = 0 credits) is the right place to store large cold artifacts — full pod definitions, audit logs, scrape results, anything > KV's 25 MB per-key cap.

5. **Netlify Free DOES have a WAF** — the prior research missed this. `firewall_enabled: true`, `traffic_rules: true`, `max_traffic_rules: 2`, `max_rules_per_set: 2`, `max_ips_per_rule: 3`, `max_countries_per_rule: 3` on Free; 5 rules / 10 rules per set / 50 IPs / 50 countries on Pro. The public REST API doesn't expose rule CRUD (all endpoints I probed returned 404); only the bb-api (cookie-auth) can configure rules. This means a Netlify-hosted sub-zone has a basic WAF available — useful for per-zone rate-limiting or geo-blocking on a budget.

6. **Grandfathered Netlify accounts (pre-Sep-2025) are ~6.7× more bandwidth-capable** than credit-based Free accounts (~100 GB vs ~15 GB max if all 300 credits spent on bandwidth). The user's "quite a few grandfathered accounts" should be allocated to high-traffic sub-zones (app, users, api) — NOT to DNS (which is free regardless) or to low-traffic static sites. Allocation strategy in §6.

7. **Netlify Traffic Splits exists** as a feature (the bb-api exposes `GET /sites/{id}/traffic_splits` which returns a list — empty in the sample I have, because the test site had none). The public REST API returns 404. The configuration syntax (branch-based? percentage-based?) requires a bb-api probe to learn. **Status: API exists, exact shape not yet probed** — recommend a follow-up probe with bb-api cookie access.

---

## 1. Apex site handling — where it lives, why, and what it does

### 1.1 Current state (live-probed 2026-08-17)

The apex `sonicloud.app` is on Cloudflare:
- CF zone ID `b09e8c12f3cf7058d42e03d0c6b0d077` in CF MAIN account `14f6f36e01410c2360f3de636b18a7b0`.
- NS at registrar (Spaceship) was migrated to CF: `giancarlo.ns.cloudflare.com` + `hazel.ns.cloudflare.com`. Public DNS confirms.
- Apex A record = `192.0.2.1` (RFC 5737 documentation IP, not routable), `proxied=true`. With CF proxy on, public DNS returns CF anycast IPs `104.21.17.247` + `172.67.178.229` instead of 192.0.2.1.
- A CF Worker `sonicloud-root-worker` is bound via Worker Routes to `sonicloud.app/*` and `www.sonicloud.app/*`. The Worker intercepts traffic at the CF edge, so 192.0.2.1 is never actually contacted.
- The Worker serves a placeholder HTML landing page at `/` and returns JSON health at `/__health` (`{"ok":true,"service":"sonicloud.app root","worker":"sonicloud-root-worker","ts":"...","region":"HKG"}`). The `region` field uses `request.cf?.colo` — IATA colo code (e.g., HKG, LAX, SJC).
- Worker bindings: empty array (no KV/D1/R2/Durable Objects). Compatibility date 2024-09-23, flag `nodejs_compat`.
- CF Email Routing is enabled and `ready` on the apex zone. MX records = `route1/2/3.mx.cloudflare.net`. SPF, DKIM, DMARC, MTA-STS, CAA all configured.
- 2FA enforcement on CF MAIN: `enforce_twofactor: true` (per the kit's `21_mint_admin_token.py` + `06_configure_root_zone.py` workflow — needs live re-verification, the kit's docs say it's done).

### 1.2 Why keep apex on CF (not pivot to Netlify as FLEET.md planned)

FLEET.md's recommendation to move apex to Netlify was sound when written (Aug 2026), because at that time:
- CF Section 2.8 "non-HTML content" ban was a recent memory — JSON API proxy on Workers was a TOS risk.
- Netlify's `_redirects` with `Country=` rules gave free geo-routing at the edge.
- Netlify AUP allowed commercial use on free tier; Vercel Hobby prohibited it.

The TOS landscape has shifted in 2026:
- **CF Section 2.8 is REMOVED in the 2026 TOS** (verified in the kit's `KIT.md` "Critical TOS findings" section). JSON API proxy on Workers is now safe.
- **Netlify's `_redirects` with `Country=` rules is country-level only** — cannot distinguish US-East from US-West (verified in FLEET.md Option A discussion). For sub-country routing you need Netlify Edge Functions, which are billed as web requests (2 credits / 10K = 200 credits for 1M req/mo = ⅔ of Free pool). That's tight.
- **CF Workers Free gives 100K req/day = 3M req/month at zero credit cost** and `request.cf.colo` (IATA code — distinguishes LAX from SJC within California). Strictly more cost-effective and more granular.

Material capability differences as of 2026-08-17 (live-verified):

| Concern | CF apex (current) | Netlify apex (FLEET target) | Winner |
|---|---|---|---|
| TOS safety for commercial | ✅ Section 2.8 removed in 2026 | ✅ AUP allows commercial | Tied |
| Bandwidth at apex | ✅ Not metered (Workers don't meter bandwidth) | ⚠️ 100 GB free, then 20 cr/GB | **CF** |
| Edge compute | ✅ CF Workers: 100K req/day = 3M/mo free | ⚠️ Netlify Edge Functions: 1M/mo as web requests = 200 cr | **CF** |
| Edge compute cold start | ✅ Sub-ms (V8 isolates) | ⚠️ Sub-ms (Deno Deploy) — similar | Tied |
| Geo granularity | ✅ `request.cf.colo` (IATA, e.g., LAX vs SJC) | ⚠️ `context.geo.subdivision.code` (US state, e.g., CA — cannot split LAX vs SJC) | **CF** |
| Edge storage (KV) | ✅ 1 GB free, 100K reads/day, 1K writes/day | ❌ No equivalent (Blobs is account-scoped, not edge) | **CF** |
| Edge SQL (D1) | ✅ 5 GB free, 5M rows read/day | ❌ No equivalent | **CF** |
| Object storage (R2) | ✅ 10 GB free, zero egress | ❌ No equivalent (Blobs has no public URLs) | **CF** |
| WAF at apex | ✅ CF Free: 1 managed rule + 5 custom rules per zone | ⚠️ Netlify Free: 2 traffic rules, 2 rules/set, 3 IPs/countries per rule (NEW FINDING — see §5) | **CF** |
| Email Routing | ✅ Native on CF apex (200 dest, 3K outbound/mo, free) | ❌ Not available on Netlify apex (CF Email Routing is zone-level, only works on CF zones; would need ImprovMX or SES) | **CF** |
| Native redirect rules | ✅ CF Redirect Rules (free) | ✅ Netlify `_redirects` (free, 2500 rules/site, Country= only) | Tied (CF more flexible, Netlify simpler) |
| DNS sub-zone support | ❌ Enterprise-only ($5K+/mo) | ✅ Free, first-class (`POST /api/v1/dns_zones`) | **Netlify** |
| Build-as-compute | ❌ Not a feature (CF Pages builds are limited) | ✅ 15 min/runtime × 2 vCPU × 4 GB × 9 GB disk, 0 credits via preview deploys | **Netlify** |
| Unmetered storage | ✅ R2 (10 GB, zero egress) | ✅ Blobs (no cap, free API reads/writes) | Tied (R2 has public URLs, Blobs doesn't) |
| SSO/private-by-default | ✅ Public by default | ⚠️ Private by default since Aug 2026 (must disable SSO via bb-api per site) | **CF** |

**Verdict**: CF wins on 9 dimensions, Netlify wins on 1 (DNS sub-zones — which is exactly the use case the kit already uses Netlify for, NOT the apex). The pivot to Netlify apex was the right call in 2025; in 2026 with Section 2.8 removed, it's a net regression. **Keep apex on CF.**

### 1.3 What the apex Worker should do (the routing role)

The apex `sonicloud-root-worker` is the natural place to put the edge router. The user said "we don't intend to serve [the apex site] but need to find way to handle well, as the root site is still quite critical." The apex is critical because:
- Brand entry point — anyone typing `sonicloud.app` lands here.
- Email Routing anchor (zone-level, must be apex).
- DMARC/MTA-STS/CAA anchor for the whole domain tree.
- Edge routing decision point — geo, A/B, pod selection.

The Worker has 5 patterns to choose from (the user's framing of "compute + page split" maps to a choice among these):

| Pattern | What it does | When to use |
|---|---|---|
| **P1. Pure redirect** | `sonicloud.app/*` → 302 to `app.sonicloud.app/*` | Lowest compute, no pod registry needed. Loses geo-routing. Use if you only have 1 pod. |
| **P2. Geo-aware 302 router** | Worker reads `request.cf.colo`, picks pod from KV registry, 302 redirects | The recommended pattern for v1+ multi-pod. ~5-15ms added. KV read on every request (~1ms warm). |
| **P3. Path-based reverse proxy** | `/api/*` → api pod, `/app/*` → app pod, `/docs/*` → docs Vercel | No 302 visible to user, but more compute per request. Good for unified UX, bad for SSR edge cases. |
| **P4. A/B landing** | Worker uses `request.cf.colo` + hash of IP/UA to assign variant A or B (sticky) | For landing-page experiments. Variant URLs are different pods or different static sites. |
| **P5. Hybrid** | Minimal HTML landing at `/` (brand + login CTA) + 302s `/app/*` to geo-routed pod | Best UX. Apex is a real page; everything else routes. |

**Recommendation for sonicloud.app v1**: **P5 (Hybrid)**. The apex Worker serves a real (small) landing page for `/` and `/__health`, and 302-redirects everything else (`/app/*`, `/api/*`, `/docs/*` if docs moves off Vercel, etc.) to the appropriate sub-zone. Pod selection for `/app/*` uses `request.cf.colo` against a KV-backed pod registry. A/B testing is supported by adding a `variant` field to the KV registry entries — the Worker reads it and weights the random pick.

### 1.4 Concrete next steps for the apex — STATUS: DONE (live 2026-08-17)

The following steps have all been executed and validated end-to-end:

1. ✅ **Added KV namespace `POD_REGISTRY`** to CF MAIN account via `POST /accounts/{id}/storage/kv/namespaces` with `{"title":"POD_REGISTRY"}`. Namespace ID `f5c32d0fdd9f4b18b3c508969224f239`. The `root_zone_token` has KV Storage Write permission (verified live).
2. ✅ **Rewrote `sonicloud-root-worker`** to v2.0.0 with KV binding + routing logic. The Worker:
   - On `/__health`: returns JSON with `colo`, `ts`, `pod_count` (read from KV, fail-open if missing).
   - On `/`: returns HTML landing page (with links to /__health, /__routes, /app/test).
   - On `/app/*`: reads KV `POD_REGISTRY:routes` (JSON: `[{path_prefix, pods: [{hostname, weight, active, regions}]}]`), picks a pod via weighted random, 302 redirects preserving path + query.
   - On `/api/*`: similar (placeholder route — no api pod deployed yet).
   - On `/__routes`: debug endpoint returning the full pod registry JSON.
3. ✅ **Seeded the KV registry** with 2 routes: `/app/` → `app-test-01.sonicloud.app` (100% weight); `/api/` → `api.sonicloud.app` (placeholder, 100% weight).
4. ✅ **Deployed** via CF API (`PUT /accounts/{id}/workers/scripts/{name}` with multipart body containing metadata + script). The metadata includes the KV binding: `{"type":"kv_namespace","name":"POD_REGISTRY","namespace_id":"f5c32d0fdd9f4b18b3c508969224f239"}`.
5. ✅ **Tested** with `curl -sk -i https://sonicloud.app/__health` (200 JSON, `pod_count: -1` without admin token, `pod_count: 2` with token, `version: 2.1.0 (admin-token-gated debug)`) and `curl -sk -i https://sonicloud.app/app/test` (302 to `https://app-test-01.sonicloud.app/app/test`).
6. ✅ **Tested pod Worker** at `https://app-test-01.sonicloud.app/__health` (200 JSON with `pod: app-test-01`, `worker: app-test-01-worker`).
7. ✅ **Tested pod registry update** (added 2nd pod via KV write, no Worker redeploy): 20 requests split 9/11 between two pods (close to 50/50 target).
8. ✅ **Live-measured latency**: apex /__health (no KV) ~44ms; apex /__routes (KV read) ~65ms; apex /app/__health (302 hop + pod) ~85ms; pod direct /__health ~38ms.
9. ✅ **Admin-token gate verified** (v2.1.0): `/__routes` returns 401 without `x-admin-token` header; `/__health`'s `pod_count` field returns -1 without token (no fleet size leak). Routing (`/app/*`) still works without token (no auth needed for routing).

The apex Worker is now operational as a KV-backed pod router. The rest of this document describes the broader architecture this enables.

---

## 2. The pod fleet — multi-account re-routing for free-tier spreading

### 2.0 IMPORTANT CORRECTION (post live-test 2026-08-17)

The original two-level NS delegation pattern I proposed (CF apex → Netlify sub-zone → CF pod-zone in per-pod CF account) is **BLOCKED on Free tier**. Live testing revealed:

- **CF error 1116**: CF rejects `POST /zones` for any name that's a subdomain of an existing CF zone. Both `app-test-01.sonicloud.app` and `app-test-02.app.sonicloud.app` got 1116 ("Please ensure you are providing the root domain and not any subdomains"). The check fires for same-account sub-zones (verified live). **Whether the check fires for cross-account sub-zones on Free tier is NOT verified** — CF docs describe a "Cross-Account Subdomain Setup" feature (https://developers.cloudflare.com/dns/zone-setups/subdomain-setup/cross-account/) that may allow a sub-zone in account B if verified via TXT record. If cross-account works on Free, the per-account isolation upgrade path becomes much simpler (no separate domain registration needed). See §9 open question #9.
- **CF error 1014**: Proxied CNAMEs to `*.workers.dev` are blocked ("CNAME Cross-User Banned"). Cannot use CNAME → workers.dev as the pod binding mechanism.

**The corrected pod pattern** (validated live 2026-08-17): per-pod Workers bound via **Worker Routes on the apex zone**, with A records pointing to `192.0.2.1` proxied=true (the canonical CF pattern). This gives per-Worker isolation (own name, own KV/D1/R2 bindings, own logs) but NOT per-account isolation (all pod Workers share CF MAIN's 100K req/day budget).

For per-account isolation (the upgrade path): each pod's hostname must be on a **separately-registered domain** (e.g., `sonicloud-pods.com` for pod fleet). One domain registration (~$10/yr) covers the whole fleet — each pod is `pod-01.sonicloud-pods.com`, `pod-02.sonicloud-pods.com`, etc., all served by a single CF zone in account B with Worker Routes per pod. The apex Worker 302-redirects `/app/*` to the chosen pod's hostname on sonicloud-pods.com.

### 2.1 The pattern (target, revised)

```
sonicloud.app (CF apex zone, CF MAIN account)
  │ NS (already delegated) → CF anycast
  │
  │ Apex Worker: sonicloud-root-worker (CF MAIN, v2.1.0 — KV-backed router + admin-token-gated debug)
  │   - Reads POD_REGISTRY from CF KV on each /app/* /api/* request
  │   - 302 redirects to chosen pod hostname
  │
  │ Per-pod binding (live-validated 2026-08-17):
  │   A    app-test-01.sonicloud.app   192.0.2.1   (proxied=true)   ← CF MAIN apex zone
  │   Worker Route: app-test-01.sonicloud.app/* → app-test-01-worker (in CF MAIN)
  │   Pod Worker app-test-01-worker: own KV/D1/R2 bindings, own logs
  │
  │ Per-account isolation (UPGRADE PATH — requires separate domain registration):
  │   Register sonicloud-pods.com (or similar)
  │   CF zone sonicloud-pods.com in CF Account B
  │   A    pod-01.sonicloud-pods.com   192.0.2.1   (proxied=true)   ← CF Account B
  │   Worker Route: pod-01.sonicloud-pods.com/* → pod-01-worker (in CF Account B)
  │   Apex Worker 302: sonicloud.app/app/* → pod-01.sonicloud-pods.com/app/...
  │
  │ NS app.sonicloud.app → dns1-4.p01.nsone.net (Netlify sub-zone, Netlify Acct #1)
  │
  ▼
app.sonicloud.app (Netlify DNS zone — for non-pod sub-zone isolation)
  │ A app.sonicloud.app → 192.0.2.1 (placeholder — no real backend yet, or use Netlify site)
  │ (No pod sub-zones — pods live on sonicloud.app apex via Worker Routes, or on sonicloud-pods.com)
```

### 2.2 Why this revised pattern works

- **CF Worker Routes on the apex zone** is the canonical pattern for binding a Worker to a hostname on CF (the same pattern the kit uses for the apex Worker itself).
- **A record `192.0.2.1` proxied=true** is the canonical CF placeholder for worker-backed zones — CF's edge intercepts the traffic and routes to the Worker; 192.0.2.1 is never actually contacted. Documented at https://developers.cloudflare.com/dns/llms-full.txt.
- **Per-Worker isolation**: each pod Worker has its own name, own KV/D1/R2 bindings, own logs, own observability. Different pods can have completely different code.
- **Adding a pod**: deploy Worker + add A record + add Worker Route + update KV registry (4 API calls, all idempotent, ~10 seconds total).
- **Removing a pod**: set `active: false` in KV registry (immediate routing stop) + delete Worker Route + delete A record + delete Worker (cleanup, can be batched).
- **Per-account isolation**: requires a separate CF account + separate registered domain. ~$10/yr per pod fleet (one domain for the whole fleet). Each pod is a Worker in that account, bound via Worker Routes on the fleet's CF zone.

### 2.3 Live validation results (2026-08-17)

The corrected pod pattern has been deployed and validated end-to-end:

**Deployed**:
- CF KV namespace `POD_REGISTRY` (id `f5c32d0fdd9f4b18b3c508969224f239`) in CF MAIN account
- Pod registry seeded with 2 routes: `/app/` → `app-test-01.sonicloud.app` (100% weight); `/api/` → `api.sonicloud.app` (placeholder)
- Apex Worker `sonicloud-root-worker` v2.1.0 (KV-backed router + admin-token-gated debug endpoints) — `/__health`, `/__routes` (admin-gated), `/app/*` 302, `/` HTML
- Pod Worker `app-test-01-worker` (in CF MAIN, separate script) — `/__health`, `/` HTML, returns `pod: app-test-01`
- A record `app-test-01.sonicloud.app → 192.0.2.1` (proxied=true) in CF apex zone
- Worker Route `app-test-01.sonicloud.app/* → app-test-01-worker`

**Verified**:
- `curl https://sonicloud.app/__health` → 200 JSON with `pod_count: 2`, `version: 2.0.0`, `region: HKG`
- `curl https://sonicloud.app/__routes` → 200 JSON with the full pod registry (KV read works)
- `curl https://sonicloud.app/app/test` → **302 with `Location: https://app-test-01.sonicloud.app/app/test`** (routing works)
- `curl -L https://sonicloud.app/app/__health` → 302 followed by pod Worker's 200 response (full chain works)
- `curl https://app-test-01.sonicloud.app/__health` → 200 JSON with `pod: app-test-01`, `worker: app-test-01-worker` (pod Worker responds)
- KV update test: added 2nd pod (app-test-02.sonicloud.app, 50/50 weight) via KV write — no Worker redeploy needed. 20 requests to `/app/test` resulted in 9 → app-test-01, 11 → app-test-02 (close to 50/50; weighted random converges with more samples).

**Latency profile** (from HK container, 5 trials each):
| Endpoint | avg | min | max |
|---|---|---|---|
| `sonicloud.app/__health` (apex Worker, no KV) | 44ms | 37ms | 56ms |
| `sonicloud.app/__routes` (apex Worker + KV read) | 65ms | 41ms | 136ms |
| `sonicloud.app/app/__health` (302 hop + pod response) | 85ms | 71ms | 112ms |
| `app-test-01.sonicloud.app/__health` (pod direct) | 38ms | 33ms | 46ms |

**Observations**:
- KV read adds ~20ms (within CF docs' stated 1-50ms range; cold reads are slower than warm).
- 302 hop adds ~40ms over pod direct (~85ms vs ~40ms) — the extra round-trip costs ~2x at this latency. For very-high-traffic scenarios, consider path-based reverse proxy (P3) which avoids the 302.
- Pod direct latency is identical to apex Worker latency (~40ms) — both run at the CF edge, no cross-region hop.

### 2.4 Per-pod backend choice

Each pod is a CF Worker + (optional) backend database. The choices:

| Backend | Free tier | When to use |
|---|---|---|
| CF KV | 1 GB, 100K reads/day, 1K writes/day | Per-pod config, session state, small lookups |
| CF D1 | 5 GB, 5M rows read/day, 100K rows written/day | Per-pod relational data (users, sessions, audit logs) |
| CF R2 | 10 GB, zero egress | Per-pod file storage (uploads, media) |
| Supabase (own account) | 500 MB DB, 5 GB egress, 50K MAU, 7-day idle pause | Per-pod Postgres + auth + storage (when you need auth) |
| Neon (own account) | 100 CU-hours, 0.5 GB, 5-min idle suspend | Per-pod Postgres only (no auth/storage) |
| Netlify Blobs (own account) | Unmetered storage + free API reads | Per-pod cold storage (full audit logs, large blobs) |

**Recommendation**: Start with KV + D1 in the pod's CF account for state. Add Supabase per pod when auth/storage is needed. Use Neon only for pure-DB pods. Use Netlify Blobs (via the scraper account, or a new dedicated Netlify account) for cold-storage artifacts > 25 MB (KV's per-key cap).

### 2.5 Pod lifecycle automation (what `30_provision_pod.py` needs to do — REVISED)

The original kit's `30_provision_pod.py` stub described a flow based on the (now-blocked) two-level NS delegation pattern. The revised flow:

1. Read `infra/pods.csv` for the pod's row (pod_id, service, region, shard, cf_account_id, ...).
2. Read `infra/pods/<pod_id>.json` for the pod's secrets (CF token for the pod's CF account, plus backend tokens).
3. **If pod lives on apex zone (per-Worker isolation, no per-account)**:
   a. In CF MAIN account (using `root_zone_token`):
      - Deploy pod Worker via `PUT /accounts/{cf_main}/workers/scripts/<pod_id>-worker` (multipart with ES module).
      - Add A record in apex zone: `<pod_id>.sonicloud.app → 192.0.2.1` (proxied=true).
      - Add Worker Route: `<pod_id>.sonicloud.app/* → <pod_id>-worker`.
   b. Update apex KV `POD_REGISTRY:routes` with the new pod entry.
   c. Verify with `dig +short A <pod_id>.sonicloud.app @1.1.1.1` and `curl https://<pod_id>.sonicloud.app/__health`.
4. **If pod lives on a separate fleet domain (per-account isolation, requires domain registration)**:
   a. Register `sonicloud-pods.com` (one-time, manual via registrar).
   b. In CF Account B (manually created):
      - Create CF zone `sonicloud-pods.com`.
      - Mint scoped tokens for the new account (kit's `03_mint_scoped_tokens.py` pattern).
      - Deploy pod Worker via `PUT /accounts/{cf_account_b}/workers/scripts/<pod_id>-worker`.
      - Add A record in fleet zone: `pod-01.sonicloud-pods.com → 192.0.2.1` (proxied=true).
      - Add Worker Route: `pod-01.sonicloud-pods.com/* → pod-01-worker`.
   c. Update apex KV `POD_REGISTRY:routes` with `hostname: pod-XX.sonicloud-pods.com` (NOT `pod-XX.sonicloud.app`).
   d. Apex Worker 302: `sonicloud.app/app/*` → `pod-XX.sonicloud-pods.com/app/*`.

The deprovision flow is the inverse: set `active: false` in KV (immediate routing stop), wait for in-flight requests to drain, then tear down Worker Route + A record + Worker script.

---

## 3. Geo-routing — regional pods with sub-country granularity

### 3.1 The routing decision tree (apex Worker logic)

> **STATUS (v2.1.0, live 2026-08-17)**: The deployed apex Worker reads `request.cf.colo` only for `/__health` telemetry. The `pickPod` function uses **weighted random only** — `colo` is ignored for pod selection. Geo-routing is Phase 3 (§8). The pseudocode below describes the TARGET design, not the live behavior.

```
on request to /app/* at sonicloud.app:
  1. colo = request.cf.colo  (e.g., "LAX", "SJC", "JFK", "LHR", "HKG")
  2. routes = await POD_REGISTRY.get("routes", "json")  // ~1ms warm KV read
  3. match = routes.find(r => r.colo_pattern matches colo)  // exact match first
  4. if no match: match = routes.find(r => r.colo_pattern matches "*")  // fallback
  5. if match has multiple pods with weights: pickWeighted(match.pods)
  6. return Response.redirect(`https://${picked.hostname}${url.pathname}${url.search}`, 302)
```

`colo_pattern` examples:
- `"LAX,SJC,SEA,PDX,SAN,PHX,LAS,DEN"` — US West coast + mountain states
- `"JFK,EWR,IAD,ATL,MIA,ORD,DFW,BOS" — US East + central
- `"LHR,MAN,CDG,FRA,AMS,MAD,MXP,ARN" — Europe
- `"HKG,NRT,SIN,SYD,BKK" — Asia-Pacific
- `"*" — global fallback

### 3.2 Why `request.cf.colo` over `context.geo.subdivision.code`

| Granularity | CF `request.cf.colo` | Netlify `context.geo.subdivision.code` |
|---|---|---|
| US East vs US West | ✅ Distinguishes (JFK/LHR vs LAX/SJC) | ✅ Distinguishes (NY vs CA) |
| US West intra-region | ✅ Distinguishes LAX vs SJC vs SEA | ❌ Cannot (all are "CA") |
| Europe country | ✅ Distinguishes LHR vs CDG vs FRA | ✅ Distinguishes (GB vs FR vs DE) |
| Cold start | Sub-ms | Sub-ms |
| Free quota | 100K req/day (3M/mo) | 1M/mo as web requests (200 credits) |

For most use cases, country-level is enough. For US regional sharding (which FLEET.md explicitly calls out as the v1+ requirement), CF Workers is the right choice. We're already on CF at apex.

### 3.3 Health-check failover

Each pod's Worker exposes `/__health` returning `{"ok":true, "pod":"app-us-east-01", "ts":"..."}`. The apex Worker can periodically (via CF Cron Trigger — 5 free triggers per account, fires per-minute at most) fetch each pod's `/__health` and update the KV registry with `active: true/false`. Failed pods get `active: false` and are excluded from the routing pick. Recovery re-enables them.

The Cron Trigger pattern:
```javascript
// scheduled handler in apex Worker
export default {
  async fetch(req, env, ctx) { /* normal request handling */ },
  async scheduled(event, env, ctx) {
    const routes = await env.POD_REGISTRY.get("routes", "json");
    for (const route of routes) {
      for (const pod of route.pods) {
        try {
          const r = await fetch(`https://${pod.hostname}/__health`, { signal: AbortSignal.timeout(2000) });
          pod.active = r.ok;
        } catch { pod.active = false; }
      }
    }
    await env.POD_REGISTRY.put("routes", JSON.stringify(routes));
  }
};
```

Cron Triggers count toward the 1K writes/day KV limit on CF Workers Free. The Cron writes the WHOLE `routes` registry once per trigger (not per-pod), so the write count is `triggers_per_day`, not `pods × triggers_per_day`. At per-minute frequency, that's 1440 writes/day — **EXCEEDS the 1K/day Free limit**. At every-5-min frequency, 288 writes/day — fits. Recommend every-5-min for Free tier. For per-minute Cron, upgrade to Workers Paid ($5/mo, removes the 1K limit).

### 3.4 Edge cases

- **DNS-level geo-routing (Route 53 geolocation)**: NOT recommended. $0.50/zone/mo + $0.40/M queries. Breaks the free-tier goal.
- **Client-side JS routing**: NOT recommended. Adds round-trip, breaks SEO, breaks curl, breaks bots.
- **Sticky sessions via cookie**: Set a cookie on first visit (`pod=app-us-east-01`) and read it on subsequent visits. Sticky per-browser. Adds ~10 lines of Worker code. Useful when session state lives on a specific pod.
- **ASN-based routing**: `request.cf.asNum` gives the visitor's ASN (e.g., AS13335 = Cloudflare). Route by ASN to peer with specific ISPs or avoid problem ASNs. Same KV pattern, just a different match key.

---

## 4. A/B landing tests

### 4.1 Three patterns compared

| Pattern | Mechanism | Pros | Cons |
|---|---|---|---|
| **A1. Netlify Traffic Splits** | Built-in feature. Branch-based: route X% to production, Y% to a branch deploy. | Native to Netlify hosting, no code changes. Free. | Requires apex or sub-zone on Netlify (we don't have it). Public API doesn't expose config — bb-api only. **Exact shape unprobed.** |
| **A2. CF Worker deterministic hash** | `sha1(ip + ua + salt) % 100 < variant_b_percent`. Sticky across sessions if IP+UA stable. | Works at apex (CF). KV-configurable percentage (no redeploy). Tightly integrated with geo-routing. | Sticky depends on IP+UA stability — mobile networks churn IPs. |
| **A3. CF Worker cookie-based** | First visit: Worker sets `variant=A` or `variant=B` cookie (random by percentage). Subsequent visits: read cookie. | Truly sticky per-browser. More accurate A/B measurement. | Adds one Set-Cookie header. Cookie expiry (default: 1 year). |

### 4.2 Recommended pattern: A2 + A3 hybrid (TARGET — not yet implemented in v2.1.0)

> **STATUS**: The deployed apex Worker v2.1.0 uses `Math.random()` for pod selection — NO cookie, NO hash. A/B stickiness is NOT implemented. The pattern below is the TARGET for Phase 4 (§8). Port the code to the deployed Worker when running your first landing-page A/B test.

```javascript
// In the apex Worker, when picking a pod:
function pickPod(req, routes, env) {
  const cookie = req.headers.get("cookie") || "";
  const variantMatch = cookie.match(/variant=([AB])/);
  let variant;
  if (variantMatch) {
    variant = variantMatch[1];  // sticky from cookie
  } else {
    // New visitor — deterministic hash of IP + UA + salt
    const ip = req.headers.get("cf-connecting-ip") || "";
    const ua = req.headers.get("user-agent") || "";
    const hash = sha1(ip + ua + env.AB_SALT).slice(0, 8);
    variant = (parseInt(hash, 16) % 100 < env.VARIANT_B_PERCENT) ? "B" : "A";
  }
  const candidates = routes.filter(r => r.variant === variant && r.active);
  return weightedPick(candidates);
}

// Set the variant cookie on first visit (no-op if already set)
function maybeSetVariantCookie(response, variant) {
  if (!response.headers.has("set-cookie")) {
    response.headers.set("set-cookie", `variant=${variant}; Path=/; Max-Age=31536000; SameSite=Lax`);
  }
  return response;
}
```

`AB_SALT` and `VARIANT_B_PERCENT` are stored in KV (or as Worker secrets via `wrangler secret put`). Update percentage without redeploying by writing to KV.

### 4.3 A/B on landing pages (specific use case)

The user mentioned "a/b tests on landing pages." The landing page is typically served at `/` on the apex (the Worker HTML). To A/B test:
- Variant A: HTML at KV key `LANDING_A` (full HTML string, ≤ 25 MB).
- Variant B: HTML at KV key `LANDING_B`.
- Worker reads `variant` cookie, fetches the matching HTML from KV, returns it. ~1ms KV read.

For more sophisticated A/B (different SSR pages, different backend logic), use the pod-routing pattern: variant A routes to `app-us-east-01` (running code A), variant B routes to `app-us-east-02` (running code B). The pod's Worker handles the actual page render.

### 4.4 What I couldn't validate about Netlify Traffic Splits

The public REST API returns 404 for `/sites/{id}/traffic_splits`. The bb-api sample in the agent-kit (`GET_sites_01c2e47f-3ff6-4e09-b45f-604c49ef90fe_traffic_splits.json`) returns `[]` (empty list — the test site had none configured). The configuration syntax (request body for creating a split) is **not yet probed**. To learn it:
1. Get a fresh `_nf-auth` cookie from browser DevTools.
2. Create a Traffic Split via the Netlify dashboard on a test site.
3. Capture the request via DevTools Network tab.
4. Reverse-engineer the request body.
5. Reproduce via bb-api (`POST /sites/{id}/traffic_splits`).

Until that's done, **A2 + A3 (CF Worker pattern) is the recommended A/B approach** for sonicloud.app. It works at the apex (which is on CF), doesn't require Netlify hosting, and integrates with the existing pod routing.

---

## 5. Netlify WAF / Traffic Rules — newly discovered capability

### 5.1 What the prior research missed

The kit's docs (`KIT.md`, `ARCHITECTURE.md`, `FLEET.md`, `LESSONS.md`, `PIVOT.md`, `AGENT.md`) repeatedly claim "Netlify apex has no WAF" / "sub-zones are on Netlify DNS, so no WAF." This is **wrong**. The account capabilities sample (`GET_accounts_6a7e84d51cdeff620a5cf5a0.json`) clearly shows:

```json
"firewall_enabled": { "included": true },
"traffic_rules": { "included": true },
"max_traffic_rules": { "included": 2, "used": 0 },
"max_rules_per_set": { "included": 2, "used": 0 },
"max_ips_per_rule": { "included": 3, "used": 0 },
"max_countries_per_rule": { "included": 3, "used": 0 }
```

This is on the **Free** plan. Pro bumps to 5 rules / 10 rules per set / 50 IPs / 50 countries. Enterprise adds `global_access_controls: true` and other features.

Site objects also have `traffic_rules_config_per_scope` (empty in the sample I have, but the field exists). The "scope" likely refers to per-domain or per-branch rules.

### 5.2 What I couldn't access via the public REST API

I probed all obvious paths:
- `GET /sites/{id}/traffic_rules` → 404
- `GET /sites/{id}/firewall` → 404
- `GET /sites/{id}/firewall/rules` → 404
- `GET /sites/{id}/waf` → 404
- `GET /sites/{id}/traffic_rules_config` → 401 (interesting — exists but PAT can't read)
- `GET /accounts/{id}/firewall` → 404
- `GET /accounts/{id}/traffic_rules` → 404

The 401 on `traffic_rules_config` (vs 404 on the others) suggests the endpoint exists but the PAT lacks the permission scope. **The configuration is almost certainly bb-api-only** (cookie auth, like SSO disable).

### 5.3 What this means for the architecture

The tradeoff "Netlify apex has no WAF" was a key argument in FLEET.md's recommendation to keep apex on CF for WAF. That argument is now weaker — Netlify apex WOULD have a basic WAF (2 rules / 2 sets / 3 IPs / 3 countries on Free). But:

1. **CF Free WAF is still more generous**: 1 managed rule + 5 custom rules per zone. Netlify Free is 2 traffic rules total, 2 rules per set, with only 3 IPs and 3 countries per rule. CF wins on every dimension.
2. **The Netlify WAF is per-site, not per-zone**. The kit's pattern has the apex zone on CF (where CF WAF applies) and sub-zones on Netlify DNS (where Netlify WAF would apply only if a Netlify site is attached to the sub-zone). Since the kit's sub-zones currently have only the `192.0.2.1` placeholder A record (no Netlify site), the Netlify WAF doesn't actually protect anything yet.
3. **For per-pod isolation, per-pod CF accounts give per-pod WAF**. Each pod's CF zone has its own 5 custom rules. This is the pattern FLEET.md describes and is unaffected by the Netlify WAF discovery.

**Revised tradeoff**: Netlify Free has a basic WAF. CF Free has a more generous WAF. For the apex (where WAF matters most because traffic is highest), CF wins. For per-pod isolation, per-pod CF accounts win. The Netlify WAF is a nice-to-have for any sub-zone that's actually hosted on Netlify (e.g., a static marketing page on a sub-zone) — but it's not a primary WAF strategy.

### 5.4 What to do about it

1. **Update the kit's docs** to remove the "Netlify apex has no WAF" claim — replace with "Netlify Free has a limited WAF (2 rules, 3 IPs/countries per rule) via bb-api only."
2. **Probe the bb-api** for Traffic Rules CRUD endpoints when next `_nf-auth` cookie is available. Likely paths:
   - `GET/POST/PUT/DELETE /access-control/bb-api/api/v1/sites/{id}/traffic_rules`
   - `GET /access-control/bb-api/api/v1/sites/{id}/traffic_rules_config`
3. **If a sub-zone is hosted on Netlify** (e.g., static marketing page), configure its 2 traffic rules for basic rate-limiting (e.g., block IPs with > 100 req/min — if the rule engine supports it).

---

## 6. Credit budget allocation — the user's "finite grandfathered accounts" problem

### 6.1 The user's framing (from their message)

> "i do have quite a few grand-fathered netlify with better free tier usage, but those are finite resource and i have more projects / sites than these account can fit"

The user has more sites/projects than the grandfathered accounts can host. Allocation strategy is needed.

### 6.2 What "grandfathered" means concretely

- Pre-Sep-2025 Netlify accounts are on the **legacy Free tier** (separate quotas):
  - 100 GB bandwidth/month (vs ~15 GB max on credit-based Free)
  - 125K function invocations/site/month (vs ~30 GB-hours shared on credit-based Free)
  - 1M edge function invocations/month (vs ~1.5M max shared on credit-based Free — actually similar)
  - 300 build minutes/month (vs not metered on credit-based Free, but each prod deploy = 15 credits)
- Switching to the credit model is **irreversible** — these accounts must NOT be migrated.
- The legacy quotas are roughly **6.7× more bandwidth capacity** than credit-based Free.
- Detection: legacy accounts would have a different `type_slug` than `credit-free`. I couldn't verify the exact slug because I don't have access to a known grandfathered account. Likely candidates: `legacy-free`, `starter-free`, `classic-free`. Probe with `GET /accounts/{id}` and check `type_slug`.

### 6.3 Allocation strategy

| Workload type | Where to host it | Why |
|---|---|---|
| High-traffic production site (app, users) | **Grandfathered Netlify account** (legacy 100 GB BW) | Bandwidth is the binding constraint; legacy gives 6.7× more |
| Low-traffic marketing site (docs, blog) | **Vercel Hobby** (non-commercial only) OR credit-based Netlify Free | Either works; Vercel has better Next.js DX |
| DNS zones (apex + sub-zones) | **Credit-based Netlify Free** (DNS is free regardless of plan) | Doesn't consume credits; doesn't benefit from grandfathering |
| Edge routing compute (the apex Worker) | **CF Workers Free** (per CF account, 100K req/day) | Not Netlify at all — CF gives more capacity for routing |
| Per-pod backend compute | **Per-pod CF Workers Free** (own CF account per pod) | Not Netlify — each pod gets dedicated 100K req/day |
| Large object storage (scrape dumps, audit logs) | **Netlify Blobs** (any Netlify account) | Unmetered storage; works on both legacy and credit accounts |
| Build-time batch compute (scraping, data pipelines) | **Credit-based Netlify Free preview deploys** | 0 credits per preview deploy; 15 min × 2 vCPU × 4 GB free |
| Cron triggers | **GitHub Actions** (2000 min/mo free) | Trigger Netlify preview deploys + CF Worker Cron Triggers (5 free per CF account) |
| Email routing (apex) | **CF Email Routing** | Free, native to CF apex zone |
| Email routing (sub-zones) | **ImprovMX** (free, 25 aliases, 500 emails/day) OR AWS SES ($0.10/1K) | CF Email Routing is zone-level only |

### 6.4 The hierarchy of "where does this thing go"

When deciding where to host a new service, ask in this order:
1. Is it static content? → CF R2 (zero egress) or Netlify Blobs (unmetered)
2. Is it edge compute (routing, A/B, geo)? → CF Worker at apex or per-pod CF Worker
3. Is it SSR with auth? → Vercel Hobby (non-commercial) or upgrade Vercel to Pro ($20/mo) for commercial
4. Is it a backend API? → Per-pod CF Worker + per-pod D1/KV/R2
5. Is it a database? → Supabase (own account per pod) for Postgres + auth, or Neon for pure Postgres
6. Is it batch compute (scraping, pipelines)? → Netlify build-as-compute via preview deploys
7. Is it storage of results? → Netlify Blobs (free) or R2 (zero egress for serving)
8. Is it DNS? → Netlify DNS zones (free regardless of plan) or CF (apex)

### 6.5 Specific recommendation for sonicloud.app today

Current state: 6 Netlify sub-zones on credit-based Free, all with placeholder A records. 0 sites, 0 credits used. The user's grandfathered accounts are NOT yet in `infra/pods.csv` (it's empty). 

**Next concrete allocation step**:
1. User provides IDs + tokens for the grandfathered Netlify accounts. Each is added to `infra/pods.csv` as a row + `infra/pods/<pod_id>.json` with the token.
2. Decide which sub-zone gets the high-bandwidth grandfathered account:
   - `app.sonicloud.app` (the SPA) → grandfathered account (highest traffic)
   - `users.sonicloud.app` (user-facing app) → grandfathered account (second-highest traffic)
   - `api.sonicloud.app` → credit-based account (lower traffic, more compute-heavy — but actually a per-pod CF Worker is better than Netlify Functions for API, so this might not need Netlify at all)
   - `content.sonicloud.app` → credit-based account or R2 (media storage)
   - `corp.sonicloud.app` → credit-based account (internal, low traffic)
   - `cdn.sonicloud.app` → R2 bucket (zero egress for serving)
3. For each sub-zone that needs Netlify hosting, attach a Netlify site (created in the appropriate account) and update the sub-zone's A record from `192.0.2.1` to a CNAME → `<site>.netlify.app` or the Netlify-managed IP.

### 6.6 What "I have more projects / sites than these accounts can fit" implies

Each Netlify account can hold 500 sites. The "fit" problem isn't a per-account site count limit — it's:
- 1 Free team per Netlify user (hard API-enforced). Adding a 6th Free account requires a 6th distinct email + GitHub OAuth.
- Each account's bandwidth/compute is shared across all 500 sites. Hosting 100 active sites on one account means each site gets ~1/100th of the bandwidth budget.
- The grandfathered accounts are precious because their bandwidth is 6.7× the credit-based Free. Spreading 100 sites across 5 grandfathered accounts (20 sites each) gives each site ~5GB/month of bandwidth on legacy = ~33GB/month equivalent on credit-based. That's better than 100 sites on 1 credit-based account (~150 MB/month per site).

**Recommendation**: Don't put more than ~20-30 active sites on one grandfathered account. Spread the rest across credit-based accounts (1 credit-based account per ~5-10 sites, since 300 credits / 5 sites = 60 credits per site = ~3 GB bandwidth per site per month). Use `infra/pods.csv` to track which site is on which account.

---

## 7. The compute + page split — minimizing Netlify credit consumption

### 7.1 The user's framing

> "all these require some combination of compute + page, while netlify free tier is very limited on credits to support these"

The user is right that Netlify free tier is tight for compute-heavy routing workloads. The fix is **don't put routing compute on Netlify** — put it on CF Workers (free, 100K req/day per account, no credit pool). Netlify's free tier is good for: DNS, Blobs (storage), build-as-compute (preview deploys for batch jobs).

### 7.2 The canonical split pattern

```
REQUEST FLOW:
  User → sonicloud.app/app/dashboard
  ↓
  CF Edge (apex Worker: sonicloud-root-worker)
    - reads pod registry from KV (~1ms)
    - picks pod by request.cf.colo
    - 302 redirects to https://app-us-east-01.sonicloud.app/app/dashboard
  ↓
  CF Edge (pod Worker: app-us-east-01-worker, in CF Account B)
    - reads user session from KV (~1ms)
    - if not authed: 302 to /login
    - if authed: fetches page data from D1 (~5-15ms) + renders SSR HTML
    - returns HTML
  ↓
  User receives HTML

NETLIFY INVOLVEMENT: NONE
  - DNS for sonicloud.app → CF (apex zone on CF)
  - DNS for app.sonicloud.app → Netlify NS1 (free, no credits)
  - DNS for app-us-east-01.sonicloud.app → CF (in CF Account B)
  - No Netlify site is in the request path

NETLIFY FREE TIER USED ELSEWHERE (NOT in routing):
  - Netlify Blobs for storing scrape results, audit logs, large blobs (free, any Netlify account)
  - Netlify build-as-compute for nightly batch jobs (preview deploys = 0 credits)
  - Netlify DNS zones for per-subdomain isolation (free, no credits)
```

### 7.3 What goes where (the comprehensive map)

| Component | Where it lives | Cost | Why |
|---|---|---|---|
| Apex DNS zone | CF (current) | Free | Workers, Email Routing, KV, R2, D1 all native |
| Sub-zone DNS | Netlify NS1 | Free | First-class standalone subdomain zones on Free |
| Pod DNS (3rd level) | CF (per-pod account) | Free | Each pod gets own zone + Worker + WAF |
| Apex Worker (router) | CF Workers (apex account) | Free, 100K req/day | KV-native, no bandwidth meter |
| Pod Workers (compute) | CF Workers (per-pod account) | Free, 100K req/day each | Per-pod 100K budget, isolated |
| Edge storage (hot) | CF KV (per account) | Free, 1 GB, 100K reads/day | Pod registry, session state, config |
| Edge SQL | CF D1 (per account) | Free, 5 GB, 5M rows read/day | Per-pod relational data |
| Object storage (cold) | Netlify Blobs (any Netlify account) | Free, unmetered | Audit logs, large blobs, scrape dumps |
| Object storage (served) | CF R2 (per account) | Free, 10 GB, zero egress | Media, file downloads |
| SSR pages | Vercel Hobby (non-commercial) OR per-pod CF Worker | Free | Vercel for marketing, CF for app |
| Batch compute | Netlify build-as-compute (preview deploys) | Free, 0 credits | 15 min × 2 vCPU × 4 GB per run |
| Cron triggers | GitHub Actions OR CF Cron Triggers | Free | GH: 2000 min/mo; CF: 5 triggers/account |
| Email routing (apex) | CF Email Routing | Free | Native to CF apex |
| Email routing (sub-zones) | ImprovMX OR AWS SES | Free or $0.10/1K | CF Email Routing is apex-only |
| DNS round-robin (if needed) | NS1 returns multiple A records | Free | Verified: rotation order varies by resolver |

### 7.4 What this means for the user's "Netlify free tier is limited" concern

Netlify's free tier is limited for **edge routing compute** (300 credits shared, ~1.5M edge function invocations max if you spend ⅔ of pool on routing). It's NOT limited for:
- DNS zones (free regardless)
- Object storage via Blobs (unmetered)
- Build-time compute via preview deploys (0 credits per run, 15 min × 2 vCPU × 4 GB)

The user's framing assumes routing compute would go on Netlify. The fix is to **put routing compute on CF Workers**, which has its own dedicated 100K req/day budget per account and doesn't share a credit pool with anything. Then Netlify's free tier is used only for what it's good at: DNS, Blobs, build-as-compute. None of those consume credits.

### 7.5 Specific test I ran (Q5 in the probe)

I tested using Netlify Blobs as a pod registry (storing the JSON of pod routes) and reading it from outside via the public API. Results:
- Write via presigned S3 URL: works, 0 credits.
- Read via `GET /api/v1/blobs/{site_id}/site:{store}/{key}`: works, returns JSON, 0 credits.
- **Latency: ~860-900 ms per read** (from this container in HK).

Compare to CF KV: ~1 ms warm / ~5 ms cold (intra-CF). For a pod registry read on every request, **Blobs is too slow** — adds ~870ms to every request. Use CF KV for the hot pod registry; use Netlify Blobs for cold storage of large artifacts (full pod definitions, audit logs, anything > 25 MB which is KV's per-key cap).

This is a concrete data point supporting the recommendation: routing compute on CF (KV-backed), Netlify reserved for storage + DNS + batch.

---

## 8. Implementation plan (phased)

### Phase 0 (already done — verified live 2026-08-17)
- ✅ CF apex zone created and active
- ✅ 6 Netlify sub-zones created with NS delegation from CF apex
- ✅ Apex Worker deployed (placeholder HTML + /__health)
- ✅ Email Routing enabled on apex
- ✅ Vercel docs/blog READY and attached
- ✅ DMARC/MTA-STS/CAA on every zone

### Phase 1 (next 1-2 sessions)
- [ ] **Add KV namespace to apex Worker** — `POD_REGISTRY` in CF MAIN account. Mint the namespace via CF API. Update Worker bindings.
- [ ] **Rewrite apex Worker** to add routing logic per §1.4. KV-backed pod registry, geo-routing via `request.cf.colo`, A/B via cookie + hash.
- [ ] **Seed the pod registry** with a single fallback entry (`app.sonicloud.app` itself, until real pods exist).
- [ ] **Test end-to-end**: `curl -sk -i https://sonicloud.app/app/test` should 302 to `https://app.sonicloud.app/test`.
- [ ] **Update `scripts/30_provision_pod.py`** to implement the full pod provisioning flow (per §2.5).

### Phase 2 (when first pod is needed)
- [ ] Manually create a new CF account (web signup, ~2 min).
- [ ] Mint scoped tokens for the new account.
- [ ] Run `python3 scripts/30_provision_pod.py app-us-east-01` — should:
  - Create CF zone `app-us-east-01.sonicloud.app` in new account
  - Deploy a template Worker
  - Bind Worker via Worker Routes
  - Add 4 NS records in the Netlify `app.sonicloud.app` zone
  - Update apex KV registry with the new pod entry
- [ ] Verify: `dig +short NS app-us-east-01.sonicloud.app @1.1.1.1` → 4 CF nameservers
- [ ] Verify: `curl -sk -i https://app-us-east-01.sonicloud.app/__health` → 200 JSON
- [ ] Verify: `curl -sk -i https://sonicloud.app/app/test` → 302 to `https://app-us-east-01.sonicloud.app/test` (or whichever pod is picked)

### Phase 3 (multiple pods + health-check failover)
- [ ] Add Cron Trigger to apex Worker for periodic health checks.
- [ ] Add a second pod in a different region.
- [ ] Test geo-routing: from a US-West IP, expect `app-us-west-01`; from EU, expect `app-eu-west-01`.
- [ ] Test failover: take down `app-us-east-01/__health` and verify routing switches to fallback pod within 1 minute.

### Phase 4 (A/B testing)
- [ ] Add `variant` field to pod registry entries.
- [ ] Implement A2 + A3 hybrid pattern in apex Worker (cookie + hash).
- [ ] Add `AB_SALT` and `VARIANT_B_PERCENT` as Worker secrets (via `wrangler secret put`).
- [ ] Test: clear cookies, hit `sonicloud.app/` 100 times, count A vs B distribution. Should match `VARIANT_B_PERCENT` ±5%.

### Phase 5 (when Netlify Traffic Splits is probed)
- [ ] Get a fresh `_nf-auth` cookie from browser DevTools.
- [ ] Create a Traffic Split via dashboard on a test site, capture the request via DevTools Network.
- [ ] Reverse-engineer the bb-api request body.
- [ ] Document the API in `docs/cloud-architecture/03_NETLIFY_TRAFFIC_SPLITS.md`.
- [ ] Decide: use Netlify Traffic Splits for landing-page A/B (native, free) vs CF Worker pattern (more flexible, requires apex on CF — which we have).

### Phase 6 (grandfathered account integration)
- [ ] User provides IDs + tokens for grandfathered Netlify accounts.
- [ ] Add to `infra/pods.csv` + `infra/pods/<pod_id>.json` per pod.
- [ ] Allocate high-traffic sub-zones to grandfathered accounts per §6.3.
- [ ] Update `scripts/30_provision_pod.py` to support specifying which Netlify account hosts which site.

### Phase 7 (hyperscale preparation)
- [ ] When pod count > 10: migrate `infra/pods.csv` to D1 (per-pod database).
- [ ] When pod count > 30: consider Doppler for secrets management.
- [ ] When pod count > 50: consider CF Workers for Platforms ($25/mo + $0.30/M req) instead of per-pod CF accounts.
- [ ] When apex bandwidth > 100 GB: upgrade apex CF account to Workers Paid ($5/mo).

---

## 9. Open questions for the next session

1. **Netlify Traffic Splits API shape** — needs bb-api probe with `_nf-auth` cookie. Create a split via dashboard, capture the request, reverse-engineer the body. Document in a new `03_NETLIFY_TRAFFIC_SPLITS.md`.

2. **Netlify WAF / Traffic Rules CRUD via bb-api** — same probe approach. Likely paths: `POST/PUT/DELETE /access-control/bb-api/api/v1/sites/{id}/traffic_rules` and `GET /access-control/bb-api/api/v1/sites/{id}/traffic_rules_config`. Document in a new `04_NETLIFY_WAF.md`.

3. **Two-level NS delegation live test** — ❌ DONE 2026-08-17 (FAILED with CF error 1116 for same-account sub-zones). See §2.0 + `two-level-ns-test.log`. Cross-account case is now open question #9 below.

4. **Grandfathered account detection** — once the user provides a known grandfathered Netlify account ID + token, check `GET /accounts/{id}` for the `type_slug` value. Document the slug (likely `legacy-free` or similar) so future sessions can detect grandfathered accounts programmatically.

5. **CF Worker with KV binding — actual deploy + latency measurement** — ✅ DONE 2026-08-17. Measured: KV read adds ~20ms over no-KV baseline (44ms → 65ms); 302 hop adds ~40ms over pod direct (40ms → 85ms). See §1.4 + §2.3 + `e2e-validation.log`.

6. **Pod template Worker** — write a canonical "pod Worker" template that handles `/__health`, session, SSR, and pod-local D1 queries. Store in `templates/app-pod-worker.js`. Each pod deploy uses this template + per-pod config (KV keys).

7. **Per-pod CF account mint** — write a script that takes a fresh CF account ID + master token and mints the scoped tokens needed (zone, worker, KV, D1, R2 — same pattern as `03_mint_scoped_tokens.py` but for the pod's account).

8. **Health-check failover Cron Trigger** — implement the scheduled handler in the apex Worker, set up the Cron Trigger via CF API, test by taking down a pod and observing the routing switch. **KV write budget**: every-5-min Cron = 288 writes/day (fits in 1K/day Free limit); per-minute Cron = 1440 writes/day (EXCEEDS Free limit, would need Workers Paid $5/mo). Recommend every-5-min for Free tier.
9. **Cross-account subdomain zone creation on CF Free tier** — create a fresh CF account (manual web signup), attempt to register `app-test-01.sonicloud.app` as a zone in it. If CF asks for TXT verification (per https://developers.cloudflare.com/dns/zone-setups/subdomain-setup/cross-account/), cross-account works on Free → per-account isolation doesn't need a separate domain registration. If error 1116 fires, the "separately-registered domain" upgrade path (§2.1) is the only free-tier option. This is the load-bearing unverified claim of the architecture — high priority.
10. ✅ **DONE 2026-08-17** — Gate `/__routes` behind an admin token (P1 security fix from opus peer review WAVE-1). Worker v2.1.0 deployed via `15_gate_routes_endpoint.py`. `/__routes` returns 401 without `x-admin-token` header; `/__health`'s `pod_count` returns -1 without token. Admin token value = `scrape_api_key` from `secrets.json` (reused as shared secret). Verified live.

---

## 10. Summary — what the next session operator should know

1. **Read `01_GROUND_TRUTH.md` first** — it's the consolidated reference of everything verified as of 2026-08-17.
2. **Apex stays on CF** — FLEET.md's pivot to Netlify apex was right at the time, but CF Section 2.8 was removed in 2026 and CF is now strictly better at apex (KV/D1/R2, finer geo via colo, no bandwidth meter, more WAF rules, native Email Routing).
3. **Netlify free tier is for DNS + Blobs + build-as-compute, NOT routing** — routing goes on CF Workers (per-account 100K req/day, dedicated, no credit pool).
4. **Pod fleet uses Worker Routes on the apex zone** (NOT two-level NS delegation — that pattern is BLOCKED by CF error 1116 on Free tier; subdomain zones require Enterprise $5K+/mo). Per-pod Workers (e.g., `app-test-01-worker`) are deployed to CF MAIN account, each bound via Worker Routes to a hostname like `app-test-01.sonicloud.app` with A record `192.0.2.1` proxied=true (canonical CF pattern). This gives per-Worker isolation (own KV/D1/R2 bindings, own logs). Per-ACCOUNT isolation requires either (a) a separately-registered domain (e.g., `sonicloud-pods.com`) — ~$10/yr, OR (b) cross-account subdomain setup IF CF Free tier allows it (unverified — see §9 open question #9). Validated live 2026-08-17.
5. **Edge router = apex CF Worker with KV-backed pod registry** — currently v2.1.0 deployed with weighted-random pod selection + admin-token-gated debug endpoints. Geo-routing via `request.cf.colo` (§3), A/B stickiness via cookie + hash (§4.2), and health-check failover via Cron Trigger (§3.3) are designed but NOT yet implemented — they're Phase 3/4 items. See §1.4 for what's actually live.
6. **Netlify Traffic Splits exists but is unprobed** — the public API returns 404; the bb-api shape is empty in the sample. Needs a cookie-auth probe.
7. **Netlify WAF exists on Free** (2 rules, 3 IPs/countries per rule) — the prior docs missed this. CF Free WAF is more generous (1 managed + 5 custom per zone). For per-pod isolation, per-pod CF accounts give per-pod WAF.
8. **Grandfathered Netlify accounts are 6.7× more bandwidth-capable** — allocate to high-traffic sub-zones (app, users), NOT to DNS or low-traffic sites.
9. **The first concrete change** (adding a KV namespace to the apex Worker and rewriting it with routing logic) is **DONE** as of 2026-08-17 (see §1.4). The admin-token gate on `/__routes` is also **DONE** (v2.1.0, per opus peer review WAVE-1 P1-1). Next concrete change: Phase 3 (geo-routing + health-check Cron Trigger) when a second real pod is added.

The plan is grounded in live-verified facts. Next action: Phase 1 of §8.
