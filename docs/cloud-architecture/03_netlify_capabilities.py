#!/usr/bin/env python3
"""03_netlify_capabilities.py — Probe Netlify capabilities relevant to the cloud architecture.

Checks:
  1. Account usage (credits consumed, bandwidth, function calls)
  2. Plan (Free vs grandfathered)
  3. Edge Functions availability + existing functions
  4. Blobs API (free, unmetered storage) for pod registry use case
  5. Site list + deploys + functions
  6. Traffic Splits (built-in A/B feature)
  7. Build bot status
"""
import json, urllib.request, urllib.error

SECRETS = json.loads(open('/home/z/my-project/scripts/secrets.json').read())
ACCT = SECRETS['netlify_account_id']
TOK = SECRETS['netlify_token']

GREEN="\033[32m"; RED="\033[31m"; YELLOW="\033[33m"; CYAN="\033[36m"; RESET="\033[0m"

def nl(method, path, body=None):
    url = f"https://api.netlify.com/api/v1{path}"
    data = json.dumps(body).encode() if body else None
    headers = {"Authorization": f"Bearer {TOK}"}
    if body: headers["Content-Type"] = "application/json"
    r = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            txt = resp.read().decode()
            try: return resp.status, json.loads(txt)
            except: return resp.status, txt
    except urllib.error.HTTPError as e:
        txt = e.read().decode()
        try: return e.code, json.loads(txt)
        except: return e.code, txt
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"

def show(label, code, body):
    print(f"\n{CYAN}── {label} ──{RESET}")
    print(f"  HTTP {code}")
    if isinstance(body, dict):
        # show keys + values trimmed
        for k, v in body.items():
            sv = json.dumps(v) if not isinstance(v, str) else v
            print(f"  {k}: {sv[:200]}")
    elif isinstance(body, list):
        print(f"  (list of {len(body)})")
        for i, x in enumerate(body[:5]):
            print(f"  [{i}]: {json.dumps(x)[:200]}")
    else:
        print(f"  {str(body)[:300]}")

# 1. Account info + plan
code, body = nl("GET", f"/accounts/{ACCT}")
show(f"Account {ACCT}", code, body)

# 2. Billing/plan
code, body = nl("GET", f"/accounts/{ACCT}/billing-profile")
show("Billing profile", code, body)

# 3. Plans
code, body = nl("GET", f"/accounts/{ACCT}/plans")
show("Plans", code, body)

# 4. Usage (bandwidth)
code, body = nl("GET", f"/accounts/{ACCT}/bandwidth")
show("Bandwidth usage", code, body)

# 5. Usage (general)
code, body = nl("GET", f"/accounts/{ACCT}/usage")
show("General usage", code, body)

# 6. Sites
code, body = nl("GET", "/sites?per_page=20")
show(f"Sites", code, body)

# 7. Edge Functions (account-level)
code, body = nl("GET", f"/accounts/{ACCT}/edge_functions")
show("Account edge functions", code, body)

# 8. Forms
code, body = nl("GET", "/forms")
show("Forms", code, body)

# 9. Deploys (recent)
code, body = nl("GET", "/deploys?per_page=5")
show("Recent deploys", code, body)

# 10. Blobs API (account-level)
code, body = nl("GET", f"/accounts/{ACCT}/stores")
show("Blob stores (account-level)", code, body)

# 11. Functions (existing)
code, body = nl("GET", "/functions?per_page=20")
show("Functions", code, body)

# 12. DNS zones (count + per-zone record count)
code, body = nl("GET", "/dns_zones")
if isinstance(body, list):
    print(f"\n{CYAN}── DNS zones + record counts ──{RESET}")
    for z in body:
        zid = z['id']
        code2, recs = nl("GET", f"/dns_zones/{zid}/dns_records")
        recs = recs if isinstance(recs, list) else []
        print(f"  {z['name']:35}  id={zid}  records={len(recs)}  NS={z.get('dns_servers')}")
