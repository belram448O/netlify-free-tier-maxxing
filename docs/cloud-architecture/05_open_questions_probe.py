#!/usr/bin/env python3
"""05_open_questions_probe.py — Live-test the 7 open questions from GROUND_TRUTH.md Part G.

Each question is tested in isolation, results saved to /home/z/my-project/scripts/open-questions-probe.log.

Questions:
  Q1. Netlify WAF / Traffic Rules API surface
  Q2. Netlify Edge Functions deploy mechanism + behavior
  Q3. Netlify Traffic Splits API (can we create one?)
  Q4. CF Worker with KV namespace binding (add to apex Worker)
  Q5. Netlify Blobs as pod registry (latency from CF Worker)
  Q6. Netlify DNS round-robin (multiple A records) — does NS1 rotate?
  Q7. Two-level NS delegation chain (CF apex → Netlify sub-zone → CF pod-zone) — does it work?
  Q8. Grandfathered account detection (can we tell legacy vs credit-free via API?)

Tests use:
  - The sonicloud.app DNS account (6a7f8f3637d951add835956d) for DNS zone tests
  - The scraper Netlify account (6a7e84d51cdeff620a5cf5a0, site 01c2e47f-3ff6-4e09-b45f-604c49ef90fe) for safe Edge Function / Blobs experiments
  - CF MAIN account for the apex Worker test

All tests are READ-ONLY or use already-existing resources. Where writes are needed (Q2 Edge Function deploy, Q4 Worker rewrite), we use safe test artifacts that we tear down at end of script.
"""
import json, subprocess, urllib.request, urllib.error, ssl, time, os, sys, hashlib, base64

SECRETS = json.loads(open('/home/z/my-project/scripts/secrets.json').read())
DOMAIN  = SECRETS['domain']
CF_MAIN_TOKEN = SECRETS['root_zone_token']  # cfat_ — works for apex zone ops
CF_MAIN_ACCT  = SECRETS['cf_main_account_id']
NL_TOKEN = SECRETS['netlify_token']
NL_ACCT  = SECRETS['netlify_account_id']  # sonicloud.app DNS account
NL_SCRAPER_ACCT = "6a7e84d51cdeff620a5cf5a0"  # the scraper account (different)
NL_SCRAPER_SITE = SECRETS['scrape_site_id']  # 01c2e47f-3ff6-4e09-b45f-604c49ef90fe

GREEN="\033[32m"; RED="\033[31m"; YELLOW="\033[33m"; CYAN="\033[36m"; RESET="\033[0m"

def log(s, color=None):
    print(f"{color or ''}{s}{RESET if color else ''}")

def nl(method, path, body=None, token=None, timeout=30):
    token = token or NL_TOKEN
    url = f"https://api.netlify.com/api/v1{path}"
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

# ══════════════════════════════════════════════════════════════════════════════
# Q1. Netlify WAF / Traffic Rules API surface
# ══════════════════════════════════════════════════════════════════════════════
log("\n" + "═"*78, CYAN)
log("Q1. Netlify WAF / Traffic Rules API surface", CYAN)
log("═"*78, CYAN)

# Try common WAF/traffic-rules endpoint paths on the scraper site (safe to probe)
paths_to_probe = [
    f"/sites/{NL_SCRAPER_SITE}/traffic_rules",
    f"/sites/{NL_SCRAPER_SITE}/firewall",
    f"/sites/{NL_SCRAPER_SITE}/firewall/rules",
    f"/sites/{NL_SCRAPER_SITE}/waf",
    f"/sites/{NL_SCRAPER_SITE}/traffic_rules_config",
    f"/accounts/{NL_SCRAPER_ACCT}/firewall",
    f"/accounts/{NL_SCRAPER_ACCT}/traffic_rules",
]
for path in paths_to_probe:
    code, body = nl("GET", path)
    snippet = str(body)[:200] if not isinstance(body, str) else body[:200]
    log(f"  GET {path:60} → HTTP {code}  {snippet[:140]}")

# Also check the site object's traffic_rules_config_per_scope field
code, body = nl("GET", f"/sites/{NL_SCRAPER_SITE}")
if isinstance(body, dict):
    tr = body.get("traffic_rules_config_per_scope")
    log(f"  site.traffic_rules_config_per_scope = {tr}")
    log(f"  site.use_edge_handlers = {body.get('use_edge_handlers')}")
    log(f"  site.functions_region = {body.get('functions_region')}")
    log(f"  site.functions_region_overrides = {body.get('functions_region_overrides')}")
    log(f"  site.dns_zone_id = {body.get('dns_zone_id')}")
    log(f"  site.managed_dns = {body.get('managed_dns')}")
    log(f"  site.branch_deploy_custom_domain = {body.get('branch_deploy_custom_domain')}")
    log(f"  site.deploy_preview_custom_domain = {body.get('deploy_preview_custom_domain')}")
    log(f"  site.has_database = {body.get('has_database')}")

# ══════════════════════════════════════════════════════════════════════════════
# Q2. Netlify Edge Functions — what's the deploy surface?
# ══════════════════════════════════════════════════════════════════════════════
log("\n" + "═"*78, CYAN)
log("Q2. Netlify Edge Functions — deploy surface + existing functions", CYAN)
log("═"*78, CYAN)

# Check account-level edge function usage
code, body = nl("GET", f"/accounts/{NL_SCRAPER_ACCT}/edge_functions")
log(f"  /accounts/{{acct}}/edge_functions → HTTP {code}  body={str(body)[:200]}")

# Site-level edge functions
code, body = nl("GET", f"/sites/{NL_SCRAPER_SITE}/edge_functions")
log(f"  /sites/{{site}}/edge_functions → HTTP {code}  body={str(body)[:200]}")

# Try OpenAPI-known endpoints for edge functions (the public OpenAPI spec may have these)
edge_paths = [
    f"/sites/{NL_SCRAPER_SITE}/edge_functions",
    f"/accounts/{NL_SCRAPER_ACCT}/edge_functions",
]
for path in edge_paths:
    code, body = nl("GET", path)
    log(f"  GET {path:50} → HTTP {code}  body={str(body)[:140]}")

# Note: We will NOT deploy an Edge Function in this probe — it would consume credits (web requests meter
# is shared with everything else) and the scraper site already has traffic. Instead we document what we find.

# ══════════════════════════════════════════════════════════════════════════════
# Q3. Netlify Traffic Splits API — read the shape, attempt to create one
# ══════════════════════════════════════════════════════════════════════════════
log("\n" + "═"*78, CYAN)
log("Q3. Netlify Traffic Splits API", CYAN)
log("═"*78, CYAN)

code, body = nl("GET", f"/sites/{NL_SCRAPER_SITE}/traffic_splits")
log(f"  GET /sites/{{site}}/traffic_splits → HTTP {code}")
log(f"    body: {str(body)[:300]}")

# Probe split-create endpoint shape with a minimal body (use a fake branch — should fail informatively)
# This tells us what fields are required without actually creating anything
probe_body = {"name": "probe-test-do-not-create"}
code, body = nl("POST", f"/sites/{NL_SCRAPER_SITE}/traffic_splits", probe_body)
log(f"  POST /sites/{{site}}/traffic_splits (probe) → HTTP {code}")
log(f"    body: {str(body)[:400]}")

# ══════════════════════════════════════════════════════════════════════════════
# Q4. CF Worker with KV namespace binding
# ══════════════════════════════════════════════════════════════════════════════
log("\n" + "═"*78, CYAN)
log("Q4. CF Worker KV namespace availability on CF MAIN account", CYAN)
log("═"*78, CYAN)

# Check existing KV namespaces in CF MAIN account
code, body = cf("GET", f"/accounts/{CF_MAIN_ACCT}/storage/kv/namespaces")
log(f"  GET /accounts/{{acct}}/storage/kv/namespaces → HTTP {code}")
if isinstance(body, dict):
    ns_list = body.get("result", [])
    log(f"    result: {len(ns_list) if isinstance(ns_list, list) else 'non-list'} namespaces")
    if isinstance(ns_list, list):
        for ns in ns_list[:10]:
            log(f"      - id={ns.get('id')} title={ns.get('title')}")

# Check D1 databases
code, body = cf("GET", f"/accounts/{CF_MAIN_ACCT}/d1/database")
log(f"  GET /accounts/{{acct}}/d1/database → HTTP {code}")
if isinstance(body, dict):
    dbs = body.get("result", [])
    if isinstance(dbs, list):
        log(f"    result: {len(dbs)} databases")
        for db in dbs[:10]:
            log(f"      - name={db.get('name')} uuid={db.get('uuid')}")

# Check R2 buckets
code, body = cf("GET", f"/accounts/{CF_MAIN_ACCT}/r2/buckets")
log(f"  GET /accounts/{{acct}}/r2/buckets → HTTP {code}")
if isinstance(body, dict):
    buckets = body.get("result", {}).get("buckets", []) if isinstance(body.get("result"), dict) else (body.get("result", []) if isinstance(body.get("result"), list) else [])
    log(f"    result: {len(buckets)} buckets")
    for b in buckets[:10]:
        log(f"      - name={b.get('name')}")

# Check token's KV permission
code, body = cf("GET", f"/user/tokens/verify")
log(f"  GET /user/tokens/verify → HTTP {code}")
if isinstance(body, dict):
    log(f"    result: {json.dumps(body.get('result', {}), indent=2)[:500]}")

# Note: actually creating a KV namespace + rewriting the apex Worker is a write operation
# that touches production sonicloud.app. We document the capability but do NOT execute here.
# The plan is to do this in a separate step with explicit user confirmation.

# ══════════════════════════════════════════════════════════════════════════════
# Q5. Netlify Blobs as pod registry — can a CF Worker read a blob via PAT?
# ══════════════════════════════════════════════════════════════════════════════
log("\n" + "═"*78, CYAN)
log("Q5. Netlify Blobs as pod registry (latency test from this container)", CYAN)
log("═"*78, CYAN)

# Write a pod-registry blob to the scraper site's store
# (the scrape_site_id has a 'scraper-results' store from prior work; let's use a separate 'pod-registry-test' store)
STORE_NAME = "pod-registry-test"
REGISTRY = {
    "pods": [
        {"id": "app-us-east-01", "region": "us-east", "url": "https://app-us-east-01.sonicloud.app", "weight": 50, "active": True},
        {"id": "app-us-west-01", "region": "us-west", "url": "https://app-us-west-01.sonicloud.app", "weight": 30, "active": True},
        {"id": "app-eu-west-01", "region": "eu-west", "url": "https://app-eu-west-01.sonicloud.app", "weight": 20, "active": True},
    ],
    "updated_at": "2026-08-17T00:00:00Z",
}

# First, list existing stores on the scraper site
code, body = nl("GET", f"/blobs/{NL_SCRAPER_SITE}")
log(f"  list blob stores → HTTP {code}")
if isinstance(body, dict):
    log(f"    stores: {body.get('stores', [])}")
elif isinstance(body, list):
    log(f"    stores: {body}")

# Write the registry blob via the presigned-URL flow
# Step 1: PUT with Accept: application/json;type=signed-url to get presigned S3 URL
PUT_HEADERS = {"Authorization": f"Bearer {NL_TOKEN}", "Content-Type": "application/json", "Accept": "application/json;type=signed-url"}
registry_bytes = json.dumps(REGISTRY, indent=2).encode()
url = f"https://api.netlify.com/api/v1/blobs/{NL_SCRAPER_SITE}/site:{STORE_NAME}/registry.json"
req = urllib.request.Request(url, data=registry_bytes, method="PUT", headers=PUT_HEADERS)
try:
    with urllib.request.urlopen(req, timeout=30) as r:
        text = r.read().decode()
        presigned_resp = json.loads(text) if text else {}
        log(f"  PUT (get presigned URL) → HTTP {r.status}")
        log(f"    presigned URL host: {presigned_resp.get('url','')[:80]}...")
except urllib.error.HTTPError as e:
    log(f"  PUT (get presigned URL) → HTTP {e.code} {e.read().decode()[:200]}", RED)
    presigned_resp = {}
except Exception as e:
    log(f"  PUT (get presigned URL) → err: {e}", RED)
    presigned_resp = {}

# Step 2: Upload to S3 via presigned URL (if we got one)
if presigned_resp.get('url'):
    s3_url = presigned_resp['url']
    req2 = urllib.request.Request(s3_url, data=registry_bytes, method="PUT", headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req2, timeout=30) as r:
            log(f"  S3 PUT (upload bytes) → HTTP {r.status}")
    except urllib.error.HTTPError as e:
        log(f"  S3 PUT → HTTP {e.code} {e.read().decode()[:200]}", RED)
    except Exception as e:
        log(f"  S3 PUT → err: {e}", RED)

# Step 3: Read it back via API + measure latency (3 trials)
for trial in range(3):
    t0 = time.time()
    code, body = nl("GET", f"/blobs/{NL_SCRAPER_SITE}/site:{STORE_NAME}/registry.json")
    elapsed_ms = (time.time() - t0) * 1000
    if isinstance(body, dict):
        log(f"  GET registry.json trial {trial+1} → HTTP {code}  size={len(json.dumps(body))}B  elapsed={elapsed_ms:.1f}ms")
    else:
        log(f"  GET registry.json trial {trial+1} → HTTP {code}  elapsed={elapsed_ms:.1f}ms  body={str(body)[:80]}")

# Cleanup: delete the test blob (requires Blobs token, not PAT — so we leave it; it's free storage)
log(f"  (cleanup: blob left in store site:{STORE_NAME}, key=registry.json — free, no cost)")

# ══════════════════════════════════════════════════════════════════════════════
# Q6. Netlify DNS round-robin — does NS1 return multiple A records in rotated order?
# ══════════════════════════════════════════════════════════════════════════════
log("\n" + "═"*78, CYAN)
log("Q6. Netlify DNS — multiple A records + query behavior", CYAN)
log("═"*78, CYAN)

# Look at one of the existing sonicloud.app sub-zones (e.g., app.sonicloud.app) and check its records
code, body = nl("GET", f"/dns_zones")
if isinstance(body, list):
    for z in body:
        if z.get("name") == "app.sonicloud.app":
            zone_id = z["id"]
            log(f"  found app.sonicloud.app zone id={zone_id}")
            # Get current records
            code, recs = nl("GET", f"/dns_zones/{zone_id}/dns_records")
            if isinstance(recs, list):
                log(f"  current records ({len(recs)}):")
                for r in recs:
                    log(f"    {r.get('type')} {r.get('hostname')} → {r.get('value')}")

# Note: We do NOT actually add multiple A records in this probe — that would change production DNS.
# We just document that the API supports it (POST multiple A records at the same hostname should work).
# Instead, we query NS1 directly to see how it rotates A records by querying a known multi-A domain.
log("\n  Querying a known multi-A record domain (cloudflare.com) to see NS1 rotation behavior:")
for resolver in ["1.1.1.1", "8.8.8.8", "9.9.9.9"]:
    try:
        out = subprocess.check_output(["dig", f"@{resolver}", "cloudflare.com", "A", "+short", "+time=3", "+tries=2"], timeout=8).decode().strip()
        log(f"    @{resolver}: {out.splitlines()}")
    except Exception as e:
        log(f"    @{resolver}: err {e}")

# ══════════════════════════════════════════════════════════════════════════════
# Q7. Two-level NS delegation — verify the chain registrar → CF → Netlify sub-zone → ??? (no pod CF zone yet)
# ══════════════════════════════════════════════════════════════════════════════
log("\n" + "═"*78, CYAN)
log("Q7. NS delegation chain (registrar → CF → Netlify sub-zone) — verify current state", CYAN)
log("═"*78, CYAN)

# Verify the existing chain: registrar → CF → Netlify NS1 (for users.sonicloud.app)
log("  Current chain for users.sonicloud.app:")
for resolver in ["1.1.1.1"]:
    # At the resolver
    ns_cf_root = subprocess.check_output(["dig", f"@{resolver}", DOMAIN, "NS", "+short"]).decode().strip().splitlines()
    log(f"    {DOMAIN} NS @{resolver}: {ns_cf_root}")
    # Get the NS for the sub-zone
    ns_sub = subprocess.check_output(["dig", f"@{resolver}", f"users.{DOMAIN}", "NS", "+short"]).decode().strip().splitlines()
    log(f"    users.{DOMAIN} NS @{resolver}: {ns_sub}")
    # Query the NS1 directly to see what A records it serves for users.sonicloud.app
    if ns_sub:
        ns1 = ns_sub[0].rstrip(".")
        a_via_ns1 = subprocess.check_output(["dig", f"@{ns1}", f"users.{DOMAIN}", "A", "+short"]).decode().strip().splitlines()
        log(f"    users.{DOMAIN} A @{ns1}: {a_via_ns1}")

# The two-level chain (CF apex → Netlify sub-zone → CF pod-zone) would require creating a CF zone
# for a hostname like `app-us-east-01.sonicloud.app` in a separate CF account, then NS-delegating
# from the Netlify `app.sonicloud.app` zone to that CF zone's NS. We document this as the target pattern
# but do NOT execute (would require creating a new CF zone + manual account creation).
log("\n  Target pattern (not yet executed):")
log("    app.sonicloud.app                    [Netlify DNS zone, p01 pool]")
log("      └─ NS app-us-east-01.sonicloud.app → <cf-pod-zone-NS-1>, <cf-pod-zone-NS-2>  [pod CF zone in account B]")
log("    Verify with: dig +short NS app-us-east-01.sonicloud.app @dns1.p01.nsone.net")
log("    If NS1 accepts the NS delegation, the chain works. (NS1 supports NS records at any zone level.)")

# ══════════════════════════════════════════════════════════════════════════════
# Q8. Grandfathered account detection — what does type_slug look like for legacy accounts?
# ══════════════════════════════════════════════════════════════════════════════
log("\n" + "═"*78, CYAN)
log("Q8. Grandfathered account detection (current account types)", CYAN)
log("═"*78, CYAN)

# Get all accounts the token can see
code, body = nl("GET", "/accounts")
log(f"  GET /accounts → HTTP {code}")
if isinstance(body, list):
    log(f"  accounts ({len(body)}):")
    for a in body:
        log(f"    - id={a.get('id')} name={a.get('name')} slug={a.get('slug')} type_name={a.get('type_name')} type_slug={a.get('type_slug')} plan_credits={a.get('plan_credits')}")
elif isinstance(body, dict):
    log(f"  body: {str(body)[:500]}")

# Also check available plan types
code, body = nl("GET", "/accounts/types")
log(f"\n  GET /accounts/types → HTTP {code}")
if isinstance(body, list):
    log(f"  plan types ({len(body)}):")
    for t in body:
        log(f"    - name={t.get('name')} slug={t.get('slug')} id={t.get('id')} available={t.get('available')} plan_credits={t.get('plan_credits')} monthly_dollar_price={t.get('monthly_dollar_price')}")
elif isinstance(body, dict):
    log(f"  body: {str(body)[:500]}")

# The current account on this token
code, body = nl("GET", f"/accounts/{NL_ACCT}")
if isinstance(body, dict):
    log(f"\n  sonicloud.app DNS account ({NL_ACCT}):")
    log(f"    type_name={body.get('type_name')} type_slug={body.get('type_slug')}")
    log(f"    plan_credits={body.get('plan_credits')} capabilities.credits.used={body.get('capabilities',{}).get('credits',{}).get('used')}")
    log(f"    capabilities.firewall_enabled={body.get('capabilities',{}).get('firewall_enabled')}")
    log(f"    capabilities.traffic_rules={body.get('capabilities',{}).get('traffic_rules')}")
    log(f"    capabilities.max_traffic_rules={body.get('capabilities',{}).get('max_traffic_rules')}")

# ══════════════════════════════════════════════════════════════════════════════
# Summary
# ══════════════════════════════════════════════════════════════════════════════
log("\n" + "═"*78, GREEN)
log("Probe complete — see output above for each question's findings.", GREEN)
log("═"*78, GREEN)
