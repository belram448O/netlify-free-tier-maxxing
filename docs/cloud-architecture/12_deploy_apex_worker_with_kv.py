#!/usr/bin/env python3
"""12_deploy_apex_worker_with_kv.py — Deploy new apex Worker with KV pod registry + routing.

This is the Phase 1 implementation from CLOUD_ARCHITECTURE.md §1.4 + §8.

Steps:
  1. Create KV namespace 'POD_REGISTRY' in CF MAIN account
  2. Seed the KV with a pod registry JSON
  3. Deploy new apex Worker 'sonicloud-root-worker' (overwrite) with:
     - KV binding: POD_REGISTRY
     - Routing logic: /app/* → 302 to chosen pod (geo-aware via request.cf.colo)
     - Health: /__health returns pod count + region info
     - Landing: / returns HTML (keep existing placeholder for now)
  4. Test: curl https://sonicloud.app/__health → should show pod count
  5. Test: curl https://sonicloud.app/app/test → should 302 to https://app-test-01.sonicloud.app/test
  6. Test: curl -L https://sonicloud.app/app/__health → should follow 302 to pod Worker's __health

The Worker keeps the existing placeholder HTML at / (backward compat), but adds:
  - /__health: enriched with pod_count from KV
  - /app/* : 302 redirect to chosen pod (geo-aware)
  - /api/* : 302 redirect to api pod (when configured)
  - /__routes : debug endpoint showing current pod registry
"""
import json, urllib.request, urllib.error, subprocess, time, ssl

SECRETS = json.loads(open('/home/z/my-project/scripts/secrets.json').read())
CF_MAIN_TOKEN = SECRETS['root_zone_token']
CF_MAIN_ACCT  = SECRETS['cf_main_account_id']

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

def http_get(url, timeout=15, verify=False, follow_redirects=False):
    ctx = None
    if not verify:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    req = urllib.request.Request(url, headers={"User-Agent": "nftm-probe/1.0"})
    try:
        t0 = time.time()
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
            body = r.read().decode('utf-8', 'replace')[:600]
            elapsed_ms = (time.time() - t0) * 1000
            return r.status, body, dict(r.headers), elapsed_ms
    except urllib.error.HTTPError as e:
        elapsed_ms = (time.time() - t0) * 1000
        # Capture 3xx responses (urllib raises on redirects if follow=False)
        return e.code, e.read().decode('utf-8', 'replace')[:600], dict(e.headers), elapsed_ms
    except Exception as e:
        return None, f"{type(e).__name__}: {e}", {}, 0

# Step 1: Find or create KV namespace 'POD_REGISTRY'
print(f"{CYAN}── Step 1: Find or create KV namespace 'POD_REGISTRY' ──{RESET}")
code, body = cf("GET", f"/accounts/{CF_MAIN_ACCT}/storage/kv/namespaces")
existing_ns = body.get("result", []) if isinstance(body, dict) else []
pod_registry_ns = next((ns for ns in existing_ns if ns.get("title") == "POD_REGISTRY"), None)
if pod_registry_ns:
    print(f"  {GREEN}✓{RESET} POD_REGISTRY namespace exists: id={pod_registry_ns['id']}")
else:
    code, body = cf("POST", f"/accounts/{CF_MAIN_ACCT}/storage/kv/namespaces", {"title": "POD_REGISTRY"})
    if isinstance(body, dict) and body.get("success"):
        pod_registry_ns = body.get("result", {})
        print(f"  {GREEN}✓{RESET} created POD_REGISTRY namespace: id={pod_registry_ns['id']}")
    else:
        print(f"  {RED}✗{RESET} create failed: {str(body)[:200]}")
        exit(1)
KV_NS_ID = pod_registry_ns['id']

# Step 2: Seed the KV with a pod registry JSON
print(f"\n{CYAN}── Step 2: Seed POD_REGISTRY with routes ──{RESET}")
ROUTES = {
    "version": 1,
    "updated_at": "2026-08-17T00:00:00Z",
    "routes": [
        {
            "path_prefix": "/app/",
            "pods": [
                {
                    "hostname": "app-test-01.sonicloud.app",
                    "weight": 100,
                    "active": True,
                    "regions": ["*"]  # wildcard — accept all
                }
            ]
        },
        {
            "path_prefix": "/api/",
            "pods": [
                {
                    "hostname": "api.sonicloud.app",
                    "weight": 100,
                    "active": True,
                    "regions": ["*"]
                }
            ]
        }
    ]
}
code, body = cf("PUT", f"/accounts/{CF_MAIN_ACCT}/storage/kv/namespaces/{KV_NS_ID}/values/routes", body=ROUTES)
if isinstance(body, dict) and body.get("success"):
    print(f"  {GREEN}✓{RESET} seeded 'routes' key in POD_REGISTRY")
else:
    print(f"  {RED}✗{RESET} seed failed: {str(body)[:200]}")

# Verify by reading back
code, body = cf("GET", f"/accounts/{CF_MAIN_ACCT}/storage/kv/namespaces/{KV_NS_ID}/values/routes")
if isinstance(body, dict):
    print(f"  {GREEN}✓{RESET} read back: {len(json.dumps(body))} bytes, {len(body.get('routes',[]))} routes")
else:
    print(f"  {RED}✗{RESET} read back failed: {str(body)[:200]}")

# Step 3: Deploy new apex Worker with KV binding + routing
print(f"\n{CYAN}── Step 3: Deploy new apex Worker 'sonicloud-root-worker' with KV binding + routing ──{RESET}")
WORKER_NAME = "sonicloud-root-worker"
WORKER_SCRIPT = r"""
// sonicloud-root-worker — apex Worker with KV-backed pod registry + geo routing
// Phase 1 implementation per CLOUD_ARCHITECTURE.md §1.4 + §8

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
    .pod { background: #f0f7ff; padding: 1rem; border-radius: 4px; margin: 0.5rem 0; }
  </style>
</head>
<body>
  <h1>sonicloud.app</h1>
  <p>Apex site. Routing compute lives here; content lives on subdomains.</p>
  <p>Try:</p>
  <ul>
    <li><a href="/__health">/__health</a> — health check + pod count</li>
    <li><a href="/__routes">/__routes</a> — current pod registry</li>
    <li><a href="/app/test">/app/test</a> — 302 redirect to geo-routed app pod</li>
  </ul>
  <div class="meta">
    <strong>Worker:</strong> sonicloud-root-worker (v2 — KV-backed router)<br>
    <strong>Account:</strong> CF MAIN (isolated)<br>
    <strong>Routing:</strong> Worker Routes on apex zone
  </div>
</body>
</html>`;

// Helper: hash a string to a 32-bit int (for weighted random pick)
function hash32(s) {
  let h = 5381;
  for (let i = 0; i < s.length; i++) {
    h = ((h << 5) + h) + s.charCodeAt(i);
    h = h & 0xffffffff;
  }
  return Math.abs(h);
}

// Helper: pick a pod by weight (optionally filtered by region)
function pickPod(pods, colo) {
  const active = pods.filter(p => p.active);
  if (active.length === 0) return null;
  
  // Region match: prefer pods whose regions include the colo's country (or "*")
  // For simplicity, all pods have regions ["*"] in v1 — just weighted random.
  // TODO v2: filter by request.cf.country matching pod.regions
  
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
    
    // Health check
    if (url.pathname === '/__health') {
      let podCount = 0;
      let routesRaw = null;
      try {
        routesRaw = await env.POD_REGISTRY.get('routes', { type: 'json' });
        podCount = routesRaw?.routes?.reduce((s, r) => s + r.pods.filter(p => p.active).length, 0) || 0;
      } catch (e) {
        // KV might be unavailable — fail open
      }
      return new Response(JSON.stringify({
        ok: true,
        service: 'sonicloud.app root',
        worker: 'sonicloud-root-worker',
        version: '2.0.0 (KV-backed router)',
        ts: new Date().toISOString(),
        region: colo,
        country: country,
        pod_count: podCount,
        routes_loaded: routesRaw !== null,
      }), { headers: { 'content-type': 'application/json' } });
    }
    
    // Debug endpoint — show current pod registry
    if (url.pathname === '/__routes') {
      let routes = null;
      try {
        routes = await env.POD_REGISTRY.get('routes', { type: 'json' });
      } catch (e) {
        return new Response(JSON.stringify({ error: e.message }), { status: 500, headers: { 'content-type': 'application/json' } });
      }
      return new Response(JSON.stringify(routes, null, 2), { headers: { 'content-type': 'application/json' } });
    }
    
    // Routing: /app/*, /api/*, etc.
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
        // KV read failed — fall through to default landing
        console.error('routes read failed:', e.message);
      }
    }
    
    // Default: serve landing page
    return new Response(HTML, { headers: { 'content-type': 'text/html;charset=utf-8' } });
  },
  
  // Optional scheduled handler for health-check failover (Phase 3)
  // async scheduled(event, env, ctx) { ... }
};
"""

# Deploy with KV binding
metadata = {
    "main_module": "worker.js",
    "bindings": [
        {
            "type": "kv_namespace",
            "name": "POD_REGISTRY",
            "namespace_id": KV_NS_ID,
        }
    ],
    "compatibility_date": "2024-09-23",
    "compatibility_flags": ["nodejs_compat"],
}
boundary = "----formdata-boundary-abc123"
multipart_body = (
    f"--{boundary}\r\n"
    f"Content-Disposition: form-data; name=\"metadata\"\r\n"
    f"Content-Type: application/json\r\n\r\n"
    f"{json.dumps(metadata)}\r\n"
    f"--{boundary}\r\n"
    f"Content-Disposition: form-data; name=\"worker.js\"; filename=\"worker.js\"\r\n"
    f"Content-Type: application/javascript+module\r\n\r\n"
    f"{WORKER_SCRIPT}\r\n"
    f"--{boundary}--\r\n"
).encode()

code, body = cf("PUT", f"/accounts/{CF_MAIN_ACCT}/workers/scripts/{WORKER_NAME}",
                raw_body=multipart_body, content_type=f"multipart/form-data; boundary={boundary}")
if isinstance(body, dict) and body.get("success"):
    print(f"  {GREEN}✓{RESET} deployed new apex Worker '{WORKER_NAME}' with KV binding")
    if body.get("result"):
        print(f"    modified: {body['result'].get('modified_on')}")
        print(f"    bindings: {body['result'].get('bindings', [])}")
else:
    print(f"  {RED}✗{RESET} deploy failed: HTTP {code}  {str(body)[:400]}")
    exit(1)

# Step 4: Test endpoints
print(f"\n{CYAN}── Step 4: Test endpoints ──{RESET}")

print(f"\n  Test 1: GET /__health (enriched with pod_count)")
code, body, headers, elapsed = http_get("https://sonicloud.app/__health", timeout=15)
print(f"    HTTP {code}  elapsed={elapsed:.1f}ms")
print(f"    body: {body[:400]}")

print(f"\n  Test 2: GET /__routes (debug — show pod registry)")
code, body, headers, elapsed = http_get("https://sonicloud.app/__routes", timeout=15)
print(f"    HTTP {code}  elapsed={elapsed:.1f}ms")
print(f"    body: {body[:400]}")

print(f"\n  Test 3: GET /app/test (expect 302 to https://app-test-01.sonicloud.app/test)")
code, body, headers, elapsed = http_get("https://sonicloud.app/app/test", timeout=15)
loc = headers.get("Location") or headers.get("location") or ""
print(f"    HTTP {code}  elapsed={elapsed:.1f}ms  Location: {loc}")

print(f"\n  Test 4: GET /app/__health (follow 302 → pod Worker's __health)")
# Use curl with -L to follow redirect
import subprocess
r = subprocess.run(["curl", "-skL", "-i", "-m", "15", "https://sonicloud.app/app/__health"], capture_output=True, timeout=20)
out = r.stdout.decode('utf-8', 'replace')[:800]
print(f"    curl -skL -i https://sonicloud.app/app/__health:")
print(f"    {out}")

print(f"\n  Test 5: GET / (landing page — backward compat)")
code, body, headers, elapsed = http_get("https://sonicloud.app/", timeout=15)
print(f"    HTTP {code}  elapsed={elapsed:.1f}ms  body_len={len(body)}")
print(f"    body (first 200): {body[:200]}")

# Step 5: Latency test
print(f"\n{CYAN}── Step 5: Latency test (5 trials each) ──{RESET}")
tests = [
    ("apex /__health", "https://sonicloud.app/__health"),
    ("apex /__routes", "https://sonicloud.app/__routes"),
    ("apex /app/__health (302 + pod response)", "https://sonicloud.app/app/__health"),
    ("pod /__health (direct)", "https://app-test-01.sonicloud.app/__health"),
]
for label, url in tests:
    times = []
    for _ in range(5):
        if "302" in label:
            # Use curl -L to follow redirect
            r = subprocess.run(["curl", "-skL", "-o", "/dev/null", "-w", "%{time_total}", "-m", "15", url], capture_output=True, timeout=20)
            try:
                t = float(r.stdout.decode().strip()) * 1000
                times.append(t)
            except:
                times.append(0)
        else:
            _, _, _, t = http_get(url, timeout=15)
            times.append(t)
    print(f"  {label:50}  avg={sum(times)/len(times):.0f}ms  min={min(times):.0f}ms  max={max(times):.0f}ms")

print(f"\n{GREEN}══════════════════════════════════════════════════════════════════════════════{RESET}")
print(f"{GREEN}Phase 1 deployment complete. Apex Worker now has:{RESET}")
print(f"{GREEN}  - KV binding (POD_REGISTRY namespace){RESET}")
print(f"{GREEN}  - /app/* routing → 302 to chosen pod (currently app-test-01.sonicloud.app){RESET}")
print(f"{GREEN}  - /__health enriched with pod_count + region info{RESET}")
print(f"{GREEN}  - /__routes debug endpoint{RESET}")
print(f"{GREEN}══════════════════════════════════════════════════════════════════════════════{RESET}")
