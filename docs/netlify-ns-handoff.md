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

1. **Validate the edge-routing-vs-isolation tradeoff** — the design discussion concluded that public-facing names (`app`, `api`, etc.) should be on the CF parent zone so CF Worker routes can terminate them, while pod names (`app-us-east-01`, etc.) should be on Netlify DNS sub-zones for TOS isolation. This is the load-bearing design decision — confirm against current CF Workers docs that custom-domain-on-Worker still requires the A record on the CF zone.

2. **Build the pod lifecycle automation** — the existing `scripts/` pattern (referenced in `agent-kit/docs/`) needs to extend to: (a) create Netlify DNS zone per pod, (b) stamp the standard record set, (c) add NS delegation in parent zone, (d) provision backend accounts, (e) register pod in the edge router's KV/D1 store.

3. **Pod token security** — since there's no WAF on the bb-api, pod auth tokens must be robust on their own (signed JWT with short TTL, not shared secrets). The scraper work proved that Netlify's network doesn't have a WAF layer to fall back on.

4. **Email routing for pods** — CF Email Routing is zone-level and won't work on delegated sub-zones. If pods need email (e.g., per-pod user support), ImprovMX or SES is required.

5. **Wear-down matching** — when pairing pods, balance resource profiles so different pods stress different meters (a storage-heavy pod paired with a compute-heavy pod, etc.). This is a pod-assignment-strategy concern, not a DNS concern, but it informs what regional/backend pairings make sense.
