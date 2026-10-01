#!/usr/bin/env bash
# Stop/SubagentStop hook: block the stop while `ruff check` fails.
input=$(cat)
# Avoid an endless loop when the agent already continued once because of this hook.
if echo "$input" | grep -q '"stop_hook_active": *true'; then
  exit 0
fi
cd "$CLAUDE_PROJECT_DIR" || exit 0
if ! out=$(uv run ruff check . 2>&1); then
  echo "ruff check failed; fix the lint errors before stopping:" >&2
  echo "$out" >&2
  exit 2
fi
exit 0
