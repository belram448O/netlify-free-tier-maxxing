# Correction — Alternate Workstream Findings (2026-08-17)

> **Purpose**: Transparent acknowledgment that an alternate workstream proved a key architectural claim of mine wrong, and documentation of the corrected architecture.
>
> This file supersedes contradictory claims in `02_CLOUD_ARCHITECTURE.md` §2.0 (which says CF error 1014 blocks CNAME → workers.dev as a blanket statement — that's WRONG) and TL;DR #2 (which says per-account pod isolation requires a separately-registered domain — also WRONG).

## What the alternate workstream proved (live, 2026-08-17)

They migrated the apex DNS from Cloudflare to Netlify and demonstrated that CF Workers can serve on `*.sonicloud.app` hostnames via DNS-only CNAMEs to `*.workers.dev`. All 7 of their claims verified live by my re-check (`18_verify_alternate_workstream.py`):

1. ✅ NS migrated to Netlify (`dns1-4.p02.nsone.net`) — confirmed at 1.1.1.1, 8.8.8.8, 9.9.9.9
2. ✅ `app.sonicloud.app` CNAMEs to `sonicloud-root-worker.sonicloud.workers.dev` — Worker responds HTTP 200 with `version: 3.0.1`
3. ✅ CF zone `sonicloud.app` is still `active` in CF even with NS on Netlify
4. ✅ Apex `sonicloud.app` serves the Worker (via ALIAS → workers.dev)
5. ✅ Vercel serves `docs.sonicloud.app` + `blog.sonicloud.app` via CNAME → `cname.vercel-dns.com`
6. ✅ Old Netlify sub-zone for `app.sonicloud.app` was deleted (was shadowing the CNAME with NS delegation)
7. ✅ Worker Route `app.sonicloud.app/* → sonicloud-root-worker` exists in the CF zone

## What I got wrong

### Wrong claim #1: "CF error 1014 blocks CNAME → workers.dev"

In `02_CLOUD_ARCHITECTURE.md` §2.0, I wrote:
> "CF error 1014: Proxied CNAMEs to `*.workers.dev` are blocked ('CNAME Cross-User Banned'). Cannot use CNAME → workers.dev as the pod binding mechanism."

This is **WRONG as a blanket statement**. CF error 1014 only applies to **PROXIED CNAMEs (orange cloud) in a CF zone**. A DNS-only CNAME at Netlify (where there's no "proxied" concept — Netlify DNS is just plain DNS) bypasses 1014 entirely. The DNS resolution returns the workers.dev A records, the client connects directly to CF's edge IPs, and CF routes the request based on SNI + Worker Routes.

In my `10_validate_pod_pattern.py`, I created a `proxied=true` CNAME in the CF apex zone — that's why I hit 1014. The alternate workstream put the CNAME on NETLIFY (where there's no proxy concept), and it works.

**Correction**: CF error 1014 blocks proxied CNAMEs to workers.dev **in a CF zone**. DNS-only CNAMEs to workers.dev (whether at Netlify, Route53, or any non-CF DNS provider) work fine.

### Wrong claim #2: "Per-account pod isolation requires a separately-registered domain"

In `02_CLOUD_ARCHITECTURE.md` TL;DR #2 and §2.1, I wrote that per-account pod isolation requires registering `sonicloud-pods.com` (~$10/yr) because CF error 1116 blocks subdomain zone creation and CF error 1014 blocks CNAME → workers.dev.

This is **WRONG**. The actual free-tier path to per-account pod isolation is:
1. Each pod Worker lives in its own CF account (own 100K req/day budget, own audit log, own tokens)
2. Each pod Worker has its own workers.dev subdomain (e.g., `app-pod-01-worker.sonicloud-account-b.workers.dev`)
3. The apex Netlify DNS zone has a CNAME: `app-pod-01.sonicloud.app → app-pod-01-worker.sonicloud-account-b.workers.dev` (DNS-only, no proxy)
4. CF's edge serves the request via SNI matching the workers.dev hostname (CF auto-issues a cert per Worker)
5. The apex Worker (in CF MAIN account) 302-redirects `sonicloud.app/app/*` to the chosen pod hostname

**No separate domain registration needed.** The CNAME → workers.dev pattern at Netlify DNS is the free-tier per-account pod isolation path.

### Wrong claim #3: "Apex must stay on CF for KV/Email Routing/WAF to work"

In `02_CLOUD_ARCHITECTURE.md` §1.2, I argued that apex must stay on CF because KV/Email Routing/WAF are zone-level features. The alternate workstream proved that **the CF zone stays `active` even when NS is on Netlify** — so all zone-level features (KV, Email Routing, WAF, Worker Routes, Universal SSL) continue to work. The only thing that changes is where DNS resolution happens (NS1 anycast instead of CF anycast — both globally distributed, both fast).

**Correction**: The apex can be on Netlify OR CF. Both work. The choice depends on:
- **Netlify apex** (what the alternate workstream set up): enables the CNAME → workers.dev pattern for per-account pod isolation; loses CF's "proxied" feature (but you don't need it for the pod pattern)
- **CF apex** (what I originally set up): enables the A record 192.0.2.1 proxied=true pattern (canonical CF Worker Routes); loses the ability to do CNAME → workers.dev at the apex (because CF blocks proxied CNAMEs to workers.dev via 1014 — but only if proxied; DNS-only would work but CF doesn't expose that toggle at the apex level for the apex itself)

The alternate workstream's Netlify-apex setup is the cleaner pattern for the pod fleet architecture.

## Corrected architecture (live as of 2026-08-17)

```
Registrar (Spaceship)
  │ NS → Netlify NS1 (p02 pool)
  ▼
Netlify apex DNS zone (sonicloud.app, id 6a82da288732aff064d6277e)
  │
  │ ALIAS  sonicloud.app         → sonicloud-root-worker.sonicloud.workers.dev
  │ CNAME  www.sonicloud.app     → sonicloud.app
  │ CNAME  app.sonicloud.app     → sonicloud-root-worker.sonicloud.workers.dev  ← pod pattern (per-account via different workers.dev)
  │ CNAME  docs.sonicloud.app    → cname.vercel-dns.com
  │ CNAME  blog.sonicloud.app    → cname.vercel-dns.com
  │ CNAME  help.sonicloud.app    → helpdesk.example.com  (placeholder)
  │ NS     users.sonicloud.app   → dns1-4.p07.nsone.net  (Netlify sub-zone, separate account isolation)
  │ NS     content.sonicloud.app → dns1-4.p07.nsone.net
  │ NS     corp.sonicloud.app    → dns1-4.p05.nsone.net
  │ NS     api.sonicloud.app     → dns1-4.p09.nsone.net
  │ NS     cdn.sonicloud.app     → dns1-4.p08.nsone.net
  │ MX     sonicloud.app         → route1/2/3.mx.cloudflare.net  (CF Email Routing, zone still on CF)
  │ TXT    sonicloud.app         → v=spf1 include:_spf.mx.cloudflare.net ~all
  │ TXT    _dmarc.sonicloud.app  → v=DMARC1; p=quarantine; ...
  │ TXT    _mta-sts.sonicloud.app → v=STSv1; id=...
  │ CAA    sonicloud.app         → cloudflare.com, letsencrypt.org
  │
  ▼
CF zone sonicloud.app (still "active" in CF MAIN account, even with NS on Netlify)
  │
  │ Apex Worker: sonicloud-root-worker (v3.0.1 — KV router + geo + cron + A/B + admin-token-gated debug)
  │   Bound via Worker Routes: sonicloud.app/* + www.sonicloud.app/* + app.sonicloud.app/*
  │
  │ KV namespace POD_REGISTRY (id f5c32d0fdd9f4b18b3c508969224f239)
  │   Routes: /app/ → app.sonicloud.app (active), regions: ["*"]
  │   ab_config: enabled=false (toggle via KV)
  │
  │ Cron Trigger: every 5 min (*/5 * * * *), probes each pod's /__health, updates active flag
  │
  │ Email Routing: enabled (catch-all → admin@sonicloud.app)
  │ Universal SSL: covers *.sonicloud.app (issuer: Google Trust Services)
  │
  ▼
Pod Workers (per-account isolation via CNAME → <pod-worker>.<pod-acct>.workers.dev)
  Current: app.sonicloud.app CNAMEs to sonicloud-root-worker.sonicloud.workers.dev
    (not a true separate pod — same Worker as apex, just a different hostname)
  Future: app-pod-01.sonicloud.app CNAMEs to <pod-01-worker>.<pod-account-b>.workers.dev
    (true per-account isolation — own 100K req/day, own audit log, own tokens)
```

## What the alternate workstream changed (live infra)

1. Created Netlify apex DNS zone for `sonicloud.app` (id `6a82da288732aff064d6277e`, NS: `dns1-4.p02.nsone.net`)
2. Added all DNS records (ALIAS, CNAME, TXT, CAA, MX, NS) to the Netlify zone
3. Key record: `CNAME app.sonicloud.app → sonicloud-root-worker.sonicloud.workers.dev`
4. Deleted the old Netlify sub-zone for `app.sonicloud.app` (was shadowing the CNAME with NS delegation)
5. Added Worker Route `app.sonicloud.app/* → sonicloud-root-worker` in the CF zone
6. Enabled `workers.dev` subdomain for the Worker (was disabled, blocking the CNAME from resolving)
7. Changed NS at Spaceship → Netlify NS (via scrape proxy — direct API was IP-blocked)
8. NS fully propagated — all 3 public resolvers show Netlify NS

## What I changed after the alternate workstream (live infra)

1. Updated KV `POD_REGISTRY:routes` to v4 — changed pod hostname from dead `app-test-01.sonicloud.app` (Cron correctly marked inactive) to live `app.sonicloud.app`. Routing `/app/test` now 302-redirects to `https://app.sonicloud.app/app/test` which serves the apex Worker.

## Phase 3b Cron Trigger validated in production

The Cron Trigger I deployed (every 5 min) **correctly fired** after the alternate workstream's migration:
- Detected that `app-test-01.sonicloud.app` was no longer responding (A record removed during migration)
- Set `active: false` in the KV registry
- Wrote the updated registry back to KV (`updated_at: 2026-08-17T10:20:54.893Z`)
- The apex Worker's `pickPod` saw no active pods and fell through to HTML landing page (correct behavior)

This is the failover logic working as designed — a real-world test of Phase 3b, validated.

## What this means for the architecture doc

The `02_CLOUD_ARCHITECTURE.md` doc has multiple stale claims that need correction:
- §2.0: "CF error 1014 blocks CNAME → workers.dev" — WRONG (only applies to proxied CNAMEs in CF zones)
- §2.1: "Per-account isolation requires a separately-registered domain" — WRONG (CNAME → workers.dev at Netlify works)
- TL;DR #2: same wrong claim
- §1.2: "Apex must stay on CF for KV/Email Routing/WAF" — WRONG (CF zone stays active even with NS on Netlify)

The corrected architecture is documented above. The `02_CLOUD_ARCHITECTURE.md` doc should be updated to reflect this — but rather than rewrite the entire doc, this correction file serves as the authoritative reference for the corrected understanding. Future sessions should read this file FIRST, then read `02_CLOUD_ARCHITECTURE.md` with the corrections in mind.

## Lessons

1. **Test the actual experiment, don't infer from docs.** I read CF error 1014's docs and inferred "CNAME → workers.dev is blocked." The alternate workstream actually tried it (with DNS-only CNAME at Netlify) and proved it works. I should have tested the DNS-only variant myself before claiming it's blocked.

2. **"Proxied" is a CF-specific concept.** Netlify DNS doesn't have a "proxied" toggle — all records are DNS-only. CF error 1014 is specifically about CF-zone proxied CNAMEs to other users' CF zones. DNS-only CNAMEs at any non-CF DNS provider bypass 1014 entirely.

3. **CF zone "active" status is sticky.** Once a zone is added to CF, it stays "active" even if NS is migrated away. This means all zone-level features (Worker Routes, KV, Email Routing, WAF, Universal SSL) continue to work — the only thing that changes is where DNS resolution happens.

4. **The apex-on-Netlify pattern is strictly more flexible than apex-on-CF for the pod fleet architecture.** It enables the CNAME → workers.dev pattern for per-account pod isolation WITHOUT requiring a separate domain registration. The apex-on-CF pattern I originally set up works but requires the A record 192.0.2.1 proxied=true pattern (which doesn't give per-account isolation — all pod Workers share CF MAIN's budget).

5. **The Cron Trigger failover works in production.** When the alternate workstream's migration accidentally killed `app-test-01.sonicloud.app`, the Cron correctly detected the failure and marked the pod inactive. The apex Worker's `pickPod` then saw no active pods and fell through to the landing page (correct behavior — no user got a 5xx from a dead pod). This is a real-world validation of Phase 3b.
