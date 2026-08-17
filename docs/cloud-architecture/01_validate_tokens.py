#!/usr/bin/env python3
"""01_validate_tokens.py — Validate every provided credential against its API.

Prints one line per token. Exit 0 if all PASS, 1 if any FAIL.
"""
import json, os, sys, urllib.request, urllib.error, base64, time

SECRETS = json.loads(open('/home/z/my-project/scripts/secrets.json').read())

GREEN  = "\033[32m"
RED    = "\033[31m"
YELLOW = "\033[33m"
RESET  = "\033[0m"

results = []  # (name, status, detail)

def req(method, url, headers=None, timeout=20, data=None):
    r = urllib.request.Request(url, method=method, data=data)
    for k,v in (headers or {}).items():
        r.add_header(k, v)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, resp.read().decode('utf-8', 'replace')
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode('utf-8', 'replace')
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"

def check(name, fn):
    try:
        ok, detail = fn()
    except Exception as e:
        ok, detail = False, f"{type(e).__name__}: {e}"
    status = GREEN+"PASS"+RESET if ok else RED+"FAIL"+RESET
    print(f"  {status:30}  {name}  —  {detail[:120]}")
    results.append((name, ok))

# ──────────────────────────────────────────────────────────────────────────────
# Cloudflare MAIN token (account-scoped cfat_)
# ──────────────────────────────────────────────────────────────────────────────
def cf_main():
    code, body = req("GET", f"https://api.cloudflare.com/client/v4/accounts/{SECRETS['cf_main_account_id']}",
                      {"Authorization": f"Bearer {SECRETS['cf_main_token']}"})
    if code != 200: return False, f"HTTP {code} {body[:200]}"
    j = json.loads(body)
    if not j.get("success"): return False, f"CF err: {j.get('errors')}"
    acct = j.get("result", {})
    return True, f"acct name={acct.get('name')} id={acct.get('id')}"
check("CF MAIN token (account-scoped)", cf_main)

# ──────────────────────────────────────────────────────────────────────────────
# Cloudflare SUB token (account-scoped cfat_, different account)
# ──────────────────────────────────────────────────────────────────────────────
def cf_sub():
    code, body = req("GET", f"https://api.cloudflare.com/client/v4/accounts/{SECRETS['cf_sub_account_id']}",
                      {"Authorization": f"Bearer {SECRETS['cf_sub_token']}"})
    if code != 200: return False, f"HTTP {code} {body[:200]}"
    j = json.loads(body)
    if not j.get("success"): return False, f"CF err: {j.get('errors')}"
    acct = j.get("result", {})
    return True, f"acct name={acct.get('name')} id={acct.get('id')}"
check("CF SUB token (account-scoped)", cf_sub)

# ──────────────────────────────────────────────────────────────────────────────
# Cloudflare zone-scoped token (cfut_)
# ──────────────────────────────────────────────────────────────────────────────
def cf_zone_token():
    code, body = req("GET", "https://api.cloudflare.com/client/v4/zones?per_page=5",
                      {"Authorization": f"Bearer {SECRETS['cf_main_zone_scoped_token']}"})
    if code != 200: return False, f"HTTP {code} {body[:200]}"
    j = json.loads(body)
    if not j.get("success"): return False, f"CF err: {j.get('errors')}"
    zones = j.get("result", [])
    names = [z["name"] for z in zones]
    return True, f"can see zones: {names}"
check("CF zone-scoped token (cfut_)", cf_zone_token)

# ──────────────────────────────────────────────────────────────────────────────
# Cloudflare root_zone_token (cfat_, used for apex zone DNS edit)
# ──────────────────────────────────────────────────────────────────────────────
def root_zone_token():
    code, body = req("GET", "https://api.cloudflare.com/client/v4/zones?name=sonicloud.app",
                      {"Authorization": f"Bearer {SECRETS['root_zone_token']}"})
    if code != 200: return False, f"HTTP {code} {body[:200]}"
    j = json.loads(body)
    if not j.get("success"): return False, f"CF err: {j.get('errors')}"
    z = j.get("result", [])
    if not z:
        return True, "token works, but sonicloud.app zone NOT in this account/token scope"
    return True, f"zone id={z[0]['id']} name={z[0]['name']} status={z[0]['status']}"
check("root_zone_token (cfat_)", root_zone_token)

# ──────────────────────────────────────────────────────────────────────────────
# Netlify token + account_id
# ──────────────────────────────────────────────────────────────────────────────
def netlify():
    code, body = req("GET", f"https://api.netlify.com/api/v1/accounts/{SECRETS['netlify_account_id']}",
                      {"Authorization": f"Bearer {SECRETS['netlify_token']}"})
    if code != 200: return False, f"HTTP {code} {body[:200]}"
    j = json.loads(body)
    return True, f"acct name={j.get('name')} slug={j.get('slug')}"
check("Netlify token", netlify)

def netlify_dns_zones():
    code, body = req("GET", "https://api.netlify.com/api/v1/dns_zones",
                      {"Authorization": f"Bearer {SECRETS['netlify_token']}"})
    if code != 200: return False, f"HTTP {code} {body[:200]}"
    j = json.loads(body)
    if not isinstance(j, list): return False, f"unexpected shape: {str(j)[:200]}"
    names = [z.get("name") for z in j]
    return True, f"{len(j)} zones: {names}"
check("Netlify DNS zones list", netlify_dns_zones)

# ──────────────────────────────────────────────────────────────────────────────
# Vercel token + team
# ──────────────────────────────────────────────────────────────────────────────
def vercel():
    code, body = req("GET", f"https://api.vercel.com/v2/teams/{SECRETS['vercel_team_id']}",
                      {"Authorization": f"Bearer {SECRETS['vercel_token']}"})
    if code != 200: return False, f"HTTP {code} {body[:200]}"
    j = json.loads(body)
    t = j.get("team", {})
    return True, f"team name={t.get('name')} slug={t.get('slug')} id={t.get('id')}"
check("Vercel token", vercel)

def vercel_projects():
    code, body = req("GET", f"https://api.vercel.com/v9/projects?teamId={SECRETS['vercel_team_id']}&limit=10",
                      {"Authorization": f"Bearer {SECRETS['vercel_token']}"})
    if code != 200: return False, f"HTTP {code} {body[:200]}"
    j = json.loads(body)
    projs = j.get("projects", [])
    return True, f"{len(projs)} projects: {[p['name'] for p in projs]}"
check("Vercel projects list", vercel_projects)

# ──────────────────────────────────────────────────────────────────────────────
# GitHub primary PAT
# ──────────────────────────────────────────────────────────────────────────────
def gh_primary():
    code, body = req("GET", "https://api.github.com/user",
                      {"Authorization": f"token {SECRETS['gh_primary_token']}", "Accept": "application/vnd.github+json"})
    if code != 200: return False, f"HTTP {code} {body[:200]}"
    j = json.loads(body)
    return True, f"login={j.get('login')} id={j.get('id')}"
check("GitHub primary token", gh_primary)

def gh_secondary():
    code, body = req("GET", "https://api.github.com/user",
                      {"Authorization": f"token {SECRETS['gh_secondary_token']}", "Accept": "application/vnd.github+json"})
    if code != 200: return False, f"HTTP {code} {body[:200]}"
    j = json.loads(body)
    return True, f"login={j.get('login')} id={j.get('id')}"
check("GitHub secondary token", gh_secondary)

# ──────────────────────────────────────────────────────────────────────────────
# GitLab PAT
# ──────────────────────────────────────────────────────────────────────────────
def gitlab():
    code, body = req("GET", "https://gitlab.com/api/v4/user",
                      {"PRIVATE-TOKEN": SECRETS['gitlab_pat']})
    if code != 200: return False, f"HTTP {code} {body[:200]}"
    j = json.loads(body)
    return True, f"login={j.get('username')} id={j.get('id')}"
check("GitLab PAT", gitlab)

# ──────────────────────────────────────────────────────────────────────────────
# Spaceship registrar API
# ──────────────────────────────────────────────────────────────────────────────
def spaceship():
    code, body = req("GET", "https://spaceship.dev/api/v1/domains?take=100&skip=0",
                      {"X-Api-Key": SECRETS['spaceship_key'], "X-Api-Secret": SECRETS['spaceship_secret']})
    if code != 200: return False, f"HTTP {code} {body[:200]}"
    j = json.loads(body)
    domains = j.get('domains', []) or j.get('data', [])
    names = [d.get('domain') for d in domains] if isinstance(domains, list) else []
    return True, f"{len(names)} domains: {names[:10]}"
check("Spaceship registrar API", spaceship)

def spaceship_specific():
    code, body = req("GET", f"https://spaceship.dev/api/v1/domains/sonicloud.app",
                      {"X-Api-Key": SECRETS['spaceship_key'], "X-Api-Secret": SECRETS['spaceship_secret']})
    if code != 200: return False, f"HTTP {code} {body[:200]}"
    j = json.loads(body)
    return True, f"sonicloud.app: status={j.get('status')} NS={j.get('nameservers') or j.get('locked', {}).get('transfer')}"
check("Spaceship domain sonicloud.app", spaceship_specific)

# ──────────────────────────────────────────────────────────────────────────────
# ZenRows PAT (web scraping API)
# ──────────────────────────────────────────────────────────────────────────────
def zenrows():
    # ZenRows API uses API key as query param; test a minimal request
    code, body = req("GET", f"https://api.zenrows.com/v1/?apikey={SECRETS['zenrows_pat']}&url=https://example.com",
                      {}, timeout=30)
    if code != 200: return False, f"HTTP {code} {body[:200]}"
    return True, f"returned {len(body)} bytes"
check("ZenRows PAT", zenrows)

print()
fails = [n for n, ok in results if not ok]
if fails:
    print(RED + f"{len(fails)} FAILED: {fails}" + RESET)
    sys.exit(1)
print(GREEN + f"All {len(results)} checks passed." + RESET)
sys.exit(0)
