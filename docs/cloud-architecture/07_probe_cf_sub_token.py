#!/usr/bin/env python3
"""07_probe_cf_sub_token.py — Test what cf_sub_token can actually do.

Earlier probe showed cf_sub_token returns 403 on GET /accounts/{sub_acct_id}.
But the token might still work for /zones operations (which is what we need for
the two-level NS delegation test).

This script tests:
  - GET /accounts/{sub_acct_id}  (expect 403)
  - GET /zones?account.id={sub_acct_id}  (test if it can list zones in CF SUB)
  - GET /accounts  (test if it can list accounts at all)
  - GET /user/tokens/verify  (introspect token)
  - GET /zones  (list all zones this token can see)
"""
import json, urllib.request, urllib.error

SECRETS = json.loads(open('/home/z/my-project/scripts/secrets.json').read())
CF_SUB_TOKEN = SECRETS['cf_sub_token']
CF_SUB_ACCT  = SECRETS['cf_sub_account_id']
CF_MAIN_ACCT = SECRETS['cf_main_account_id']

GREEN="\033[32m"; RED="\033[31m"; YELLOW="\033[33m"; CYAN="\033[36m"; RESET="\033[0m"

def cf(method, path, token, body=None, timeout=20):
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

def show(label, code, body):
    succ = isinstance(body, dict) and body.get("success", False)
    color = GREEN if succ else RED
    snippet = ""
    if isinstance(body, dict):
        if body.get("errors"):
            snippet = json.dumps(body["errors"])[:200]
        elif body.get("result") is not None:
            r = body["result"]
            if isinstance(r, list):
                snippet = f"list[{len(r)}]: {json.dumps(r[:2])[:200]}"
            else:
                snippet = json.dumps(r)[:200]
        else:
            snippet = str(body)[:200]
    else:
        snippet = str(body)[:200]
    print(f"  {color}{'✓' if succ else '✗'}{RESET} {label:55} HTTP {code}  {snippet}")

print(f"{CYAN}── cf_sub_token capability probe ──{RESET}")
print(f"  Token: cf_sub_token (cfat_…, account-scoped)")
print(f"  CF SUB account ID: {CF_SUB_ACCT}")
print()

# 1. GET /accounts/{sub_acct_id} — should 403
code, body = cf("GET", f"/accounts/{CF_SUB_ACCT}", CF_SUB_TOKEN)
show("GET /accounts/{sub_acct_id}", code, body)

# 2. GET /accounts — list all accounts the token can see
code, body = cf("GET", "/accounts", CF_SUB_TOKEN)
show("GET /accounts (list)", code, body)

# 3. GET /zones — list all zones the token can see
code, body = cf("GET", "/zones?per_page=10", CF_SUB_TOKEN)
show("GET /zones (list)", code, body)
if isinstance(body, dict) and body.get("result"):
    for z in body["result"]:
        print(f"      zone: name={z.get('name')} id={z.get('id')} account={z.get('account',{}).get('id')} status={z.get('status')}")

# 4. GET /zones?account.id={sub_acct_id} — filter by account
code, body = cf("GET", f"/zones?account.id={CF_SUB_ACCT}", CF_SUB_TOKEN)
show(f"GET /zones?account.id={{sub_acct_id}}", code, body)
if isinstance(body, dict) and body.get("result"):
    for z in body["result"]:
        print(f"      zone: name={z.get('name')} id={z.get('id')} status={z.get('status')}")

# 5. GET /zones?name=sonicloud.app — does it see the apex zone?
code, body = cf("GET", "/zones?name=sonicloud.app", CF_SUB_TOKEN)
show("GET /zones?name=sonicloud.app (apex)", code, body)

# 6. Try POST /zones — can we CREATE a zone in CF SUB?
# (This is a real write — we'll attempt to create 'app-test-01.sonicloud.app' as a test)
print()
print(f"  {YELLOW}── Attempting to create test zone app-test-01.sonicloud.app in CF SUB ──{RESET}")
code, body = cf("POST", "/zones", CF_SUB_TOKEN, {
    "name": "app-test-01.sonicloud.app",
    "account": {"id": CF_SUB_ACCT},
    "type": "full",
})
show("POST /zones (create app-test-01.sonicloud.app)", code, body)
if isinstance(body, dict) and body.get("result"):
    z = body["result"]
    print(f"      created! id={z.get('id')} status={z.get('status')} NS={z.get('name_servers')}")
elif isinstance(body, dict) and body.get("errors"):
    err = body["errors"][0] if body["errors"] else {}
    if err.get("code") == 1061:
        print(f"      (code 1061 = zone already exists — check if we can list it)")
        # Try to find it via list
        code, body = cf("GET", "/zones?name=app-test-01.sonicloud.app", CF_SUB_TOKEN)
        show("GET /zones?name=app-test-01.sonicloud.app (after create attempt)", code, body)
        if isinstance(body, dict) and body.get("result"):
            z = body["result"][0]
            print(f"      found! id={z.get('id')} status={z.get('status')} NS={z.get('name_servers')}")

print()
print(f"{CYAN}── Conclusion ──{RESET}")
print(f"  If POST /zones worked: cf_sub_token CAN create zones in CF SUB account.")
print(f"  This means we can use CF SUB as the 'pod account' for two-level NS delegation tests.")
print(f"  If 403/401: cf_sub_token lacks zone-create permission; would need to mint a scoped token via cf_main_token (which can mint scoped tokens).")
