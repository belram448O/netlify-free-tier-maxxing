# Final Review — Netlify Cloud Architecture Deliverable (05_NETLIFY_ARCHITECTURE.md)

**Reviewer**: opus-final-reviewer
**Date**: 2026-08-17
**Task ID**: FINAL-NETLIFY-REVIEW
**Subject**: `nftm/docs/cloud-architecture/05_NETLIFY_ARCHITECTURE.md` (310 lines)
**Method**: Read all five required source docs + live-verified DNS/HTTP claims via curl/dig (read-only; no writes).

---

## Verdict: **APPROVE-WITH-CONDITIONS**

The deliverable is accurate on every load-bearing claim, consistent with 03/04/findings-report, and free of any stale references to the corrected-out architecture. Three minor traceability gaps exist (none of them invalidate the architecture) — they are documented below as conditions, not blockers. The architecture as described in 05 is the live state of sonicloud.app as I just verified.

---

## 1. Live-state verification (re-run 2026-08-17T12:00Z)

I executed the five read-only checks listed in the task brief:

| Check | Expected | Observed | Match |
|---|---|---|---|
| `curl https://sonicloud.app/__health` | v3.0.1, pod_count=-1 | `{"ok":true,"worker":"sonicloud-root-worker","version":"3.0.1 (verbose-gate-fix)","pod_count":-1,...}` | ✅ |
| `curl -i https://sonicloud.app/app/test` | 302 → app.sonicloud.app | `HTTP/2 302, location: https://app.sonicloud.app/app/test` | ✅ |
| `curl https://app.sonicloud.app/__health` | Worker v3.0.1 | Same JSON as apex (`worker":"sonicloud-root-worker","version":"3.0.1"` — same Worker, same account) | ✅ |
| `dig NS sonicloud.app @1.1.1.1` | dns*.p02.nsone.net | `dns1-4.p02.nsone.net` | ✅ |
| `dig CNAME app.sonicloud.app @1.1.1.1` | sonicloud-root-worker.sonicloud.workers.dev | `sonicloud-root-worker.sonicloud.workers.dev.` | ✅ |

All five claims in 05 §4.1–§4.4 are reproduced exactly by the live system. Cross-checked against `live-audit-state.json` (dated 2026-08-17T11:55Z, 1161 lines): every DNS record, CF zone field, Worker Route, KV namespace, Netlify zone, and HTTP status 05 cites is present and identical. No drift.

---

## 2. Accuracy — credit cost numbers vs findings-report

I diffed 05 §2.1 (the 6-meter table) and §2.3 (effective capacity) against `findings-report.md` Part 1 / Part 10 / Part 11:

| Metric | 05 | findings-report | Match |
|---|---|---|---|
| Production deploy cost | 15 cr | 15 cr | ✅ |
| Compute rate | 10 cr/GB-hr | 10 cr/GB-hr | ✅ |
| AI inference rate | 180 cr/$1 | 180 cr/$1 | ✅ |
| Bandwidth rate | 20 cr/GB | 20 cr/GB | ✅ |
| Web requests rate | 2 cr/10K | 2 cr/10K | ✅ |
| Forms | free | free | ✅ |
| Per-invocation compute cost (1 GB × 200 ms) | ~0.0006 cr | 0.000556 cr ≈ 0.0006 | ✅ |
| Compute-only exhaustion | ~540K inv/mo | ~540K inv/mo | ✅ (exact match) |
| Web-requests-only exhaustion | ~1.5M/mo | ~1.5M max | ✅ |
| Bandwidth-only exhaustion | ~15 GB/mo | ~15 GB max | ✅ |
| Edge Function 1M inv/mo cost | 200 cr (⅔ pool) | 1M × (2/10K) = 200 cr | ✅ (math verified) |
| Grandfathered vs credit BW multiplier | 6.7× (100 GB vs 15 GB) | 6.7× | ✅ |
| Grandfathered function inv/site/mo | 125K | 125K | ✅ |
| Grandfathered edge inv/mo | 1M | 1M | ✅ |
| Grandfathered build minutes | 300/mo hard | 300 min/mo hard | ✅ |
| Sites per account (both plans) | 500 | 500 | ✅ |
| One Free team per user (API-enforced) | yes | yes (422 verified) | ✅ |

Every credit-related number in 05 traces to a dashboard-verified measurement in findings-report. The doc's own caveat (§2.1, "Important" callout) that the credit API lags 5–30+ min and the dashboard is authoritative matches findings-report Part 10's "API lags 5-30+ min" finding verbatim.

---

## 3. Consistency — 05 vs 04 vs 03

| Topic | 03 (correction) | 04 (final arch) | 05 (Netlify) | Consistent? |
|---|---|---|---|---|
| Apex NS on Netlify p02 | ✓ | ✓ | ✓ | ✅ |
| CF zone stays "active" with NS on Netlify | ✓ | ✓ | ✓ (TL;DR #10) | ✅ |
| CNAME → workers.dev works at Netlify | ✓ | ✓ | ✓ (§1.3 diagram) | ✅ |
| CF error 1014 only blocks proxied CNAMEs in CF zones | ✓ | ✓ (explicit "what I got wrong" table) | (not re-litigated — correctly) | ✅ |
| Worker v3.0.1, KV POD_REGISTRY id f5c32d0f... | ✓ | ✓ | ✓ (§4.2) | ✅ |
| Cron `*/5 * * * *` created 09:08:29Z | ✓ | ✓ | ✓ (§4.2) | ✅ |
| Pod registry routes key v4, /app/ → app.sonicloud.app 100% active | ✓ | ✓ | ✓ (§4.2) | ✅ |
| ab_config: enabled=false, salt=sonicloud-ab-salt-v1 | ✓ | ✓ | ✓ (§4.2) | ✅ |
| Grandfathered 6.7× multiplier | (not in scope) | §3.2 table | §2.4 table | ✅ (identical) |
| Allocation strategy (grandfathered → high-traffic sub-zones) | (not in scope) | §3.4 + §3.6 | §2.4 | ✅ (identical recommendation) |
| Spread rule: ≤20–30 active sites per grandfathered acct, 1 credit-based per 5–10 sites | (not in scope) | §3.7 | §2.4 "Spread rule" | ✅ (identical) |

05's scope stays Netlify-focused — when it touches CF (Worker Routes, KV, R2, Cron) it cites "see 04_FINAL_ARCHITECTURE.md" rather than re-explaining. No drift into Worker implementation. ✅ Scope discipline maintained.

---

## 4. Stale-claim sweep

Grepped 05 for every known stale phrase from the old (wrong) 02_CLOUD_ARCHITECTURE.md:

- `"apex must stay on CF"` → 0 matches in 05 (it appears in 04 only inside the explicit "what I got wrong" correction table)
- `"per-account isolation requires a separately-registered domain"` → 0 matches in 05
- `"CF error 1014 blocks CNAME → workers.dev"` → 0 matches in 05
- `"1116"` (CF error 1116) → 0 matches in 05

05 is clean. The corrected architecture (apex on Netlify, CNAME → workers.dev, CF zone sticky-active) is what 05 documents. No vestigial references to the older $10/yr-domain pattern.

---

## 5. User's constraint — "finite grandfathered accounts"

The user said: *"i do have quite a few grand-fathered netlify with better free tier usage, but those are finite resource and i have more projects / sites than these account can fit."*

05 §2.4 directly addresses this:
1. **Quotes the user's constraint verbatim.**
2. Provides a **grandfathered-vs-credit comparison table** with concrete multiplier (6.7× bandwidth).
3. Provides an **allocation strategy table** (workload type → where to host, with rationale).
4. Provides a **concrete spread rule**: "≤20–30 active sites on one grandfathered account; 1 credit-based per 5–10 sites (300 cr / 5 sites = 60 cr per site ≈ 3 GB BW/site/mo)."
5. (Cross-checked against 04 §3.7 — identical numbers, so 05 isn't making up new rules.)

This is concrete enough to act on: when the user provides their grandfathered account IDs/tokens, the allocation logic is fully specified. Pending items (§5.2 #3 and §5.2 #4) flag what's still unknown about grandfathered accounts (`type_slug` value, dashboard BW meter reality) — correctly scoped as "needs user input," not pretend-answered.

---

## 6. Conditions / minor accuracy gaps (non-blocking)

These are traceability nits, not architecture problems. They don't contradict findings-report or the live state — they just lack the citation rigor the rest of the doc has.

**C1 — WAF capability fields not in live-audit-state.json.** 05 §3.1 claims `max_rules_per_set: {included:2, used:0}`, `max_ips_per_rule: {included:3, used:0}`, `max_countries_per_rule: {included:3, used:0}` were "verified live 2026-08-17". The `live-audit-state.json` dns_account object only contains `firewall_enabled`, `traffic_rules`, `max_traffic_rules` (3 fields) — it does NOT contain the `max_rules_per_set` / `max_ips_per_rule` / `max_countries_per_rule` fields. Either these came from a different audit script (the worklog mentions `20_credit_state_audit.py` but that script's purpose was credit state, not capabilities) or they were inferred from the Netlify pricing page. Recommend 05 either (a) cite the script that captured these fields, or (b) soften the language from "verified live" to "per Netlify pricing page."

**C2 — `swar_auto_topup_credits: 400` claim.** 05 §4.3 says "Both accounts have: ... `swar_auto_topup_credits: 400`". The worklog Task 7 entry does mention this field as captured by `20_credit_state_audit.py`, but `live-audit-state.json` (the doc 05 cites as its source in §4) does NOT contain this field for either account. Same fix as C1 — either re-run the audit and dump this field into live-audit-state.json, or change the citation.

**C3 — Netlify Blobs store name.** 05 §1.1 says scraper account has `site:scraper-results` store. The findings-report Part 3 says the actual store on the scraper account is `site:hn-scrapes` (1.13 MB, 5 blobs) plus `site:test-egress` and `site:build-events`. There is no `site:scraper-results` store in any source doc I read. This looks like a conceptual rename that didn't get reconciled with the live store name. Recommend changing "site:scraper-results" → "site:hn-scrapes" or "the scraper site's Blobs store" to match findings-report Part 3's verified table.

**C4 — "Netlify Blobs read latency ~870ms (measured from HK)"** (05 §1.2). This number doesn't appear in findings-report or live-audit-state.json. Plausible (Blobs backend is S3 us-east-2; HK→Ohio RTT ~200ms × TLS + SDK overhead could land near ~870ms) but unverified by any source doc I can find. Recommend either citing the measurement script or softening to "observed high single-digit-hundreds-of-ms range from HK" without the precise ~870ms.

None of C1–C4 are architecture-affecting. All can be fixed with a 5-line edit pass.

---

## 7. What's already excellent

- **Netlify scope discipline.** 05 stays focused on what Netlify does (DNS, Blobs, build-as-compute, Functions, Edge Functions, WAF, Traffic Splits) and explicitly defers CF Worker / KV / Cron / Vercel / R2 details to 04. The §1.2 "what Netlify is NOT used for (and why)" table is exactly the right framing.
- **The "what's pending" section (§5)** is unusually honest. Items needing `_nf-auth` cookie, grandfathered IDs, or CLI deploys are clearly tagged with the specific blocker — not hand-waved.
- **TL;DR is information-dense and accurate** — 10 bullets, each independently verifiable, no marketing fluff.
- **The Edge Functions deployment limitation** (§3.3) — "API draft deploys don't pick up `netlify.toml` edge function declarations" — is a genuinely new finding that wasn't in findings-report. This is 05 contributing original research beyond the prior track.
- **Compute+page split** is framed correctly: Netlify is NOT in the request path; routing compute goes to CF Workers; Netlify's credit pool is preserved for what it's good at. This directly addresses the user's "Netlify free tier is limited" concern.

---

## 8. Recommendation

**APPROVE the deliverable for delivery to the user.** The architecture described in 05 is the live state of sonicloud.app (verified 2026-08-17T12:00Z), the credit numbers trace to dashboard-verified measurements in findings-report, and the corrected architecture from 03 is faithfully reflected.

**Apply conditions C1–C4 as a minor edit pass** before declaring the doc 100% final. These are documentation-hygiene issues, not architecture issues — a 10-minute fixup. Specifically:
1. Add a citation for the WAF capability fields (§3.1) or soften "verified live" → "per Netlify pricing page" for the three max_* fields not in live-audit-state.json.
2. Either add `swar_auto_topup_credits` to live-audit-state.json (re-run audit) or change §4.3's "Both accounts have" → "Both accounts have (per `20_credit_state_audit.py`)".
3. Change "site:scraper-results store" → "site:hn-scrapes store" in §1.1.
4. Cite or soften the "~870ms" Blobs read latency claim in §1.2.

Once C1–C4 are addressed, 05 is the authoritative Netlify-side reference and can be marked final.
