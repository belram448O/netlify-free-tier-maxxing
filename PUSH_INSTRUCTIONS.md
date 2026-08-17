# Push Instructions — netlify-free-tier-maxxing

> **STATUS: ✅ PUSHED TO BOTH REMOTES (2026-08-17)**
>
> - **GitHub:** https://github.com/belram448O/netlify-free-tier-maxxing (HEAD: `508666af`)
> - **GitLab:** https://gitlab.com/ansgareutychisO/netlify-free-tier-maxxing (HEAD: `508666af`)
>
> Recursive clone verified working: `git clone --recursive https://github.com/belram448O/netlify-free-tier-maxxing.git`
>
> Both the scraper submodule repo (`netlify-free-scraper`) and the clean agent-kit repo (`netlify-free-tier-agent-kit`) were also made public on GitHub to ensure recursive clone works for anonymous users.
>
> The remainder of this file is preserved for historical reference.

---

## Pre-push checklist (already done in this session)

✅ De-submodule-ized `netlify-probe` (it had no remote — content absorbed as regular directory)
✅ Added `.gitmodules` at root to properly register `netlify-free-scraper` as a submodule
✅ Moved `sonicloud-ns-architecture.md` to `cross-project-handoffs/` (not a Netlify artifact)
✅ Wrote `README.md` explaining maxxing = superset, agent-kit = clean subset
✅ Wrote `INDEX.md` cataloguing every artifact
✅ Synced ALL missing content from the `/tmp/my-project` snapshot into the working tree:
  - `agent-kit/netlify-project/` now contains the **inlined scraper code** (cli/, functions/, lib/, plugins/store-data/, tools/, PROTOCOL.md) plus extra functions (download.mjs, log-exfil.mjs, proxy.mjs)
  - `agent-kit/netlify-project/tools/samples/` now contains the **32 sample API responses** (saved JSON shapes from the bb-api investigation)
  - `netlify-log-probe/functions/` now contains 5 additional probe scripts (check-binding.js, check-deep.js, check-pkgs.js, scrape-af.js, scrape.js)
  - `netlify-log-probe/plugins/probe-build/` (extra plugin)
  - `netlify-log-probe/src/build-log-probe.js`, `build-probe.js`, `data-test.json`
✅ All changes staged for commit
✅ Committed locally (commit hash: see `git log -1`)
✅ Produced offline archive at `download/netlify-free-tier-maxxing-final.tar.gz`

## Pre-push checklist (TODO — requires GitHub PAT)

- [ ] Commit the staged changes locally
- [ ] Create the empty GitHub repo `belram448O/netlify-free-tier-maxxing`
- [ ] Add `origin` remote pointing to it
- [ ] Push
- [ ] Verify the submodule clones cleanly with `git clone --recursive`

---

## Step 1 — Commit the staged changes

```bash
cd /home/z/my-project

git status
# Expected: .gitmodules added, netlify-probe de-submodule-ized (D + A entries), 
#           README.md / INDEX.md / PUSH_INSTRUCTIONS.md new, sonicloud-ns-architecture.md moved

git commit -m "Restructure as netlify-free-tier-maxxing (research superset)

- De-submodule-ize netlify-probe (had no remote; content absorbed as regular dir)
- Add .gitmodules to properly register netlify-free-scraper submodule
- Move sonicloud-ns-architecture.md to cross-project-handoffs/ (not a netlify artifact)
- Add README.md explaining maxxing = superset, agent-kit = clean subset
- Add INDEX.md cataloguing every artifact
- Add PUSH_INSTRUCTIONS.md (this file)"
```

## Step 2 — Create the empty GitHub repo

Requires your GitHub PAT. From a machine with `gh` CLI installed:

```bash
gh repo create belram448O/netlify-free-tier-maxxing \
  --public \
  --description "Raw research artifacts + superset from the Netlify free-tier investigation. The lean version is at github.com/belram448O/netlify-free-tier-agent-kit." \
  --homepage ""
```

Or via the web UI: https://github.com/new
- Owner: `belram448O`
- Repository name: `netlify-free-tier-maxxing`
- Description: `Raw research artifacts + superset from the Netlify free-tier investigation. The lean version is at github.com/belram448O/netlify-free-tier-agent-kit.`
- Public
- Do NOT initialize with README / .gitignore / license (we already have everything)

## Step 3 — Add `origin` remote and push

```bash
cd /home/z/my-project

# Remove the old gitlab remote (the agent-kit repo's gitlab mirror is irrelevant here)
git remote remove gitlab 2>/dev/null

# Add the new origin
git remote add origin https://github.com/belram448O/netlify-free-tier-maxxing.git

# Push main branch + set upstream
git push -u origin main
```

## Step 4 — Verify the submodule clones cleanly

After the push, do a fresh clone to make sure the submodule registration works:

```bash
cd /tmp
git clone --recursive https://github.com/belram448O/netlify-free-tier-maxxing.git maxxing-test
cd maxxing-test
git submodule status
# Expected: " 9e5e906cce24525709c3b54a226217bdaf8cec16 netlify-free-scraper (heads/master)"
# (leading space = registered and at correct commit)

ls netlify-free-scraper/
# Expected: PROTOCOL.md, README.md, cli/, functions/, lib/, netlify.toml, package.json, package-lock.json, plugins/, src/, tools/
```

If the submodule clone fails with auth errors, the scraper repo might be private. Either make `netlify-free-scraper` public, or update `.gitmodules` to use SSH:
```
[submodule "netlify-free-scraper"]
    path = netlify-free-scraper
    url = git@github.com:belram448O/netlify-free-scraper.git
```

---

## Step 5 (optional) — Move sonicloud-ns-architecture.md to its proper home

The file `cross-project-handoffs/sonicloud-ns-architecture.md` belongs in `github.com/zulfikarbarbora-outl/sonicloud-infra` (private, separate repo). Once that repo is checked out locally:

```bash
# In the sonicloud-infra repo
cp /home/z/my-project/cross-project-handoffs/sonicloud-ns-architecture.md docs/sonicloud-ns-architecture.md
git add docs/sonicloud-ns-architecture.md
git commit -m "Add NS/DNS architecture handoff doc from cross-project session"
git push

# Back in maxxing repo — remove the handoff copy (it's now in its proper home)
cd /home/z/my-project
git rm cross-project-handoffs/sonicloud-ns-architecture.md
rmdir cross-project-handoffs/  # only if empty
git commit -m "Move sonicloud-ns-architecture.md to sonicloud-infra repo"
git push
```

---

## What to do if the push fails

**Symptom:** `could not read Username for 'https://github.com': No such device or address`
**Cause:** No GitHub credentials in the environment.
**Fix:** Either:
1. Set up a GitHub PAT and configure git credentials:
   ```bash
   git config --global credential.helper store
   echo "https://belram448O:<PAT>@github.com" > ~/.git-credentials
   chmod 600 ~/.git-credentials
   ```
2. Or use `gh auth login` if `gh` CLI is installed.
3. Or run the push from a machine that already has GitHub credentials configured.

---

## What if `netlify-free-tier-maxxing` already exists on GitHub?

If the repo was already created (e.g., by a previous attempt), just push to it. The local commits will be ahead of any empty remote. If the remote has commits the local doesn't:

```bash
git pull --rebase origin main
git push -u origin main
```

---

## Final state after push

- `github.com/belram448O/netlify-free-tier-agent-kit` — unchanged, still the lean/clean version
- `github.com/belram448O/netlify-free-tier-maxxing` — **new** (or updated), the full superset
- `github.com/belram448O/netlify-free-scraper` — unchanged, still its own repo (pulled in as submodule)
- `github.com/zulfikarbarbora-outl/sonicloud-infra` — separate private repo, gets the sonicloud handoff doc eventually
