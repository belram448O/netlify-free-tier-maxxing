# Ground Truth — Netlify Free-Tier Maxxing + Custom Domain Infra Kit

> **Purpose**: One consolidated reference of everything I have read and verified as of 2026-08-17, before any new architecture work begins. Written by the main agent after a full re-read of: handoff, findings-report, agent-skill, dashboard-automation, methodology, KIT, FLEET, ARCHITECTURE, LESSONS, PIVOT, CODEBASE, AGENT, SECURITY, REPLICATE, 06_configure_root_zone, 30_provision_pod, 22_stamp_all_subdomains, 01_create_zones, e2e_test, Makefile, ci.yml, all 32 sample JSONs, plus live probing of sonicloud.app.
>
> This file is the gate. The architecture plan in `02_PLAN.md` is built directly on this. If anything here is wrong, the plan is wrong.

---

## Part A — What the Netlify Free Tier actually is (post-Sep-2025 credit model)

### A1. Pricing model — verified facts
- Single shared pool of **300 credits/month** on the Free tier (Personal = 1000, Pro = 3000–20000 by tier).
- 6 meters: production deploys, compute, AI inference, bandwidth (egress only), web requests, forms (free).
- Credit rates (post-Apr-2026):
  - Production deploy = **15 credits each** (failed/rollback free; preview/branch deploys 0 credits).
  - Compute = **10 credits / GB-hour** = `wall-clock hours × memory GB`. 1024 MB × 200ms = 0.0006 credits per invocation → ~540,000 invocations/month if compute is the only meter used.
  - Bandwidth (egress) = **20 credits / GB**. Includes static assets, function API responses, image CDN, file downloads, DB egress. **Ingress is NOT metered** (function downloads from internet = free).
  - Web requests = **2 credits / 10,000** requests (page views, function API calls, asset requests, Edge Function invocations).
  - AI inference = 180 credits / $1 USD (Agent Runners, AI Gateway).
  - Forms = free / unlimited.
- Effective free-tier ceiling if all 300 credits spent on one meter:
  - Bandwidth-only: ~15 GB/month
  - Web-requests-only: ~1.5M requests/month
  - Compute-only: ~30 GB-hours = ~108,000 function-seconds at 1GB
- Credit meter lag: API `capabilities.credits.used` lags the dashboard by 5–30+ minutes. **Dashboard is authoritative for real-time state**.
- Preview deploys `draft: true` are 0 credits AND appear to not meter their generated traffic either (bandwidth meter frozen after 33 MB of function traffic via preview URLs).
- One Free team per Netlify user — hard API-enforced (`POST /accounts` with Free `type_id` returns `422`). The multiplier is N distinct Netlify users (separate email + GitHub OAuth each), not N teams under one user.
- Accounts created before Sep 4, 2025 are grandfathered with legacy quotas (100 GB bandwidth, 125k function calls, 1M edge invocations, 300 build minutes — all separate). Switching to credit model is **irreversible**. The user mentioned having "quite a few grandfathered Netlify accounts with better free tier usage" — these are valuable precisely because they pre-date the credit model.
- Netlify Database (Neon-backed) is free **until July 1, 2026** — already past as of this writing. Becomes metered.

### A2. Netlify Blobs — genuinely unmetered storage
- Backend = AWS S3 in `us-east-2` (Ohio). Bucket hostname `cmh-services-prod-netliblob-935421240257.s3.us-east-2.amazonaws.com`.
- 5 GB max object size, no documented per-store or per-account total cap, no per-operation meter.
- Write pattern = presigned S3 URLs; read pattern = direct API GET.
- REST API surface (undocumented in OpenAPI):
  - `GET /api/v1/blobs/{site_id}` — list stores
  - `GET /api/v1/blobs/{site_id}/{store}` — list blobs in store (store name format: `site:{name}`, the `site:` prefix is mandatory in REST paths)
  - `GET /api/v1/blobs/{site_id}/{store}/{key}` — read blob content (PAT works)
  - `PUT /api/v1/blobs/{site_id}/{store}/{key}` — get presigned S3 URL, then upload to S3 (PAT works for this flow)
  - `HEAD` / `DELETE` require a Blobs token (NOT a user PAT) — only available inside Netlify runtime contexts (Functions, Edge Functions, Build plugins) via `NETLIFY_BLOBS_CONTEXT` env var.
- The `NETLIFY_BLOBS_CONTEXT` env var is **only available in**: build plugin `onPostBuild` hook, Netlify Functions, Netlify Edge Functions. It is **NOT available in the build command phase** — that phase writes to `/tmp/` and the plugin reads it.

### A3. Build process — free compute
- Per preview deploy: up to 15 min runtime, 2 vCPU, 4 GB RAM, 9 GB disk, Kata Container VM (Ubuntu Noble 24.04), Node 24, full Linux userland (`apt-get`, `bun`, `uv`), can install `@sparticuz/chromium` (190 MB in 2.5s), outbound HTTP works.
- Free plan: 1 concurrent build (additional triggers queue).
- API deploys (`POST /sites/{id}/deploys` with files but no `draft:true`) **do NOT trigger the build process** — they're direct CDN uploads completing in ~1s with no plugin run. To trigger a build plugin, must use `netlify deploy` CLI or Git push.
- Build hooks (`POST /build_hooks/{id}`) return HTTP 200 but **do NOT trigger builds** if the site has no Git repo connected.
- Scheduled functions: registered but did not reliably execute in the tested sandbox account (function-logs API is Pro-gated, couldn't diagnose).

### A4. Netlify Functions — Lambda-backed, regional
- Provider: AWS Lambda (`aws_lambda`), Node 24.x runtime, region `us-east-2` (Ohio) only on Free.
- Memory locked at 1024 MB on Free. Per-invocation compute cost ~0.0006 credits (200ms wall-clock × 1 GB × 10 cr/GB-hr).
- Timeouts: sync 30s (per Lambda default), scheduled 30s, background 15 min.
- Cold start ~1.3s first request, ~50–200ms warm.
- Native binaries: must use `node_bundler = "zisi"` (not esbuild). `curl-cffi-node` fails due to glibc 2.38 requirement (Lambda has 2.26 or 2.34). `tls-impersonate` (pure JS) works for Chrome JA3/JA4 impersonation.
- New Netlify accounts (post-Aug 2026) have "Private by default" — function URLs return 401 until `sso_login: false` is set via the bb-api (NOT the public PAT API — that returns null).

### A5. Netlify Edge Functions — Deno Deploy edge
- Run on Deno Deploy at the CDN edge (globally distributed, ~5–15 ms added latency).
- Account-level usage endpoint: `GET /accounts/{id}/edge_functions` returns `{used, included, additional, last_updated_at, period_*}` — included is `null` on Free (no hard cap separate from the credit pool).
- Billed as **web requests**: 2 credits / 10,000 invocations. So 1M edge function invocations = 200 credits (~⅔ of the Free pool).
- 1MB code cap, Deno-only, v8 isolates (sub-ms cold starts, unlike Lambda).
- `context.geo` available: `context.geo.country.code`, `context.geo.subdivision.code` (US state), `context.geo.city`, `context.geo.latitude/longitude`. This is **sub-country granularity** — distinguishes US-East from US-West.
- Plugin pattern: `netlify/edge-functions/router.ts` — Deno TypeScript.

### A6. Netlify Traffic Splits — built-in A/B feature
- API endpoint: `GET /sites/{id}/traffic_splits` returns a list. Empty in the sample because the test site had no splits configured.
- This is a real feature; the API shape is undocumented in the public OpenAPI spec but exists in the bb-api. Likely supports branch-based traffic splitting (production vs branch deploys).
- **Status: API shape known to exist, but exact configuration syntax not yet probed.** Need a live test to learn the request/response.

### A7. Netlify WAF / Traffic Rules — NEW FINDING the prior research missed
- Sample `GET_accounts_6a7e84d51cdeff620a5cf5a0.json` shows `capabilities` includes:
  - `firewall_enabled: {included: true}` ✅
  - `traffic_rules: {included: true}` ✅
  - `max_traffic_rules: {included: 2}` (Free), 5 (Pro)
  - `max_rules_per_set: {included: 2}` (Free), 10 (Pro)
  - `max_ips_per_rule: {included: 3}` (Free), 50 (Pro)
  - `max_countries_per_rule: {included: 3}` (Free), 50 (Pro)
- Site object also has `traffic_rules_config_per_scope` field (empty in sample).
- **This means Netlify Free does have a basic WAF** — limited to 2 traffic rules total, 2 rules per set, 3 IPs and 3 countries per rule. NOT zero. The prior docs' claim "Netlify apex has no WAF" is wrong.
- Compare Cloudflare Free: 1 managed rule + 5 custom rules per zone (more permissive).
- This changes the apex-on-Netlify vs apex-on-CF tradeoff calculation.

### A8. Internal bb-api (Backend-For-Frontend)
- Base URL: `https://app.netlify.com/access-control/bb-api/api/v1/...`
- Auth: session cookies (`_nf-auth` JWT alone is sufficient — no WAF, no `connect.sid`, no User-Agent/Origin/Referer required). PAT does NOT work.
- Use cases the PAT can't do:
  - Disable SSO on a site (`PUT /sites/{id}` with `sso_login: false`)
  - Real-time bandwidth (`GET /accounts/{id}/bandwidth`)
  - Observability query API (`POST /sites/{id}/observability/query/{counts,timeseries}`)
  - List blob stores on a site (`GET /sites/{id}/blobs`)
  - Audit logs (`GET /accounts/{id}/audit`)
- Cookie extraction: browser DevTools → Application → Cookies → `app.netlify.com` → copy `_nf-auth` value (starts with `nfu_`). Expires (hours to days, no API refresh path found).

### A9. Netlify DNS zones (NS1 reseller)
- Netlify is a reseller of NS1 Connect (IBM NS1). Each zone gets `dns1-4.p0X.nsone.net` (pool p01 through p09, opaque assignment).
- API: `POST /api/v1/dns_zones` with `{"name":"<zone>","account_id":"<acct-uuid>"}` returns `{id, name, dns_servers: [...]}` synchronously. Records via `POST /dns_zones/{id}/dns_records`.
- Records API returns bare array (not CF's `{result: [...]}` wrapper).
- DNS operations don't consume credits. Free, unmetered queries, NS1 anycast (5ms globally).
- **Standalone subdomain DNS zones** are first-class on Netlify Free (unlike CF which gates this to Enterprise at $5K+/mo).
- One Free team per Netlify user is the multiplier constraint — N distinct Netlify users = N × 300 credits, fully siloed, each with its own DNS zones, sites, audit logs.

### A10. Function URL access — Private-by-default gotcha
- New Netlify accounts have `account_sso_login: true, account_sso_login_context: "all"` hard-enforced.
- All URLs (including function URLs and static assets) return `HTTP 401` with redirect to `app.netlify.com/edge-access`.
- Public API `PATCH /api/v1/sites/{id}` returns null for SSO fields — cannot disable via PAT.
- Fix: bb-api `PUT /sites/{id}` with `{"password":"","password_context":"all","sso_login":false,"sso_login_context":"all"}` — works, requires only `_nf-auth` cookie.
- Account-level visibility can't be changed on Free (`422 "Account is not eligible to update global access controls"`); site-level fix is sufficient.

---

## Part B — What the custom-domain infra kit is

### B1. The kit's purpose (from KIT.md)
Generalized playbook for a multi-account custom domain with:
- Multi-account isolation (per-subdomain or per-region CF/Vercel/Netlify accounts).
- Free-tier scaling (linear cost — add pods at $0 each until limits hit, then upgrade only that pod).
- Email routing (CF on apex, ImprovMX on sub-zones).
- DMARC/MTA-STS/CAA on every zone.
- 2FA enforcement on CF (automatable via API), manual elsewhere.
- Drift detection + 25-test E2E suite + hourly GH Actions CI.

### B2. The 5 core decisions (from KIT.md)
1. **Apex DNS provider**: Netlify (TOS-safe, 100 GB free) OR Cloudflare (free WAF + Workers + KV + R2 + Email Routing). Both free; CF gives more features at apex, Netlify is TOS-safer.
2. **Per-subdomain DNS provider**: Netlify DNS zones (free, first-class standalone subdomain support via `POST /api/v1/dns_zones`). CF subdomain zones require Enterprise ($5K+/mo). Route 53 is $0.50/zone/mo + per-query.
3. **Apex hosting**: Static HTML on Netlify (100 GB BW, TOS-safe), or CF Worker (100K req/day, more features). Vercel Hobby is non-commercial only (TOS landmine).
4. **Per-subdomain hosting**: CF Worker per pod (TOS-safe for commercial, free to 100K req/day). Vercel only for non-commercial surfaces. AWS Lambda for backend APIs.
5. **Email**: CF Email Routing on apex (free, 200 dest, 3K outbound/month). ImprovMX on sub-zones (free, 25 aliases, 500 emails/day). Or AWS SES (paid).

### B3. Architecture pattern (from ARCHITECTURE.md — the kit's primary diagram)
```
Registrar (Spaceship)
  │ NS → Cloudflare
  ▼
CF apex account (Free tier)
  Zone: example.com (active)
  A      example.com  → 192.0.2.1 (proxied=true, Worker Route intercepts)
  CNAME  www          → apex
  CNAME  docs         → Vercel (DNS-only / grey cloud)
  CNAME  blog         → Vercel (DNS-only)
  CNAME  help         → vendor (DNS-only)
  NS     users        → Netlify NS1 (per-subdomain isolation)
  NS     app          → Netlify NS1
  NS     content      → Netlify NS1
  NS     corp         → Netlify NS1
  NS     api          → Netlify NS1
  NS     cdn          → Netlify NS1
  TXT    apex         SPF/DMARC
  CAA    apex         cert restrict
  Worker: example-root-worker (bound via Worker Routes)
  Email Routing: enabled
  2FA enforcement: enabled
        │
        │ NS del. to Netlify NS1
        ▼
Netlify DNS zones (Free, NS1-backed)
  Per-zone: A + CNAME www + SPF + DMARC + MTA-STS + 2× CAA (7 records)
  Each zone = separate billing/access envelope
```

### B4. FLEET.md's future-state target architecture (DIFFERENT from B3)
The fleet pattern flips the apex from CF to Netlify:
```
Registrar (Spaceship) → NS → Netlify NS1 (apex zone on Netlify)
Apex zone on Netlify (Free, TOS-safe, 100 GB BW, native _redirects with Country= rules)
  A   example.com → Netlify site IP
  CNAME docs/blog → Vercel
  NS  users/app/content/corp/api/cdn → NS1 sub-zones (each in own Netlify account)

Apex site (Netlify):
  - Static HTML landing
  - _redirects with Country= rules (zero-latency geo routing at the edge)
  - OR Netlify Edge Function reading context.geo.subdivision for sub-country routing
  - OR (Phase 3+) CF Worker at router.example.com for paid advanced routing
```
Pod fleet (target):
```
app-us-east-01.example.com  (DNS A record inside app.example.com Netlify sub-zone)
  Hosting: CF Worker in own CF account #1 (free, 100K req/day, dedicated)
  DB: Supabase project in own Supabase account, us-east-1 region
app-us-west-01.example.com
  Hosting: CF Worker in own CF account #2
  DB: Supabase in us-west-1
app-eu-west-01.example.com
  Hosting: CF Worker in own CF account #3
  DB: Neon in aws-eu-west-1
api-us-east-01.example.com
  Hosting: CF Worker in own CF account
  KV + R2 for state (in same CF account)
```
"Redirect magic" — 4 options compared (FLEET.md recommends B):
- **Option A**: Netlify `_redirects` with `Country=` rules. Zero latency, free, **country-level only — CANNOT distinguish US-East from US-West.** 2,500 rules/site cap.
- **Option B (RECOMMENDED)**: Netlify Edge Function reading `context.geo` (subdivision.code gives US state). 1M invocations/month free, ~5-15 ms added. Programmatic, can do health-check failover, dynamic pod registry.
- **Option C**: CF Worker at apex reading `request.cf.colo` (3-letter IATA code — distinguishes LAX from SJC). Requires apex on CF, not Netlify. 100K req/day free.
- **Option D**: Route 53 geolocation routing. $0.50/zone/mo + $0.40/M queries. Breaks free-tier goal.

### B5. The five next-session topics from netlify-ns-handoff.md
1. Validate edge-routing-vs-isolation tradeoff — confirm CF Workers custom-domain-on-Worker still requires A record on the CF zone (can't terminate a Worker route on a delegated Netlify sub-zone).
2. Build pod lifecycle automation — extend `scripts/` pattern: create Netlify DNS zone per pod, stamp records, NS delegation in parent, provision backend accounts, register pod in edge router's KV/D1 store.
3. Pod token security — no WAF on bb-api means pod auth tokens must be robust on their own (signed JWT with short TTL, not shared secrets).
4. Email routing for pods — CF Email Routing is zone-level, won't work on delegated sub-zones. ImprovMX or SES required.
5. Wear-down matching — balance resource profiles so different pods stress different meters (storage-heavy paired with compute-heavy, etc.).

---

## Part C — Current LIVE state of sonicloud.app (probed 2026-08-17)

### C1. Apex zone
- Domain: `sonicloud.app` (registrar Spaceship, observed NS `launch1.spaceship.net` + `launch2.spaceship.net` per CF `original_name_servers` field — but actual NS in public DNS is CF: `giancarlo.ns.cloudflare.com` + `hazel.ns.cloudflare.com`).
- CF zone ID: `b09e8c12f3cf7058d42e03d0c6b0d077` (account `14f6f36e01410c2360f3de636b18a7b0` = CF MAIN).
- Status: `active`. NS migrated from Spaceship defaults to CF.
- **Apex is on Cloudflare, NOT on Netlify.** This matches the kit's ARCHITECTURE.md (CF apex), NOT FLEET.md's target (Netlify apex). The pivot to Netlify apex has been documented but NOT executed.
- CF MAIN token `cfat_…` (the user-supplied master token) returns `403 Unauthorized to access requested resource` on `GET /accounts/{id}` — but the user-scoped `root_zone_token` (cfat_) and the zone-scoped `cf_main_zone_scoped_token` (cfut_) both work. The cf_main_token can only mint scoped tokens, not read account metadata directly. **Operational gotcha (per opus peer review WAVE-1 P2-5)**: `cf_main_token` is mint-only — it has `Account API Tokens:Edit` permission but NOT `Account Settings:Read` or `Zone:Read`. All CF operations in this session use `root_zone_token` (a scoped token minted by the kit's `03_mint_scoped_tokens.py`). If you inherit only `cf_main_token`, you must mint a scoped token first via `08_mint_scoped_token.py` (which uses `cf_main_token` to call `POST /user/tokens` — but wait, that requires user-level auth which `cfat_` tokens don't have; you'd need to mint via the CF dashboard, not the API). For per-pod CF account bootstrap, the user must provide a fresh scoped token minted via dashboard (cf_sub_token is the same — mint-only, can't create zones).

### C2. Apex DNS records (45 records in zone)
- `A sonicloud.app 192.0.2.1` (proxied=true → returns CF anycast 104.21.17.247 + 172.67.178.229)
- `CNAME www.sonicloud.app → sonicloud.app` (proxied=true)
- `CNAME docs.sonicloud.app → cname.vercel-dns.com` (DNS-only/grey)
- `CNAME blog.sonicloud.app → cname.vercel-dns.com` (DNS-only/grey)
- `CNAME help.sonicloud.app → helpdesk.example.com` (DNS-only, placeholder)
- 4 × `NS {sub}.sonicloud.app → dns{1-4}.p0X.nsone.net` for each of users/app/content/corp/api/cdn (24 NS records total)
- `MX` × 3 → route1/2/3.mx.cloudflare.net (CF Email Routing)
- `TXT sonicloud.app v=spf1 include:_spf.mx.cloudflare.net ~all`
- `TXT _dmarc.sonicloud.app v=DMARC1; p=quarantine; rua=mailto:admin@sonicloud.app; ruf=...`
- `TXT _dmarc.{users,app,content,corp,api,cdn}.sonicloud.app` × 6 (parent-zone DMARC for visibility)
- `TXT _mta-sts.sonicloud.app v=STSv1; id=20260815T000000`
- `TXT cf2024-1._domainkey.sonicloud.app v=DKIM1; ...` (CF Email Routing DKIM)
- `CAA` × 12 (default CF Universal SSL CAA records — includes cloudflare/letsencrypt/comodoca/digicert/pki.goog/ssl.com issue + issuewild, plus iodef mailto:admin@sonicloud.app)

### C3. Apex Worker
- Name: `sonicloud-root-worker`. Modified 2026-08-14T23:04:32Z. Compatibility date 2024-09-23, flag `nodejs_compat`. **Bindings: empty array**. No KV/D1/R2 bindings currently.
- Worker Routes: `sonicloud.app/*` + `www.sonicloud.app/*` → `sonicloud-root-worker`.
- Worker content (retrieved via HTTP, since the `/content` endpoint returns 405 with Bearer auth): placeholder HTML landing page. `/__health` returns `{"ok":true,"service":"sonicloud.app root","worker":"sonicloud-root-worker","ts":"...","region":"HKG"}` — uses `request.cf?.colo` for region. HTTP 200, served by CF (server: cloudflare, cf-ray).

### C4. Email Routing
- Status: `ready` on `sonicloud.app` apex. MX = CF route1/2/3. SPF + DKIM present.
- Catch-all rule likely configured (per the kit's standard setup). Destination `admin@sonicloud.app` — verification status unverified via API (needs bb-api or dashboard).

### C5. Netlify DNS zones (6 sub-zones)
All on account `6a7f8f3637d951add835956d` (zulfikarbarbora-y5u4uww's team, Free credit-based plan, 300 plan_credits, 0 used at last probe, period 2026-08-14 → 2026-09-14). Account is **post-Sep-2025 credit-based Free** (NOT grandfathered). `enforce_mfa: not_enforced`, `sso_login: true` on the account (default for new accounts), `sso_login_context: "all"`.
| Sub-zone | Netlify Zone ID | NS1 Pool | Records |
|---|---|---|---|
| `cdn.sonicloud.app` | `6a80b7c0ee37ee2d4890c59e` | p08 | A 192.0.2.1, CNAME www→cdn, TXT spf, TXT _dmarc, TXT _mta-sts, CAA letsencrypt, CAA cloudflare (7) |
| `api.sonicloud.app` | `6a80b7ab139f4780ad78bf2a` | p09 | 7 records (same template) |
| `corp.sonicloud.app` | `6a80b796139f47814f78bf0a` | p05 | 7 records |
| `content.sonicloud.app` | `6a80b780a694d25f2145f208` | p07 | 7 records |
| `app.sonicloud.app` | `6a80b76a8879772642e346b6` | p01 | 7 records |
| `users.sonicloud.app` | `6a80588c88797781fbe34721` | p07 | 7 records |
- **Every sub-zone's apex A record is the `192.0.2.1` placeholder.** None of the sub-zones actually serve real traffic yet — `https://app.sonicloud.app/`, `https://users.sonicloud.app/`, `https://api.sonicloud.app/`, `https://cdn.sonicloud.app/`, `https://content.sonicloud.app/`, `https://corp.sonicloud.app/` all return `HTTP None` (TLS or routing failure — 192.0.2.1 is RFC 5737 documentation IP, not routable).
- Each sub-zone has 4 NS records in the CF root zone (DNS-only/grey, not proxied — NS records can't be proxied at CF).

### C6. Vercel projects
- Team ID: `team_d4A0omMsba9zqkVl50WPNFrK` (3 projects: `template`, `sonicloud-blog`, `sonicloud-docs`).
- `sonicloud-blog` + `sonicloud-docs` are READY (production). HTTP 200 with `server: Vercel` on `https://docs.sonicloud.app/` and `https://blog.sonicloud.app/`.
- Hobby tier (non-commercial TOS — docs/blog are pure marketing so OK).

### C7. Credit budget state (Netlify)
- 0 sites in this Netlify account (`GET /sites` returns empty list — no production deploys ever triggered). 
- 6 DNS zones + 0 sites + 0 functions + 0 edge functions = 0 credits used (preview deploy experiment was on a different Netlify account: `6a7e84d51cdeff620a5cf5a0` slug `belram448o` — that one has the scraper site `01c2e47f-3ff6-4e09-b45f-604c49ef90fe` "transcendent-cheesecake-03f934" which is what `scrape_api_url` points to).
- Bandwidth used: 0 bytes in current period.
- The current sonicloud.app Netlify account is **completely unused for hosting** — only DNS zones. Zero credit consumption.

### C8. Spaceship registrar (sonicloud.app registrar)
- API at `https://spaceship.dev/api` is **IP-blocked from this container** — `HTTP 403 error code: 1010` (Cloudflare browser_signature_banned). This is a known issue documented in the kit (`LESSONS.md` A-series + `SECURITY.md`).
- Workaround: use a different IP (e.g., a VPS or CI runner) when Spaceship API calls are needed. Or use the Spaceship dashboard manually for transfer lock / NS changes.
- Currently NS is at CF (not Spaceship defaults), so this isn't blocking anything operationally right now.

### C9. The scraper deployment
- Site ID: `01c2e47f-3ff6-4e09-b45f-604c49ef90fe` (transcendent-cheesecake-03f934.netlify.app)
- On Netlify account `6a7e84d51cdeff620a5cf5a0` (slug `belram448o`) — DIFFERENT from the account holding the 6 sonicloud.app sub-zones (`6a7f8f3637d951add835956d`).
- Public scrape API: `https://6a803881151f1ede038cd0cc--transcendent-cheesecake-03f934.netlify.app/api/scrape`
- API key: a Netlify PAT (`nfp_…`, stored in `secrets.json` as `scrape_api_key`, used as shared bearer secret — not the user account PAT).
- Blobs API: `https://api.netlify.com/api/v1/blobs/01c2e47f-3ff6-4e09-b45f-604c49ef90fe/site:scraper-results`
- Has 1 build hook: `https://api.netlify.com/build_hooks/6a7fce3a8879774054e3468a` (POST to trigger build).
- Env var: `SCRAPE_API_KEY` set (contexts=all).
- This is a separate project from the architecture work — user explicitly said "ignore the scraper which is a byproduct and now we spinned it off". Reference for "what's possible with Netlify free tier" only.

---

## Part D — The GAP between current live state and FLEET.md's target

### D1. Apex location: CF (current) vs Netlify (FLEET target)
| Aspect | Current (CF apex) | FLEET target (Netlify apex) | Gap |
|---|---|---|---|
| Apex DNS provider | CF zone `b09e8c12f3cf7058d42e03d0c6b0d077` | Netlify DNS zone `sonicloud.app` (would need creation) | Need to create apex Netlify zone + migrate NS at registrar (Spaceship API is currently IP-blocked) |
| Apex hosting | CF Worker `sonicloud-root-worker` with Worker Routes | Netlify site (static + `_redirects` + Edge Function) | Need to deploy Netlify site, port Worker HTML to Netlify static, port `/__health` to Edge Function |
| Apex WAF | CF Free: 1 managed rule + 5 custom rules per zone | **Netlify Free: 2 traffic rules, 2 rules/set, 3 IPs/countries per rule** (NEW FINDING) | CF gives more WAF; Netlify gives TOS safety + native `_redirects`. Tension — not a clear win. |
| Email Routing | CF Email Routing on apex (already enabled, ready) | CF Email Routing **does not work on Netlify apex** — would need to move email to ImprovMX (free, 25 aliases, 500/day) or AWS SES | Migration cost: lose CF's 200 dest / 3K outbound/day. ImprovMX is smaller. |
| Edge compute at apex | CF Worker (100K req/day free, KV + R2 + D1 native) | Netlify Edge Function (1M invoc/mo free as web requests; can read `context.geo.subdivision.code`) | CF gives KV/R2/D1 at apex; Netlify gives finer geo granularity + higher invocation cap |
| Sub-country routing | CF Worker reads `request.cf.colo` (3-letter IATA, distinguishes LAX from SJC) | Netlify Edge Function reads `context.geo.subdivision.code` (US state — distinguishes CA from NY but NOT LAX from SJC within CA) | CF is strictly more granular for US West intra-region sharding |
| TOS safety for commercial | CF Section 2.8 removed in 2026 (no longer a concern) | Netlify AUP allows commercial use, no redirect restriction | Tied in 2026 — was a Netlify advantage before the 2026 TOS update |
| Free bandwidth at apex | CF doesn't meter Worker bandwidth | Netlify: 100 GB free (then metered at 20 cr/GB) | CF wins on bandwidth |

**My read**: FLEET.md's pivot to Netlify apex was the right call when CF Section 2.8 was a risk. As of 2026 (Section 2.8 removed), the case for moving apex off CF is much weaker. CF at apex gives strictly better features (KV/R2/D1, finer geo via colo, no bandwidth meter, more WAF rules, native Email Routing). The TOS-safety argument is now moot.

**Recommendation**: Keep apex on CF (current state). Re-evaluate only if CF re-introduces a TOS restriction or if the per-pod CF accounts (not the apex) become a problem.

### D2. Sub-zones: live but idle
All 6 sub-zones (users/app/content/corp/api/cdn) are created on Netlify, NS delegated from CF apex, but all 6 have A record = `192.0.2.1` placeholder. None serve real traffic. To make them functional:
1. Pick hosting target per sub-zone (CF Worker in own account / Vercel / Netlify Functions / SaaS CNAME / R2 bucket).
2. Replace A record with the real target (CNAME → vendor, A → CF Worker proxied IP, or CNAME → another Netlify site).
3. Wait for DNS propagation (minutes — NS1 anycast is fast).
4. Test with `dig +short A app.sonicloud.app @1.1.1.1` and `curl -sk https://app.sonicloud.app/`.

### D3. Pod fleet: not started
- No pods exist yet. `infra/pods.csv` is empty (only headers).
- `scripts/30_provision_pod.py` is a stub — exits with code 2 ("not implemented").
- The target pattern (`app-us-east-01.example.com` with own CF account + own Supabase + own Neon) requires:
  - Manual account creation at each provider (no API for signup).
  - Per-pod secrets file at `infra/pods/<pod_id>.json` (or Doppler at >5 pods).
  - Pod registry storage (KV / D1 / Netlify Blobs).
  - Edge router logic that reads the pod registry and redirects.

### D4. Edge router: no implementation yet
- FLEET.md recommends Option B (Netlify Edge Function) but the apex is currently on CF, so Option C (CF Worker at apex reading `request.cf.colo`) is the natural fit for the current state.
- The apex Worker `sonicloud-root-worker` currently has NO bindings (no KV/D1/R2). Adding a pod registry means adding a KV namespace binding and rewriting the Worker to:
  1. Read pod registry from KV on each request.
  2. Pick a pod based on `request.cf.colo` (or fallback to round-robin / hash of IP / weighted random for A/B).
  3. 302 redirect to the chosen pod (or reverse-proxy if path-based routing is needed).
- KV writes are 1K/day free on Workers free — enough for pod registry updates but not for per-request state.

---

## Part E — The cloud architecture angles the user explicitly asked about

### E1. Apex site handling
The apex (`sonicloud.app`) is currently a placeholder CF Worker returning HTML. The user said "we don't intend to serve [the apex site] but need to find way to handle well, as the root site is still quite critical." The apex is critical because:
- It's the brand entry point — anyone typing `sonicloud.app` lands here.
- It's the natural 302 target for naked-domain visits.
- It's where edge routing decisions happen (geo-routing, A/B, pod selection).
- It's where Email Routing is configured (zone-level, must be apex).
- It's the DMARC/MTA-STS/CAA anchor for the whole domain tree.

**Options for apex handling** (now that apex is on CF, not Netlify):
1. **Pure redirect**: `sonicloud.app/*` → 302 to `app.sonicloud.app/*`. Lowest compute, no pod registry needed at apex. Loses geo-routing (the redirect is uniform).
2. **Geo-aware 302 router**: CF Worker at apex reads `request.cf.colo`, picks a pod, 302 redirects. ~5-15ms added. Pod registry in KV (read-only at request time, updated via separate admin API).
3. **Path-based reverse proxy**: CF Worker at apex proxies `/api/*` → api pod, `/app/*` → app pod, `/docs/*` → docs Vercel. No 302 visible to user, but more compute per request and TLS/headers more complex.
4. **A/B landing**: CF Worker at apex uses `request.cf.colo` + a hash of the visitor's IP/UA to deterministically assign to variant A or B (sticky across sessions if IP hash is stable). Variant URLs are different pods or different static sites.
5. **Hybrid**: apex Worker serves a minimal HTML landing (brand, login CTA) AND 302s `/app/*` to the geo-routed pod.

### E2. Multi-pod re-routing (for free-tier-account spreading)
Goal: spread traffic across multiple free-tier accounts to multiply effective limits.
- CF Workers Free = 100K req/day per account. 10 pods × 100K = 1M req/day capacity.
- Each pod is a CF Worker in its own CF account, with its own 100K/day budget, its own audit log, its own WAF rules, its own tokens.
- Pod DNS lives inside the parent service's Netlify sub-zone (e.g., `app-us-east-01.sonicloud.app` is an A record in the `app.sonicloud.app` Netlify zone). Pod A record points at the pod's CF Worker (via `192.0.2.1` proxied through that pod's own CF zone, OR via CNAME to `<worker>.<acct>.workers.dev`).
- **Open question**: Can a CF Worker in account B serve traffic for a hostname whose DNS A record lives on a Netlify sub-zone? **Yes** — the A record just needs to point at the CF Worker's edge IPs, OR the worker needs to be bound to the hostname via Worker Custom Domain on a CF zone in account B (which would require the hostname to be a CF zone in account B — but you can't make `app-us-east-01.sonicloud.app` a CF zone without NS-delegating it from Netlify to CF). **The cleaner pattern**: NS-delegate `app-us-east-01.sonicloud.app` from the Netlify `app.sonicloud.app` zone to a new CF zone in account B. This gives the pod's CF account its own zone + Worker + WAF. Tradeoff: more NS delegations (DNS latency increases by one lookup).

### E3. Geo-routing (regional pods)
- FLEET.md's Option B (Netlify Edge Function with `context.geo.subdivision.code`) gives US-state granularity. Requires apex on Netlify.
- FLEET.md's Option C (CF Worker with `request.cf.colo`) gives IATA code (e.g., LAX, SJC, JFK, LHR, HKG). Strictly finer. Works with current apex-on-CF state.
- `request.cf` also exposes: `country`, `region`, `city`, `postalCode`, `latitude`, `longitude`, `timezone`, `asNum` (ASN), `asn` (AS name). All available with no extra configuration on CF Workers free.
- A pod registry is a mapping: `{country: {region: pod_hostname}}` or `{colo: pod_hostname}` or `{asn: pod_hostname}`. Store as JSON in KV (1 read per request, ~5ms cold / sub-ms warm).

### E4. A/B landing tests
- Option A: Netlify Traffic Splits (built-in feature, API shape exists but unprobed in this session). Branch-based: route X% to production, Y% to a branch deploy. No code changes needed, native to Netlify hosting. Requires apex or sub-zone on Netlify (currently neither).
- Option B: CF Worker at apex with deterministic hash (e.g., `sha1(ip + ua + salt) % 100 < variant_b_percent`). Sticky across sessions if IP+UA stable. Can be tuned per-route. KV-backed config lets you change split percentage without redeploying Worker.
- Option C: Path-based (/v1/ → variant A, /v2/ → variant B). Manual user selection. Not great for true A/B but useful for canary testing.
- Option D: Cookie-based. Worker sets a cookie on first visit (`variant=A` or `variant=B`), reads on subsequent visits. Sticky per-browser. Best for accurate A/B measurement.

### E5. Compute + Page split
The user's framing: routing decisions require "compute" (the Worker/Edge Function logic) while content serving is a "page" (static HTML/SSR). Netlify free tier is "very limited on credits to support these" because every compute invocation eats into the 300-credit pool.

**The split pattern that minimizes Netlify credit consumption**:
1. **Routing compute lives on CF Workers** (apex, on the CF MAIN account). 100K req/day free, no bandwidth meter, no credit pool shared with anything else. KV/D1/R2 available for pod registry.
2. **Static pages live on Netlify Blobs** (unmetered storage, free API reads). The page content is fetched by the routing Worker via `fetch("https://api.netlify.com/api/v1/blobs/<site_id>/site:<store>/<key>")` — but this counts as bandwidth egress on the Netlify side (20 cr/GB). To avoid this, the page can be served via a Netlify site's static URL (also bandwidth-metered) or via Cloudflare R2 (zero egress).
3. **SSR pages live on Vercel** (Hobby tier, non-commercial, 100 GB BW + 1M invocations + 4 CPU-hrs free). When SSR is needed for a sub-zone, attach the domain to a Vercel project via CNAME.
4. **Per-pod Workers** (for app/api/corp pods that need compute) live on per-pod CF accounts. Each pod gets 100K req/day free, dedicated.

**Specific to Netlify Edge Functions vs CF Workers for the routing compute**:
- Netlify Edge Function: 1M invocations/month free as web requests (2 credits per 10K = 200 credits for full 1M). Sub-country geo via `context.geo.subdivision.code`. Adds 5-15ms. Deno-only.
- CF Worker: 100K req/day free (= 3M/month) at no credit cost. IATA-code geo via `request.cf.colo`. Sub-ms cold start. JS/TS native. **Strictly more capacity per unit cost** when apex is on CF.

**The "compute+page split" pattern that the user is hinting at**:
- Page (HTML/JSON) is a static artifact — store in Netlify Blobs (free) OR Cloudflare R2 (free egress) OR Netlify static deploy (bandwidth-metered).
- Compute (the routing decision + the SSR if needed) is the Worker.
- For SSR: Vercel is the natural fit (Next.js adapter, Image CDN, edge functions via Vercel Edge).
- For edge routing: CF Worker at apex is the natural fit.
- For per-pod compute: per-pod CF Worker is the natural fit.
- **Netlify Functions and Edge Functions are NOT the natural fit for routing** given the credit-pool constraint — they're better used for one-off tasks (scraping, queue processing) where the build-as-compute pattern makes them free.

### E6. Credit budget allocation (the "finite resource" problem)
The user said: "i do have quite a few grand-fathered netlify with better free tier usage, but those are finite resource and i have more projects / sites than these account can fit". So the planning unit is: **how many grandfathered Netlify accounts do you have, and how should their legacy quotas be allocated across projects?**

- Grandfathered account = pre-Sep-2025, legacy quotas (100 GB BW + 125K function calls + 1M edge invocations + 300 build minutes, all separate). Switching to credit model is irreversible.
- These accounts are roughly **6.7× more bandwidth capacity** than a post-Sep-2025 credit account (~15 GB max if all 300 credits spent on bandwidth).
- Each grandfathered account is best used for: high-bandwidth sites, frequent function invocations, edge-function-heavy routing. NOT best for: build-as-compute scraping (preview deploys are 0 credits on both legacy and new — no advantage to legacy).
- **Allocation strategy**: grandfathered accounts → production traffic for high-traffic sub-zones (app, users, api). Credit-based accounts → DNS zones (free regardless) + low-traffic static sites + the scraper/experimental workloads. Don't "waste" grandfathered quotas on DNS or low-traffic sites.

---

## Part F — Reconciling the user's framing with ground truth

The user's mental model (from their message):
- "netlify is currently planned as the NS running the sub-domains (which CF wouldn't allow in free tier)" — **correct, verified**. CF subdomain zones require Enterprise ($5K+/mo), Netlify offers them free.
- "also hold the apex domain site (which we don't intend to serve but need to find way to handle well, as the root site is still quite critical)" — **partially correct**. The handoff doc says FLEET.md planned to move apex to Netlify. But the LIVE state has apex on CF (the pivot was documented but not executed). The "root site" question is open — see E1 above.
- "we want to do the re-rounting (for different regions, or pods i.e. free-tier-based accounts to spread the usage, dynamically, to split the traffic, this is an advanced scenario for high traffic situation, or for doing a/b tests on landing pages etc.)" — **the angles in E2, E3, E4 above**.
- "all these require some combination of compute + page, while netlify free tier is very limited on credits to support these" — **partially correct, but nuanced**. Netlify free tier credit pool is small (300/mo, ~15 GB BW max), BUT:
  - Netlify DNS zones are free regardless (don't consume credits).
  - Netlify Blobs are unmetered (free storage + free API reads).
  - Netlify build-as-compute (preview deploys) is 0 credits.
  - Netlify Edge Functions are billed as web requests (2 cr/10K), not as a separate compute meter — for a routing use case at 1M req/mo that's 200 credits (~⅔ of the pool), which IS tight.
  - **The user is right that Netlify free tier is tight for routing compute** — but the answer is to put routing on CF Workers (100K req/day free, no credit pool, dedicated), not on Netlify Edge Functions.
- "i do have quite a few grand-fathered netlify with better free tier usage, but those are finite resource and i have more projects / sites than these account can fit" — **see E6**. Allocation strategy needed.
- "so look into this as deep as you can, research / test / validate" — **the live tests in next step will validate the specific claims** (Netlify WAF capability, Blobs-as-pod-registry, Edge Function behavior, CF Worker with KV for pod registry).

---

## Part G — Open questions / things I need to validate live before writing the deep-dive doc

1. **Netlify WAF / Traffic Rules API** — the capabilities say `firewall_enabled: true`, `traffic_rules: true`, `max_traffic_rules: 2`. What's the actual API surface? Can I configure rules via PAT or bb-api? What can the rules do (block by IP, block by country, rate-limit, custom matcher)? This affects the apex-on-Netlify vs apex-on-CF tradeoff materially.

2. **Netlify Edge Functions** — confirmed available on Free (1M invoc/mo as web requests). What's the deploy mechanism? Where does the code live in the site structure (`netlify/edge-functions/`)? Can I deploy one and see it run, on the existing scraper account (without consuming meaningful credits)?

3. **Netlify Traffic Splits** — API exists (`GET /sites/{id}/traffic_splits`), shape empty in sample. Can I create one via bb-api? What does it actually do — branch-based A/B? Percentage-based? This affects whether A/B landing is "free with Netlify" or needs a CF Worker implementation.

4. **CF Worker with KV binding** — the apex Worker currently has no bindings. Can I add a KV namespace and rewrite the Worker to read a pod registry on each request? How fast is KV read in practice (docs say eventually-consistent, ~1ms warm)?

5. **Blobs as pod registry** — Blobs are free, but the user PAT can read them. Can a CF Worker (running on CF MAIN) call `api.netlify.com/api/v1/blobs/...` on every request to read a pod registry blob? What's the latency? Compare to KV (which lives inside CF and is faster).

6. **Netlify DNS as edge router** — can a Netlify DNS zone be configured with multiple A records for the same name (round-robin)? Can it do weighted routing natively? (Probably no — Netlify DNS is plain NS1, no weighted DNS.) So pod-level load balancing must happen at the Worker level, not DNS level.

7. **Per-pod NS delegation to a CF zone** — can I NS-delegate `app-us-east-01.sonicloud.app` (from the Netlify `app.sonicloud.app` sub-zone) to a CF zone in account B? This would give the pod its own CF zone + Worker + WAF. Need to verify the chain: registrar → CF apex → Netlify sub-zone → CF pod-zone. Two levels of NS delegation.

8. **Grandfathered account detection** — can I detect via API whether a Netlify account is on legacy (pre-Sep-2025) quotas vs the credit model? The `type_slug` field showed `credit-free` for the current account. Legacy accounts would presumably have a different slug. The user's "quite a few grandfathered accounts" — what are their IDs? Are they already in `infra/pods.csv` or `secrets.json`? (Answer: not yet — the user hasn't provided them. They would need to be added to `pods.csv` + `infra/pods/<pod_id>.json` per pod.)

---

## Part H — What I will do next (corrected plan)

1. **Live-test the 7 open questions in Part G** — write probe scripts that hit the live sonicloud.app + Netlify APIs to validate each claim. Use the existing scraper Netlify account (`6a7e84d51cdeff620a5cf5a0`, site `01c2e47f-3ff6-4e09-b45f-604c49ef90fe`) for safe Edge Function / Blobs / Traffic Splits experiments that don't touch the production sonicloud.app infra.

2. **Write `02_CLOUD_ARCHITECTURE.md`** — the deep-dive document covering:
   - The current vs target architecture (CF apex vs Netlify apex) with my recommendation (stay on CF apex).
   - The pod fleet pattern with two-level NS delegation (CF apex → Netlify sub-zone → CF pod-zone).
   - The edge router design (CF Worker with KV pod registry at apex, with sub-country geo via `request.cf.colo`).
   - The compute+page split (CF Worker for routing, R2 for static pages, Vercel for SSR, per-pod CF Workers for pod-local compute).
   - The A/B testing pattern (CF Worker with deterministic hash + KV-configurable split percentage).
   - The credit budget allocation strategy (grandfathered accounts for high-traffic, credit accounts for low-traffic / DNS-only).
   - The Netlify WAF / Traffic Rules capability (newly discovered — 2 rules, 2 sets, 3 IPs/countries per rule on Free).
   - The build-as-compute + Blobs-as-storage pattern (carried forward from the scraper work) for non-routing workloads.

3. **Update `docs/netlify-ns-handoff.md`** — add a section pointing to the cloud-architecture deep-dive, plus the new findings (Netlify WAF exists on Free, the apex-on-CF decision rationale, the two-level NS delegation pattern).

4. **Backup + push to both remotes** — `/home/sync/nftm-backup.tar.gz` + `git push origin main` (GitHub) + `git push gitlab main` (GitLab mirror).

The plan is now grounded. Next action: write the probe script for the 7 open questions.
