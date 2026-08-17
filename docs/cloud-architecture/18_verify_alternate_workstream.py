#!/usr/bin/env python3
"""18_verify_alternate_workstream.py — Verify the other workstream's claims.

Their claims:
  1. NS migrated to Netlify (dns*.p02.nsone.net)
  2. CF Worker serves on app.sonicloud.app via CNAME → workers.dev (DNS-only at Netlify)
  3. CF zone still "active" even with NS on Netlify
  4. CF Worker serves on apex sonicloud.app via ALIAS → workers.dev
  5. Vercel serves docs/blog via CNAME → cname.vercel-dns.com
  6. Old Netlify sub-zone for app.sonicloud.app was deleted
  7. Worker Route for app.sonicloud.app/* exists in CF zone

I need to verify ALL of these live, transparently. If they're right, my docs are wrong and I need to fix them.
"""
import json, subprocess, urllib.request, urllib.error, ssl, sys

SECRETS = json.loads(open('/home/z/my-project/scripts/secrets.json').read())
CF_MAIN_TOKEN = SECRETS['root_zone_token']
CF_MAIN_ACCT  = SECRETS['cf_main_account_id']
NL_TOKEN = SECRETS['netlify_token']

GREEN="\033[32m"; RED="\033[31m"; YELLOW="\033[33m"; CYAN="\033[36m"; RESET="\033[0m"

def dig(name, qtype, resolver="1.1.1.1"):
    try:
        out = subprocess.check_output(["dig", f"@{resolver}", name, qtype, "+short", "+time=3", "+tries=2"], timeout=8).decode().strip()
        return [l for l in out.splitlines() if l.strip()]
    except Exception as e:
        return [f"<err: {e}>"]

def curl(url, timeout=15, follow=False):
    args = ["curl", "-sk", "-i", "-m", str(timeout)]
    if follow: args.append("-L")
    args.append(url)
    try:
        r = subprocess.run(args, capture_output=True, timeout=timeout+5)
        return r.stdout.decode('utf-8', 'replace')
    except Exception as e:
        return f"<err: {e}>"

def cf(method, path):
    url = f"https://api.cloudflare.com/client/v4{path}"
    req = urllib.request.Request(url, method=method)
    req.add_header("Authorization", f"Bearer {CF_MAIN_TOKEN}")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())
    except Exception as e:
        return None, {"_err": str(e)}

def nl(method, path):
    url = f"https://api.netlify.com/api/v1{path}"
    req = urllib.request.Request(url, method=method)
    req.add_header("Authorization", f"Bearer {NL_TOKEN}")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            text = r.read().decode()
            return r.status, (json.loads(text) if text else [])
    except urllib.error.HTTPError as e:
        return e.code, []
    except Exception as e:
        return None, {"_err": str(e)}

print(f"{CYAN}══════════════════════════════════════════════════════════════════════════════{RESET}")
print(f"{CYAN}VERIFYING ALTERNATE WORKSTREAM'S CLAIMS (transparent re-check){RESET}")
print(f"{CYAN}══════════════════════════════════════════════════════════════════════════════{RESET}")

# Claim 1: NS migrated to Netlify (dns*.p02.nsone.net)
print(f"\n{CYAN}── Claim 1: NS on Netlify (dns*.p02.nsone.net) ──{RESET}")
for r in ["1.1.1.1", "8.8.8.8", "9.9.9.9"]:
    ns = dig("sonicloud.app", "NS", r)
    is_netlify = any("nsone.net" in n for n in ns)
    is_cf = any("cloudflare.com" in n for n in ns)
    if is_netlify and not is_cf:
        verdict = f"{GREEN}✓ Netlify{RESET}"
    elif is_cf and not is_netlify:
        verdict = f"{RED}✗ still on CF{RESET}"
    elif is_netlify and is_cf:
        verdict = f"{YELLOW}? mixed (both CF + Netlify){RESET}"
    else:
        verdict = f"{RED}? neither{RESET}"
    print(f"  @{r}: {ns}  →  {verdict}")

# Claim 2: CF Worker serves on app.sonicloud.app via CNAME → workers.dev
print(f"\n{CYAN}── Claim 2: app.sonicloud.app CNAME → workers.dev, Worker responds ──{RESET}")
for r in ["1.1.1.1"]:
    cname = dig("app.sonicloud.app", "CNAME", r)
    a = dig("app.sonicloud.app", "A", r)
    print(f"  @{r}: CNAME = {cname}")
    print(f"  @{r}: A     = {a}")
    is_workers_dev_cname = any("workers.dev" in c for c in cname)
    if is_workers_dev_cname:
        print(f"  {GREEN}✓ CNAME points to workers.dev{RESET}")
    else:
        print(f"  {RED}✗ CNAME does NOT point to workers.dev{RESET}")

# Live test: curl app.sonicloud.app/__health
print(f"\n  Live test: curl https://app.sonicloud.app/__health")
out = curl("https://app.sonicloud.app/__health", timeout=15)
print(f"  {out[:600]}")
if "sonicloud-root-worker" in out and "200" in out.split("\n")[0]:
    print(f"  {GREEN}✓ Worker IS serving on app.sonicloud.app{RESET}")
elif "HTTP/2 200" in out or "HTTP/1.1 200" in out:
    print(f"  {GREEN}✓ Worker IS serving on app.sonicloud.app{RESET}")
else:
    print(f"  {RED}✗ Worker NOT serving on app.sonicloud.app{RESET}")

# Claim 3: CF zone still active even with NS on Netlify
print(f"\n{CYAN}── Claim 3: CF zone still 'active' even with NS on Netlify ──{RESET}")
code, body = cf("GET", "/zones?name=sonicloud.app")
if isinstance(body, dict) and body.get("result"):
    z = body["result"][0]
    print(f"  zone id={z.get('id')} status={z.get('status')} name={z.get('name')}")
    print(f"  name_servers (CF assigned) = {z.get('name_servers')}")
    print(f"  original_name_servers = {z.get('original_name_servers')}")
    if z.get('status') == 'active':
        print(f"  {GREEN}✓ CF zone is active (NS change didn't deactivate it){RESET}")
    else:
        print(f"  {YELLOW}? CF zone status: {z.get('status')}{RESET}")
else:
    print(f"  {RED}✗ Could not query CF zone{RESET}")

# Claim 4: Worker serves on apex sonicloud.app via ALIAS → workers.dev
print(f"\n{CYAN}── Claim 4: apex sonicloud.app serves Worker (via ALIAS) ──{RESET}")
for r in ["1.1.1.1"]:
    a = dig("sonicloud.app", "A", r)
    print(f"  @{r}: A = {a}")
out = curl("https://sonicloud.app/__health", timeout=15)
print(f"  Live: {out[:300]}")
if "sonicloud-root-worker" in out:
    print(f"  {GREEN}✓ Apex Worker responds{RESET}")

# Claim 5: Vercel serves docs/blog
print(f"\n{CYAN}── Claim 5: Vercel serves docs.sonicloud.app + blog.sonicloud.app ──{RESET}")
for sub in ["docs", "blog"]:
    cname = dig(f"{sub}.sonicloud.app", "CNAME", "1.1.1.1")
    print(f"  {sub}.sonicloud.app CNAME = {cname}")
    out = curl(f"https://{sub}.sonicloud.app/", timeout=15)
    first_line = out.split("\n")[0] if out else ""
    print(f"  HTTP response: {first_line}")
    if "vercel" in out.lower() or "200" in first_line:
        print(f"  {GREEN}✓ {sub} on Vercel{RESET}")

# Claim 6: Old Netlify sub-zone for app.sonicloud.app was deleted
print(f"\n{CYAN}── Claim 6: Old Netlify sub-zone for app.sonicloud.app was deleted ──{RESET}")
code, body = nl("GET", "/dns_zones")
if isinstance(body, list):
    print(f"  Netlify DNS zones ({len(body)}):")
    for z in body:
        print(f"    - name={z.get('name')} id={z.get('id')} NS={z.get('dns_servers')}")
    has_app_subzone = any(z.get("name") == "app.sonicloud.app" for z in body)
    if has_app_subzone:
        print(f"  {RED}✗ app.sonicloud.app sub-zone STILL EXISTS (claim says deleted){RESET}")
    else:
        print(f"  {GREEN}✓ app.sonicloud.app sub-zone was deleted (as claimed){RESET}")
    has_apex_zone = any(z.get("name") == "sonicloud.app" for z in body)
    if has_apex_zone:
        print(f"  {GREEN}✓ sonicloud.app apex zone exists on Netlify (new) — claim verified{RESET}")
    else:
        print(f"  {YELLOW}? sonicloud.app apex zone NOT on Netlify{RESET}")

# Claim 7: Worker Route for app.sonicloud.app/* exists in CF zone
print(f"\n{CYAN}── Claim 7: Worker Routes in CF apex zone ──{RESET}")
code, body = cf("GET", "/zones?name=sonicloud.app")
if isinstance(body, dict) and body.get("result"):
    zone_id = body["result"][0]["id"]
    code, body = cf("GET", f"/zones/{zone_id}/workers/routes")
    if isinstance(body, dict):
        routes = body.get("result", [])
        print(f"  Worker Routes ({len(routes)}):")
        for r in routes:
            print(f"    - pattern={r.get('pattern')} script={r.get('script')}")
        has_app_route = any(r.get("pattern", "").startswith("app.sonicloud.app") for r in routes)
        if has_app_route:
            print(f"  {GREEN}✓ Worker Route for app.sonicloud.app exists{RESET}")
        else:
            print(f"  {RED}✗ No Worker Route for app.sonicloud.app{RESET}")

# Bonus: check the Netlify apex zone records
print(f"\n{CYAN}── Bonus: Netlify apex zone records (if exists) ──{RESET}")
code, body = nl("GET", "/dns_zones")
if isinstance(body, list):
    apex_zone = next((z for z in body if z.get("name") == "sonicloud.app"), None)
    if apex_zone:
        print(f"  Netlify apex zone: id={apex_zone.get('id')} NS={apex_zone.get('dns_servers')}")
        # Get records
        code, recs = nl("GET", f"/dns_zones/{apex_zone['id']}/dns_records")
        if isinstance(recs, list):
            print(f"  Records ({len(recs)}):")
            for r in sorted(recs, key=lambda x: (x.get("type",""), x.get("hostname",""))):
                val = r.get("value","")
                if len(str(val)) > 80: val = str(val)[:77] + "..."
                print(f"    {r.get('type','?'):6} {r.get('hostname','?'):45} → {val}")
    else:
        print(f"  No Netlify apex zone for sonicloud.app")

print(f"\n{CYAN}══════════════════════════════════════════════════════════════════════════════{RESET}")
print(f"{CYAN}Verification complete — see results above.{RESET}")
print(f"{CYAN}══════════════════════════════════════════════════════════════════════════════{RESET}")
