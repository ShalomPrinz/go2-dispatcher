#!/usr/bin/env bash
# Stop/SubagentStop hook: apply `ruff format` and import sorting to the changed .py files.
# Non-blocking: always exits 0. lint.sh calls it first so the two never race (tests/docs/testing.md).
input=$(cat)
active=$(printf '%s' "$input" | python3 -c 'import json,sys
try: d=json.load(sys.stdin)
except Exception: d={}
print("1" if d.get("stop_hook_active") else "")' 2>/dev/null)
[ -n "$active" ] && exit 0

cwd=$(printf '%s' "$input" | python3 -c 'import json,sys
try: print(json.load(sys.stdin).get("cwd") or "")
except Exception: print("")' 2>/dev/null)
tree=$(git -C "${cwd:-.}" rev-parse --show-toplevel 2>/dev/null) || exit 0
cd "$tree" || exit 0

files=()
while IFS= read -r -d '' f; do
  [[ $f == *.py && -f $f ]] && files+=("$f")
done < <(git diff -z --name-only HEAD 2>/dev/null; git ls-files -z --others --exclude-standard 2>/dev/null)

changed=0
if [ ${#files[@]} -gt 0 ]; then
  before=$(md5sum -- "${files[@]}" 2>/dev/null)
  uv run ruff format --quiet -- "${files[@]}" >/dev/null 2>&1
  uv run ruff check --select I --fix --quiet -- "${files[@]}" >/dev/null 2>&1
  after=$(md5sum -- "${files[@]}" 2>/dev/null)
  changed=$(diff <(printf '%s\n' "$before") <(printf '%s\n' "$after") | grep -c '^>')
fi

if [ "$changed" -gt 0 ]; then
  printf '{"systemMessage":"format ✓ (%d files)","suppressOutput":true}\n' "$changed"
else
  printf '{"systemMessage":"format -","suppressOutput":true}\n'
fi
exit 0
