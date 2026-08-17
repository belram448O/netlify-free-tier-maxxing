#!/usr/bin/env python3
"""13_debug_worker_bindings.py — Figure out why KV binding didn't apply.

The previous deploy returned `"bindings": []` even though my metadata included
the KV binding. Let me:
  1. Check current Worker settings (GET /accounts/{id}/workers/scripts/{name}/settings)
  2. Try deploying via the alternative endpoint (PUT /settings with separate binding config)
  3. Try a different multipart format
"""
import json, urllib.request, urllib.error, ssl, time

SECRETS = json.loads(open('/home/z/my-project/scripts/secrets.json').read())
CF_MAIN_TOKEN = SECRETS['root_zone_token']
CF_MAIN_ACCT  = SECRETS['cf_main_account_id']
WORKER_NAME = "sonicloud-root-worker"

GREEN="\033[32m"; RED="\033[31m"; YELLOW="\033[33m"; CYAN="\033[36m"; RESET="\033[0m"

def cf(method, path, body=None, token=None, timeout=30, raw_body=None, content_type=None, accept=None):
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
    if accept:
        req.add_header("Accept", accept)
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

# Step 1: Check current Worker settings
print(f"{CYAN}── Step 1: GET current Worker settings ──{RESET}")
code, body = cf("GET", f"/accounts/{CF_MAIN_ACCT}/workers/scripts/{WORKER_NAME}/settings")
print(f"  HTTP {code}")
if isinstance(body, dict):
    print(f"  success: {body.get('success')}")
    if body.get("result"):
        result = body["result"]
        print(f"  bindings: {json.dumps(result.get('bindings', []), indent=2)}")
        print(f"  usage_model: {result.get('usage_model')}")
        print(f"  compatibility_date: {result.get('compatibility_date')}")

# Step 2: Get the KV namespace ID
print(f"\n{CYAN}── Step 2: Find POD_REGISTRY namespace ID ──{RESET}")
code, body = cf("GET", f"/accounts/{CF_MAIN_ACCT}/storage/kv/namespaces")
ns_list = body.get("result", []) if isinstance(body, dict) else []
pod_ns = next((ns for ns in ns_list if ns.get("title") == "POD_REGISTRY"), None)
if pod_ns:
    print(f"  {GREEN}✓{RESET} POD_REGISTRY id={pod_ns['id']}")
    KV_NS_ID = pod_ns['id']
else:
    print(f"  {RED}✗{RESET} POD_REGISTRY not found")
    exit(1)

# Step 3: Try the alternative — PATCH the Worker's bindings via /settings endpoint
print(f"\n{CYAN}── Step 3: PATCH Worker bindings via /settings ──{RESET}")
settings_body = {
    "bindings": [
        {
            "type": "kv_namespace",
            "name": "POD_REGISTRY",
            "namespace_id": KV_NS_ID,
        }
    ]
}
code, body = cf("PATCH", f"/accounts/{CF_MAIN_ACCT}/workers/scripts/{WORKER_NAME}/settings", body=settings_body)
print(f"  HTTP {code}")
if isinstance(body, dict):
    print(f"  success: {body.get('success')}")
    if body.get("result"):
        print(f"  bindings: {json.dumps(body['result'].get('bindings', []), indent=2)}")
    if body.get("errors"):
        print(f"  errors: {json.dumps(body['errors'], indent=2)}")

# Step 4: Verify
print(f"\n{CYAN}── Step 4: Verify settings ──{RESET}")
code, body = cf("GET", f"/accounts/{CF_MAIN_ACCT}/workers/scripts/{WORKER_NAME}/settings")
if isinstance(body, dict) and body.get("result"):
    print(f"  bindings: {json.dumps(body['result'].get('bindings', []), indent=2)}")

# Step 5: Try a fresh deploy with the correct multipart format
# Maybe the issue is that the multipart body needs to be a proper Python bytes-encoded multipart
# with the right Content-Type per part. Let me use a different multipart construction.
print(f"\n{CYAN}── Step 5: Re-deploy Worker with proper multipart + bindings ──{RESET}")
WORKER_SCRIPT = r"""
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
    <li><a href="/__routes">/__routes</a></li>
    <li><a href="/app/test">/app/test</a></li>
  </ul>
  <div class="meta">
    <strong>Worker:</strong> sonicloud-root-worker (v2 — KV-backed router)<br>
    <strong>Account:</strong> CF MAIN (isolated)<br>
    <strong>Routing:</strong> Worker Routes on apex zone
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
    
    if (url.pathname === '/__health') {
      let podCount = 0;
      let routesRaw = null;
      try {
        routesRaw = await env.POD_REGISTRY.get('routes', { type: 'json' });
        podCount = routesRaw?.routes?.reduce((s, r) => s + r.pods.filter(p => p.active).length, 0) || 0;
      } catch (e) {}
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
    
    if (url.pathname === '/__routes') {
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

# Use the multipart form-data with explicit boundary markers
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

# Construct multipart body properly using bytes
boundary = b"----formdata-boundary-xyz123"
parts = []
# Metadata part
parts.append(b"--" + boundary)
parts.append(b'Content-Disposition: form-data; name="metadata"')
parts.append(b"Content-Type: application/json")
parts.append(b"")
parts.append(json.dumps(metadata).encode())
# Worker script part
parts.append(b"--" + boundary)
parts.append(b'Content-Disposition: form-data; name="worker.js"; filename="worker.js"')
parts.append(b"Content-Type: application/javascript+module")
parts.append(b"")
parts.append(WORKER_SCRIPT.encode())
# Closing boundary
parts.append(b"--" + boundary + b"--")
parts.append(b"")

multipart_body = b"\r\n".join(parts)

print(f"  multipart body size: {len(multipart_body)} bytes")
print(f"  metadata.bindings: {json.dumps(metadata['bindings'])}")

code, body = cf("PUT", f"/accounts/{CF_MAIN_ACCT}/workers/scripts/{WORKER_NAME}",
                raw_body=multipart_body, content_type=f"multipart/form-data; boundary={boundary.decode()}")
if isinstance(body, dict):
    print(f"  HTTP {code}  success={body.get('success')}")
    if body.get("result"):
        result = body["result"]
        print(f"  modified: {result.get('modified_on')}")
        print(f"  bindings: {json.dumps(result.get('bindings', []), indent=2)}")
        print(f"  usage_model: {result.get('usage_model')}")
    if body.get("errors"):
        print(f"  errors: {json.dumps(body['errors'], indent=2)}")
else:
    print(f"  HTTP {code}  body: {str(body)[:400]}")

# Wait for deploy to propagate
print(f"\n  waiting 10s for deploy to propagate...")
time.sleep(10)

# Test
print(f"\n{CYAN}── Step 6: Test endpoints after re-deploy ──{RESET}")
import subprocess
for label, url in [
    ("/__health", "https://sonicloud.app/__health"),
    ("/__routes", "https://sonicloud.app/__routes"),
    ("/app/test (expect 302)", "https://sonicloud.app/app/test"),
]:
    r = subprocess.run(["curl", "-sk", "-i", "-m", "15", url], capture_output=True, timeout=20)
    out = r.stdout.decode('utf-8', 'replace')[:500]
    print(f"\n  {label}:")
    print(f"    {out}")
