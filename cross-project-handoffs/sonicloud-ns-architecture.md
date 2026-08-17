# sonicloud.app — NS / DNS Architecture & Design Discussion

**Captured:** 2026-08-17
**Purpose:** Handoff to a new session. Captures (a) the live state as of 2026-08-15 and (b) the design discussion that occurred immediately after about how the NS layer should evolve for regional sharding. None of this is in the existing repo docs (`findings-report.md`, `dashboard-automation.md`, `agent-skill.md`, `free-tier-investigation-methodology.md`) — those are all Netlify-scraper-specific.

> **Note on scope:** The scraper investigation is a separate project. sonicloud.app is a separate project. The scraper findings (Blobs, build-as-compute, bb-api) are NOT in scope here. This doc is strictly about DNS/NS topology for sonicloud.app.

---

## Part 1 — Live State (as of 2026-08-15T19:05 UTC)

### Subdomain inventory

| Subdomain | DNS zone location | Hosting | Status |
|---|---|---|---|
| `sonicloud.app` | CF MAIN | CF Worker | ✅ HTTP 200 |
| `www.sonicloud.app` | CF MAIN | CF Worker (same as apex) | ✅ HTTP 200 |
| `docs.sonicloud.app` | CF MAIN | Vercel SSR (Next.js 15) | ✅ HTTP 200 |
| `blog.sonicloud.app` | CF MAIN | Vercel SSR (Next.js 15) | ✅ HTTP 200 |
| `help.sonicloud.app` | CF MAIN | CNAME placeholder | ⚠️ Awaiting vendor hostname |
| `users.sonicloud.app` | Netlify DNS zone | A record placeholder | ✅ DNS resolves, hosting TBD |
| `app.sonicloud.app` | Netlify DNS zone | A record placeholder | ✅ DNS resolves, hosting TBD |
| `content.sonicloud.app` | Netlify DNS zone | A record placeholder | ✅ DNS resolves, hosting TBD |
| `corp.sonicloud.app` | Netlify DNS zone | A record placeholder | ✅ DNS resolves, hosting TBD |
| `api.sonicloud.app` | Netlify DNS zone | A record placeholder | ✅ DNS resolves, hosting TBD |
| `cdn.sonicloud.app` | Netlify DNS zone | A record placeholder | ✅ DNS resolves, hosting TBD |

### Registrar + apex zone

```
Spaceship (registrar)
  sonicloud.app
  NS → giancarlo/hazel.ns.cloudflare.com  (CF MAIN account)
```

### CF MAIN account — DNS records (28 total)

```
A       sonicloud.app       192.0.2.1                    ⚡ Worker-routed
CNAME   www                 → sonicloud.app
CNAME   docs                → cname.vercel-dns.com (DNS-only)
CNAME   blog                → cname.vercel-dns.com (DNS-only)
CNAME   help                → helpdesk.example.com (placeholder)

# Per-subdomain NS delegations to Netlify DNS (each subdomain gets its own NS1 pool)
NS      users               → dns1-4.p07.nsone.net  (×4)
NS      app                 → dns1-4.p01.nsone.net  (×4)
NS      content             → dns1-4.p07.nsone.net  (×4)
NS      corp                → dns1-4.p05.nsone.net  (×4)
NS      api                 → dns1-4.p09.nsone.net  (×4)
NS      cdn                 → dns1-4.p08.nsone.net  (×4)

# Email security on root
TXT     sonicloud.app       v=spf1 include:_spf.mx.cloudflare.net ~all
TXT     _dmarc.sonicloud.app    v=DMARC1; p=quarantine; ...
TXT     _dmarc.users.sonicloud.app   (×6 — one per delegated sub)
TXT     _mta-sts.sonicloud.app   v=STSv1; ...

# Cert authority lockdown on root
CAA     sonicloud.app       0 issue "cloudflare.com"
CAA     sonicloud.app       0 issue "letsencrypt.org"
CAA     sonicloud.app       0 iodef "mailto:admin@..."
```

### CF MAIN account — services enabled

- **Worker:** `sonicloud-root-worker`
  - Routes: `sonicloud.app/*` and `www.sonicloud.app/*`
  - Endpoint: `GET /__health → JSON {ok, service, worker, ts, region}`
- **Email Routing:** enabled (status: ready)
  - MX: `route1/2/3.mx.cloudflare.net`
  - Catch-all: `*@sonicloud.app → admin@sonicloud.app`
  - ⚠️ Destination verification PENDING — Cloudflare sent a verification email; user must click the link in the inbox. Link has session-bound CSRF tokens, cannot be auto-clicked.
- **2FA enforcement:** ✅ enabled (`enforce_twofactor: true`)

### Netlify DNS zones — 6 zones (Free tier)

Each zone has identical 7-record pattern (A + CNAME www + TXT spf + TXT dmarc + TXT mta-sts + CAA ×2).

| Zone | Netlify zone ID | NS1 pool |
|---|---|---|
| `users.sonicloud.app` | `6a80588c88797781fbe34721` | p07 |
| `app.sonicloud.app` | `6a80b780a694d25f2145f208` | p01 |
| `content.sonicloud.app` | `6a80b780a694d25f2145f208` | p07 (shared ID with app — note this) |
| `corp.sonicloud.app` | `6a80b796139f47814f78bf0a` | p05 |
| `api.sonicloud.app` | `6a80b7ab139f4780ad78bf2a` | p09 |
| `cdn.sonicloud.app` | `6a80b7c0ee37ee2d4890c59e` | p08 |

**Per-zone record template:**
```
A      sub.sonicloud.app        192.0.2.1     (placeholder A, points at nothing real yet)
CNAME  www.sub.sonicloud.app    → sub.sonicloud.app
TXT    sub.sonicloud.app        v=spf1 ...
TXT    _dmarc.sub.sonicloud.app v=DMARC1;...
TXT    _mta-sts.sub.sonicloud.app v=STSv1;...
CAA    sub.sonicloud.app        0 issue "letsencrypt.org"
CAA    sub.sonicloud.app        0 issue "cloudflare.com"
```

### Vercel projects (Hobby tier)

| Project | ID | Repo | Domain |
|---|---|---|---|
| `sonicloud-docs` | `prj_cRlHohrW9b1A0WbX2AvnKOYCCjpX` | `admin-sonicloud/sonicloud-docs` (Next.js 15) | `docs.sonicloud.app` (verified) |
| `sonicloud-blog` | `prj_6omcRp6pGWfae0RLeuj6aCWJyAJB` | `admin-sonicloud/sonicloud-blog` (Next.js 15) | `blog.sonicloud.app` (verified) |

Both projects: latest deployment = READY.

### Source-of-truth repo

`https://github.com/zulfikarbarbora-outl/sonicloud-infra` (private). All infra managed by Python scripts under `scripts/`, state in `infra/state.json`, secrets in `infra/secrets.json` (mode 0600, gitignored).

> **NOTE for new session:** The sonicloud-infra repo is NOT checked out in the current workspace. The workspace `/home/z/my-project/` contains only the unrelated Netlify scraper work. The new session will need to `git clone https://github.com/zulfikarbarbora-outl/sonicloud-infra` to access the actual scripts.

---

## Part 2 — Key Architectural Decisions (Current State)

These are the decisions already baked into the live deployment above. They are documented here so any future changes know what they're changing FROM.

### D1. DNS provider = Cloudflare at root (CF MAIN account)
- Free tier Workers, Email Routing, KV, R2, D1, Pages all available
- API-first management
- Spaceship NS migrated programmatically via real API at `https://spaceship.dev/api`

### D2. Per-subdomain isolation via Netlify DNS zones (NOT CF subdomain zones)
- CF subdomain zones require Enterprise tier + "Subdomain Support" entitlement (gated, not available on free)
- Netlify has first-class standalone subdomain DNS zones via `POST /api/v1/dns_zones` (free)
- Each subdomain gets its own NS1 nameservers (different `p0X` pools for load distribution)
- Goal: separate billing, separate access tokens, separate audit logs per subdomain

### D3. Hosting decisions per subdomain (current)

| Pattern | Subdomains | Why |
|---|---|---|
| CF Worker (Worker Routes) | `sonicloud.app`, `www.*` | Native CF integration, free, no SSL issues |
| Vercel SSR (Next.js 15) | `docs.*`, `blog.*` | SSR requirement; Vercel native |
| CNAME placeholder | `help.*` | Awaiting vendor selection |
| A record placeholder | `app/users/content/corp/api/cdn.*` | Hosting TBD — point A record at the chosen target when ready |

### D4. Email Routing — split between CF + ImprovMX
- **CF Email Routing:** `sonicloud.app` root only (CF Email Routing is zone-level, doesn't work on delegated sub-zones)
- **ImprovMX (optional):** `users.*`, `corp.*` if email is wanted there. Free 25 aliases, MX-based, no signup API (web-only)
- Alternatives: AWS SES (paid), or skip email on subdomains (current state)

### D5. Security
- 2FA enforcement on CF MAIN: ✅ enabled
- API tokens: account-scoped (not user-scoped) — limited blast radius if leaked
- DMARC `p=quarantine` on all zones (root + each sub-zone)
- MTA-STS records on all zones
- CAA records restrict cert issuance to Cloudflare + Let's Encrypt
- SPF includes (CF for root, ImprovMX for users/corp if enabled)

### D6. CI/CD
- All infra managed by Python scripts under `scripts/`
- Idempotent: safe to re-run any script
- State persisted in `infra/state.json`
- Secrets in `infra/secrets.json` (mode 0600, gitignored)
- All scripts + docs archived to private GH repo

---

## Part 3 — Automation Status (current)

### ✅ Fully automated (one-time, already done)
- NS migration at Spaceship → Cloudflare (`scripts/18_migrate_ns_spaceship.py`)
- CF root zone creation + DNS records + Worker + Email Routing (`scripts/01-07`)
- Netlify DNS zone creation for 6 subdomains + NS delegation (`scripts/19, 22`)
- Vercel project setup + domain attachment + production deploy (`scripts/08-13`)
- DMARC/MTA-STS/CAA on every zone (`scripts/06, 19, 22`)
- 2FA enforcement on CF MAIN (`scripts/21` mints admin token; CF API auto-sets `enforce_twofactor`)
- All artifacts archived to private GitHub repo (`scripts/14`)

### ⚠️ Partially automated (needs user-supplied input)
- **ImprovMX email forwarding for `users.*` and `corp.*`**: User must (1) sign up at improvmx.com (web-only, no API), (2) generate API key, (3) save key to `secrets.json`, (4) run `scripts/23_setup_improvmx.py`. The script does the rest.
- **Help-desk vendor**: User picks vendor (Zendesk/Intercom/HelpScout), then run `scripts/24_set_help_vendor.py` (TODO — 5-line script to update the CNAME).

### ❌ Truly manual (cannot be automated; requires user action)
1. **CF Email Routing destination verification** — Cloudflare sends a verification email to `admin@sonicloud.app`. User must click the link in the inbox. Link has session-bound CSRF tokens, cannot be auto-clicked. Until done, catch-all forwarding is dormant. Alternative (supply IMAP creds to read inbox programmatically) is out of scope.
2. **ImprovMX signup** — no API; web form only. ~2 minutes.
3. **GitHub PAT rotation** — GitHub does not allow token rotation via API; must be done in dashboard.
4. **CF/Vercel/Netlify master token rotation** — each vendor requires dashboard access to rotate the master token.
5. **Vendor selection for `help.sonicloud.app`** — business decision; once chosen, the technical step (update CNAME) is automated.

---

## Part 4 — Operator Runbook (current)

### Daily operations
- Deploy code: push to relevant GH repo (Vercel auto-deploys; for CF Workers, run `scripts/06_configure_root_zone.py`)
- Check status: `python3 scripts/20_final_verify.py`
- View audit logs: CF dashboard → Audit Logs

### Add a new subdomain
```bash
# Single command — creates Netlify zone, adds DNS records, adds NS delegation in CF root
# (add the new sub to ALL_SUBDOMAINS list in the script first)
python3 scripts/22_stamp_all_subdomains.py
```

### Kill-switch (compromised subdomain)
```bash
# Revoke all API tokens for affected account via CF dashboard
# Re-create the Netlify DNS zone with new NS
# Update NS records in CF root zone
# Audit logs
```

### Token rotation (every 90 days)
```bash
python3 scripts/03_mint_scoped_tokens.py          # Re-mint scoped CF token
# OR
python3 scripts/21_mint_admin_token.py            # Admin token (with account perms)
# Update secrets.json with new token
# Rotate Netlify/Vercel/GH tokens via each dashboard (manual)
# Update secrets.json with new tokens
```

---

## Part 5 — Design Discussion: The NS Layer Should Evolve

This section captures the design conversation that happened immediately after the live state above was finalized. The conversation was about whether the current topology (Netlify DNS zones for `app`, `users`, `api`, etc.) is the right shape for future regional sharding, or whether those public-facing names should move back to the CF root zone.

### The problem statement (as raised)

Current state: every public-facing subdomain (`app`, `users`, `api`, etc.) is on its own Netlify DNS zone with NS delegation from CF. That gives clean TOS/account isolation. But it creates two issues when scaling:

1. **Regional routing:** If `app.sonicloud.app` lives on a Netlify DNS zone, the A record can point at one backend. To route US users to a US pod and EU users to an EU pod, you need something in front of that A record doing geo-routing. Netlify DNS doesn't do that. CF Workers do, but only if the name is on a CF zone with a Worker route.

2. **Anti-bot / compute at edge:** The user wants every visitor to pass through compute (anti-bot measurement, routing decision) BEFORE hitting the backend. Netlify Functions can do this but they are us-east-2 only (empirically verified, separate scraper work — not relevant here except as the regional-lock fact). CF Workers run at anycast edge.

3. **No-redirect requirement:** User explicitly does not want HTTP redirects. Wants the request handled in-place — ideally with compute deciding what to do with that user. This rules out a simple "302 to pod URL" pattern.

### The reframe that came out of the discussion

**"DNS is not the bottleneck — the A record target is."**

When `app.sonicloud.app` is on a Netlify DNS zone:
- Visitor's resolver hits Netlify's NS1 anycast **once per TTL** (NS1 is globally distributed, ~5ms anywhere)
- Resolver caches the A record for the TTL duration
- Subsequent requests in the TTL window go straight to whatever IP the A record points at

So the actual question is: **what does the A record point at?** If it points at Netlify Functions → us-east-2 (bad). If it points at a CF Worker custom domain → edge-distributed (good). But CF Workers don't accept custom domains from non-CF zones — that's the real constraint.

**Implication:** Netlify DNS is fine for pods, but the public-facing names (`app`, `users`, `api`, etc.) should live on the CF root zone so CF Workers can terminate them at the edge.

### Proposed topology (NOT yet implemented — discussion only)

```
                ┌─────────────────────────────────────────────┐
                │ CF root zone: sonicloud.app (single zone)    │
                │                                             │
                │ A   sonicloud.app          → CF Worker       │
                │ A   app.sonicloud.app      → CF Worker       │
                │ A   api.sonicloud.app      → CF Worker       │
                │ A   users.sonicloud.app    → CF Worker       │
                │                                             │
                │ NS  app-us-east-01.sonicloud.app → Netlify   │
                │ NS  app-us-west-01.sonicloud.app  → Netlify  │
                │ NS  api-us-east-01.sonicloud.app  → Netlify  │
                │ NS  api-us-west-01.sonicloud.app   → Netlify │
                │ NS  app-eu-west-01.sonicloud.app   → Netlify │
                │ ... (one NS delegation per pod)              │
                └──────────────┬──────────────────────────────┘
                               │
                               ▼
                ┌─────────────────────────────────────────────┐
                │ CF Worker ("central nervous system")        │
                │ • Bot management (free Bot Fight Mode)     │
                │ • Geo-routing via cf.colo                   │
                │ • User→pod lookup (KV/D1, free)             │
                │ • fetch() to pod URL — reverse proxy       │
                │ • 100K req/day free                         │
                └──────┬──────────────────────────────────────┘
                       │ fetch() per request
                       ▼
        ┌──────────────────────────────────────────────────────┐
        │ Pod fleet (each on own Netlify DNS zone + own acct)  │
        │                                                      │
        │ app-us-east-01  → Vercel us-east-1  + Supabase us-east-1│
        │ app-us-west-01   → Vercel us-west   + Neon us-west      │
        │ app-eu-west-01   → Vercel eu-west   + Supabase eu-west   │
        │ ...                                                   │
        │                                                      │
        │ Each pod = full vertical slice (frontend+API+DB)      │
        │ Each pod = own Netlify DNS zone (TOS isolation)      │
        │ Each pod = own backend accounts (free tier per acct)   │
        └──────────────────────────────────────────────────────┘
```

**Key principle:** Public-facing names (`app`, `api`, `users`) on CF because they need edge routing. Pod names (`app-us-east-01`, etc.) on Netlify DNS because they need TOS/account isolation. NS delegation from CF → Netlify connects the two layers.

### The reverse-proxy pattern (NOT a redirect)

```js
// CF Worker at app.sonicloud.app
export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);

    // 1. Anti-bot (free tier has Bot Fight Mode + botManagement signals)
    const botScore = request.cf.botManagement?.score ?? 50;
    if (botScore < 30 && !request.cf.botManagement?.verifiedBot) {
      return new Response('Blocked', { status: 403 });
    }

    // 2. Geo-routing (free — request.cf.colo and request.cf.country)
    const colo = request.cf.colo; // e.g., 'SJC', 'EWR'
    const region = coloToRegion(colo); // 'us-west', 'us-east'

    // 3. User-shard lookup (KV or D1 — both free)
    const userId = parseCookie(request).userId;
    let podUrl;
    if (userId) {
      podUrl = await env.PODS.get(`user:${userId}`); // KV lookup
    }
    if (!podUrl) {
      podUrl = await pickLeastLoadedPod(region); // new user → assign
    }

    // 4. Reverse proxy — NOT a redirect
    const backendUrl = `https://${podUrl}${url.pathname}${url.search}`;
    const backendReq = new Request(backendUrl, request);
    backendReq.headers.set('X-Pod-Auth', env.POD_SHARED_SECRET);
    backendReq.headers.set('X-User-Region', region);

    return fetch(backendReq);
  }
};
```

The user's URL bar stays at `app.sonicloud.app`. No 301/302. The backend pod URL is invisible. This is the "handle the request with compute" pattern requested.

### The two-layer fan-out (cost optimization)

The naive version routes EVERY request through the CF Worker. That burns the 100K/day Worker limit on API calls — which are the real cost driver, not page loads.

**Layer 1 (page loads) → through CF Worker:**
- HTML, anti-bot, pod assignment
- Worker returns pod URL + signed token in response body
- ~1 Worker invocation per page load
- 100K page loads/day is a lot for a free app

**Layer 2 (API calls) → direct to pod, bypassing Worker:**
- Frontend receives `{podUrl: "https://app-us-east-01.sonicloud.app", token: "..."}` on page load
- Subsequent `fetch("https://app-us-east-01.sonicloud.app/api/...", {headers: {Authorization: token}})` goes direct
- Pod validates token + CORS origin
- Doesn't burn Worker invocations

This is the same shape Vercel itself uses (frontend talks to backend functions directly after initial load). It means:
- Worker only handles page loads + auth bootstrap (~5% of traffic)
- API traffic goes straight to pods (~95% of traffic)
- Each pod's backend account has its own free tier quota
- Adding pods scales horizontally without touching the Worker

### Pod naming

User mentioned hex codes (`a1b2c3.sonicloud.app`) instead of readable names (`app-us-east-01`). Conclusion: fine, but real security is the per-pod auth token, not URL unguessability. Stick with readable names unless there's a specific reason.

### Why this beats "Netlify runs the apex"

| Concern | Netlify at apex | CF Worker at apex |
|---|---|---|
| Every visitor hits one region | ✗ us-east-2 only (empirically verified) | ✓ anycast edge, ~300 PoPs |
| Anti-bot at edge | ✗ no WAF on free | ✓ Bot Fight Mode + signals |
| Compute before routing | ✗ Functions are backend-only | ✓ Worker runs at edge |
| Reverse proxy (no redirect) | ✗ only Functions (us-east) | ✓ native `fetch()` |
| Geo-routing | ✗ | ✓ `request.cf.colo` |
| Free tier req limit | 300 credits/month (shared pool) | 100K req/day (Workers) |
| Per-pod isolation | ✓ (keep for pods) | n/a (edge is stateless) |

Netlify's role narrows to: **DNS zone per pod (isolation) + optional backend hosting for the pod itself**. It stops being the apex — it becomes the pod registry.

### When to actually upgrade

The trigger for upgrading is **only** when the edge router itself exceeds free tier, not when individual pods do. Individual pods just get new siblings added.

| Threshold | Action |
|---|---|
| Pod hits 80% of any meter | Stop new user assignment to it; spin up a new pod in same region |
| Edge Worker hits 100K req/day | Either upgrade CF Workers ($5/mo, simplest) or shard the edge across 2 CF accounts (complex) |
| Need >1 CF account's worth of KV/D1 | Shard by user ID prefix across CF accounts |
| Pod count grows unwieldy (>20) | Consider a paid tier on the busiest pod's backend (e.g., one Vercel Pro) rather than continuing to fan out |

### Wear-down matching (user's concept, restated)

Pair pods so their resource profiles balance. A pod that's storage-heavy (lots of user uploads) should be paired with one that's compute-heavy (lots of API processing) — they stress different meters, so neither hits its cap first. This is a pod-assignment-strategy concern, not a DNS concern, but it informs what regional/backend pairings make sense.

### Open question (the one left hanging)

The user asked whether, for `app.sonicloud.app` and similar, since they already point to CF (via NS delegation to Netlify, but the NS records sit on CF), the routing should just be solved at the CF level and Netlify DNS would "get out of the way." The answer is yes, but the current topology has the A record on the Netlify DNS zone, not on CF — so CF can't terminate it with a Worker route. To make CF the routing layer, the A record for `app.sonicloud.app` would need to move from the Netlify DNS zone to the CF root zone (or a CF subdomain zone, but Enterprise is required for true subdomain zones). That means deleting the Netlify DNS zone for `app` (and `users`, `api`, etc.) and re-adding those records directly on CF.

Netlify DNS zones would then only exist for actual pods (`app-us-east-01`, etc.), not for the public-facing names.

**This is the migration the new session needs to evaluate.**

---

## Part 6 — Concrete Migration Steps (if proceeding with the design from Part 5)

Not yet executed. Listed here as the proposed sequence for the new session to validate and run.

1. **Move `app/users/content/corp/api/cdn.sonicloud.app` back to CF root zone.**
   - Delete the 6 Netlify DNS zones (capture any records first — currently just the placeholder A + security TXTs/CAAs which already exist on CF root or can be re-stamped)
   - Add A records on CF root zone pointing at the CF Worker (or use Worker custom domains which CF provisions automatically)
   - DMARC/MTA-STS/CAA on CF root already cover `*.` via wildcards? — verify. May need per-sub TXT records added on CF root.

2. **Keep Netlify DNS zones, but repurpose them for pods.**
   - Each pod gets its own Netlify DNS zone: `app-us-east-01.sonicloud.app`
   - A record on the pod zone points at the pod's hosting (Vercel/Netlify Functions/etc.)
   - Add NS delegation in CF root zone for each pod subdomain

3. **Build the CF Worker as the edge reverse proxy.**
   - Wire up KV for user→pod mapping
   - Wire up D1 for pod health/usage metrics
   - Implement anti-bot + geo-routing + reverse proxy logic
   - Deploy via Wrangler

4. **Pod backends — flexible.** Each pod's backend can be:
   - Vercel (regional, free 100GB bandwidth)
   - Netlify Functions (us-east-2 only — but free)
   - Supabase (regional DB, free 500MB)
   - Neon (regional DB, free 0.5GB compute)

5. **Pod assignment strategy.** New users → least-loaded pod in their region. Returning users → sticky to their assigned pod (data locality). When a pod hits ~80% of any free-tier meter, stop assigning new users to it.

---

## Part 7 — What the new session should figure out first

1. **Validate the reframe.** Is "DNS is not the bottleneck, A record target is" actually correct? Specifically: does a CF Worker route on `app.sonicloud.app` require the A record to be on the CF zone, or can it work via CNAME from a Netlify DNS zone? (I believe it requires the record on the CF zone, but this should be confirmed against current CF Workers docs.)

2. **Decide on the migration trigger.** Is the current state (Netlify DNS for public names, A records point at placeholders) acceptable until traffic grows, or should the migration to CF-hosted public names happen now to avoid future renumbering?

3. **Plan the pod lifecycle automation.** The existing `scripts/22_stamp_all_subdomains.py` stamps the current 6 subdomains. The new pattern needs a `pod-manager` CLI that can spin up a new pod (Netlify DNS zone + NS delegation + backend provisioning + Worker KV registration) and tear one down.

4. **Pod token security.** The user noted earlier (in the now-irrelevant scraper work) that Netlify's network has no WAF on its bb-api endpoint — plain curl works with the right cookie. The pod auth tokens need to be robust (signed JWT with short TTL, not a shared secret), because there's no WAF to fall back on.

5. **Email routing for pods.** CF Email Routing is zone-level and won't work on delegated sub-zones. Pods probably don't need email, but if they do (e.g., per-pod user support addresses), ImprovMX or similar is required.

---

## Part 8 — Files / locations (for the new session)

- **Workspace:** `/home/z/my-project/` — contains ONLY the Netlify scraper work (unrelated to sonicloud). Do not confuse the two.
- **Existing docs in workspace (scraper-specific, NOT sonicloud):**
  - `/home/z/my-project/agent-kit/docs/findings-report.md` — Netlify free tier findings
  - `/home/z/my-project/agent-kit/docs/dashboard-automation.md` — Netlify bb-api reference
  - `/home/z/my-project/agent-kit/docs/agent-skill.md` — Netlify scraper skill
  - `/home/z/my-project/agent-kit/docs/free-tier-investigation-methodology.md` — generalized methodology
- **sonicloud-infra repo (NOT in workspace):** `https://github.com/zulfikarbarbora-outl/sonicloud-infra` (private) — clone to access the actual scripts referenced in Part 3.
- **This doc:** `/home/z/my-project/download/sonicloud-ns-architecture.md`
