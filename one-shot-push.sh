#!/usr/bin/env bash
# one-shot-push.sh — Push netlify-free-tier-maxxing to GitHub
#
# This script does everything needed to push the local repo to GitHub.
# Run it from a machine that has GitHub credentials configured.
#
# Usage:
#   # Option A: Restore from git bundle (preserves full history)
#   git clone /path/to/netlify-free-tier-maxxing.gitbundle netlify-free-tier-maxxing
#   cd netlify-free-tier-maxxing
#   bash PUSH_INSTRUCTIONS.sh   # or run the steps below manually
#
#   # Option B: Restore from tar.gz (loses git history, starts fresh)
#   mkdir netlify-free-tier-maxxing
#   cd netlify-free-tier-maxxing
#   tar xzf /path/to/netlify-free-tier-maxxing-final.tar.gz
#   git init && git add -A && git commit -m "Initial commit (restored from archive)"
#   bash PUSH_INSTRUCTIONS.sh   # or run the steps below manually
#
#   # Option C: From a sandbox with the original /home/z/my-project intact
#   cd /home/z/my-project && bash one-shot-push.sh
#
# Requires:
#   - GitHub PAT with repo:create scope, configured via one of:
#     - `gh auth login` (recommended if gh CLI is installed)
#     - git credential helper (e.g., `git config --global credential.helper store`)
#     - GITHUB_TOKEN env var (this script will wire it into git credentials)
#
set -euo pipefail

REPO_OWNER="belram448O"
REPO_NAME="netlify-free-tier-maxxing"
REPO_DESC="Raw research artifacts + superset from the Netlify free-tier investigation. The lean version is at github.com/belram448O/netlify-free-tier-agent-kit."

# Determine repo root (directory containing this script)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "=== Working directory: $(pwd) ==="
echo "=== Git status: ==="
git status --short | head -10

# Wire up GITHUB_TOKEN if provided
if [ -n "${GITHUB_TOKEN:-}" ]; then
  echo "=== GITHUB_TOKEN detected, configuring git credentials ==="
  git config --global credential.helper store
  echo "https://${REPO_OWNER}:${GITHUB_TOKEN}@github.com" > ~/.git-credentials
  chmod 600 ~/.git-credentials
fi

# Check if gh CLI is available — preferred path
if command -v gh >/dev/null 2>&1; then
  echo "=== gh CLI available, using it for repo creation ==="
  if ! gh repo view "${REPO_OWNER}/${REPO_NAME}" >/dev/null 2>&1; then
    echo "=== Creating repo ${REPO_OWNER}/${REPO_NAME} ==="
    gh repo create "${REPO_OWNER}/${REPO_NAME}" \
      --public \
      --description "${REPO_DESC}" \
      --homepage ""
  else
    echo "=== Repo ${REPO_OWNER}/${REPO_NAME} already exists, skipping creation ==="
  fi
else
  echo "=== gh CLI not available ==="
  echo "If the repo ${REPO_OWNER}/${REPO_NAME} does not exist yet, create it manually at:"
  echo "  https://github.com/new"
  echo "  Owner: ${REPO_OWNER}"
  echo "  Name: ${REPO_NAME}"
  echo "  Description: ${REPO_DESC}"
  echo "  Public, do NOT initialize with README/.gitignore/license"
  echo ""
  read -p "Press ENTER once the repo is created (or if it already exists)... "
fi

# Ensure origin remote is set
if ! git remote get-url origin >/dev/null 2>&1; then
  echo "=== Adding origin remote ==="
  git remote add origin "https://github.com/${REPO_OWNER}/${REPO_NAME}.git"
else
  CURRENT_ORIGIN=$(git remote get-url origin)
  if [ "$CURRENT_ORIGIN" != "https://github.com/${REPO_OWNER}/${REPO_NAME}.git" ]; then
    echo "=== Updating origin remote (was: $CURRENT_ORIGIN) ==="
    git remote set-url origin "https://github.com/${REPO_OWNER}/${REPO_NAME}.git"
  fi
fi

echo "=== Pushing to origin main ==="
git push -u origin main

echo ""
echo "=== Push complete ==="
echo "Repo: https://github.com/${REPO_OWNER}/${REPO_NAME}"
echo ""
echo "=== Verifying submodule clones cleanly ==="
echo "To verify the scraper submodule, clone recursively:"
echo "  cd /tmp && git clone --recursive https://github.com/${REPO_OWNER}/${REPO_NAME}.git maxxing-test"
echo "  cd maxxing-test && git submodule status"
echo "  Expected: ' 9e5e906cce24525709c3b54a226217bdaf8cec16 netlify-free-scraper (heads/master)'"
echo ""
echo "=== Optional: remove old gitlab remote (was pointing to agent-kit mirror) ==="
echo "  git remote remove gitlab"
