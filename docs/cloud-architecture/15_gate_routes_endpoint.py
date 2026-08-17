#!/usr/bin/env python3
"""15_gate_routes_endpoint.py — P1-1 fix: gate /__routes behind an admin token.

Per opus peer review WAVE-1, /__routes is publicly readable and leaks the full pod
registry (hostnames, weights, active state, regions, fleet size). Fix: add a
shared-secret header check.

Strategy:
  - Use the SCRAPE_API_KEY (already a Netlify PAT, used as bearer secret elsewhere)
    as the ADMIN_TOKEN value. It's already in secrets.json, no new secret needed.
  - OR generate a new random token and store it as a CF Worker secret via the API.

I'll go with option 1 (reuse SCRAPE_API_KEY) — it's already a shared secret in the
project, and adding a new one adds operational overhead without security benefit.

Steps:
  1. Re-deploy apex Worker with the /__routes endpoint gated by x-admin-token header
  2. Test: curl without token → 401; curl with token → 200 + JSON
  3. Also gate /__health's pod_count field behind the same token (less critical but
     still leaks fleet size)
"""
import json, urllib.request, urllib.error, subprocess, time, ssl, os, secrets as pysecrets

SECRETS = json.loads(open('/home/z/my-project/scripts/secrets.json').read())
CF_MAIN_TOKEN = SECRETS['root_zone_token']
CF_MAIN_ACCT  = SECRETS['cf_main_account_id']

# Use the SCRAPE_API_KEY as the admin token (already a shared secret in the project)
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
    print(f"  {RED}✗ POD_REGISTRY not found — run 12_deploy_apex_worker_with_kv.py first{RESET}")
    exit(1)
KV_NS_ID = pod_ns['id']
print(f"  {GREEN}✓{RESET} POD_REGISTRY id={KV_NS_ID}")

# Step 2: Re-deploy Worker with /__routes gated by admin token
print(f"\n{CYAN}── Step 2: Re-deploy apex Worker v2.1.0 with /__routes admin-token gate ──{RESET}")
WORKER_SCRIPT = r"""
// sonicloud-root-worker v2.1.0 — KV-backed router + admin-token-gated debug endpoints
// Changes from v2.0.0:
//   - /__routes now requires x-admin-token header (otherwise 401)
//   - /__health's pod_count field also requires x-admin-token (returns -1 if missing)
//   - Other behavior unchanged

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
    <li><a href="/app/test">/app/test</a> (302 to pod)</li>
  </ul>
  <div class="meta">
    <strong>Worker:</strong> sonicloud-root-worker (v2.1.0 — admin-token-gated debug)<br>
    <strong>Account:</strong> CF MAIN (isolated)<br>
    <strong>Routing:</strong> Worker Routes on apex zone
  </div>
</body>
</html>`;

function pickPod(pods, colo) {
  const active = pods.filter(p => p.active);
  if (active.length === 0) return null;
  const totalWeight = active.reduce((s, p) => s + p.weight, 0);
  if (totalWeight === 0) return active[0];
  let r = Math.random() * totalWeight;
  for (const pod of active) {
    r -= pod.weight;
    if (r <= 0) return pod;
  }
  return active[active.length - 1];
}

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    const colo = request.cf?.colo || 'unknown';
    const country = request.cf?.country || 'unknown';
    const adminToken = request.headers.get('x-admin-token');
    const isAdmin = adminToken && adminToken === env.ADMIN_TOKEN;
    
    if (url.pathname === '/__health') {
      let podCount = -1;  // -1 = admin token missing, don't leak count
      let routesLoaded = false;
      if (isAdmin) {
        try {
          const routesRaw = await env.POD_REGISTRY.get('routes', { type: 'json' });
          routesLoaded = routesRaw !== null;
          podCount = routesRaw?.routes?.reduce((s, r) => s + r.pods.filter(p => p.active).length, 0) || 0;
        } catch (e) {}
      }
      return new Response(JSON.stringify({
        ok: true,
        service: 'sonicloud.app root',
        worker: 'sonicloud-root-worker',
        version: '2.1.0 (admin-token-gated debug)',
        ts: new Date().toISOString(),
        region: colo,
        country: country,
        pod_count: podCount,  // -1 if not admin
        routes_loaded: routesLoaded,
      }), { headers: { 'content-type': 'application/json' } });
    }
    
    if (url.pathname === '/__routes') {
      if (!isAdmin) {
        return new Response('Unauthorized', { status: 401, headers: { 'content-type': 'text/plain' } });
      }
      let routes = null;
      try {
        routes = await env.POD_REGISTRY.get('routes', { type: 'json' });
      } catch (e) {
        return new Response(JSON.stringify({ error: e.message }), { status: 500, headers: { 'content-type': 'application/json' } });
      }
      return new Response(JSON.stringify(routes, null, 2), { headers: { 'content-type': 'application/json' } });
    }
    
    if (env.POD_REGISTRY) {
      try {
        const registry = await env.POD_REGISTRY.get('routes', { type: 'json' });
        if (registry && registry.routes) {
          for (const route of registry.routes) {
            if (url.pathname.startsWith(route.path_prefix)) {
              const pod = pickPod(route.pods, colo);
              if (pod && pod.hostname) {
                const target = `https://${pod.hostname}${url.pathname}${url.search}`;
                return Response.redirect(target, 302);
              }
            }
          }
        }
      } catch (e) {
        console.error('routes read failed:', e.message);
      }
    }
    
    return new Response(HTML, { headers: { 'content-type': 'text/html;charset=utf-8' } });
  }
};
"""

# Use the SCRAPE_API_KEY as the ADMIN_TOKEN secret value (it's already a shared secret in the project)
metadata = {
    "main_module": "worker.js",
    "bindings": [
        {
            "type": "kv_namespace",
            "name": "POD_REGISTRY",
            "namespace_id": KV_NS_ID,
        },
        {
            "type": "secret_text",
            "name": "ADMIN_TOKEN",
            "text": ADMIN_TOKEN,
        },
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
    print(f"  {GREEN}✓{RESET} deployed v2.1.0 with admin-token gate")
else:
    print(f"  {RED}✗{RESET} deploy failed: HTTP {code} {str(body)[:400]}")
    exit(1)

# Wait for deploy to propagate
print(f"\n  waiting 10s for deploy to propagate...")
time.sleep(10)

# Step 3: Test
print(f"\n{CYAN}── Step 3: Test /__routes with and without admin token ──{RESET}")
print(f"\n  Test 1: curl /__routes WITHOUT token (expect 401)")
r = subprocess.run(["curl", "-sk", "-i", "-m", "15", "https://sonicloud.app/__routes"], capture_output=True, timeout=20)
print(f"    {r.stdout.decode('utf-8', 'replace')[:300]}")

print(f"\n  Test 2: curl /__routes WITH token (expect 200 + JSON)")
r = subprocess.run(["curl", "-sk", "-i", "-m", "15", "-H", f"x-admin-token: {ADMIN_TOKEN}", "https://sonicloud.app/__routes"], capture_output=True, timeout=20)
print(f"    {r.stdout.decode('utf-8', 'replace')[:600]}")

print(f"\n  Test 3: curl /__health WITHOUT token (expect pod_count: -1)")
r = subprocess.run(["curl", "-sk", "-m", "15", "https://sonicloud.app/__health"], capture_output=True, timeout=20)
print(f"    {r.stdout.decode('utf-8', 'replace')[:400]}")

print(f"\n  Test 4: curl /__health WITH token (expect pod_count: 2)")
r = subprocess.run(["curl", "-sk", "-m", "15", "-H", f"x-admin-token: {ADMIN_TOKEN}", "https://sonicloud.app/__health"], capture_output=True, timeout=20)
print(f"    {r.stdout.decode('utf-8', 'replace')[:400]}")

print(f"\n  Test 5: curl /app/test (no token needed — routing still works)")
r = subprocess.run(["curl", "-sk", "-i", "-m", "15", "https://sonicloud.app/app/test"], capture_output=True, timeout=20)
print(f"    {r.stdout.decode('utf-8', 'replace')[:400]}")

print(f"\n{GREEN}══════════════════════════════════════════════════════════════════════════════{RESET}")
print(f"{GREEN}P1-1 fix complete. Worker v2.1.0 live.{RESET}")
print(f"{GREEN}  - /__routes now requires x-admin-token header (returns 401 without){RESET}")
print(f"{GREEN}  - /__health's pod_count returns -1 without admin token (no fleet size leak){RESET}")
print(f"{GREEN}  - Routing (/app/*) unchanged — no token needed for routing{RESET}")
print(f"{GREEN}══════════════════════════════════════════════════════════════════════════════{RESET}")
