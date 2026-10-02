#!/usr/bin/env bash
# Stop/SubagentStop hook: format the changed .py files, then block the stop while `ruff check` fails on them.
# Changed files only, tree from payload .cwd, loop breaker per session (.claude/hooks/README.md, docs/testing.md).
input=$(cat)
field() {
  printf '%s' "$input" | python3 -c 'import json,sys
try: v=json.load(sys.stdin).get(sys.argv[1])
except Exception: v=None
print("1" if v is True else (v if isinstance(v,str) else ""))' "$1" 2>/dev/null
}

# Format first, in sequence: hooks for one event run in parallel and would race on the same files.
# Its status line is merged into ours, because a hook's stdout must be a single JSON object.
fmt=$(printf '%s' "$input" | "$(dirname "$0")/format.sh" | python3 -c 'import json,sys
try: print(json.loads(sys.stdin.read()).get("systemMessage",""))
except Exception: print("")' 2>/dev/null)

# Avoid an endless loop when the agent already continued once because of a Stop hook.
[ -n "$(field stop_hook_active)" ] && exit 0

cwd=$(field cwd)
tree=$(git -C "${cwd:-.}" rev-parse --show-toplevel 2>/dev/null) || tree=$(git rev-parse --show-toplevel 2>/dev/null) || exit 0
cd "$tree" || exit 0

status() {
  local msg="$1"
  [ -n "$fmt" ] && msg="$fmt · $msg"
  python3 -c 'import json,sys; print(json.dumps({"systemMessage":sys.argv[1],"suppressOutput":True},ensure_ascii=False))' "$msg"
}

files=()
while IFS= read -r -d '' f; do
  [[ $f == *.py && -f $f ]] && files+=("$f")
done < <(git diff -z --name-only HEAD 2>/dev/null; git ls-files -z --others --exclude-standard 2>/dev/null)

if [ ${#files[@]} -eq 0 ]; then
  status "lint -"
  exit 0
fi

session=$(field session_id | tr -cd 'A-Za-z0-9_-')
state="${TMPDIR:-/tmp}/claude-lint-${session:-unknown}"

if out=$(uv run ruff check --force-exclude -- "${files[@]}" 2>&1); then
  rm -f -- "$state"
  status "lint ✓ (${#files[@]} files)"
  exit 0
fi

# Loop breaker: the same failure already blocked once in this session, so warn instead of blocking again.
hash=$(printf '%s' "$out" | md5sum | cut -d' ' -f1)
if [ -f "$state" ] && [ "$(cat "$state" 2>/dev/null)" = "$hash" ]; then
  status "lint ✗ unchanged since the last block; not blocking again (run uv run ruff check .)"
  exit 0
fi
printf '%s\n' "$hash" >"$state"
echo "ruff check failed; fix the lint errors before stopping:" >&2
echo "$out" >&2
exit 2
