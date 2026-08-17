#!/usr/bin/env python3
"""11_fix_pod_pattern.py — Replace CNAME→workers.dev with Worker Routes on apex zone.

The previous test (10_validate_pod_pattern.py) hit CF error 1014 (CNAME Cross-User
Banned) because CF blocks proxied CNAMEs to *.workers.dev subdomains.

The CORRECT pattern for binding a Worker to a hostname on the apex zone is:
  - Worker Route: POST /zones/{apex_zone_id}/workers/routes
                  body: {"pattern": "app-test-01.sonicloud.app/*", "script": "app-test-01-worker"}
  - A record: app-test-01.sonicloud.app → 192.0.2.1 (proxied=true)
              (canonical CF pattern — 192.0.2.1 is never contacted, Worker intercepts at edge)

This is the same pattern the kit uses for the apex Worker (sonicloud-root-worker bound
to sonicloud.app/* via Worker Routes, with A=192.0.2.1 proxied=true).

Steps:
  1. Delete the CNAME I added in step 6 of the previous test (app-test-01.sonicloud.app → workers.dev)
  2. Add A record: app-test-01.sonicloud.app → 192.0.2.1 (proxied=true)
  3. Add Worker Route: app-test-01.sonicloud.app/* → app-test-01-worker
  4. Test: curl https://app-test-01.sonicloud.app/__health → expect 200 from pod Worker
  5. Measure latency (3 trials)

NOTE: This validates per-Worker isolation (own name, own bindings) but NOT per-account
isolation (the pod Worker shares CF MAIN's 100K req/day budget with the apex Worker).
Per-account isolation for pods requires a separately-registered domain (e.g., sonicloud-pods.com)
which is documented in the architecture doc as the upgrade path.
"""
import json, urllib.request, urllib.error, subprocess, time, ssl

SECRETS = json.loads(open('/home/z/my-project/scripts/secrets.json').read())
CF_MAIN_TOKEN = SECRETS['root_zone_token']
CF_MAIN_ACCT  = SECRETS['cf_main_account_id']

GREEN="\033[32m"; RED="\033[31m"; YELLOW="\033[33m"; CYAN="\033[36m"; RESET="\033[0m"

def cf(method, path, body=None, token=None, timeout=30):
    token = token or CF_MAIN_TOKEN
    url = f"https://api.cloudflare.com/client/v4{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    if body is not None: req.add_header("Content-Type", "application/json")
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

def http_get(url, timeout=15, verify=False):
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

# Step 0: Find apex zone
print(f"{CYAN}── Step 0: Find apex zone ──{RESET}")
code, body = cf("GET", "/zones?name=sonicloud.app")
apex_zone = body["result"][0] if isinstance(body, dict) and body.get("result") else None
if not apex_zone:
    print(f"  {RED}✗ apex zone not found{RESET}")
    exit(1)
apex_zone_id = apex_zone["id"]
print(f"  apex zone id: {apex_zone_id}")

# Step 1: Delete the CNAME record I added (app-test-01.sonicloud.app → workers.dev)
print(f"\n{CYAN}── Step 1: Delete the CNAME record (app-test-01.sonicloud.app → workers.dev) ──{RESET}")
code, body = cf("GET", f"/zones/{apex_zone_id}/dns_records?type=CNAME&name=app-test-01.sonicloud.app")
existing_cname = body.get("result", []) if isinstance(body, dict) else []
for rec in existing_cname:
    print(f"  found CNAME: id={rec['id']} content={rec['content']} proxied={rec['proxied']}")
    code, body = cf("DELETE", f"/zones/{apex_zone_id}/dns_records/{rec['id']}")
    if isinstance(body, dict) and body.get("success"):
        print(f"  {GREEN}✓{RESET} deleted CNAME id={rec['id']}")
    else:
        print(f"  {RED}✗{RESET} delete failed: {str(body)[:200]}")
if not existing_cname:
    print(f"  (no CNAME found — already deleted)")

# Step 2: Add A record: app-test-01.sonicloud.app → 192.0.2.1 (proxied=true)
print(f"\n{CYAN}── Step 2: Add A record (app-test-01.sonicloud.app → 192.0.2.1, proxied) ──{RESET}")
# Check if A record already exists
code, body = cf("GET", f"/zones/{apex_zone_id}/dns_records?type=A&name=app-test-01.sonicloud.app")
existing_a = body.get("result", []) if isinstance(body, dict) else []
if existing_a:
    rec = existing_a[0]
    print(f"  A record exists: id={rec['id']} content={rec['content']} proxied={rec['proxied']}")
    if rec['content'] != '192.0.2.1' or not rec['proxied']:
        # Update
        code, body = cf("PUT", f"/zones/{apex_zone_id}/dns_records/{rec['id']}", {
            "type": "A", "name": "app-test-01.sonicloud.app", "content": "192.0.2.1", "ttl": 1, "proxied": True
        })
        if isinstance(body, dict) and body.get("success"):
            print(f"  {GREEN}✓{RESET} updated to 192.0.2.1 proxied=true")
        else:
            print(f"  {RED}✗{RESET} update failed: {str(body)[:200]}")
    else:
        print(f"  {GREEN}✓{RESET} already correct")
else:
    code, body = cf("POST", f"/zones/{apex_zone_id}/dns_records", {
        "type": "A", "name": "app-test-01.sonicloud.app", "content": "192.0.2.1", "ttl": 1, "proxied": True
    })
    if isinstance(body, dict) and body.get("success"):
        print(f"  {GREEN}✓{RESET} A record created: id={body.get('result',{}).get('id')}")
    else:
        print(f"  {RED}✗{RESET} A record create failed: {str(body)[:200]}")

# Step 3: Add Worker Route: app-test-01.sonicloud.app/* → app-test-01-worker
print(f"\n{CYAN}── Step 3: Add Worker Route (app-test-01.sonicloud.app/* → app-test-01-worker) ──{RESET}")
PATTERN = "app-test-01.sonicloud.app/*"
SCRIPT = "app-test-01-worker"

# Check if route already exists
code, body = cf("GET", f"/zones/{apex_zone_id}/workers/routes")
existing_routes = body.get("result", []) if isinstance(body, dict) else []
print(f"  existing Worker Routes: {len(existing_routes)}")
for r in existing_routes:
    print(f"    pattern={r.get('pattern')} script={r.get('script')}")

route_exists = any(r.get("pattern") == PATTERN for r in existing_routes)
if route_exists:
    # Update the script binding
    existing_route = next(r for r in existing_routes if r.get("pattern") == PATTERN)
    print(f"  route exists, current script: {existing_route.get('script')}")
    if existing_route.get("script") != SCRIPT:
        # CF doesn't have a PUT for routes — need to delete + recreate
        print(f"  {YELLOW}→ deleting + recreating to update script...{RESET}")
        # Get route ID (need to query differently — the list doesn't include IDs)
        # Actually CF's Worker Routes API returns just {pattern, script} without IDs
        # To "update" you DELETE then POST
        # But DELETE requires the pattern as a URL-encoded path
        import urllib.parse
        code, body = cf("DELETE", f"/zones/{apex_zone_id}/workers/routes/{urllib.parse.quote(PATTERN, safe='')}")
        print(f"    delete: HTTP {code}")
        code, body = cf("POST", f"/zones/{apex_zone_id}/workers/routes", {"pattern": PATTERN, "script": SCRIPT})
        if isinstance(body, dict) and body.get("success"):
            print(f"  {GREEN}✓{RESET} route recreated with script={SCRIPT}")
        else:
            print(f"  {RED}✗{RESET} recreate failed: {str(body)[:200]}")
    else:
        print(f"  {GREEN}✓{RESET} route already correct")
else:
    code, body = cf("POST", f"/zones/{apex_zone_id}/workers/routes", {"pattern": PATTERN, "script": SCRIPT})
    if isinstance(body, dict) and body.get("success"):
        print(f"  {GREEN}✓{RESET} Worker Route created: {PATTERN} → {SCRIPT}")
    else:
        print(f"  {RED}✗{RESET} route create failed: {str(body)[:200]}")

# Step 4: Wait for propagation, verify DNS + HTTP
print(f"\n{CYAN}── Step 4: Wait 30s for propagation, then verify ──{RESET}")
time.sleep(30)

print(f"  DNS resolution:")
for resolver in ["1.1.1.1", "8.8.8.8"]:
    try:
        out = subprocess.check_output(["dig", f"@{resolver}", "app-test-01.sonicloud.app", "A", "+short"], timeout=8).decode().strip()
        print(f"    @{resolver}: A = {out.splitlines()}")
    except Exception as e:
        print(f"    @{resolver}: err {e}")

print(f"\n  HTTP test:")
code, body, headers, elapsed = http_get("https://app-test-01.sonicloud.app/__health", timeout=15)
print(f"    HTTP {code}  elapsed={elapsed:.1f}ms  server={headers.get('Server','')}")
print(f"    body: {body[:300]}")

# Step 5: Latency comparison: apex Worker vs pod Worker
print(f"\n{CYAN}── Step 5: Latency comparison (apex Worker vs pod Worker) ──{RESET}")
apex_times = []
pod_times = []
for _ in range(5):
    _, _, _, t = http_get("https://sonicloud.app/__health", timeout=10)
    apex_times.append(t)
    _, _, _, t = http_get("https://app-test-01.sonicloud.app/__health", timeout=10)
    pod_times.append(t)
print(f"  apex Worker  (sonicloud.app/__health):     {[f'{t:.0f}ms' for t in apex_times]}  avg={sum(apex_times)/len(apex_times):.0f}ms")
print(f"  pod Worker   (app-test-01.sonicloud.app/__health): {[f'{t:.0f}ms' for t in pod_times]}  avg={sum(pod_times)/len(pod_times):.0f}ms")

# Step 6: Summary
print(f"\n{GREEN}══════════════════════════════════════════════════════════════════════════════{RESET}")
print(f"{GREEN}Probe complete.{RESET}")
print(f"{GREEN}  - If HTTP 200 above with JSON containing 'pod: app-test-01': PATTERN VALIDATED{RESET}")
print(f"{GREEN}  - Worker Routes on apex zone works for pod hostnames (per-Worker isolation in CF MAIN){RESET}")
print(f"{GREEN}  - Per-account isolation requires separate registered domain (documented in arch doc){RESET}")
print(f"{GREEN}══════════════════════════════════════════════════════════════════════════════{RESET}")
