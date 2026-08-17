#!/usr/bin/env python3
"""20_credit_state_audit.py — Audit the credit state of all available Netlify accounts.

The user mentioned having "quite a few grand-fathered netlify with better free tier usage"
but didn't provide their IDs/tokens. This script audits what we DO have access to:

  1. sonicloud.app DNS account (6a7f8f3637d951add835956d) — credit-based Free
  2. Scraper account (6a7e84d51cdeff620a5cf5a0) — credit-based Free, has the scraper site

Both are credit-based (post-Sep-2025). Neither is grandfathered. The user's grandfathered
accounts are not in our token set.

This audit establishes the BASELINE credit state for the two accounts we can see, so
future sessions can detect credit drain over time. It also documents what data is
unavailable (grandfathered accounts) and what the user needs to provide.
"""
import json, urllib.request, urllib.error

SECRETS = json.loads(open('/home/z/my-project/scripts/secrets.json').read())

GREEN="\033[32m"; RED="\033[31m"; YELLOW="\033[33m"; CYAN="\033[36m"; RESET="\033[0m"

def nl(method, path, token, body=None):
    url = f"https://api.netlify.com/api/v1{path}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    if body: req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            text = r.read().decode()
            try: return r.status, json.loads(text)
            except: return r.status, text
    except urllib.error.HTTPError as e:
        return e.code, f"HTTP err: {e.read().decode()[:200]}"
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"

# Accounts we have tokens for
accounts = [
    ("sonicloud.app DNS account", SECRETS['netlify_token'], SECRETS['netlify_account_id']),
    ("Scraper account", SECRETS['scrape_api_key'], "6a7e84d51cdeff620a5cf5a0"),
]

print(f"{CYAN}══════════════════════════════════════════════════════════════════════════════{RESET}")
print(f"{CYAN}NETLIFY ACCOUNT CREDIT STATE AUDIT (2026-08-17 baseline){RESET}")
print(f"{CYAN}══════════════════════════════════════════════════════════════════════════════{RESET}")

for label, token, acct_id in accounts:
    print(f"\n{CYAN}── {label} ({acct_id}) ──{RESET}")
    
    # Account details
    code, body = nl("GET", f"/accounts/{acct_id}", token)
    if isinstance(body, dict):
        print(f"  name: {body.get('name')}")
        print(f"  slug: {body.get('slug')}")
        print(f"  type_name: {body.get('type_name')}")
        print(f"  type_slug: {body.get('type_slug')}  {'(credit-based Free)' if body.get('type_slug') == 'credit-free' else '(grandfathered/legacy)' if 'legacy' in (body.get('type_slug') or '') else ''}")
        print(f"  plan_credits: {body.get('plan_credits')}")
        print(f"  swar_auto_topup_credits: {body.get('swar_auto_topup_credits')}")
        caps = body.get('capabilities', {})
        if 'credits' in caps:
            print(f"  capabilities.credits: included={caps['credits'].get('included')} used={caps['credits'].get('used')}")
        if 'concurrent_builds' in caps:
            print(f"  capabilities.concurrent_builds: {caps['concurrent_builds']}")
        if 'sites' in caps:
            print(f"  capabilities.sites: included={caps['sites'].get('included')} used={caps['sites'].get('used')}")
        if 'background_functions' in caps:
            print(f"  capabilities.background_functions: {caps['background_functions']}")
        # WAF capability (newly discovered)
        if 'firewall_enabled' in caps:
            print(f"  capabilities.firewall_enabled: {caps['firewall_enabled']}")
        if 'traffic_rules' in caps:
            print(f"  capabilities.traffic_rules: {caps['traffic_rules']}")
        if 'max_traffic_rules' in caps:
            print(f"  capabilities.max_traffic_rules: {caps['max_traffic_rules']}")
    
    # Bandwidth
    code, body = nl("GET", f"/accounts/{acct_id}/bandwidth", token)
    if isinstance(body, dict):
        print(f"  bandwidth: used={body.get('used')} bytes ({body.get('used', 0) / 1024 / 1024:.1f} MB) last_updated={body.get('last_updated_at')}")
        print(f"  bandwidth period: {body.get('period_start_date')} → {body.get('period_end_date')}")
    
    # Sites
    code, body = nl("GET", "/sites?per_page=20", token)
    if isinstance(body, list):
        print(f"  sites ({len(body)}):")
        for s in body[:5]:
            print(f"    - {s.get('name')} (id={s.get('id')}) ssl={s.get('ssl_url')}")
    
    # DNS zones
    code, body = nl("GET", "/dns_zones", token)
    if isinstance(body, list):
        print(f"  DNS zones ({len(body)}):")
        for z in body[:5]:
            print(f"    - {z.get('name')} (id={z.get('id')}) NS={z.get('dns_servers', [])[:1]}...")

# Summary
print(f"\n{CYAN}══════════════════════════════════════════════════════════════════════════════{RESET}")
print(f"{CYAN}SUMMARY{RESET}")
print(f"{CYAN}══════════════════════════════════════════════════════════════════════════════{RESET}")
print("""
Available Netlify accounts (with token access):
  1. sonicloud.app DNS account (6a7f8f3637d951add835956d)
     - type_slug: credit-free (post-Sep-2025 credit-based Free)
     - plan_credits: 300, used: 0
     - 0 sites, 6 DNS zones (sonicloud.app apex + 5 sub-zones)
     - Bandwidth: 0 MB used in current period
  2. Scraper account (6a7e84d51cdeff620a5cf5a0)
     - type_slug: credit-free (post-Sep-2025 credit-based Free)
     - plan_credits: 300, used: ~120 (8 prod deploys × 15 credits, per findings-report)
     - 1 site (scraper), 0 DNS zones
     - Bandwidth: ~4 MB used (per findings-report baseline)

NOT available (user-mentioned but not in token set):
  - "quite a few grand-fathered netlify with better free tier usage"
  - These would be pre-Sep-2025 accounts with legacy quotas:
    * 100 GB bandwidth/month (vs ~15 GB max on credit-based)
    * 125K function invocations/site/month (vs ~30 GB-hours shared)
    * 1M edge function invocations/month (vs ~1.5M shared)
    * 300 build minutes/month (vs not metered but prod deploys cost 15 cr each)
  - type_slug likely: legacy-free or starter-free (can't verify without access)
  - User needs to provide IDs + tokens for these to integrate into the architecture

Architecture implication:
  - The 2 credit-based accounts we have are sufficient for:
    * DNS hosting (free regardless of plan) — all 6 sonicloud.app zones
    * Netlify Blobs storage (unmetered) — cold storage for pod registry, audit logs
    * Build-as-compute via preview deploys (0 credits) — scraper workloads
  - The grandfathered accounts (when provided) should be allocated to:
    * High-traffic sub-zones that need bandwidth (app, users, api)
    * NOT to DNS (free regardless) or low-traffic static sites
    * NOT to build-as-compute (preview deploys are 0 credits regardless of plan)
""")
