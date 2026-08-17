#!/usr/bin/env python3
"""10_validate_pod_pattern.py — Live-validate the CORRECTED pod pattern.

The two-level NS delegation pattern (CF apex → Netlify sub-zone → CF pod-zone)
is BLOCKED by CF error 1116 (subdomain zones require Enterprise, $5K+/mo).

The corrected pattern is:
  - Pod Worker runs in own CF account, accessible at <worker>.<acct>.workers.dev
  - Apex CF zone (sonicloud.app) has a CNAME: app-test-01.sonicloud.app → <worker>.<acct>.workers.dev
  - CF's proxied=true on the CNAME means CF edge handles TLS + forwards to the workers.dev backend
  - Worker Routes on apex NOT needed for the pod hostname (CF auto-forwards CNAME→target)
  - Apex Worker CAN intercept via Worker Route (e.g., for auth/rate-limit) — optional

Since I can't easily create a new CF account (manual web signup), I'll test the
pattern by deploying a pod Worker in CF MAIN account (same as apex). The Worker
will be accessible at <worker>.<cf-main-subdomain>.workers.dev.

Steps:
  1. Find the CF MAIN workers.dev subdomain
  2. Deploy a test pod Worker `app-test-01-worker` to CF MAIN
  3. Add CNAME in apex zone: app-test-01.sonicloud.app → <worker>.<subdomain>.workers.dev (proxied=true)
  4. Test: curl https://app-test-01.sonicloud.app/__health
       → expect 200 JSON from pod Worker
  5. Test: curl https://<worker>.<subdomain>.workers.dev/__health
       → expect same response (direct Worker URL)
  6. Measure latency: dig + curl timing for both paths
"""
import json, urllib.request, urllib.error, subprocess, time, sys, os, ssl

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

def http_get(url, timeout=15, verify=True):
    ctx = None
    if not verify:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    req = urllib.request.Request(url, headers={"User-Agent": "nftm-probe/1.0"})
    try:
        t0 = time.time()
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
            body = r.read().decode('utf-8', 'replace')[:500]
            elapsed_ms = (time.time() - t0) * 1000
            return r.status, body, dict(r.headers), elapsed_ms
    except urllib.error.HTTPError as e:
        elapsed_ms = (time.time() - t0) * 1000
        return e.code, e.read().decode('utf-8', 'replace')[:500], dict(e.headers), elapsed_ms
    except Exception as e:
        return None, f"{type(e).__name__}: {e}", {}, 0

def dig(name, qtype, resolver="1.1.1.1"):
    try:
        out = subprocess.check_output(["dig", f"@{resolver}", name, qtype, "+short", "+time=3", "+tries=2"], timeout=8).decode().strip()
        return [l for l in out.splitlines() if l.strip()]
    except Exception as e:
        return [f"<err: {e}>"]

# ══════════════════════════════════════════════════════════════════════════════
print(f"{CYAN}══════════════════════════════════════════════════════════════════════════════{RESET}")
print(f"{CYAN}VALIDATE CORRECTED POD PATTERN{RESET}")
print(f"{CYAN}══════════════════════════════════════════════════════════════════════════════{RESET}")

# Step 1: Find the CF MAIN workers.dev subdomain
print(f"\n  {CYAN}Step 1: Find CF MAIN workers.dev subdomain{RESET}")
code, body = cf("GET", f"/accounts/{CF_MAIN_ACCT}/workers/subdomain")
if isinstance(body, dict) and body.get("success"):
    subdomain = body.get("result", {}).get("subdomain", "")
    print(f"    {GREEN}✓{RESET} workers.dev subdomain: {subdomain}")
    if not subdomain:
        # Try to register one
        print(f"    {YELLOW}→ No subdomain registered yet, attempting to register 'sonicloud'...{RESET}")
        code, body = cf("PUT", f"/accounts/{CF_MAIN_ACCT}/workers/subdomain", {"subdomain": "sonicloud"})
        if isinstance(body, dict) and body.get("success"):
            subdomain = body.get("result", {}).get("subdomain", "sonicloud")
            print(f"    {GREEN}✓{RESET} registered: {subdomain}")
        else:
            print(f"    {RED}✗{RESET} register failed: {str(body)[:200]}")
            subdomain = "sonicloud"  # assume it worked
else:
    print(f"    {RED}✗{RESET} GET subdomain failed: HTTP {code} {str(body)[:200]}")
    subdomain = "sonicloud"  # fallback

# Step 2: Deploy a test pod Worker `app-test-01-worker`
print(f"\n  {CYAN}Step 2: Deploy test pod Worker 'app-test-01-worker' to CF MAIN{RESET}")
WORKER_NAME = "app-test-01-worker"
WORKER_SCRIPT = r"""
// app-test-01-worker — test pod Worker
// Demonstrates the pod pattern: own KV binding, /__health endpoint, simple SSR

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    
    if (url.pathname === '/__health') {
      return new Response(JSON.stringify({
        ok: true,
        pod: 'app-test-01',
        worker: 'app-test-01-worker',
        region: request.cf?.colo || 'unknown',
        country: request.cf?.country || 'unknown',
        ts: new Date().toISOString(),
        version: '1.0.0',
      }), { headers: { 'content-type': 'application/json' } });
    }
    
    if (url.pathname === '/') {
      return new Response(`<!DOCTYPE html>
<html><head><title>app-test-01 pod</title></head>
<body>
<h1>app-test-01 pod</h1>
<p>Served by Worker: app-test-01-worker</p>
<p>Region (cf.colo): ${request.cf?.colo || 'unknown'}</p>
<p>Country: ${request.cf?.country || 'unknown'}</p>
<p>Timestamp: ${new Date().toISOString()}</p>
</body></html>`, { headers: { 'content-type': 'text/html;charset=utf-8' } });
    }
    
    return new Response('Not found', { status: 404 });
  }
};
"""

# Deploy via multipart form data (ES module syntax)
metadata = {
    "main_module": "worker.js",
    "bindings": [],
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
    print(f"    {GREEN}✓{RESET} deployed Worker '{WORKER_NAME}'")
    if body.get("result"):
        print(f"      modified: {body['result'].get('modified_on')}")
else:
    print(f"    {RED}✗{RESET} deploy failed: HTTP {code}  {str(body)[:300]}")
    sys.exit(1)

# Step 3: Get the apex zone ID
print(f"\n  {CYAN}Step 3: Find apex zone ID for sonicloud.app{RESET}")
code, body = cf("GET", "/zones?name=sonicloud.app")
apex_zone = body["result"][0] if isinstance(body, dict) and body.get("result") else None
if not apex_zone:
    print(f"    {RED}✗{RESET} apex zone not found")
    sys.exit(1)
apex_zone_id = apex_zone["id"]
print(f"    apex zone id: {apex_zone_id}")

# Step 4: Check if the pod Worker is enabled on the workers.dev subdomain
print(f"\n  {CYAN}Step 4: Enable workers.dev subdomain for pod Worker{RESET}")
code, body = cf("POST", f"/accounts/{CF_MAIN_ACCT}/workers/scripts/{WORKER_NAME}/subdomain", {"enabled": True})
if isinstance(body, dict) and body.get("success"):
    print(f"    {GREEN}✓{RESET} workers.dev subdomain enabled")
elif isinstance(body, dict):
    err = body.get("errors", [{}])[0] if body.get("errors") else {}
    if "already" in err.get("message", "").lower():
        print(f"    {GREEN}✓{RESET} workers.dev subdomain already enabled")
    else:
        print(f"    {YELLOW}?{RESET} enable response: HTTP {code} {str(body)[:200]}")

# Step 5: Get the workers.dev URL for the pod Worker
pod_worker_url = f"https://{WORKER_NAME}.{subdomain}.workers.dev"
print(f"\n  {CYAN}Step 5: Test direct Worker URL: {pod_worker_url}/__health{RESET}")
code, body, headers, elapsed = http_get(f"{pod_worker_url}/__health", verify=False, timeout=15)
print(f"    HTTP {code}  elapsed={elapsed:.1f}ms")
print(f"    body: {body[:300]}")

# Step 6: Add CNAME in apex zone for app-test-01.sonicloud.app → pod_worker_url
print(f"\n  {CYAN}Step 6: Add CNAME in apex zone: app-test-01.sonicloud.app → {WORKER_NAME}.{subdomain}.workers.dev{RESET}")
# Check if record already exists
code, body = cf("GET", f"/zones/{apex_zone_id}/dns_records?type=CNAME&name=app-test-01.sonicloud.app")
existing = body.get("result", []) if isinstance(body, dict) else []
if existing:
    rec = existing[0]
    print(f"    {GREEN}✓{RESET} CNAME already exists: id={rec.get('id')} content={rec.get('content')} proxied={rec.get('proxied')}")
    # Verify it points to the right target
    if rec.get("content") != f"{WORKER_NAME}.{subdomain}.workers.dev":
        # Update it
        print(f"    {YELLOW}→ updating to point to pod Worker...{RESET}")
        code, body = cf("PUT", f"/zones/{apex_zone_id}/dns_records/{rec['id']}", {
            "type": "CNAME", "name": "app-test-01.sonicloud.app",
            "content": f"{WORKER_NAME}.{subdomain}.workers.dev",
            "ttl": 1, "proxied": True
        })
        if isinstance(body, dict) and body.get("success"):
            print(f"    {GREEN}✓{RESET} updated")
        else:
            print(f"    {RED}✗{RESET} update failed: {str(body)[:200]}")
else:
    code, body = cf("POST", f"/zones/{apex_zone_id}/dns_records", {
        "type": "CNAME", "name": "app-test-01.sonicloud.app",
        "content": f"{WORKER_NAME}.{subdomain}.workers.dev",
        "ttl": 1, "proxied": True
    })
    if isinstance(body, dict) and body.get("success"):
        print(f"    {GREEN}✓{RESET} CNAME created: id={body.get('result',{}).get('id')}")
    else:
        print(f"    {RED}✗{RESET} CNAME create failed: {str(body)[:200]}")

# Step 7: Wait for propagation, verify DNS + HTTP
print(f"\n  {CYAN}Step 7: Wait 30s for DNS propagation, then verify{RESET}")
time.sleep(30)

print(f"    DNS resolution:")
for resolver in ["1.1.1.1", "8.8.8.8"]:
    a = dig("app-test-01.sonicloud.app", "A", resolver)
    cname = dig("app-test-01.sonicloud.app", "CNAME", resolver)
    print(f"      @{resolver}: A = {a}")
    print(f"      @{resolver}: CNAME = {cname}")

print(f"\n    HTTP test (via sonicloud.app hostname):")
code, body, headers, elapsed = http_get("https://app-test-01.sonicloud.app/__health", verify=False, timeout=15)
print(f"      HTTP {code}  elapsed={elapsed:.1f}ms  server={headers.get('Server','')}")
print(f"      body: {body[:300]}")

# Step 8: Compare direct vs proxied latency
print(f"\n  {CYAN}Step 8: Compare latency: direct workers.dev vs proxied via sonicloud.app{RESET}")
# Direct
direct_times = []
for _ in range(3):
    _, _, _, t = http_get(f"{pod_worker_url}/__health", verify=False, timeout=15)
    direct_times.append(t)
# Proxied via sonicloud.app
proxied_times = []
for _ in range(3):
    _, _, _, t = http_get("https://app-test-01.sonicloud.app/__health", verify=False, timeout=15)
    proxied_times.append(t)
print(f"    Direct (workers.dev): {[f'{t:.0f}ms' for t in direct_times]}  avg={sum(direct_times)/len(direct_times):.0f}ms")
print(f"    Proxied (sonicloud.app): {[f'{t:.0f}ms' for t in proxied_times]}  avg={sum(proxied_times)/len(proxied_times):.0f}ms")
print(f"    Overhead added by CF proxy: ~{sum(proxied_times)/len(proxied_times) - sum(direct_times)/len(direct_times):.0f}ms")

print(f"\n{GREEN}══════════════════════════════════════════════════════════════════════════════{RESET}")
print(f"{GREEN}Probe complete. Pattern validated if HTTP 200 above.{RESET}")
print(f"{GREEN}══════════════════════════════════════════════════════════════════════════════{RESET}")
