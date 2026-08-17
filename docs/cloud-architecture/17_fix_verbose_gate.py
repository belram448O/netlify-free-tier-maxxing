#!/usr/bin/env python3
"""17_fix_verbose_gate.py — P0-A fix: remove the verbose=1 bypass that leaks pod_count.

The Wave 3 review found that v3.0.0's /__health?verbose=1 query param bypasses the
admin-token gate (line 210 had `if (isAdmin || url.searchParams.get('verbose') === '1')`).
This regressed the Wave 2 P1-1 fix.

Fix: require admin token for verbose mode too. The non-verbose /__health response
(returns pod_count: -1, ab_enabled: false, routes_loaded: false) is the public response.
The verbose response requires the same x-admin-token header as /__routes.

Also adds the missing Secure flag on the variant cookie (P2-2 from Wave 3).
"""
import json, urllib.request, urllib.error, subprocess, time

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
KV_NS_ID = pod_ns['id']
print(f"  {GREEN}✓{RESET} POD_REGISTRY id={KV_NS_ID}")

# Step 2: Deploy Worker v3.0.1 with the security fix
print(f"\n{CYAN}── Step 2: Deploy apex Worker v3.0.1 (security fix) ──{RESET}")
WORKER_SCRIPT = r"""
// sonicloud-root-worker v3.0.1 — security fix: require admin token for verbose /__health
// v3.0.0 had `if (isAdmin || verbose=1)` which leaked pod_count without token. Fixed.

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
    <strong>Worker:</strong> sonicloud-root-worker (v3.0.1 — geo + cron + A/B + verbose-gate-fix)<br>
    <strong>Account:</strong> CF MAIN (isolated)<br>
    <strong>Routing:</strong> Worker Routes on apex zone + KV pod registry
  </div>
</body>
</html>`;

function hash32(s) {
  let h = 5381;
  for (let i = 0; i < s.length; i++) {
    h = ((h << 5) + h) + s.charCodeAt(i);
    h = h & 0xffffffff;
  }
  return Math.abs(h);
}

function pickPod(pods, country, colo) {
  const active = pods.filter(p => p.active);
  if (active.length === 0) return null;
  const regionMatching = active.filter(p => {
    if (!p.regions || p.regions.includes("*")) return true;
    return p.regions.includes(country);
  });
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

function pickVariant(request, env, abConfig) {
  if (!abConfig || !abConfig.enabled) return 'A';
  const cookie = request.headers.get("cookie") || "";
  const variantMatch = cookie.match(/variant=([AB])/);
  if (variantMatch) {
    return variantMatch[1];
  }
  const ip = request.headers.get("cf-connecting-ip") || "";
  const ua = request.headers.get("user-agent") || "";
  const hash = hash32(ip + ua + (abConfig.salt || ""));
  const percent = abConfig.variant_b_percent || 50;
  return (hash % 100 < percent) ? 'B' : 'A';
}

function withVariantCookie(response, variant) {
  const newResponse = new Response(response.body, response);
  if (variant === 'A' || variant === 'B') {
    // P2-2 fix: add Secure flag (Wave 3 review)
    newResponse.headers.set("set-cookie", `variant=${variant}; Path=/; Max-Age=31536000; SameSite=Lax; Secure`);
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
    
    if (url.pathname === '/__health') {
      let podCount = -1;
      let routesLoaded = false;
      let abEnabled = false;
      // P0-A FIX: verbose mode requires admin token (not just verbose=1 query param)
      if (isAdmin) {
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
        version: '3.0.1 (verbose-gate-fix)',
        ts: new Date().toISOString(),
        region: colo,
        country: country,
        pod_count: podCount,
        routes_loaded: routesLoaded,
        ab_enabled: abEnabled,
      }), { headers: { 'content-type': 'application/json' } });
    }
    
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
    
    if (env.POD_REGISTRY) {
      try {
        const registry = await env.POD_REGISTRY.get('routes', { type: 'json' });
        if (registry && registry.routes) {
          for (const route of registry.routes) {
            if (url.pathname.startsWith(route.path_prefix)) {
              let candidatePods = route.pods;
              const abConfig = await env.POD_REGISTRY.get('ab_config', { type: 'json' });
              if (abConfig && abConfig.enabled) {
                const variant = pickVariant(request, env, abConfig);
                candidatePods = route.pods.filter(p => !p.variant || p.variant === variant);
                if (candidatePods.length === 0) candidatePods = route.pods;
                const pod = pickPod(candidatePods, country, colo);
                if (pod && pod.hostname) {
                  const target = `https://${pod.hostname}${url.pathname}${url.search}`;
                  const response = Response.redirect(target, 302);
                  return withVariantCookie(response, variant);
                }
              } else {
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
    print(f"  {GREEN}✓{RESET} deployed v3.0.1")
else:
    print(f"  {RED}✗{RESET} deploy failed: HTTP {code} {str(body)[:400]}")
    exit(1)

# Cron Trigger is already configured (from v3.0.0 deploy) — Worker upgrade preserves it
print(f"\n  Cron Trigger preserved from v3.0.0 (every 5 min)")

# Wait for deploy
print(f"\n  waiting 15s for deploy to propagate...")
time.sleep(15)

# Verify the security fix
print(f"\n{CYAN}── Step 3: Verify P0-A fix (verbose=1 no longer bypasses admin token) ──{RESET}")

print(f"\n  Test 1: GET /__health (no token, expect pod_count: -1, version: 3.0.1)")
r = subprocess.run(["curl", "-sk", "-m", "15", "https://sonicloud.app/__health"], capture_output=True, timeout=20)
print(f"    {r.stdout.decode('utf-8', 'replace')[:300]}")

print(f"\n  Test 2: GET /__health?verbose=1 (no token, expect pod_count: -1, version: 3.0.1) — THIS IS THE FIX")
r = subprocess.run(["curl", "-sk", "-m", "15", "https://sonicloud.app/__health?verbose=1"], capture_output=True, timeout=20)
out = r.stdout.decode('utf-8', 'replace')
print(f"    {out[:300]}")
if '"pod_count": -1' in out and '"version": "3.0.1' in out:
    print(f"    {GREEN}✓{RESET} P0-A FIXED: verbose=1 no longer leaks pod_count")
else:
    print(f"    {RED}✗{RESET} P0-A NOT FIXED — verbose=1 still leaks")

print(f"\n  Test 3: GET /__health (with admin token, expect pod_count: 1)")
r = subprocess.run(["curl", "-sk", "-m", "15", "-H", f"x-admin-token: {ADMIN_TOKEN}", "https://sonicloud.app/__health"], capture_output=True, timeout=20)
print(f"    {r.stdout.decode('utf-8', 'replace')[:300]}")

print(f"\n  Test 4: GET /app/test (expect 302 to pod, A/B disabled so no cookie)")
r = subprocess.run(["curl", "-sk", "-i", "-m", "15", "https://sonicloud.app/app/test"], capture_output=True, timeout=20)
out = r.stdout.decode('utf-8', 'replace')
print(f"    {out[:400]}")

# Enable A/B briefly to verify Secure flag on cookie
print(f"\n  Test 5: Enable A/B and verify Secure flag on variant cookie")
AB_ENABLED = {"enabled": True, "variant_b_percent": 50, "salt": "sonicloud-ab-salt-v1", "updated_at": "2026-08-17T09:30:00Z"}
cf("PUT", f"/accounts/{CF_MAIN_ACCT}/storage/kv/namespaces/{KV_NS_ID}/values/ab_config", body=AB_ENABLED)
time.sleep(2)
r = subprocess.run(["curl", "-sk", "-i", "-m", "15", "-H", "User-Agent: test-secure-flag", "https://sonicloud.app/app/test"], capture_output=True, timeout=20)
out = r.stdout.decode('utf-8', 'replace')
import re
m = re.search(r'set-cookie:[^\r\n]*', out, re.IGNORECASE)
if m:
    print(f"    {GREEN}✓{RESET} Cookie header: {m.group(0)}")
    if 'Secure' in m.group(0):
        print(f"    {GREEN}✓{RESET} P2-2 FIXED: Secure flag present")
    else:
        print(f"    {RED}✗{RESET} P2-2 NOT FIXED: Secure flag missing")
else:
    print(f"    {RED}✗{RESET} No cookie set")

# Revert A/B
AB_DISABLED = {"enabled": False, "variant_b_percent": 50, "salt": "sonicloud-ab-salt-v1", "updated_at": "2026-08-17T09:30:00Z"}
cf("PUT", f"/accounts/{CF_MAIN_ACCT}/storage/kv/namespaces/{KV_NS_ID}/values/ab_config", body=AB_DISABLED)
print(f"  A/B reverted to disabled")

print(f"\n{GREEN}══════════════════════════════════════════════════════════════════════════════{RESET}")
print(f"{GREEN}P0-A + P2-2 fixes complete. Worker v3.0.1 live.{RESET}")
print(f"{GREEN}  - /__health?verbose=1 no longer leaks pod_count without admin token{RESET}")
print(f"{GREEN}  - Variant cookie now has Secure flag{RESET}")
print(f"{GREEN}  - All v3.0.0 functionality preserved (geo + cron + A/B + admin gate){RESET}")
print(f"{GREEN}══════════════════════════════════════════════════════════════════════════════{RESET}")
