#!/usr/bin/env bash
# Creates a worktree for <slug> and restores everything git does not track, so the
# app is runnable there. Invoked by the `worktree` skill as its single setup step.
set -euo pipefail

slug="${1:-}"
if [[ ! "$slug" =~ ^[a-z0-9]+(_[a-z0-9]+)*$ ]]; then
    echo "usage: setup.sh <snake_case_slug>" >&2
    exit 1
fi

# Resolve main's root from the script's own path, not the cwd — the script must never
# branch a worktree off another worktree.
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
wt="$root-$slug"

[[ -e "$wt" ]] && { echo "refusing: $wt already exists" >&2; exit 1; }

echo "==> worktree $wt on branch $slug"
git -C "$root" worktree add -b "$slug" "$wt" main

echo "==> git-ignored files"
# Both optional: the default suite needs neither. .env holds the API key and Telegram token, config.toml
# the local settings. Copied, not symlinked, so a branch under test cannot change main's config.
# runs/ (logs and the stub posture file) is not copied: the worktree starts with a fresh stub.
for f in .env config.toml; do
    if [[ -f "$root/$f" ]]; then cp "$root/$f" "$wt/$f"; else echo "skip $f (absent in main)"; fi
done

echo "==> claude additionalDirectories"
settings="$root/.claude/settings.local.json"
[[ -f "$settings" ]] || echo '{}' > "$settings"
tmp="$(mktemp)"
jq --arg d "$wt" \
    '.permissions.additionalDirectories = ((.permissions.additionalDirectories // []) | if index($d) then . else . + [$d] end)' \
    "$settings" > "$tmp"
mv "$tmp" "$settings"

echo "==> uv sync"
# Core + dev deps only (stub mode); the robot and vision extras belong on the lab machine.
(cd "$wt" && uv sync)

echo
echo "worktree ready: $wt (branch $slug)"
