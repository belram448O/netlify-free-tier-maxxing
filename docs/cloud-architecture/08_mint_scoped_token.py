#!/usr/bin/env python3
"""08_mint_scoped_token.py — Mint a scoped CF token with zone-create permission for CF SUB account.

cf_sub_token is a cfat_ (account API token) which CAN mint scoped tokens (per kit docs).
We need a token with these perms for CF SUB account:
  - Zone:Create (com.cloudflare.api.account.zone.create)
  - Zone:Edit (on the new zone, once created)
  - Workers Scripts:Edit (to deploy Worker)
  - Workers Routes:Edit (to bind Worker)
  - Workers KV Storage:Edit (for KV namespace)
  - DNS:Edit (on the new zone)

This script:
  1. Lists permission groups available to cf_sub_token
  2. Picks the ones we need
  3. Mints a scoped token via POST /user/tokens
"""
import json, urllib.request, urllib.error, sys

SECRETS = json.loads(open('/home/z/my-project/scripts/secrets.json').read())
CF_SUB_TOKEN = SECRETS['cf_sub_token']
CF_SUB_ACCT  = SECRETS['cf_sub_account_id']

GREEN="\033[32m"; RED="\033[31m"; YELLOW="\033[33m"; CYAN="\033[36m"; RESET="\033[0m"

def cf(method, path, token, body=None, timeout=30):
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

# Step 1: List all permission groups available to cf_sub_token
print(f"{CYAN}── Step 1: List permission groups available to cf_sub_token ──{RESET}")
code, body = cf("GET", "/user/tokens/permission_groups", CF_SUB_TOKEN)
if not (isinstance(body, dict) and body.get("success")):
    print(f"  {RED}✗ GET /user/tokens/permission_groups → HTTP {code}  {str(body)[:300]}{RESET}")
    print(f"  {YELLOW}cf_sub_token cannot mint scoped tokens. Need to use dashboard to create a token.{RESET}")
    sys.exit(1)

perms = body.get("result", [])
print(f"  {GREEN}✓{RESET} Found {len(perms)} permission groups")

# Filter to the ones we need (by name keyword)
needed_keywords = ["zone create", "zone edit", "zone read", "dns edit", "dns read",
                   "workers scripts", "workers routes", "workers kv", "workers r2",
                   "account settings", "user tokens"]
matching = []
for p in perms:
    name = p.get("name", "").lower()
    if any(kw in name for kw in needed_keywords):
        matching.append(p)

print(f"\n  Matching permission groups ({len(matching)}):")
for p in matching[:40]:
    print(f"    - id={p.get('id')} name={p.get('name')} scopes={p.get('scopes')}")

# Step 2: Find the specific perms we need by name
def find_perm(name_substr, scope_type=None):
    for p in perms:
        if name_substr.lower() in p.get("name","").lower():
            if scope_type is None or scope_type in p.get("scopes", []):
                return p
    return None

# Required perms:
# - "Zone Create" (account scope) — to create the new zone
# - "Zone Edit" (zone scope, will apply to new zone) — to manage DNS records
# - "Zone Read" (zone scope)
# - "DNS Edit" (zone scope)
# - "Workers Scripts Write" (account scope)
# - "Workers Routes Write" (zone scope)
# - "Workers KV Storage Write" (account scope)
# - "Workers KV Storage Read" (account scope)
# - "User API Tokens Write" (user scope) — so we can rotate this token later (per kit pattern)

zone_create = find_perm("Zone Create", "com.cloudflare.api.account")
zone_edit   = find_perm("Zone Edit", "com.cloudflare.api.account.zone")
zone_read   = find_perm("Zone Read", "com.cloudflare.api.account.zone")
dns_edit    = find_perm("DNS Edit", "com.cloudflare.api.account.zone")
workers_scripts = find_perm("Workers Scripts Write", "com.cloudflare.api.account")
workers_routes  = find_perm("Workers Routes Write", "com.cloudflare.api.account.zone")
workers_kv_write = find_perm("Workers KV Storage Write", "com.cloudflare.api.account")
workers_kv_read  = find_perm("Workers KV Storage Read", "com.cloudflare.api.account")

print(f"\n  Selected perm groups:")
for label, p in [
    ("Zone Create", zone_create),
    ("Zone Edit", zone_edit),
    ("Zone Read", zone_read),
    ("DNS Edit", dns_edit),
    ("Workers Scripts Write", workers_scripts),
    ("Workers Routes Write", workers_routes),
    ("Workers KV Storage Write", workers_kv_write),
    ("Workers KV Storage Read", workers_kv_read),
]:
    if p:
        print(f"    {GREEN}✓{RESET} {label:30}  id={p.get('id')}  scopes={p.get('scopes')}")
    else:
        print(f"    {RED}✗{RESET} {label:30}  NOT FOUND")

# Step 3: Mint the scoped token
print(f"\n{CYAN}── Step 3: Mint scoped token 'sonicloud-pod-ops' ──{RESET}")
selected_perms = [p for p in [zone_create, zone_edit, zone_read, dns_edit, workers_scripts, workers_routes, workers_kv_write, workers_kv_read] if p]
if not selected_perms:
    print(f"  {RED}✗ No matching perms found — abort{RESET}")
    sys.exit(1)

# Token body: account-scoped, with all the perms above (mix of account-scope and zone-scope)
# For zone-scope perms, we need to specify resources (zone ids). But we don't have a zone yet —
# we'll create the zone first using just zone_create perm, then re-mint with the new zone id.
# For simplicity, use wildcards:
# - account-scope perms apply to all zones in the account (good)
# - zone-scope perms apply to all zones in the account (good — we can scope later)
token_body = {
    "name": "sonicloud-pod-ops",
    "policies": [{
        "effect": "allow",
        "resources": {
            # Account-scoped resources: CF SUB account
            f"com.cloudflare.api.account.{CF_SUB_ACCT}": "*"
        },
        "permission_groups": [{"id": p["id"]} for p in selected_perms if "com.cloudflare.api.account" in p.get("scopes", []) and "zone" not in p.get("scopes", [""])[0]]
    }]
}

# Actually CF's token policy format is more nuanced. Let me try the simpler approach: use the
# "all zones in account" resource pattern for zone-scope perms, and explicit account for account-scope perms.
account_scope_perms = [p for p in selected_perms if "com.cloudflare.api.account" in p.get("scopes", []) and "com.cloudflare.api.account.zone" not in p.get("scopes", [])]
zone_scope_perms = [p for p in selected_perms if "com.cloudflare.api.account.zone" in p.get("scopes", [])]

print(f"  Account-scope perms: {len(account_scope_perms)}")
for p in account_scope_perms:
    print(f"    - {p.get('name')}")
print(f"  Zone-scope perms: {len(zone_scope_perms)}")
for p in zone_scope_perms:
    print(f"    - {p.get('name')}")

# Build the token body with two policies (one account-scope, one zone-scope "all zones in account")
policies = []
if account_scope_perms:
    policies.append({
        "effect": "allow",
        "resources": {f"com.cloudflare.api.account.{CF_SUB_ACCT}": "*"},
        "permission_groups": [{"id": p["id"]} for p in account_scope_perms]
    })
if zone_scope_perms:
    # "All zones in account" pattern
    policies.append({
        "effect": "allow",
        "resources": {f"com.cloudflare.api.account.zone.{CF_SUB_ACCT}": "*"},
        "permission_groups": [{"id": p["id"]} for p in zone_scope_perms]
    })

token_body = {
    "name": "sonicloud-pod-ops",
    "policies": policies,
}

code, body = cf("POST", "/user/tokens", CF_SUB_TOKEN, token_body)
if isinstance(body, dict) and body.get("success"):
    result = body.get("result", {})
    token_value = result.get("value")
    print(f"\n  {GREEN}✓ Minted scoped token 'sonicloud-pod-ops'!{RESET}")
    print(f"    id: {result.get('id')}")
    print(f"    token: {token_value}")
    print(f"    {YELLOW}⚠ SAVE THIS TOKEN — it's only shown once.{RESET}")
    # Write to a local file for use by subsequent scripts
    with open('/home/z/my-project/scripts/cf_sub_scoped_token.txt', 'w') as f:
        f.write(token_value)
    os_chmod = __import__('os').chmod
    os_chmod('/home/z/my-project/scripts/cf_sub_scoped_token.txt', 0o600)
    print(f"    Saved to /home/z/my-project/scripts/cf_sub_scoped_token.txt (mode 0600)")
else:
    print(f"\n  {RED}✗ Mint failed: HTTP {code}  {str(body)[:400]}{RESET}")
    if isinstance(body, dict):
        for err in body.get("errors", []):
            print(f"    error: code={err.get('code')} message={err.get('message')}")
