from __future__ import annotations

import difflib
import py_compile
import re
import shutil
import time
from pathlib import Path

from ..context import AgentContext

MAX_READ = 20000
MAX_HITS = 400
BACKUP_DIR = ".jrock_backups"
IGNORES = {".git", "__pycache__", "node_modules", ".venv", "venv", "data/sessions", ".jrock_backups"}


def _resolve(ctx: AgentContext, path: str) -> Path:
    base = Path(ctx.workspace) if ctx.workspace else Path.cwd()
    p = Path(path).expanduser()
    p = p if p.is_absolute() else base / p
    return p.resolve()


async def read_file(args: dict, ctx: AgentContext) -> str:
    """Read a file with line numbers. Supports start_line/end_line (1-based).
    Always reports total lines so the agent knows if it saw everything."""
    p = _resolve(ctx, args.get("path", ""))
    if not p.exists():
        return f"Not found: {p}"
    if p.is_dir():
        return f"{p} is a directory."
    try:
        lines = p.read_text(errors="replace").splitlines()
    except Exception as e:
        return f"Error: {e}"
    total = len(lines)
    try:
        start = max(int(args.get("start_line") or 1), 1)
        end = min(int(args.get("end_line") or total), total)
    except (TypeError, ValueError):
        return "start_line/end_line must be integers."
    out, size = [], 0
    last = start - 1
    for i in range(start, end + 1):
        row = f"{i}: {lines[i - 1]}"
        size += len(row) + 1
        if size > MAX_READ:
            break
        out.append(row)
        last = i
    head = f"[{p} | lines {start}-{last} of {total}]"
    if last < end:
        head += f" TRUNCATED - call again with start_line={last + 1} to read the rest"
    elif last >= total:
        head += " (end of file)"
    return head + "\n" + "\n".join(out)


async def write_file(args: dict, ctx: AgentContext) -> str:
    p = _resolve(ctx, args.get("path", ""))
    ok = await ctx.ask_permission(f"write file: {p}")
    if not ok:
        return "User denied the write."
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(args.get("content", ""))
    return f"Wrote {len(args.get('content',''))} chars to {p}."


def _backup(p: Path, ctx: AgentContext) -> Path:
    base = Path(ctx.workspace) if ctx.workspace else Path.cwd()
    d = base / BACKUP_DIR
    d.mkdir(parents=True, exist_ok=True)
    dest = d / f"{int(time.time())}_{p.name}"
    shutil.copy2(p, dest)
    return dest


def _diff(old: str, new: str, name: str) -> str:
    d = difflib.unified_diff(old.splitlines(), new.splitlines(),
                             f"{name} (before)", f"{name} (after)", lineterm="", n=2)
    text = "\n".join(d)
    return text[:1500] + ("\n...[diff truncated]" if len(text) > 1500 else "")


async def edit_file(args: dict, ctx: AgentContext) -> str:
    p = _resolve(ctx, args.get("path", ""))
    if not p.exists():
        return f"Not found: {p}"
    text = p.read_text(errors="replace")
    old, new = args.get("old", ""), args.get("new", "")
    if not old:
        return "'old' is empty."
    count = text.count(old)
    if count == 0:
        return "'old' string not found in file. Re-read the file and copy the exact text."
    if count > 1:
        return (f"'old' matches {count} places - ambiguous. "
                "Add more surrounding lines so it matches exactly once.")
    updated = text.replace(old, new, 1)
    ok = await ctx.ask_permission(f"edit file: {p}\n{_diff(text, updated, p.name)}")
    if not ok:
        return "User denied the edit."
    backup = _backup(p, ctx)
    p.write_text(updated)
    if p.suffix == ".py":
        try:
            py_compile.compile(str(p), doraise=True)
        except py_compile.PyCompileError as e:
            shutil.copy2(backup, p)  # rollback
            return f"EDIT ROLLED BACK - syntax error after change: {e.msg}"
    return f"Edited {p}. Syntax OK. Backup: {backup}"


async def list_dir(args: dict, ctx: AgentContext) -> str:
    p = _resolve(ctx, args.get("path", "."))
    if not p.exists():
        return f"Not found: {p}"
    entries = sorted(p.iterdir(), key=lambda x: (x.is_file(), x.name))
    lines = []
    for e in entries[:200]:
        if any(part in IGNORES for part in e.parts):
            continue
        lines.append(f"{e.name}/" if e.is_dir() else e.name)
    return "\n".join(lines) or "(empty)"


def _iter_files(root: Path):
    for f in root.rglob("*"):
        if f.is_file() and not any(part in IGNORES for part in f.parts):
            yield f


async def search(args: dict, ctx: AgentContext) -> str:
    """Regex/substring search. Returns EVERY matching line (up to MAX_HITS)."""
    q = args.get("query", "")
    root = _resolve(ctx, args.get("path", "."))
    try:
        rx = re.compile(q)
    except re.error:
        rx = None
    hits: list[str] = []
    for f in _iter_files(root):
        try:
            for i, line in enumerate(f.read_text(errors="ignore").splitlines(), 1):
                if (rx.search(line) if rx else q in line):
                    hits.append(f"{f}:{i}: {line.strip()[:200]}")
                    if len(hits) >= MAX_HITS:
                        return "\n".join(hits) + f"\n[stopped at {MAX_HITS} hits - narrow the query]"
        except Exception:
            continue
    return "\n".join(hits) or "No matches."


async def find_usages(args: dict, ctx: AgentContext) -> str:
    """Who imports / calls / references a symbol or module? Use BEFORE calling
    anything a bug or dead code. Zero results is itself evidence."""
    sym = (args.get("symbol") or "").strip()
    if not sym:
        return "symbol is required."
    root = _resolve(ctx, args.get("path", "."))
    word = re.compile(rf"\b{re.escape(sym)}\b")
    hits: list[str] = []
    for f in _iter_files(root):
        if f.suffix not in {".py", ".js", ".ts", ".json", ".md", ".txt", ".service", ""}:
            continue
        try:
            for i, line in enumerate(f.read_text(errors="ignore").splitlines(), 1):
                if word.search(line):
                    kind = "IMPORT" if re.match(r"\s*(from|import)\s", line) else "REF"
                    hits.append(f"{kind} {f}:{i}: {line.strip()[:160]}")
        except Exception:
            continue
    if not hits:
        return f"No references to '{sym}' anywhere under {root}."
    imports = sum(h.startswith("IMPORT") for h in hits)
    return f"{len(hits)} references ({imports} imports) to '{sym}':\n" + "\n".join(hits[:MAX_HITS])
