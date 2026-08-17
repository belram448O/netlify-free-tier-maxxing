#!/usr/bin/env python3
"""25_full_live_audit.py — Comprehensive audit of everything live on sonicloud.app.

This is a READ-ONLY audit that captures the complete current state:
  1. DNS: NS, apex records, sub-zone delegations
  2. CF: zone status, Worker Routes, KV namespaces, Worker scripts, Cron
  3. Netlify: apex zone + records, sub-zones, account capabilities + credits
  4. HTTP: reachability of all hostnames
  5. Vercel: project status

The output is a single JSON state file that opus sub-agents can read to verify
the architecture docs match reality.
"""
import json, subprocess, urllib.request, urllib.error, ssl, time, sys, os

SECRETS = json.loads(open('/home/z/my-project/scripts/secrets.json').read())
DOMAIN = SECRETS['domain']
CF_TOKEN = SECRETS['root_zone_token']
CF_ACCT = SECRETS['cf_main_account_id']
NL_TOKEN = SECRETS['netlify_token']
NL_ACCT = SECRETS['netlify_account_id']
NL_SCRAPER_TOKEN = SECRETS['scrape_api_key']
NL_SCRAPER_ACCT = "6a7e84d51cdeff620a5cf5a0"
V_TOKEN = SECRETS['vercel_token']
V_TEAM = SECRETS['vercel_team_id']

state = {"timestamp": time.time(), "date_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}

def dig(name, qtype, resolver="1.1.1.1"):
    try:
        out = subprocess.check_output(["dig", f"@{resolver}", name, qtype, "+short", "+time=3", "+tries=2"], timeout=8).decode().strip()
        return [l for l in out.splitlines() if l.strip()]
    except: return []

def http_get(url, timeout=12):
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    req = urllib.request.Request(url, headers={"User-Agent": "audit/1.0"})
    try:
        t0 = time.time()
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
            body = r.read().decode('utf-8', 'replace')[:300]
            elapsed = (time.time() - t0) * 1000
            return {"status": r.status, "elapsed_ms": round(elapsed), "body_preview": body, "headers": {k: v for k, v in r.headers.items() if k.lower() in ("server", "content-type", "location", "set-cookie")}}
    except urllib.error.HTTPError as e:
        return {"status": e.code, "error": str(e)[:100]}
    except Exception as e:
        return {"status": None, "error": str(e)[:100]}

def cf_api(path, token=None):
    token = token or CF_TOKEN
    req = urllib.request.Request(f"https://api.cloudflare.com/client/v4{path}")
    req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")
    except Exception as e:
        return None, {"_err": str(e)}

def nl_api(path, token=None):
    token = token or NL_TOKEN
    req = urllib.request.Request(f"https://api.netlify.com/api/v1{path}")
    req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            text = r.read().decode()
            return r.status, (json.loads(text) if text else [])
    except urllib.error.HTTPError as e:
        return e.code, []
    except Exception as e:
        return None, {"_err": str(e)}

# ══════════════════════════════════════════════════════════════════════════════
# 1. DNS
# ══════════════════════════════════════════════════════════════════════════════
state["dns"] = {}
for resolver in ["1.1.1.1", "8.8.8.8", "9.9.9.9"]:
    state["dns"][resolver] = {
        "apex_NS": dig(DOMAIN, "NS", resolver),
        "apex_A": dig(DOMAIN, "A", resolver),
        "app_NS": dig(f"app.{DOMAIN}", "NS", resolver),
        "app_CNAME": dig(f"app.{DOMAIN}", "CNAME", resolver),
        "app_A": dig(f"app.{DOMAIN}", "A", resolver),
        "docs_CNAME": dig(f"docs.{DOMAIN}", "CNAME", resolver),
        "users_NS": dig(f"users.{DOMAIN}", "NS", resolver),
    }

# ══════════════════════════════════════════════════════════════════════════════
# 2. CF zone + Workers
# ══════════════════════════════════════════════════════════════════════════════
state["cloudflare"] = {}
code, body = cf_api(f"/zones?name={DOMAIN}")
if body.get("result"):
    z = body["result"][0]
    zone_id = z["id"]
    state["cloudflare"]["zone"] = {"id": z["id"], "status": z["status"], "name_servers": z.get("name_servers"), "original_name_servers": z.get("original_name_servers")}
    
    # DNS records
    code, body = cf_api(f"/zones/{zone_id}/dns_records?per_page=200")
    state["cloudflare"]["dns_records"] = [{"type": r["type"], "name": r["name"], "content": r["content"][:80], "proxied": r.get("proxied")} for r in body.get("result", [])]
    
    # Worker Routes
    code, body = cf_api(f"/zones/{zone_id}/workers/routes")
    state["cloudflare"]["worker_routes"] = [{"pattern": r.get("pattern"), "script": r.get("script")} for r in body.get("result", [])]
    
    # Worker scripts
    code, body = cf_api(f"/accounts/{CF_ACCT}/workers/scripts")
    state["cloudflare"]["worker_scripts"] = [{"id": s.get("id"), "modified_on": s.get("modified_on")} for s in body.get("result", [])]
    
    # Worker settings (bindings) for the apex worker
    code, body = cf_api(f"/accounts/{CF_ACCT}/workers/scripts/sonicloud-root-worker/settings")
    state["cloudflare"]["apex_worker_settings"] = body.get("result", {})
    
    # KV namespaces
    code, body = cf_api(f"/accounts/{CF_ACCT}/storage/kv/namespaces")
    state["cloudflare"]["kv_namespaces"] = [{"id": ns.get("id"), "title": ns.get("title")} for ns in body.get("result", [])]
    
    # KV routes key content
    for ns in state["cloudflare"]["kv_namespaces"]:
        if ns.get("title") == "POD_REGISTRY":
            code, body = cf_api(f"/accounts/{CF_ACCT}/storage/kv/namespaces/{ns['id']}/values/routes")
            state["cloudflare"]["pod_registry_routes"] = body if isinstance(body, dict) else body
            code, body = cf_api(f"/accounts/{CF_ACCT}/storage/kv/namespaces/{ns['id']}/values/ab_config")
            state["cloudflare"]["pod_registry_ab_config"] = body if isinstance(body, dict) else body
    
    # Cron schedules
    code, body = cf_api(f"/accounts/{CF_ACCT}/workers/scripts/sonicloud-root-worker/schedules")
    state["cloudflare"]["cron_schedules"] = body.get("result", {}).get("schedules", []) if isinstance(body, dict) else []
    
    # workers.dev subdomain
    code, body = cf_api(f"/accounts/{CF_ACCT}/workers/subdomain")
    state["cloudflare"]["workers_dev_subdomain"] = body.get("result", {}).get("subdomain") if isinstance(body, dict) else None
    
    # Email Routing
    code, body = cf_api(f"/zones/{zone_id}/email/routing")
    state["cloudflare"]["email_routing"] = body.get("result", {}) if isinstance(body, dict) else {}

# ══════════════════════════════════════════════════════════════════════════════
# 3. Netlify
# ══════════════════════════════════════════════════════════════════════════════
state["netlify"] = {}

# DNS account (sonicloud.app)
code, body = nl_api(f"/accounts/{NL_ACCT}")
if isinstance(body, dict):
    caps = body.get("capabilities", {})
    state["netlify"]["dns_account"] = {
        "id": body.get("id"),
        "name": body.get("name"),
        "type_name": body.get("type_name"),
        "type_slug": body.get("type_slug"),
        "plan_credits": body.get("plan_credits"),
        "credits": caps.get("credits"),
        "firewall_enabled": caps.get("firewall_enabled"),
        "traffic_rules": caps.get("traffic_rules"),
        "max_traffic_rules": caps.get("max_traffic_rules"),
        "sites_cap": caps.get("sites"),
        "concurrent_builds": caps.get("concurrent_builds"),
    }
    code, bw = nl_api(f"/accounts/{NL_ACCT}/bandwidth", NL_TOKEN)
    state["netlify"]["dns_account"]["bandwidth"] = bw if isinstance(bw, dict) else {}

# Netlify DNS zones
code, body = nl_api("/dns_zones")
if isinstance(body, list):
    state["netlify"]["dns_zones"] = []
    for z in body:
        zone_info = {"name": z.get("name"), "id": z.get("id"), "dns_servers": z.get("dns_servers")}
        code, recs = nl_api(f"/dns_zones/{z['id']}/dns_records")
        zone_info["records"] = [{"type": r.get("type"), "hostname": r.get("hostname"), "value": str(r.get("value",""))[:80]} for r in (recs if isinstance(recs, list) else [])]
        state["netlify"]["dns_zones"].append(zone_info)

# Scraper account
code, body = nl_api(f"/accounts/{NL_SCRAPER_ACCT}", NL_SCRAPER_TOKEN)
if isinstance(body, dict):
    caps = body.get("capabilities", {})
    state["netlify"]["scraper_account"] = {
        "id": body.get("id"),
        "name": body.get("name"),
        "type_name": body.get("type_name"),
        "type_slug": body.get("type_slug"),
        "plan_credits": body.get("plan_credits"),
        "credits": caps.get("credits"),
        "sites_cap": caps.get("sites"),
    }
    code, sites = nl_api("/sites?per_page=20", NL_SCRAPER_TOKEN)
    state["netlify"]["scraper_account"]["sites"] = [{"name": s.get("name"), "id": s.get("id"), "sso_login": s.get("sso_login")} for s in (sites if isinstance(sites, list) else [])]

# ══════════════════════════════════════════════════════════════════════════════
# 4. HTTP reachability
# ══════════════════════════════════════════════════════════════════════════════
state["http"] = {}
test_urls = [
    f"https://{DOMAIN}/",
    f"https://{DOMAIN}/__health",
    f"https://{DOMAIN}/__routes",
    f"https://{DOMAIN}/app/test",
    f"https://www.{DOMAIN}/",
    f"https://app.{DOMAIN}/__health",
    f"https://docs.{DOMAIN}/",
    f"https://blog.{DOMAIN}/",
    f"https://users.{DOMAIN}/",
    f"https://api.{DOMAIN}/",
    f"https://cdn.{DOMAIN}/",
    f"https://content.{DOMAIN}/",
    f"https://corp.{DOMAIN}/",
]
for url in test_urls:
    state["http"][url] = http_get(url)

# ══════════════════════════════════════════════════════════════════════════════
# 5. Vercel
# ══════════════════════════════════════════════════════════════════════════════
state["vercel"] = {}
req = urllib.request.Request(f"https://api.vercel.com/v9/projects?teamId={V_TEAM}&limit=20", headers={"Authorization": f"Bearer {V_TOKEN}"})
try:
    with urllib.request.urlopen(req, timeout=20) as r:
        body = json.loads(r.read().decode())
        state["vercel"]["projects"] = [{"name": p.get("name"), "targets": list((p.get("targets") or {}).keys())} for p in body.get("projects", [])]
except: state["vercel"]["projects"] = []

# Save
out_path = "/home/z/my-project/nftm/docs/cloud-architecture/live-audit-state.json"
with open(out_path, "w") as f:
    json.dump(state, f, indent=2, default=str)
print(f"State saved to {out_path}")
print(f"\n=== SUMMARY ===")
print(f"DNS: NS at {state['dns']['1.1.1.1']['apex_NS']}")
print(f"CF zone: {state['cloudflare'].get('zone', {}).get('status')}")
print(f"Worker Routes: {len(state['cloudflare'].get('worker_routes', []))}")
print(f"KV namespaces: {len(state['cloudflare'].get('kv_namespaces', []))}")
print(f"Cron schedules: {len(state['cloudflare'].get('cron_schedules', []))}")
print(f"workers.dev: {state['cloudflare'].get('workers_dev_subdomain')}")
print(f"Netlify DNS zones: {len(state['netlify'].get('dns_zones', []))}")
print(f"Netlify apex zone records: {len([z for z in state['netlify'].get('dns_zones', []) if z.get('name') == DOMAIN][0].get('records', [])) if any(z.get('name') == DOMAIN for z in state['netlify'].get('dns_zones', [])) else 0}")
print(f"Vercel projects: {len(state['vercel'].get('projects', []))}")
print(f"HTTP endpoints tested: {len(state['http'])}")
for url, result in state["http"].items():
    status = result.get("status")
    print(f"  {url:55} → {status}")
