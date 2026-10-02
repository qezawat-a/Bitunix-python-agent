from __future__ import annotations

import asyncio

from ..context import AgentContext

TIMEOUT = 600
MAX_OUT = 6000


async def run(args: dict, ctx: AgentContext) -> str:
    cmd = args.get("command", "").strip()
    if not cmd:
        return "No command given."
    if not ctx.settings.terminal_allowed:
        return "Terminal access is disabled in /config."
    cwd = str(ctx.workspace) if ctx.workspace else None
    ok = await ctx.ask_permission(f"terminal: `{cmd[:200]}`")
    if not ok:
        return "User denied the terminal command."
    try:
        proc = await asyncio.create_subprocess_shell(
            cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, cwd=cwd)
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=TIMEOUT)
        except asyncio.TimeoutError:
            proc.kill()
            return f"Command timed out after {TIMEOUT}s."
    except Exception as e:
        return f"Failed to run: {e}"
    text = out.decode("utf-8", "replace")
    if len(text) > MAX_OUT:
        text = text[:MAX_OUT] + f"\n... [truncated, exit={proc.returncode}]"
    return f"exit={proc.returncode}\n{text}"
