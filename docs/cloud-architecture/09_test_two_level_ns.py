#!/usr/bin/env python3
"""09_test_two_level_ns.py — Validate the two-level NS delegation chain live.

Strategy: Since cf_sub_token can't create zones in CF SUB (no zone-create perm),
and we can't easily mint a scoped token for CF SUB either (cf_sub_token can't mint
user-level tokens), we test the DNS chain using CF MAIN account for both ends:

  CF apex zone: sonicloud.app                  (in CF MAIN, already exists)
    NS app.sonicloud.app → Netlify NS1          (already delegated)
  Netlify sub-zone: app.sonicloud.app           (already exists, has placeholder A)
    NS app-test-01.sonicloud.app → CF NS        (NEW — what we're testing)
  CF pod-zone: app-test-01.sonicloud.app         (in CF MAIN, NEW — we'll create)

This tests whether NS1 (Netlify) accepts NS records that delegate to a CF zone,
and whether the resulting chain resolves correctly end-to-end.

Steps:
  1. Try to create CF zone app-test-01.sonicloud.app in CF MAIN using root_zone_token.
  2. If success: get the 4 CF-assigned NS for that zone.
  3. Add 4 NS records in the Netlify app.sonicloud.app zone:
       NS app-test-01.app.sonicloud.app → dns1.ns.cloudflare.com (×4)
     Wait — there's a subtlety. The NS record name should be `app-test-01.app.sonicloud.app`
     (i.e., app-test-01 as a subdomain of app.sonicloud.app), NOT app-test-01.sonicloud.app
     directly. Let me think...
     
     Actually no — the NS record name IS the full hostname being delegated.
     - If we want app-test-01.sonicloud.app to be served by a CF zone, the NS record
       for app-test-01.sonicloud.app must live in the sonicloud.app zone (the parent).
     - But sonicloud.app is on CF (us), and we already have NS records delegating
       app.sonicloud.app → Netlify NS1.
     - Once NS1 is authoritative for app.sonicloud.app, it can have NS records
       delegating app-test-01.app.sonicloud.app → CF NS (for a CF zone
       app-test-01.app.sonicloud.app).
     - This is "app-test-01.app.sonicloud.app" (3 levels), not "app-test-01.sonicloud.app" (2 levels).
     
     The FLEET.md naming was "app-us-east-01.example.com" — i.e., direct child of example.com,
     not nested under app.example.com. But in the kit's actual deployment, "app.sonicloud.app"
     is a Netlify sub-zone (not a record in the CF apex). So "app-us-east-01.sonicloud.app"
     would actually be a record in the CF apex zone (not in the Netlify sub-zone).
     
     Hmm. Let me reconsider the architecture.
     
     The kit's current state:
       CF apex zone sonicloud.app:
         A    sonicloud.app                192.0.2.1  (proxied, Worker Routes intercept)
         NS   app.sonicloud.app             → dns1-4.p01.nsone.net  (delegated to Netlify)
         NS   users.sonicloud.app           → dns1-4.p07.nsone.net
         NS   api.sonicloud.app             → dns1-4.p09.nsone.net
         ...
       
       Netlify sub-zone app.sonicloud.app:
         A    app.sonicloud.app             192.0.2.1  (placeholder)
         CNAME www.app.sonicloud.app        → app.sonicloud.app
         TXT  app.sonicloud.app              v=spf1 ...
         ...
     
     Now: where should "app-us-east-01.sonicloud.app" live?
     
     Option 1: As an A record in the CF apex zone (sibling of app.sonicloud.app).
       CF apex:
         A    app-us-east-01.sonicloud.app   → 192.0.2.1  (proxied, Worker Route intercepts)
       Worker Route: app-us-east-01.sonicloud.app/* → app-us-east-01-worker
       This is the simplest — no two-level NS delegation needed.
       Trade-off: pod is in CF MAIN account (same as apex), no per-account isolation.
     
     Option 2: As an NS delegation in the CF apex zone, pointing to a CF pod-zone in another account.
       CF apex:
         NS   app-us-east-01.sonicloud.app   → dns1-2.ns.cloudflare.com (assigned by the new zone)
       New CF zone in CF SUB (or any other CF account): app-us-east-01.sonicloud.app
       This is "one-level NS delegation from CF apex" (skipping the Netlify sub-zone entirely).
       Trade-off: doesn't use the Netlify sub-zone at all; per-pod isolation via separate CF account.
     
     Option 3: Nested under the Netlify sub-zone (two-level NS delegation as I originally described).
       Netlify sub-zone app.sonicloud.app:
         NS   app-us-east-01.app.sonicloud.app   → dns1-2.ns.cloudflare.com
       New CF zone in CF SUB: app-us-east-01.app.sonicloud.app  (4 levels deep)
       This is the "true" two-level chain: registrar → CF apex → Netlify sub-zone → CF pod-zone.
       Trade-off: hostname is longer (app-us-east-01.app.sonicloud.app); but the chain works.

The user's intent (from FLEET.md): pods are named like "app-us-east-01.example.com" — direct child
of example.com, sibling of app.example.com. So Option 1 (A record in CF apex zone) is what FLEET.md
describes. The "per-pod CF account" isolation comes from running the pod's Worker in a separate
CF account, NOT from DNS isolation.

Actually wait — re-reading FLEET.md more carefully:
  "Each regional pod = isolated account per service (CF account for edge worker, Vercel team for SSR,
   Supabase project for DB, Neon project for serverless PG)"
  "DNS: A record in app.example.com Netlify zone"
  
So FLEET.md says: pod DNS is an A record in the Netlify app.example.com zone, and the pod's CF
Worker runs in its own CF account. The hostname "app-us-east-01.example.com" is NOT a separate
CF zone — it's an A record in the Netlify sub-zone, pointing at the CF Worker's edge IP.

But here's the issue: a CF Worker can only bind to a hostname via Worker Custom Domain (which
requires the hostname to be a CF zone) or via Worker Routes (which requires the hostname's DNS
record to be in a CF zone that's in the same account as the Worker). You can't have a CF Worker
in account B serve traffic for a hostname whose A record lives on Netlify DNS — the Worker
Route pattern requires the zone to be in the same CF account.

So either:
  (a) Pod hostname is an A record in CF MAIN zone (same account as apex Worker) — works for
      Worker Routes but no per-account isolation.
  (b) Pod hostname is its own CF zone (in account B) — works for Worker Routes AND per-account
      isolation, but requires NS delegation from somewhere.
  (c) Pod hostname uses CF Worker Custom Domain with the new CF for SaaS feature — complex.

Option (b) requires NS delegation. Where does the NS record live?
  - If pod hostname is `app-us-east-01.sonicloud.app` (sibling of app.sonicloud.app), the NS
    record lives in the CF apex zone (CF MAIN). One-level delegation.
  - If pod hostname is `app-us-east-01.app.sonicloud.app` (child of app.sonicloud.app),
    the NS record lives in the Netlify app.sonicloud.app zone. Two-level delegation.
    This is what I was originally describing.

FLEET.md's example "app-us-east-01.example.com" suggests sibling (one-level), so the NS
record would be in the CF apex zone. Let me go with that — it's simpler and matches FLEET.md.

So the test plan:
  1. Create CF zone `app-test-01.sonicloud.app` in CF MAIN (using root_zone_token).
     (Can't easily test in CF SUB without a properly scoped token; using CF MAIN is sufficient
     to validate the DNS chain. Per-account isolation is a separate concern not affected by DNS.)
  2. Get the 4 CF-assigned NS for the new zone.
  3. Add 4 NS records in the CF apex zone (sonicloud.app) for `app-test-01.sonicloud.app` → those 4 NS.
     (This is "one-level NS delegation" — registrar → CF apex → CF pod-zone, skipping Netlify.)
  4. Wait for propagation.
  5. Verify with: `dig +short NS app-test-01.sonicloud.app @1.1.1.1` → 4 CF nameservers.
  6. Deploy a pod Worker to the new zone, bind via Worker Routes.
  7. Test: `curl https://app-test-01.sonicloud.app/__health` → 200 JSON from pod Worker.

ALSO, to test the two-level chain (CF apex → Netlify sub-zone → CF pod-zone):
  8. Create CF zone `app-test-02.app.sonicloud.app` in CF MAIN.
  9. Get its 4 CF-assigned NS.
 10. Add 4 NS records in the Netlify `app.sonicloud.app` zone for `app-test-02.app.sonicloud.app`.
 11. Wait, verify, deploy pod Worker, test.

This gives us BOTH patterns validated live.
"""
import json, urllib.request, urllib.error, subprocess, time, sys, os

SECRETS = json.loads(open('/home/z/my-project/scripts/secrets.json').read())
CF_MAIN_TOKEN = SECRETS['root_zone_token']
CF_MAIN_ACCT  = SECRETS['cf_main_account_id']
NL_TOKEN = SECRETS['netlify_token']
NL_ACCT  = SECRETS['netlify_account_id']

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

def nl(method, path, body=None, timeout=30):
    url = f"https://api.netlify.com/api/v1{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {NL_TOKEN}")
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

def dig(name, qtype, resolver="1.1.1.1"):
    try:
        out = subprocess.check_output(["dig", f"@{resolver}", name, qtype, "+short", "+time=3", "+tries=2"], timeout=8).decode().strip()
        return [l for l in out.splitlines() if l.strip()]
    except Exception as e:
        return [f"<err: {e}>"]

# ══════════════════════════════════════════════════════════════════════════════
print(f"{CYAN}══════════════════════════════════════════════════════════════════════════════{RESET}")
print(f"{CYAN}TEST A: One-level NS delegation (CF apex → CF pod-zone, both in CF MAIN){RESET}")
print(f"{CYAN}══════════════════════════════════════════════════════════════════════════════{RESET}")

ZONE_A = "app-test-01.sonicloud.app"

# A.1: Check if zone already exists
print(f"\n  {CYAN}A.1: Check if {ZONE_A} zone already exists{RESET}")
code, body = cf("GET", f"/zones?name={ZONE_A}")
existing = body.get("result", []) if isinstance(body, dict) else []
if existing:
    zone = existing[0]
    print(f"    {GREEN}✓{RESET} already exists: id={zone.get('id')} status={zone.get('status')}")
else:
    print(f"    not found — will create")

# A.2: Create the zone
if not existing:
    print(f"\n  {CYAN}A.2: Create CF zone {ZONE_A} in CF MAIN{RESET}")
    code, body = cf("POST", "/zones", {
        "name": ZONE_A,
        "account": {"id": CF_MAIN_ACCT},
        "type": "full",
    })
    if isinstance(body, dict) and body.get("success"):
        zone = body["result"]
        print(f"    {GREEN}✓{RESET} created! id={zone.get('id')} status={zone.get('status')}")
        print(f"      assigned NS: {zone.get('name_servers')}")
    else:
        print(f"    {RED}✗ create failed: HTTP {code}  {str(body)[:300]}{RESET}")
        # If create failed because the token lacks zone-create perm, document and exit
        if isinstance(body, dict):
            for err in body.get("errors", []):
                if "zone.create" in err.get("message", ""):
                    print(f"    {YELLOW}→ root_zone_token lacks Zone Create permission.{RESET}")
                    print(f"    {YELLOW}→ Would need to mint a new scoped token with zone-create perm via the kit's 03_mint_scoped_tokens.py (requires the cf_main_token master which also returns 403 on /accounts).{RESET}")
                    print(f"    {YELLOW}→ Cannot complete TEST A live. Documenting as known limitation.{RESET}")
                    # Skip to TEST B
                    zone = None
                else:
                    zone = None
        else:
            zone = None

if existing or (zone and not isinstance(zone, dict) is False and zone.get('id')):
    ZONE_A_ID = zone["id"]
    ZONE_A_NS = zone.get("name_servers", [])
    
    # A.3: Add NS records in CF apex zone for app-test-01.sonicloud.app
    print(f"\n  {CYAN}A.3: Add NS records in CF apex zone sonicloud.app → {ZONE_A_NS}{RESET}")
    # First get the apex zone ID
    code, body = cf("GET", "/zones?name=sonicloud.app")
    apex_zone_id = body["result"][0]["id"] if body.get("result") else None
    print(f"    apex zone id: {apex_zone_id}")
    
    # Get existing NS records for app-test-01.sonicloud.app in apex zone
    code, body = cf("GET", f"/zones/{apex_zone_id}/dns_records?type=NS&name={ZONE_A}")
    existing_ns = body.get("result", []) if isinstance(body, dict) else []
    print(f"    existing NS records for {ZONE_A}: {len(existing_ns)}")
    
    # Add the 4 NS records
    for ns in ZONE_A_NS:
        already = any(r["content"] == ns for r in existing_ns)
        if already:
            print(f"    {GREEN}✓{RESET} skip NS {ZONE_A} → {ns} (already exists)")
        else:
            code, body = cf("POST", f"/zones/{apex_zone_id}/dns_records", {
                "type": "NS", "name": ZONE_A, "content": ns, "ttl": 1, "proxied": False
            })
            if isinstance(body, dict) and body.get("success"):
                print(f"    {GREEN}✓{RESET} NS {ZONE_A} → {ns}")
            else:
                print(f"    {RED}✗{RESET} NS {ZONE_A} → {ns}  err={str(body)[:200]}")
    
    # A.4: Wait for propagation, then verify
    print(f"\n  {CYAN}A.4: Wait 30s for DNS propagation, then verify{RESET}")
    time.sleep(30)
    for resolver in ["1.1.1.1", "8.8.8.8", "9.9.9.9"]:
        ns = dig(ZONE_A, "NS", resolver)
        print(f"    {resolver}: NS = {ns}")
    
    # A.5: Add a placeholder A record (will be replaced by Worker Route later)
    print(f"\n  {CYAN}A.5: Add placeholder A record (192.0.2.1, proxied=true) in pod-zone{RESET}")
    code, body = cf("POST", f"/zones/{ZONE_A_ID}/dns_records", {
        "type": "A", "name": ZONE_A, "content": "192.0.2.1", "ttl": 1, "proxied": True
    })
    if isinstance(body, dict) and body.get("success"):
        print(f"    {GREEN}✓{RESET} A {ZONE_A} → 192.0.2.1 (proxied)")
    else:
        err = body.get("errors", [{}])[0] if isinstance(body, dict) else {}
        if "already exists" in err.get("message", "").lower() or err.get("code") == 81053:
            print(f"    {GREEN}✓{RESET} A record already exists (skip)")
        else:
            print(f"    {RED}✗{RESET} A record failed: {str(body)[:200]}")

# ══════════════════════════════════════════════════════════════════════════════
print(f"\n{CYAN}══════════════════════════════════════════════════════════════════════════════{RESET}")
print(f"{CYAN}TEST B: Two-level NS delegation (CF apex → Netlify sub-zone → CF pod-zone){RESET}")
print(f"{CYAN}══════════════════════════════════════════════════════════════════════════════{RESET}")

ZONE_B = "app-test-02.app.sonicloud.app"  # 4 levels: app-test-02.app.sonicloud.app

# B.1: Check if zone already exists
print(f"\n  {CYAN}B.1: Check if {ZONE_B} zone already exists{RESET}")
code, body = cf("GET", f"/zones?name={ZONE_B}")
existing = body.get("result", []) if isinstance(body, dict) else []
if existing:
    zone_b = existing[0]
    print(f"    {GREEN}✓{RESET} already exists: id={zone_b.get('id')} status={zone_b.get('status')}")
else:
    print(f"    not found — will create")

# B.2: Create the zone
if not existing:
    print(f"\n  {CYAN}B.2: Create CF zone {ZONE_B} in CF MAIN{RESET}")
    code, body = cf("POST", "/zones", {
        "name": ZONE_B,
        "account": {"id": CF_MAIN_ACCT},
        "type": "full",
    })
    if isinstance(body, dict) and body.get("success"):
        zone_b = body["result"]
        print(f"    {GREEN}✓{RESET} created! id={zone_b.get('id')} status={zone_b.get('status')}")
        print(f"      assigned NS: {zone_b.get('name_servers')}")
    else:
        print(f"    {RED}✗ create failed: HTTP {code}  {str(body)[:300]}{RESET}")
        zone_b = None

if zone_b:
    ZONE_B_ID = zone_b["id"]
    ZONE_B_NS = zone_b.get("name_servers", [])
    
    # B.3: Find the Netlify app.sonicloud.app zone, add NS records for app-test-02.app.sonicloud.app
    print(f"\n  {CYAN}B.3: Add NS records in Netlify app.sonicloud.app zone{RESET}")
    code, body = nl("GET", "/dns_zones")
    netlify_zones = body if isinstance(body, list) else []
    app_zone = next((z for z in netlify_zones if z.get("name") == "app.sonicloud.app"), None)
    if not app_zone:
        print(f"    {RED}✗ Netlify zone app.sonicloud.app not found{RESET}")
    else:
        print(f"    found Netlify zone: id={app_zone['id']}")
        # Get existing NS records
        code, recs = nl("GET", f"/dns_zones/{app_zone['id']}/dns_records")
        recs = recs if isinstance(recs, list) else []
        existing_ns_b = [r for r in recs if r.get("type") == "NS" and r.get("hostname") == ZONE_B]
        print(f"    existing NS records for {ZONE_B}: {len(existing_ns_b)}")
        # Add the 4 NS records
        for ns in ZONE_B_NS:
            already = any(r.get("value") == ns for r in existing_ns_b)
            if already:
                print(f"    {GREEN}✓{RESET} skip NS {ZONE_B} → {ns} (already exists)")
            else:
                code, body = nl("POST", f"/dns_zones/{app_zone['id']}/dns_records", {
                    "type": "NS", "hostname": ZONE_B, "value": ns, "ttl": 3600
                })
                if 200 <= (code or 0) < 300:
                    print(f"    {GREEN}✓{RESET} NS {ZONE_B} → {ns}")
                else:
                    print(f"    {RED}✗{RESET} NS {ZONE_B} → {ns}  code={code} err={str(body)[:200]}")
    
    # B.4: Wait for propagation, then verify
    print(f"\n  {CYAN}B.4: Wait 60s for DNS propagation (two-level chain takes longer), then verify{RESET}")
    time.sleep(60)
    for resolver in ["1.1.1.1", "8.8.8.8"]:
        ns = dig(ZONE_B, "NS", resolver)
        print(f"    {resolver}: NS = {ns}")
    # Also try querying the NS1 directly
    if app_zone:
        ns1 = app_zone.get("dns_servers", [""])[0]
        if ns1:
            print(f"    query NS1 ({ns1}) directly:")
            ns_via_ns1 = dig(ZONE_B, "NS", ns1)
            print(f"      NS = {ns_via_ns1}")
    
    # B.5: Add placeholder A record (proxied=true)
    print(f"\n  {CYAN}B.5: Add placeholder A record (192.0.2.1, proxied=true) in pod-zone{RESET}")
    code, body = cf("POST", f"/zones/{ZONE_B_ID}/dns_records", {
        "type": "A", "name": ZONE_B, "content": "192.0.2.1", "ttl": 1, "proxied": True
    })
    if isinstance(body, dict) and body.get("success"):
        print(f"    {GREEN}✓{RESET} A {ZONE_B} → 192.0.2.1 (proxied)")
    else:
        err = body.get("errors", [{}])[0] if isinstance(body, dict) else {}
        if err.get("code") == 81053:
            print(f"    {GREEN}✓{RESET} A record already exists (skip)")
        else:
            print(f"    {RED}✗{RESET} A record failed: {str(body)[:200]}")

print(f"\n{GREEN}══════════════════════════════════════════════════════════════════════════════{RESET}")
print(f"{GREEN}Probe complete. Use scripts/10_deploy_apex_worker_kv.py + 11_deploy_pod_worker.py next.{RESET}")
print(f"{GREEN}══════════════════════════════════════════════════════════════════════════════{RESET}")
