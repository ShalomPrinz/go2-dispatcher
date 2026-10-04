"""Print a Markdown preview of the uncommitted diff for the reviewer; read-only (.claude/scripts/README.md)."""

from __future__ import annotations

import difflib
import os
import re
import subprocess
import sys
from collections.abc import Iterable
from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ownership import load_map, owners  # noqa: E402

EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
UNTRACKED_LINES = 60  # per untracked file shown (tunable)
GREP_HITS = 5  # per stale name (tunable)

# Contract surfaces and what a change there means (owning docs in docs/README.md).
CONTRACTS: tuple[tuple[str, str], ...] = (
    ("dispatcher/prompts.py", "fixed texts: registry hash and golden files change (skills/docs/skills.md)"),
    ("skills/catalog/*/SKILL.md", "skill catalog: registry hash and golden files change (skills/docs/skills.md)"),
    ("dispatcher/tests/golden/*", "golden file (tests/docs/testing.md)"),
    ("dispatcher/registry.py", "registry hash (skills/docs/skills.md)"),
    ("dispatcher/runlog.py", "run-log records (dispatcher/docs/run-log.md)"),
    ("dispatcher/config.py", "config keys (docs/configuration.md)"),
    ("config.example.toml", "config keys (docs/configuration.md)"),
    ("dispatcher/models.py", "plan schema and step results (dispatcher/docs/loop-and-context.md)"),
    ("dispatcher/llm.py", "submit_plan tool schema and request (dispatcher/docs/llm.md)"),
    ("skills/schema.py", "skill response and error codes (skills/docs/skills.md)"),
    ("skills/frontmatter.py", "SKILL.md schema (skills/docs/skills.md)"),
    ("skills/env.py", "skill process environment (skills/docs/skills.md)"),
)
# Safety paths for reviewer check 4 (docs/safety.md): whole files, then words on changed lines elsewhere.
SAFETY_FILES = ("dispatcher/executor.py", "skills/stop_move.py", "skills/motion.py", "skills/process.py")
SAFETY_WORDS = re.compile(
    r"\b(StopMove|stop_move|killpg|_killpg|ORPHANED|orphaned|start_orphan_watchdog|move_loop|SECRET_ENV|child_env|shutdown)\b"
)

DEF = re.compile(r"^\s*(?:async\s+)?(?:def|class)\s+([A-Za-z_]\w*)")
CONST = re.compile(r"^\s*([A-Z][A-Z0-9_]{2,})\s*(?::[^=]+)?=(?!=)")
CONFIG_KEY = re.compile(r"^\s*([a-z][a-z0-9_]*)\s*(?::[^=]+)?=(?!=)")
QUOTED_CODE = re.compile(r"""["']([A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+|[a-z][a-z0-9]*(?:_[a-z0-9]+)+)["']""")
NUMBER = re.compile(r"(?<![\w.])-?\d+(?:\.\d+)?(?:e-?\d+)?(?![\w.])")
LITERAL = re.compile(r""""(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'|""" + NUMBER.pattern)
HUNK = re.compile(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@")
MD_LINK = re.compile(r"\]\(([^)\s]+)\)")
AT_PATH = re.compile(r"(?<![\w`])@[\w.-]*[/.][\w./-]*\w")
DOC_CITE = re.compile(r"\(([\w.-]+(?:/[\w.-]+)*\.md)(?:#[\w-]+)?\)")  # the `(<doc path>)` comment form


@dataclass
class FileDiff:
    path: str
    removed: list[tuple[int, str]] = field(default_factory=list)  # old line number, text
    added: list[tuple[int, str]] = field(default_factory=list)  # new line number, text
    pairs: list[tuple[int, str, str]] = field(default_factory=list)  # new line number, old text, new text


def git(*args: str) -> str:
    done = subprocess.run(["git", "-c", "core.quotepath=off", *args], capture_output=True, text=True, errors="replace")
    return done.stdout if done.returncode == 0 else ""


def base() -> str:
    return "HEAD" if git("rev-parse", "--verify", "-q", "HEAD").strip() else EMPTY_TREE


def parse_diff(text: str) -> dict[str, FileDiff]:
    """Per-file removed and added lines of a `-U0` diff, and the -/+ lines paired in order within each change."""
    files: dict[str, FileDiff] = {}
    cur: FileDiff | None = None
    in_hunk = False
    old_no = new_no = 0
    minus: list[str] = []
    plus: list[tuple[int, str]] = []

    def flush() -> None:
        if cur is not None:
            cur.pairs.extend((n, old, new) for old, (n, new) in zip(minus, plus, strict=False))
        minus.clear()
        plus.clear()

    for line in text.splitlines():
        if line.startswith("diff --git "):
            flush()
            name = line.rsplit(" b/", 1)[-1]
            cur, in_hunk = files.setdefault(name, FileDiff(name)), False
        elif m := HUNK.match(line):
            flush()
            in_hunk, old_no, new_no = True, int(m[1]), int(m[2])
        elif cur is None or not in_hunk:
            continue
        elif line.startswith("-"):
            if plus:
                flush()
            cur.removed.append((old_no, line[1:]))
            minus.append(line[1:])
            old_no += 1
        elif line.startswith("+"):
            cur.added.append((new_no, line[1:]))
            plus.append((new_no, line[1:]))
            new_no += 1
    flush()
    return files


def read(root: Path, rel: str) -> str | None:
    try:
        data = (root / rel).read_bytes()
    except OSError:
        return None
    return None if b"\0" in data[:4096] else data.decode("utf-8", "replace")


def section(title: str, lines: Iterable[str]) -> list[str]:
    body = list(lines)
    return [f"## {title}", "", *body, ""] if body else []


def change_set(root: Path, untracked: list[str]) -> list[str]:
    out = ["```", git("status", "--short").rstrip(), "```"]
    stat = git("diff", "--stat=120", "-M", base()).rstrip()
    if stat:
        out += ["", "```", stat, "```"]
    for rel in untracked:
        text = read(root, rel)
        if text is None:
            out += ["", f"Untracked `{rel}`: binary or unreadable."]
            continue
        lines = text.splitlines()
        more = f", first {UNTRACKED_LINES} shown" if len(lines) > UNTRACKED_LINES else ""
        out += ["", f"Untracked `{rel}` ({len(lines)} lines{more}):", "", "````", *lines[:UNTRACKED_LINES], "````"]
    return out


def doc_index(root: Path) -> list[str]:
    """Repo-relative docs listed in the docs/README.md tables, plus the index itself."""
    index = root / "docs" / "README.md"
    text = read(root, "docs/README.md") or ""
    docs = ["docs/README.md"] if text else []
    for row in (r for r in text.splitlines() if r.startswith("| [")):
        for link in MD_LINK.findall(row):
            path = os.path.normpath(index.parent / link.split("#")[0])
            docs.append(Path(path).relative_to(root).as_posix())
    return list(dict.fromkeys(docs))


def owning_docs(root: Path, rel: str, docs: list[str], texts: dict[str, str]) -> list[str]:
    """Indexed docs that name `rel` (or its package-relative path), plus indexed docs cited inside `rel`."""
    if rel in docs:
        return [rel]
    names = {rel, rel.split("/", 1)[-1]}
    found = [d for d in docs if d != "docs/README.md" and any(n in texts[d] for n in names)]
    if rel.endswith(".py"):
        cited = {c for c in DOC_CITE.findall(read(root, rel) or "") if c in docs}
        found += sorted(cited - set(found))
    return found


def ownership(root: Path, paths: list[str], scopes: dict[str, tuple[str, ...]], packages: tuple[str, ...]) -> list[str]:
    docs = doc_index(root)
    texts = {d: read(root, d) or "" for d in docs}
    out = ["| Path | Agent | Owning docs |", "|---|---|---|"]
    flags = []
    for rel in paths:
        found = owning_docs(root, rel, docs, texts)
        out.append(f"| `{rel}` | {owners(scopes, rel)} | {', '.join(found) or '-'} |")
        in_package = any(rel.startswith(p) for p in packages) and "/tests/" not in rel and not rel.endswith(".md")
        if in_package and not set(found) & set(paths):
            flags.append(f"- `{rel}`: package code changed with no owning doc in the diff.")
    return out + ([""] + flags if flags else [])


def removed_names(diffs: Iterable[FileDiff]) -> set[str]:
    names: set[str] = set()
    for d in diffs:
        is_py, is_config = d.path.endswith(".py"), d.path.endswith((".toml", "config.py"))
        for _, text in d.removed:
            if is_py and (m := DEF.match(text) or CONST.match(text)):
                names.add(m[1])
            if is_config and (m := CONFIG_KEY.match(text)):
                names.add(m[1])
            if is_py:
                names.update(QUOTED_CODE.findall(text))
    return names


def stale_references(diffs: dict[str, FileDiff]) -> list[str]:
    added = "\n".join(t for d in diffs.values() for _, t in d.added)
    names = sorted(n for n in removed_names(diffs.values()) if not re.search(rf"\b{re.escape(n)}\b", added))
    if not names:
        return []
    args = [a for n in names for a in ("-e", n)]
    hits: dict[str, list[str]] = {}
    for line in git("grep", "-n", "-w", "-F", "-I", "--untracked", *args).splitlines():
        for n in names:
            if re.search(rf"\b{re.escape(n)}\b", line.split(":", 2)[-1]):
                hits.setdefault(n, []).append(line.strip())
    out = []
    for n in names:
        if n in hits:
            shown = hits[n][:GREP_HITS]
            more = f" (+{len(hits[n]) - GREP_HITS} more)" if len(hits[n]) > GREP_HITS else ""
            out += [f"- `{n}` removed, still referenced{more}:", *(f"  - `{h[:160]}`" for h in shown)]
    return out


def changed_values(diffs: dict[str, FileDiff]) -> list[str]:
    out = []
    for d in diffs.values():
        for n, old, new in d.pairs:
            pattern = NUMBER if d.path.endswith(".md") else LITERAL  # prose apostrophes are not strings
            old_lits, new_lits = pattern.findall(old), pattern.findall(new)
            if old_lits == new_lits or not (old_lits or new_lits):
                continue
            ratio = difflib.SequenceMatcher(None, pattern.sub("_", old), pattern.sub("_", new)).ratio()
            gone = [short(x) for x in old_lits if x not in new_lits]
            came = [short(x) for x in new_lits if x not in old_lits]
            if ratio >= 0.8 and (gone or came):
                out.append(f"- `{d.path}:{n}`: {', '.join(gone) or '-'} -> {', '.join(came) or '-'}")
    return out


def short(text: str, limit: int = 60) -> str:
    return text if len(text) <= limit else text[: limit - 3] + "..."


def contract_surfaces(paths: list[str]) -> list[str]:
    return [f"- `{p}`: {why}" for p in paths for pat, why in CONTRACTS if fnmatch(p, pat)]


def safety(root: Path, diffs: dict[str, FileDiff], untracked: list[str], packages: tuple[str, ...]) -> list[str]:
    out = [f"- `{p}`: safety file" for p in [*diffs, *untracked] if p in SAFETY_FILES]
    for rel in [*diffs, *untracked]:
        if rel in SAFETY_FILES or not rel.endswith(".py") or "/tests/" in rel or not rel.startswith(packages):
            continue
        lines = [t for _, t in [*diffs[rel].removed, *diffs[rel].added]] if rel in diffs else [read(root, rel) or ""]
        words = sorted({w for t in lines for w in SAFETY_WORDS.findall(t)})
        if words:
            out.append(f"- `{rel}`: changed lines mention {', '.join(f'`{w}`' for w in words)}")
    if not out:
        return ["No executor, stop path, motion loop or secret stripping change: check 4 not needed."]
    return ["Check 4 needed (docs/safety.md):", *out]


def doc_hygiene(root: Path, diffs: dict[str, FileDiff], untracked: list[str]) -> list[str]:
    out = []
    changed = [p for p in [*diffs, *untracked] if (root / p).is_file()]
    for rel in (p for p in changed if p.endswith(".md")):
        fenced = False
        for n, line in enumerate((read(root, rel) or "").splitlines(), 1):
            fenced ^= line.lstrip().startswith("```")
            for link in [] if fenced else MD_LINK.findall(line):
                target = link.split("#")[0]
                if target and "://" not in target and not target.startswith("mailto:"):
                    if not (root / rel).parent.joinpath(target).exists():
                        out.append(f"- `{rel}:{n}`: broken link `{link}`")
    claude_mds = git("ls-files", "--cached", "--others", "--exclude-standard", "*CLAUDE.md").split()
    for rel in claude_mds:
        for n, line in enumerate((read(root, rel) or "").splitlines(), 1):
            out += [f"- `{rel}:{n}`: `{m}` inlines a file into every session" for m in AT_PATH.findall(line)]
    for rel in (p for p in changed if p.endswith(".py")):
        lines = diffs[rel].added if rel in diffs else list(enumerate((read(root, rel) or "").splitlines(), 1))
        for n, line in lines:
            comment = line.split("# ", 1)[1] if "# " in line else line if line.lstrip().startswith('"""') else ""
            for cite in DOC_CITE.findall(comment):
                if not (root / cite).exists() and not (root / rel).parent.joinpath(cite).exists():
                    out.append(f"- `{rel}:{n}`: cites missing doc `{cite}`")
    return out


def main() -> None:
    top = git("rev-parse", "--show-toplevel").strip()
    if not top:
        print("Not in a git repository.")
        return
    root = Path(top)
    os.chdir(root)
    diffs = parse_diff(git("diff", "-U0", "-M", "--no-color", "--no-ext-diff", base()))
    untracked = git("ls-files", "--others", "--exclude-standard").splitlines()
    paths = list(dict.fromkeys([*diffs, *untracked]))
    if not paths:
        print("# Change preview\n\nNo uncommitted changes.")
        return
    out = ["# Change preview", "", "Flags are leads to confirm against the diff, not findings.", ""]
    out += section("Change set", change_set(root, untracked))
    loaded = load_map(root / ".claude" / "ownership.json")
    scopes, packages = loaded if loaded else ({}, ())
    out += section("Ownership", ownership(root, paths, scopes, packages))
    out += section("Stale references", stale_references(diffs))
    out += section("Changed values", changed_values(diffs))
    out += section("Contract surfaces", contract_surfaces(paths))
    out += section("Safety trigger", safety(root, diffs, untracked, packages))
    out += section("Doc hygiene", doc_hygiene(root, diffs, untracked))
    print("\n".join(out).rstrip())


if __name__ == "__main__":
    main()
