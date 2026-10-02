"""Sub-agents: /team and /agents build|plan."""
from __future__ import annotations

from .context import AgentContext
from .core import Agent

ROLES = ("build", "plan")

DESCRIPTIONS = {
    "build": "Runs commands, writes and edits files, verifies the result. "
             "Use /agents build <task> for implementation work.",
    "plan": "Read-only research and design. Produces an ordered plan with "
            "risks and verification. Use /agents plan <task>.",
}


def team_status(ctx: AgentContext) -> str:
    s = ctx.settings
    lines = ["<b>Team</b> (sub-agents of this harness)"]
    for role in ROLES:
        agent = Agent(ctx, role=role)
        lines.append(f"\n<b>{role}</b>: {DESCRIPTIONS[role]}")
        lines.append("tools: " + ", ".join(sorted(agent.tools)))
    lines.append("\nMCP tools: " +
                 (", ".join(t["function"]["name"] for t in ctx.mcp.tool_specs())
                  if ctx.mcp and ctx.mcp.tool_specs() else "none"))
    lines.append(f"\nMax steps per task: {s.max_agent_steps}. "
                 "The main agent can also delegate via agent_spawn(role, task).")
    return "\n".join(lines)


async def run_subagent(role: str, task: str, ctx: AgentContext) -> str:
    if role not in ROLES:
        return f"Unknown role '{role}'. Use build or plan."
    agent = Agent(ctx, role=role)
    return await agent.run(task)
