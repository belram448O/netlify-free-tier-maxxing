#!/usr/bin/env python3
"""04_retrieve_apex_worker.py — Retrieve the current apex Worker code + settings.

Reads sonicloud-root-worker script + bindings + routes, to understand what the apex site does today.
"""
import json, urllib.request, urllib.error

SECRETS = json.loads(open('/home/z/my-project/scripts/secrets.json').read())
ACCT = SECRETS['cf_main_account_id']
TOK = SECRETS['root_zone_token']
DOMAIN = SECRETS['domain']

def cf(method, path, token=None):
    token = token or TOK
    r = urllib.request.Request(f"https://api.cloudflare.com/client/v4{path}", method=method,
                                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())
    except Exception as e:
        return None, {"_err": str(e)}

# Get zone
code, body = cf("GET", f"/zones?name={DOMAIN}")
zone_id = body["result"][0]["id"]
print(f"Zone: {DOMAIN} id={zone_id}\n")

# Worker script content
code, body = cf("GET", f"/accounts/{ACCT}/workers/scripts/sonicloud-root-worker")
print(f"=== Worker script HTTP {code} ===")
if isinstance(body, dict) and body.get("result"):
    res = body["result"]
    if isinstance(res, dict):
        print(f"  script: {res.get('script','<binary>')[:800] if isinstance(res.get('script'), str) else '<non-string>'}")
        print(f"  bindings: {json.dumps(res.get('bindings',[]), indent=2)[:1000]}")
        print(f"  usage_model: {res.get('usage_model')}")
        print(f"  compatibility_date: {res.get('compatibility_date')}")
        print(f"  modified: {res.get('modified_on')}")
        print(f"  etag: {res.get('etag')}")
    else:
        print(f"  raw result: {str(res)[:1500]}")
else:
    print(f"  body: {str(body)[:1500]}")

# Worker script content (raw)
print(f"\n=== Worker script content (raw, /content) ===")
code, body = cf("GET", f"/accounts/{ACCT}/workers/scripts/sonicloud-root-worker/content")
if isinstance(body, dict):
    print(f"  HTTP {code}")
    if body.get("success"):
        # body['result'] is the script body as a string
        script_text = body.get('result', '')
        if isinstance(script_text, str):
            print(script_text[:3000])
        else:
            print(f"  result type: {type(script_text).__name__}, value: {str(script_text)[:1000]}")
    else:
        print(f"  errors: {body.get('errors')}")
else:
    print(f"  body: {str(body)[:1500]}")

# Worker bindings / settings (latest)
print(f"\n=== Worker settings ===")
code, body = cf("GET", f"/accounts/{ACCT}/workers/scripts/sonicloud-root-worker/settings")
if isinstance(body, dict) and body.get("result"):
    res = body["result"]
    print(f"  bindings: {json.dumps(res.get('bindings',[]), indent=2)}")
    print(f"  usage_model: {res.get('usage_model')}")
    print(f"  compatibility_date: {res.get('compatibility_date')}")
    print(f"  compatibility_flags: {res.get('compatibility_flags')}")
    print(f"  limits: {json.dumps(res.get('limits',{}), indent=2)}")
else:
    print(f"  HTTP {code}: {str(body)[:500]}")

# Routes
print(f"\n=== Worker Routes ===")
code, body = cf("GET", f"/zones/{zone_id}/workers/routes")
if isinstance(body, dict):
    for r in body.get("result", []):
        print(f"  pattern={r.get('pattern')} script={r.get('script')}")

# Try Worker versions
print(f"\n=== Worker versions ===")
code, body = cf("GET", f"/accounts/{ACCT}/workers/scripts/sonicloud-root-worker/versions")
if isinstance(body, dict) and body.get("result"):
    for v in body["result"].get("items", [])[:5]:
        print(f"  id={v.get('id')} created={v.get('created_on')} metadata={v.get('metadata',{})}")

# KV namespaces available
print(f"\n=== KV namespaces in account ===")
code, body = cf("GET", f"/accounts/{ACCT}/storage/kv/namespaces")
if isinstance(body, dict):
    for ns in body.get("result", [])[:10]:
        print(f"  id={ns.get('id')} title={ns.get('title')}")

# D1 databases
print(f"\n=== D1 databases in account ===")
code, body = cf("GET", f"/accounts/{ACCT}/d1/database")
if isinstance(body, dict):
    for db in body.get("result", [])[:10]:
        print(f"  name={db.get('name')} uuid={db.get('uuid')}")

# R2 buckets
print(f"\n=== R2 buckets in account ===")
code, body = cf("GET", f"/accounts/{ACCT}/r2/buckets")
if isinstance(body, dict):
    for b in body.get("result", {}).get("buckets", [])[:10]:
        print(f"  name={b.get('name')}")

# Worker for Platforms dispatch namespaces
print(f"\n=== Workers for Platforms dispatch namespaces ===")
code, body = cf("GET", f"/accounts/{ACCT}/workers/dispatch/namespaces")
if isinstance(body, dict):
    print(f"  HTTP {code}, result: {body.get('result')}")
else:
    print(f"  HTTP {code}: {str(body)[:200]}")
