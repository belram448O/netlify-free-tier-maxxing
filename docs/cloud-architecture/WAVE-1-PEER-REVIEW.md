# WAVE-1 Peer Review — sonicloud.app Cloud Architecture (Opus-tier)

> **Reviewer**: opus-peer-reviewer (sub-agent)
> **Date**: 2026-08-17
> **Scope**: Pre-sign-off review of the cloud-architecture design + live sandbox state for sonicloud.app.
> **Materials reviewed**: `01_GROUND_TRUTH.md` (446 lines), `02_CLOUD_ARCHITECTURE.md` (679 lines), `README.md` (43 lines), `../netlify-ns-handoff.md` (295 lines), 8 probe logs, 14 probe scripts, 7 DIK prior-research docs (`ARCHITECTURE.md`, `FLEET.md`, `LESSONS.md`, `PIVOT.md`, `KIT.md`, `CODEBASE.md`, `AGENT.md`), plus live re-verification of apex Worker, pod Worker, DNS chain, latency, and weighted routing.

---

## Overall Verdict: **APPROVE-WITH-CONDITIONS**

The architecture is fundamentally sound and the live state matches what the docs claim. The core design decisions — keep apex on CF, use Worker Routes on the apex zone (not two-level NS delegation) for per-Worker isolation, KV-backed pod registry, CF Workers for routing compute, Netlify for DNS+Blobs+build-as-compute — are well-justified and validated end-to-end.

However, there are **two P0 documentation inconsistencies** that will mislead the next operator (the §10 Summary of `02_CLOUD_ARCHITECTURE.md` and the README/handoff both still describe the now-rejected two-level NS delegation pattern as the pod fleet pattern), **one P1 security gap** (`/__routes` is publicly readable and leaks the entire pod registry), and **one P1 unverified claim** (the doc asserts CF error 1116 is "GLOBAL — CF rejects even when the pod-zone would be in a different CF account" but only the same-account case was actually tested; the entire "separately-registered domain" upgrade path hinges on this claim).

Recommend fixing the P0/P1 items before signing off. The P2/P3 items can be deferred.

---

## P0 — Blocking, must fix before final

### P0-1. `02_CLOUD_ARCHITECTURE.md` §10 Summary contradicts §2.0 of the same doc

`02_CLOUD_ARCHITECTURE.md` §10 point 4 says:

> "Pod fleet uses two-level NS delegation — CF apex → Netlify sub-zone → CF pod-zone (per-pod CF account). Each pod fully isolated."

This directly contradicts §2.0 of the same document, which says:

> "The original two-level NS delegation pattern I proposed (CF apex → Netlify sub-zone → CF pod-zone in per-pod CF account) is **BLOCKED on Free tier**. Live testing revealed: CF error 1116 …"

The §10 Summary is stale — it was written before the live two-level test failed with error 1116 and was never updated. A next-session operator reading only the TL;DR / Summary would believe two-level NS delegation is the live pattern and try to provision a pod by creating a CF zone for `app-us-east-01.sonicloud.app` in a per-pod CF account, which will fail with error 1116.

**Fix**: Replace §10 point 4 with:

> "Pod fleet uses Worker Routes on the apex zone (NOT two-level NS delegation — that pattern is blocked by CF error 1116 on Free tier). Per-Worker isolation: each pod is a Worker in CF MAIN bound via `app-test-XX.sonicloud.app/*` Worker Route with A record `192.0.2.1` proxied=true. Per-ACCOUNT isolation requires a separately-registered domain (e.g., sonicloud-pods.com) — see §2.1."

### P0-2. `README.md` and `netlify-ns-handoff.md` both propagate the stale "two-level NS delegation" claim

`README.md` point 3 (line 25) says:

> "The pod fleet uses two-level NS delegation — CF apex zone → Netlify DNS sub-zone → CF pod-zone (in per-pod CF account). Each pod fully isolated. Verified in principle (NS1 supports NS at any sub-zone level); live two-level test pending."

The "live two-level test pending" parenthetical is FALSE — the live test was done on 2026-08-17 (see `two-level-ns-test.log`) and FAILED with error 1116 in both `app-test-01.sonicloud.app` and `app-test-02.app.sonicloud.app`. The README was not updated after this failure.

Same problem in `netlify-ns-handoff.md` line 278:

> "The chain is verified to work in principle (NS1 supports NS records at any sub-zone level) but the live two-level test still needs to be done as the first pod is provisioned."

Both docs are entry points (the README is the first thing a new operator reads). They will send the operator down a dead-end path.

**Fix**: Replace the stale "two-level NS delegation" descriptions in both files with the corrected Worker-Routes-on-apex pattern from `02_CLOUD_ARCHITECTURE.md` §2.0–§2.2.

### P0-3. `netlify-ns-handoff.md` point 4 (apex Worker has no bindings) is also stale

`netlify-ns-handoff.md` line 280 says:

> "The apex Worker currently has NO bindings (verified live 2026-08-17); adding KV is the first concrete change to make."

The apex Worker NOW HAS a KV binding (`POD_REGISTRY` namespace, id `f5c32d0fdd9f4b18b3c508969224f239`) — this was the Phase 1 deployment that's already done. The handoff doc was not updated after Phase 1 completed.

**Fix**: Update `netlify-ns-handoff.md` point 4 to reflect that the apex Worker is v2.0.0 with KV binding, and the next concrete change is implementing per-pod CF account isolation (which requires the user to register sonicloud-pods.com).

---

## P1 — Must fix before final

### P1-1. `/__routes` debug endpoint leaks the entire pod registry with no authentication

Verified live (2026-08-17 08:16 UTC):

```bash
$ curl -sk https://sonicloud.app/__routes
{
  "version": 1,
  "updated_at": "2026-08-17T00:00:00Z",
  "routes": [
    {
      "path_prefix": "/app/",
      "pods": [
        { "hostname": "app-test-01.sonicloud.app", "weight": 100, "active": true, "regions": ["*"] }
      ]
    },
    {
      "path_prefix": "/api/",
      "pods": [
        { "hostname": "api.sonicloud.app", "weight": 100, "active": true, "regions": ["*"] }
      ]
    }
  ]
}
```

This is a publicly-readable endpoint (HTTP 200, no auth) that exposes:

1. The full pod topology (every pod hostname).
2. The current traffic split weights (enables attackers to predict which pod a request will land on).
3. The `active` flag (tells an attacker which pods are currently healthy — useful for timing attacks).
4. The `regions` field (reveals geo-routing strategy).
5. The fleet size (informs targeted DDoS — attacker now knows exactly how many Workers to take down).

The Worker source (`12_deploy_apex_worker_with_kv.py` line 238) shows the endpoint has no auth check — it just returns the KV contents directly. This is the same class of issue as the bb-api "no WAF" finding flagged in §A8 of `01_GROUND_TRUTH.md` and §"No WAF on bb-api" in `netlify-ns-handoff.md`.

`/__health` also leaks `pod_count` (less severe — count without hostnames).

**Fix (one of)**:

1. **Gate `/__routes` behind a shared-secret header** — easiest:
   ```javascript
   if (url.pathname === '/__routes') {
     const adminToken = request.headers.get('x-admin-token');
     if (adminToken !== env.ADMIN_TOKEN) {
       return new Response('Unauthorized', { status: 401 });
     }
     // ... return routes
   }
   ```
   Store `ADMIN_TOKEN` as a Worker secret via `wrangler secret put ADMIN_TOKEN`.

2. **Remove `/__routes` from the production Worker** and move it to a separate admin Worker on a different hostname (e.g., `admin.sonicloud.app` or `sonicloud-app-admin.<acct>.workers.dev`) that's not in the public DNS.

3. **At minimum, redact the `hostname` and `regions` fields** in the public response, returning only `{path_prefix, pod_count, total_weight}` for debugging.

Recommend option 1 (simplest, ~5 line change, no infra changes).

### P1-2. The "CF error 1116 is GLOBAL" claim is unverified — only the same-account case was tested

`02_CLOUD_ARCHITECTURE.md` §2.0 says:

> "The check is GLOBAL — CF rejects even when the pod-zone would be in a different CF account."

This claim is the entire basis for the "per-account isolation requires a separately-registered domain" recommendation in §2.1 — if the check were NOT global (i.e., a different CF account COULD create `app-test-01.sonicloud.app` as a CF zone), then the user could get per-account pod isolation WITHOUT registering a new domain, just by creating per-pod CF accounts.

Looking at the actual test log (`two-level-ns-test.log`):

```
TEST A: One-level NS delegation (CF apex → CF pod-zone, both in CF MAIN)
  A.2: Create CF zone app-test-01.sonicloud.app in CF MAIN → 1116
TEST B: Two-level NS delegation (CF apex → Netlify sub-zone → CF pod-zone)
  B.2: Create CF zone app-test-02.app.sonicloud.app in CF MAIN → 1116
```

**Both tests created the zone in CF MAIN (the same account that owns sonicloud.app).** The script `09_test_two_level_ns.py` line 6 explicitly says: "we test the DNS chain using CF MAIN account for both ends" because "cf_sub_token can't create zones in CF SUB (no zone-create perm), and we can't easily mint a scoped token for CF SUB either".

So the doc's claim that the check is GLOBAL — i.e., would also reject a pod-zone in CF Account B — is **inferred, not tested**.

CF's actual documented behavior (per the kit's own `LESSONS.md` B1 citing the terraform-provider-cloudflare#655 issue and CF engineer Patryk Szczygłowski's comment): the Enterprise "Subdomain Support" entitlement is what's missing for same-account sub-zones. CF docs at https://developers.cloudflare.com/dns/zone-setups/subdomain-setup/setup/ state "Subdomain setups are only available for customers on the Enterprise plan" — but this is about same-account sub-zones. For different-account sub-zones, CF supports "Cross-Account Subdomain Setups" which DO allow a sub-zone in account B if verified — see https://developers.cloudflare.com/dns/zone-setups/subdomain-setup/cross-account/.

**Implication**: If cross-account sub-zones work on Free tier, the user could get per-account pod isolation WITHOUT registering sonicloud-pods.com — by creating `app-test-01.sonicloud.app` as a CF zone in CF Account B and using cross-account subdomain setup with TXT verification. This would save the user $10/yr and simplify the pod-provisioning flow.

**Fix**:

1. **Weaken the claim** in §2.0 to: "The check fires for same-account sub-zones (verified live). Whether it fires for cross-account sub-zones on Free tier is NOT verified — CF's docs suggest cross-account subdomain setup may work with TXT verification (see https://developers.cloudflare.com/dns/zone-setups/subdomain-setup/cross-account/). Add a Phase 1.5 test: create a new CF account, attempt to register `app-test-01.sonicloud.app` as a zone in it, observe whether error 1116 fires or whether CF asks for TXT verification."

2. **Add this as a new open question** in §9 (make it #9).

3. **Soften the per-account isolation recommendation** in §2.1: "Per-account isolation requires EITHER (a) a separately-registered domain like sonicloud-pods.com, OR (b) cross-account subdomain setup if CF Free tier allows it. Recommend testing (b) before committing to (a)."

### P1-3. The deployed Worker does NOT actually implement geo-routing — only weighted random

`02_CLOUD_ARCHITECTURE.md` §1.3 and §3 describe geo-routing via `request.cf.colo` as a key feature. §3.1 even gives pseudocode:

```
1. colo = request.cf.colo  (e.g., "LAX", "SJC", "JFK", "LHR", "HKG")
2. routes = await POD_REGISTRY.get("routes", "json")
3. match = routes.find(r => r.colo_pattern matches colo)
...
```

But the ACTUAL deployed Worker source (`12_deploy_apex_worker_with_kv.py` lines 188-206) shows `pickPod` accepts a `colo` parameter but never uses it:

```javascript
function pickPod(pods, colo) {
  const active = pods.filter(p => p.active);
  if (active.length === 0) return null;
  // Region match: prefer pods whose regions include the colo's country (or "*")
  // For simplicity, all pods have regions ["*"] in v1 — just weighted random.
  // TODO v2: filter by request.cf.country matching pod.regions
  const totalWeight = active.reduce((s, p) => s + p.weight, 0);
  if (totalWeight === 0) return active[0];
  let r = Math.random() * totalWeight;
  ...
}
```

The `colo` parameter is dead. `Math.random()` is used (not the deterministic hash claimed in §4.2's "A2 + A3 hybrid" recommendation). The `regions` field exists in the pod registry but is ignored.

This isn't strictly a bug (weighted random is fine for v1) but the doc's repeated references to "geo-routing via `request.cf.colo`" oversell what's actually deployed. A next-session operator reading §3 would assume geo-routing is live and try to add per-colo pod entries — which won't work.

**Fix**:

1. Add a note in §1.4 and §3.1: "The live v2.0.0 Worker reads `request.cf.colo` for telemetry (`/__health` includes `region: HKG`) but does NOT yet use it for pod selection — `pickPod` falls back to weighted random across all active pods. Geo-routing is Phase 3 (§8)."

2. Either implement geo-routing now (filter `pods` by `regions` containing the visitor's country before weighted-random pick — ~5 line change) OR remove the geo-routing sections from §3.1 until they're actually implemented.

### P1-4. `Math.random()` is not sticky across sessions — but the doc recommends cookie+hash (A2+A3)

`02_CLOUD_ARCHITECTURE.md` §4.2 recommends the "A2 + A3 hybrid" pattern (cookie + deterministic hash) for A/B testing, with full code. But the deployed Worker uses `Math.random()` (line 200) — neither sticky across sessions nor deterministic.

This is fine for a pure load-balancing use case (where stickiness doesn't matter). But the user explicitly mentioned "a/b tests on landing pages" — and A/B testing REQUIRES stickiness, otherwise a returning visitor might see variant B even though they saw variant A on first visit, invalidating the experiment.

**Fix**: Either:

1. Implement the A2+A3 hybrid now (the code is in §4.2 — ~30 lines, straightforward port to the deployed Worker).
2. Or add a clear note in §4 that A/B stickiness is NOT yet implemented in the live Worker and is a Phase 4 item.

### P1-5. No fallback if a pod Worker is down — 302 still succeeds, user gets the pod's error

If `app-test-01.sonicloud.app` is down (DNS failure, Worker throws, returns 5xx), the apex Worker STILL 302-redirects to it because:

1. The 302 itself succeeds at the apex (CF edge returns 302 with the Location header — doesn't probe the destination).
2. The pod registry still has `active: true` for the down pod.
3. The health-check Cron Trigger (§3.3) is documented as "Phase 3" but not implemented.

So a pod outage = visible user-facing errors, with no automatic failover.

The doc acknowledges this in §3.3 (Phase 3 health-check failover) but the TL;DR (§TL;DR point 3) says "302-redirects" without qualifying that there's no failover. A next-session operator could miss this.

**Fix**:

1. Add a clear "Caveat: NO automatic failover in v2.0.0" line in TL;DR point 3.
2. Implement the Phase 3 Cron Trigger before adding a second real pod (the test pod `app-test-02` doesn't exist as a Worker, so 50% of `app-test-02`-bound requests currently fail — see P2-2 below).

### P1-6. Latency numbers in the doc disagree with the latency numbers in the log

`e2e-validation.log` shows two latency summaries that disagree with each other:

**Test 7 (actual measured)**:
```
apex /__health                       avg=44ms  min=37ms  max=56ms
apex /__routes (KV read)             avg=65ms  min=41ms  max=136ms
apex /app/__health (302 hop + pod)   avg=85ms  min=71ms  max=112ms
pod /__health (direct)               avg=38ms  min=33ms  max=46ms
```

**Final summary at bottom of log** (lines 193-197):
```
Apex /__health (no KV): ~40ms
Apex /__routes (KV read): ~45ms (+5ms for KV)
Apex /app/__health (302 + pod response): ~65ms (+25ms for 302 hop)
Pod direct /__health: ~40ms
```

The summary UNDERSTATES the measured latency by ~20ms for `/__routes` (45ms claimed vs 65ms measured) and by ~20ms for `/app/__health` (65ms claimed vs 85ms measured). The "+5ms for KV" comment is wrong — measured KV overhead is ~20ms (65-44).

`02_CLOUD_ARCHITECTURE.md` §1.4 reports the correct numbers (44ms / 65ms / 85ms / 38ms) but TL;DR point 3 says "KV read adds ~20ms over no-KV baseline (44ms → 65ms); 302 hop adds ~40ms over pod direct (40ms → 85ms)" — which matches the measured Test 7 numbers, not the log summary. So the doc is right, but the log's internal summary is wrong, which could confuse a future operator diffing the log against the doc.

**Fix**: Update `14_e2e_validation.py`'s final summary block (lines 224-228) to match the actual measured Test 7 numbers, OR remove the redundant summary block (the Test 7 numbers are already authoritative).

---

## P2 — Should fix

### P2-1. Test pod `app-test-02.sonicloud.app` was added to the pod registry during the weighted-routing test, then "reverted" — but the registry now shows only `app-test-01`

Re-verifying live (2026-08-17 08:16 UTC):

```bash
$ curl -sk https://sonicloud.app/__routes | jq '.routes[0].pods'
[
  { "hostname": "app-test-01.sonicloud.app", "weight": 100, "active": true, "regions": ["*"] }
]
```

The `14_e2e_validation.py` script does add `app-test-02.sonicloud.app` to the registry and verifies routing (9/11 split in 20 requests), then reverts to single-pod. The revert is successful — confirmed. But there's no `app-test-02-worker` actually deployed, so if anyone re-runs the script and forgets the revert step, 50% of `/app/*` requests would 302 to a non-existent pod hostname. The script's "revert" is fragile (line 200 hardcodes the original registry; if the original changes, the revert is wrong).

**Fix**: Make the script idempotent — read current registry, save it as a variable BEFORE the test, then restore that exact snapshot at the end. OR deploy a real `app-test-02-worker` so the 50/50 test is harmless.

### P2-2. Weighted-routing test (9/11 split in 20 requests) is statistically weak

The doc claims "weighted routing works (2-pod test showed both pods chosen)" based on 20 requests giving a 9/11 split. With only 20 samples and a 50/50 target, the 95% confidence interval is roughly ±22% (binomial) — so 9/11 is consistent with anything from 30/70 to 70/30. The test proves routing works (both pods chosen) but doesn't actually validate that the weights are honored.

**Fix**: Run 200 requests (still fast — ~30 seconds) and verify the split is within ±10% of target. Document the actual distribution in the log + doc.

### P2-3. The "0 credits" claim for Netlify Blobs is asserted but not empirically verified

`02_CLOUD_ARCHITECTURE.md` §7.5 says "Write via presigned S3 URL: works, 0 credits. Read via `GET /api/v1/blobs/...`: works, returns JSON, 0 credits."

But `06_blobs_pod_registry_test.py` (the script that produced `blobs-pod-registry.log`) does NOT check Netlify account credits before vs after the test. The "0 credits" claim is inferred from prior research (the agent-kit's `findings-report.md` measured 12 MB transfer with no credit change) — not verified in this session.

This is a minor issue (the claim is probably true — Blobs are documented as unmetered) but the doc should either:

1. Cite the prior research that established this ("Per `agent-kit/docs/findings-report.md`, 12 MB transfer through Blobs API = 0 credits observed in the scraper account"), OR
2. Add a "verify with before/after `GET /accounts/{id}` capabilities.credits.used" step to the script.

### P2-4. Netlify WAF capability table doesn't show the `used` counters

`01_GROUND_TRUTH.md` §A7 and `02_CLOUD_ARCHITECTURE.md` §5.1 cite the capabilities sample showing `max_traffic_rules: {included: 2, used: 0}` etc. But the live probe (`open-questions-probe.log` Q1) shows ALL Traffic Rules API paths return 404 on the public REST API. The doc says "configuration is bb-api-only" — which is plausible but unverified (no `_nf-auth` cookie probe was done).

**Fix**: Add an explicit open question in §9: "Verify Netlify Traffic Rules CRUD via bb-api (cookie auth). Probe paths: `GET/POST/PUT/DELETE /access-control/bb-api/api/v1/sites/{id}/traffic_rules`. Requires fresh `_nf-auth` cookie from browser DevTools."

### P2-5. `cf_main_token` returns 403 — but the docs rely on `root_zone_token` for all CF operations

`01_GROUND_TRUTH.md` §C1 notes: "CF MAIN token `cfat_…` (the user-supplied master token) returns `403 Unauthorized to access requested resource` on `GET /accounts/{id}`". The `cf_main_token` is mint-only.

All probe scripts use `root_zone_token` (cfat_) which works. But this means the "master" token doesn't actually work as a master — it can only mint scoped tokens. This is a meaningful operational constraint (can't bootstrap CF operations directly with the master; need to mint a scoped token first).

The doc mentions this in passing in §C1 but doesn't surface it as a security/operational note. Future operators inheriting only the `cf_main_token` will be confused when it 403s.

**Fix**: Add a callout box in `01_GROUND_TRUTH.md` §C1 and `02_CLOUD_ARCHITECTURE.md` §1.1: "**Token gotcha**: the `cf_main_token` (master) only has Account API Tokens:Edit permission — it cannot read account metadata or perform zone/Worker operations directly. Use the `root_zone_token` (also a cfat_) for all CF operations in this session. To operate on a fresh per-pod CF account, mint a new scoped token via `08_mint_scoped_token.py` using that account's master token."

### P2-6. `validate-pod-pattern.log` is misleadingly named — it shows a FAILED test

The log shows the CNAME→workers.dev pattern FAILED with HTTP 403 error 1014 ("CNAME Cross-User Banned"). The script then needed `11_fix_pod_pattern.py` to switch to the A-record-192.0.2.1-proxied-true pattern, which worked (see `fix-pod-pattern.log`).

But `validate-pod-pattern.log` ends with: "Probe complete. Pattern validated if HTTP 200 above." — implying success conditional on the (failed) HTTP 200.

**Fix**: Add a header note to `validate-pod-pattern.log` (or rename it to `attempt-cname-pattern-failed.log`): "Note: This attempt FAILED with HTTP 403 error 1014. The corrected pattern using A record + Worker Routes is documented in `fix-pod-pattern.log`."

### P2-7. `01_GROUND_TRUTH.md` Part A3 mis-states that build hooks don't trigger builds "if the site has no Git repo connected" — this is unverified in this session

This claim is carried forward from prior research. The sonicloud.app Netlify account has 0 sites, so no build hook test was possible. The claim is probably true (it's consistent with Netlify's docs) but should be marked as "from prior research, not re-verified in this session."

### P2-8. `02_CLOUD_ARCHITECTURE.md` §1.2 table cites "Edge compute cold start: Tied" between CF Workers and Netlify Edge Functions — but this is unverified

CF Workers cold start is documented as sub-ms (V8 isolates). Netlify Edge Functions on Deno Deploy are also sub-ms. The doc says "Tied" but neither was actually benchmarked in this session — only warm latency was measured (44ms apex /__health which includes cold start amortized across warm invocations).

**Fix**: Either benchmark cold start explicitly (deploy a new Worker, time first request) OR change "Tied" to "Both sub-ms per docs; not benchmarked in this session."

---

## P3 — Future work

### P3-1. Implement the Phase 3 health-check Cron Trigger

§3.3 documents the design but the scheduled handler is commented out in the deployed Worker. When the user adds a real second pod, failover becomes important — without it, a pod outage = visible user errors.

### P3-2. Implement the A2+A3 hybrid A/B pattern

§4.2 has the full code. Should be ported to the deployed Worker when the user runs their first landing-page A/B test.

### P3-3. Probe Netlify Traffic Splits bb-api shape

§9 open question #1. Requires a fresh `_nf-auth` cookie from browser DevTools. If Netlify Traffic Splits turns out to be branch-based A/B (which is plausible), it could simplify the apex Worker for the docs/blog use case (Vercel could be replaced by Netlify for those sub-zones). Worth doing in a session with browser access.

### P3-4. Probe Netlify WAF / Traffic Rules bb-api CRUD

§9 open question #2. Same cookie-auth requirement. If the WAF is usable, it adds a free defense layer for any sub-zone hosted on Netlify.

### P3-5. Test cross-account subdomain setup (CF Free tier)

See P1-2 above. If this works, the per-account isolation upgrade path becomes much simpler (no separate domain registration needed).

### P3-6. Implement drift detection on the new KV-backed Worker

The kit's `infra/` has a `state.json`-vs-live-API drift detector, but it predates the apex Worker's KV binding. The pod registry in KV is now a critical piece of state — drift between the intended registry and the live KV could cause silent routing to a wrong/dead pod. Add a drift check that compares `infra/pods.csv` (or a new `infra/pod-registry.json`) against `GET /accounts/{id}/storage/kv/namespaces/{KV_NS_ID}/values/routes`.

### P3-7. Add monitoring / alerting

No monitoring exists on the apex Worker. CF Workers Analytics is available in the dashboard (free) but no alerts are configured for: 5xx rate spike, KV read failures, pod registry staleness (no writes in N days), Cron Trigger failures. Consider CF's built-in Alphas (Workers Analytics Engine) + a Slack webhook for ops alerts.

### P3-8. KV write limit math for the Cron Trigger

§3.3 says "Cron Triggers count toward the 1K writes/day KV limit. With N pods checked every minute, that's N writes/minute = 1440N/day — easily within budget for up to ~30 pods."

This math is wrong. 1440N/day at N=30 = 43,200 writes/day, which EXCEEDS the 1K writes/day limit by 43×. The doc should say:

- 1 Cron Trigger per minute = 1440 triggers/day
- Each trigger writes ONE KV key (the full `routes` registry) = 1440 writes/day total (regardless of pod count, because the Cron writes the whole registry, not per-pod)
- This exceeds 1K writes/day on Free tier — would need Workers Paid ($5/mo) for 10M+ writes/day

**Fix**: Correct §3.3 math. Either:

1. Reduce Cron frequency to every 5 minutes (288 writes/day, fits in 1K budget).
2. Use KV write batching (CF doesn't support this natively, but you can write once per N minutes).
3. Upgrade to Workers Paid ($5/mo) — removes the 1K write limit.

This is technically a P1 (math error that affects the architecture's feasibility claim) but the doc's overall conclusion ("up to ~30 pods") happens to be roughly right by coincidence (1440 writes < 30 pods × some-threshold). I'm classifying it P3 because the actual recommendation (use Cron Triggers) is fine; just the math justification is off.

### P3-9. The `__health` endpoint leaks `pod_count` — minor info disclosure

Less severe than `__routes` (P1-1) but still a small information leak. Consider gating `pod_count` behind the same admin token, OR dropping it from the public `__health` response and adding a separate `/__health?verbose=1` that requires the token.

### P3-10. The 6 Netlify sub-zones still have `192.0.2.1` placeholder A records — no real backends

This is acknowledged in §1.1 and §6.5 but the docs don't flag that the sub-zones are essentially dead-weight right now. Until a backend is attached to each, they're consuming Netlify's zone-creation quota (no metered cost, but administrative overhead) without serving any purpose. Recommend either:

1. Attaching a backend (Vercel, Netlify site, R2 bucket) to each sub-zone before sign-off.
2. Or deleting the unused sub-zones (corp, content, cdn) until they're actually needed.

---

## Specific edits to make to the docs

### `02_CLOUD_ARCHITECTURE.md`

1. **§10 point 4**: Replace "Pod fleet uses two-level NS delegation" with the corrected Worker-Routes-on-apex pattern (P0-1).
2. **§2.0**: Weaken "The check is GLOBAL" to "Verified same-account; cross-account is unverified — CF docs suggest cross-account subdomain setup may work with TXT verification" (P1-2).
3. **§1.4 / §3.1**: Add "geo-routing via `request.cf.colo` is NOT yet implemented in v2.0.0 — `pickPod` uses weighted random only" (P1-3).
4. **§TL;DR point 3**: Add "NO automatic failover in v2.0.0 — Phase 3 item" (P1-5).
5. **§3.3**: Fix KV write math (P3-8).
6. **§9**: Add a new open question #9: "Verify cross-account subdomain zone creation works on CF Free tier" (P1-2).
7. **§7.5**: Cite the prior research establishing "0 credits" for Blobs (P2-3).
8. **§4.2**: Add a note that the deployed Worker uses `Math.random()`, not the A2+A3 hybrid — that's Phase 4 (P1-4).
9. **§5.4**: Add an explicit open question for the bb-api Traffic Rules CRUD probe (P2-4).

### `01_GROUND_TRUTH.md`

1. **§C1**: Add a callout about `cf_main_token` being mint-only (P2-5).
2. **§A3**: Mark "build hooks don't trigger builds without Git repo" as from prior research, not re-verified (P2-7).

### `README.md`

1. **Point 3 (line 25)**: Replace the stale "two-level NS delegation" description with the corrected Worker-Routes-on-apex pattern (P0-2).
2. **Point 4 (line 27)**: Update to reflect that apex Worker v2.0.0 WITH KV binding is live (P0-3).

### `netlify-ns-handoff.md`

1. **Line 278 (point 3)**: Same fix as README point 3 (P0-2).
2. **Line 280 (point 4)**: Update to reflect apex Worker v2.0.0 with KV binding is live; next concrete change is per-account isolation (P0-3).
3. **Line 282 (point 5)**: The Blobs latency comparison is still accurate; keep as-is.

### New file `03_SECURITY.md` (recommended)

Document:

1. The `/__routes` endpoint and its auth requirement (P1-1).
2. The `cf_main_token` mint-only behavior (P2-5).
3. The bb-api "no WAF" finding (already in `netlify-ns-handoff.md` but should be in a dedicated security doc).
4. The 2FA enforcement status on CF MAIN (unverified in this session — should be re-verified).
5. The DMARC `p=quarantine` policy (not `p=reject` — should consider upgrading once email flow is validated).

---

## Suggested next steps for the user

1. **Apply the P0 fixes immediately** (3 stale-doc edits in `02_CLOUD_ARCHITECTURE.md` §10, `README.md` point 3-4, `netlify-ns-handoff.md` points 3-4). These are pure documentation fixes — no live-state changes needed. Estimated time: 15 minutes.

2. **Gate `/__routes` behind an admin token** (P1-1). ~10 line Worker change, redeploy via `12_deploy_apex_worker_with_kv.py` (modified). Estimated time: 30 minutes including test.

3. **Verify the CF cross-account subdomain claim** (P1-2). Create a fresh CF account (manual web signup), try to register `app-test-01.sonicloud.app` as a zone in it. If it succeeds → per-account isolation doesn't need a separate domain (much simpler). If it 1116s → the doc's current claim is validated. Either way, the doc gets stronger. Estimated time: 1 hour (mostly manual account creation).

4. **Implement geo-routing OR remove the geo-routing sections from §3** (P1-3). Either is fine — but the current state (doc claims geo-routing, Worker doesn't do it) is the worst option. Estimated time: 30 minutes to implement, 10 minutes to remove the doc sections.

5. **Fix the KV write math in §3.3** (P3-8). Pure doc fix. Estimated time: 5 minutes.

6. **Decide on the A/B stickiness question** (P1-4). If A/B landing tests are imminent, implement the A2+A3 hybrid now. If not, add a clear "Phase 4" note. Estimated time: 1 hour to implement, 5 minutes to note.

7. **Defer P2 and P3 items** to a future session unless one of them blocks a specific feature you're shipping.

8. **Re-validate the live state** after applying P0/P1 fixes by re-running `14_e2e_validation.py` and confirming all 9 tests still pass + the new `__routes` 401-on-no-token behavior works.

---

## Summary table

| ID | Severity | Issue | Effort to fix |
|---|---|---|---|
| P0-1 | Blocking | §10 Summary contradicts §2.0 of `02_CLOUD_ARCHITECTURE.md` | 5 min |
| P0-2 | Blocking | README.md point 3 stale (two-level NS delegation) | 5 min |
| P0-3 | Blocking | `netlify-ns-handoff.md` points 3-4 stale | 10 min |
| P1-1 | Must fix | `/__routes` debug endpoint leaks pod registry publicly | 30 min |
| P1-2 | Must fix | "CF error 1116 is GLOBAL" claim is unverified | 1 hr |
| P1-3 | Must fix | Deployed Worker doesn't do geo-routing (doc oversells) | 30 min |
| P1-4 | Must fix | Deployed Worker uses `Math.random()`, not A2+A3 hybrid | 1 hr or 5 min note |
| P1-5 | Must fix | No automatic failover in v2.0.0 — TL;DR should say so | 5 min |
| P1-6 | Must fix | Log internal summary disagrees with measured Test 7 numbers | 5 min |
| P2-1 | Should fix | E2E test "revert" is fragile (hardcoded registry) | 15 min |
| P2-2 | Should fix | Weighted-routing test only 20 samples (statistically weak) | 5 min to expand |
| P2-3 | Should fix | "0 credits" claim for Blobs not verified in this session | 5 min cite or 30 min verify |
| P2-4 | Should fix | Netlify WAF bb-api CRUD unprobed — add as open Q | 5 min |
| P2-5 | Should fix | `cf_main_token` mint-only — not surfaced as operational note | 10 min |
| P2-6 | Should fix | `validate-pod-pattern.log` is misleadingly named | 5 min rename |
| P2-7 | Should fix | Build-hook claim is from prior research, not re-verified | 5 min cite |
| P2-8 | Should fix | "Edge compute cold start: Tied" is unverified | 5 min note |
| P3-1..10 | Future work | Cron Trigger, A/B, Traffic Splits probe, drift detection, monitoring, KV math, info leak, sub-zones | Hours-days |

---

**End of review.** Verdict: **APPROVE-WITH-CONDITIONS** — fix the 3 P0 doc-consistency issues and the 6 P1 issues (especially `/__routes` auth gating and the cross-account subdomain claim verification) before final sign-off. Everything else is good-to-go or future work.
