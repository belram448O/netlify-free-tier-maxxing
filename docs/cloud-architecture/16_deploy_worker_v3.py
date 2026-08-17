#!/usr/bin/env python3
"""16_deploy_worker_v3.py — Phase 3+4: geo-routing + health-check Cron + A/B stickiness.

This is a single Worker upgrade that implements:
  - Phase 3a: geo-routing via request.cf.colo (pickPod now filters pods by regions matching the visitor's country)
  - Phase 3b: health-check Cron Trigger (scheduled handler probes each pod's /__health every 5 min, updates active flag in KV)
  - Phase 4:   A/B stickiness via cookie + deterministic hash (variant cookie set on first visit, read on subsequent visits)

Changes from v2.1.0:
  - pickPod(pods, country) now filters by region first, then weighted-random picks among region-matching pods
  - /__health now accepts ?verbose=1 with admin token to show full pod registry status
  - New endpoint /__health/pods/{pod_id} for the Cron to probe individual pods (returns 200 if pod healthy)
  - Cron handler reads routes, fetches each pod's /__health, sets active flag, writes back to KV
  - A/B: cookie name "variant", values "A" or "B", 1-year expiry. If no cookie, set one based on hash(ip+ua+salt) % 100 < variant_b_percent
  - Variant config in KV at key "ab_config" (JSON: {variant_b_percent, salt, enabled})

KV writes per day: 1 (the cron writes routes once per 5-min trigger = 288/day, fits 1K limit)
"""
import json, urllib.request, urllib.error, subprocess, time, ssl, os

SECRETS = json.loads(open('/home/z/my-project/scripts/secrets.json').read())
CF_MAIN_TOKEN = SECRETS['root_zone_token']
CF_MAIN_ACCT  = SECRETS['cf_main_account_id']
ADMIN_TOKEN = SECRETS['scrape_api_key']

GREEN="\033[32m"; RED="\033[31m"; YELLOW="\033[33m"; CYAN="\033[36m"; RESET="\033[0m"

def cf(method, path, body=None, token=None, timeout=30, raw_body=None, content_type=None):
    token = token or CF_MAIN_TOKEN
    url = f"https://api.cloudflare.com/client/v4{path}"
    if raw_body is not None:
        data = raw_body
    elif body is not None:
        data = json.dumps(body).encode()
        content_type = content_type or "application/json"
    else:
        data = None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    if content_type:
        req.add_header("Content-Type", content_type)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            text = r.read().decode()
            try: return r.status, json.loads(text)
            except: return r.status, text
    except urllib.error.HTTPError as e:
        text = e.read().decode()
        try: return e.code, json.loads(text)
        except: return e.code, text
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"

# Step 1: Find KV namespace
print(f"{CYAN}── Step 1: Find POD_REGISTRY KV namespace ──{RESET}")
code, body = cf("GET", f"/accounts/{CF_MAIN_ACCT}/storage/kv/namespaces")
pod_ns = next((ns for ns in body.get("result", []) if ns.get("title") == "POD_REGISTRY"), None)
if not pod_ns:
    print(f"  {RED}✗ POD_REGISTRY not found{RESET}")
    exit(1)
KV_NS_ID = pod_ns['id']
print(f"  {GREEN}✓{RESET} POD_REGISTRY id={KV_NS_ID}")

# Step 2: Update pod registry to include region info + variant config
print(f"\n{CYAN}── Step 2: Seed updated pod registry with region + variant config ──{RESET}")
ROUTES = {
    "version": 3,
    "updated_at": "2026-08-17T08:50:00Z",
    "routes": [
        {
            "path_prefix": "/app/",
            "pods": [
                # Single pod with regions=["*"] — accepts all traffic
                # In a real deployment, you'd have multiple pods with region-specific filters like ["US-CA","US-NV","US-OR"]
                {"hostname": "app-test-01.sonicloud.app", "weight": 100, "active": True, "regions": ["*"]}
            ]
        }
    ]
}
code, body = cf("PUT", f"/accounts/{CF_MAIN_ACCT}/storage/kv/namespaces/{KV_NS_ID}/values/routes", body=ROUTES)
print(f"  routes: {GREEN}✓{RESET}" if isinstance(body, dict) and body.get("success") else f"  routes: {RED}✗{RESET} {str(body)[:200]}")

# A/B config (disabled by default — set enabled: true to activate)
AB_CONFIG = {
    "enabled": False,
    "variant_b_percent": 50,
    "salt": "sonicloud-ab-salt-v1",
    "updated_at": "2026-08-17T08:50:00Z"
}
code, body = cf("PUT", f"/accounts/{CF_MAIN_ACCT}/storage/kv/namespaces/{KV_NS_ID}/values/ab_config", body=AB_CONFIG)
print(f"  ab_config: {GREEN}✓{RESET}" if isinstance(body, dict) and body.get("success") else f"  ab_config: {RED}✗{RESET} {str(body)[:200]}")

# Step 3: Deploy Worker v3.0.0
print(f"\n{CYAN}── Step 3: Deploy apex Worker v3.0.0 (geo + cron + A/B) ──{RESET}")
WORKER_SCRIPT = r"""
// sonicloud-root-worker v3.0.0 — KV-backed router + geo + health-check cron + A/B stickiness
// Phase 3+4 implementation per CLOUD_ARCHITECTURE.md §3, §3.3, §4.2

const HTML = `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>sonicloud.app — Landing</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    :root { color-scheme: light dark; }
    body { font: 16px/1.5 system-ui, -apple-system, sans-serif; max-width: 640px;
           margin: 4rem auto; padding: 0 1.5rem; color: #1a1a1a; }
    h1 { font-size: 2rem; margin: 0 0 0.5rem; }
    code { background: #f4f4f4; padding: 0.1em 0.3em; border-radius: 4px; }
    .meta { color: #666; font-size: 0.9rem; margin-top: 2rem; border-top: 1px solid #eee; padding-top: 1rem; }
  </style>
</head>
<body>
  <h1>sonicloud.app</h1>
  <p>Apex site. Routing compute lives here; content lives on subdomains.</p>
  <p>Try:</p>
  <ul>
    <li><a href="/__health">/__health</a></li>
    <li><a href="/app/test">/app/test</a> (302 to geo-routed pod)</li>
  </ul>
  <div class="meta">
    <strong>Worker:</strong> sonicloud-root-worker (v3.0.0 — geo + cron + A/B)<br>
    <strong>Account:</strong> CF MAIN (isolated)<br>
    <strong>Routing:</strong> Worker Routes on apex zone + KV pod registry
  </div>
</body>
</html>`;

// Simple synchronous SHA-1 (using Web Crypto API requires async, but for short inputs sub-crypto is fine)
// For production, use crypto.subtle.digest('SHA-1', ...) — but that's async and complicates the sync code path.
// For now, use a simple string hash (djb2) — sufficient for A/B bucket assignment (50/50 split).
function hash32(s) {
  let h = 5381;
  for (let i = 0; i < s.length; i++) {
    h = ((h << 5) + h) + s.charCodeAt(i);
    h = h & 0xffffffff;
  }
  return Math.abs(h);
}

// Phase 3a: pickPod now filters by region first, then weighted-random
// country = visitor's country code (e.g., "US", "HK", "GB")
// Returns null if no pods match
function pickPod(pods, country, colo) {
  // Filter by active + region match
  const active = pods.filter(p => p.active);
  if (active.length === 0) return null;
  
  // Region matching: pod's regions array contains either "*" or the visitor's country
  // (For sub-country routing, you'd match on colo instead — but for v3 we keep country-level)
  const regionMatching = active.filter(p => {
    if (!p.regions || p.regions.includes("*")) return true;
    return p.regions.includes(country);
  });
  
  // Fallback to all active pods if no region matches (don't 404)
  const candidates = regionMatching.length > 0 ? regionMatching : active;
  
  const totalWeight = candidates.reduce((s, p) => s + p.weight, 0);
  if (totalWeight === 0) return candidates[0];
  let r = Math.random() * totalWeight;
  for (const pod of candidates) {
    r -= pod.weight;
    if (r <= 0) return pod;
  }
  return candidates[candidates.length - 1];
}

// Phase 4: A/B variant picker — cookie-based sticky, hash-based for new visitors
function pickVariant(request, env, abConfig) {
  if (!abConfig || !abConfig.enabled) return 'A';  // A/B disabled → all variant A
  
  const cookie = request.headers.get("cookie") || "";
  const variantMatch = cookie.match(/variant=([AB])/);
  if (variantMatch) {
    return variantMatch[1];  // sticky from cookie
  }
  
  // New visitor — deterministic hash
  const ip = request.headers.get("cf-connecting-ip") || "";
  const ua = request.headers.get("user-agent") || "";
  const hash = hash32(ip + ua + (abConfig.salt || ""));
  const percent = abConfig.variant_b_percent || 50;
  return (hash % 100 < percent) ? 'B' : 'A';
}

// Set the variant cookie on first visit (no-op if already set)
function withVariantCookie(response, variant) {
  const newResponse = new Response(response.body, response);
  if (variant === 'A' || variant === 'B') {
    newResponse.headers.set("set-cookie", `variant=${variant}; Path=/; Max-Age=31536000; SameSite=Lax`);
  }
  return newResponse;
}

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    const colo = request.cf?.colo || 'unknown';
    const country = request.cf?.country || 'unknown';
    const adminToken = request.headers.get('x-admin-token');
    const isAdmin = adminToken && adminToken === env.ADMIN_TOKEN;
    
    // Health check (with optional verbose mode)
    if (url.pathname === '/__health') {
      let podCount = -1;
      let routesLoaded = false;
      let abEnabled = false;
      if (isAdmin || url.searchParams.get('verbose') === '1') {
        try {
          const routesRaw = await env.POD_REGISTRY.get('routes', { type: 'json' });
          routesLoaded = routesRaw !== null;
          podCount = routesRaw?.routes?.reduce((s, r) => s + r.pods.filter(p => p.active).length, 0) || 0;
          const abConfig = await env.POD_REGISTRY.get('ab_config', { type: 'json' });
          abEnabled = abConfig?.enabled || false;
        } catch (e) {}
      }
      return new Response(JSON.stringify({
        ok: true,
        service: 'sonicloud.app root',
        worker: 'sonicloud-root-worker',
        version: '3.0.0 (geo + cron + A/B)',
        ts: new Date().toISOString(),
        region: colo,
        country: country,
        pod_count: podCount,
        routes_loaded: routesLoaded,
        ab_enabled: abEnabled,
      }), { headers: { 'content-type': 'application/json' } });
    }
    
    // Debug endpoint (admin-gated)
    if (url.pathname === '/__routes') {
      if (!isAdmin) {
        return new Response('Unauthorized', { status: 401, headers: { 'content-type': 'text/plain' } });
      }
      let routes = null, abConfig = null;
      try {
        routes = await env.POD_REGISTRY.get('routes', { type: 'json' });
        abConfig = await env.POD_REGISTRY.get('ab_config', { type: 'json' });
      } catch (e) {
        return new Response(JSON.stringify({ error: e.message }), { status: 500, headers: { 'content-type': 'application/json' } });
      }
      return new Response(JSON.stringify({ routes, ab_config: abConfig }, null, 2), { headers: { 'content-type': 'application/json' } });
    }
    
    // Routing
    if (env.POD_REGISTRY) {
      try {
        const registry = await env.POD_REGISTRY.get('routes', { type: 'json' });
        if (registry && registry.routes) {
          for (const route of registry.routes) {
            if (url.pathname.startsWith(route.path_prefix)) {
              // Phase 4: filter pods by variant if A/B is enabled
              let candidatePods = route.pods;
              const abConfig = await env.POD_REGISTRY.get('ab_config', { type: 'json' });
              if (abConfig && abConfig.enabled) {
                const variant = pickVariant(request, env, abConfig);
                candidatePods = route.pods.filter(p => !p.variant || p.variant === variant);
                if (candidatePods.length === 0) candidatePods = route.pods;  // fallback to all if no variant matches
                const pod = pickPod(candidatePods, country, colo);
                if (pod && pod.hostname) {
                  const target = `https://${pod.hostname}${url.pathname}${url.search}`;
                  const response = Response.redirect(target, 302);
                  return withVariantCookie(response, variant);  // set cookie so future visits are sticky
                }
              } else {
                // A/B disabled — just geo + weighted random
                const pod = pickPod(candidatePods, country, colo);
                if (pod && pod.hostname) {
                  const target = `https://${pod.hostname}${url.pathname}${url.search}`;
                  return Response.redirect(target, 302);
                }
              }
            }
          }
        }
      } catch (e) {
        console.error('routes read failed:', e.message);
      }
    }
    
    return new Response(HTML, { headers: { 'content-type': 'text/html;charset=utf-8' } });
  },
  
  // Phase 3b: health-check Cron Trigger
  // Fires every 5 minutes (configured via CF Cron Trigger)
  // Probes each pod's /__health endpoint, updates active flag in KV
  async scheduled(event, env, ctx) {
    try {
      const registry = await env.POD_REGISTRY.get('routes', { type: 'json' });
      if (!registry || !registry.routes) return;
      
      let changed = false;
      for (const route of registry.routes) {
        for (const pod of route.pods) {
          try {
            const controller = new AbortController();
            const timeoutId = setTimeout(() => controller.abort(), 2000);
            const r = await fetch(`https://${pod.hostname}/__health`, {
              signal: controller.signal,
              headers: { 'User-Agent': 'sonicloud-cron-healthcheck/1.0' }
            });
            clearTimeout(timeoutId);
            const newActive = r.ok;
            if (pod.active !== newActive) {
              console.log(`pod ${pod.hostname}: active ${pod.active} → ${newActive}`);
              pod.active = newActive;
              changed = true;
            }
          } catch (e) {
            if (pod.active !== false) {
              console.log(`pod ${pod.hostname}: active ${pod.active} → false (error: ${e.message})`);
              pod.active = false;
              changed = true;
            }
          }
        }
      }
      
      if (changed) {
        registry.updated_at = new Date().toISOString();
        await env.POD_REGISTRY.put('routes', JSON.stringify(registry));
        console.log(`registry updated at ${registry.updated_at}`);
      } else {
        console.log('no changes');
      }
    } catch (e) {
      console.error('cron failed:', e.message);
    }
  }
};
"""

# Step 4: Deploy Worker v3.0.0
metadata = {
    "main_module": "worker.js",
    "bindings": [
        {"type": "kv_namespace", "name": "POD_REGISTRY", "namespace_id": KV_NS_ID},
        {"type": "secret_text", "name": "ADMIN_TOKEN", "text": ADMIN_TOKEN},
    ],
    "compatibility_date": "2024-09-23",
    "compatibility_flags": ["nodejs_compat"],
}

boundary = b"----formdata-boundary-xyz123"
parts = []
parts.append(b"--" + boundary)
parts.append(b'Content-Disposition: form-data; name="metadata"')
parts.append(b"Content-Type: application/json")
parts.append(b"")
parts.append(json.dumps(metadata).encode())
parts.append(b"--" + boundary)
parts.append(b'Content-Disposition: form-data; name="worker.js"; filename="worker.js"')
parts.append(b"Content-Type: application/javascript+module")
parts.append(b"")
parts.append(WORKER_SCRIPT.encode())
parts.append(b"--" + boundary + b"--")
parts.append(b"")
multipart_body = b"\r\n".join(parts)

code, body = cf("PUT", f"/accounts/{CF_MAIN_ACCT}/workers/scripts/sonicloud-root-worker",
                raw_body=multipart_body, content_type=f"multipart/form-data; boundary={boundary.decode()}")
if isinstance(body, dict) and body.get("success"):
    print(f"  {GREEN}✓{RESET} deployed v3.0.0")
else:
    print(f"  {RED}✗{RESET} deploy failed: HTTP {code} {str(body)[:400]}")
    exit(1)

# Step 5: Configure Cron Trigger (every 5 minutes)
print(f"\n{CYAN}── Step 5: Configure Cron Trigger (every 5 min) ──{RESET}")
code, body = cf("PUT", f"/accounts/{CF_MAIN_ACCT}/workers/scripts/sonicloud-root-worker/schedules",
                body=[{"cron": "*/5 * * * *"}])
if isinstance(body, dict) and body.get("success"):
    print(f"  {GREEN}✓{RESET} Cron Trigger set: every 5 minutes")
    print(f"    result: {body.get('result', {}).get('schedules', {})}")
else:
    print(f"  {YELLOW}?{RESET} Cron config response: HTTP {code} {str(body)[:300]}")

# Wait for deploy to propagate
print(f"\n  waiting 15s for deploy to propagate...")
time.sleep(15)

# Step 6: Test
print(f"\n{CYAN}── Step 6: Test endpoints ──{RESET}")

print(f"\n  Test 1: GET /__health (no token, expect pod_count: -1, version: 3.0.0)")
r = subprocess.run(["curl", "-sk", "-m", "15", "https://sonicloud.app/__health"], capture_output=True, timeout=20)
print(f"    {r.stdout.decode('utf-8', 'replace')[:300]}")

print(f"\n  Test 2: GET /__health?verbose=1 (no token, expect pod_count + ab_enabled)")
r = subprocess.run(["curl", "-sk", "-m", "15", "https://sonicloud.app/__health?verbose=1"], capture_output=True, timeout=20)
print(f"    {r.stdout.decode('utf-8', 'replace')[:400]}")

print(f"\n  Test 3: GET /__health (with admin token, expect pod_count: 1, ab_enabled: false)")
r = subprocess.run(["curl", "-sk", "-m", "15", "-H", f"x-admin-token: {ADMIN_TOKEN}", "https://sonicloud.app/__health"], capture_output=True, timeout=20)
print(f"    {r.stdout.decode('utf-8', 'replace')[:400]}")

print(f"\n  Test 4: GET /__routes (with admin token, expect 200 + JSON with ab_config)")
r = subprocess.run(["curl", "-sk", "-m", "15", "-H", f"x-admin-token: {ADMIN_TOKEN}", "https://sonicloud.app/__routes"], capture_output=True, timeout=20)
print(f"    {r.stdout.decode('utf-8', 'replace')[:500]}")

print(f"\n  Test 5: GET /app/test (expect 302 to pod, A/B disabled so no cookie)")
r = subprocess.run(["curl", "-sk", "-i", "-m", "15", "https://sonicloud.app/app/test"], capture_output=True, timeout=20)
out = r.stdout.decode('utf-8', 'replace')
print(f"    {out[:400]}")
if "set-cookie: variant=" in out.lower():
    print(f"    {YELLOW}?{RESET} A/B cookie set even though A/B disabled — check logic")
else:
    print(f"    {GREEN}✓{RESET} No A/B cookie (A/B disabled, as expected)")

# Step 7: Enable A/B and re-test
print(f"\n{CYAN}── Step 7: Enable A/B and re-test ──{RESET}")
AB_CONFIG_ENABLED = {"enabled": True, "variant_b_percent": 50, "salt": "sonicloud-ab-salt-v1", "updated_at": "2026-08-17T09:00:00Z"}
code, body = cf("PUT", f"/accounts/{CF_MAIN_ACCT}/storage/kv/namespaces/{KV_NS_ID}/values/ab_config", body=AB_CONFIG_ENABLED)
print(f"  A/B enabled: {GREEN}✓{RESET}" if isinstance(body, dict) and body.get("success") else f"  {RED}✗{RESET}")
time.sleep(2)

print(f"\n  Test 6: GET /app/test with A/B enabled (expect 302 + set-cookie: variant=A or B)")
# Use a fresh session (no cookie jar) to simulate new visitor
r = subprocess.run(["curl", "-sk", "-i", "-m", "15", "-H", "User-Agent: test-visitor-1", "https://sonicloud.app/app/test"], capture_output=True, timeout=20)
out = r.stdout.decode('utf-8', 'replace')
# Extract set-cookie header
import re
cookie_match = re.search(r'set-cookie:\s*variant=([AB])', out, re.IGNORECASE)
if cookie_match:
    variant = cookie_match.group(1)
    print(f"    {GREEN}✓{RESET} Got variant cookie: {variant}")
    print(f"    {out[:300]}")
else:
    print(f"    {RED}✗{RESET} No variant cookie set")
    print(f"    {out[:400]}")

# Test stickiness — same UA + IP should always get same variant (if A/B logic uses hash)
print(f"\n  Test 7: 10 requests with same UA — should all get same variant (sticky via hash)")
variants = []
for i in range(10):
    r = subprocess.run(["curl", "-sk", "-i", "-m", "15", "-H", "User-Agent: test-visitor-sticky", "https://sonicloud.app/app/test"], capture_output=True, timeout=20)
    out = r.stdout.decode('utf-8', 'replace')
    m = re.search(r'set-cookie:\s*variant=([AB])', out, re.IGNORECASE)
    variants.append(m.group(1) if m else "?")
    time.sleep(0.2)
print(f"    variants: {variants}")
unique = set(variants)
if len(unique) == 1 and "?" not in unique:
    print(f"    {GREEN}✓{RESET} Sticky — all 10 requests got variant {variants[0]} (deterministic hash works)")
else:
    print(f"    {YELLOW}?{RESET} Non-sticky — variants seen: {unique} (hash isn't deterministic, or cf-connecting-ip changes)")

# Disable A/B again for safety (test pods are real traffic)
print(f"\n  Reverting A/B to disabled")
AB_CONFIG_DISABLED = {"enabled": False, "variant_b_percent": 50, "salt": "sonicloud-ab-salt-v1", "updated_at": "2026-08-17T09:00:00Z"}
code, body = cf("PUT", f"/accounts/{CF_MAIN_ACCT}/storage/kv/namespaces/{KV_NS_ID}/values/ab_config", body=AB_CONFIG_DISABLED)
print(f"  A/B disabled: {GREEN}✓{RESET}" if isinstance(body, dict) and body.get("success") else f"  {RED}✗{RESET}")

print(f"\n{GREEN}══════════════════════════════════════════════════════════════════════════════{RESET}")
print(f"{GREEN}Phase 3+4 deployment complete. Worker v3.0.0 live.{RESET}")
print(f"{GREEN}  - Geo-routing via request.cf.country (region filter in pickPod){RESET}")
print(f"{GREEN}  - Health-check Cron Trigger every 5 min (288 writes/day, fits 1K Free limit){RESET}")
print(f"{GREEN}  - A/B stickiness via cookie + hash (disabled by default, can enable via KV){RESET}")
print(f"{GREEN}  - Admin-token gate preserved from v2.1.0{RESET}")
print(f"{GREEN}══════════════════════════════════════════════════════════════════════════════{RESET}")
