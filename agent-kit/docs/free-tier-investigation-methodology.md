# Agent Skill: Free-Tier Service Investigation Methodology

> **Load this file when investigating any cloud platform's free tier for scraping, data pipelines, or background compute.**
> This is a generalized methodology distilled from a deep investigation of Netlify's free tier. The patterns apply to any metered cloud platform (Cloudflare, Vercel, Supabase, Railway, Fly.io, etc.).

## Investigation framework

When evaluating a cloud platform's free tier, systematically probe these dimensions in order:

### Phase 1: Pricing model understanding

1. **Read the pricing page and credit/billing docs carefully.** Don't skim — every word matters. Look for:
   - Is there a shared pool (like Netlify's 300 credits) or separate per-feature quotas?
   - Which actions cost credits and which are free?
   - Are there "0 credit" actions that are genuinely free? (Netlify: preview deploys, blob storage, form submissions)
   - What's the billing unit? (per-invocation? per-GB-hour? per-request? wall-clock vs CPU-time?)
   - Is there a hard stop (account paused) or overage billing?

2. **Check for legacy vs new plans.** Platforms often revamp pricing. Legacy accounts may be grandfathered with more generous quotas. Check:
   - When did the pricing change?
   - Are existing accounts grandfathered?
   - Is switching to the new plan reversible? (Netlify: irreversible)

3. **Test the credit meter lag.** The API-reported usage often lags the dashboard by 5-30+ minutes. Always note which source is real-time. Test by:
   - Recording API-reported usage before an action
   - Performing the action
   - Polling the API every 30s for 5 min
   - Comparing against the dashboard

### Phase 2: Quota multiplication strategies

1. **Check if the free quota is per-account, per-team, per-project, or per-email.** This determines whether you can multiply capacity by creating multiple entities.
   - **Per-org (Supabase):** One account → N orgs → N free quotas. Easy multiplication.
   - **Per-user (Netlify):** One Free team per user. Multiplication requires N distinct accounts.
   - **Per-project (some platforms):** Each project gets its own quota. Easy within one account.

2. **Test the enforcement.** Try to create a second free entity:
   - Does the API return 422? (Netlify: `"Account plan is unavailable to this user"`)
   - Does it silently fail?
   - Is there a phone/credit-card verification gate?

3. **Check for identity verification gates.** Some platforms require:
   - Credit card on file (even for free tier)
   - Phone verification
   - Email verification per account
   - GitHub OAuth uniqueness
   The fewer gates, the easier multiplication (but also the more likely ToS prohibits it).

### Phase 3: Storage and data exfiltration

1. **Identify ALL meters.** Map out every possible way data flows in/out:
   - **Ingress** (downloads FROM external sites INTO the platform) — often free
   - **Egress** (responses sent FROM the platform TO clients) — usually metered
   - **At-rest storage** — sometimes free (Netlify Blobs), sometimes metered (Netlify Database after Jul 2026)
   - **API read/write** — sometimes free (Netlify Blobs API), sometimes metered
   - **Internal transfers** (function → storage within the platform) — usually free

2. **Find unmetered channels.** Look for data paths that DON'T appear in the billing meter list:
   - Storage at-rest with no per-GB charge (Netlify Blobs)
   - API reads/writes with no per-operation charge (Netlify Blobs)
   - Internal function-to-storage transfers (Netlify function → Blobs)
   - Function logs with retention (Netlify: 24h, 4KB/invocation — a free egress channel)

3. **Test empirically.** Push 10+ MB through each channel and check if the meter moves:
   - Download 10 MB from internet through a function → does bandwidth meter increase?
   - Write 10 MB to storage → does a storage meter increase?
   - Read 10 MB back via API → does an API/egress meter increase?

4. **The "free egress" pattern.** The optimal data flow for any scraping use case:
   ```
   Function scrapes URL → downloads data (INGRESS = free)
   → writes to blob storage (AT-REST = free)
   → returns tiny "ok" response (EGRESS = minimal)
   → client reads data via API (API READ = free)
   Total cost: ~0.0006 credits (compute only)
   ```

### Phase 4: Compute — build vs function

1. **Map the compute surfaces.** Most platforms offer multiple compute contexts:
   - **Serverless functions** (Netlify Functions, Cloudflare Workers, Vercel Functions) — short timeout (10-30s), per-invocation billing
   - **Build process** (Netlify Build, Vercel Build) — long timeout (15+ min), triggered by deploys, often free on preview deploys
   - **Edge functions** (Netlify Edge Functions, Cloudflare Workers) — run at CDN edge, different billing model
   - **Background functions** (Netlify Background Functions) — long-running async, different timeout

2. **Find the free compute windows.**
   - Preview deploys often trigger builds for free (Netlify: 0 credits for preview deploys)
   - Build process can run arbitrary code (npm install, scripts) for up to the build timeout
   - Build plugins can access platform storage (Blobs, env vars) that the build command itself cannot

3. **Key architectural pattern: build-as-compute.**
   ```
   Cron trigger → deploy (free preview) → build runs scraper (15 min compute)
   → plugin stores results to blob storage (free) → deploy completes (0 credits)
   ```

4. **Billing unit matters.**
   - **Wall-clock × memory** (Netlify Functions): You pay while waiting on I/O. Bad for slow API calls.
   - **CPU-time only** (Cloudflare Workers): Waiting on I/O is free. Good for I/O-bound scraping.
   - **Per-invocation** (old model): Number of calls matters more than duration.
   - **Per-request** (Cloudflare Workers): 100k/day free. Good for high-volume proxy use.

### Phase 5: TLS fingerprinting (for scraping bot-protected sites)

1. **Check the default fingerprint.** Run `fetch()` from a function and hit a TLS echo service (like `tls.peet.ws/api/all`). Note the JA3/JA4 hash.
   - Node.js `fetch()` has a recognizable JA3 hash — bots are easily detected
   - Cloudflare Workers have a different but also recognizable fingerprint

2. **Test TLS impersonation options.**
   - **Pure JS libraries** (like `tls-impersonate`): Work in Lambda-like environments. Can change cipher list, extensions, ALPN. Partial impersonation — may not match real browser exactly.
   - **Native binary packages** (like `curl-cffi-node`): More accurate but often fail due to glibc version mismatch in Lambda/container environments. Check glibc requirements before deploying.
   - **Headless browser** (like `@sparticuz/chromium` + puppeteer): Most accurate (real Chrome TLS) but heavy (~150MB binary, slow startup). Only works in build process or container-based functions.
   - **Platform-native** (like Cloudflare Workers using the built-in `fetch()`): May already have a good fingerprint depending on the platform.

3. **Test against real anti-bot services.** After impersonation, test against:
   - Cloudflare Bot Management
   - DataDome
   - PerimeterX
   Most sites only check JA3 (basic), not JA4 (extension-level). The `tls-impersonate` approach bypasses basic checks.

### Phase 6: Access control and project visibility

1. **Check default visibility.** New projects may be private by default (Netlify: post-Aug 2026, "Private by default" enabled for new teams). This means:
   - Function URLs return 401
   - Static assets require login
   - No anonymous access at all

2. **Find the visibility toggle.** Check:
   - Is it in the public API? (Netlify: NO — `PATCH /api/v1/sites/{id}` returns null for SSO fields)
   - Is it in an internal/dashboard API? (Netlify: YES — bb-api at `app.netlify.com/access-control/bb-api/`)
   - What auth does the internal API need? (Netlify: session cookies, specifically `_nf-auth` JWT)

3. **Test the internal API's WAF.** Before assuming you need a browser/Playwright:
   - Try plain `curl` with just the auth cookie
   - Try without User-Agent, Origin, Referer headers
   - Try with only the JWT cookie (without session cookies)
   - (Netlify: NO WAF — plain curl with just `_nf-auth` cookie works for both GET and PUT)

4. **Document the one-time setup.** The visibility toggle is usually a one-time operation per site. Document:
   - What endpoint to call
   - What auth is needed
   - What the request body should be
   - How to verify it worked

### Phase 7: Internal API discovery

1. **Capture a HAR file.** Open the platform dashboard, perform common actions (view settings, toggle visibility, check usage), and capture all network traffic.

2. **Parse the HAR for internal endpoints.** Look for:
   - API calls to the dashboard's own backend (not the public API)
   - Endpoints that return data the public API doesn't (real-time usage, observability, settings)
   - Endpoints that accept writes the public API doesn't (SSO toggle, env vars via alternative path)

3. **Probe for additional endpoints.** Based on dashboard UI features, guess likely API paths and probe them with the session cookie:
   - `/accounts/{id}/audit`
   - `/sites/{id}/env`
   - `/sites/{id}/observability/query/counts`
   - `/sites/{id}/blobs`

4. **Save sample responses.** For each working endpoint, save the response JSON. Use for:
   - Mock testing (build tests against saved shapes)
   - Schema change detection (re-probe later, diff against saved)
   - Documentation (show actual response structure)

5. **Build a CLI tool.** Wrap all discovered endpoints in a single CLI tool that takes the cookie/token and provides named commands. This makes the internal API usable without a browser.

### Phase 8: Stress testing and rate limits

1. **Test deploy/build concurrency.** Trigger multiple deploys rapidly:
   - Does the platform queue them? (Netlify: 1 concurrent build on Free, queued deploys wait)
   - Does it drop them? (Netlify: API deploys with empty files stay in `state=new` forever — never trigger builds)
   - Is there a rate limit on deploy creation? (Netlify: not observed — 10 API deploys accepted without 429)

2. **Distinguish API deploys from CLI deploys.**
   - API deploys (`POST /sites/{id}/deploys` with files) may complete in ~1s (direct CDN upload, NO build process)
   - CLI deploys (`netlify deploy`) run the full build locally (npm install, function bundling, plugins) then upload
   - If you need the build process (for plugins, queue processing), you MUST use CLI deploys or Git pushes — API deploys skip the build

3. **Test function invocation rate limits.** Rapidly invoke the function and check for 429s:
   - (Netlify: no documented rate limit on function invocations; billing is per-GB-hour, not per-invocation)

## Generalized findings (platform-agnostic)

### The "free maxxing" pattern

For any metered platform, the optimal free-tier strategy is:

1. **Use free compute (preview deploys / build process) for heavy work**
2. **Use free storage (unmetered at-rest) for data dumps**
3. **Use free API reads for data retrieval**
4. **Avoid metered egress** — return tiny responses, let clients fetch data via the free API
5. **Use function logs as a secondary egress channel** (if logs are free and readable via API)
6. **Trigger builds via CLI, not API** (API deploys may skip the build process)

### The "build-as-compute" pattern

The build process is often the most generous free compute:
- Longer timeout than functions (15+ min vs 30s)
- Can install arbitrary npm packages (including headless Chrome)
- Can write to platform storage via plugins (which have different env vars than the build command)
- Triggered by preview deploys (which are free)
- Limitation: 1 concurrent build on free plans (serialized processing)

### The "three free egress channels" pattern

For any scraping use case, use all three:
1. **Blob/object storage API** — free reads, persistent, no size limit
2. **Function logs** — free, time-limited retention (24h-7d), per-invocation cap (4KB typical)
3. **Function HTTP response** — metered egress, but tiny JSON pointers responses cost ~0

### The "internal API" pattern

Every cloud platform's dashboard uses an internal API (BFF) that the public API doesn't expose. This API typically:
- Uses session cookies or JWT (NOT the public API token)
- Has no WAF (plain curl works)
- Exposes operations the public API can't do (visibility toggles, real-time usage, observability)
- Can be discovered by capturing a HAR file from the dashboard
- Should be saved as sample responses for mock testing and schema change detection

### Cookie refresh strategy

Session cookies/JWTs expire. Strategies:
1. **Manual refresh** — re-extract from browser when tool returns 401
2. **Playwright automation** — programmatically login and extract cookies (requires storing credentials)
3. **HAR rotation** — periodically capture a new HAR and extract cookies
4. **OAuth token exchange** — check if the public API token can be exchanged for a session token (rarely works, but worth testing)

## What to document for future sessions

When investigating a platform, always produce:

1. **An llms.txt** — LLM-friendly API doc with exact endpoints, tokens, and IDs for the LIVE deployment
2. **An agent skill doc** — dev-session-ready file with all findings, gotchas, and recipes
3. **Sample API responses** — saved JSON for every discovered endpoint
4. **A CLI tool** — wrapping internal API endpoints in named commands
5. **A findings report** — comprehensive preservation of the research process and all data points

## Red flags to watch for

- **"Free tier" that requires credit card** — may charge on overage
- **Per-invocation billing on wall-clock time** — expensive for I/O-bound work
- **No preview/draft deploy option** — can't avoid production deploy costs
- **API deploys that skip builds** — can't trigger plugins/queue processing via API
- **SSO/visibility enabled by default** — functions unreachable until manually disabled
- **Storage that becomes metered after a date** — check for "free until" clauses
- **Native binary packages requiring newer glibc** — won't work in Lambda-like environments
- **glibc 2.38+ requirement** — AWS Lambda typically has 2.26 or 2.34
- **Credit meter lag** — API may show 0/300 for 30+ min after usage; trust the dashboard
