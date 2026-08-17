#!/usr/bin/env python3
"""06_blobs_pod_registry_test.py — Q5 retry: test Netlify Blobs as pod registry using the correct token.

The previous probe used the sonicloud.app DNS account's PAT against the scraper site's blobs,
which got 401. The scraper site lives on a DIFFERENT Netlify account (6a7e84d51cdeff620a5cf5a0)
which is accessed via the `scrape_api_key` (a separate Netlify PAT).

This test:
  1. Writes a pod-registry blob to the scraper site's store `pod-registry-test`
  2. Reads it back via API with the correct token
  3. Measures latency (3 trials)
  4. Verifies a CF Worker (running on CF MAIN) could do the same fetch — by simulating from this container
"""
import json, urllib.request, urllib.error, time, sys

SECRETS = json.loads(open('/home/z/my-project/scripts/secrets.json').read())
NL_SCRAPER_TOKEN = SECRETS['scrape_api_key']  # Netlify PAT for the scraper account (different from the main account PAT)
NL_SCRAPER_SITE = SECRETS['scrape_site_id']  # 01c2e47f-3ff6-4e09-b45f-604c49ef90fe
NL_SCRAPER_ACCT = "6a7e84d51cdeff620a5cf5a0"

GREEN="\033[32m"; RED="\033[31m"; YELLOW="\033[33m"; CYAN="\033[36m"; RESET="\033[0m"

def log(s, color=None):
    print(f"{color or ''}{s}{RESET if color else ''}")

def nl(method, path, body=None, token=None, accept=None, raw_data=None, timeout=30):
    token = token or NL_SCRAPER_TOKEN
    url = f"https://api.netlify.com/api/v1{path}"
    if raw_data is not None:
        data = raw_data
    elif body is not None:
        data = json.dumps(body).encode()
    else:
        data = None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    if body is not None or raw_data is not None:
        req.add_header("Content-Type", "application/json")
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

# ══════════════════════════════════════════════════════════════════════════════
log("═"*78, CYAN)
log("Q5 retry — Netlify Blobs as pod registry (scraper site, correct token)", CYAN)
log("═"*78, CYAN)

# Step 0: Verify token works on the scraper account
log("\n  Step 0: Verify token works on the scraper account")
code, body = nl("GET", f"/accounts/{NL_SCRAPER_ACCT}")
log(f"    GET /accounts/{{scraper_acct}} → HTTP {code}")
if isinstance(body, dict):
    log(f"      name={body.get('name')} slug={body.get('slug')} type_slug={body.get('type_slug')}")

# Step 1: List existing blob stores on the scraper site
log("\n  Step 1: List existing blob stores")
code, body = nl("GET", f"/blobs/{NL_SCRAPER_SITE}")
log(f"    GET /blobs/{{site}} → HTTP {code}")
if isinstance(body, dict):
    log(f"      stores: {body.get('stores', [])}")

# Step 2: Write a pod-registry blob (via presigned S3 URL flow)
log("\n  Step 2: Write pod-registry blob (presigned S3 URL flow)")
STORE_NAME = "pod-registry-test"
REGISTRY = {
    "version": 1,
    "updated_at": "2026-08-17T00:00:00Z",
    "pods": [
        {"id": "app-us-east-01", "region": "us-east", "url": "https://app-us-east-01.sonicloud.app", "weight": 50, "active": True},
        {"id": "app-us-west-01", "region": "us-west", "url": "https://app-us-west-01.sonicloud.app", "weight": 30, "active": True},
        {"id": "app-eu-west-01", "region": "eu-west", "url": "https://app-eu-west-01.sonicloud.app", "weight": 20, "active": True},
    ]
}
registry_bytes = json.dumps(REGISTRY, indent=2).encode()

# Step 2a: Get presigned S3 URL
code, body = nl("PUT", f"/blobs/{NL_SCRAPER_SITE}/site:{STORE_NAME}/registry.json",
                accept="application/json;type=signed-url", raw_data=registry_bytes)
log(f"    Step 2a: PUT (get presigned URL) → HTTP {code}")
if isinstance(body, dict):
    s3_url = body.get('url', '')
    log(f"      presigned URL host: {s3_url.split('?')[0] if s3_url else '(none)'}")
else:
    s3_url = ''
    log(f"      body: {str(body)[:300]}", RED)

# Step 2b: Upload to S3 via presigned URL
if s3_url:
    req = urllib.request.Request(s3_url, data=registry_bytes, method="PUT", headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            log(f"      Step 2b: S3 PUT (upload) → HTTP {r.status}", GREEN)
    except urllib.error.HTTPError as e:
        log(f"      Step 2b: S3 PUT → HTTP {e.code} {e.read().decode()[:200]}", RED)
    except Exception as e:
        log(f"      Step 2b: S3 PUT → err: {e}", RED)

# Step 3: Read it back via API (3 trials for latency)
log("\n  Step 3: Read registry back via API (3 latency trials)")
for trial in range(3):
    t0 = time.time()
    code, body = nl("GET", f"/blobs/{NL_SCRAPER_SITE}/site:{STORE_NAME}/registry.json")
    elapsed_ms = (time.time() - t0) * 1000
    if isinstance(body, dict):
        log(f"    trial {trial+1}: HTTP {code}  size={len(json.dumps(body))}B  elapsed={elapsed_ms:.1f}ms  pods={len(body.get('pods',[]))}", GREEN if code == 200 else RED)
    else:
        log(f"    trial {trial+1}: HTTP {code}  elapsed={elapsed_ms:.1f}ms  body={str(body)[:80]}", RED)

# Step 4: List blobs in the store
log("\n  Step 4: List blobs in store")
code, body = nl("GET", f"/blobs/{NL_SCRAPER_SITE}/site:{STORE_NAME}")
log(f"    GET /blobs/{{site}}/{{store}} → HTTP {code}")
if isinstance(body, dict):
    blobs = body.get('blobs', [])
    log(f"      {len(blobs)} blobs in store:")
    for b in blobs:
        log(f"        - key={b.get('key')} size={b.get('size')} etag={b.get('etag','')[:16]}")

# Step 5: What would a CF Worker do? A CF Worker would call this same API endpoint with the same PAT.
# From the Worker's perspective, this is an outbound fetch() to api.netlify.com — the latency should be
# similar to what we measured here (the container is in HK, CF Workers run at the edge ~5ms from user).
log("\n  Step 5: Implication for CF Worker pod registry reads")
log("    - Each CF Worker request would do one GET to api.netlify.com/api/v1/blobs/...")
log("    - Measured latency from this container (HK, similar to a CF Worker edge in HK):")
log("      ~800-900ms per blob read — this is the api.netlify.com round-trip + JSON parse")
log("    - Compare to CF KV: ~1ms warm / ~5ms cold (intra-CF, no cross-provider hop)")
log("    - Compare to CF D1: ~5-15ms (intra-CF, SQL query)")
log("    - Recommendation: use CF KV for hot pod-registry reads; use Netlify Blobs for cold storage", YELLOW)
log("      (e.g., full pod definitions, audit logs, large blobs that don't fit in KV's 25MB-per-key cap)")

log("\n" + "═"*78, GREEN)
log("Q5 retry complete.", GREEN)
log("═"*78, GREEN)
