#!/usr/bin/env python3
"""02_probe_live_state.py — Probe the live state of sonicloud.app end-to-end.

Checks:
  1. Public DNS: NS, A, MX, TXT for apex (1.1.1.1 + 8.8.8.8 + 9.9.9.9)
  2. Each sub-zone NS delegation
  3. CF zone records (apex)
  4. Netlify DNS zones + records for each sub-zone
  5. HTTP reachability of apex + Worker
  6. Vercel project status
"""
import json, subprocess, urllib.request, urllib.error, ssl, sys, time

SECRETS = json.loads(open('/home/z/my-project/scripts/secrets.json').read())
DOMAIN  = SECRETS['domain']

GREEN  = "\033[32m"
RED    = "\033[31m"
YELLOW = "\033[33m"
CYAN   = "\033[36m"
RESET  = "\033[0m"

def dig(query, type_="NS", resolver="1.1.1.1", timeout=8):
    """Return list of strings (one per answer)."""
    try:
        out = subprocess.check_output(
            ["dig", f"@{resolver}", query, type_, "+short", "+time=3", "+tries=2"],
            timeout=timeout, stderr=subprocess.STDOUT
        ).decode('utf-8', 'replace').strip()
        return [l for l in out.splitlines() if l.strip()]
    except subprocess.TimeoutExpired:
        return ["<timeout>"]
    except Exception as e:
        return [f"<err: {e}>"]

def http_get(url, timeout=15, allow_redirects=True, verify=True):
    """Return (status, body, headers) or (None, err, {})."""
    try:
        ctx = None
        if not verify:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        req = urllib.request.Request(url, headers={"User-Agent": "nftm-probe/1.0"})
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
            return r.status, r.read().decode('utf-8', 'replace')[:500], dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode('utf-8', 'replace')[:500], dict(e.headers)
    except Exception as e:
        return None, f"{type(e).__name__}: {e}", {}

def cf_api(method, path, token=None):
    token = token or SECRETS['root_zone_token']
    r = urllib.request.Request(f"https://api.cloudflare.com/client/v4{path}", method=method)
    r.add_header("Authorization", f"Bearer {token}")
    r.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())
    except Exception as e:
        return None, {"_err": str(e)}

def nl_api(method, path, body=None):
    r = urllib.request.Request(f"https://api.netlify.com/api/v1{path}", method=method)
    r.add_header("Authorization", f"Bearer {SECRETS['netlify_token']}")
    if body is not None:
        r.add_header("Content-Type", "application/json")
        data = json.dumps(body).encode()
    else:
        data = None
    try:
        with urllib.request.urlopen(urllib.request.Request(f"https://api.netlify.com/api/v1{path}", data=data, method=method, headers={"Authorization": f"Bearer {SECRETS['netlify_token']}", "Content-Type": "application/json"} if body else {"Authorization": f"Bearer {SECRETS['netlify_token']}"}), timeout=30) as resp:
            text = resp.read().decode()
            return resp.status, (json.loads(text) if text else {})
    except urllib.error.HTTPError as e:
        text = e.read().decode()
        try:
            return e.code, json.loads(text)
        except:
            return e.code, text
    except Exception as e:
        return None, {"_err": str(e)}

def header(s, c=CYAN):
    print(f"\n{c}── {s} ──{RESET}")

# ══════════════════════════════════════════════════════════════════════════════
# 1. Public DNS — apex
# ══════════════════════════════════════════════════════════════════════════════
header(f"1. Public DNS — apex {DOMAIN}")
for resolver in ["1.1.1.1", "8.8.8.8", "9.9.9.9"]:
    print(f"  {resolver}:")
    for qtype in ["NS", "A", "MX", "TXT", "CAA"]:
        answers = dig(f"{DOMAIN}", qtype, resolver)
        # Filter long TXT into preview
        if qtype == "TXT" and answers and "<" not in answers[0]:
            answers = [a[:90]+"..." if len(a)>90 else a for a in answers]
        print(f"    {qtype:5}: {answers}")

# Apex subdomains the kit planned
SUBZONES = ["users", "app", "content", "corp", "api", "cdn"]
header(f"2. Public DNS — sub-zones (NS delegation check)")
for sub in SUBZONES:
    fqdn = f"{sub}.{DOMAIN}"
    print(f"  {fqdn}:")
    for resolver in ["1.1.1.1"]:
        ns = dig(f"{fqdn}", "NS", resolver)
        a  = dig(f"{fqdn}", "A", resolver)
        print(f"    NS: {ns}")
        print(f"    A:  {a}")

# ══════════════════════════════════════════════════════════════════════════════
# 3. CF zone records (apex)
# ══════════════════════════════════════════════════════════════════════════════
header(f"3. CF zone records for {DOMAIN}")
code, body = cf_api("GET", f"/zones?name={DOMAIN}")
zone_id = None
if code == 200 and body.get("result"):
    zone_id = body["result"][0]["id"]
    z = body["result"][0]
    print(f"  zone id={z['id']} status={z['status']} NS={z.get('name_servers')}")
    print(f"  observed NS={z.get('original_name_servers')}")

if zone_id:
    code, body = cf_api("GET", f"/zones/{zone_id}/dns_records?per_page=200")
    recs = body.get("result", []) if isinstance(body, dict) else []
    print(f"  records ({len(recs)}):")
    for r in sorted(recs, key=lambda x: (x["type"], x["name"])):
        print(f"    {r['type']:6} {r['name']:45} → {str(r.get('content'))[:70]}  proxied={r.get('proxied')}")

    # Worker routes
    code, body = cf_api("GET", f"/zones/{zone_id}/workers/routes")
    routes = body.get("result", []) if isinstance(body, dict) else []
    print(f"\n  Worker Routes ({len(routes)}):")
    for r in routes:
        print(f"    pattern={r.get('pattern')} script={r.get('script')}")

    # Workers list
    code, body = cf_api("GET", f"/accounts/{SECRETS['cf_main_account_id']}/workers/scripts")
    scripts = body.get("result", []) if isinstance(body, dict) else []
    print(f"\n  Worker scripts in CF MAIN account ({len(scripts)}):")
    for s in scripts:
        print(f"    {s.get('id')} modified={s.get('modified_on')}")

    # Email Routing
    code, body = cf_api("GET", f"/zones/{zone_id}/email/routing")
    er = body.get("result", {}) if isinstance(body, dict) else {}
    print(f"\n  Email Routing: status={er.get('status')} name={er.get('name')}")

# ══════════════════════════════════════════════════════════════════════════════
# 4. Netlify DNS zones + records
# ══════════════════════════════════════════════════════════════════════════════
header(f"4. Netlify DNS zones + records")
code, body = nl_api("GET", "/dns_zones")
zones = body if isinstance(body, list) else []
print(f"  {len(zones)} zones:")
for z in zones:
    print(f"\n    {z.get('name')}  id={z.get('id')}  NS={z.get('dns_servers')}")
    # Records
    code, recs = nl_api("GET", f"/dns_zones/{z['id']}/dns_records")
    recs = recs if isinstance(recs, list) else []
    for r in sorted(recs, key=lambda x: (x.get("type",""), x.get("hostname",""))):
        print(f"      {r.get('type','?'):6} {r.get('hostname','?'):45} → {str(r.get('value','?'))[:70]}")

# ══════════════════════════════════════════════════════════════════════════════
# 5. HTTP reachability
# ══════════════════════════════════════════════════════════════════════════════
header(f"5. HTTP reachability")
test_urls = [
    f"https://{DOMAIN}/",
    f"https://{DOMAIN}/__health",
    f"https://www.{DOMAIN}/",
    f"https://docs.{DOMAIN}/",
    f"https://blog.{DOMAIN}/",
    f"https://app.{DOMAIN}/",
    f"https://users.{DOMAIN}/",
    f"https://api.{DOMAIN}/",
    f"https://cdn.{DOMAIN}/",
    f"https://content.{DOMAIN}/",
    f"https://corp.{DOMAIN}/",
]
for u in test_urls:
    code, body, hdrs = http_get(u, verify=False, timeout=12)
    loc = hdrs.get("Location") or hdrs.get("location") or ""
    server = hdrs.get("Server") or hdrs.get("server") or ""
    print(f"  {u:55} → HTTP {code}  server={server[:25]}  loc={loc[:60]}")

# ══════════════════════════════════════════════════════════════════════════════
# 6. Vercel project status
# ══════════════════════════════════════════════════════════════════════════════
header(f"6. Vercel project status")
r = urllib.request.Request(f"https://api.vercel.com/v9/projects?teamId={SECRETS['vercel_team_id']}&limit=20", headers={"Authorization": f"Bearer {SECRETS['vercel_token']}"})
try:
    with urllib.request.urlopen(r, timeout=20) as resp:
        j = json.loads(resp.read().decode())
        for p in j.get("projects", []):
            latest = p.get("latestDeployments", [{}])[0] if p.get("latestDeployments") else {}
            print(f"  {p['name']:30} targets={[t for t in (p.get('targets',{}) or {}).keys()]}  latest={latest.get('readyState')} alias={[a for a in (p.get('alias') or [])][:3]}")
except Exception as e:
    print(f"  err: {e}")

print()
print(f"{GREEN}Probe complete.{RESET}")
