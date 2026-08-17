# WAVE-2 Verification Review — sonicloud.app Cloud Architecture (Opus-tier)

> **Reviewer**: opus-peer-reviewer-wave2 (sub-agent)
> **Date**: 2026-08-17 (post P0/P1 fix wave)
> **Scope**: Verify that the P0 + P1 fixes applied after WAVE-1 review are correct, complete, and introduce no new inconsistencies. Surface any new issues.
> **Materials reviewed**: WAVE-1 peer review (full), worklog, `02_CLOUD_ARCHITECTURE.md` (full re-read), `README.md`, `../netlify-ns-handoff.md`, `01_GROUND_TRUTH.md` §C1, `15_gate_routes_endpoint.py`, `gate-routes-endpoint.log`, `scripts/14_e2e_validation.py`, plus live re-verification of the deployed Worker v2.1.0 endpoints.

---

## Overall Verdict: **APPROVE-WITH-CONDITIONS**

All 3 P0 fixes and all 6 P1 fixes are functionally correct and verified live. The pod-routing architecture works, `/__routes` is now 401 without token, `pod_count` redacts to `-1` without token, and the docs no longer claim "two-level NS delegation" as the pod fleet pattern.

However, the Wave 2 fix wave introduced **a fresh layer of staleness** in `02_CLOUD_ARCHITECTURE.md`: the document was only *partially* updated to v2.1.0 (STATUS callouts in §3.1 and §4.2 were rewritten, but the TL;DR, §1.2 diagram, §1.4 verified list, and §10 Summary point 5 still say "v2.0.0"). Several places also still describe `/__routes` as publicly readable — directly contradicting the new §3.1 STATUS callout. The Wave 1 P0 was about stale "two-level NS" claims; the Wave 2 partial update created a parallel stale-v2.0.0 problem.

A cross-doc inconsistency also exists: `netlify-ns-handoff.md` point 3 still asserts as fact that CF error 1116 fires "even in a different CF account" — while `02_CLOUD_ARCHITECTURE.md` §2.0 now explicitly defers this as unverified. The handoff doc must be brought in line.

Recommend: ~20-minute doc-consistency pass to converge on v2.1.0 and remove stale `/__routes` claims, then APPROVE for final delivery to the user.

---

## P0 fix verification (3/3 verified)

| ID | Description | Status |
|---|---|---|
| **P0-1** | `02_CLOUD_ARCHITECTURE.md` §10 Summary point 4 — was "Pod fleet uses two-level NS delegation"; now says "Worker Routes on the apex zone". | ✅ Verified (line 678). Reads correctly, no stale two-level claim. |
| **P0-2** | `README.md` point 3 (line 25) — same fix as P0-1. | ✅ Verified. Also expanded to include the cross-account-Free-tier "unverified" caveat + open question #9 reference. |
| **P0-3** | `netlify-ns-handoff.md` points 3-4 — same fix + apex Worker now described as v2.1.0 with admin-token-gated debug endpoints live. | ✅ Verified on the "Worker Routes" + "v2.1.0 admin-token-gated" claims. **⚠ Partially broken on the cross-account claim** — see new issue N1 below. |

## P1 fix verification (6/6 verified)

| ID | Description | Status |
|---|---|---|
| **P1-1** | `/__routes` admin-token gate (Worker v2.1.0) | ✅ Verified both in script (`15_gate_routes_endpoint.py` lines 122-164) and live. Live curl results just now: (1) `/__health` no-token → `version:"2.1.0 (admin-token-gated debug)"`, `pod_count:-1`, `routes_loaded:false`; (2) `/__health` with-token → `pod_count:2`, `routes_loaded:true`; (3) `/__routes` no-token → HTTP 401 "Unauthorized"; (4) `/__routes` with-token → 200 + full JSON; (5) `/app/test` no-token → 302 to `app-test-01.sonicloud.app/app/test` (routing unchanged). All 5 expected behaviors confirmed. **Note**: `gate-routes-endpoint.log` Test 3 captured a v2.0.0 response during CF's ~30s deploy-propagation window (the script's 10s `time.sleep` was insufficient) — but live state now is correct v2.1.0. Side-effect: WAVE-1 P3-9 (`/__health` leaks pod_count) is also closed by this fix. |
| **P1-2** | "CF error 1116 is GLOBAL" claim weakened | ✅ Verified in `02_CLOUD_ARCHITECTURE.md` §2.0 (line 125): now reads "The check fires for same-account sub-zones (verified live). Whether the check fires for cross-account sub-zones on Free tier is NOT verified" + points to §9 open question #9 (line 668). §9 #9 itself is well-written and explicitly flags this as "the load-bearing unverified claim of the architecture — high priority." **⚠ Cross-doc inconsistency** with `netlify-ns-handoff.md` — see N1 below. |
| **P1-3** | Geo-routing caveat in §3.1 | ✅ Verified (line 251). STATUS callout reads: "STATUS (v2.1.0, live 2026-08-17): The deployed apex Worker reads `request.cf.colo` only for `/__health` telemetry. The `pickPod` function uses weighted random only — `colo` is ignored for pod selection. Geo-routing is Phase 3 (§8). The pseudocode below describes the TARGET design, not the live behavior." Prominent enough — bolded, dated, versioned. |
| **P1-4** | A/B stickiness caveat in §4.2 | ✅ Verified (lines 327-329). Heading is now "Recommended pattern: A2 + A3 hybrid (TARGET — not yet implemented in v2.1.0)". STATUS callout: "The deployed apex Worker v2.1.0 uses `Math.random()` for pod selection — NO cookie, NO hash. A/B stickiness is NOT implemented." Clear and operator-won't-be-misled. |
| **P1-5** | Failover caveat in TL;DR point 3 | ✅ Verified (line 17). TL;DR point 3 caveat (a): "NO automatic failover — Phase 3 Cron Trigger not yet implemented; pod outage = visible user errors." Clear, prominent, in the right place. |
| **P1-6** | Latency log summary in `14_e2e_validation.py` | ✅ Verified in the script (lines 224-228): now reads `44/65/85/38 ms`, matching the Test 7 measured numbers cited in WAVE-1. **⚠ Log file `e2e-validation.log` was NOT regenerated** — its embedded summary still shows the old `40/45/65/40 ms` numbers and the old `v2.0.0` worker line. See N5 below. |

## P2 fix verification (spot check)

| ID | Description | Status |
|---|---|---|
| **P2-5** | `cf_main_token` mint-only callout in `01_GROUND_TRUTH.md` §C1 | ✅ Verified (line 213). Operational-gotcha block added with explicit "mint-only" label, lists missing permissions (`Account Settings:Read`, `Zone:Read`), and explains the chicken-and-egg of needing a dashboard-minted scoped token to bootstrap. Good operator guidance. |

---

## New issues found in Wave 2

### N1 (P1) — Cross-doc inconsistency on cross-account CF error 1116 claim

`netlify-ns-handoff.md` point 3 (line 278) was updated to mention Worker Routes, but still asserts as **fact** that CF error 1116 fires "**even in a different CF account, per CF's docs on Enterprise-only Subdomain Support**". This directly contradicts the P1-2 fix in `02_CLOUD_ARCHITECTURE.md` §2.0 which explicitly defers the cross-account case as "**NOT verified**". Per the README reading order, an operator reads `netlify-ns-handoff.md` first — they'll inherit the wrong mental model that per-account isolation via cross-account subdomain is impossible on Free, when in fact it's merely unverified.

**Fix**: change `netlify-ns-handoff.md` point 3 to match `02_CLOUD_ARCHITECTURE.md` §2.0 wording — strike "even in a different CF account, per CF's docs on Enterprise-only Subdomain Support" and replace with "(same-account case verified live; cross-account case on Free tier is NOT verified — see `02_CLOUD_ARCHITECTURE.md` §9 open question #9)".

### N2 (P1) — `02_CLOUD_ARCHITECTURE.md` still references v2.0.0 in 6 places

The doc was partially updated: §3.1 and §4.2 STATUS callouts correctly say "v2.1.0", but other live-state references still say v2.0.0:

1. **TL;DR point 3** (line 17): "`sonicloud-root-worker` v2.0.0, deployed live 2026-08-17" + "(Caveats (v2.0.0))".
2. **§1.2 diagram** (line 138): "Apex Worker: sonicloud-root-worker (CF MAIN, v2.0.0 — KV-backed router)".
3. **§1.4 verified list** (line 184): "`curl https://sonicloud.app/__health` → 200 JSON with `pod_count: 2`, `version: 2.0.0`, `region: HKG`".
4. **§1.4 deployed list** (line 178): "Apex Worker `sonicloud-root-worker` v2.0.0 (KV-backed router)".
5. **§10 Summary point 5** (line 679): "currently v2.0.0 deployed with weighted-random pod selection".
6. **§1.4 verified line 185**: "curl https://sonicloud.app/__routes → 200 JSON with the full pod registry" — **this is now factually FALSE** (returns 401 without admin token; needs `x-admin-token` header).

Live Worker returns `version: "2.1.0 (admin-token-gated debug)"` (just verified). An operator running the §1.4 documented `curl` will get a 401 and conclude the Worker is broken. **At minimum**, lines 17, 178, 184, and 679 should say v2.1.0; line 185 should be updated to show the admin-token header.

### N3 (P1) — TL;DR point 3 caveat (d) still says "/__routes ... P1 security issue, fix pending"

Line 17 caveat (d): "`/__routes` debug endpoint is publicly readable and leaks the pod registry — P1 security issue, fix pending." This is stale — the P1-1 fix is DONE and live. Should be updated to: "`/__routes` requires `x-admin-token` header (gated v2.1.0); `/__health` `pod_count` field also requires admin token (returns -1 otherwise)."

### N4 (P2) — §10 Summary point 9 still says "next concrete change is gating /__routes"

Line 683: "The next concrete change is gating `/__routes` behind an admin token (P1 security fix), then implementing Phase 3 (geo-routing + health-check Cron Trigger) when a second real pod is added." The "next concrete change" framing is wrong — admin token gating is done. Should read: "Phase 1 (KV + admin-token gate) is DONE as of 2026-08-17. Next concrete change: Phase 3 (geo-routing + health-check Cron Trigger) once a second real pod is added."

### N5 (P2) — §9 Open question #10 still lists `/__routes` admin-token gate as open

Line 669 lists as open question #10: "Gate `/__routes` behind an admin token (P1 security fix from opus peer review WAVE-1)..." — but this is now DONE. Should be marked `✅ DONE 2026-08-17 (Worker v2.1.0 deployed via 15_gate_routes_endpoint.py)` or removed entirely.

### N6 (P2) — `14_e2e_validation.py` script will silently break on v2.1.0 worker

The P1-6 fix updated the latency summary correctly (lines 224-228). But two other parts of the script were not updated for v2.1.0:

- **Line 70**: `if body.get("version", "").startswith("2.0")` — the version field is now `"2.1.0 (admin-token-gated debug)"`, which does NOT `.startswith("2.0")`. Test 1 will silently fail to print the "✓ v2 Worker is live" success message.
- **Lines 81-82 (Test 2)**: `curl("https://sonicloud.app/__routes")` without `x-admin-token` header — will now return 401, breaking Test 2's expected 200 + JSON output.
- **Lines 147-149 (Test 8 verify)**: same issue — reads `/__routes` without admin token, will get 401 instead of the expected JSON.

Re-running `14_e2e_validation.py` against the live v2.1.0 worker will produce a confusing report. Fix: add `-H f"x-admin-token: {ADMIN_TOKEN}"` to the curl calls in Test 2 and Test 8, and change line 70's check to `.startswith("2.")` (or accept both "2.0" and "2.1").

### N7 (P3) — `e2e-validation.log` was not regenerated after script update

The log file's embedded summary block (final ~30 lines) still shows the OLD numbers (`40/45/65/40 ms`) and `v2.0.0` worker. The P1-6 fix updated the *script*, not the *log*. Future operator diffing the script against the log will see a mismatch. Fix: re-run `14_e2e_validation.py` (after fixing N6) and overwrite the log.

---

## Wave 1 P2 items — escalation review

Reviewed all 8 Wave 1 P2 items for possible escalation to P1. **None warrant escalation**:

- P2-1 (fragile revert) — operational fragility, not user-facing risk.
- P2-2 (20 samples for weighted routing) — statistically weak but the test is a one-off validation, not a production path.
- P2-3 (Blobs 0-credits claim) — verified via 12 MB transfer; full before/after credit comparison would be nice-to-have, not blocking.
- P2-4 (Netlify WAF bb-api unprobed) — the WAF capability is documented as a finding; CRUD probe is Phase 2 work.
- P2-5 (cf_main_token mint-only) — DONE in Wave 2.
- P2-6 (`validate-pod-pattern.log` misleadingly named) — annoyance, not a wrongness.
- P2-7 (build-hook claim) — pulled from prior research, accuracy unverified but plausible.
- P2-8 (cold-start claim) — unverified, but the recommendation is "Tied" which is the conservative default.

**Bonus**: Wave 1 P3-9 ("/__health also leaks pod_count") is now **DONE** as a side-effect of the P1-1 fix (verified live: `pod_count: -1` without token). Worth noting in the changelog.

---

## Final verdict + sign-off

**APPROVE-WITH-CONDITIONS**.

The architecture is sound and the Wave 1 P0/P1 fixes are functionally correct and live-verified. The blocking Wave 1 issues (stale "two-level NS" + `/__routes` public leak + GLOBAL claim + un-caveated Phase 3/4 features + wrong latency log) are all properly closed.

However, the fix wave introduced a fresh layer of staleness — primarily in `02_CLOUD_ARCHITECTURE.md` where the v2.0.0 references and the "publicly readable /__routes" claim were not swept, and in `netlify-ns-handoff.md` where the cross-account 1116 claim was not aligned with the P1-2 weakening in `02_CLOUD_ARCHITECTURE.md`. These will confuse the next operator and cause them to mis-test the live Worker.

**Required before final APPROVE** (estimated 25 minutes total):

1. **N1 (P1)** — Update `netlify-ns-handoff.md` point 3 to defer cross-account 1116 case as "unverified", matching `02_CLOUD_ARCHITECTURE.md` §2.0. *(5 min)*
2. **N2 (P1)** — Update `02_CLOUD_ARCHITECTURE.md` v2.0.0 references → v2.1.0 at lines 17, 138, 178, 184, 679. Update line 185 to show the `x-admin-token` header. *(10 min)*
3. **N3 (P1)** — Update `02_CLOUD_ARCHITECTURE.md` TL;DR point 3 caveat (d) — remove "fix pending", note the gate is live. *(2 min)*
4. **N4 (P2, recommended)** — Update `02_CLOUD_ARCHITECTURE.md` §10 Summary point 9 — re-frame as "Phase 1 DONE, next is Phase 3". *(2 min)*
5. **N5 (P2, recommended)** — Mark `02_CLOUD_ARCHITECTURE.md` §9 open question #10 as DONE. *(2 min)*
6. **N6 (P2, recommended)** — Update `14_e2e_validation.py` Test 1 version check + Tests 2/8 curl calls to pass `x-admin-token`. *(5 min)*

Items 1-3 are blocking for sign-off. Items 4-6 can be done in the same pass or deferred to a Wave 3 follow-up.

Once items 1-3 land, this is ready for final delivery to the user.
