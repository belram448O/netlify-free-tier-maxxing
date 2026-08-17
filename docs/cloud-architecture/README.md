# Cloud Architecture — sonicloud.app deep dive

> **Session**: 2026-08-17
> **Carries on from**: `../netlify-ns-handoff.md`
> **Goal**: Pick up the broader cloud architecture angles — apex site handling, multi-pod re-routing for free-tier-account spreading, geo-routing, A/B landing tests, compute+page split given Netlify's limited credits, and credit budget allocation for the user's mix of grandfathered vs credit-based Netlify accounts.

## Read in order (AUTHORITATIVE — read 04 first)

1. **`04_FINAL_ARCHITECTURE.md`** ⭐ **THE AUTHORITATIVE REFERENCE** — the final cloud architecture consolidating everything. Read this FIRST. It supersedes contradictory claims in 02_CLOUD_ARCHITECTURE.md (which has stale v2.0.0/v2.1.0 references and wrong claims about CF error 1014 that were corrected by the alternate workstream). Covers: (a) the corrected architecture (Netlify apex + CF zone stays active + CNAME→workers.dev per-account pods); (b) the pod fleet pattern with per-account isolation; (c) credit budget allocation strategy for grandfathered vs credit-based Netlify accounts; (d) compute+page split (CF Worker routing, R2 static, Vercel SSR, Netlify Blobs cold storage, Netlify build-as-compute batch); (e) multi-region pod strategy; (f) A/B landing test strategy; (g) phased implementation status (Phase 0-1.5-3-4 ✅ DONE, Phase 2-5-6-7 🟡 PENDING user input).

2. **`03_CORRECTION_ALTERNATE_WORKSTREAM.md`** — transparent acknowledgment of what the alternate workstream proved and what I got wrong (3 specific claims). Read this to understand why 04 supersedes 02.

3. **`01_GROUND_TRUTH.md`** — the consolidated reference of everything I verified before writing the architecture doc. Still useful for the raw facts (Netlify free tier credit model, Blobs unmetered, build-as-compute, bb-api surface, etc.) but the architecture conclusions in it are superseded by 04.

4. **`02_CLOUD_ARCHITECTURE.md`** — the original cloud-architecture deep dive. **READ WITH CAUTION** — has stale v2.0.0/v2.1.0 references and 3 wrong claims that were corrected by the alternate workstream (see 03_CORRECTION). Kept for historical context; 04 is the authoritative version.

5. **`WAVE-1-PEER-REVIEW.md`**, **`WAVE-2-VERIFY-REVIEW.md`**, **`WAVE-3-VERIFY-REVIEW.md`** — three opus peer review waves that verified the design + sandbox state. Wave 1 found 3 P0 + 6 P1 issues (all fixed). Wave 2 verified fixes + found 3 new P1 (all fixed). Wave 3 verified v3.0.0 + found 2 P0 (all fixed). Read for the review history.

6. **Probe scripts** (numbered `01_validate_tokens.py` through `20_credit_state_audit.py`) — the live-test scripts used to validate every claim. Run them again to re-verify state.

7. **Probe logs** (`*.log`) — captured outputs from each probe run on 2026-08-17. Use as a baseline for diffing future state.

8. **`worklog.md`** — the multi-agent worklog protocol record for this session. Read to see the concrete steps taken.

## Key conclusions (TL;DR from `04_FINAL_ARCHITECTURE.md`)

1. **Apex DNS on Netlify** (NS1 ancast, free, supports ALIAS at apex) — the alternate workstream migrated NS from CF to Netlify and proved CF zone stays "active" even with NS on Netlify, so all zone-level features (Worker Routes, KV, Email Routing, WAF, Universal SSL) continue to work. **This corrects my earlier wrong claim that apex must stay on CF.**

2. **Per-account pod isolation via CNAME → workers.dev** — each pod Worker lives in its own CF account, accessible at `<pod-name>.<pod-acct-subdomain>.workers.dev`, CNAME'd from the Netlify apex zone (DNS-only, no proxy). This bypasses CF error 1014 (which only applies to PROXIED CNAMEs in CF zones). **This corrects my earlier wrong claim that per-account isolation requires a separately-registered domain.**

3. **The edge router is a CF Worker at the apex** (`sonicloud-root-worker` v3.0.1, live 2026-08-17) with KV-backed pod registry + geo-routing via `request.cf.country` + health-check Cron every 5 min + A/B stickiness via cookie + djb2 hash + admin-token-gated debug endpoints. All Phase 1-4 items DONE.

4. **Netlify free tier is best used for DNS + Blobs + build-as-compute, NOT for routing compute.** Live test of Netlify Blobs as a pod registry: ~860-900 ms per read (vs CF KV's ~20 ms warm) — too slow for hot path; use Blobs for cold storage only. CF Workers (100K req/day = 3M/month, free, dedicated per account) is strictly more cost-effective for routing.

5. **Netlify Free has a WAF** (the prior research missed this) — `firewall_enabled: true, traffic_rules: true, max_traffic_rules: 2, max_rules_per_set: 2, max_ips_per_rule: 3, max_countries_per_rule: 3` on Free. Public REST API returns 404 for rule CRUD; bb-api-only (needs `_nf-auth` cookie).

6. **Grandfathered Netlify accounts (pre-Sep-2025) are ~6.7× more bandwidth-capable** than credit-based Free (~100 GB vs ~15 GB max). Allocate to high-traffic sub-zones (app, users, api) — NOT to DNS (free regardless) or low-traffic static sites. Allocation strategy in `04_FINAL_ARCHITECTURE.md` §3.

7. **Compute + page split**: CF Worker for routing (KV pod registry), R2 for served static assets (zero egress), Vercel Hobby for non-commercial SSR (docs/blog), per-pod CF Workers for pod-local compute, Netlify Blobs for cold storage (unmetered), Netlify build-as-compute for batch jobs (preview deploys = 0 credits). Netlify is NOT in the request path for routing — only DNS + storage + batch.

## What to do next

Phase 1-4 are DONE. Next concrete steps (need user input):

1. **Phase 2 (per-account pod isolation live test)**: Mint a CF SUB scoped token via dashboard with Workers Scripts:Edit permission, save to `secrets.json` as `cf_sub_scoped_token`. Then I can deploy a pod Worker to CF SUB, CNAME from Netlify apex zone, and validate per-account isolation end-to-end.

2. **Phase 6 (grandfathered account integration)**: Provide IDs + tokens for grandfathered Netlify accounts. Add to `infra/pods.csv` + `infra/pods/<pod_id>.json` per pod. Then allocate high-traffic sub-zones to grandfathered accounts per the strategy in §3.4.

3. **Phase 5 (Netlify Traffic Splits probe)**: Provide a fresh `_nf-auth` cookie from browser DevTools. Then I can probe the bb-api shape of Traffic Splits + WAF/Traffic Rules CRUD.
