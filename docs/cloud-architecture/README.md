# Cloud Architecture — sonicloud.app deep dive

> **Session**: 2026-08-17
> **Carries on from**: `../netlify-ns-handoff.md`
> **Goal**: Pick up the broader cloud architecture angles — apex site handling, multi-pod re-routing for free-tier-account spreading, geo-routing, A/B landing tests, compute+page split given Netlify's limited credits, and credit budget allocation for the user's mix of grandfathered vs credit-based Netlify accounts.

## Read in order

1. **`01_GROUND_TRUTH.md`** — the consolidated reference of everything I verified before writing anything new. Read this first. It documents: (a) the Netlify Free Tier post-Sep-2025 credit model with exact meter rates; (b) Netlify Blobs as genuinely unmetered storage (S3-backed); (c) Netlify build process as free compute (15 min × 2 vCPU × 4 GB); (d) Netlify Functions and Edge Functions limits; (e) the bb-api (cookie-auth) surface; (f) Netlify DNS zones as NS1 reseller; (g) what the custom-domain infra kit does; (h) the current LIVE state of sonicloud.app (CF apex + 6 Netlify sub-zones with placeholder A records + Vercel docs/blog); (i) the GAP between current state and FLEET.md's target; (j) the 8 open questions I needed to validate live.

2. **`02_CLOUD_ARCHITECTURE.md`** — the cloud-architecture deep dive. Read this after the ground truth. It documents: (a) why apex stays on CF (not Netlify as FLEET.md planned); (b) the pod fleet pattern using **Worker Routes on the apex zone** (NOT two-level NS delegation — that pattern is BLOCKED by CF error 1116 on Free tier, see §2.0); (c) the edge router design (CF Worker v2.1.0 at apex with KV-backed pod registry + admin-token-gated debug endpoints, deployed live 2026-08-17); (d) A/B testing patterns (CF Worker cookie + hash hybrid — designed but not yet implemented, Phase 4); (e) the compute+page split (CF Worker for routing, R2 for static, Vercel for SSR, per-pod CF Workers for pod-local compute, Netlify Blobs for cold storage); (f) credit budget allocation strategy (grandfathered accounts for high-traffic, credit-based accounts for low-traffic/DNS-only); (g) the newly-discovered Netlify WAF capability (2 traffic rules on Free, bb-api-only config); (h) a phased implementation plan (Phase 1 ✅ DONE, Phases 2-7 pending) and a list of 10 open questions for the next session.

3. **Probe scripts** (numbered `01_validate_tokens.py` through `15_gate_routes_endpoint.py`) — the live-test scripts used to validate the open questions and deploy the architecture. Scripts 07-15 are the post-ground-truth probes that discovered CF error 1116 + 1014, deployed the corrected pod pattern, deployed the apex Worker with KV binding, and gated the debug endpoints behind an admin token. Run them again to re-verify state.

4. **Probe logs** (`*.log`) — captured outputs from each probe run on 2026-08-17. Use as a baseline for diffing future state.

5. **`worklog.md`** — the multi-agent worklog protocol record for this session. Read to see the concrete steps taken and where the prior session was interrupted.

## Key conclusions (TL;DR from `02_CLOUD_ARCHITECTURE.md`)

1. **Apex stays on Cloudflare** — FLEET.md's planned pivot to Netlify apex was the right call when CF Section 2.8 ("non-HTML ban") was a risk; that ban was REMOVED in the 2026 TOS, so the case for moving apex is now materially weaker. CF at apex gives KV/D1/R2 native, no bandwidth meter, 100K req/day Worker free, IATA-code geo (`request.cf.colo`) which is strictly finer than Netlify's `context.geo.subdivision.code`, more WAF rules per zone, and native Email Routing. The TOS-safety argument is moot in 2026.

2. **Netlify Free has a WAF after all** (the prior research missed this) — `firewall_enabled: true, traffic_rules: true, max_traffic_rules: 2, max_rules_per_set: 2, max_ips_per_rule: 3, max_countries_per_rule: 3` on Free. CF Free WAF is still more generous (1 managed + 5 custom per zone).

3. **The pod fleet uses Worker Routes on the apex zone** (NOT two-level NS delegation — that pattern is BLOCKED by CF error 1116 on Free tier, see `02_CLOUD_ARCHITECTURE.md` §2.0). Per-pod Workers deployed to CF MAIN, each bound via Worker Routes to a hostname like `app-test-01.sonicloud.app` with A record `192.0.2.1` proxied=true (canonical CF pattern). Per-ACCOUNT isolation requires either (a) a separately-registered domain (e.g., sonicloud-pods.com), OR (b) cross-account subdomain setup IF CF Free tier allows it (unverified — see open question #9). Validated live 2026-08-17.

4. **The edge router is a CF Worker at the apex** (`sonicloud-root-worker` v2.1.0, deployed live 2026-08-17) with KV-backed pod registry + admin-token-gated debug endpoints. KV namespace `POD_REGISTRY` (id `f5c32d0fdd9f4b18b3c508969224f239`). Routes `/app/*` → 302 to chosen pod via weighted random. Debug endpoints `/__routes` and `/__health` (verbose fields) require `x-admin-token` header. **Caveats**: NO automatic failover (Phase 3), NO geo-routing (Phase 3), NO A/B stickiness (Phase 4).

5. **Netlify free tier is best used for DNS + Blobs + build-as-compute, NOT for routing compute.** Live test of Netlify Blobs as a pod registry: ~860-900 ms per read (vs CF KV's ~1 ms warm) — too slow for hot path; use Blobs for cold storage only. CF Workers (100K req/day = 3M/month, free, dedicated per account) is strictly more cost-effective for routing.

6. **Netlify Traffic Splits API exists** but the public REST API returns 404; the bb-api sample returns `[]` (empty). Exact configuration syntax requires a bb-api probe with `_nf-auth` cookie access — needs a follow-up session with browser access.

7. **Grandfathered Netlify accounts are ~6.7× more bandwidth-capable** than credit-based Free. Allocate to high-traffic sub-zones (app, users, api) — NOT to DNS (free regardless) or low-traffic static sites. Allocation strategy in `02_CLOUD_ARCHITECTURE.md` §6.

## What to do next

Phase 1 is DONE (apex Worker v2.1.0 with KV + admin-token gate is live). Next concrete steps:
1. **Phase 2**: Provision a real second pod (manual CF account creation + `30_provision_pod.py` once it's implemented). Test geo-routing + failover.
2. **Phase 3**: Implement geo-routing via `request.cf.colo` in `pickPod` (~5 line change). Implement health-check Cron Trigger (every 5 min, 288 writes/day, fits in 1K/day Free KV budget).
3. **Phase 4**: Implement A2+A3 hybrid A/B stickiness (cookie + hash). Full code in `02_CLOUD_ARCHITECTURE.md` §4.2.
4. **Open Q #9**: Test cross-account subdomain zone creation on CF Free tier (high priority — verifies the per-account isolation upgrade path).
