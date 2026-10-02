"""/harness - a live report of the agent's runtime configuration."""
from __future__ import annotations

from .context import AgentContext
from .core import REGISTRY


def status(ctx: AgentContext) -> str:
    s = ctx.settings
    lines = [
        "<b>J-Rock harness</b>",
        f"provider: <code>{s.provider}</code>   model: <code>{s.model}</code>",
        f"thinking: <code>{s.thinking}</code>   auto-compat: "
        f"{'on' if s.auto_compat else 'off'}",
        f"soul: <code>{s.default_soul}</code>   learning: "
        f"{'on' if s.learning else 'off'}",
        f"terminal access: {'enabled' if s.terminal_allowed else 'disabled'}   "
        f"auto_approve_on_edit: {'on' if s.auto_approve_on_edit else 'off'}",
        f"max steps per task: {s.max_agent_steps}",
        "",
        f"<b>Tools ({len(REGISTRY)})</b>: " + ", ".join(REGISTRY),
    ]
    if ctx.mcp and ctx.mcp.servers:
        lines.append("\n<b>MCP servers</b>:")
        for name, srv in ctx.mcp.servers.items():
            lines.append(f"- {name}: {len(srv.tools)} tools")
    if ctx.skills:
        names = ctx.skills.names()
        lines.append("\n<b>Skills</b>: " + (", ".join(names) if names else "none"))
    if ctx.soul:
        lines.append("<b>Souls</b>: " + ", ".join(ctx.soul.names()))
    if ctx.sessions:
        lines.append(f"<b>Sessions</b>: {len(ctx.sessions.list(ctx.user_id))} saved")
    return "\n".join(lines)
