# Netlify DNS — NS Setup & Subdomain Delegation Pattern

> **Purpose:** Carry-over note for the next session. Covers only the Netlify DNS / NS specific findings — how Netlify DNS zones behave, what the per-subdomain delegation pattern looks like, what's free vs metered, and the operational gotchas. Everything here was verified hands-on during the Netlify free-tier investigation (2026-08-14 through 2026-08-17).
>
> The repo for this work: **https://github.com/belram448O/netlify-free-tier-maxxing** (GitHub) / https://gitlab.com/ansgareutychisO/netlify-free-tier-maxxing (GitLab mirror). Recursive clone gets the scraper submodule too.
>
> The clean/lean docs (skill file, findings report, dashboard-automation reference, methodology) live in `agent-kit/docs/` of that repo, and also at https://github.com/belram448O/netlify-free-tier-agent-kit.

---

## TL;DR

Netlify offers **free standalone DNS zones** that can be delegated to from any parent zone (Cloudflare, etc.). Each Netlify DNS zone gets its own NS1 anycast nameserver pool (`dns1.p0X.nsone.net` × 4, with the `p0X` pool chosen per-zone). This gives per-subdomain isolation: separate billing, separate access tokens, separate audit logs.

The pattern that emerged from the investigation:

```
Parent zone (any DNS provider)
  NS  subdomain.parent.com  → dns1.p07.nsone.net  (×4, Netlify-assigned)
  NS  subdomain.parent.com  → dns2.p07.nsone.net
  NS  subdomain.parent.com  → dns3.p07.nsone.net
  NS  subdomain.parent.com  → dns4.p07.nsone.net

Netlify DNS zone (subdomain.parent.com)
  A     subdomain.parent.com     192.0.2.1   (or whatever the pod/hosting target is)
  CNAME www.subdomain.parent.com → subdomain.parent.com
  TXT   subdomain.parent.com     v=spf1 ...
  TXT   _dmarc.subdomain.parent.com  v=DMARC1; p=quarantine; ...
  TXT   _mta-sts.subdomain.parent.com v=STSv1; ...
  CAA   subdomain.parent.com     0 issue "letsencrypt.org"
  CAA   subdomain.parent.com     0 issue "cloudflare.com"
```

---

## What Netlify DNS actually is

Netlify DNS is a **reseller of NS1** (now IBM NS1 Connect). The nameservers Netlify assigns to your zone (`dns1.p0X.nsone.net` through `dns4.p0X.nsone.net`) are NS1 anycast nameservers. Netlify's value-add is the API and dashboard — the actual DNS resolution happens on NS1's global anycast network.

Implications:
- **Free, no queries-per-second limit observed** — we hammered NS1 anycast with no throttling
- **Globally distributed** — NS1 anycast means ~5ms latency anywhere
- **API-driven** — every DNS operation scriptable via `POST /api/v1/dns_zones` (PAT-authenticated)
- **Each zone gets its own `p0X` pool** — load distribution across NS1's `p01` through `p09` pools

---

## API surface for DNS zones

The Netlify DNS zone API is part of the public REST API (`api.netlify.com/api/v1/`), PAT-authenticated. Undocumented in the public OpenAPI spec for a long time — the discovery path is documented in `agent-kit/docs/findings-report.md` in the repo.

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/v1/dns_zones` | Create a new DNS zone. Body: `{"name": "subdomain.parent.com", "account_id": "<acct>"}`. Returns `{id, name, account_slug, dns_servers: [...]}`. |
| `GET` | `/api/v1/dns_zones` | List all DNS zones on the account |
| `GET` | `/api/v1/dns_zones/{zone_id}` | Get zone details (including the 4 assigned NS1 nameservers) |
| `DELETE` | `/api/v1/dns_zones/{zone_id}` | Delete a zone (must clear records first) |
| `POST` | `/api/v1/dns_zones/{zone_id}/dns_records` | Add a record. Body: `{"type": "A", "hostname": "subdomain.parent.com", "value": "192.0.2.1"}` |
| `GET` | `/api/v1/dns_zones/{zone_id}/dns_records` | List records in the zone |
| `DELETE` | `/api/v1/dns_zones/{zone_id}/dns_records/{record_id}` | Delete a record |

### What the create-zone response looks like

```json
{
  "id": "6a80588c88797781fbe34721",
  "name": "users.example.com",
  "account_slug": "belram448O",
  "dns_servers": [
    "dns1.p07.nsone.net",
    "dns2.p07.nsone.net",
    "dns3.p07.nsone.net",
    "dns4.p07.nsone.net"
  ],
  "records": []
}
```

The `dns_servers` array is what you put in the parent zone as NS records. Netlify's dashboard surfaces these same four nameservers.

### Per-zone `p0X` pool distribution

In the tested account, 6 zones were created and they landed on different NS1 pools:

| Zone (last segment) | NS1 pool |
|---|---|
| zone 1 | `p07` |
| zone 2 | `p01` |
| zone 3 | `p07` (same as zone 1 — pool assignment is not unique per zone) |
| zone 4 | `p05` |
| zone 5 | `p09` |
| zone 6 | `p08` |

NS1 distributes load across pools; the assignment is opaque. Don't rely on a particular pool — always read `dns_servers` from the create response and put exactly those four in the parent zone.

---

## Per-zone record template (what to put on each Netlify zone)

For each subdomain zone created on Netlify, the standard record set is:

```
A      <subdomain>.<parent>        192.0.2.1        (placeholder A, or real backend IP)
CNAME  www.<subdomain>.<parent>    → <subdomain>.<parent>
TXT    <subdomain>.<parent>        v=spf1 <include policy> ~all
TXT    _dmarc.<subdomain>.<parent> v=DMARC1; p=quarantine; rua=mailto:...; ...
TXT    _mta-sts.<subdomain>.<parent> v=STSv1; id=...
CAA    <subdomain>.<parent>        0 issue "letsencrypt.org"
CAA    <subdomain>.<parent>        0 issue "cloudflare.com"
```

The A record is a placeholder until the actual hosting target is decided. Once the backend is chosen (Vercel, Supabase, another Netlify site, an IP), update the A record to point at it.

DMARC `p=quarantine` is the recommended minimum. MTA-STS records prevent SMTP downgrade attacks. CAA records restrict which cert authorities can issue certs for the subdomain (Cloudflare + Let's Encrypt only).

---

## What to put in the parent zone

In the parent DNS zone (e.g., Cloudflare, Route53, anything that supports NS records), add 4 NS records delegating the subdomain:

```
NS  <subdomain>.<parent>  dns1.p0X.nsone.net
NS  <subdomain>.<parent>  dns2.p0X.nsone.net
NS  <subdomain>.<parent>  dns3.p0X.nsone.net
NS  <subdomain>.<parent>  dns4.p0X.nsone.net
```

After propagation (minutes, not hours — NS1 anycast is fast), queries for `<subdomain>.<parent>` will be answered by NS1 using the records you put on the Netlify zone.

### Caveats

- **Cloudflare-specific gotcha:** If the parent zone is on Cloudflare, you must set the NS records to "DNS only" (no orange cloud). Cloudflare cannot proxy NS records — they must be raw DNS. If you proxy them, delegation breaks.
- **Vercel custom-domain gotcha:** CF Worker custom domains require the A record (or CNAME) to be on the CF zone — they cannot terminate a domain that lives on a delegated sub-zone. If you want a CF Worker route on `app.example.com`, the A record for `app.example.com` must live on the CF parent zone, not on a Netlify DNS sub-zone.
- **Email routing is zone-level:** Cloudflare Email Routing only works on the apex zone, not on delegated sub-zones (sub-zones are on Netlify, not CF, so CF can't see them as "its zone"). If you need email on a sub-zone, use ImprovMX (free, MX-based, web-signup-only) or AWS SES (paid, API-driven).

---

## What's free vs metered on Netlify DNS

Netlify DNS is **completely free** — including:

- **Zone creation** — no limit on number of zones per account (we tested 6, no pushback)
- **Queries** — NS1 anycast handles resolution; no per-query meter
- **Record reads/writes** — API calls are free (don't touch the 300-credit/month pool)
- **At-rest records** — no storage charge

This is separate from the 300 credits/month pool that gates deploys / compute / bandwidth / web requests. DNS operations don't consume credits.

### The one thing that DOES consume credits

If you point a Netlify DNS zone's A record at a Netlify site (i.e., the subdomain is hosted on Netlify), then:
- Production deploys on that site cost 15 credits each
- Bandwidth out of that site costs 20 credits/GB
- Function invocations cost ~0.0006 credits each (negligible)

But if the A record points at a non-Netlify target (Vercel, Supabase, an IP), Netlify DNS is just the DNS layer — **zero credit cost**.

This is the key property that makes Netlify DNS useful as a free "subdomain isolation" tool even when you don't host on Netlify: you get per-subdomain zones for free, and the actual hosting can live anywhere.

---

## Why use Netlify DNS instead of just CF or Route53

If you only need DNS resolution, Cloudflare is fine. The reason to use Netlify DNS is **per-subdomain TOS/account isolation**:

- Each subdomain can be on its own Netlify account (separate email, separate OAuth identity — required because one Free team per Netlify user is hard-enforced server-side)
- Each subdomain's audit log is separate
- If one subdomain's account gets suspended, the others are unaffected
- Each subdomain can have its own backend account (Vercel/Supabase/etc.) for free-tier multiplication

The tradeoff: you lose Cloudflare's edge features (Worker routes, Bot Fight Mode, edge caching) on those subdomains, because the A record lives on Netlify DNS, not CF.

This tradeoff is the core design tension. If edge features matter for a given name (anti-bot, geo-routing, edge compute), the A record needs to be on the CF parent zone — not on a delegated Netlify sub-zone. If TOS isolation matters more than edge features (background pods, isolated user shards), use the Netlify DNS sub-zone pattern.

---

## Operational patterns

### Adding a new subdomain

```bash
# 1. Create the zone on Netlify (via PAT-authenticated API)
curl -X POST "https://api.netlify.com/api/v1/dns_zones" \
  -H "Authorization: Bearer $NETLIFY_PAT" \
  -H "Content-Type: application/json" \
  -d '{"name":"app-us-east-01.example.com","account_id":"<acct>"}'
# Response includes the 4 NS1 nameservers — capture them

# 2. Stamp the standard records on the new zone (A, CNAME www, TXT spf/dmarc/mta-sts, CAA ×2)
# (loop POSTing each record to /dns_zones/{zone_id}/dns_records)

# 3. Add NS delegation in the parent zone
# For Cloudflare parent, use the API or dashboard:
#   NS app-us-east-01.example.com dns1.p0X.nsone.net
#   NS app-us-east-01.example.com dns2.p0X.nsone.net
#   NS app-us-east-01.example.com dns3.p0X.nsone.net
#   NS app-us-east-01.example.com dns4.p0X.nsone.net
# Set to "DNS only" (no proxy)
```

### Killing a compromised subdomain

```bash
# 1. Delete the Netlify DNS zone (clears all records)
curl -X DELETE "https://api.netlify.com/api/v1/dns_zones/{zone_id}" \
  -H "Authorization: Bearer $NETLIFY_PAT"

# 2. Remove the 4 NS records from the parent zone

# 3. Rotate any tokens that were on that subdomain's account
# 4. Audit logs
```

### Token rotation

Netlify PATs can't be rotated via API — must be done in the dashboard. The bb-api (`app.netlify.com/access-control/bb-api/`) uses session cookies (`_nf-auth` JWT specifically; `connect.sid` is NOT needed) and these also expire — re-extract from browser DevTools when the tool starts returning 401.

The bb-api has NO WAF — plain curl with just the `_nf-auth` cookie works for both GET and PUT. This is documented in `agent-kit/docs/dashboard-automation.md`.

---

## The "free maxxing" framing

Netlify DNS plays one specific role in the broader free-tier-maxxing pattern: it's the **per-subdomain isolation layer**. The complete pattern (covered in `agent-kit/docs/agent-skill.md`):

1. **DNS isolation** — Netlify DNS zone per pod (free, unmetered, NS1-backed)
2. **Account isolation** — each pod's hosting on its own Free-tier account (separate email/OAuth per Netlify user, since one Free team per user is enforced)
3. **Free egress channels** — three paths that bypass the 300-credit meter: Blobs API (no size limit, free reads/writes), function logs (24h retention, 4KB/call), HTTP response (small JSON, metered but tiny)
4. **Build-as-compute** — the Netlify build process is 15 min of free compute per preview deploy (2 vCPU, 4GB RAM, 9GB disk, outbound HTTP, can run headless Chrome via `@sparticuz/chromium`)

DNS is the connective tissue. The actual hosting can be anywhere — Vercel (regional), Supabase (regional DB), Neon (regional DB), or even Netlify Functions (us-east-2 only). Netlify DNS doesn't care where the A record points.

---

## Known limits and gotchas

1. **One Free team per Netlify user** — hard API-enforced. `POST /accounts` with Free `type_id` returns `422 "Account plan is unavailable to this user"` if you already own one. To multiply Free quotas, you need N distinct Netlify users (separate email + GitHub OAuth each).
2. **Credit meter lag** — API-reported usage lags the dashboard by 5-30+ minutes. Trust the dashboard for real-time state.
3. **No WAF on bb-api** — `_nf-auth` cookie alone (no `connect.sid`, no User-Agent, no Origin) is sufficient for both GET and PUT operations on the internal dashboard API. This is convenient for automation but also means there's no protection layer to fall back on — token hygiene matters.
4. **API deploys don't trigger builds** — `POST /sites/{id}/deploys` with `draft:true` creates a preview deploy (0 credits) but does NOT run the build process. To trigger the build plugin (which is where the heavy compute happens), use `netlify deploy` CLI or Git push.
5. **Preview deploys = 0 credits, production deploys = 15 credits** — always use `netlify deploy` without `--prod` for free compute runs.
6. **Netlify Blobs = unmetered storage** — at-rest storage is free, API read/write traffic doesn't touch the bandwidth meter. Verified with 12+ MB transfers through the API.

---

## What's in the repo

The maxxing repo contains the full audit trail. Key files for Netlify DNS / NS context:

- `agent-kit/docs/findings-report.md` — comprehensive findings (973 lines), including the DNS zone discovery
- `agent-kit/docs/dashboard-automation.md` — bb-api endpoint reference (the internal API used for SSO toggling, real-time bandwidth, observability — useful for operating DNS zones that host Netlify sites)
- `agent-kit/docs/agent-skill.md` — dev-session-ready skill file (1345 lines) — load at the start of any session building on these patterns
- `agent-kit/docs/free-tier-investigation-methodology.md` — generalized methodology for investigating any cloud platform's free tier
- `agent-kit/netlify-project/tools/samples/` — 32 sample API response JSONs (saved shapes from real bb-api calls, including `GET_dns_zones.json`)
- `agent-kit/netlify-project/tools/netlify-dashboard-api.mjs` — CLI wrapping the bb-api and DNS operations
- `netlify-probe/` — code that probes the Netlify build container (verifies region, OS, CPU, disk, egress IP)
- `netlify-log-probe/` — code that validates using Netlify function logs as a free egress channel
- `upload/app.netlify.visibility*.har` — raw HAR captures from the dashboard (evidence for the bb-api findings)
- `tool-results/*.txt` — saved grep/read outputs from the investigation

The scraper submodule (`netlify-free-scraper/`) is its own repo at https://github.com/belram448O/netlify-free-scraper — it's a production HTTP scraper built on the free-tier patterns above. Reference implementation, not directly DNS-related.

---

## Next session — what to figure out

If the next session is continuing work on the broader architecture (pod fleet, edge routing, regional sharding):

**First read `docs/cloud-architecture/01_GROUND_TRUTH.md` and `docs/cloud-architecture/02_CLOUD_ARCHITECTURE.md`** — these are the cloud-architecture deep-dive that picks up from this handoff. They consolidate the Netlify DNS findings here, plus the broader CF + Vercel + R2 + D1 + KV picture, plus live probes of sonicloud.app (2026-08-17).

Key conclusions from the cloud-architecture deep dive (full rationale in `02_CLOUD_ARCHITECTURE.md`):

1. **Apex stays on Cloudflare** — FLEET.md's planned pivot to Netlify apex was the right call when CF Section 2.8 ("non-HTML ban") was a risk; that ban was REMOVED in the 2026 TOS, so the case for moving apex is now materially weaker. CF at apex gives KV/D1/R2 native, no bandwidth meter, 100K req/day Worker free, IATA-code geo (`request.cf.colo`) which is strictly finer than Netlify's `context.geo.subdivision.code`, more WAF rules per zone, and native Email Routing. The TOS-safety argument is moot in 2026.

2. **Netlify Free has a WAF after all** — the prior research missed this. Account capabilities show `firewall_enabled: true, traffic_rules: true, max_traffic_rules: 2, max_rules_per_set: 2, max_ips_per_rule: 3, max_countries_per_rule: 3` on Free. The public REST API returns 404 for rule CRUD endpoints; configuration is bb-api-only (cookie-auth). CF Free WAF is still more generous (1 managed + 5 custom per zone), but "Netlify has no WAF" was wrong.

3. **The pod fleet uses Worker Routes on the apex zone** (NOT two-level NS delegation — that pattern was BLOCKED live on 2026-08-17 by CF error 1116, which rejects subdomain zone creation when the parent is already a CF zone; same-account case verified live, cross-account case on Free tier is NOT verified — see `02_CLOUD_ARCHITECTURE.md` §9 open question #9). Per-pod Workers (e.g., `app-test-01-worker`) are deployed to CF MAIN account, each bound via Worker Routes to a hostname like `app-test-01.sonicloud.app` with A record `192.0.2.1` proxied=true (canonical CF pattern). This gives per-Worker isolation (own KV/D1/R2 bindings, own logs). Per-ACCOUNT isolation requires either (a) a separately-registered domain (e.g., sonicloud-pods.com) — ~$10/yr, OR (b) cross-account subdomain setup IF CF Free tier allows it (unverified — see `02_CLOUD_ARCHITECTURE.md` §9 open question #9). Validated live 2026-08-17.

4. **The edge router is a CF Worker at the apex** (`sonicloud-root-worker` v2.1.0, deployed live 2026-08-17), bound via Worker Routes to `sonicloud.app/*`. It reads a pod registry from CF KV namespace `POD_REGISTRY` (id `f5c32d0fdd9f4b18b3c508969224f239`) on each `/app/*` request, picks a pod via weighted random, and 302-redirects. Debug endpoints `/__routes` and `/__health` (verbose fields) are gated by `x-admin-token` header. **Caveats**: NO automatic failover (Phase 3), NO geo-routing (Phase 3, but `request.cf.colo` IS read for `/__health` telemetry), NO A/B stickiness (Phase 4). Live-measured latency: KV read adds ~20ms (44ms → 65ms); 302 hop adds ~40ms over pod direct (40ms → 85ms).

5. **Netlify free tier is best used for DNS + Blobs + build-as-compute, NOT for routing compute.** Netlify Edge Functions are billed as web requests (2 credits / 10K), so 1M invocations/month = 200 credits = ⅔ of the Free pool — tight. CF Workers (100K req/day = 3M/month, free, dedicated per account) is strictly more cost-effective for routing. Live test of Netlify Blobs as a pod registry: ~860-900 ms per read (vs CF KV's ~1 ms warm) — too slow for hot path; use Blobs for cold storage only.

6. **Netlify Traffic Splits API exists** but the public REST API returns 404; the bb-api sample returns `[]` (empty). Exact configuration syntax (branch-based? percentage-based?) requires a bb-api probe with `_nf-auth` cookie access — needs a follow-up session with browser access.

7. **Grandfathered Netlify accounts (pre-Sep-2025) are ~6.7× more bandwidth-capable** than credit-based Free (~100 GB vs ~15 GB max). The user's "quite a few grandfathered accounts" should be allocated to high-traffic sub-zones (app, users, api) — NOT to DNS (free regardless) or low-traffic static sites. Allocation strategy in `02_CLOUD_ARCHITECTURE.md` §6.

8. **Original 5 next-session topics from this handoff** — all addressed in the cloud-architecture deep dive:
   1. **Edge-routing-vs-isolation tradeoff** — confirmed: Worker Custom Domain on CF requires the A record on the CF zone, cannot terminate a Worker route on a delegated Netlify sub-zone. Per-pod NS delegation to a CF pod-zone is the cleaner pattern (`02_CLOUD_ARCHITECTURE.md` §2).
   2. **Pod lifecycle automation** — `scripts/30_provision_pod.py` (currently a stub) needs to implement the full flow described in `02_CLOUD_ARCHITECTURE.md` §2.5.
   3. **Pod token security** — confirmed: no WAF on bb-api, pod auth tokens must be signed JWTs with short TTL. Per-pod CF account scoping limits blast radius (`02_CLOUD_ARCHITECTURE.md` §2.2).
   4. **Email routing for pods** — CF Email Routing is zone-level, won't work on delegated sub-zones. ImprovMX (free, 25 aliases, 500 emails/day) or AWS SES ($0.10/1K) required for sub-zone email (`02_CLOUD_ARCHITECTURE.md` §7.3).
   5. **Wear-down matching** — pair storage-heavy pods (Blobs-heavy) with compute-heavy pods (D1/KV-heavy) to balance meters. Grandfathered accounts allocated by bandwidth need (`02_CLOUD_ARCHITECTURE.md` §6).

The `02_CLOUD_ARCHITECTURE.md` doc has a phased implementation plan (§8) and a list of open questions for the next session (§9). Start there.
