# WAVE-3 Verification Review — sonicloud.app Cloud Architecture (Opus-tier)

> **Reviewer**: opus-peer-reviewer-wave3 (sub-agent)
> **Date**: 2026-08-17 (post Phase 3+4 deploy)
> **Scope**: Verify Worker v3.0.0 deployment (geo-routing, Cron Trigger, A/B stickiness) and Open Q #9 resolution. Surface new issues and recommend APPROVE / APPROVE-WITH-CONDITIONS / REJECT.
> **Materials reviewed**: worklog (Task 1-4 + WAVE-1 + WAVE-2), WAVE-2-VERIFY-REVIEW.md (full), 02_CLOUD_ARCHITECTURE.md (targeted Grep + Read of TL;DR, §1.2, §1.4, §2.0, §3.1, §3.3, §4.2, §8, §9, §10), README.md, netlify-ns-handoff.md, `16_deploy_worker_v3.py` (full Worker source + Cron config), `deploy-worker-v3.log` (full), live re-verification of all v3.0.0 endpoints + Cron schedule via CF API.

---

## Overall Verdict: **APPROVE-WITH-CONDITIONS**

Worker v3.0.0 is **functionally deployed correctly** — geo-routing, Cron Trigger, A/B stickiness, and Open Q #9 resolution are all live and working as designed. The deployed Worker source matches the architecture spec. All 6 expected endpoint behaviors verified live. Cron Trigger is registered (`*/5 * * * *`, created 2026-08-17T09:08:29Z). Phase 3+4 logic is sound: `pickPod` falls back to all-active pods (no 404 risk on region miss), the Cron writes only on state change (correctly stays within the 1K/day Free KV write limit at 288 writes/day max), and the A/B hash is deterministic (10/10 same-UA requests got the same variant in deploy log Test 7).

**However**, the v3.0.0 deployment introduced **two blocking issues** that must be addressed before final delivery:

1. **P0-A (security regression)**: `/__health?verbose=1` without admin token returns `pod_count: 1, routes_loaded: true, ab_enabled: false` — re-leaking the fleet size that Wave 2 P1-1 specifically gated. The Worker code's `if (isAdmin || url.searchParams.get('verbose') === '1')` OR clause bypasses the admin gate. Verified live just now.
2. **P0-B (severe documentation drift)**: `02_CLOUD_ARCHITECTURE.md` still describes v2.1.0 as live in 12+ places — TL;DR point 3, §1.2 diagram, §1.4 deployed list + verified list, §3.1 STATUS callout, §4.2 STATUS callout, §8 Phase 3+4 task lists (unchecked), §9 #8, §10 points 4+5+9. All still say "v2.1.0 deployed, geo-routing is Phase 3, A/B stickiness is Phase 4, Cron not implemented". The user's task description explicitly flagged this — confirmed across all flagged locations plus several more.

Netlify-ns-handoff.md also has stale Worker-version + cross-account claims (points 3+4), compounding the doc-drift problem.

---

## Phase 3a (geo-routing) verification — ✅ Logic sound

Worker source (`16_deploy_worker_v3.py` lines 145-168) — `pickPod(pods, country, colo)`:
1. **Active filter** (line 147): `pods.filter(p => p.active)` first. If empty, returns `null` (caller falls through to HTML landing).
2. **Region match** (lines 152-155): `p.regions.includes("*") || p.regions.includes(country)`. Correctly includes wildcard pods. Also handles missing `regions` field (`!p.regions` returns true → treated as `"*"`).
3. **Fallback** (line 158): `regionMatching.length > 0 ? regionMatching : active` — does NOT 404 on region miss. ✓

**Edge case review**: no scenario where a valid request gets 404'd by the region filter. The only "null return" path is all-pods-inactive, which falls through to the HTML landing page (line 284) — a behavior worth noting (see P3-A below).

Live evidence: `/app/test` from HK container → 302 to `app-test-01.sonicloud.app/app/test`. Pod's registry entry is `{regions: ["*"]}` so it matches all countries. ✓

---

## Phase 3b (Cron Trigger) verification — ✅ Correctly deployed + registered

**Live state confirmed via CF API** (just now):
```
GET /accounts/{acct}/workers/scripts/sonicloud-root-worker/schedules
→ {"schedules":[{"cron":"*/5 * * * *","created_on":"2026-08-17T09:08:29.32032Z"}],"success":true}
```

**Frequency**: `*/5 * * * *` = 288 fires/day. Worst-case KV writes = 288/day (only if every fire detects a state change). Fits within CF Free 1K writes/day limit with 3.5× headroom. ✓

**Logic** (lines 290-332): reads registry, loops pods sequentially, fetches `https://{pod.hostname}/__health` with 2s `AbortController` timeout, sets `pod.active = r.ok` (HTTP 2xx → true), only writes back to KV if `changed === true`. State-change-only writeback is a correct optimization — `updated_at` in KV will NOT advance on every cron fire, only when a pod's active flag flips. (The user's instruction "verify updated_at changed" is a false negative test: if all pods are healthy, `updated_at` stays at the seed value — that's the design.)

**Concerns**:
- **Sequential fetch**: 1 pod × 2s = 2s total — safe. At 30 pods × 2s = 60s — safe. At 150 pods × 2s = 300s — would approach the 5-min cadence. Recommend `Promise.allSettled(pods.map(fetch))` for Phase 5+ scale. P2-1.
- **Race condition**: CF guarantees at most one concurrent invocation per Cron Trigger, so two fires cannot overlap. ✓ No race.
- **2s timeout**: tight for cold-start pods but CF Workers cold-start is sub-10ms, so 2s is generous for the actual pod Worker. ✓

**Note**: the deploy log shows the Cron PUT initially FAILED at 09:07:54 with HTTP 403 error 10063 "You need a workers.dev subdomain in order to proceed." The script `16_deploy_worker_v3.py` does NOT bootstrap the workers.dev subdomain first — this is a deploy-script bug (P3-B). The cron was subsequently registered at 09:08:29 (likely via dashboard or after the user manually created the `sonicloud` workers.dev subdomain). Live check confirms workers.dev subdomain = "sonicloud". Future operators re-running the script on a fresh CF account will hit the same 403 — the script should `PUT /accounts/{acct}/workers/subdomain` before scheduling.

---

## Phase 4 (A/B stickiness) verification — ✅ Sticky + correctly disabled by default

**Live evidence** (deploy log + just now):
- A/B disabled (default): `/app/test` → 302 with NO `Set-Cookie` header. ✓
- A/B enabled (during deploy Test 6): `/app/test` → 302 + `Set-Cookie: variant=B; Path=/; Max-Age=31536000; SameSite=Lax`. ✓
- Stickiness (deploy Test 7): 10 requests with same UA + same container IP → all got variant A. Deterministic hash confirmed. ✓

**Logic review** (`pickVariant`, lines 171-186):
- Cookie regex `/variant=([AB])/` — only matches A or B; not lower-case, not other variants. ✓
- `hash32(ip + ua + salt)` — djb2 (synchronous, no Web Crypto needed), `Math.abs(h)` handles JS int sign. Deterministic given same input. ✓
- `hash % 100 < percent` — standard 50/50 split logic. ✓
- Cookie set via `withVariantCookie` (lines 189-195) using `new Response(response.body, response)` then `headers.set("set-cookie", ...)`. CF Workers DO support setting cookies on 302 redirects — verified live. ✓

**Variant filter** (lines 256-261): `route.pods.filter(p => !p.variant || p.variant === variant)` — pods without `variant` field are always included (treated as "any variant"). Fallback `if (candidatePods.length === 0) candidatePods = route.pods`. ✓ Correct — no 404 if variant mismatch.

**Concerns**:
- **Cookie missing `Secure` flag** — `variant=A; Path=/; Max-Age=31536000; SameSite=Lax` should be `Secure` for HTTPS-only transmission. Minor P2-2 (low risk since site is HTTPS-only and SameSite=Lax prevents CSRF).
- **Hash uses `cf-connecting-ip`** — for mobile users on rotating IPs, the variant can flip between sessions. Documented tradeoff in §4.1 row A2 ("Sticky depends on IP+UA stability — mobile networks churn IPs"). The hybrid A2+A3 design uses cookie for long-term stickiness + hash for first-visit determinism — correct.

---

## Open Q #9 resolution — ✅ Definitively resolved

**§9 #9** (line 669): marked ✅ RESOLVED 2026-08-17 with full CF docs quote: *"Subdomain setup is only available for Enterprise accounts. If you only want to create a subdomain for your site in Cloudflare, refer to Create a subdomain record."* Conclusion that "the separately-registered domain path is the ONLY free-tier path to per-account pod isolation" follows directly from the docs — Enterprise is the gate, not the account. ✓

**§2.0** (line 126): updated with same docs quote. Conclusion is correct and authoritative.

**Inconsistency**: §10 Summary point 4 (line 679) STILL says "Per-ACCOUNT isolation requires either (a) a separately-registered domain... OR (b) cross-account subdomain setup IF CF Free tier allows it (unverified — see §9 open question #9)" — directly contradicts §9 #9 resolution. Should be updated to remove option (b). P1-1.

---

## Backward compatibility — ✅ No regressions

| v2.1.0 behavior | v3.0.0 status | Live verified |
|---|---|---|
| `/__health` returns version + region | ✓ v3.0.0, also adds `country` + `ab_enabled` fields | ✓ |
| `/__health` no-token → `pod_count: -1` | ✗ **REGRESSION** via `?verbose=1` bypass — see P0-A | ✗ |
| `/__health` with-token → real pod count | ✓ | ✓ pod_count: 1 |
| `/__routes` no-token → 401 | ✓ | ✓ (verified live just now) |
| `/__routes` with-token → 200 JSON | ✓ (now also includes `ab_config`) | ✓ |
| `/app/test` → 302 to pod | ✓ | ✓ |
| Pod Worker at `app-test-01.sonicloud.app` responds | ✓ (version 1.0.0) | ✓ 200 in 45ms |
| Apex HTML landing at `/` | ✓ (HTML now says "v3.0.0 — geo + cron + A/B") | ✓ |

The single backward-compat regression is the `?verbose=1` security bypass — see P0-A.

---

## New issues found in Wave 3

### P0-A (blocking) — `/__health?verbose=1` bypasses admin token, leaks pod_count

**Worker code line 210**: `if (isAdmin || url.searchParams.get('verbose') === '1')`. The OR clause means anyone hitting `/__health?verbose=1` (no token) gets `pod_count: 1, routes_loaded: true, ab_enabled: false` — the exact same info leak Wave 2 P1-1 was designed to close.

**Live evidence** (just now): `curl -sk "https://sonicloud.app/__health?verbose=1"` returns `{"pod_count":1,"routes_loaded":true,"ab_enabled":false,"version":"3.0.0 (geo + cron + A/B)"}`. The deploy log captured this exact leak at Test 2 (line 22 of `deploy-worker-v3.log`) and the deploy script didn't flag it.

**Fix**: change line 210 to `if (isAdmin)` — drop the `verbose=1` bypass entirely. If verbose info is needed without a header (e.g., browser testing), require both: `if (isAdmin && url.searchParams.get('verbose') === '1')`. ~30-second edit + redeploy.

### P0-B (blocking) — Severe doc drift: 12+ places still describe v2.1.0 + Phase 3/4 as "not implemented"

`02_CLOUD_ARCHITECTURE.md` is still half-stale. Specific line-level inventory:

| Location | Current (stale) text | Should be |
|---|---|---|
| Line 17 (TL;DR pt 3) | "v2.1.0... (a) NO automatic failover — Phase 3 Cron Trigger not yet implemented; (b) NO geo-routing... (c) NO A/B stickiness — uses Math.random()" | "v3.0.0... (a) ✓ failover via every-5-min Cron; (b) ✓ geo-routing via `request.cf.country` region filter; (c) ✓ A/B stickiness via cookie + djb2 hash (disabled by default, enable via `ab_config` KV)" |
| Line 139 (§1.2 diagram) | "Apex Worker: sonicloud-root-worker (CF MAIN, v2.1.0 — KV-backed router + admin-token-gated debug)" | "...v3.0.0 — KV-backed router + geo + Cron failover + A/B stickiness" |
| Line 179 (§1.4 deployed) | "Apex Worker `sonicloud-root-worker` v2.1.0 (KV-backed router + admin-token-gated debug endpoints)" | "v3.0.0 (KV-backed router + geo + Cron failover + A/B stickiness)" |
| Line 185 (§1.4 verified) | "pod_count: 2, version: 2.0.0, region: HKG" | "pod_count: 1 (with token; -1 without), version: 3.0.0 (geo + cron + A/B), region: HKG, country: HK, ab_enabled: false" |
| Line 186 (§1.4 verified) | "curl https://sonicloud.app/__routes → 200 JSON with the full pod registry" | "curl -H 'x-admin-token: $TOKEN' https://sonicloud.app/__routes → 200 JSON with {routes, ab_config}" (gate verified Wave 2) |
| Line 190 (§1.4 KV test) | "added 2nd pod (app-test-02... 50/50 weight) via KV write... 20 requests... 9 → app-test-01, 11 → app-test-02" | Mark as "test-only, pod since removed" — registry now has 1 pod |
| Line 252 (§3.1 STATUS) | "STATUS (v2.1.0, live 2026-08-17): The deployed apex Worker reads request.cf.colo only for /__health telemetry. The pickPod function uses weighted random only — colo is ignored for pod selection. Geo-routing is Phase 3 (§8)." | "STATUS (v3.0.0, live 2026-08-17): pickPod now filters by `request.cf.country` against pod's `regions` array (`*` matches all). Falls back to all active pods if no region match. The pseudocode below is the LIVE design." |
| Lines 328-330 (§4.2 STATUS) | "TARGET — not yet implemented in v2.1.0... uses Math.random()... A/B stickiness is NOT implemented" | "STATUS (v3.0.0, live 2026-08-17): implemented as A2+A3 hybrid. Cookie `variant=A\|B`, 1-yr expiry, djb2 hash on first visit using ip+ua+salt. Disabled by default; enable via KV `ab_config.enabled=true`. Verified sticky (10/10 same-UA → same variant)." |
| Lines 619-622 (§8 Phase 3) | "[ ] Add Cron Trigger to apex Worker for periodic health checks. [ ] Add a second pod in a different region. [ ] Test geo-routing... [ ] Test failover..." | "✅ DONE 2026-08-17: Cron Trigger registered `*/5 * * * *` (created 09:08:29Z), geo-routing live (`request.cf.country` filter), failover logic live (`pod.active = r.ok`). Pending: second real pod + multi-region test." |
| Lines 625-629 (§8 Phase 4) | "[ ] Add variant field... [ ] Implement A2+A3 hybrid... [ ] Add AB_SALT... [ ] Test: clear cookies..." | "✅ DONE 2026-08-17: variant filter live, A2+A3 hybrid live (djb2 hash + cookie), `ab_config` KV key holds `{enabled, variant_b_percent, salt}` (no secret-redeploy needed). Verified sticky. Pending: 100-sample distribution test before first real landing test." |
| Line 668 (§9 #8) | "Health-check failover Cron Trigger — implement the scheduled handler..." (no DONE marker) | "✅ DONE 2026-08-17 — Worker v3.0.0 implements `scheduled()` handler. Cron registered `*/5 * * * *`. State-change-only KV writeback (288/day max). Pod outage → `active=false` → routing skips pod. Test by manually setting pod's `active=false` in KV and observing routing switch (cron then re-enables when pod's /__health returns 200)." |
| Line 679 (§10 pt 4) | "...Per-ACCOUNT isolation requires either (a) a separately-registered domain... OR (b) cross-account subdomain setup IF CF Free tier allows it (unverified — see §9 open question #9)" | Remove option (b). "Per-ACCOUNT isolation requires a separately-registered domain (~$10/yr) — cross-account subdomain setup is definitively BLOCKED on Free (Enterprise-only, see §9 #9)." |
| Line 680 (§10 pt 5) | "currently v2.1.0 deployed with weighted-random pod selection + admin-token-gated debug endpoints. Geo-routing via request.cf.colo (§3), A/B stickiness via cookie + hash (§4.2), and health-check failover via Cron Trigger (§3.3) are designed but NOT yet implemented — they're Phase 3/4 items." | "v3.0.0 deployed with weighted-random pod selection + admin-token-gated debug + geo-routing (§3.1) + A/B stickiness (§4.2, disabled by default) + every-5-min health-check Cron (§3.3). All Phase 3+4 items are DONE." |
| Line 684 (§10 pt 9) | "Next concrete change: Phase 3 (geo-routing + health-check Cron Trigger) when a second real pod is added." | "Phase 3+4 DONE 2026-08-17 (Worker v3.0.0). Next concrete change: add a second real pod (in a different CF account via separately-registered domain) and verify geo-routing + failover + A/B distribution end-to-end with real multi-pod traffic." |

### P1-1 — `netlify-ns-handoff.md` points 3+4 stale (v2.1.0 + cross-account "unverified")

- **Point 3** (line 278): "Per-ACCOUNT isolation requires either (a) a separately-registered domain... OR (b) cross-account subdomain setup IF CF Free tier allows it (unverified — see `02_CLOUD_ARCHITECTURE.md` §9 open question #9)" — stale; §9 #9 is now RESOLVED, option (b) is definitively blocked.
- **Point 4** (line 280): "sonicloud-root-worker v2.1.0... Caveats: NO automatic failover (Phase 3), NO geo-routing (Phase 3, but `request.cf.colo` IS read for /__health telemetry), NO A/B stickiness (Phase 4)" — ALL THREE caveats are now DONE in v3.0.0.

This is the entry-point doc per README reading order. An operator reading this first will inherit the wrong mental model that Phase 3+4 are still pending. ~5-minute doc edit.

### P2-1 — Cron fetches are sequential, not parallel

At 1 pod this is fine (max 2s). At 30 pods × 2s = 60s — still fine. At 150 pods × 2s = 300s — approaches 5-min cadence. Recommend `Promise.allSettled(pods.map(pod => fetchWithTimeout(pod)))` for Phase 5+ scale. Non-blocking for current 1-pod fleet.

### P2-2 — `variant` cookie missing `Secure` flag

Cookie string is `variant=A; Path=/; Max-Age=31536000; SameSite=Lax`. Should be `variant=A; Path=/; Max-Age=31536000; SameSite=Lax; Secure` for HTTPS-only transmission. Low actual risk (site is HTTPS-only, `SameSite=Lax` blocks CSRF), but defense-in-depth. ~5-character edit.

### P3-A — All-pods-inactive returns HTML landing page, not 503

If all pods in a route go inactive (or pickPod returns null), the routing code falls through to `return new Response(HTML, ...)` (line 284) — the user gets a 200 with the apex landing page, not a 503 with an explicit error. Operationally misleading: the user thinks the URL worked, but no pod served the request. Recommend returning `503 Service Unavailable` with a JSON error body when `pickPod` returns null in the routing path. ~5-line change.

### P3-B — Deploy script `16_deploy_worker_v3.py` doesn't bootstrap workers.dev subdomain

The Cron registration step (lines 373-374) failed at 09:07:54 with HTTP 403 error 10063 "You need a workers.dev subdomain in order to proceed." The script printed `?` (warning) and continued — silent failure for the operator. Cron was subsequently registered at 09:08:29 after the `sonicloud` workers.dev subdomain was created (likely manually via dashboard).

Fix: before Step 5, add `PUT /accounts/{acct}/workers/subdomain` with body `{"subdomain": "sonicloud"}` (idempotent — returns 200 if already exists). Or check existence first via `GET /accounts/{acct}/workers/subdomain` and only PUT if 404. ~10-line addition.

### P3-C — `__health` includes `country` field (minor info disclosure)

v3.0.0's `/__health` now returns `country: "HK"` (visitor's country code) and `region: "HKG"` (colo). Previously v2.1.0 returned only `region`. Disclosing the visitor's country via a public endpoint is mildly useful for debugging but slightly more info than necessary. Acceptable, just note it.

---

## Final verdict + sign-off

**APPROVE-WITH-CONDITIONS**.

Worker v3.0.0 is functionally correct and live-verified — geo-routing, Cron, A/B stickiness, and Open Q #9 resolution are all done as designed. Phase 3+4 logic is sound (no 404 risk, state-change-only writeback, deterministic hash). Backward compat with v2.1.0 is preserved except for the `?verbose=1` security regression.

**Required before final APPROVE** (estimated 40 minutes total):

1. **P0-A** (~5 min) — Fix Worker line 210 to remove the `?verbose=1` bypass. Redeploy via `16_deploy_worker_v3.py` (or just a Worker source PUT). Verify live: `curl -sk https://sonicloud.app/__health?verbose=1` returns `pod_count: -1`.
2. **P0-B** (~25 min) — Sweep `02_CLOUD_ARCHITECTURE.md` per the 12-row table above. TL;DR pt 3, §1.2 diagram, §1.4 deployed + verified lists, §3.1 STATUS callout, §4.2 STATUS callout, §8 Phase 3+4 task lists (mark ✅ DONE), §9 #8 (mark ✅ DONE), §10 pts 4+5+9.
3. **P1-1** (~5 min) — Update `netlify-ns-handoff.md` points 3+4 to reflect v3.0.0 + resolved §9 #9.

**Recommended** (defer to Wave 4 if time-constrained):

4. **P2-1** — Cron parallel fetch (Phase 5+ scale work).
5. **P2-2** (~30 sec) — Add `Secure` flag to variant cookie.
6. **P3-A** (~5 min) — 503 on all-pods-inactive instead of HTML landing.
7. **P3-B** (~10 min) — Deploy script bootstraps workers.dev subdomain before scheduling.
8. **P3-C** — Note `country` field disclosure in §3.1.

Once P0-A + P0-B + P1-1 land, this is ready for final delivery to the user.
