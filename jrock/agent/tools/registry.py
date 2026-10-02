from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from ..context import AgentContext

Handler = Callable[[dict, AgentContext], Awaitable[str]]


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict
    handler: Handler
    dangerous: bool = False   # requires user approval unless auto-approve
    enabled_by: str = "all"   # which agent roles may use it: all|build|plan

    def spec(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


def _p(props: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": props, "required": required or []}


S = {"type": "string"}


def build_registry() -> dict[str, Tool]:
    from . import terminal, fs, web, generator, apps
    from . import trader as trader_tools

    tools = [
        Tool("terminal_run", "Run a shell command on the machine where the bot runs. "
             "Use for git, pip, tests, system inspection. Requires user approval.",
             _p({"command": S}, ["command"]), terminal.run, dangerous=True),
        Tool("file_read", "Read a file (UTF-8, truncated).", _p({"path": S}, ["path"]), fs.read_file),
        Tool("file_write", "Write/create a file with content.", _p({"path": S, "content": S},
             ["path", "content"]), fs.write_file, dangerous=True),
        Tool("file_edit", "Replace an exact string in a file.", _p({"path": S, "old": S, "new": S},
             ["path", "old", "new"]), fs.edit_file, dangerous=True),
        Tool("file_list", "List files/directories.", _p({"path": S}), fs.list_dir),
        Tool("file_search", "Search files by regex or substring.", 
             _p({"query": S, "path": S}, ["query"]), fs.search),
        Tool("web_search", "Search the web.", _p({"query": S}, ["query"]), web.search),
        Tool("web_fetch", "Fetch a URL and return readable text.",
             _p({"url": S}, ["url"]), web.fetch),
        Tool("web_deepsearch", "Multi-step research with citations.",
             _p({"query": S}, ["query"]), web.deepsearch),
        Tool("generate_image", "Generate an image from a prompt and send it to the chat.",
             _p({"prompt": S, "size": S}, ["prompt"]), generator.image),
        Tool("generate_tts", "Turn text into a voice message.", _p({"text": S}, ["text"]), generator.tts),
        Tool("generate_video", "Generate a short video from a prompt.", _p({"prompt": S},
             ["prompt"]), generator.video),
        Tool("generate_file", "Create a document/code file and attach it.",
             _p({"prompt": S, "filename": S, "kind": S}, ["prompt", "filename"]), generator.document),
        Tool("app_call", "Call a connected app via its saved config (see /app_connector).",
             _p({"app": S, "action": S, "payload": S}, ["app", "action"]), apps.call),
        Tool("skill_read", "Load the full text of an installed skill by name.",
             _p({"name": S}, ["name"]), _skill_read),
        Tool("memory_store", "Remember a durable fact about the user or project.",
             _p({"fact": S}, ["fact"]), _memory_store),
        Tool("memory_recall", "Recall stored facts relevant to a query.",
             _p({"query": S}, ["query"]), _memory_recall),
        Tool("trader_get_settings", "Show the trader's saved settings (risk, leverage, "
             "breakeven, trailing, etc.).", _p({}), trader_tools.get_settings),
        Tool("trader_set", "Change one trader setting. key is one of: symbol, timeframes, "
             "leverage, risk, margin_mode, position_mode, max_positions, consensus, confidence, "
             "tf_confidence, breakeven, trailing_trigger, trailing_stop, trailing_dist, "
             "liq_distance, account_tp, account_sl, universe, min_volume, scan_interval, "
             "guard_interval, mid_interval, tpsl_method, tp_mode, trailing_method, strategies, "
             "paper. Risky keys (risk, leverage, paper, symbol, margin/position mode, account "
             "TP/SL, max_positions) need the user to tap Approve. Never change a setting the "
             "user did not ask for.", _p({"key": S, "value": S}, ["key", "value"]),
             trader_tools.set_setting),
        Tool("agent_spawn", "Delegate a task to a sub-agent (build|plan).",
             _p({"role": S, "task": S}, ["role", "task"]), _agent_spawn, enabled_by="all"),
    ]
    return {t.name: t for t in tools}


# --- small context-aware helpers -------------------------------------------
async def _skill_read(args: dict, ctx: AgentContext) -> str:
    name = args.get("name", "")
    if not ctx.skills:
        return "No skill store."
    text = ctx.skills.read(name)
    return text or f"Skill '{name}' not found. Use /skills to list."


async def _memory_store(args: dict, ctx: AgentContext) -> str:
    if not ctx.s.learning:
        return "Learning is off; memory not stored."
    if ctx.memory:
        await ctx.memory.store(ctx.user_id, args.get("fact", ""))
    return "Stored."


async def _memory_recall(args: dict, ctx: AgentContext) -> str:
    if not ctx.memory:
        return "No memory."
    hits = await ctx.memory.recall(ctx.user_id, args.get("query", ""))
    return "\n".join(f"- {h}" for h in hits) or "No relevant memories."


async def _agent_spawn(args: dict, ctx: AgentContext) -> str:
    from ..team import run_subagent
    role = args.get("role", "plan")
    task = args.get("task", "")
    if role not in ("build", "plan"):
        return "role must be 'build' or 'plan'."
    return await run_subagent(role, task, ctx)


# Built after the helpers above are defined — build_registry() references them.
TOOL_SPECS = [t.name for t in build_registry().values()]
